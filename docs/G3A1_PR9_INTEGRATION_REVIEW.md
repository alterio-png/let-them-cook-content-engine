# Issue #12 — revisão técnica final do PR #9

**Decisão: GO para solicitar uma decisão humana de aprovação/merge do PR #9,
no escopo local testado.** Nenhum defeito material bloqueante foi reproduzido.
Há quatro findings de capacidade/diagnóstico ainda **OPEN**, detalhados abaixo.
Este parecer técnico assistido não é aprovação humana, não executa merge e não
torna o PR ready-for-review. G3B-P1 permanece **NO GO**; cloud **NOT_VALIDATED**
e custos reais **UNKNOWN**.

## Referências fixadas e independência da revisão

Auditoria da [Issue #12](https://github.com/alterio-png/let-them-cook-content-engine/issues/12)
em 2026-11-07, com quatro frentes de revisão em contextos novos: SQL,
publicação/ObjectStore, HTTP/Auth e backpressure. Uma quinta leitura verificou
contagens, proveniência e fechamento. Os revisores não modificaram a fonte
examinada. São revisões técnicas por agentes, não pessoas externas aprovando o
próprio PR; aprovação humana independente continua pendente.

| Referência | SHA e estado conferido |
| --- | --- |
| Main | `7a271382487ac4d16e278e5ccb8c345279b3cc16` |
| PR #9 examinado | `e40357d08fa3c0b258b63c0d3a811cbab2b23206`, OPEN / DRAFT, sem merge/aprovação humana registrada |
| PR #11 documental | `eb507aff12fdfc16427aa958f46338855cf88d99`, OPEN / DRAFT, sem merge |
| Creative OS | Main `1a30934928a767fb851cd1d846318d6c60660394`, somente leitura |

O diff main→PR #9 contém 35 adições e nenhuma alteração dos 114 arquivos
anteriores: árvore de 114 para 149 arquivos. Todas as 114 entradas de
`G3A1_BASELINE_SHA256SUMS.txt` coincidem com os bytes do main e do PR #9.
O PR #11 acrescenta somente seis Markdown sobre a mesma base; seus arquivos
e referência permanecem intactos. Esta branch de auditoria não integra o código
do PR #9 nem sincroniza o PR #11.

## Reprodução independente e evidências

Foi criado um clone Git independente com `--no-local`, no SHA exato do PR #9.
A árvore estava limpa antes e depois. Dependências previamente revisadas foram
reutilizadas no venv existente, sem instalação nova; `pip check` passou. As
versões, hashes de fontes, comandos e limites estão na
[proveniência](../evidence/issue12/provenance.json) e no
[guia de reprodução](../evidence/issue12/README.md).

Ambiente: Python 3.12.14, PostgreSQL 16.15 e GoTrue oficial 2.197.0 com imagens
fixadas por digest, FFmpeg 7.1.5, Docker 29.9.2/vfs, Linux x86_64. Dados somente
sintéticos; banco e Auth locais descartáveis. O login HTTP restrito e o papel
de reconciliação foram exercitados separadamente.

| Etapa correspondente aos workflows | Resultado novo desta auditoria |
| --- | --- |
| G0, contratos e FFmpeg real | 12 testes PASS |
| G1, PostgreSQL/isolamento/confiabilidade | 36 testes PASS |
| G2, mídia, objetos, falhas e concorrência | 49 testes PASS |
| G3A, contratos locais | 118 testes PASS |
| G3A, Auth oficial local | 10 testes PASS |
| G3A.1, upload compartilhado | 44 testes PASS |
| G3A.1, Auth oficial compartilhado | 7 testes PASS |
| Total da reprodução principal | **276 IDs distintos, zero skips, sete seções OK** |
| IaC histórica, somente validação local | Terraform 1.17.1 / provider Google 8.10.0; valid=true, zero erros/avisos; sem plan/apply |

O [log integral](../evidence/issue12/full-276.txt) termina no marcador de
conclusão do runner com `set -euo pipefail`; o
[log IaC](../evidence/issue12/iac.txt) registra os comandos locais executados.
O status do processo do runner não foi preservado após a mudança de turno;
não é inventado no JSON de proveniência. Os PASS acima são comprovados pelas
saídas individuais, contagem de IDs, ausência de skips e marcador final.

Os cinco workflows publicados no HEAD do PR #9 também foram reconferidos em
SUCCESS, tanto para push como PR. Esses runs arquivados são distintos desta
reprodução local:
[G0](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140494),
[G1](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140564),
[G2](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140477),
[G3A](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140450),
[G3A.1](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140558).

## Revisões adicionais, sem inflar os 276 casos

| Revisão | Evidência adicional efetivamente executada |
| --- | --- |
| [SQL independente](../evidence/issue12/sql-review/INDEPENDENT_SQL_REVIEW.md) | **36 checks** novos: catálogo/RLS, 112 células de privilégios, helpers/search_path, identidade/owner/viewer, conflitos de chave, concorrência, quota cumulativa, rollback de audit/expiry, FKs, deadlines após locks/escritas e papel reconciler separado. |
| [Backend](../evidence/issue12/backend-review/REVIEW.md) e [ObjectStore](../evidence/issue12/backend-review/object-contract/REVIEW.md) | 21 testes existentes de falha/deadline, cinco probes novos incluindo SIGKILL após commit SQL antes da resposta, seis probes de objeto e 18 testes históricos de ObjectStore. **50 execuções** nesse escopo, com sobreposição da suíte principal. |
| [HTTP/Auth](../evidence/issue12/http-review/REVIEW.md) | Novo laboratório GoTrue real, dois processos HTTP e probes de organização/owner/viewer, sessão expirada, logout/revogação durante I/O, framing/body cap/timeout e binding privado. Nove testes criptográficos existentes rodaram separadamente, sem serem chamados de emissão Auth real. |
| [Backpressure](../evidence/issue12/backpressure-review/REPORT.md) | Reanálise independente do ZIP/eventos do CI anterior; limites de retry e perfis futuros propostos. Reanálise não é um novo teste de carga. |
| [QA de fechamento](../evidence/issue12/final-qa/REVIEW.md) | Reconferência dos 276 IDs distintos, 114 hashes, artefatos, contratos de revogação e findings OPEN. |

### Propriedades de correção examinadas

Reserva fixa owner/chave/tamanho/hash/objeto/expiração; conflito não vira nova
cobrança. Locks seguem membership→cota→reserva. Migração é aditiva, sem DML de
ledger para usuários; funções privadas têm ACL explícita e search_path vazio.
RLS/FKs compostas e recibos imutáveis preservam organizações e ownership.

Claim durável precede storage. Corpo é copiado/hash antes de claim; publicação
create-if-absent é seguida de readback real. Recibo só entra com hash/bytes/
generation coerentes e fence atual. Foram exercitados SIGKILL, resposta perdida,
transação SQL falha, heartbeat sob lock, trigger atrasado, escritor antigo e
retomada após TTL. Prazo SQL é rechecado após locks e escritas; não promete ACK
de COMMIT/fsync em hard real-time ou cancelamento de I/O externo bloqueado.

Somente reserva expirada nunca reclamada, fence zero, reembolsa uma vez.
Publicada/incerta continua cobrada; ausência de objeto não prova ausência de
escritor. Não há GC destrutivo nem rollback de organização ativa a SQLite vazio.
O reconciler separado recupera histórico sem conceder DML de tabela à API.

### Revogação: candidatos investigados e RESOLVED por contrato existente

Os probes oficiais distinguiram duas situações, sem alterar código:

- Logout após a verificação de sessão não cancela uma requisição já admitida:
  o PUT pausado concluiu com 201; requisições novas nas duas réplicas retornaram
  401. Isso coincide com `docs/G3A_AUTH.md:72` no SHA examinado.
- Revogação de membership durante readback de recibo já autorizado pode deixar
  GET/PUT replay em andamento retornar o recibo existente. Novos requests foram
  negados, sem novo recibo/cobrança. Na publicação nova, revogação antes do
  commit negou a criação com 403 e reteve a quota/objeto incertos.

Status **RESOLVED como interpretação de contrato**, sem fix aplicado e sem
defeito de severidade atribuído. Não anunciar cancelamento retroativo de toda
operação admitida; exigir isso seria um contrato novo a desenhar/revisar.

## Concorrência: resultado novo versus CI arquivado

| Coorte | Reprodução desta auditoria | CI anterior de dois CPUs |
| --- | --- | --- |
| 100 duplicatas | 140 tentativas físicas: 100×201 + **40×503** recuperadas; um grant/128 bytes; máximo observado duas tentativas por operação | 305 tentativas: 100×201 + **205×503**; máximo observado cinco; um grant/128 bytes |
| 20 chaves distintas, limite dez | 20 tentativas: 10×201 + 10×429; dez reservas/1.280 bytes | 21 tentativas: 10×201 + 10×429 + uma 503 recuperada |
| Restart | PIDs 198468/198521, novo 199257; mesmo recibo/grant/cobrança; zero SQLite | PIDs 16517/16520, novo 17428; mesmas invariantes |
| p95 lógico duplicatas | 0,473332 s, incluindo retry/backoff | 1,727618 s, incluindo retry/backoff |

O [report novo](../evidence/issue12/full-run/shared-benchmark/summary.json)
identifica SHA limpo e cinco CPUs lógicos; o arquivado identifica dois. Houve
diferença de ambiente/coorte: não é regressão ou ganho de capacidade cloud
demonstrado. 503 não foi decomposto por causa; não chamar todos de saturação
de um componente específico. A carga usa LOCAL_CRYPTO_CONTRACT; Auth oficial
é um gate funcional separado, não carga mista de sessão/DB/render.

CPU/RSS medidos abrangem cliente/descendentes e excluem PostgreSQL/GoTrue;
amostragem não é pico exato. O benchmark conta retries físicos e falha em
exaustão; uma coorte sem progresso ou composta só de 503 não pode PASS.

## Findings: severidade, owner, status e efeito no merge

Severidade refere-se à lacuna observada, não a uma alegação de corrupção ou
bypass. Nenhum achado crítico/alto de correção local foi reproduzido.

| ID | Severidade / achado | Reprodução/evidência | Owner proposto / status / gate |
| --- | --- | --- | --- |
| BP-02 | Média: envelope sustentado e justiça entre tenants não validados | Burst arquivado 3,05× de amplificação; sem carga sustentada ou coorte de tenants concorrentes. Ver análise/proposta de perfis. | Performance/serviço, a designar. **OPEN**; bloqueia SLA/escala/operação, não integração local com esse limite declarado. |
| BP-03 | Média: orçamento agregado Auth/render/upload/heartbeat/reconciler não medido | Quatro slots por autoridade de upload não incluem sessão/render; checker abre conexão por request antes da admissão de rota. | Backend/banco, a designar. **OPEN**; bloqueia carga mista/operacional até medição e enforcement. |
| BP-04 | Média: 503 sem atribuição de causa/espera/backlog por estágio | Eventos guardam status numérico; SQL/conexão/semaforo/handler podem resultar em 503. A causa não pode ser reconstruída dos eventos. | Observabilidade/backend, a designar. **OPEN**; fechar antes de aceitar um perfil operacional de backpressure. |
| BP-05 | Baixa: resumo de coorte falha não conserva todos os totais terminais/parciais | `HTTPWorkload.call` só anexa operação concluída; em falha o status FAILED/raw attempts persiste, mas faltam totais de exaustão/parciais no resumo. | Benchmark, a designar. **OPEN**; melhoria diagnóstica; falha não vira PASS no código examinado. |
| BP-01 | Informativa: distinção físico/lógico e retry finito confirmada | Reanálise dos eventos e limite de 16 tentativas; exaustão gera erro. | Benchmark. **RESOLVED por verificação**, nenhum fix desta auditoria. |

Os quatro OPEN devem ser aceitos explicitamente como backlog fora do escopo
de integração local pelo revisor humano. Este parecer não os fecha por haver
276 PASS. Perfis futuros propostos: serial 1 primeiro, concorrência HTTP 2
somente depois; em baseline local sem falha, estados esperados, uma tentativa
e nenhum 503 inesperado. Carga de overload deve medir progresso por tenant,
deadline lógico total, filas limitadas, exaustões, recursos agregados e drain.
São critérios a aprovar/testar, **NOT_VALIDATED**, sem SLA cloud inventado.

## Gate humano e sincronização posterior do PR #11

**A única ação humana pendente para desbloquear a próxima etapa é o mantenedor
revisar este parecer/findings e decidir explicitamente a aprovação e o merge
controlado do PR #9 no SHA examinado.** Nenhum merge foi feito e o PR permanece
DRAFT. A conta autora não deve usar este parecer para autoaprovar o próprio PR.
Mudança de HEAD exige revisar o delta e revalidar as provas afetadas.

Após um merge humano real, a etapa automatizável seguinte é verificar o SHA
integrado e os **cinco workflows no novo main**. Qualquer resultado ausente ou
diferente de PASS mantém o bloqueio. Só depois pode começar a sincronização
documental descrita no [plano do PR #11](PR11_POST_MERGE_SYNC_PLAN.md).

Não foi feito rebase, mudança de base, atualização dos seis documentos ou push
no PR #11. O plano preserva a prova histórica, exige main real/backup/branch
isolada e apenas mudanças documentais revisadas, com CI no SHA final. Merge do
PR #11 também requer decisão humana própria. Nenhuma dessas ações autoriza P1.

## Limites permanentes desta entrega

Sem cloud login, recursos hospedados, serviços pagos, dados de clientes,
produção, deploy, Terraform plan/apply, merge ou mudança no Creative OS.
Somente laboratórios locais e validação histórica sem backend remoto.
Auth hospedado, IAM/OIDC remoto, GCS/Tasks/Jobs reais, conexões cloud, custos
completos e hard termination remota permanecem **NOT_VALIDATED/UNKNOWN**.

Os arquivos de evidência têm um manifesto relativo completo em
[SHA256SUMS.txt](../evidence/issue12/SHA256SUMS.txt). O PR de auditoria publica
separadamente seu próprio SHA e CI; seus workflows herdados não são uma nova
execução do workflow G3A.1 nem mudam o SHA do PR #9.
