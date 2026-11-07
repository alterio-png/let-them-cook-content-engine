# G3B-P0 — preflight documental e auditoria de dependências

Issue [#10](https://github.com/alterio-png/let-them-cook-content-engine/issues/10).
Consulta em **2026-11-07 UTC**. Escopo: análise, documentação e verificações
estáticas; nenhum recurso ou ensaio cloud foi criado ou acionado. Este documento
não autoriza G3B-P1, merge, gasto, deploy, Terraform plan/apply ou uso de cliente.

## 1. Estado que esta proposta pode afirmar

| Referência | Estado observado | Classificação e alcance |
| --- | --- | --- |
| `main` | `7a271382487ac4d16e278e5ccb8c345279b3cc16`, árvore `af1a03c8bdd2a3ea6800ed41114e8f5818f553ae` | **PASS** para identificar a base; contém G3A integrado, não G3A.1. |
| PR [#9](https://github.com/alterio-png/let-them-cook-content-engine/pull/9) | **OPEN / DRAFT**, HEAD `e40357d08fa3c0b258b63c0d3a811cbab2b23206`, árvore `a2ce768cc21e6df3decb5c6d5ba9e9342d8fc2c3` | **PASS** para existência/proveniência da proposta; aprovação humana e merge continuam **BLOCKED**. |
| Histórico entre as duas referências | 114 arquivos no main, 149 no HEAD do PR #9; todos os 114 anteriores idênticos, 35 adições | **PASS** para preservação estática; não importa o PR #9 nesta branch. |
| Prova local G3A.1 | 276 testes e cinco workflows no HEAD acima; artifact `13580575204` conferido em leitura | **PASS** para a evidência local arquivada, sem repetir os testes nesta etapa documental. |
| CI disponível nesta branch P0 | Os quatro workflows existentes no main; o quinto workflow G3A.1 pertence somente ao PR #9 | **PASS** para distinguir as referências; execução dos quatro checks no HEAD documental deve ser registrada na entrega. Não chamar quatro workflows do P0 de cinco. |
| Cloud, hosted Auth, IAM, conectividade e cobrança | Nenhuma validação operacional fornecida por G3A/G3A.1 | **NOT_VALIDATED**; preços publicados não são fatura medida. |
| Dono do staging, conta/projeto/região e autorização de gasto | Não definidos por esta issue | **UNKNOWN**; a ausência impede execução de P1. |

Esta branch `docs/g3b-p0-cloud-preflight` parte do main acima e adiciona somente
os seis documentos P0. Arquivos G3A.1 são referências imutáveis ao HEAD do PR #9,
não dependências já instaladas no main. Nenhum histórico é reescrito para fazer
parecer que uma proposta DRAFT foi integrada.

## 2. Auditoria dos documentos e aparente conflito SQLite/PostgreSQL

Foram lidos AGENTS.md, README.md, G0_DECISION.md e os documentos obrigatórios
G3A_COSTS_AND_G3B.md, G3A_ARCHITECTURE.md, G3A_IAC.md, G3A1_DESIGN.md,
G3A1_SCHEMA.md, G3A1_DECISION.md e G3A1_RUNBOOK.md no HEAD do PR #9. O audit
também leu G3A_DECISION.md, G3A1_BENCHMARK.md e os arquivos de implementação
indicados no apêndice. No main, os três documentos G3A têm os mesmos bytes que
no PR #9. Os documentos G3A.1 ainda não existem no main.

| Achado | Interpretação correta e encaminhamento |
| --- | --- |
| G3A_ARCHITECTURE.md, linhas 107–112, e G3A_IAC.md, linhas 228–232, tratam SQLite como bloqueador de múltiplas réplicas. | Continua correto para o main auditado. O PR #9 propõe a autoridade PostgreSQL compartilhada e a comprova somente em ambiente local. Após eventual merge, uma futura documentação operacional deve apontar explicitamente a seleção compartilhada e suas provas, sem apagar o diagnóstico histórico. |
| G3A_IAC.md descreve imagens e serviços declarativos, guardas de autorização falsas e listener local loopback. | **BLOCKED** para tratar esses arquivos como implantação pronta. G3A.1 não cria runtime HTTP cloud, imagem aprovada ou validação IAM/rede. A integração runtime/cloud exigirá trabalho e revisão separados. |
| G3A_IAC.md, linhas 234–247, propõe dez conexões e ressalva Auth/rede não medidos. | Dez é uma alocação histórica proposta, não limite global implementado nem capacidade hospedada provada. O ledger de upload, render e consulta de sessão têm orçamentos distintos; dispatcher, heartbeat, sweep, Auth e reserva operacional também entram no total. Rever a conta agregada da [arquitetura P0](G3B_P0_ARCHITECTURE.md) antes de aprovar qualquer escala. |
| G3A_IAC.md descreve a fila com cinco tentativas e duração de retry de 300 s. | A referência oficial atual de [RetryConfig](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig), consultada em 2026-11-07 e atualizada em 2026-09-30, condiciona o fim dos retries a **ambos** os limites satisfeitos, salvo sucesso. Portanto `maxAttempts=5` com `maxRetryDuration=300s` não comprova um hard cap de cinco callbacks. Registrar correção futura da interpretação/configuração sem modificar o arquivo histórico; limite cumulativo independente por ensaio ainda precisa ser implementado e testado. |
| Pausar a fila ou limitar dispatch não fecha toda forma de acionamento. | A referência oficial de [tasks.run](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks/run), consultada em 2026-11-07 e atualizada em 2026-09-30, informa que o método de debug força dispatch mesmo com tarefa em execução, fila pausada ou rate limits atingidos. Exige `cloudtasks.tasks.run`: revisar explicitamente esse privilégio e não usar pausa/rate limit como controle único de parada ou custo. |
| G3A_DECISION.md conserva uma execução inicial de 216 testes e um addendum final de 225. | São execuções/versionamentos diferentes, explicitados no próprio documento. Não relabelar o log inicial como execução do sweep final. G3A.1 acrescenta 51 casos e chega a 276 no seu HEAD. |
| G3A1_DECISION.md registra 144 tentativas físicas na execução local de 100 duplicatas; o CI final registra 305. | Ambos os relatórios são datados e identificam seus SHAs/ambientes. São coortes distintas, não valores intercambiáveis. O CI de dois CPUs teve 205 respostas 503 recuperadas; consistência da reserva não prova capacidade sustentada ou aceitação na primeira tentativa. |
| G3A1_DESIGN.md diz `uploaded_pending_validation`. | Recibo de bytes não transforma upload em asset G2 verificado. Manter ingestão/probe/rights separados e sem DSN privilegiada na API. |
| O plano histórico propõe USD 10 e até duas horas. | São valores propostos, sem crédito, orçamento ou região aprovados. Alertas não são hard caps; os limiares de USD 5 medidos/USD 8 previstos só poderão valer se aprovados. Ver [custos](G3B_P0_COST_MODEL.md) e [runbook](G3B_P0_STAGING_RUNBOOK.md). |

## 3. Revisão documental do limite transacional do PR #9

A leitura estática abaixo verifica coerência entre contrato, código e evidência
arquivada. Não substitui aprovação de um revisor humano independente, uma nova
execução no main após merge ou prova contra serviços hospedados.

| Área examinada | Evidência no HEAD `e40357d…` | Resultado da leitura e gate restante |
| --- | --- | --- |
| Autorização da API | `engine/shared_upload_http.py:25`; `engine/shared_upload_authority.py:159`; `engine/cloud_auth.py:117` | **PASS** local/documental: seleção exige PostgresUploadAuthority e mesmo DSN restrito; login não dono/NOINHERIT, claims verificados e sessão atual. Hosted session invalidation, chaves/issuer/audience/TTL reais: **NOT_VALIDATED**. |
| RLS e grants | Migração `20261107150813_shared_upload_authority.sql:111`, `:176`, `:454`; `upload_tests/g3a1/test_permissions.py:49` | **PASS** para o contrato auditado: sem DML de ledger para authenticated; helpers SECURITY DEFINER com search_path vazio e ACL explícita; reconciliação de serviço separada. Confirmar schema fora da Data API, papéis efetivos e revisão humana: **BLOCKED** antes de P1. |
| Isolamento e recibo | Migração `:26`, `:52`, `:80`; `test_permissions.py:141`; `test_ledger_failures.py:274` | **PASS** local: identidade imutável, chave por organização, FKs compostas e recibo único; fence antigo não substitui resultado. IAM/storage remoto entre organizações: **NOT_VALIDATED**. |
| Ordem de locks/deadlines | Migração `:144`, `:176`, `:262`, `:305`; adapter `:70`, `:208`; `test_deadline.py:110` | **PASS** local: membership, cota e reserva; relógio/deadline do banco, checks após locks/escritas e timeout restante. A API compartilhada usa wrappers com deadline. Isto não promete prazo real-time de ACK de COMMIT/fsync nem mata I/O externo bloqueado; supervisor cloud: **BLOCKED**. |
| Retry/backpressure | `scripts/benchmark_g3a1.py`; `docs/G3A1_BENCHMARK.md`; artifact descrito abaixo | **PASS** para correção local com tentativas limitadas e explícitas; throughput/pool agregado hospedado: **NOT_VALIDATED**. Não multiplicar o pico local em uma SLA. |
| Recuperação/retention | Adapter `:362`, `:412`, `:444`; migração `:154`, `:370`; G3A1_RUNBOOK.md:62 | **PASS** para regra local conservadora: apenas reserva expirada nunca reclamada (`fence=0`) reembolsa uma vez; incertos/concluídos continuam cobrados. Verificação real de generation e retenção remota: **NOT_VALIDATED**. |
| Rollback | G3A1_RUNBOOK.md:92 | **PASS** para contrato documentado: parar admissão, drenar/fencear escritores, preservar ledger/objetos/recibos; nunca voltar organização ativa a SQLite vazio. Ensaio remoto: **NOT_VALIDATED**. |

Há wrappers SQL legados com assinatura preservada; a leitura de deployment deve
confirmar que o caminho HTTP selecionado chama os wrappers com deadline. Não
expor funções privadas ou credentials de serviço para obter um atalho. Não
propor importação automática de SQLite: dados ativos, tombstones, cobranças,
recibos e escritores pendentes exigem migração revisada separadamente.

O default do adapter é upload máximo de 1 MiB, TTL de 120 s e orçamento de
operação de 30 s. A mídia sintética 1080p de 1/5 minutos pode exigir limites
diferentes. Tamanho/bitrate real e configuração explícita terão de ser revisados
antes do ensaio; a coorte de bytes do ledger não comprova esse caminho maior.

## 4. Matriz de pré-requisitos para solicitar autorização de P1

**Owner proposto é uma função a designar, não uma pessoa já autorizada.**

| ID | Pré-requisito/evidência exigida | Estado atual | Owner/decisão pendente |
| --- | --- | --- | --- |
| P0-01 | Branch documental sobre main confirmado, seis arquivos e histórico preservado | **PASS** para base identificada; integridade final deve ser repetida antes da entrega | Mantenedor a designar; ver apêndice estático. |
| P1-01 | Revisão humana independente do PR #9, incluindo sete áreas acima | **BLOCKED** | Revisor independente a designar; registrar aprovação e findings fechados. |
| P1-02 | Merge controlado/autorizado do PR #9 e cinco workflows PASS no novo main | **BLOCKED** | Mantenedor humano a designar; não pertence ao P0. |
| P1-03 | Conta/projeto isolados, billing account, plano Auth/DB, região e residency aprovados | **UNKNOWN** | Responsáveis por infraestrutura/dados/finanças a designar; nenhuma seleção é presumida. |
| P1-04 | Autorização separada com teto, janela, workload, operador, stop/kill e teardown | **BLOCKED** | Autorizador humano e operador a designar; USD 10/duas horas são somente proposta. |
| P1-05 | Preços correntes aplicáveis, custos desconhecidos resolvidos e pior caso dentro do teto com margem | **UNKNOWN** | Owner financeiro/técnico a designar; ver modelo datado. Billing real é **NOT_VALIDATED**. |
| P1-06 | Imagens com digest/SBOM, proveniência, assinatura, vulnerabilidades e licença FFmpeg/GPL/codecs revisadas | **BLOCKED** | Segurança/release/legal a designar; binário local não é aprovação de distribuição. |
| P1-07 | Runtime HTTP cloud, entrada IAM/TLS, segredos, session lookup e pool TLS testáveis | **BLOCKED** | Backend/plataforma a designar; main/PR #9 têm listener local, não deployment. |
| P1-08 | Budget agregado por serviço/papel e conexões reais, backpressure e retry limitados | **BLOCKED** | Banco/plataforma a designar; incluir API, Auth, render, worker, dispatcher, reconciler e headroom. |
| P1-09 | IAM efetivo, JWT/OIDC, GCS generation preconditions e fronteiras organizacionais | **NOT_VALIDATED** | Segurança/plataforma a designar; preparar gates negativos do documento de segurança. |
| P1-10 | Uma admissão física global; toda tentativa/sweep passa pelo launch ledger; terminal proof antes de liberar slot | **NOT_VALIDATED** em cloud | Worker/plataforma a designar; paralelismo de Job/fila não substitui controle global. |
| P1-11 | Retenção/backup/restore/versions/soft-delete e GC que protege in-flight/recibos válidos | **BLOCKED** | Dados/operações a designar; retenção incerta conta no custo e não autoriza apagar. |
| P1-12 | Observabilidade sem segredos, inventários pré/pós, medição de billing atrasado e plano de recuperação | **NOT_VALIDATED** | Operador a designar; runbook futuro, não exercício realizado. |
| P1-13 | Contadores independentes e cumulativos por ensaio: proposta de até 20 callbacks e quatro execuções físicas, incluindo retries/sweeps/acionamentos manuais; revisão de `cloudtasks.tasks.run` | **BLOCKED** — não implementados nem aprovados | Backend/plataforma/operador a designar; não derivar hard cap de cinco callbacks de `maxAttempts=5` e 300 s. O gate deve existir fora do retry do provedor e persistir entre réplicas/restarts. |

Nenhum item **UNKNOWN**, **BLOCKED** ou **NOT_VALIDATED** pode ser contado como
PASS por estar previsto num diagrama, teste local, variável Terraform ou preço
público. A decisão consolidada está em [G3B_P0_DECISION.md](G3B_P0_DECISION.md).

## 5. Ordem futura de execução do staging — não executada

1. Fechar revisão, autorização e merge controlado do PR #9; confirmar o novo
   main, diffs e todos os cinco workflows nesse SHA. Se o HEAD mudar, revisar
   novamente o delta e atualizar estas referências.
2. Resolver owners, plano/provedores, conta/projeto/região/residency, preços
   aplicáveis e termos de custo UNKNOWN. Aprovar explicitamente envelope,
   janela, limiares, inventário e kill/teardown. Antes disso, nenhuma criação.
3. Revisar runtime/imagens/IaC e limites agregados, segredo/identidade por papel,
   conectividade/TLS e protocolo do ObjectStore. A mera troca de flags nas
   propostas antigas não autoriza ação nem implementa o runtime ausente.
   Confirmar controles independentes de contagem cumulativa por ensaio: a
   proposta de 20 callbacks/quatro execuções físicas é **BLOCKED** até aprovação,
   implementação e prova; as opções de retry da fila não equivalem a esses caps.
4. Somente com autorização de P1, o operador executará o inventário inicial e
   provisionamento mínimo aprovado. Testar primeiro identidades/negações,
   acesso ao banco e sessões, depois storage/generations, e então scheduling/
   launch control. Nenhum FFmpeg antes de fechar esses gates.
5. Começar com uma execução física de mídia sintética, depois a segunda apenas
   se autorizada. Medir coortes 1080p de 1/5 minutos e três formatos, limites
   de bytes/CPU/RAM/tempo e todos os custos por tentativa. Os limites e a conta
   de conexões são globais, inclusive em erro/retry/sweep.
6. Exercitar duplicatas/503/backpressure e falhas controladas dentro do mesmo
   envelope: storage aceito com resposta perdida, lease/fence obsoleto, queda de
   worker e run de resultado incerto. Um run incerto retém slot; um upload
   incerto retém cota/objeto até prova segura. Não repetir launch cegamente.
7. Parar admissão, drenar/cancelar escritores identificados, reconciliar cada
   incerteza, preservar estado ainda autoritativo e só então fazer teardown
   autorizado. Registrar inventários e cobranças tardias; ausência na primeira
   consulta de billing não significa custo zero.

**Parada imediata:** bypass de autenticação ou tenant, custo/cobrança não
explicados, lançamento físico duplicado, objeto/recibo/hash/generation divergente
ou múltiplos Jobs sem fencing/admissão global. Suspender a coorte, preservar
evidência e iniciar o runbook; nenhuma dessas situações permite aumentar cota,
repetir launch ou apagar ledger para continuar o teste.

## 6. Evidência do CI G3A.1 e limites de interpretação

O [run final de push](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140558)
e seu [artefato 13580575204](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140558/artifacts/13580575204)
contêm o SHA `e40357d08fa3c0b258b63c0d3a811cbab2b23206`, árvore limpa e
**276 casos sem skips**: 12 G0 + 36 G1 + 49 G2 + 118 G3A + 10 Auth histórico +
44 upload compartilhado + 7 Auth compartilhado. Esta auditoria leu os arquivos
arquivados; não afirma nova execução das suítes ou validação cloud pelo P0.

No report final, dois processos HTTP (16517/16520) e reinício em 17428 usaram
PostgreSQL 16.15 e store local compatível. As 100 operações lógicas duplicadas
fizeram **305 tentativas físicas**: 100 respostas 201 e 205 respostas 503;
somente 24 tiveram 201 inicial, 76 exigiram retry. O p95 lógico foi 1,727618 s,
incluindo backoff; o p95 físico foi 0,399521 s. O resultado foi um grant e uma
cobrança de 128 bytes. Na coorte distinta, 20 operações/21 tentativas produziram
10 respostas 201, 10 respostas 429 e uma 503 recuperada; cota final dez grants/
1.280 bytes. Há zero arquivo SQLite criado no benchmark.

O teste prova consistência e recuperação da autoridade no ambiente local,
incluindo backpressure. Não prova vazão sustentada, pool agregado com Auth real,
equivalência de filesystem entre hosts ou custo/latência cloud. A carga usou
tokens `LOCAL_CRYPTO_CONTRACT`; os sete testes oficiais de Auth local são um
gate separado, não uma substituição por fixture de uma integração hospedada.

## Apêndice A — referências imutáveis e SHA-256

Todos os caminhos abaixo são lidos de
[`e40357d08fa3c0b258b63c0d3a811cbab2b23206`](https://github.com/alterio-png/let-them-cook-content-engine/tree/e40357d08fa3c0b258b63c0d3a811cbab2b23206).
Os três documentos G3A também coincidem com o main auditado. SHA-256 é dos
bytes do arquivo, não do commit Git.

| Arquivo | SHA-256 |
| --- | --- |
| `docs/G3A_COSTS_AND_G3B.md` | `b148ab63e6d94c9f336124670cc52aaed8dc9f301f1008e57047deba5a79ed1d` |
| `docs/G3A_ARCHITECTURE.md` | `9c641ec16b70b57f5c23c8d184fb73faa9048cb642301423f45079e3f93204f9` |
| `docs/G3A_IAC.md` | `9ac0f201a8505cd4be7170a37c54078ebf00c131f834a199b5b3556f165a7ff4` |
| `docs/G3A1_DESIGN.md` | `aae6fa25d0bb8669cf862e3474a37a07c21a6df817c559aa1eaceb6d1cc8c4b0` |
| `docs/G3A1_SCHEMA.md` | `2d4e626d177405de5fdd2a88d0802ab24a03d1a388fc833621795dacd4509184` |
| `docs/G3A1_DECISION.md` | `863d955f1c8d8670c03a379ecc30aa5e916169611ff3b418290accc375e3f395` |
| `docs/G3A1_RUNBOOK.md` | `1536fc2ffaf0c1db994988945c29536e02104f6b16ebaa68bbaef4229aa7844d` |
| `docs/G3A1_BENCHMARK.md` | `bd2b82bde36acd1a7b4f29908b010bfaedf2c6830c170893db760e0c7d98fe62` |
| `engine/shared_upload_authority.py` | `670c43a6e6f7dae35e012c8fe4dc67d2c883f82e6db6e9fe878523beaafbcc3f` |
| `engine/shared_upload_http.py` | `77708c3d7cf3cd9612e45f51b1820c3fecb9dbb5c1860ec081c28a9ba9515ae3` |
| `supabase/migrations/20261107150813_shared_upload_authority.sql` | `9d8521e9eed6a72c18942d6628b08dc2f53dd2ddc5d4bb3befbfb26705f08364` |
| `scripts/benchmark_g3a1.py` | `6a4e6664e5fb14b238554654a7a7e47f94ae9430e1731844e884e71d45370298` |
| `G3A1_BASELINE_SHA256SUMS.txt` | `862994ef56d423d1a6116c43d678b5a6376038dba7aa4cb7ee7cf1a49f034c3f` |

O ZIP do artefato acima foi verificado com SHA-256
`dd8464d5ada9c45eb46c24518daad9077522477a76bfa0cd7b557a4d53fb52da`.
Hashes dos membros usados nesta auditoria:

| Membro do ZIP | SHA-256 |
| --- | --- |
| `ltc-g3a1-ci/g3a1-tests.txt` | `40791188ea6d4873bf5e1cc35cfa91833205c6d17f3bfd0744153b901a3f1489` |
| `ltc-g3a1-ci/g3a1-iac.txt` | `2b1e2d1a5e13f573df295c29c1266c97eab23faff95a3fcbec1634058e52195c` |
| `ltc-g3a1-evidence/shared-benchmark/summary.json` | `273b869eca2adc94f606b5a0fdae8f6dd8b5ba8100fe0b3ab79772367ea0ca5b` |
| `ltc-g3a1-evidence/shared-auth.txt` | `6c5fad23ee0319ac0fdd6812d1aaeb80edeac98da03a99f69bfa4c7869c25a1d` |

## Apêndice B — checks estáticos reproduzíveis e resultado

Nos checks locais de auditoria abaixo foram executados somente Git/leitura de
bytes/Python stdlib, sem Terraform, adapter cloud, migração, stack Auth ou FFmpeg.
O CI herdado pode repetir as regressões locais e a validação histórica de
toolchain com backend desabilitado; isso não executa plan/apply ou cria recursos
nem transforma regressão local em teste cloud P0.
Comandos pressupõem os dois commits obtidos por Git; não fazem merge:

```sh
git ls-remote origin refs/heads/main refs/heads/feat/g3a1-shared-upload-ledger-01
git show e40357d08fa3c0b258b63c0d3a811cbab2b23206:docs/G3A1_SCHEMA.md
git diff --name-status 7a271382487ac4d16e278e5ccb8c345279b3cc16 e40357d08fa3c0b258b63c0d3a811cbab2b23206
git diff --check
python3 - <<'PY'
import hashlib, subprocess
base = '7a271382487ac4d16e278e5ccb8c345279b3cc16'
proposal = 'e40357d08fa3c0b258b63c0d3a811cbab2b23206'
def read(ref, path):
    return subprocess.check_output(['git', 'show', f'{ref}:{path}'])
def files(ref):
    return set(subprocess.check_output(
        ['git', 'ls-tree', '-r', '--name-only', ref], text=True).splitlines())
old, new = files(base), files(proposal)
assert len(old) == 114 and len(new) == 149
assert old <= new
assert all(read(base, p) == read(proposal, p) for p in old)
rows = read(proposal, 'G3A1_BASELINE_SHA256SUMS.txt').decode().splitlines()
assert len(rows) == 114
for row in rows:
    digest, path = row.split('  ', 1)
    assert path in old
    assert hashlib.sha256(read(base, path)).hexdigest() == digest
    assert hashlib.sha256(read(proposal, path)).hexdigest() == digest
print('PASS: main=114, PR9=149, unchanged=114, additions=35, manifest=114')
PY
```

Resultado observado do check acima: **PASS: main=114, PR9=149, unchanged=114,
additions=35, manifest=114**. Os hashes do apêndice A foram calculados por
`sha256(git show <SHA>:<path>)`. A validação do ZIP leu os membros listados,
confirmou `source_sha`, `source_worktree_clean=true`, 276 testes em sete seções
`OK` e as contagens/limites do benchmark. É conferência de evidência arquivada,
não reexecução dos testes.

Após os seis documentos estarem presentes, executar o check abaixo na raiz do
checkout P0. Ele inclui arquivos documentais ainda não commitados e verifica os
links Markdown relativos sem rede. O output final e seu SHA-256 devem ser
registrados pelo mantenedor na entrega; esta instrução não é uma declaração de
que todos os documentos já estavam presentes no momento desta redação.

```sh
python3 - <<'PY'
from pathlib import Path
from urllib.parse import unquote, urlsplit
import hashlib, re, subprocess
base = '7a271382487ac4d16e278e5ccb8c345279b3cc16'
root = Path.cwd()
old = set(subprocess.check_output(
    ['git', 'ls-tree', '-r', '--name-only', base], text=True).splitlines())
expected = {f'docs/G3B_P0_{name}.md' for name in (
    'PREFLIGHT', 'ARCHITECTURE', 'COST_MODEL', 'SECURITY_GATES',
    'STAGING_RUNBOOK', 'DECISION')}
current = set(subprocess.check_output(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
    text=True).splitlines())
assert len(old) == 114 and current - old == expected
for path in old:
    original = subprocess.check_output(['git', 'show', f'{base}:{path}'])
    assert (root / path).read_bytes() == original, path
for path in expected:
    document = root / path
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', document.read_text()):
        uri = urlsplit(target.strip('<>'))
        if uri.scheme or uri.netloc or not uri.path:
            continue
        assert (document.parent / unquote(uri.path)).exists(), (path, target)
workflows = sorted(root.glob('.github/workflows/*.yml'))
assert len(workflows) == 4
output = 'PASS: six additive Markdown docs; 114 historical files unchanged; local links resolve; 4 inherited workflows'
print(output)
print('output_sha256=' + hashlib.sha256((output + '\n').encode()).hexdigest())
PY
```

Caso main/PR #9 mudem durante a revisão, os resultados históricos não descrevem
automaticamente o novo estado; interromper a promoção e revalidar o delta antes
de solicitar autorização de P1. A documentação não transforma checks CI locais
ou este verificador estático em prova operacional cloud.

### Resultado observado da integração documental

Os dois snippets acima e o bloco `Decimal` de COST_MODEL foram executados na
integração dos seis documentos, com exit 0. A revisão independente reconferiu
os links, hashes e aritmética; não restou finding documental material. Foram
também conferidos os 12 hashes HTML da pesquisa contra os arquivos temporários
efetivamente consultados. Saída registrada:

```text
PASS: main=114, PR9=149, unchanged=114, additions=35, manifest=114
PASS: six additive Markdown docs; 114 historical files unchanged; local links resolve; 4 inherited workflows
output_sha256=da33ef7aae1583fea926945fed254b289981ef7a26aad7fb2609eaf704f4ec3b
known_subtotal_USD 0.59265232250
all_in_USD UNKNOWN; cloud NOT_VALIDATED; spend BLOCKED
PASS: balanced fenced code blocks in all six documents
PASS: 12 public-price HTML response hashes match research files
```

O `output_sha256` é da linha anterior mais newline, conforme o snippet, não
um digest circular deste documento. `git diff --check` também retornou exit 0.
O PR DRAFT da entrega registra separadamente o SHA final, a repetição dos checks
em clone limpo e os quatro workflows herdados concluídos. Nenhum desses PASS
fecha os bloqueios de autorização/implementação ou valida serviços remotos.
