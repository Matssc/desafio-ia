"""
Agente de análise PLD/FT com function calling.

Para cada cliente sinalizado, o agente:
1. Recebe um resumo inicial (flags das regras determinísticas)
2. DECIDE quais ferramentas chamar (não chama todas sempre)
3. Executa as ferramentas escolhidas
4. Produz um parecer estruturado com base nos resultados

Uso:
    python -m nivel_2.agente
"""

import os
import sys
import json
import time
import random
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from nivel_2.utils import carregar_e_limpar, aplicar_regras, top_10_sinalizados
from nivel_2.tools import historico_cliente, operacoes_do_dia, perfil_canal, _df

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────
# Em alguns ambientes Windows/Anaconda, a resolução de credenciais interna
# do google-genai/google-auth pode deixar a variável de ambiente
# SSL_CERT_FILE num estado inválido antes de cair no caminho manual de
# criação do contexto SSL, causando FileNotFoundError mesmo com o
# certificado do certifi existindo normalmente. Forçamos essa variável
# explicitamente ANTES de qualquer tentativa da biblioteca, garantindo
# que o caminho usado seja sempre o correto.
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

try:
    import truststore
    truststore.extract_from_ssl()
except ImportError:
    pass

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"), vertexai=False)
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)


# ── Schema de saída ─────────────────────────────────────────────────────
class ParecerLLM(BaseModel):
    nivel_risco: str
    tipologia_suspeita: str
    red_flags: list[str]
    justificativa: str


# ── Declaração das ferramentas para o Gemini ────────────────────────────
tool_declarations = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="historico_cliente",
            description=(
                "Resumo agregado de todas as operações do cliente: volume total, "
                "período, flags das regras, contrapartes, canais. "
                "Use como PRIMEIRO passo para entender o perfil geral."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente (ex: CLI-001)",
                    }
                },
                "required": ["cliente_id"],
            },
        ),
        types.FunctionDeclaration(
            name="operacoes_do_dia",
            description=(
                "Lista detalhada das operações de um cliente em um dia específico. "
                "Use quando precisar investigar um dia suspeito — por exemplo, "
                "um dia com muitas operações (possível fracionamento)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente",
                    },
                    "data": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD",
                    },
                },
                "required": ["cliente_id", "data"],
            },
        ),
        types.FunctionDeclaration(
            name="perfil_canal",
            description=(
                "Distribuição de uso por canal (pix, ted, boleto, etc.) com "
                "volume e percentuais. Use quando quiser entender se o cliente "
                "concentra operações em um canal específico."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente",
                    }
                },
                "required": ["cliente_id"],
            },
        ),
    ]
)

# Mapeamento nome → função
TOOL_FUNCTIONS = {
    "historico_cliente": historico_cliente,
    "operacoes_do_dia": operacoes_do_dia,
    "perfil_canal": perfil_canal,
}


# ── Contexto inicial por cliente (montado em pandas, não no LLM) ───────
def montar_contexto_inicial(cliente_id: str, df) -> str:
    """Monta um resumo curto das flags para o agente decidir o que investigar."""
    ops = df[df["cliente_id"] == cliente_id]
    n_ops = len(ops)
    volume = ops["valor_brl"].sum()
    flag_r1 = ops["flag_regra1_fracionamento"].any()
    flag_r2 = ops["flag_regra2_valor_atipico"].any()
    n_r2 = int(ops["flag_regra2_valor_atipico"].sum())

    # Dias com mais operações (pistas para o agente investigar)
    dias = ops[ops["data"].notna()].groupby("data").size().sort_values(ascending=False)
    dias_relevantes = dias.head(3).to_dict()

    return (
        f"CLIENTE: {cliente_id}\n"
        f"Total de operações: {n_ops}\n"
        f"Volume total (BRL): R$ {volume:,.2f}\n"
        f"Regra 1 (fracionamento): {'ATIVADA' if flag_r1 else 'não ativada'}\n"
        f"Regra 2 (valor atípico): {'ATIVADA — ' + str(n_r2) + ' operação(ões)' if flag_r2 else 'não ativada'}\n"
        f"Dias com mais operações: {dias_relevantes}\n"
    )


