"""
Funções compartilhadas de limpeza e regras determinísticas.
Reaproveita a lógica do Nível 1 para qualquer base de operações.
"""

import json
import pandas as pd


def carregar_e_limpar(caminho_json: str) -> tuple[pd.DataFrame, float]:
    """
    Carrega o JSON de operações, aplica limpeza e retorna (df, taxa_cambio).
    
    Limpeza aplicada:
    1. Remove linhas 100% duplicadas (mesmo id + mesmos campos)
    2. Mantém operações com data nula (valor continua válido)
    3. Cria coluna 'valor_brl' normalizando USD → BRL
    """
    with open(caminho_json, encoding="utf-8") as f:
        dados = json.load(f)

    taxa = dados["taxa_cambio_usd_brl"]
    df = pd.DataFrame(dados["operacoes"])

    # 1. Duplicatas exatas
    antes = len(df)
    df = df.drop_duplicates()
    removidas = antes - len(df)

    # 2. Data nula — manter, só registrar
    nulos_data = df["data"].isnull().sum()

    # 3. Normalizar para BRL
    df["valor_brl"] = df.apply(
        lambda r: r["valor"] * taxa if r["moeda"] == "USD" else r["valor"],
        axis=1,
    )

    print(f"[utils] Carregado: {antes} ops → {len(df)} após remover {removidas} duplicata(s)")
    print(f"[utils] Datas nulas (mantidas): {nulos_data} | Operações USD convertidas: {(df['moeda']=='USD').sum()}")

    return df, taxa


def aplicar_regra1_fracionamento(df: pd.DataFrame) -> list[str]:
    """
    Regra 1 — Fracionamento (smurfing).
    Retorna lista de cliente_ids sinalizados.
    
    Critério: em uma MESMA data, 3+ operações com soma > R$ 50k
    e NENHUMA operação isolada >= R$ 20k.
    """
    grupo = df.groupby(["cliente_id", "data"]).agg(
        n_ops=("valor_brl", "count"),
        soma_dia=("valor_brl", "sum"),
        max_op=("valor_brl", "max"),
    ).reset_index()

    mask = (grupo["n_ops"] >= 3) & (grupo["soma_dia"] > 50_000) & (grupo["max_op"] < 20_000)
    return sorted(grupo[mask]["cliente_id"].unique().tolist())


def aplicar_regra2_valor_atipico(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regra 2 — Valor atípico.
    Retorna DataFrame com colunas extras: mediana_brl, limiar_5x, flag_regra2.
    
    Critério: operação com valor_brl > 5× a mediana do cliente,
    apenas para clientes com 4+ operações.
    """
    stats = df.groupby("cliente_id")["valor_brl"].agg(["median", "count"]).reset_index()
    stats.columns = ["cliente_id", "mediana_brl", "n_ops_cliente"]
    stats_4plus = stats[stats["n_ops_cliente"] >= 4].copy()
    stats_4plus["limiar_5x"] = stats_4plus["mediana_brl"] * 5

    df = df.merge(
        stats_4plus[["cliente_id", "mediana_brl", "limiar_5x"]],
        on="cliente_id",
        how="left",
    )
    df["flag_regra2_valor_atipico"] = df["valor_brl"] > df["limiar_5x"]

    return df


def aplicar_regras(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica ambas as regras e retorna o DataFrame com flags.
    """
    clientes_r1 = aplicar_regra1_fracionamento(df)
    df["flag_regra1_fracionamento"] = df["cliente_id"].isin(clientes_r1)

    df = aplicar_regra2_valor_atipico(df)

    return df


def top_10_sinalizados(df: pd.DataFrame) -> pd.DataFrame:
    """
    Retorna os 10 clientes mais sinalizados, ordenados por:
    1. Número total de sinalizações (desc)
    2. Volume total em BRL (desc) como desempate
    """
    # Regra 1: flag booleana por cliente (0 ou 1)
    r1_por_cliente = df.groupby("cliente_id")["flag_regra1_fracionamento"].any().astype(int)
    r1_por_cliente.name = "flags_regra1"

    # Regra 2: contar operações flagged por cliente
    r2_por_cliente = df.groupby("cliente_id")["flag_regra2_valor_atipico"].sum().fillna(0).astype(int)
    r2_por_cliente.name = "flags_regra2"

    # Volume total
    volume = df.groupby("cliente_id")["valor_brl"].sum()
    volume.name = "volume_total_brl"

    resumo = pd.concat([r1_por_cliente, r2_por_cliente, volume], axis=1).reset_index()
    resumo["total_sinalizacoes"] = resumo["flags_regra1"] + resumo["flags_regra2"]

    resumo = resumo.sort_values(
        ["total_sinalizacoes", "volume_total_brl"],
        ascending=[False, False],
    ).head(10)

    return resumo
