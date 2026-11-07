# G3B-P0 — decisão de readiness para G3B-P1

**Data: 2026-11-07 UTC. Decisão: NO GO para avançar agora à autorização
executável de G3B-P1.** O preflight documental pode seguir para revisão humana;
ele não autoriza criação de recursos, contratação, credenciais, merge ou deploy.
Cloud permanece **NOT_VALIDATED**, gasto real e custo total **UNKNOWN**.

Esta decisão atende à [Issue #10](https://github.com/alterio-png/let-them-cook-content-engine/issues/10)
sem executar o staging. O main observado continua
`7a271382487ac4d16e278e5ccb8c345279b3cc16`; o [PR #9](https://github.com/alterio-png/let-them-cook-content-engine/pull/9)
permanece **OPEN / DRAFT**, HEAD
`e40357d08fa3c0b258b63c0d3a811cbab2b23206`, sem merge e sem aprovação humana
registrada na consulta desta auditoria. Não há autoridade compartilhada G3A.1
integrada ao main. Se qualquer referência mudar, revisar o delta antes de
reutilizar este parecer.

## Evidência disponível e o que ela permite concluir

| Item | Resultado e limite |
| --- | --- |
| Base e preservação | **PASS** na leitura de Git: 114 arquivos do main idênticos aos mesmos arquivos no HEAD do PR #9; proposta com 149 arquivos totais. Esta branch documental preserva o main e adiciona os seis documentos listados abaixo. Comandos, hashes e saída estática estão no preflight. |
| CI já arquivado do PR #9 | **PASS local**: cinco workflows no HEAD exato, artefato `13580575204` com origem limpa e 276 testes sem skips. A auditoria conferiu os bytes arquivados; não reexecutou esses testes como prova cloud. |
| Autoridade compartilhada proposta | **PASS local/documental** para reserva/cota/recibo, membership, RLS/grants, locking, deadlines, lease/fence e recuperação conservadora. Revisão humana independente e integração permanecem **BLOCKED**. |
| Concorrência arquivada | Um grant/cobrança sob 100 duplicatas; 20 chaves distintas respeitam dez vagas. No runner de dois CPUs, 100 operações exigiram 305 tentativas físicas, incluindo **205 respostas 503 recuperadas**. É prova de consistência com backpressure, não capacidade sustentada. |
| Preços públicos | **PASS para pesquisa datada de tarifas**, com URLs e hipóteses no modelo; não é cotação para uma conta/região escolhida nem fatura. Créditos e saldo de franquias não são presumidos. |
| Arquitetura e ensaio futuro | **PASS documental** para distinguir contratos existentes, lacunas e alternativas. Imagens, conectividade, IAM, sessões hospedadas, storage e launch real são **NOT_VALIDATED**. |
| CI da entrega P0 | A entrega deve apontar SHA final e os quatro workflows herdados do main no PR DRAFT. Esses checks preservam regressões locais; não são um quinto workflow G3A.1 integrado ou aprovação de P1. |
| Creative OS | **PASS para preservação/read-only**: main consultado em `1a30934928a767fb851cd1d846318d6c60660394`; nenhum arquivo ou referência desse repositório foi alterado. |

O [preflight](G3B_P0_PREFLIGHT.md) identifica cada arquivo, SHA-256, commit e
membro de artefato usado. O relatório local de 144 tentativas e o relatório CI
de 305 tentativas são coortes diferentes; não trocar seus números ou ambientes.

## Bloqueios que impedem uma autorização executável

Os responsáveis abaixo são funções **a designar**, não pessoas já aprovadas.

| Gate | Estado atual | Evidência necessária para reconsiderar | Owner pendente |
| --- | --- | --- | --- |
| Dependência G3A.1 | **BLOCKED** | Revisão humana independente do PR #9, findings encerrados, autorização e merge controlados; cinco workflows aprovados no novo main/SHA. A issue e este PR não fazem esse merge. | Revisor independente e mantenedor |
| Autoridade financeira/operacional | **UNKNOWN / BLOCKED** | Conta, projeto isolado, região, residency, plano Auth/DB, operador/kill/teardown e assinatura de autorização separados; pior caso completo dentro do teto com margem. | Autorizador humano, finanças, operações e dados |
| Custo completo | **UNKNOWN** | Resolver todos os termos não precificados para a configuração escolhida: baseline, Auth/DB, backups, redes/egress, logs, imagens, requests/retries, retenção e cobrança tardia. USD 10 é somente proposta; nova assinatura Pro de USD 25/mês não cabe nela. | Owner financeiro/técnico |
| Runtime e ingestão | **BLOCKED** | Implementação/revisão separada de listener cloud/PORT, configuração/segredos, scheduler/sweep e ponte explícita de recibo `uploaded_pending_validation` para asset G2 verificado. Um helper executado manualmente não fecha o pipeline HTTP completo. | Backend/plataforma |
| Identidade, rede e banco | **NOT_VALIDATED / BLOCKED** | Provar Google IAM e JWT do usuário como identidades separadas, sessão hospedada/logout, RLS/papéis efetivos, TLS/hostname, pooling e conectividade. Rever o papel interno amplo `engine_worker` e permissões de list/delete antes de atribuir autoridade de cleanup. | Segurança/banco/plataforma |
| Capacidade e retry | **NOT_VALIDATED / BLOCKED** | Aprovar/enforçar orçamento agregado proposto de **13 conexões mais Auth/plataforma UNKNOWN**, backpressure e retries; capacidade real deve incluir cada pool e overhead. Demonstrar os limites cumulativos de ensaio e a política de entrega Tasks, sem inferir teto de cinco callbacks. | Banco/plataforma/operações |
| Storage e recuperação | **NOT_VALIDATED** | Private IAM, create-only e geração condicional, hash/bytes reais, interrupção/resposta perdida, retenção/versions/soft-delete, restore e reconciliação sem reembolso de upload incerto. | Dados/storage/segurança |
| Launch físico | **NOT_VALIDATED / BLOCKED** | Um slot global retido na incerteza, máximo cumulativo de quatro execuções e encerramento físico comprovado antes de liberar slot. Sem bypass direto por console, IAM, scheduler ou sweep; callback duplicado nunca autoriza `jobs.run` cego. | Plataforma/worker |
| Artefatos e parser nativo | **BLOCKED** | Imagens com digest, proveniência, assinatura/enforcement, SBOM e revisão de vulnerabilidades/licenças/FFmpeg GPL/codecs; limites/supervisão e dados somente sintéticos. Nada foi construído, distribuído ou aprovado pelo P0. | Release/segurança/legal |
| Workload 1/5 minutos | **NOT_VALIDATED / BLOCKED** | Bytes reais, SRT/áudio e três formatos dentro dos caps; HTTP/upload de 1 MiB, orçamento de upload de 30 s, job aplicativo de 120 s e entrega HTTP de 32 MiB não são ampliados pela proposta de provider de 300 s. Medir viabilidade antes de qualquer aumento revisado/autorizado. | Backend/QA/plataforma |
| Operação e encerramento | **NOT_VALIDATED / BLOCKED** | Inventário pré/pós, stop imediato e ensaio de kill/terminal proof, retenção/backup coerentes, teardown revisado e reconciliação de recursos/cobranças atrasadas. | Operador/teardown a designar |

Os testes de cloud precisam acontecer depois da autorização específica, dentro
de um plano previamente implementável, revisado e limitado. Resolver um gate
documental não o transforma em teste remoto PASS. Um gate sem prova operacional
necessária deve constar explicitamente na autorização como ensaio a executar,
com condição de parada, e nunca como funcionalidade já validada.

## Envelope proposto e ressalvas financeiras

A proposta comum aos seis documentos é serial: uma instância/requisição de API,
uma de controlador e **um slot físico global**; um task/parallelism por Job,
**zero retries da plataforma**, 1 vCPU/2 GiB e timeout provider de 300 s. O
deadline aplicativo continua 120 s. Planejam-se **três jobs lógicos e no máximo
quatro execuções físicas no ensaio inteiro**: os casos sintéticos de 1/5 minutos
e um job separado de falha com retomada. Essa contagem cumulativa é requisito
futuro, não mecanismo já implementado. Janela máxima proposta: duas horas,
incluindo parada e reconciliação; aumentar recursos, tentativas ou tempo exige
nova avaliação/autorização.

O orçamento de callbacks proposto também depende de controle próprio. A
[referência oficial RetryConfig](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig),
consultada em 2026-11-07, exige as condições de tentativas **e** duração para
encerrar retries. A proposta histórica `max_attempts=5` com duração 300 s não
prova máximo de cinco entregas. Registrar uma correção futura de contrato/
configuração e custo, sem reescrever a história G3A nesta branch.

**USD 10 não é crédito nem orçamento aprovado.** Somente se aprovados, alertas
em 50/90/100% e stop em USD 5 medidos ou USD 8 previstos poderão compor a
política. São alertas do desenho histórico, não hard caps. A documentação atual
também descreve spend caps opcionais para serviços elegíveis; elegibilidade,
cobertura e comportamento aqui são **NOT_VALIDATED** e não são assumidos.
Dados de billing chegam com atraso, inclusive após teardown. Termo de custo
UNKNOWN, previsão sem margem ou contabilização incompleta interrompem admissão.
[Budgets oficiais](https://docs.cloud.google.com/billing/docs/how-to/budgets).

O [modelo de custos](G3B_P0_COST_MODEL.md) usa tarifas publicadas e cenários
hipotéticos, incluindo subscrição mínima, arredondamentos e retenção; não
confunde duração da mídia, tempo de CPU local e segundos alocados faturáveis.
Uma nova assinatura [Supabase Pro](https://supabase.com/pricing) começa em
USD 25/mês na consulta de 2026-11-07. Uma conta existente, plano gratuito,
franquia ou crédito não verificados não podem zerar esse termo para aprovar o
teto de USD 10. Valores parciais de compute baixos não demonstram custo total.

## Condições de parada e preservação

Interromper imediatamente o futuro ensaio em bypass de Auth/tenant, cobrança
inexplicada, launch físico duplicado, divergência de objeto/recibo/hash/generation
ou múltiplos Jobs sem fencing/admissão global. Primeiro fechar admissão e
preservar evidência; drenar/cancelar escritores identificados e reconciliar
terminalidade. Um job SQL concluído, timeout ou operação `done` não basta para
liberar capacidade física desconhecida.

Não apagar objetos autoritativos/incertos, reembolsar cota por mera ausência,
derrubar ledger ou voltar organização ativa para SQLite vazio. Manter recibos,
owners históricos, tombstones, chaves e generation coerentes durante rollback.
Não interpretar apagar um projeto como procedimento seguro de reconciliação.

## Entrega P0 e próxima decisão

Os seis documentos são:

1. [Preflight e evidências](G3B_P0_PREFLIGHT.md).
2. [Arquitetura e alternativas](G3B_P0_ARCHITECTURE.md).
3. [Modelo de custos oficiais](G3B_P0_COST_MODEL.md).
4. [Gates de segurança](G3B_P0_SECURITY_GATES.md).
5. [Runbook futuro de staging](G3B_P0_STAGING_RUNBOOK.md).
6. Este registro de decisão.

O PR permanece DRAFT e exclusivamente documental, sobre o main confirmado.
Checks estáticos verificam somente seis adições Markdown, 114 arquivos
históricos intactos, links e aritmética reproduzível. A entrega registra no PR
o SHA final e os checks herdados efetivamente concluídos. Nenhum resultado de
CI documental valida cloud ou autoriza merge/recursos.

Reavaliar readiness somente após fechar revisão/integração, owners, desenho
executável e lacunas de implementação, custo completo e autorização separada.
Um novo parecer poderá recomendar a **autorização limitada de ensaios P1**;
esse parecer ainda não será aprovação de produção, clientes, IA, publicação
social, operação paga contínua ou escala aberta.