from collections import deque

# ── Rate limiter global (janela deslizante) ──────────────────────────────
RPM_LIMITE = 12  # gemini-3.1-flash-lite libera 15 RPM na camada gratuita; margem de segurança
_timestamps_chamadas = deque()


def _aguardar_rate_limit():
    """
    Garante que nunca ultrapassamos RPM_LIMITE requisições em qualquer
    janela de 60 segundos — independente de ser um novo turno do mesmo
    cliente ou o início de outro cliente.
    """
    agora = time.time()

    # Descartar timestamps com mais de 60s
    while _timestamps_chamadas and agora - _timestamps_chamadas[0] > 60:
        _timestamps_chamadas.popleft()

    if len(_timestamps_chamadas) >= RPM_LIMITE:
        espera = 60 - (agora - _timestamps_chamadas[0]) + 1  # +1s de margem
        if espera > 0:
            print(f"    🕐 Rate limiter: {len(_timestamps_chamadas)} chamadas no último minuto. "
                  f"Aguardando {espera:.1f}s...")
            time.sleep(espera)

    _timestamps_chamadas.append(time.time())


# ── Retry com backoff para erros transitórios (503, 429) ────────────────
def _chamar_com_retry(**kwargs):
    """
    Chama generate_content com retry para erros transitórios.

    Antes de cada chamada, respeita o rate limiter global (RPM_LIMITE).
    Além disso, trata dois casos de erro de forma diferente:
    - 503 (servidor sobrecarregado): transitório, backoff curto (segundos) resolve.
    - 429 (rate limit / cota excedida): não deveria mais ocorrer com o rate
      limiter ativo, mas se ocorrer (ex: outro processo usando a mesma key),
      esperamos bastante (~1 min) já que backoff curto não resolve.
    """
    max_tentativas = 5
    for tentativa in range(1, max_tentativas + 1):
        _aguardar_rate_limit()
        try:
            return client.models.generate_content(**kwargs)
        except Exception as e:
            erro_str = str(e)
            is_rate_limit = "429" in erro_str
            is_sobrecarga = "503" in erro_str or "UNAVAILABLE" in erro_str

            if not (is_rate_limit or is_sobrecarga) or tentativa >= max_tentativas:
                raise

            if is_rate_limit:
                espera = 65
                print(f"    ⏳ Limite de requisições por minuto atingido "
                      f"(tentativa {tentativa}/{max_tentativas}). Aguardando {espera}s...")
            else:
                espera = (2 ** tentativa) + random.uniform(0, 1)
                print(f"    ⚠️ API sobrecarregada (tentativa {tentativa}/{max_tentativas}). "
                      f"Aguardando {espera:.1f}s...")

            time.sleep(espera)


