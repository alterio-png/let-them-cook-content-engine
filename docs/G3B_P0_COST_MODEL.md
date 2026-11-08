# G3B-P0 — modelo de custos e limites da proposta

## Adendo de proveniência, 2026-11-08 UTC

O [PR #9](https://github.com/alterio-png/let-them-cook-content-engine/pull/9), HEAD revisado `e40357d08fa3c0b258b63c0d3a811cbab2b23206`, foi mesclado em 2026-11-08 no `main` **`0c67fc6ac90aa1ec8d859e4392471dc603291a7c`**, árvore **`a2ce768cc21e6df3decb5c6d5ba9e9342d8fc2c3`**. A autorização de merge foi explícita pelo mantenedor nesta conversa; o registro GitHub de revisão técnica está como `COMMENTED`, não como um `APPROVED` humano independente. O [PR #13](https://github.com/alterio-png/let-them-cook-content-engine/pull/13) reproduziu 276 testes e apontou BP-02, BP-03 e BP-04 (médios) e BP-05 (baixo), ainda abertos.

A integração modifica a **proveniência da implementação**, mas não as tarifas consultadas. O modelo abaixo mantém integralmente a pesquisa de **2026-11-07**, inclusive as 12 capturas HTML e SHA-256, referências oficiais, SKU ilustrativo `us-central1`, hipóteses, sensibilidades e aritmética `Decimal`. Nenhuma nova região, plano Supabase, conta, crédito ou fatura foi verificada neste adendo.

**Subtotal parcial hipotético: USD 0.59265232250; custo all-in: UNKNOWN; autorização de gasto: BLOCKED.** O teto de USD 10 é somente uma proposta sem hard cap; uma nova assinatura Supabase Pro de USD 25/mês excede esse valor antes dos demais custos. Não tratar crédito, franquia, baseline, egress, versionamento/soft deletion, callbacks/Jobs, retenção ou billing atrasado como gratuitos ou já validados. As 305 tentativas físicas/205 respostas 503 no CI representam backpressure local, não preço por render ou garantia de escala.

O main `0c67fc6ac90aa1ec8d859e4392471dc603291a7c` tem cinco workflows PASS ([links no preflight](G3B_P0_PREFLIGHT.md)); a branch documental exige cinco PASS separados em seu HEAD final. Esta sincronização não executa serviços cloud nem consulta billing.

---

> **Snapshot original de 2026-11-07, preservado sem reescrita:** O texto a seguir foi produzido para o PR #11 em `eb507aff12fdfc16427aa958f46338855cf88d99`. Menções a “PR #9 OPEN/DRAFT”, main sem G3A.1 e apenas quatro workflows correspondem àquela data, não ao status integrado descrito acima.


**Consulta: 2026-11-07 UTC. PASS para pesquisa pública e aritmética estática;
custos reais UNKNOWN; execução cloud NOT_VALIDATED; contratação BLOCKED.**
Este documento não autoriza despesa. Conta, contrato, moeda de cobrança,
região, projeto, plano Supabase, impostos e créditos disponíveis não foram
selecionados nem inspecionados. Nenhum recurso foi criado.

O main auditado é `7a271382487ac4d16e278e5ccb8c345279b3cc16`. G3A.1 é a
proposta do [PR #9, HEAD e40357d](https://github.com/alterio-png/let-them-cook-content-engine/pull/9),
ainda OPEN/DRAFT, e não uma funcionalidade integrada ao main. A evidência local
desse HEAD demonstra consistência, incluindo 205 respostas 503 recuperadas em
305 tentativas físicas para 100 duplicatas; não estabelece tempo de render
cloud, capacidade sustentada ou preço por minuto de vídeo.

## 1. Como ler os números

As tarifas abaixo foram verificadas nas páginas oficiais na data da consulta.
As tabelas Google abertas exibiam **Iowa/us-central1**, que é apenas um SKU
ilustrativo, **não uma escolha de região ou aprovação de transferência de
dados**. Para outra região, confirmar novamente cada SKU antes da autorização.
Valores são USD de lista, sem compromissos, descontos, créditos, free tiers,
tributos, conversão cambial ou margem. Uma tarifa conhecida não torna conhecido
o consumo nem o valor final da fatura.

Não tratar duração da mídia como tempo alocado: início, leitura, hash, probe,
três renders, uploads, verificação e término também consomem tempo. `getrusage`
e RSS locais não medem vCPU/GiB-segundo faturados. G1 `cost_cents` é um fixture
de admissão e não uma tarifa.

## 2. Tarifas oficiais observadas

| ID / componente | Tarifa de lista observada | Unidade e condição |
| --- | --- | --- |
| C1 Cloud Run Jobs e serviço instance-based | CPU `0.000018`; RAM `0.000002` | USD/vCPU-s e USD/GiB-s, SKU ilustrativo us-central1; Jobs e instance-based cobram vida da instância, mínimo 60 s; arredondamento de 100 ms. |
| C1 Cloud Run serviço request-based | CPU ativo `0.000024`; RAM ativa `0.0000025`; requests `0.40/milhão` | CPU idle de mínimo configurado `0.0000025`, RAM idle `0.0000025`; a configuração de billing muda a fórmula. Não aplicar tarifa de serviço request-based ao worker Job. |
| C2 GCS Standard regional, namespace plano | `0.000027397/GiB-h`; A `0.005/1.000`; B `0.0004/1.000` | GiB-dia ilustrativo = `24 × 0.000027397 = 0.000657528`; Standard sem duração mínima. Objeto vivo, versão não corrente e soft-deleted consomem storage. |
| C2 GCS internet | Exemplo de faixa inicial `0.12/GiB` | Destinos mundiais elegíveis, excluindo Austrália/China, até 10 TiB/mês; origem/destino, rota e franquias precisam ser confirmados. Não usar como tarifa universal de toda rede GCP. |
| C3 Cloud Tasks | `0.40/milhão` de operações após franquia | Contar chamadas de API e tentativas de entrega, com unidades de 32 KB; listagens podem contar por tarefa. Rede é adicional. |
| C4 Secret Manager | versão ativa/localização `0.000082192/h`; acesso `0.03/10.000`; rotação `0.05/notificação` | Enabled **e Disabled** contam como ativos. Replicação automática conta uma localização; replicação gerenciada multiplica localizações. |
| C5 Artifact Registry | `0.000136986/GiB-h` acima da franquia | Imagens/camadas retidas, cópias e transferência contam; scanner/build não estão incluídos nessa tarifa. |
| C6 Cloud Logging | ingestão/storage inicial `0.50/GiB`; retenção adicional `0.01/GiB-mês` após 30 dias | Sem aplicar franquia; exportação para outro destino pode cobrar armazenamento/rede no destino. Métricas customizadas, traces e monitores não estão incluídos nessa linha. |
| C7 Supabase Pro | plano a partir de `25/mês`; MAU excedente `0.00325`; disco excedente `0.125/GB`; egress excedente `0.09/GB` | Página lista 100.000 MAU, 8 GB de disco/projeto e 250 GB egress incluídos; uso, franquia remanescente e projeto existente UNKNOWN. Daily backup com retenção de sete dias está listado no Pro. |
| C8 Supabase Micro | `0.01344/h` | Arredonda hora parcial para hora inteira; consumo independe de requisições. Compute não é coberto pelo Spend Cap. |
| C9 Supabase PITR de sete dias | `0.137/h` | Hora parcial inteira; add-on não coberto pelo Spend Cap. Não habilitado nem necessário por presunção. |
| C10 R2 Standard | storage `0.015/GB-mês`; A `4.50/milhão`; B `0.36/milhão`; egress R2 internet sem tarifa | Arredonda uso para próxima unidade de billing. Custos do provedor de origem, Workers/proxy e conta não desaparecem. GB-mês usa picos diários médios de período de 30 dias; não misturar com GiB-h Google. |
| C11 S3 alternativa | UNKNOWN para SKU/rota desta proposta | Página oficial consultada, porém região, classe, requests, replicação e tabela dinâmica aplicável não selecionados; não substituir por tarifa antiga de blog ou por preço de S3 Tables. |
| C12 VM alternativa | UNKNOWN para máquina/disco/rota desta proposta | Família, região, CPU, RAM, discos, IPv4 e operação não selecionados; VM parada pode deixar discos/endereço/backup faturando. |

Fontes oficiais, todas consultadas em **2026-11-07**:

- **C1:** [Cloud Run pricing](https://cloud.google.com/run/pricing).
- **C2:** [Cloud Storage pricing](https://cloud.google.com/storage/pricing).
- **C3:** [Cloud Tasks pricing](https://cloud.google.com/tasks/pricing).
- **C4:** [Secret Manager pricing](https://cloud.google.com/secret-manager/pricing).
- **C5:** [Artifact Registry pricing](https://cloud.google.com/artifact-registry/pricing).
- **C6:** [Google Cloud Observability pricing](https://cloud.google.com/products/observability/pricing).
- **C7:** [Supabase pricing](https://supabase.com/pricing).
- **C8:** [Manage Compute usage](https://supabase.com/docs/guides/platform/manage-your-usage/compute), página atualizada em 2026-10-29.
- **C9:** [Manage PITR usage](https://supabase.com/docs/guides/platform/manage-your-usage/point-in-time-recovery), atualizada em 2026-10-23.
- **C10:** [R2 pricing](https://developers.cloudflare.com/r2/pricing/), atualizada em 2026-10-30.
- **C11:** [Amazon S3 pricing](https://aws.amazon.com/s3/pricing/).
- **C12:** [Compute Engine pricing](https://cloud.google.com/products/compute/pricing).
- **C13:** [Google network pricing](https://cloud.google.com/vpc/network-pricing).
- **C14:** [Budgets and budget alerts](https://docs.cloud.google.com/billing/docs/how-to/budgets).

Não foi consultado billing privado ou API com credenciais. Free tiers e créditos
são linhas separadas a verificar: nenhuma franquia foi subtraída nas contas.
Exemplos: créditos de compute Supabase de USD 10/mês não são USD 10 de crédito
geral nem aprovação de despesa; não cobrem a assinatura Pro. Tarifas de métricas
elegíveis e novas regras de alerting com data futura não foram tratadas como
cobranças presentes nem como gratuidade de toda observabilidade.

## 3. Planilha de hipóteses do ensaio futuro

| Entrada | Proposta inicial, não aprovada | Estado / evidência a obter |
| --- | --- | --- |
| Mídia | Fontes sintéticas de 60 s e 300 s, três formatos cada; áudio e SRT | Bytes/bitrate/CPU reais UNKNOWN; uploads devem caber no limite atual de 1 MiB ou exigir mudança revisada. |
| Worker | 1 vCPU, 2 GiB, uma task, parallelism 1, retries de plataforma 0 | Proposta herdada; provider timeout 300 s e deadline de app 120 s. Sucesso do vídeo de cinco minutos BLOCKED até provar viabilidade ou revisar os limites. |
| Quantidade | Uma operação normal de 1 min, uma de 5 min, uma operação distinta de falha com kill/recovery: no máximo **quatro execuções físicas cumulativas** | Uma única vaga física global; incerteza mantém a vaga. Quatro é orçamento total, não quatro retries por operação. |
| Sinais Tasks | Cenário de até quatro eventos e orçamento independente de **20 callbacks totais**, com contador e stop explícitos futuros | **BLOCKED:** o limite cumulativo não está implementado/validado. `maxAttempts=5` e `maxRetryDuration=300s` históricos não garantem cinco entregas/evento. Creates, lookups, listagens, deletes e chunks são adicionais. |
| API/controlador | Um serviço/uma instância máxima por função, 1 vCPU/0,5 GiB, min=0 | É proposta a revisar, não configuração implantada; Auth/session DB tem orçamento separado. |
| Janela | Até duas horas, incluindo operação e drenagem planejada | Stop de admissão antes de `fim − timeout máximo − drenagem`; término real e cauda faturada UNKNOWN. |
| Objetos | Hipótese de até 2 GiB **somando todas as gerações faturáveis** | Não é hard cap implementado. Quatro tentativas × três saídas × 128 MiB já permitem 1,5 GiB, antes de inputs/metadados/versões; medir inventário e bloquear se não houver limite demonstrável. |
| Logs / registry / secrets | Para a conta ilustrativa: 50 MiB logs, 1 GiB imagem, dez versões-localização por duas horas | Só hipóteses. Logs de falha/retry, histórico de imagem e versões antigas precisam de limites/retention revisados. |
| Região/conta/residência | Não selecionadas | BLOCKED; nenhuma tarifa de exemplo define esses valores. |

## 4. Fórmula completa, sem transformar UNKNOWN em zero

Para cada tentativa física `i`, `T_i` é a vida alocada medida pelo provedor,
incluindo startup/shutdown; `V_i` e `M_i` são CPU e RAM alocados:

```text
Jobs_lista = Σ max(60, ceil(T_i/0.1) × 0.1) × (V_i × pCPU + M_i × pRAM)
Storage_lista = Σ (bytes faturáveis da geração / 2^30) × horas × pGiB_h
Operações_lista = A/1000 × pA + B/1000 × pB + Tasks_unidades/1e6 × pTasks
API_lista = tempo agregado de instâncias ativas × recursos/tarifas
            + tempo idle cobrável configurado + requests físicos/1e6 × pReq
Fatura = plano/idle fixo + Jobs + API + storage + operações + logs + secrets
         + imagens/build/scans + DB/Auth/backups + egress de cada origem
         + conectividade + suporte + cauda de retenção/teardown + impostos
         − descontos elegíveis comprovados − créditos efetivamente disponíveis
```

Somar tempo por instância evita cobrar duas vezes requests concorrentes no
mesmo tempo alocado. Usar todo tempo de cada request como se fosse exclusivo é
apenas um cenário conservador, não medição de billing. Um 503 que chegou ao
container pode consumir CPU, request, Auth lookup e logs; retries não são grátis
por serem idempotentes. Falha de Task pode gerar operação cobrada. A leitura de
status de um upload resumable também deve ser medida; uma transferência
resumable GCS não equivale necessariamente a uma operação A por chunk.

Se qualquer termo aplicável não tiver preço, volume ou limite verificável,
`Fatura = UNKNOWN`; o subtotal conhecido abaixo **não é orçamento all-in**.

## 5. Cenários de render de um e cinco minutos

Contas de **compute Jobs somente**, com C1 e uma execução física. `T` é uma
hipótese de tempo alocado, não benchmark nem relação garantida com a mídia.

| Mídia sintética | T alocado hipotético | CPU/RAM | Compute USD | Elegibilidade na proposta inicial |
| --- | --- | --- | --- | --- |
| 1 min | 30 s, faturados 60 s | 1 / 2 GiB | 0.001320 | Mínimo de billing; sucesso UNKNOWN. |
| 1 min | 60 s | 1 / 2 GiB | 0.001320 | Hipótese, não previsão. |
| 1 min | 120 s | 1 / 2 GiB | 0.002640 | Deadline app herdado não prova que render/probe/upload cabem. |
| 5 min | 300 s | 1 / 2 GiB | 0.006600 | **BLOCKED** como plano de sucesso: app herdado limita whole-job a 120 s; o timeout de provedor não amplia o app. |
| 5 min | 600 s | 1 / 2 GiB | 0.013200 | Sensibilidade: exige revisão de app/provedor, teste e nova autorização. |
| 5 min | 1.800 s | 1 / 2 GiB | 0.039600 | Sensibilidade de timeout, não configuração selecionada. |
| 5 min | 600 s | 2 / 4 GiB | 0.026400 | Alternativa com o dobro dos recursos; desempenho e aprovação UNKNOWN. |

Três formatos executados dentro da mesma task devem ser medidos como uma vida
de execução com CPU/RAM alocados; não multiplicar o preço novamente por três
se `T` já inclui os três renders. Qualquer retry físico soma outra execução e
pode pagar outro mínimo de 60 s. Launch incerto não autoriza retry físico.

## 6. Baseline idle e subtotal do pior caso condicionado

Idle não significa stack sem custo. Um worker Jobs sem execuções não tem tempo
alocado nessa linha; serviços min=0 sem instância também não têm tempo alocado
naquela condição. Ainda restam banco/Auth, storage retido, imagens, secrets,
backups, endpoints de rede e observabilidade. Consumo real dessas linhas é
**UNKNOWN**, não custo total zero.

Uma nova assinatura Supabase Pro lista USD 25/mês, antes de extras. Não assumir
que uma janela de duas horas transforma a assinatura em `25/730 × 2` nem que
existe conta aprovada: **o caminho de nova assinatura Pro não cabe no teto
proposto de USD 10**. Micro por duas horas completas soma USD 0.02688 de compute
bruto; sete dias de PITR por duas horas soma USD 0.274 de add-on se selecionado,
além da assinatura e arredondamentos de hora. Plano Free ou conta existente
exigem aprovação, avaliação de limites e backup; não são fallback gratuito
presumido. DB self-hosted desloca custo/backup/Auth/operação, não os elimina.

| Linha do cenário limitado | Hipótese explícita | USD conhecido, sem créditos |
| --- | --- | --- |
| Jobs inicial | Hipótese de quatro vidas alocadas de 300 s × 1 vCPU/2 GiB | 0.026400 |
| API + controlador ocupados | Dois serviços × uma instância × 7.200 s × 1 vCPU/0,5 GiB, tarifa ativa request-based | 0.363600 |
| Requests serviços | 1.000 requests físicos que chegam ao container | 0.000400 |
| Tasks | 1.000 unidades totais, incluindo tentativas/gestão | 0.000400 |
| GCS operações | 25.000 A + 25.000 B Standard regional plano | 0.135000 |
| GCS retido | 2 GiB por oito dias, todas as gerações já dentro desses 2 GiB | 0.010520448 |
| Logs | 50 MiB de logs elegíveis × USD 0.50/GiB | 0.0244140625 |
| Secrets | Dez versões-localização por duas horas + 10.000 acessos, sem rotações | 0.031643840 |
| Imagem | 1 GiB de imagem retida por duas horas | 0.000273972 |
| **Subtotal conhecido desse cenário** | Aritmética, sem DB/Auth/backups/rede/taxas/cauda desconhecidos | **0.5926523225** |
| DB/Auth/backups, build/scans, rede/conectividade, suporte, tributos, saldo de créditos, recursos residuais | Não selecionados nem medidos | **UNKNOWN** |
| **Total all-in / autorização de gastar USD 10** | Não demonstrado | **UNKNOWN / BLOCKED** |

Os volumes de operação acima são tetos **de proposta a tornar verificáveis**,
não configuração implantada. O envelope não é hard cap financeiro. Seu operador
deve contar chamadas físicas, negar trabalho acima dos limites e parar se um
contador estiver indisponível; billing atrasado não substitui essa contabilidade.
Registry e secrets retidos após duas horas aumentam suas linhas; não foram
declarados destruídos. Dois GiB que permanecem por 30 dias custam USD
`0.039451680` só em storage nessa tarifa, em vez de USD `0.010520448` por oito.
Um objeto incerto sem prazo de encerramento tem cauda de custo **UNKNOWN**.

Os 300 s por vida alocada são hipótese aritmética, não um limite financeiro
deduzido do timeout de task. Startup, shutdown, atraso de cancelamento e tempo
faturável fora do relógio desse timeout precisam ser medidos; eventual excesso
é termo adicional **UNKNOWN**, não incluído nos USD 0.026400 da linha Jobs.

A [referência oficial RetryConfig](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig),
consultada em 2026-11-07 e atualizada em 2026-09-30, condiciona o fim de retries
à satisfação de **ambos** os limites `maxAttempts` e `maxRetryDuration`. Portanto,
o produto `quatro eventos × cinco tentativas` não é um teto comprovado da fila
histórica. Requests adicionais durante a janela, chamadas de inspeção e eventual
cauda são **UNKNOWN** sem contador/stop independente implementado e ensaiado.
O mesmo vale para o novo limite cumulativo de quatro execuções do ensaio: a vaga
física concorrente e fencing existentes não implementam por si esse contador
cumulativo. Esses dois controles futuros são pré-requisitos BLOCKED, e não
premissas já garantidas usadas para declarar o subtotal como pior caso all-in.

Sensibilidade adicional: uma vaga ocupada por duas horas a 1 vCPU/2 GiB soma
USD `0.158400` de compute; **duas** vagas a 2 vCPU/4 GiB durante duas horas somam
USD `0.633600`. A segunda configuração **não** pertence à proposta inicial.
Janela/timeout só limitam custo se cancelamento, draining e término físico
forem demonstrados. Retardo de terminação/cancelamento e faturamento posterior
continuam UNKNOWN; não afirmar que USD `0.633600` é o pior caso de toda a conta.

## 7. Storage, transferência e alternativas

| Caminho | Modelo / trade-off econômico | Bloqueio de medição |
| --- | --- | --- |
| GCS co-localizado com Run | Somar GiB-h de cada geração e A/B; a regra documentada de mesmo local pode evitar transferência, condicionada à topologia exata. | IAM, rota e local escolhido NOT_VALIDATED. Região e multi-região não são a mesma localização por presunção. |
| Run → R2 | R2 storage/ops + **egress da origem GCP** + leituras de volta + proxy/Workers, se usados. | Saída gratuita de R2 não torna upload GCP→R2 gratuito. Rota/bytes e SKU C13 UNKNOWN; unidades GB/GiB devem ser convertidas explicitamente. |
| GCS → R2/S3, exemplo | Transferir 2 GiB numa rota elegível de C2 a USD 0.12/GiB adiciona USD 0.24 só de saída GCS. | Não reutilizar esse SKU para a saída de Cloud Run, que usa rede Premium C13. |
| S3 | Storage por classe/região + requests + versões + replicação + saída AWS + saída da origem GCP. | Todas as tarifas selecionadas e o consumo UNKNOWN; comparar após obter a tabela oficial aplicável, sem importar preços históricos. |
| Jobs → worker VM | Comparar `Σ tentativas × custo Job` com `horas VM × tarifa + discos/IP/backup/operação`, inclusive idle e atualizações de segurança. | Família/região/utilização/serviço Auth escolhidos UNKNOWN. Sem break-even monetário comprovado. |
| Tasks → polling | Economiza operações Tasks/IAM, mas adiciona tempo de processo, consultas DB e capacidade para sweep. | Não inferir custo zero de uma alternativa sem fila; medir ambos sob o mesmo envelope. |

GCS com versioning e soft-delete de sete dias, como na proposta histórica, exige
inventário de versões não correntes e soft-deleted. Deletar a versão viva pode
não liberar bytes faturáveis; desabilitar novos uploads também não remove o
storage existente. Reconciliar recibos e gerações antes de qualquer remoção.
Uploads incertos mantêm cota e objeto; não converter orçamento em permissão
para refund ou GC destrutivo. Retention/backup de evidências também entra na
cauda, incluindo eventual armazenamento de exportação fora do projeto. Fontes:
[soft delete](https://docs.cloud.google.com/storage/docs/soft-delete) e
[Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning),
consultadas em 2026-11-07; esses mecanismos não foram acionados.

## 8. Aprovação, sinais de stop e fatura tardia

**USD 10 é apenas teto proposto**, não crédito, verba ou gasto autorizado.
Pedir aprovação separada de todos os provedores, custos fixos, retenção e cauda
antes de P1. Sem preço/volume limitado de qualquer termo relevante, não iniciar.
Se e somente se aprovados esses valores e escopo: alertas em 50/90/100%, stop
de novas admissões em **USD 5 medidos ou USD 8 previstos**, janela total de até
duas horas; parar antes se limites, contadores ou segurança forem incertos.

A proposta usa **alerts-only budgets**, que notificam e não cortam consumo.
A documentação atual também descreve spend-cap budgets para serviços elegíveis;
elegibilidade/cobertura desse stack é NOT_VALIDATED e nenhum cap foi configurado.
Não tratar alerta, Supabase Spend Cap ou cancelamento solicitado como prova de
interrupção de todas as cobranças. Créditos/descontos podem mascarar consumo:
acompanhar bruto, líquido e saldo separadamente. Fonte: [C14](https://docs.cloud.google.com/billing/docs/how-to/budgets).

Stop imediato por faturamento desconhecido, múltiplos launches físicos não
explicados, divergência objeto/recibo, bypass de auth/tenant ou Jobs sem fencing.
Após parar admissões, drenar/cancelar execuções conhecidas, reconciliar incerteza,
reter ledger e objetos autoritativos, inventariar recursos e versões, verificar
fatura tardia até sua estabilização e atribuir owner explícito para qualquer
resíduo. Essa política é um plano futuro, não uma automação implementada.

## 9. Reprodução da aritmética — sem chamadas cloud

Este bloco usa apenas Python stdlib `Decimal`. Executá-lo é validação estática,
não simulação de performance nem previsão aprovada. Saída esperada abaixo:

```python
from decimal import Decimal as D, ROUND_CEILING

def job(seconds, cpu=1, gib=2):
    t = max(D(60), (D(seconds) * 10).to_integral_value(rounding=ROUND_CEILING) / 10)
    return t * (D(cpu) * D("0.000018") + D(gib) * D("0.000002"))

rows = {
    "one_min_60s": job(60),
    "one_min_120s": job(120),
    "five_min_300s": job(300),
    "five_min_600s_sensitivity": job(600),
    "five_min_1800s_sensitivity": job(1800),
    "initial_jobs_4x300": 4 * job(300),
    "api_controller_busy_2h": 2 * D(7200) * (D("0.000024") + D("0.5") * D("0.0000025")),
    "api_requests_1000": D(1000) / 1_000_000 * D("0.40"),
    "tasks_units_1000": D(1000) / 1_000_000 * D("0.40"),
    "gcs_A25000_B25000": D(25000) / 1000 * (D("0.005") + D("0.0004")),
    "gcs_2GiB_8days": 2 * 8 * 24 * D("0.000027397"),
    "logs_50MiB": D(50) / 1024 * D("0.50"),
    "secrets_10versions_2h_10000accesses": 10 * 2 * D("0.000082192") + D("0.03"),
    "registry_1GiB_2h": 2 * D("0.000136986"),
    "one_slot_1CPU_2GiB_2h": job(7200),
    "two_slots_2CPU_4GiB_2h_sensitivity": 2 * job(7200, 2, 4),
}
subtotal = sum(rows[k] for k in (
    "initial_jobs_4x300", "api_controller_busy_2h", "api_requests_1000",
    "tasks_units_1000", "gcs_A25000_B25000", "gcs_2GiB_8days", "logs_50MiB",
    "secrets_10versions_2h_10000accesses", "registry_1GiB_2h"))
assert subtotal == D("0.59265232250")
assert job(30) == job(60) == D("0.001320")
assert rows["initial_jobs_4x300"] == D("0.026400")
assert rows["two_slots_2CPU_4GiB_2h_sensitivity"] == D("0.633600")
print("known_subtotal_USD", subtotal)
print("all_in_USD UNKNOWN; cloud NOT_VALIDATED; spend BLOCKED")
```

```text
known_subtotal_USD 0.59265232250
all_in_USD UNKNOWN; cloud NOT_VALIDATED; spend BLOCKED
```

## 10. Evidência da consulta pública

Páginas HTTP 200 capturadas com Python `urllib.request` sem credenciais em
2026-11-07, entre **18:43:44 e 18:44:11 UTC**. Os hashes identificam os bytes HTML
observados em arquivos temporários de pesquisa, não um arquivo de preços
assinado nem conteúdo imutável. Páginas dinâmicas podem mudar seus hashes sem
mudar tarifas; verificar os valores novamente antes da autorização. Não é
necessário conservar/republicar páginas inteiras de terceiros no repositório.

| Fonte | SHA-256 da resposta HTML observada |
| --- | --- |
| C1 | `503c8933172472dae9dd22da877ec910dfba8bd16061d4a105b00f0bb030e58c` |
| C2 | `f79b0b2cbc2b6fb0aad3688daa77a61a10acb1f96ee02f5e1efdaf6109c34122` |
| C3 | `38cc930414e1f84c9576be89b69060d85272999a19f160ca79514ed101593b9c` |
| C4 | `6cad08ad1e642f3b0a7820e5588bb7bbcc3ac89fd29b02f04be77967b892b805` |
| C5 | `c4386ed9d6d880fa3c4369f3dcd572126d7570a7623b572f810f34c82e3da84a` |
| C6 | `1a56969b21601ee360246bb19e1ee3f83aa877c1f8d530184ce59570e8166c7e` |
| C7 | `760733dd1a6f45ffe9c3b42bbcb28f6e92e18966aea66ed940f12e0200f88d5e` |
| C8 | `d030b3bd0a0cdc7caa4275ffd8a04b9ab73f0cf1571cd4a824deacbc5df05c66` |
| C9 | `0108ad6c31d5574ae724532a46181df2be7a6196634a9e2f6c51305a8fb7fd95` |
| C10 | `c7d3ecdceb112be01807ff5adc5269e0115b5a2398d9530fd7361153d644e620` |
| C11 | `9395f37087a8a889cf7d6f77c9fbd9caca9507f922be3454760e3a338ce4c505` |
| C12 | `13b7f86868ff15672ffeaa1d9f2b956066088627ea76285417176af5293f96a0` |

Para reproduzir uma consulta pública de C1, sem ferramentas de infraestrutura:

```sh
python3 - <<'PY'
import hashlib
from urllib.request import Request, urlopen
from datetime import datetime, timezone
url = "https://cloud.google.com/run/pricing"
with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
    payload = r.read(8 * 1024 * 1024)
    print(r.status, r.url, len(payload), hashlib.sha256(payload).hexdigest())
print(datetime.now(timezone.utc).isoformat())
PY
```

Consultas C13/C14 foram lidas pelo navegador de pesquisa oficial; sem captura
HTML local correspondente, não se atribui hash inexistente. Aritmética,
integridade Git e links são verificáveis localmente; comportamento cloud,
faturamento real e condições de conta continuam NOT_VALIDATED/UNKNOWN.
