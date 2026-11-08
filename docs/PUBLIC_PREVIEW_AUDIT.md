# Content Engine — auditoria e substituição do preview

Data: 2026-11-08 UTC. Escopo autorizado: continuar este repositório, auditar e
corrigir a experiência pública de frontend, entregar QA mobile e planejar
staging futuro. Sem merge, contratação, backend público ou alteração no
Creative OS.

## GitHub confirmado

- Main: `477c7c922a8ceb0dbb1b7dca98e4b1da12fba149`.
- PR #16 OPEN/DRAFT, base main e HEAD
  `5d6e3da15b88edde79b67d71c089656cfa222cda`.
- API de check-runs: 12 conclusões `success`, todas no HEAD acima. São seis
  jobs por evento push/PR; não doze suítes diferentes.
- O relatório do PR registra 307 testes e E2E Chromium real. Os checks foram
  conferidos novamente; isso não equivale a uma nova execução manual nesta sessão.
- Issue #17: aprovação audiovisual e fluxo principal autoatestados pelo Owner;
  smoke completo **PARTIALLY_DOCUMENTED**. Nenhum dado humano foi inventado.
- Trabalho novo em branch separada `feat/content-engine-mobile-preview`, baseada
  no HEAD exato do PR #16. Os 209 arquivos existentes permanecem byte-idênticos.

## Inventário Netlify

| Site existente | Deploy auditado / controles | Ação desta etapa |
| --- | --- | --- |
| `letthemcook-ux-preview` | ID `f5594b67-54a1-4ec6-a8fd-b329cf92cce2`; público, sem password/SSO; deploy `6adbb291672b260680951d5f`, ready | Alvo autorizado da correção do frontend |
| `letthemcook-v1-preview` | ID `8cd12982-1144-48cd-849f-366e63587695`; SSO em non-production | Apenas leitura; preservar |
| `letthemcook-functional-preview` | ID `f12f3b5e-471d-4dcb-8744-8cd59fa77c00`; SSO em todos os deploys | Apenas leitura; preservar |

O deploy público é de **2026-10-23 19:16:33 UTC**, source CLI, sem
commit_ref, branch, build ID, functions ou edge functions. Título:
“LetThemCook visual Auth-only preview | validated commit 4d430f1 | no remote
workspace persistence or real AI”. Ele não representa o Content Engine do R0.
Não interpretar `ready` no Netlify como saúde funcional da página.
Os tools não expõem build settings/Git CD, billing detalhado ou upload de deploy.
Plano retornado `nf_team_pro` descreve o site; não comprova saldo ou custo
marginal de publicação. Nenhum plano foi alterado ou serviço contratado.

## Travamento reproduzido

O HTML antigo contém somente um loader. O bootstrap espera a recuperação
Auth antes de renderizar. Duas falhas controladas deixam o loader por tempo
indefinido: módulo JS bloqueado e refresh de sessão sintética pendente.
Reproduzidas em Chromium e WebKit com viewport iPhone. Em sessões limpas, ambos
abrem login; a causa específica no iPhone do Owner continua não identificada.
Não houve login ou envio de credenciais ao Auth remoto.

Ver [diagnóstico detalhado](PUBLIC_PREVIEW_OLD_DIAGNOSIS.md) e
[evidências](../evidence/public-preview/2026-11-08/old-preview).

## Correção preparada

- HTML inicial útil, incluindo vídeo e links diretos, mesmo sem JavaScript.
  Não há loader global, Auth remoto ou leitura de storage.
- Layout responsivo com navegação mobile, seletor de formatos, reprodução
  inline, artefatos reais e uma explicação interativa do pipeline local.
- Downloads públicos sintéticos claramente separados dos downloads privados
  do laboratório. Novos uploads/renders, IA e publicação social indisponíveis.
- Conferência SHA-256 e tamanho sob demanda. Erro, timeout de oito segundos e
  cancelamento ao trocar formato não deixam status falso de “verificado”.
- Build com allowlist e hashes a partir do SHA original. JS/CSS com nomes por
  conteúdo, HTML revalidado, CSP restrito e sem dependências externas.
- Sem mudança de API, Auth, banco, cotas, worker ou evidências históricas.

O README histórico G0 e os documentos de decisões anteriores continuam
preservados; não são a descrição de capacidade atual deste preview.

## Entrega e limites

O pacote estático é gerado em `public_preview/dist`. A suíte nova produz
`summary.json`, screenshots e provas de download; o workflow separado guarda
o pacote e QA como artefato por 14 dias. O QA automatizado é distinto do aceite
no iPhone físico e da escuta humana.

A publicação depende de acesso para upload no site existente: nenhum segredo
Netlify está configurado no executor e o conector não expõe upload. Não tratar
o alias antigo como corrigido até haver novo deploy `ready`, `release.json`
correspondente e QA do HTTPS publicado. A autorização de frontend dada pelo
Owner já é suficiente; falta capacidade de execução, não nova aprovação de escopo.

[Runbook e smoke iPhone](PUBLIC_PREVIEW_RUNBOOK.md).
[Plano posterior de staging privado](CONTENT_ENGINE_STAGING_PLAN.md).
G3B-P1 permanece **NO GO** e custo cloud total **UNKNOWN**.
