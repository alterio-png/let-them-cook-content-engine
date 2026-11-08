# QA do frontend público — 2026-11-08

Escopo: apenas o preview estático com arquivos sintéticos, sem backend remoto,
login, upload novo, render novo ou mídia de clientes. Execução automatizada não
substitui iPhone físico, escuta humana ou a aceitação local da Issue #17.

**Resultado final local: PASS, 40 verificações** — 20 em Chromium
156.0.8078.79 com sandbox habilitado e 20 em WebKit 26.5.
[Relatório e screenshots](../evidence/public-preview/2026-11-08/qa).

## Ferramentas e reprodução

Reutilizado `playwright-core` **1.62.1**, lock/SRI já revisados em
[dependências do R0](G3BR0_BROWSER_DEPENDENCIES.md). Nenhum novo pacote npm e
nenhuma dependência de produção. Para instalar apenas a ferramenta de teste:

```sh
npm ci --prefix browser_tests --ignore-scripts --omit=optional
python3 scripts/build_public_preview.py
PREVIEW_QA_WEBKIT=1 node browser_tests/public_preview_e2e.mjs
```

O driver usa Chromium/Chrome instalado (`BROWSER_PATH` para selecionar), sandbox
explicitamente habilitado, e WebKit oficial Playwright. O workflow usa Chrome
do runner e instala WebKit com suas dependências oficiais. Versões efetivamente
executadas e sandbox são registradas em `summary.json`, não inferidas do nome do
browser. Os binários não são empacotados ou distribuídos com o preview.

Nesta sessão, WebKit 26.5/v2336 veio do CDN oficial Playwright; bibliotecas
Debian oficiais foram extraídas no diretório do usuário. Procedimento e versões
em [runtime de teste](../evidence/public-preview/2026-11-08/old-preview/WEBKIT_RUNTIME.md).
Para esse executor, adicionar `WEBKIT_PATH=/workspace/scratch/webkit-libs/webkit-local.sh`.
O CI instala dependências no runner descartável; não utiliza esse caminho local.

O script cria um servidor temporário em loopback, aplica o CSP de `_headers` e
encerra navegador/servidor ao terminar. `PREVIEW_QA_OUTPUT` escolhe o diretório
de evidências. O workflow separado também guarda o pacote estático por 14 dias.

## Cobertura

Cada motor deve passar 20 verificações: metadata e avanço real da reprodução;
reprodução dos três formatos; três downloads reais com bytes/hash comparados
ao manifesto e arquivo original; walkthrough/reinício; larguras 320, 844 e
1440 além da sessão mobile 390; voltar à página; zero requests externos,
mutações HTTP ou erros de aplicação; HTML útil sem JS; módulo bloqueado;
storage negado; arquivo corrompido de mesmo tamanho; manifesto 404; deadline
de oito segundos; troca de formato com verificação em andamento. Falhas devem
permitir retry sem manter um download falsamente marcado como verificado.

A reprodução automática usa áudio **mutado** para verificar decodificação e
avanço de frames. Não é um teste perceptivo de áudio. Os arquivos preservam
os hashes do PR #16, cuja prova de áudio/legendas é histórica e separada.

## Diagnósticos do próprio harness

No WebKit local, cada chamada de screenshot do Playwright injeta uma folha de
estilo inline que o CSP bloqueia. O evento ocorre exatamente durante as duas
capturas, não durante uso da página ou reprodução. Usar `caret: initial` não
removeu essa injeção. O relatório retém mensagem, URL e fase em
`harnessDiagnostics`; o driver só separa a mensagem exata nesse motor,
nessas fases e com limite de duas ocorrências. Qualquer outro erro de console
ou JS falha a suíte. O CSP do produto não foi relaxado.

O visualizador nativo de JSON/texto do WebKit também injeta estilo e não serve
para medir erros da aplicação. A proveniência é conferida por GET de
`release.json`; o teste de histórico navega entre duas URLs do próprio HTML.

Uma execução Chromium inicial detectou 404 durante reconstrução concorrente do
dist. Reexecução com pacote estável passou; não reconstruir o diretório servido
durante o QA. A execução WebKit inicial precisava rolar o vídeo para o viewport
antes de esperar avanço, por política de reprodução de mídia fora da tela.
O driver agora reproduz com o vídeo visível, como na interação real.

## Gates restantes

- QA publicado HTTPS/headers/Range e SHA da release: depende de deploy.
- Safari em iPhone físico, som e salvar em Arquivos/Compartilhar: pendente.
- Issue #17: smoke local humano permanece parcialmente documentado.
- Backend privado/G3B-P1: NO GO; nenhuma promoção automática por este QA.
