# PR #11 — plano de sincronização documental após merge humano do PR #9

Preparado em **2026-11-07 UTC** para a [Issue #12](https://github.com/alterio-png/let-them-cook-content-engine/issues/12).
**Estado: BLOCKED; procedimento futuro, não executado.** Este arquivo não altera
os PRs #9/#11, não executa merge/rebase/push e não autoriza G3B-P1.

## 1. Referências e condição de desbloqueio

| Referência observada | Identidade e alcance |
| --- | --- |
| Main histórico | `7a271382487ac4d16e278e5ccb8c345279b3cc16`; árvore `af1a03c8bdd2a3ea6800ed41114e8f5818f553ae`; 114 arquivos |
| [PR #9](https://github.com/alterio-png/let-them-cook-content-engine/pull/9) | OPEN / DRAFT; HEAD revisado `e40357d08fa3c0b258b63c0d3a811cbab2b23206`; árvore `a2ce768cc21e6df3decb5c6d5ba9e9342d8fc2c3`; 149 arquivos, 35 adições, 114 históricos idênticos |
| [PR #11](https://github.com/alterio-png/let-them-cook-content-engine/pull/11) | OPEN / DRAFT; HEAD documental `eb507aff12fdfc16427aa958f46338855cf88d99`; seis Markdown adicionados ao main histórico; quatro workflows e 225 testes históricos nessa branch |
| Main após merge real do PR #9 | **UNKNOWN / não existe nesta evidência**; preencher somente após merge humano real e verificação do provedor |
| HEAD final da futura sincronização | **UNKNOWN / não criado**; não confundir com nenhum dos SHAs acima |

A **única ação humana para desbloquear a próxima etapa** é o revisor/mantenedor
avaliar o parecer técnico e seus findings e, **somente se a revisão humana for
GO**, aprovar e executar explicitamente o merge controlado do PR #9. Uma revisão
técnica realizada por IA, testes locais PASS, CI verde, esta issue ou retirar o
estado DRAFT não substituem aprovação humana. Uma decisão humana NO GO mantém
o bloqueio e exige corrigir/revisar o delta antes de reconsideração.

Mesmo depois dessa ação, a sincronização permanece bloqueada até conferir o
merge efetivo, a árvore integrada, os 114 arquivos históricos e **cinco
workflows completos com conclusão success no SHA real do novo main**. Falta,
skip, cancelamento, falha ou resultado pertencente a outro SHA não fecha o gate.
Não inventar um merge SHA, usar o HEAD do PR #9 como se fosse o merge, nem
aceitar `refs/pull/9/merge` como prova de integração real. Squash/rebase merge
pode mudar a identidade dos commits: comparar conteúdo/proveniência, além da
evidência da operação de merge no GitHub. Se o conteúdo final diferir do HEAD
revisado, revisar e reproduzir o delta antes de continuar.

## 2. CI e contagens que não podem ser misturados

| Estado | Workflows e significado |
| --- | --- |
| Main histórico e PR #11 atual | Quatro: G0, G1, G2, G3A. Os quatro PASS de `eb507aff…` são históricos da branch documental; não provam G3A.1 integrado. |
| PR #9 revisado | Cinco no HEAD `e40357d…`, com 276 testes sem skips; evidência local/CI da proposta, não pós-merge. |
| Main real após merge humano | Exigir os cinco abaixo no SHA integrado efetivamente observado. Não transportar os PASS do PR #9. |
| Futura branch documental sincronizada | Herdará os cinco se a integração for a esperada. Exigir novamente todos os cinco no **HEAD final sincronizado**; quatro PASS não bastam. |

Os cinco são `.github/workflows/ci.yml` (**G0 contracts and render smoke**),
`g1.yml` (**G1 PostgreSQL isolation and reliability**), `g2.yml` (**G2 real media
execution and recovery**), `g3a.yml` (**G3A local Auth and cloud contracts**) e
`g3a1.yml` (**G3A.1 shared PostgreSQL upload authority**). Registrar para cada
um URL/run ID, evento, `head_sha`, conclusão e horários UTC. Diferenciar o SHA
do HEAD documental do SHA sintético de um evento pull_request; exigir evidência
no HEAD exato, por exemplo os runs de push, sem atribuir outro SHA a ele.

A relação **114 → 149 → 155 arquivos** só é válida se o main real tiver
exatamente a árvore revisada do PR #9 e a sincronização adicionar somente os
seis documentos, sem qualquer outra mudança. As seis edições posteriores são
nesses mesmos caminhos. Se main incluir mudanças concorrentes, contar a árvore
real, revisar o delta e registrar a nova união; não impor 149/155 artificialmente.
Este plano e outros arquivos de auditoria da Issue #12 ficam fora da importação
de seis documentos. Incluí-los mudaria o escopo e invalidaria a conta 155.

## 3. Procedimento futuro em branch isolada

Os passos abaixo são uma receita para depois do gate da seção 1; **nenhum foi
executado por este plano**. Usar Git/read-only para observar o provedor e não
iniciar serviços cloud. A cópia de trabalho desta auditoria não é o local para
alterar as branches remotas dos PRs.

1. Reconsultar main, estado/HEAD de ambos os PRs, registro da aprovação humana,
   operação real de merge e cinco runs no main integrado. Guardar as respostas
   redigidas com timestamps. Se qualquer HEAD mudou, interromper a promoção e
   revisar o delta. Não assumir que a aprovação do HEAD antigo cobre o novo.
2. Em clone limpo, fazer fetch das referências necessárias sem atualizar
   branches de trabalho existentes. Capturar os SHAs reais e criar referências
   **locais** de backup, com nomes novos, antes de qualquer edição: main antigo,
   main integrado, HEAD revisado do PR #9 e HEAD original do PR #11. Guardar
   também SHA/tree dos quatro; opcionalmente um bundle local desses backups.
   Nunca sobrescrever backup existente nem publicar refs de backup por rotina.
3. Criar uma nova branch, por exemplo `docs/pr11-post-pr9-sync-<data>`, **a partir
   do SHA real de main que passou os cinco checks**; usar worktree/clone separado
   e limpo. Não iniciar do main histórico nem da branch antiga do PR #11.
4. Conferir que o delta original de PR #11 contra o main histórico contém
   somente as seis adições Markdown listadas na seção 4. Importar os bytes
   desses seis caminhos do HEAD `eb507aff…`, sem importar commits de runtime.
   **Não fazer cherry-pick de PR #9**: seu runtime já deve vir do main real.
   Não trazer workflows, migrations, engine, scripts, locks, manifests ou
   arquivos de evidência por meio do PR #11.
5. Aplicar somente as correções da seção 4. Cada documento deve separar o
   snapshot original de 2026-11-07 do addendum de integração datado, com SHA
   real e URLs. Fazer revisão humana de cada afirmação que passou de proposta
   para capacidade local integrada. Não substituir globalmente SHAs/status.
6. Conferir o diff contra o main integrado: exatamente os seis Markdown e zero
   alterações no restante da árvore. Rodar checks estáticos da seção 5 e
   registrar saídas/digests novos. Se houver conflito, caminho já existente,
   diferença inesperada ou nova alteração no main, parar, preservar as duas
   versões e pedir revisão humana do delta; não usar resolução automática
   `ours`/`theirs`, não apagar conteúdo e não reescrever a história.
7. Após revisão e checks locais, o fluxo autorizado poderá publicar a nova
   branch normalmente e apresentar um PR DRAFT vinculado ao #11, ou propor ao
   mantenedor uma atualização explícita do #11. A escolha do destino exige
   manter clara a continuidade da revisão. Não executar rebase da branch remota
   antiga, push forçado ou mudança de base do #11 automaticamente. Se o futuro
   procedimento escolhido exigir reescrita remota, obter decisão humana
   específica com backups/diff revisados; `--force-with-lease` sozinho não é
   autorização. Este plano não autoriza qualquer push agora.
8. No HEAD final publicado, aguardar/verificar os cinco workflows. Se houver
   nova edição, o HEAD mudou: os cinco devem corresponder ao novo HEAD. Entregar
   diff, relatório de preservação e matriz dos runs para revisão humana.
   **Não fazer merge automático do PR #11 ou do PR substituto.** Manter G3B-P1
   NO GO; merge documental não concede autorização de execução cloud.

Exemplo exclusivamente futuro da importação com allowlist (não executado):

```sh
git restore --source=eb507aff12fdfc16427aa958f46338855cf88d99 -- \
  docs/G3B_P0_ARCHITECTURE.md \
  docs/G3B_P0_COST_MODEL.md \
  docs/G3B_P0_DECISION.md \
  docs/G3B_P0_PREFLIGHT.md \
  docs/G3B_P0_SECURITY_GATES.md \
  docs/G3B_P0_STAGING_RUNBOOK.md
```

Executar esse exemplo somente no worktree novo do passo 3, após comparar
caminhos/bytes. Nenhum comando deste plano faz merge, rebase ou push.

## 4. Inventário preciso de patches nos seis documentos

Aplicar esta lista **somente após o gate**, usando os títulos/linhas de contexto
abaixo no HEAD original do PR #11. As referências históricas completas e seus
SHA-256 permanecem vinculadas ao commit de origem; novos fatos ganham um bloco
separado, datado, com a evidência real. Se o runtime integrado não corresponder
ao revisado, não aplicar mecanicamente a redação proposta.

### `docs/G3B_P0_PREFLIGHT.md`

- **Seção 1, tabela de estado e parágrafo da branch:** preservar o main
  `7a271382…`, PR #9 OPEN/DRAFT, 114/149 e quatro workflows como **snapshot da
  consulta original**. Acrescentar tabela de estado integrado: main real,
  aprovação/merge humano, tree, cinco runs pós-merge e HEAD final sincronizado
  com seus cinco runs. Atualizar a descrição da nova branch como baseada no
  main real, com somente seis docs; o PR #9 deixa de ser dependência ausente
  apenas nesse novo estado.
- **Seção 2, introdução e linha SQLite/PostgreSQL:** anotar que os documentos
  G3A.1 agora existem no main real verificado; a ausência no main histórico
  continua fato. O blocker histórico SQLite se aplica à aplicação legada;
  `SharedMediaHTTPApplication` seleciona `PostgresUploadAuthority` explicitamente
  para a prova local integrada. Listener cloud, ingest bridge, Auth hospedado,
  rede, pooling, GCS/Tasks/Jobs, capacidade/custos continuam sem validação.
- **Matriz da seção 4:** P0-01 recebe nova base/relatório de integridade. P1-01
  só pode ganhar referência à aprovação humana efetivamente registrada; P1-02
  só fecha com merge real + cinco PASS nesse main. Não promover os demais
  gates por consequência. **Seção 5, passo 1:** registrar a dependência
  satisfeita com evidência, mantendo revalidação se main/HEAD mudar.
- **Apêndice A e seção 6:** manter todos os commits, hashes, artefatos, 276
  casos e 305/205 como origem histórica. Se novos arquivos forem hashados,
  criar outra tabela com SHA de origem explícito, sem sobrescrever a antiga.
- **Apêndice B e resultado observado:** conservar os dois snippets originais,
  as saídas e `output_sha256=da33ef7aae1583fea926945fed254b289981ef7a26aad7fb2609eaf704f4ec3b`
  como verificação histórica da branch de quatro workflows. Acrescentar
  verificador integrado separado: base real, seis docs, preservação de todos
  os arquivos dessa base e cinco workflows; conferir também o manifesto dos
  114 históricos. Não relabelar a saída original para 149/155/cinco. Registrar
  nova saída, digest e HEAD sincronizado. Links devem resolver no checkout novo.

### `docs/G3B_P0_ARCHITECTURE.md`

- **Abertura:** identificar a referência antiga como snapshot e acrescentar
  estado integrado com main real/merge/cinco PASS. Trocar a afirmação de
  ausência no main somente para a descrição atual, mantendo a evidência local
  do HEAD PR #9 e o gate de autorização P1 separado.
- **Seção 1, linha Auth/PostgreSQL, e seção 2, passo 2:** descrever a autoridade
  compartilhada como código local integrado após o gate, com seleção explícita
  e sem fallback SQLite. Não afirmar deploy, API cloud pronta ou storage
  compartilhado remoto. Manter o gap de ingestão do passo 5.
- **Seção 5, divergência SQLite:** explicar que o diagnóstico original continua
  válido para o main histórico/caminho legado, enquanto o novo main contém o
  caminho compartilhado local revisado. Preservar os documentos históricos;
  o futuro wiring cloud/IaC continua trabalho separado.
- **Seção 6, fontes:** manter links imutáveis `e40357d…` e fontes públicas
  consultadas em 2026-11-07; acrescentar links do main real para a integração.
  Manter integralmente envelope, 13 conexões + Auth UNKNOWN, retries/Tasks,
  305 tentativas/205 respostas 503 e limites de custo/capacidade.

### `docs/G3B_P0_COST_MODEL.md`

- **Abertura, parágrafo de proveniência:** acrescentar o main real, aprovação,
  merge e cinco runs; a frase OPEN/DRAFT/não integrado passa a ser marcada
  como observação histórica, sem apagar seu SHA. A integração muda proveniência,
  não transforma consumo/preço hipotético em medição operacional.
- **Seções 1–10:** preservar consulta e capturas de **2026-11-07**, 12 hashes
  HTML, SKUs/unidades/hipóteses, fórmulas e bloco `Decimal`. Manter subtotal
  `0.59265232250`, total all-in **UNKNOWN**, cloud **NOT_VALIDATED**, gasto
  **BLOCKED**, USD 10 não aprovado e nova assinatura Pro USD 25/mês fora do teto.
  Uma futura consulta de preço precisa de linha/addendum com nova data e
  hipóteses próprias; não redatar a pesquisa antiga por causa do merge.
- Manter 305 tentativas físicas/205 respostas 503, custo de retries, cauda de
  retenção, ausência de hard cap e bloqueios cumulativos de callbacks/execuções.
  Reexecutar somente a aritmética local; não obter billing, credenciais ou
  contratar serviços como parte da sincronização.

### `docs/G3B_P0_SECURITY_GATES.md`

- **Abertura:** marcar main antigo e PR #9 OPEN/DRAFT como snapshot, e registrar
  o estado local integrado no SHA real, preservando 276 casos como evidência de
  origem. Definições de PASS/BLOCKED/NOT_VALIDATED/UNKNOWN não mudam.
- **S01:** só retirar o blocker de integração após referência à revisão humana,
  merge e cinco runs reais; adicionar obrigação dos cinco runs no HEAD final
  sincronizado. O PASS permanece limitado a integração/local, sem prova cloud.
- **S02–S16, identidades/rede/dados:** manter gates hospedados e owners
  UNASSIGNED/UNKNOWN conforme evidência. Seleção PostgreSQL local integrada não
  valida IAM/OIDC remoto, sessões hospedadas, GCS, cleanup ou privilégios do
  worker. Não atribuir split de roles nem controle cumulativo inexistente.
- **Static audit evidence:** manter hashes completos do HEAD `e40357d…`, hashes
  públicos e datas consultadas. Acrescentar proveniência integrada separada;
  não alterar silenciosamente o campo “Source file at e40357d…”.

### `docs/G3B_P0_STAGING_RUNBOOK.md`

- **Abertura:** preservar baseline antigo/PR DRAFT como snapshot e adicionar
  main real/merge/cinco PASS. Manter **Execution status: BLOCKED**: integração
  local não fornece a autorização específica nem fecha os outros gates P1.
- **Seção 1, duas primeiras linhas da autorização:** preencher somente com
  revisão humana e merge/checks reais, com URLs e SHAs. Não preencher pessoas,
  conta, região, orçamento, operador, imagens ou credenciais por inferência.
- **Seção 2:** explicitar que o código local agora está integrado, mantendo
  loopback/PORT, ingest bridge, scheduler, limites e secrets como gaps abertos;
  não adicionar comandos cloud ou flags de deploy.
- **Seção 4, ordem 0:** reconfirmar main/runtime real e cinco checks, citando
  também HEAD documental sincronizado e seus cinco runs antes de reutilizar o
  runbook. Demais ordens continuam futuras e NOT_VALIDATED; nenhuma ação é
  considerada executada pelo merge.
- **Source boundary:** manter origem `e40357d…` e fontes de 2026-11-07;
  acrescentar proveniência do novo main sem apagar o diagnóstico SQLite
  histórico. Preservar 305/205, limites de 1/5 minutos, 120/300 segundos,
  quatro execuções, 20 callbacks condicionais, stop/uncertainty e retenção.

### `docs/G3B_P0_DECISION.md`

- **Abertura:** manter decisão original e data de **2026-11-07**. Adicionar
  addendum de integração com data real, SHA, aprovação humana/merge e runs;
  distinguir o snapshot OPEN/DRAFT da situação integrada comprovada.
- **Tabela de evidências:** separar base histórica 114/149 e P0 com quatro
  workflows de base real/sync final com cinco. Autoridade passa de proposta
  local a código local integrado somente após o gate. Registrar os novos
  checks como regressões locais; não reatribuir os 225/276 históricos.
- **Gate “Dependência G3A.1”:** só marcar satisfeito mediante revisão humana,
  merge real e cinco PASS no novo main. Demais gates preservam estado e
  owners conforme evidência; **readiness G3B-P1 continua NO GO**, cloud
  NOT_VALIDATED e custo completo UNKNOWN. Não emitir GO por mero alinhamento
  documental.
- **“Entrega P0 e próxima decisão”:** descrever a nova base e seis docs, nova
  contagem condicional e cinco workflows finais; conservar o relato original
  dos quatro checks como histórico. PR #11 ou substituto permanece DRAFT até
  revisão e não é mesclado automaticamente. Manter separadas autorização de
  P1 e aprovação de produção/cliente/publicação.

## 5. Verificação e pacote de revisão futuro

O responsável pela sincronização deve produzir estes resultados no checkout
novo, sem executar ensaio cloud:

- Allowlist do diff contra o SHA integrado: exatamente os seis Markdown; zero
  mudanças em arquivos da base. Se a base já contiver um desses documentos,
  revisar o caso e o conjunto esperado antes de importar; não sobrescrever.
- Comparação byte a byte de todos os arquivos do main real com o sync, além
  de verificar as 114 entradas do manifesto histórico contra o main histórico,
  PR #9 revisado, main real e sync. G3A.1/workflow/migration chegam só do main.
- Para a união exata: 114 históricos preservados, 35 adições PR #9 intactas,
  seis docs adicionados e 155 arquivos. Caso contrário, inventário real e delta
  revisado explicitamente, sem alegar o caso exato.
- `git diff --check`; links relativos resolvidos; code fences balanceados;
  aritmética `Decimal` preservada e reproduzida. O novo checker integrado usa
  cinco workflows; o checker histórico conserva sua saída original de quatro.
- Revisão textual dos seis docs: estado atual e histórico separados; SHAs e
  SHA-256 de origem intactos; as **144 tentativas da prova local original do
  PR #9** separadas das 305 tentativas CI/205 respostas 503. A reprodução nova
  da Issue #12 teve **140 tentativas/40 respostas 503** e, se citada, deve ter
  proveniência própria; não substituir as contagens históricas. Manter 225
  testes históricos separados dos 276 de G3A.1;
  datas/preços/UNKNOWN preservados; nenhum gate cloud promovido por inferência.
- Matriz dos cinco workflows no main real **e outra matriz dos cinco no HEAD
  sincronizado final**, com links/run IDs e SHAs completos. Checks apenas
  solicitados ou indisponíveis são NOT_RUN/NOT_VALIDATED; skip não é PASS.
- Diff revisável, backups locais, inventário/checks/digests, decisão humana de
  PR #9 e pendências. A entrega deve pedir revisão do resultado concreto; não
  executar merge #11 como etapa final automática.

Nem esta preparação nem a futura sincronização autorizam Terraform plan/apply,
cloud login, credenciais hospedadas, conta/projeto/bucket/fila/Job, serviço pago,
deploy, execução G3B-P1, mídia de cliente ou mudanças no Creative OS. A ausência
desses atos é parte do escopo, não prova de que os serviços remotos funcionam.
