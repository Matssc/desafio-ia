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

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # preencha GOOGLE_API_KEY com sua própria chave
```

- Nível 1: abra `nivel_1/nivel_1.ipynb` (já entregue com saídas executadas).
- Nível 2: `python -m nivel_2.agente` (em construção).

## Status

Em desenvolvimento — acompanhe o progresso real em `ENTREGA.yaml`.

## Modelo e provedor

- **Provedor:** Google AI Studio (camada gratuita)
- **Modelo:** `gemini-3.6-flash`

Escolhido pelo rate limit generoso na camada free e SDK oficial simples (`google-genai`),
além de bom desempenho em tarefas de análise estruturada.
