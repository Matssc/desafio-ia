"""
Ferramentas de consulta à base de operações.
Cada função recebe parâmetros simples e retorna uma string formatada
pronta para ser consumida pelo LLM como contexto.

O agente (agente.py) decide quais ferramentas chamar para cada cliente.
"""

import pandas as pd
from nivel_2.utils import carregar_e_limpar, aplicar_regras


# Carregar e preparar dados uma única vez (singleton)
_df, _taxa = carregar_e_limpar("dados/dados_nivel_2.json")
_df = aplicar_regras(_df)


def historico_cliente(cliente_id: str) -> str:
    """
    Resumo agregado das operações de um cliente.
    Retorna: total de operações, volume total em BRL, período,
    contrapartes, flags das regras determinísticas.
    """
    ops = _df[_df["cliente_id"] == cliente_id]

    if ops.empty:
        return f"Cliente {cliente_id} não encontrado na base."

    volume = ops["valor_brl"].sum()
    n_ops = len(ops)
    datas = ops["data"].dropna()
    periodo = f"{datas.min()} a {datas.max()}" if len(datas) > 0 else "sem datas"
    contrapartes = ops["contraparte"].unique().tolist()
    canais = ops["canal"].value_counts().to_dict()
    flag_r1 = ops["flag_regra1_fracionamento"].any()
    flag_r2 = ops["flag_regra2_valor_atipico"].any()
    n_r2 = int(ops["flag_regra2_valor_atipico"].sum()) if "flag_regra2_valor_atipico" in ops.columns else 0
    mediana = ops["valor_brl"].median()

    return (
        f"HISTÓRICO DO CLIENTE {cliente_id}\n"
        f"  Período: {periodo}\n"
        f"  Total de operações: {n_ops}\n"
        f"  Volume total (BRL): R$ {volume:,.2f}\n"
        f"  Mediana por operação (BRL): R$ {mediana:,.2f}\n"
        f"  Canais utilizados: {canais}\n"
        f"  Contrapartes: {contrapartes}\n"
        f"  Regra 1 (fracionamento): {'SIM' if flag_r1 else 'NÃO'}\n"
        f"  Regra 2 (valor atípico): {'SIM' if flag_r2 else 'NÃO'} ({n_r2} operação(ões))\n"
    )


def operacoes_do_dia(cliente_id: str, data: str) -> str:
    """
    Recorte das operações de um cliente em um dia específico.
    Retorna: lista de operações com id, valor, canal, tipo, contraparte.
    """
    ops = _df[(_df["cliente_id"] == cliente_id) & (_df["data"] == data)]

    if ops.empty:
        return f"Nenhuma operação encontrada para {cliente_id} em {data}."

    soma = ops["valor_brl"].sum()
    linhas = []
    for _, r in ops.iterrows():
        flag = " ⚠️" if r.get("flag_regra2_valor_atipico", False) else ""
        linhas.append(
            f"  {r['id']} | R$ {r['valor_brl']:,.2f} | {r['canal']} | "
            f"{r['tipo']} | {r['contraparte']}{flag}"
        )

    return (
        f"OPERAÇÕES DE {cliente_id} EM {data}\n"
        f"  Quantidade: {len(ops)}\n"
        f"  Soma do dia (BRL): R$ {soma:,.2f}\n"
        f"  Detalhamento:\n" + "\n".join(linhas) + "\n"
    )


def perfil_canal(cliente_id: str) -> str:
    """
    Distribuição de uso por canal de um cliente.
    Retorna: contagem e volume por canal, percentuais.
    """
    ops = _df[_df["cliente_id"] == cliente_id]

    if ops.empty:
        return f"Cliente {cliente_id} não encontrado na base."

    resumo = ops.groupby("canal").agg(
        n_ops=("id", "count"),
        volume_brl=("valor_brl", "sum"),
    ).reset_index()

    total_ops = resumo["n_ops"].sum()
    total_vol = resumo["volume_brl"].sum()

    resumo["pct_ops"] = (resumo["n_ops"] / total_ops * 100).round(1)
    resumo["pct_vol"] = (resumo["volume_brl"] / total_vol * 100).round(1)
    resumo = resumo.sort_values("volume_brl", ascending=False)

    linhas = []
    for _, r in resumo.iterrows():
        linhas.append(
            f"  {r['canal']:<10} | {r['n_ops']:>3} ops ({r['pct_ops']:>5.1f}%) | "
            f"R$ {r['volume_brl']:>12,.2f} ({r['pct_vol']:>5.1f}%)"
        )

    return (
        f"PERFIL DE CANAL — {cliente_id}\n"
        f"  Total: {total_ops} operações, R$ {total_vol:,.2f}\n"
        + "\n".join(linhas) + "\n"
    )


# Registro de ferramentas para o agente
FERRAMENTAS = {
    "historico_cliente": {
        "funcao": historico_cliente,
        "descricao": "Resumo agregado de todas as operações do cliente (volume, período, flags, contrapartes).",
        "parametros": {"cliente_id": "str — identificador do cliente (ex: CLI-001)"},
    },
    "operacoes_do_dia": {
        "funcao": operacoes_do_dia,
        "descricao": "Lista detalhada das operações de um cliente em um dia específico.",
        "parametros": {
            "cliente_id": "str — identificador do cliente",
            "data": "str — data no formato YYYY-MM-DD",
        },
    },
    "perfil_canal": {
        "funcao": perfil_canal,
        "descricao": "Distribuição de uso por canal (pix, ted, boleto, etc.) com volume e percentual.",
        "parametros": {"cliente_id": "str — identificador do cliente"},
    },
}


if __name__ == "__main__":
    # Teste rápido das ferramentas
    print("=" * 60)
    print(historico_cliente("CLI-014"))
    print("=" * 60)
    # Pegar uma data com operações para CLI-014
    ops_014 = _df[_df["cliente_id"] == "CLI-014"]["data"].dropna().value_counts()
    if len(ops_014) > 0:
        dia_teste = ops_014.index[0]
        print(operacoes_do_dia("CLI-014", dia_teste))
    print("=" * 60)
    print(perfil_canal("CLI-014"))
