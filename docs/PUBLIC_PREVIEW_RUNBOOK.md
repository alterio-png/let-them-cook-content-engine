# Preview público do Content Engine

Este frontend estático é uma demonstração com artefatos sintéticos do PR #16.
Não é um backend remoto nem substitui o smoke humano da Issue #17.

## Preparar e verificar

Requisitos: Python 3, Git, FFmpeg; Node 20+ para QA. Sem novos pacotes de
produção. O navegador usa HTML/CSS/JS locais, sem framework, CDN, Auth,
armazenamento persistente, analytics ou API externa.

```sh
python3 scripts/build_public_preview.py
npm ci --prefix browser_tests --ignore-scripts --omit=optional
node browser_tests/public_preview_e2e.mjs
```

O build lê os MP4 e manifesto diretamente do commit imutável
`5d6e3da15b88edde79b67d71c089656cfa222cda` pelo Git, confere bytes/hash e
dimensões declaradas, gera posters e copia somente uma allowlist para
`public_preview/dist/`. Não copiar a raiz do repositório para um hosting.
O build não renderiza vídeos novos nem inicia serviços ou infraestrutura.
`release.json` registra o SHA do frontend, estado dirty e hashes do pacote.
Para publicação revisável, usar checkout limpo; `frontend_dirty` precisa ser false.

Para testar manualmente antes da publicação:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory public_preview/dist
```

Abrir `http://127.0.0.1:8765`. O servidor simples não aplica `_headers`; o
teste automatizado aplica o CSP do pacote. A verificação SHA-256 exige contexto
seguro: localhost/loopback ou HTTPS. Não abrir o HTML como `file://`.

## Publicar somente o frontend autorizado

Site alvo existente: `letthemcook-ux-preview`, ID
`f5594b67-54a1-4ec6-a8fd-b329cf92cce2`.
Não usar os sites `letthemcook-v1-preview` ou `letthemcook-functional-preview`.
Não alterar as proteções de acesso dos outros sites.

A integração Netlify disponível nesta sessão permite auditar site/deploy, mas
não contém operação de upload de deploy. Não há `NETLIFY_AUTH_TOKEN` configurado
no executor. É necessário configurar uma credencial de deploy nas configurações
seguras do ambiente ou usar upload manual autenticado no projeto existente.
Nunca enviar tokens por chat, Git, bundle, log ou screenshot.

Com uma versão revisada do Netlify CLI já instalada e credencial configurada,
usar `--dir public_preview/dist` e `--site` com o ID acima. Fazer primeiro um
deploy draft para QA por HTTPS; só então atualizar o alias público autorizado.
Nenhum comando de criação de site, database, functions ou backend é necessário.
Se vincular o CLI, usar `public_preview` como cwd: sua `.gitignore` já exclui
`.netlify/` e `dist/`. Não versionar `.netlify/state.json`.

Antes de um deploy manual de produção, confirmar se há Git CD conectado.
Se houver, um próximo push na branch de produção pode substituir o deploy
manual; informar isso ao Owner e sugerir o lock do deploy se necessário.
O deploy auditado era CLI, sem commit/branch/build ID; isso não comprova a
ausência de configuração Git CD atual, não exposta pelas ferramentas de leitura.

O pacote contém `_headers`, inclusive CSP restrito e `noindex`, e não contém
redirect SPA: toda navegação interna usa âncoras. Confirmar esses headers no
deploy real. Não instalar extensões pagas nem alterar planos do time.

## Aceitação no iPhone físico

Depois de obter a URL HTTPS do novo deploy, registrar data/hora UTC, iPhone,
versão do iOS e Safari, SHA em `release.json` e resultado de cada item:

1. Abrir em aba normal e privada; conteúdo aparece sem login/loader global.
2. Tocar em **Testar os 3 formatos** e alternar 9:16, 1:1 e 4:5.
3. Reproduzir cada MP4 com som; escutar o tom sintético e conferir a legenda.
4. Verificar integridade em cada formato: SHA-256 e tamanho devem conferir.
5. Salvar cada arquivo via download/Compartilhar/Arquivos conforme o Safari;
   reabrir o MP4 salvo. Testar retorno à página após salvar.
6. Percorrer as quatro etapas explicativas, reiniciar e girar o aparelho.
7. Interromper a conexão durante a verificação: deve apresentar erro em até
   8 segundos, com nova tentativa disponível; voltar online e repetir.
8. Confirmar os rótulos: amostra pré-gerada pública, fluxo demonstrativo,
   novos uploads/renders só locais. Nenhum formulário de credenciais aparece.

WebKit automatizado é cobertura do motor, não atesta escuta humana ou Safari
em hardware real. Esse checklist não fecha o smoke funcional local da Issue #17.
O próximo backend privado segue [plano separado](CONTENT_ENGINE_STAGING_PLAN.md).
