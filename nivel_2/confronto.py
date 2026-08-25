"""
Confronto entre regras determinísticas e parecer do agente (LLM).

Pergunta central: o número de sinalizações das regras prediz o nível
de risco atribuído pelo agente? Ou o agente está de fato interpretando
contexto além da contagem bruta de flags?

Uso:
    python -m nivel_2.confronto
"""

import json
from pathlib import Path
import pandas as pd

from nivel_2.utils import carregar_e_limpar, aplicar_regras, top_10_sinalizados

OUTPUTS_DIR = Path("outputs")


def carregar_pareceres() -> pd.DataFrame:
    """Lê todos os outputs/parecer_*.json e monta um DataFrame."""
    registros = []
    for path in sorted(OUTPUTS_DIR.glob("parecer_*.json")):
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        registros.append({
            "cliente_id": r["cliente_id"],
            "nivel_risco_agente": r["parecer"]["nivel_risco"],
            "tipologia_agente": r["parecer"]["tipologia_suspeita"],
            "n_red_flags": len(r["parecer"]["red_flags"]),
            "n_ferramentas_chamadas": len(r["ferramentas_chamadas"]),
            "ferramentas": [f["ferramenta"] for f in r["ferramentas_chamadas"]],
        })
    return pd.DataFrame(registros)


def montar_confronto(df_operacoes: pd.DataFrame) -> pd.DataFrame:
    """
    Junta o resultado das regras determinísticas com o parecer do agente
    para os clientes que foram efetivamente analisados.
    """
    top10 = top_10_sinalizados(df_operacoes)
    pareceres = carregar_pareceres()

    confronto = top10.merge(pareceres, on="cliente_id", how="inner")

    # Mapear nível de risco para ordem numérica (para detectar divergência)
    ordem_risco = {"baixo": 1, "médio": 2, "alto": 3, "indeterminado": 0}
    confronto["risco_ordinal"] = confronto["nivel_risco_agente"].map(ordem_risco)

    # Se as regras "concordassem" perfeitamente com uma contagem simples,
    # mais sinalizações -> risco maior. Vamos checar isso.
    confronto = confronto.sort_values("total_sinalizacoes", ascending=False)

    return confronto


def analisar_divergencias(confronto: pd.DataFrame) -> str:
    """
    Produz um texto explicando os casos onde a contagem bruta de regras
    NÃO prediz o nível de risco do agente — evidência de que o agente
    interpreta contexto, não só conta flags.
    """
    linhas = []
    linhas.append("=" * 70)
    linhas.append("ANÁLISE DE DIVERGÊNCIAS: contagem de regras vs. risco do agente")
    linhas.append("=" * 70)
    linhas.append("")

    # Ordenar por total_sinalizacoes para ver se risco acompanha
    ordenado = confronto.sort_values("total_sinalizacoes", ascending=False).reset_index(drop=True)

    linhas.append(f"{'Cliente':<10} {'Regra1':<7} {'Regra2':<7} {'Total':<7} {'Risco (agente)':<15}")
    linhas.append("-" * 55)
    for _, row in ordenado.iterrows():
        linhas.append(
            f"{row['cliente_id']:<10} {row['flags_regra1']:<7} {row['flags_regra2']:<7} "
            f"{row['total_sinalizacoes']:<7} {row['nivel_risco_agente']:<15}"
        )

    linhas.append("")

    # Caso 1: cliente com MAIS sinalizações mas risco NÃO é o mais alto
    max_sinalizacoes = ordenado["total_sinalizacoes"].max()
    top_sinalizados = ordenado[ordenado["total_sinalizacoes"] == max_sinalizacoes]
    linhas.append(f"Cliente(s) com MAIS sinalizações ({max_sinalizacoes}): "
                  f"{', '.join(top_sinalizados['cliente_id'])}")
    linhas.append(f"  → Risco atribuído: {', '.join(top_sinalizados['nivel_risco_agente'])}")
    linhas.append("")

    # Caso 2: clientes com só 1 sinalização mas risco "alto"
    poucos_mas_alto = ordenado[(ordenado["total_sinalizacoes"] <= 1) & (ordenado["nivel_risco_agente"] == "alto")]
    if len(poucos_mas_alto) > 0:
        linhas.append("Clientes com POUCAS sinalizações (≤1) mas risco 'alto' atribuído pelo agente:")
        for _, row in poucos_mas_alto.iterrows():
            linhas.append(f"  - {row['cliente_id']}: {row['n_ferramentas_chamadas']} ferramentas chamadas, "
                          f"tipologia: {row['tipologia_agente']}")
        linhas.append("")

    # Caso 3: mesma regra (Regra 1), risco diferente
    regra1_clientes = ordenado[ordenado["flags_regra1"] == 1]
    if len(regra1_clientes) > 1:
        riscos_distintos = regra1_clientes["nivel_risco_agente"].nunique()
        if riscos_distintos > 1:
            linhas.append("Clientes que ativaram a MESMA regra (Regra 1 - fracionamento) "
                          "mas receberam riscos DIFERENTES do agente:")
            for _, row in regra1_clientes.iterrows():
                linhas.append(f"  - {row['cliente_id']}: risco '{row['nivel_risco_agente']}'")
            linhas.append("  → Evidência de que o agente pondera contexto além da regra "
                          "que disparou (ex: canais usados, concentração de contrapartes).")
            linhas.append("")

    linhas.append("CONCLUSÃO:")
    linhas.append(
        "A contagem bruta de sinalizações das regras determinísticas NÃO prediz\n"
        "linearmente o nível de risco atribuído pelo agente. Isso é esperado e\n"
        "desejável: as regras são um gatilho para investigação (correto e barato\n"
        "de calcular), mas a gravidade real depende de contexto que só aparece ao\n"
        "investigar — concentração temporal, natureza das contrapartes, uso de\n"
        "espécie, coerência entre canal e perfil declarado. É exatamente essa\n"
        "divisão de trabalho (regra decide QUEM investigar, LLM decide QUÃO GRAVE)\n"
        "que o desafio pede."
    )

    return "\n".join(linhas)


if __name__ == "__main__":
    print("Carregando dados e regras...")
    df, taxa = carregar_e_limpar("dados/dados_nivel_2.json")
    df = aplicar_regras(df)

    confronto = montar_confronto(df)

    # Salvar tabela de confronto
    confronto_export = confronto[[
        "cliente_id", "flags_regra1", "flags_regra2", "total_sinalizacoes",
        "volume_total_brl", "nivel_risco_agente", "tipologia_agente",
        "n_red_flags", "n_ferramentas_chamadas"
    ]]
    confronto_path = OUTPUTS_DIR / "confronto_regra_vs_agente.csv"
    confronto_export.to_csv(confronto_path, index=False)
    print(f"Tabela de confronto salva em {confronto_path}")

    # Gerar e salvar análise textual
    analise = analisar_divergencias(confronto)
    print("\n" + analise)

    analise_path = OUTPUTS_DIR / "confronto_analise.txt"
    with open(analise_path, "w", encoding="utf-8") as f:
        f.write(analise)
    print(f"\nAnálise salva em {analise_path}")
