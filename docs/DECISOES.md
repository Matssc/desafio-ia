# Decisões técnicas

Este documento registra trade-offs, limitações e planos — não repete o que o código já mostra.

## Nível 1

_(preencher conforme avançamos)_

## Nível 2

**Dois modelos diferentes entre Nível 1 e Nível 2 — por quê:**

O Nível 1 usa `gemini-3.6-flash` e já está completo (2 chamadas de API
no total, para os prompts V1 e V2 — bem dentro da cota de 20/dia desse
modelo). Não há necessidade de reexecutar o Nível 1 com outro modelo.

O Nível 2 processa 10 clientes em lote, cada um consumindo de 2 a 6
chamadas — 20-60 chamadas no total, o que estoura a cota diária de
20 requisições do `gemini-3.6-flash`. Por isso o Nível 2 usa
`gemini-3.1-flash-lite`, que a Google otimiza para alto volume
(confirmado em ai.dev/rate-limit: 15 RPM / 250K TPM / 500 RPD).

Se o Nível 1 for reexecutado do zero após esse ponto, ele também usará
o modelo definido em `GEMINI_MODEL` no `.env` no momento da execução
(o código não fixa o modelo) — isso é esperado e não invalida os
resultados já salvos no notebook.

**Limitação de cota da camada gratuita (Google AI Studio):**

Durante os testes, o modelo `gemini-3.6-flash` retornou erro 429 com
`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `limit: 20` —
ou seja, apenas 20 requisições por dia inteiro nesse modelo específico na
camada gratuita. Como cada cliente analisado pelo agente consome de 2 a 6
requisições (histórico + ferramentas + parecer final), isso inviabilizava
rodar o lote completo de 10 clientes em um único dia com esse modelo.

Solução adotada: trocar para `gemini-3.1-flash-lite` (15 RPM / 500 RPD
confirmados em ai.dev/rate-limit) via variável de ambiente `GEMINI_MODEL`
(nenhuma mudança de código necessária — o valor já é lido dinamicamente
do `.env`). Modelos da linha "Lite" da Google têm cota diária bem maior
por serem otimizados para alto volume — 500 RPD é suficiente para rodar
o lote completo de 10 clientes mesmo com retentativas.

Também foram implementadas duas proteções no `agente.py` independente
do modelo escolhido:
- Rate limiter com janela deslizante de 60s, que conta todas as chamadas
  (inclusive entre turnos do mesmo cliente) e pausa antes de estourar o
  limite por minuto.
- Checkpoint incremental: cada parecer é salvo em `outputs/` assim que
  fica pronto, e um cliente já processado é pulado se o lote precisar
  ser reiniciado — evita perder progresso e gastar cota reprocessando.

## Nível 3

_(preencher conforme avançamos)_

## Limitações gerais

_(preencher ao final)_

## O que faria com mais tempo

_(preencher ao final)_
