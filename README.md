# Desafio Técnico — Estágio em Engenharia de IA

Pipeline de triagem de operações financeiras para prevenção à lavagem de dinheiro,
combinando **regras determinísticas** (cálculo, em pandas) com um **LLM**
(interpretação e redação de parecer).

> Todos os dados são fictícios, gerados para fins de avaliação.

## Estrutura

```
├── dados/              # arquivos de entrada (anexos do e-mail do desafio)
├── nivel_1/            # notebook: limpeza, regras determinísticas, 1ª análise com LLM
├── nivel_2/            # ferramentas, agente, execução em lote, confronto regra x modelo
├── nivel_3/            # trilha opcional (a definir)
├── outputs/            # resultados salvos das execuções
├── docs/
│   ├── DECISOES.md     # trade-offs, limitações, plano do que falta
│   └── USO_DE_IA.md    # como IA foi usada no desenvolvimento
├── ENTREGA.yaml         # autodeclaração de status por item
├── requirements.txt
└── .env.example
```

## O que foi concluído

Pipeline completo de triagem PLD/FT com os 3 níveis funcionando:
regras determinísticas em pandas, agente com function calling decidindo
quais ferramentas investigar por cliente, e interface conversacional
para o analista explorar os casos.

**Achado principal:** ao confrontar um critério simples baseado só na
contagem de sinalizações das regras com o risco atribuído pelo agente,
a taxa de concordância foi de apenas **50%** (5 de 10 clientes). Nas
divergências, o agente considerou contexto que a regra sozinha não
enxerga — concentração temporal, natureza das contrapartes, uso de
espécie, coerência entre canal e perfil declarado. Detalhes e exemplos
concretos em `docs/DECISOES.md`.

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # preencha GOOGLE_API_KEY com sua própria chave
```

- **Nível 1**: abra `nivel_1/nivel_1.ipynb` (completo, com saídas executadas).
- **Nível 2**:
  - `python -m nivel_2.agente` — roda o agente sobre os 10 clientes mais sinalizados
    (usa API, consome cota; resultados já estão salvos em `outputs/`)
  - `python -m nivel_2.confronto` — compara regra vs. parecer do agente
    (não usa API, só lê os pareceres já salvos)
  - `python -m nivel_2.tools` — testa as 3 ferramentas isoladamente
    (não usa API)

## Status

- **Nível 1**: completo (limpeza, regras, validação, análise com LLM, comparação de prompts)
- **Nível 2**: completo (regras em escala, ferramentas, agente, execução em lote, confronto)
- **Nível 3**: não iniciado

Detalhamento item a item em `ENTREGA.yaml`.

## Modelo e provedor

- **Provedor:** Google AI Studio (camada gratuita)
- **Modelos:** `gemini-3.6-flash` (Nível 1) e `gemini-3.1-flash-lite` (Nível 2)

Dois modelos diferentes por necessidade real de cota: o Nível 1 faz poucas
chamadas (cabe na cota diária do 3.6-flash), enquanto o Nível 2 processa
10 clientes em lote e precisa da cota diária maior do Flash-Lite. Detalhes
em `docs/DECISOES.md`.
