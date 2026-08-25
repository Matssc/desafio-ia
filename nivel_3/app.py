"""
Nível 3 — Interface conversacional (Streamlit)

Permite conversar livremente sobre um cliente sinalizado: você pergunta,
o agente decide quais das 3 ferramentas do Nível 2 chamar para responder,
e mantém o contexto da conversa.

Também expõe um botão para rodar o parecer formal e estruturado
(reaproveita nivel_2/agente.py sem duplicar lógica).

Uso:
    streamlit run nivel_3/app.py
"""

import sys
import os
from pathlib import Path

# Garante que "nivel_2" seja importável a partir da raiz do projeto,
# independente de onde o streamlit for iniciado
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from google.genai import types

from nivel_2 import agente as ag
from nivel_2.utils import carregar_e_limpar, aplicar_regras, top_10_sinalizados


# ── Configuração da página ───────────────────────────────────────────────
st.set_page_config(page_title="PLD/FT — Assistente de Compliance", page_icon="🔍", layout="wide")


# ── Carregamento de dados (cacheado — não recarrega a cada interação) ───
@st.cache_resource
def carregar_dados():
    df, taxa = carregar_e_limpar("dados/dados_nivel_2.json")
    df = aplicar_regras(df)
    top10 = top_10_sinalizados(df)
    return df, taxa, top10


df, taxa, top10 = carregar_dados()


# ── Sidebar: seleção de cliente e resumo das regras ─────────────────────
st.sidebar.title("🔍 Clientes sinalizados")
st.sidebar.caption("Top 10 por número de sinalizações das regras determinísticas")

cliente_selecionado = st.sidebar.selectbox(
    "Selecione um cliente para investigar:",
    options=top10["cliente_id"].tolist(),
)

linha = top10[top10["cliente_id"] == cliente_selecionado].iloc[0]
st.sidebar.metric("Regra 1 (fracionamento)", "ATIVADA" if linha["flags_regra1"] else "não ativada")
st.sidebar.metric("Regra 2 (valor atípico)", f"{int(linha['flags_regra2'])} operação(ões)")
st.sidebar.metric("Volume total (BRL)", f"R$ {linha['volume_total_brl']:,.2f}")

st.sidebar.divider()
st.sidebar.caption(
    "As regras acima já foram calculadas em pandas (nivel_2/utils.py). "
    "O LLM nunca recalcula — apenas interpreta o que já foi apurado."
)


# ── Estado da conversa (por cliente selecionado) ─────────────────────────
if "historico_chat" not in st.session_state:
    st.session_state.historico_chat = {}

if cliente_selecionado not in st.session_state.historico_chat:
    st.session_state.historico_chat[cliente_selecionado] = []

mensagens = st.session_state.historico_chat[cliente_selecionado]


# ── Cabeçalho ─────────────────────────────────────────────────────────────
st.title("Assistente de Compliance PLD/FT")
st.caption(
    f"Conversando sobre **{cliente_selecionado}** — pergunte sobre operações, "
    "canais, dias específicos, ou peça um parecer completo."
)

col1, col2 = st.columns([3, 1])
with col2:
    if st.button("📋 Gerar parecer formal", use_container_width=True):
        with st.spinner("Analisando (isso usa a mesma lógica do Nível 2)..."):
            resultado = ag.analisar_cliente(cliente_selecionado, df)
        st.session_state[f"parecer_{cliente_selecionado}"] = resultado

# Exibir parecer formal se já foi gerado
parecer_key = f"parecer_{cliente_selecionado}"
if parecer_key in st.session_state:
    p = st.session_state[parecer_key]["parecer"]
    cor = {"alto": "🔴", "médio": "🟡", "baixo": "🟢"}.get(p["nivel_risco"], "⚪")
    with st.expander(f"{cor} Parecer formal — risco: {p['nivel_risco']}", expanded=True):
        st.write(f"**Tipologia:** {p['tipologia_suspeita']}")
        st.write("**Red flags:**")
        for flag in p["red_flags"]:
            st.write(f"- {flag}")
        st.write(f"**Justificativa:** {p['justificativa']}")
        st.caption(
            f"Ferramentas usadas: {len(st.session_state[parecer_key]['ferramentas_chamadas'])} | "
            f"Tokens: {st.session_state[parecer_key]['tokens_entrada']} entrada / "
            f"{st.session_state[parecer_key]['tokens_saida']} saída"
        )

st.divider()


# ── Histórico do chat ─────────────────────────────────────────────────────
for msg in mensagens:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("ferramentas"):
            st.caption("🔧 Ferramentas usadas: " + ", ".join(msg["ferramentas"]))


# ── Loop do agente conversacional (livre, guiado pela pergunta do usuário) ─
def responder(pergunta: str, cliente_id: str, historico_mensagens: list) -> tuple[str, list]:
    """
    Processa uma pergunta livre do usuário sobre o cliente selecionado.
    Reaproveita as mesmas ferramentas e declarações do agente do Nível 2,
    mas permite conversa livre em vez de um fluxo fixo de análise.
    """
    contexto_inicial = ag.montar_contexto_inicial(cliente_id, df)

    system_prompt = (
        "Você é um assistente de compliance especializado em PLD/FT, conversando "
        "com um analista humano sobre um cliente específico.\n\n"
        f"Contexto inicial do cliente já calculado (não recalcule nada):\n{contexto_inicial}\n\n"
        "Use as ferramentas disponíveis para responder com precisão às perguntas do analista. "
        "Seja direto e cite números/datas específicas quando relevante. "
        "Não invente dados — se não souber, chame uma ferramenta para descobrir."
    )

    # Reconstruir histórico como Content do Gemini
    contents = []
    for m in historico_mensagens:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=pergunta)]))

    ferramentas_usadas = []
    max_turnos = 4

    for _ in range(max_turnos):
        response = ag._chamar_com_retry(
            model=ag.MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[ag.tool_declarations],
                temperature=0.3,
            ),
        )

        candidate = response.candidates[0]
        tem_function_call = any(p.function_call is not None for p in candidate.content.parts)

        if tem_function_call:
            contents.append(candidate.content)
            tool_response_parts = []
            for part in candidate.content.parts:
                if part.function_call:
                    fn_name = part.function_call.name
                    fn_args = dict(part.function_call.args) if part.function_call.args else {}
                    ferramentas_usadas.append(fn_name)

                    fn = ag.TOOL_FUNCTIONS.get(fn_name)
                    resultado = fn(**fn_args) if fn else f"Ferramenta '{fn_name}' não encontrada."

                    tool_response_parts.append(
                        types.Part.from_function_response(name=fn_name, response={"result": resultado})
                    )
            contents.append(types.Content(role="user", parts=tool_response_parts))
        else:
            return response.text.strip(), ferramentas_usadas

    return "Não consegui concluir a resposta dentro do limite de turnos.", ferramentas_usadas


# ── Campo de entrada do chat ──────────────────────────────────────────────
pergunta = st.chat_input(f"Pergunte algo sobre {cliente_selecionado}...")

if pergunta:
    mensagens.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    with st.chat_message("assistant"):
        with st.spinner("Investigando..."):
            resposta, ferramentas = responder(pergunta, cliente_selecionado, mensagens[:-1])
        st.markdown(resposta)
        if ferramentas:
            st.caption("🔧 Ferramentas usadas: " + ", ".join(ferramentas))

    mensagens.append({"role": "assistant", "content": resposta, "ferramentas": ferramentas})