# ── Loop do agente ──────────────────────────────────────────────────────
def analisar_cliente(cliente_id: str, df) -> dict:
    """
    Executa o loop do agente para um cliente:
    1. Envia contexto inicial + ferramentas disponíveis
    2. LLM decide quais ferramentas chamar
    3. Executa e retorna resultados
    4. Repete até o LLM produzir o parecer final
    """
    contexto = montar_contexto_inicial(cliente_id, df)

    system_prompt = (
        "Você é um analista de compliance especializado em PLD/FT (Prevenção à "
        "Lavagem de Dinheiro e Financiamento do Terrorismo).\n\n"
        "Você tem acesso a ferramentas para consultar a base de operações. "
        "Use-as estrategicamente:\n"
        "- NÃO chame todas as ferramentas para todo cliente — isso é um script, não análise.\n"
        "- Comece pelo histórico se precisar de visão geral.\n"
        "- Use operacoes_do_dia apenas se houver um dia suspeito para investigar.\n"
        "- Use perfil_canal se o padrão de canais parecer relevante.\n\n"
        "Todos os cálculos (somas, medianas, limiares) já foram feitos. "
        "NÃO recalcule — use os valores fornecidos.\n\n"
        "Quando tiver informação suficiente, produza o parecer final como JSON:\n"
        '{"nivel_risco": "baixo|médio|alto", "tipologia_suspeita": "...", '
        '"red_flags": ["..."], "justificativa": "..."}\n'
    )

    messages = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(
                text=f"Analise o seguinte cliente sinalizado pelas regras determinísticas:\n\n{contexto}\n\n"
                "Use as ferramentas disponíveis para investigar e depois emita o parecer."
            )]
        ),
    ]

    ferramentas_chamadas = []
    tokens_entrada = 0
    tokens_saida = 0
    t0 = time.time()
    max_turnos = 6  # segurança contra loops infinitos

    for turno in range(max_turnos):
        response = _chamar_com_retry(
            model=MODEL,
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[tool_declarations],
                temperature=0.2,
            ),
        )

        # Contabilizar tokens
        if response.usage_metadata:
            tokens_entrada += response.usage_metadata.prompt_token_count or 0
            tokens_saida += response.usage_metadata.candidates_token_count or 0

        # Checar se o LLM quer chamar ferramentas
        candidate = response.candidates[0]
        has_function_call = any(
            part.function_call is not None for part in candidate.content.parts
        )

        if has_function_call:
            # Adicionar resposta do modelo ao histórico
            messages.append(candidate.content)

            # Executar cada ferramenta solicitada
            tool_response_parts = []
            for part in candidate.content.parts:
                if part.function_call:
                    fn_name = part.function_call.name
                    fn_args = dict(part.function_call.args) if part.function_call.args else {}
                    
                    print(f"  🔧 Turno {turno+1}: {fn_name}({fn_args})")
                    ferramentas_chamadas.append({"ferramenta": fn_name, "args": fn_args})

                    # Executar
                    fn = TOOL_FUNCTIONS.get(fn_name)
                    if fn:
                        resultado = fn(**fn_args)
                    else:
                        resultado = f"Ferramenta '{fn_name}' não encontrada."

                    tool_response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={"result": resultado},
                        )
                    )

            # Adicionar resultados das ferramentas ao histórico
            messages.append(types.Content(role="user", parts=tool_response_parts))

        else:
            # LLM respondeu com texto — deve ser o parecer final
            tempo_total = time.time() - t0
            texto = response.text.strip()

            # Limpar backticks de markdown
            if texto.startswith("```"):
                texto = texto.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            # Tentar validar com Pydantic
            try:
                parecer = ParecerLLM.model_validate_json(texto)
                parecer_dict = parecer.model_dump()
            except (ValidationError, Exception) as e:
                print(f"  ⚠️ Validação falhou ({e}), tentando extrair JSON do texto...")
                # Tentar encontrar JSON no meio do texto
                try:
                    start = texto.index("{")
                    end = texto.rindex("}") + 1
                    json_str = texto[start:end]
                    parecer = ParecerLLM.model_validate_json(json_str)
                    parecer_dict = parecer.model_dump()
                except Exception:
                    parecer_dict = {
                        "nivel_risco": "indeterminado",
                        "tipologia_suspeita": "erro na análise",
                        "red_flags": [],
                        "justificativa": texto,
                    }

            return {
                "cliente_id": cliente_id,
                "parecer": parecer_dict,
                "ferramentas_chamadas": ferramentas_chamadas,
                "tokens_entrada": tokens_entrada,
                "tokens_saida": tokens_saida,
                "tempo_segundos": round(tempo_total, 2),
                "turnos": turno + 1,
            }

    # Se esgotou os turnos sem parecer
    return {
        "cliente_id": cliente_id,
        "parecer": {
            "nivel_risco": "indeterminado",
            "tipologia_suspeita": "timeout do agente",
            "red_flags": [],
            "justificativa": "Agente não produziu parecer dentro do limite de turnos.",
        },
        "ferramentas_chamadas": ferramentas_chamadas,
        "tokens_entrada": tokens_entrada,
        "tokens_saida": tokens_saida,
        "tempo_segundos": round(time.time() - t0, 2),
        "turnos": max_turnos,
    }


