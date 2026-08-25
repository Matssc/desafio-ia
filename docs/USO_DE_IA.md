# Uso de IA neste projeto

## Ferramentas usadas

- **Claude (Anthropic)**: usado como par de programação ao longo de todo o desafio — discussão de abordagem, geração de código inicial, revisão e explicação de decisões de design (ex.: separação entre regra determinística e LLM, estrutura do agente do Nível 2).

## Como foi usado

O Claude foi usado em três modos diferentes ao longo do desafio:

**1. Geração de código a partir de especificação.**
Para cada parte do desafio, descrevi o requisito (ex: "Regra 1 — cliente
com 3+ operações no mesmo dia, soma > R$ 50k, nenhuma isolada ≥ R$ 20k")
e revisei o código gerado linha por linha antes de aceitar, incluindo
rodar contra os dados reais para confirmar que o resultado batia com o
que eu esperava manualmente (ex: conferi à mão que CLI-A-1 deveria ser
sinalizado antes de aceitar o código da Regra 1).

**2. Depuração de erros reais de execução.**
Vários erros só apareceram ao rodar o código de verdade na minha máquina
(não foram previstos de antemão):

- `Part.from_text() takes 1 positional argument but 2 were given` — mudança
  de assinatura entre versões do SDK `google-genai`.
- `404 NOT_FOUND: model gemini-2.0-flash is no longer available` — modelo
  descontinuado pela Google durante o desenvolvimento.
- `429 RESOURCE_EXHAUSTED` com `quotaId: ...PerDay...` — cota diária da
  camada gratuita, não cota por minuto como parecia inicialmente.
- Erro de SSL no Windows (`FileNotFoundError` dentro de
  `ssl.create_default_context`) ao rodar a interface Streamlit — este
  foi o mais difícil: levou várias tentativas erradas até isolar a
  causa raiz por eliminação. Detalhado no item abaixo.

Em cada caso, colei o traceback completo e o Claude identificou a causa
a partir da mensagem de erro, propôs a correção, e eu testei antes de
aceitar.

**3. Revisão de decisões de design.**
Por exemplo, ao decidir como o agente do Nível 2 deveria escolher entre
as 3 ferramentas disponíveis, discuti com o Claude se deveria ser um
fluxo fixo (sempre chamar as 3) ou uma decisão real do LLM via function
calling — optamos pela segunda, e isso é verificável nos resultados:
clientes diferentes geraram sequências de chamadas diferentes (ex:
CLI-013 resolveu em 3 chamadas, CLI-005 precisou de 5).

## Onde a IA errou / me levou pro caminho errado

**Nome de modelo incorreto.** Ao trocar o provedor de LLM depois que o
`gemini-2.0-flash` foi descontinuado, o Claude sugeriu inicialmente
`gemini-3.5-flash-lite` como substituto para o Nível 2. Esse modelo não
estava disponível na minha conta — a página oficial de cota
(ai.dev/rate-limit) mostrava `gemini-3.1-flash-lite` como a opção real
disponível. Corrigimos assim que percebi a divergência entre o que foi
sugerido e o que a própria Google mostrava para minha chave.

**Subestimou o rigor do rate limit da camada gratuita.** As primeiras
versões do tratamento de erro tratavam 503 (sobrecarga temporária) e 429
(limite de cota) da mesma forma, com um backoff curto. Na prática, o 429
da camada gratuita é por cota diária/por minuto real, e um backoff de
poucos segundos não resolve — foi preciso reformular para um rate
limiter de janela deslizante, e mesmo assim ainda tivemos que trocar de
modelo (para um com cota diária maior) para viabilizar o lote completo.

**Não conseguiu reproduzir o problema de SSL do Windows diretamente,
mas ajudou a diagnosticar e resolver por eliminação.** O ambiente do
Claude é Linux, então o erro de `FileNotFoundError` em
`ssl.create_default_context()` no Windows/Anaconda não pôde ser
reproduzido diretamente. Mesmo assim, testes isolados sugeridos pelo
Claude (criar o contexto SSL sozinho, depois após importar a lib, depois
só ao instanciar o cliente) permitiram isolar exatamente em qual chamada
o problema ocorria, o que levou à causa raiz (a variável `SSL_CERT_FILE`
ficando num estado inválido durante a resolução de credenciais interna
da biblioteca) e à correção (forçar essa variável explicitamente antes
de criar o cliente). Levou várias tentativas erradas antes de chegar
na causa certa.
