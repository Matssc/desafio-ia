# Decisões técnicas

Este documento registra trade-offs, limitações e planos — não repete o que o código já mostra.

## Nível 1

**Duplicata: remover em vez de agregar.**
`OP-0007` aparecia duas vezes com todos os campos idênticos. A decisão foi
usar `drop_duplicates()` simples (mantém a primeira ocorrência) em vez de
somar os valores — são o mesmo evento reportado duas vezes pelo sistema
de origem, não duas operações reais. Somar teria inflado artificialmente
o volume do cliente e poderia disparar a Regra 1 por engano.

**Data nula: manter a operação, não inventar data.**
`OP-0017` veio com `data: null` e a observação "data não capturada pelo
sistema". A operação foi mantida no dataset (o valor é real e válido),
mas a data ficou nula. Isso significa que essa operação nunca entra em
agrupamentos por dia (Regra 1), o que é o comportamento correto — não
temos como saber se ela faz parte de um padrão de fracionamento sem
saber quando ocorreu.

**Conversão de moeda: usar a taxa fornecida, sem arredondamento manual.**
A única operação em USD (`OP-0013`) foi convertida com a taxa exata do
JSON (5.4), sem arredondamentos intermediários, para não introduzir
erro de precisão nas comparações com os limiares das regras (R$ 50.000
e R$ 20.000).

**Separação regra/LLM.** Todo número que entra no prompt do LLM (soma,
mediana, contagem, comparação com limiar) já foi calculado em pandas
antes da chamada. O LLM nunca recebe a lista bruta de operações para
"fazer as contas sozinho" — ele recebe os resultados prontos e sua
única tarefa é interpretar e redigir. Isso é verificável olhando o
código: nenhuma célula que monta o prompt faz uma operação aritmética
sobre os dados brutos.

## Nível 2

**Design do agente: liberdade real de decisão, não um fluxo fixo.**
As 3 ferramentas (`historico_cliente`, `operacoes_do_dia`, `perfil_canal`)
são declaradas ao Gemini via function calling nativo, com descrições que
orientam quando cada uma é útil, mas o agente decide sozinho quais chamar,
quantas vezes, e em que ordem. Isso é visível nos resultados reais: CLI-013
resolveu em 3 chamadas / 4.31s, enquanto CLI-005 precisou de 5 chamadas
(3 dias diferentes investigados) / 32.14s. Um script fixo geraria o mesmo
padrão de chamadas para todo cliente; o agente não gerou.

**Achado do confronto: contagem de regras não prediz o risco do agente.**
Rodando `nivel_2/confronto.py` sobre os 10 pareceres reais:
- CLI-014 tem o MAIOR número de sinalizações (3× Regra 2), mas o agente
  deu risco **médio** — a leitura do agente foi que os valores atípicos,
  apesar de existirem, tinham contrapartes e canais coerentes com uma
  atividade comercial plausível.
- CLI-029 e CLI-017 ativaram a MESMA regra (Regra 1 — fracionamento),
  mas receberam riscos diferentes: CLI-029 → **alto** (justificado pela
  combinação com uso de espécie e 15 contrapartes distintas em menos de
  3 meses), CLI-017 → **médio** (fracionamento isolado, sem outros
  agravantes).
- Interpretação: as regras determinísticas fazem bem o trabalho para o
  qual foram desenhadas — sinalizar candidatos a investigar, de forma
  barata e auditável. Mas a gravidade real de cada caso depende de
  contexto (concentração temporal, natureza das contrapartes, uso de
  espécie, coerência entre canal e perfil) que só aparece investigando
  — e é exatamente aí que o agente agrega valor sobre a regra sozinha.

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

**Trilha escolhida: C — Interface conversacional. Por quê.**

Das três trilhas (A — multiagente, B — servidor MCP, C — interface
conversacional), a C foi escolhida por dois motivos práticos, dado o
tempo restante do prazo de 24h:

1. **Menor risco de retrabalho.** Ela reaproveita 100% da lógica já
   testada e validada do Nível 2 (as 3 ferramentas, as declarações de
   function calling, o loop de decisão) sem precisar redesenhar nada —
   só muda a camada de interação (chat em vez de execução em lote).
   As trilhas A e B exigiriam desenhar uma nova arquitetura do zero
   (estado compartilhado entre agentes na A; um servidor stdio na B).

2. **Valor demonstrável imediato.** Uma interface conversacional é
   visualmente verificável na hora (dá pra ver o agente decidindo
   ferramentas em tempo real, conversar livremente sobre um cliente,
   comparar clientes diferentes na mesma conversa), o que facilita a
   avaliação em comparação com um fluxo multiagente ou um protocolo
   MCP que exigem mais contexto para serem "vistos" funcionando.

**Interface conversacional (Streamlit) — completa e funcionando.**

