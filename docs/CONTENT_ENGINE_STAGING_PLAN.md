# Content Engine — próximo staging privado

Plano documental de 2026-11-08. **G3B-P1: NO GO; REMOTE_STAGING:
NOT_VALIDATED; custo total: UNKNOWN.** Este plano não executa nem autoriza
provisionamento, contratação, credenciais remotas ou publicação de backend.
O preview visual público contém somente demonstração e evidências sintéticas;
ele não é o staging descrito aqui.

## Ponto de partida e mudança desde o P0

- Main confirmado: `477c7c922a8ceb0dbb1b7dca98e4b1da12fba149`.
- PR #16: DRAFT, HEAD `5d6e3da15b88edde79b67d71c089656cfa222cda`.
- O R0 implementa login/logout GoTrue local, reserva/recibo PostgreSQL,
  ingestão interna de par sintético aprovado, job/outbox, worker FFmpeg e
  downloads privados com hash. Sua API aceita somente loopback e sua
  segurança não foi validada como serviço remoto.
- A Issue #17 registra aprovação audiovisual e relato do fluxo pelo Owner;
  `LOCAL_MANUAL_SMOKE=PARTIALLY_DOCUMENTED`. Não converter esse relato em
  smoke completo, aprovação de merge ou autorização cloud.
- BP-02/03/04/05 continuam parcialmente abertos. Os 307 testes e o E2E local
  arquivados não estabelecem capacidade, custo, sessão hospedada ou IAM remoto.

Este plano complementa [arquitetura R0](G3BR0_DESIGN.md),
[decisão P0](G3B_P0_DECISION.md), [segurança P0](G3B_P0_SECURITY_GATES.md),
[custos P0](G3B_P0_COST_MODEL.md) e [runbook P0](G3B_P0_STAGING_RUNBOOK.md).
Os snapshots históricos permanecem preservados. A ponte de ingestão existe
agora **localmente**; a antiga lacuna não pode ser repetida como ausência de
código, nem considerada validada em cloud por existir no R0.

## Sequência proposta

| Etapa | Entrega revisável antes da execução remota | Critério de saída |
| --- | --- | --- |
| 1. Consolidar o local | Fechar os campos reais do smoke na Issue #17; revisar findings e o delta do PR #16 | Relatório humano completo e decisão de merge separada, sem merge automático |
| 2. Selecionar o desenho privado | ADR comparando acesso restrito a laboratório existente e staging gerenciado isolado; inventário de serviços, identidades, dados e custos | Uma opção implementável escolhida, sem abrir o listener loopback à internet |
| 3. Preparar os adaptadores | PR separado para TLS/ingress, Auth remoto, storage, lançamento/reconciliação e configuração por referências a segredos | Revisão de segurança, testes negativos e CI do SHA exato; nenhum recurso contratado |
| 4. Quantificar e aprovar | Plano finito com conta/projeto/região, plano DB/Auth, responsáveis, preços atuais e pior caso completo | Autorização humana específica de infraestrutura, escopo e teto financeiro |
| 5. Ensaio privado | Login, upload sintético, ingestão, render, downloads e falhas sob limites medidos | Evidências por operação e relatório GO/NO GO somente para aquele staging |
| 6. Encerrar e reconciliar | Parar admissão, comprovar término físico, inventariar objetos/ledger e reconciliar cobrança posterior | Nenhum runtime esquecido; retenção incerta com responsável e prazo |

As etapas 1–4 podem gerar código e documentos para revisão. A execução das
etapas 5–6 depende da autorização futura, ainda ausente. O desenho e a revisão
podem revelar trabalho adicional; não selecionar serviços por conveniência do
preview.

## Fronteira privada e dados

Preferir uma única origem HTTPS para frontend autenticado e API, atrás de
controle de acesso por identidade de revisores. A identidade de acesso ao
ambiente não substitui a sessão do produto: cada operação continua verificando
usuário, sessão vigente e organização. O proxy não pode injetar uma credencial
administrativa compartilhada para simular usuários. Se houver origens distintas,
revisar CORS/CSRF, cookies/tokens, redirecionamentos e proteção de todos os
endpoints, inclusive saúde, callbacks e download.

Não apontar o preview público para um IP local, expor o servidor HTTP de
desenvolvimento por túnel público, habilitar signup aberto ou publicar senhas de
laboratório. Não colocar DSN, signing key, service-role, capability URL ou token
privilegiado em bundle, manifesto, vídeo, screenshot ou log. Tokens do produto
continuam fora de armazenamento persistente do navegador, salvo futuro desenho
explícito e revisado de sessão.

Usar contas não produtivas e apenas os pares sintéticos aprovados por hash.
Upload não pode aprovar sua própria proveniência. Mídia de clientes, IA e APIs
sociais ficam fora deste ensaio. Definir região, retenção, acesso a logs/backups,
rotação/revogação e recuperação antes da primeira requisição remota.

## Controles a implementar e provar

