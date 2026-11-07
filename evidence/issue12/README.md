# Evidências da Issue #12

Fonte examinada: PR #9, `e40357d08fa3c0b258b63c0d3a811cbab2b23206`.
Base: main `7a271382487ac4d16e278e5ccb8c345279b3cc16`.
Todos os dados são sintéticos e laboratórios usam somente loopback/Docker local.
Nenhuma fonte de produto foi modificada. Relatório consolidado:
[G3A1_PR9_INTEGRATION_REVIEW.md](../../docs/G3A1_PR9_INTEGRATION_REVIEW.md).

## Inventário

- `full-276.txt`, `full-run/`: reprodução nova da suíte inteira, Auth e benchmarks.
- `iac.txt`: validação histórica local de Terraform/provider, sem plan/apply.
- `integrity-114.txt`, `provenance.json`: integridade, ambiente e limites da prova.
- `sql-review/`: scripts originais, 36 checks, catálogo/RLS/ACL e logs reais.
- `backend-review/`: scripts/probes, falhas/deadlines, SIGKILL e contrato de objeto.
- `http-review/`: script/runner original e logs de GoTrue real/HTTP/revogação.
- `backpressure-review/`: reanálise do CI **arquivado**, não carga nova.
- `final-qa/`: reconferência independente de contagens, boundaries e findings.

Um manifesto relativo único cobre todos os arquivos entregues, inclusive fontes
dos probes e ZIP arquivado. Os manifests antigos dos revisores usavam caminhos
absolutos e foram substituídos, na entrega, por esse inventário completo.
Verificar a partir da raiz do repositório:

```sh
sha256sum -c evidence/issue12/SHA256SUMS.txt
```

Não há JWT, chave privada ou segredo temporário de GoTrue nos logs entregues.
DSNs constantes em scripts SQL são **fixtures públicas descartáveis locais**,
com bancos/usuários de teste e portas 55611/55612; não são credenciais hospedadas.
Segredos reais de cada laboratório GoTrue são gerados no momento e removidos.
O changelog público completo não é republicado: a leitura está descrita no
relatório HTTP, sem transformar documentação de provedor em prova operacional.

## Reproduzir a suíte principal em clone limpo

Pré-requisitos: Linux, Docker local, Python 3.12 e FFmpeg/ffprobe com os codecs
históricos. Instalar somente o lock já revisado; nenhuma dependência nova foi
adicionada por esta auditoria. Escolher portas locais livres. Exemplo:

```sh
git clone https://github.com/alterio-png/let-them-cook-content-engine.git /tmp/issue12-pr9
git -C /tmp/issue12-pr9 checkout --detach e40357d08fa3c0b258b63c0d3a811cbab2b23206
python3.12 -m venv /tmp/issue12-venv
/tmp/issue12-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r /tmp/issue12-pr9/requirements-g3a.lock
cd /tmp/issue12-pr9
git status --porcelain
G3A_PORT=55601 G3A1_PORT=55602 \
 G3A1_PYTHON=/tmp/issue12-venv/bin/python \
 G3A1_EVIDENCE_DIR=/tmp/issue12-new-evidence \
 bash scripts/run_g3a1_local.sh
sh infra/g3a/validate_toolchain.sh
git status --porcelain
```

O runner aplica migrações em bancos novos e remove seus containers; não apontar
para banco existente ou remoto. O script IaC histórico baixa apenas ferramentas
revisadas e usa backend desabilitado para init/validate, sem plan/apply.
Saída deve ter 12+36+49+118+10+44+7 testes, nenhum skip, sete `OK`, benchmark
`PASS_LOCAL_ONLY`, SHA correto e árvore limpa. Latências e número de 503 podem
variar: comparar invariantes e tentativas físicas/lógicas, sem prometer repetir
o número 40 ou 205 em outro host. O runner só prova localmente o contrato.

## Reproduzir os diagnósticos originais

Os scripts externos foram preservados **exatamente como executados**, com
caminhos originais `/workspace/issue12-pr9-clean`, `/workspace/issue12-evidence`
e `/workspace/g3a-venv/bin/python`. Não são apresentados como ferramentas já
portáveis para qualquer diretório. Cada `REVIEW.md` registra comandos, banco,
porta, preparação, credenciais de fixture, contagens e cleanup específicos.

Para replay exato em laboratório descartável com `/workspace` disponível:

1. Criar um clone limpo do SHA examinado em `/workspace/issue12-pr9-clean` e o
   venv hash-locked em `/workspace/g3a-venv` (ou ajustar os comandos explicitamente).
2. Copiar **uma cópia de trabalho** deste pacote para `/workspace/issue12-evidence`.
   Preservar a cópia arquivada/manifesto, pois probes gravam novos resultados.
3. Para SQL/backend, iniciar somente o PostgreSQL pinado do respectivo relatório,
   na porta/banco de fixture indicados, e aplicar `prepare_g3a1_database.py`.
   Nunca usar DSN de produção. Executar os comandos daquele relatório e remover
   somente seu container ao final. A suíte principal usa bancos diferentes.
4. O runner HTTP externo reutiliza o runner oficial local do SHA examinado,
   mudando só diretório e invocation do probe; inicia seu próprio GoTrue/PG,
   gera segredos e limpa os recursos por trap. Não precisa nem usa Auth hospedado.
5. Para reanálise de backpressure, colocar a cópia do ZIP entregue em
   `/workspace/g3a1-evidence/final-head-ci.zip`. O script lê os eventos já
   extraídos no subdiretório `backpressure-review/archived-ci/` e escreve um
   JSON novo, não executa carga ou chamadas ao provedor.

Se outros caminhos/portas forem necessários, alterar somente uma cópia de
trabalho dos probes/runners e registrar substituições e novo hash. Não editar
a fonte examinada nem afirmar que o hash do script modificado é o do original.
O replay de probe que escreve resultados deve usar diretório de saída novo.

O ZIP original arquivado, também disponível no
[artefato GitHub 13580575204](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42387140558/artifacts/13580575204),
está em `backpressure-review/archived-ci.zip`, SHA-256
`dd8464d5ada9c45eb46c24518daad9077522477a76bfa0cd7b557a4d53fb52da`.
É o CI antigo de dois CPUs: 305 tentativas/205 respostas 503. O benchmark
**novo** desta auditoria está em `full-run/shared-benchmark/` e produziu
140 tentativas/40 respostas 503. Não misturar esses dois registros.