O código está em `nivel_3/app.py` e reaproveita integralmente a lógica
do agente do Nível 2 (mesmas ferramentas, mesmas declarações de function
calling) — nada foi duplicado. Mantém memória da conversa por cliente
via `st.session_state` (a conversa persiste mesmo trocando de cliente
na sidebar e voltando). Como as funções de ferramenta aceitam qualquer
`cliente_id` como parâmetro, o agente consegue investigar e comparar
outros clientes além do selecionado na sidebar, caso a pergunta do
analista peça isso — ex: "compare CLI-005 com CLI-014".

**Problema real enfrentado e resolvido: SSL quebrado no Windows/Anaconda.**
Ao instanciar o cliente `google-genai`, o Windows retornava
`FileNotFoundError` dentro de `ssl.create_default_context()`, mesmo com
o certificado do `certifi` existindo normalmente no disco (confirmado
com testes isolados). A mesma chamada funcionava sem problema nos
notebooks do Nível 1 e no `python -m nivel_2.agente` executado via
Jupyter — só falhava ao rodar via `streamlit run` num terminal comum.

Diagnóstico, por eliminação:
1. `ssl.create_default_context(cafile=certifi.where())` isolado → funciona.
2. Mesmo teste após `import google.genai` → também funciona.
3. Só falha ao **instanciar** `genai.Client(...)` de fato.

Isso indicou que algo na resolução interna de credenciais do
`google-genai`/`google-auth` (visível no log como
`Failed to get default SSL context from google-auth`) deixa a variável
de ambiente `SSL_CERT_FILE` num estado inválido antes de cair no
caminho manual de criação do contexto SSL.

**Correção aplicada em `nivel_2/agente.py`:** forçar `SSL_CERT_FILE`
explicitamente para o caminho do `certifi` antes de criar o cliente,
reverter qualquer patch do `truststore` (dependência do conda que
também mexe no módulo `ssl`), e desabilitar a resolução de credenciais
Vertex AI (`vertexai=False`, já que a autenticação é por API key direta).
Testado e confirmado funcionando no Windows após a correção.

## Limitações gerais

- **Amostra pequena para validar estatisticamente as regras.** Com 6
  clientes (Nível 1) e 30 clientes (Nível 2), os limiares escolhidos
  (soma > R$ 50.000, 5× a mediana) não foram calibrados contra uma base
  histórica real — são os valores especificados no enunciado. Em produção,
  esses limiares deveriam ser ajustados com dados rotulados reais.

- **O agente não tem memória entre clientes.** Cada `analisar_cliente()`
  começa do zero — se dois clientes tiverem contrapartes em comum (ex:
  ambos transacionam com "Trading XYZ"), o agente não percebe essa conexão
  porque analisa um cliente por vez, isoladamente. Detectar redes de
  contrapartes exigiria uma ferramenta adicional de "buscar outros
  clientes que transacionam com X".

- **Dependência de cota de API externa.** A escolha de modelo (documentada
  acima) foi determinada por restrições de infraestrutura do provedor, não
  só por qualidade do modelo. Isso é uma limitação prática de qualquer
  pipeline que dependa da camada gratuita de um provedor de LLM.

- **Validação Pydantic não garante correção semântica.** O schema
  `ParecerLLM` garante que a estrutura do JSON está correta (campos certos,
  tipos certos), mas não valida se o conteúdo faz sentido — por exemplo,
  não há checagem de que `nivel_risco` seja de fato coerente com os
  `red_flags` listados. Isso ficaria a cargo de um revisor humano.

- **Fragilidade de SSL em Windows/Anaconda com o SDK `google-genai`.**
  Como detalhado na seção do Nível 3, essa combinação específica pode
  causar `FileNotFoundError` ao instanciar o cliente, mesmo com o
  certificado presente e correto no disco. A correção já está aplicada
  em `nivel_2/agente.py` (força `SSL_CERT_FILE`, reverte patch do
  `truststore`, desabilita resolução Vertex AI), mas é uma fragilidade
  de ambiente que pode se repetir em outras máquinas Windows com
  configuração semelhante.

## O que faria com mais tempo

- **Multiagente** (Triador → Investigador → Redator) como evolução do
  Nível 3 atual, separando a decisão de "vale investigar" da redação do
  parecer final — permitiria auditar cada etapa isoladamente.
- **Calibração dos limiares das regras** contra uma base histórica maior,
  se disponível, em vez de usar os valores fixos do enunciado.
- **Ferramenta de rede de contrapartes**: permitir ao agente perguntar
  "quais outros clientes transacionam com esta mesma contraparte?" —
  útil para detectar estruturas de lavagem que envolvem múltiplas contas.
- **Testes automatizados** (pytest) para `aplicar_regra1_fracionamento()`
  e `aplicar_regra2_valor_atipico()`, cobrindo os casos-limite já
  explorados manualmente na validação do Nível 1.
- **Investigar a causa raiz exata do bug de SSL** (Nível 3) dentro do
  código-fonte do `google-genai`/`google-auth`, em vez de contornar via
  variável de ambiente — útil para reportar o problema upstream.