# ── Execução em lote ────────────────────────────────────────────────────
def executar_lote(df) -> list[dict]:
    """
    Roda o agente sobre os top 10 clientes sinalizados.

    O espaçamento entre requisições é controlado pelo rate limiter global
    (_aguardar_rate_limit), que entra em ação antes de qualquer chamada
    à API — não é preciso pausa manual adicional aqui.
    """
    top10 = top_10_sinalizados(df)
    clientes = top10["cliente_id"].tolist()

    print(f"\n{'='*60}")
    print(f"Executando agente sobre {len(clientes)} clientes")
    print(f"{'='*60}\n")

    resultados = []
    for i, cid in enumerate(clientes, 1):
        # Pular cliente que já tem parecer salvo de uma execução anterior
        checkpoint_path = OUTPUTS_DIR / f"parecer_{cid}.json"
        if checkpoint_path.exists():
            print(f"\n[{i}/{len(clientes)}] {cid} já processado (outputs/parecer_{cid}.json) — pulando.")
            with open(checkpoint_path, encoding="utf-8") as f:
                resultados.append(json.load(f))
            continue

        print(f"\n[{i}/{len(clientes)}] Analisando {cid}...")
        resultado = analisar_cliente(cid, df)
        resultados.append(resultado)
        print(
            f"  ✅ Risco: {resultado['parecer']['nivel_risco']} | "
            f"Ferramentas: {len(resultado['ferramentas_chamadas'])} | "
            f"Tempo: {resultado['tempo_segundos']}s"
        )

        # Salvar imediatamente (checkpoint) — se o lote for interrompido,
        # o progresso já feito não se perde
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)

    return resultados


def salvar_resultados(resultados: list[dict]):
    """Salva resultados individuais e resumo em outputs/."""
    # Salvar cada parecer individualmente
    for r in resultados:
        path = OUTPUTS_DIR / f"parecer_{r['cliente_id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)

    # Salvar resumo consolidado
    resumo_path = OUTPUTS_DIR / "resumo_lote.json"
    with open(resumo_path, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    # Resumo em tabela com pandas
    import pandas as pd

    resumo_df = pd.DataFrame([
        {
            "cliente_id": r["cliente_id"],
            "nivel_risco": r["parecer"]["nivel_risco"],
            "tipologia": r["parecer"]["tipologia_suspeita"],
            "n_red_flags": len(r["parecer"]["red_flags"]),
            "n_ferramentas": len(r["ferramentas_chamadas"]),
            "tokens_entrada": r["tokens_entrada"],
            "tokens_saida": r["tokens_saida"],
            "tempo_s": r["tempo_segundos"],
            "turnos": r["turnos"],
        }
        for r in resultados
    ])

    resumo_csv_path = OUTPUTS_DIR / "resumo_lote.csv"
    resumo_df.to_csv(resumo_csv_path, index=False)

    print(f"\n{'='*60}")
    print("RESUMO DA EXECUÇÃO EM LOTE")
    print(f"{'='*60}")
    print(resumo_df.to_string(index=False))
    print(f"\nTokens totais — entrada: {resumo_df['tokens_entrada'].sum()}, saída: {resumo_df['tokens_saida'].sum()}")
    print(f"Tempo total: {resumo_df['tempo_s'].sum():.1f}s")
    print(f"\nArquivos salvos em {OUTPUTS_DIR}/")

    return resumo_df


# ── Main ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Carregando e preparando dados...")
    df, taxa = carregar_e_limpar("dados/dados_nivel_2.json")
    df = aplicar_regras(df)

    resultados = executar_lote(df)
    resumo = salvar_resultados(resultados)