| Área | Evidência necessária no ambiente escolhido |
| --- | --- |
| Sessão e tenant | Negar sem token, token inválido/expirado/revogado, organização alheia e membership retirada; logout real bloqueia a reutilização do token e downloads |
| Banco | RLS e grants efetivos por principal; API sem DML de ingestão/proveniência; TLS/hostname, deadlines, pools e reservas totais medidos |
| Upload/ingestão | Caps existentes, objeto imutável e par sintético aprovado; replays usam a mesma chave; resposta perdida não cria outro asset nem reembolsa cota incerta |
| Worker | Imagem/digest/SBOM/licenças revisados; FFmpeg limitado; lease/fencing; processo antigo não publica; contagem física persiste através de retries e reinícios |
| Objetos/download | Storage privado, geração condicional, bytes/hash/readback; adulteração ou ausência bloqueiam entrega; URL vazada não contorna sessão/membership |
| Orquestração | Separar JWT do usuário e identidade do provedor; audiência/caller internos exatos; sinais duplicados e perda de resposta de launch não iniciam outra execução cega |
| Operação | Stop/kill com responsáveis; billing indisponível ou resultado incerto fecha admissão; logs sanitizados distinguem 401, 403, 429, 503 e falhas de dependência |
| Mobile | WebKit automatizado e Safari real no iPhone: login, upload, espera/retomada, três vídeos com áudio/SRT, downloads/Compartilhar, logout e acesso negado após logout |

Preservar os caps atuais: **1 MiB por upload, 120 s por job, 32 MiB por
download**. Aumentá-los requer PR, revisão e orçamento próprios. Começar pela
amostra existente de 2,3 s e resoluções smoke (270×480, 320×320 e 320×400).
O plano histórico de clipes de 1/5 minutos não cabe automaticamente nesses
limites e exige prova de viabilidade antes de ser agendado.

O R0 documenta **16 consumidores normais de conexões**, incluindo GoTrue (4),
API (6), worker/dispatcher/queue/heartbeat (3), ingester/heartbeat (2) e monitor
(1); conexões administrativas de setup/teste ficam fora desse orçamento. O P0 tinha uma proposta de
13 mais Auth/plataforma desconhecidos. São topologias diferentes: recalcular
todo o orçamento para a opção escolhida, incluindo ingester, pools, Auth,
replicas, manutenção e capacidade reservada. Não copiar um desses números como
limite remoto já validado.

## Envelope financeiro e operacional para decidir depois

A proposta histórica de USD 10 **não foi aprovada**. A pesquisa de preços de
2026-11-07 e seu subtotal parcial não são cotação atual ou custo all-in. Antes
de solicitar a autorização final, preencher:

| Campo | Estado atual | Exigência |
| --- | --- | --- |
| Conta, projeto, plano DB/Auth, região e moeda | UNKNOWN | Inventário e preço verificados; sem assumir créditos/free tier ou capacidade já paga |
| Custo fixo e mínimo de contratação | UNKNOWN | Incluir assinatura, banco, backup, imagem/build, logs e baseline, mesmo sem renders |
| Custo variável e residual | UNKNOWN | Compute alocado, requests/retries, storage/versões, egress, retenção, impostos e cobrança atrasada |
| Teto aprovado e margem | AUSENTE | Valor all-in e reserva suficiente para pior caso, parada e retenção |
| Pessoas e janela | NÃO DESIGNADAS | Operador, suplente, segurança, billing e teardown com horário UTC e poderes limitados |
| Mecanismo de stop | NOT_VALIDATED | Admissão e lançamento fecham por prazo/contagem, independentemente de alertas de billing |

Como ponto de discussão, preservar o envelope serial histórico: uma execução
física global, três jobs lógicos e no máximo quatro execuções físicas no ensaio
inteiro, janela máxima de duas horas e zero retries da plataforma. Esses tetos
continuam **propostos**, precisam de implementação e não são renovados por
timeout, reinício, callback ou retry manual. Um resultado de launch desconhecido
retém o slot até prova de terminalidade.

Se houver Cloud Tasks, `maxAttempts=5` e duração de retry não provam um limite
de cinco callbacks. Implementar contagem cumulativa e stop próprios, incluir
tentativas em voo e negar o bypass de despacho manual. Alertas financeiros
não são hard caps. Reduzir instâncias não limita sozinho o gasto total. Um
termo de custo sem estimativa limitada impede iniciar o ensaio.

## Relatório e próxima decisão

O relatório remoto deve identificar SHA/imagens, operador, horários, navegador,
recursos, permissões efetivas, jobs/operações, gerações, hashes, resultados dos
testes negativos e término físico, sem segredos. Registrar o gasto observado
separadamente do estimado e reconciliar novamente após atraso de billing.

Falha de isolamento, exposição de dados/segredo, launch duplicado, divergência
de hash/ledger, orçamento incerto ou incapacidade de parar exigem fechar
admissão e preservar evidência. Não apagar objeto incerto, liberar slot ou
reembolsar cota apenas porque houve timeout. Retenção e cleanup precisam de
inventário e autorização próprios.

Uma eventual aprovação desse ensaio não aprova produção, clientes, longos
vídeos, IA, publicação social, escala, contratação contínua ou merge. O próximo
gate executável só pode mudar após a revisão e a autorização correspondentes;
o preview público pode evoluir sem promover o backend a cloud-ready.
