# AGENTS.md

Notas operacionais para agentes trabalhando neste projeto. O **comportamento**
(persona DBA Master) esta em `.devin/rules/dba-master.md` e `dba_master_prompt.txt`;
os **comandos** do framework estao na skill `.devin/skills/framework-troubleshoot/`.
Este arquivo guarda so o que nao esta nesses dois lugares.

## Shell: o padrao do agente e Git Bash, nao PowerShell

O projeto e todo Windows (`.bat`, workflows em PowerShell 5.1), mas a ferramenta
de exec do agente roda **bash por padrao**. Nele as barras invertidas somem:

```
.\.venv\Scripts\python.exe AMBIENTES.py
-> /usr/bin/bash: ..venvScriptspython.exe: command not found
```

Duas formas que funcionam (ambas verificadas em 23/09/2026):

- **bash com barras normais** — `./.venv/Scripts/python.exe AMBIENTES.py`
- **`shell_flavor: "powershell"`** — ai o `.\healthcheck.bat DBSRV600` da skill
  vale literalmente, como esta escrito na documentacao.

Use sempre `workdir` = raiz do projeto (a pasta com `COLETAR.py`), nunca um `cd`
solto: chamadas one-shot nao preservam diretorio entre si.

## O que o agente pode e nao pode executar

Regra de seguranca de 02/08/2026: a senha do banco e digitada pelo humano a cada
execucao — nao ha `--password`, arquivo, nem `ORA_PASSWORD`.

**Nao tente rodar** (conectam no banco): `COLETAR.py`, `healthcheck.bat`,
`redija.bat` quando recoleta, `DETALHAR_SQL.py`, `JANELA.py`, `TESTE_HOMOLOG.py`,
`VALIDAR_KB.py --conn`.

O perigo nao e falhar, e **travar**: o guarda `senha.ha_como_perguntar()` usa
`isatty()`, que devolve `True` numa sessao de agente com PTY. O processo para num
prompt invisivel em vez de errar rapido (ja aconteceu em 18/09/2026). Monte o
comando e peca ao usuario que rode, ou sugira `.\painel.bat`.

**Pode rodar a vontade** (offline): `TROUBLESHOOT.py`, `CASO.py`, `AMBIENTES.py`,
`HISTORICO.py`, `TREINAMENTO.py`, `MELHORIA.py`, `VALIDAR_KB.py` sem `--conn`.

## Verificacao

- `.\verificar.bat` — checa se a maquina esta pronta (`troubleshoot.doctor`),
  nao acessa banco. Aceita `.venv\` ou `python\` (pacote portatil).
- `./.venv/Scripts/python.exe -m pytest tests/ -q` — suite offline (~70 arquivos).
- `./.venv/Scripts/python.exe VALIDAR_KB.py` — contrato da KB (risco declarado,
  read-only primeiro, "o que olhar" em todo passo, armadilha em passo destrutivo).
- `./.venv/Scripts/python.exe MELHORIA.py --corpus` — reanalisa evidencia ja
  gravada e acusa se uma mudanca de codigo alterou o diagnostico de caso entregue.

## `troubleshooting/` tem duas coisas, e nenhuma e entulho

A pasta guarda **investigacao** e **foto de coleta** com a mesma cara de pasta.
Desde 25/09/2026 o `--listar`, o Fim do dia e o painel mostram so investigacao
(`caso.e_troubleshooting`: tem hipotese/acao registrada, ou hostname digitado por
humano). `--todos` volta a ver tudo. Nada foi apagado — so deixou de ser contado.

Duas pastas parecem descartaveis e **nao sao**:

- **`DEMO/`** — o UNICO caso versionado (`.gitignore` libera so ele). Sustenta
  `TROUBLESHOOT.py DEMO --open`, documentado no README como relatorio de
  referencia, e o corpus de um clone limpo. Apagar quebra a suite: ha teste que
  compara os arquivos rastreados pelo git com o disco.
- **`teste/`** — apesar do nome, e coleta REAL de agosto (72 arquivos) com o
  unico roteiro de guia percorrido que existe gravado. E corpus do
  `MELHORIA.py --corpus`, que compara diagnostico contra ela.

Nao edite `meta.json` de caso para "limpar a vista": e evidencia datada, e o
corpus compara contra ela. Se o objetivo e sumir da lista, o filtro ja faz isso.

## Antes de perguntar ao usuario, consulte o inventario

`AMBIENTES.py <SERVIDOR>` ja responde criticidade, versao, RAC/CDB, host e as
notas do parque — offline. Dois detalhes que ele expoe e que mudam conclusao:

- **Criticidade `?`**: o nome nao revela se e producao. Pergunte antes de agir;
  nao assuma. Hoje ha 1 ambiente nessa situacao.
- **Hosts compartilhados** (`AMBIENTES.py --hosts`): ha host com **6 bancos** e
  outro com 2. Metrica de host (CPU, memoria, filesystem) e **comum** — nao
  atribua a um banco so, e lembre que encher o storage afeta os vizinhos.

## Convencoes do parque Brasil (nao chute, ja esta decidido)

Confirmado pelo usuario em 28/09/2026, durante o INC071535488. Vale para o
ambiente **Brasil** — Chile e Argentina (`*.cmp.cl.corp`) podem diferir.

- **O listener e sempre `LISTENER1529`, na porta 1529, e mora sempre no GRID
  home** — nunca o listener default do database home. Entao:
  `lsnrctl status` sem argumento nao acha nada util; use
  `lsnrctl status LISTENER1529`, e como usuario **`grid`** (o `listener.ora` fica
  em `<GRID_HOME>/network/admin/`, e so o grid escreve nele).
- **O inventario e chaveado pelo AMBIENTE (o PDB), nao pelo CDB nem pelo host.**
  Alerta de monitoracao costuma citar o CDB (`CDBPR088`) e o servidor
  (`dbjjpalbr03`), e nenhum dos dois acha nada no `AMBIENTES.py`. A hierarquia e
  `servidor > CDB > PDB`, e o nome do PDB e o que vale (`APPPR490` dentro de
  `cdbpr088` em `dbjjpalbr03`). Ha precedente na lista de conexoes:
  `APPPR172 -> APPPR172:1529/cdbpr293`. Ao registrar caso ou ambiente, use o PDB
  e descreva CDB/host nas notas — o contrario deixa o parque inconsistente.

  *(Identificadores trocados por equivalentes que seguem a convencao: o prefixo
  real dos PDB e o hostname sao marca local, e `tests/test_fonte_sem_marca.py`
  barra o commit. A licao nao depende dos nomes.)*

- **O catalogo do RMAN so resolve com `TNS_ADMIN` apontado para o diretorio do
  wallet** (`$ORACLE_HOME/network/admin_wallet`), nunca no `network/admin`
  default: o alias do catalogo existe so la. Sem o export, o `rman ... catalog`
  morre em `ORA-12154`/`RMAN-04004` e parece problema de catalogo quando e de
  resolucao de nome. No mesmo diretorio, `mkstore -listCredential` responde
  **qual usuario roda o backup** sem expor senha. Os aliases reais de catalogo,
  os hosts do repositorio de historico e os caminhos dos scripts estao nas notas
  do inventario (`AMBIENTES.py <AMBIENTE>`), que nao e versionado; o
  procedimento generico esta no playbook `rman_catalogo_wallet`.

## Patch out-of-place: o que ele deixa quebrado para tras

Padrao observado no INC071535488 (CHG003174286, 19.32.0.0.0). Os homes levam a
versao no nome (`database1932`, `grid1932`) e o home anterior e **removido** —
logo nao ha rollback rapido, nem com o que comparar configuracao. Arquivos que o
patch **nao** leva corretamente para o home novo, e que ja causaram incidente:

- **`listener.ora`** — entrada estatica `SID_DESC` continuou apontando para o
  `ORACLE_HOME` antigo (ja inexistente). Quebra o canal DMON-a-DMON do Data
  Guard (`ORA-12757` num lado, `ORA-16664`/`ORA-16607` no outro) **enquanto
  conexao de aplicacao segue normal**, porque essa usa registro dinamico. Pior:
  a automacao gerou `listener.ora.bak` com o conteudo ja errado.
  Corrigir o arquivo e dar `reload` **nao basta** — o DMON cacheia o destino;
  precisa reciclar `dg_broker_start` (FALSE/TRUE, `SCOPE=MEMORY`).
- **Estado do Data Guard** — `TRANSPORT-OFF`/`APPLY-OFF` sao passo planejado do
  patch e ficaram sem reversao. Nada monitora `SHOW CONFIGURATION`, entao o
  alerta so veio 2 dias depois, como GAP.
- **Catalogo RMAN** — `DBMS_RCVCAT`/`DBMS_RCVMAN` ficaram na versao anterior,
  sem `UPGRADE CATALOG`.

Em qualquer caso apos patch, confira esses tres antes de investigar mais fundo.

## Backup: a falha que voce ve raramente e o problema

Quatro casos na semana de 24-30/09/2026 (INC071379230, INC071477193,
INC071546009, INC071584140). Em **tres deles** o "backup falhando" era sintoma de
outra coisa, e a falha era **antiga** — de meses a anos — sem ninguem ver.

Antes de tratar o erro do log, responda nesta ordem:

1. **O banco ainda existe?** `DROP DATABASE` autorizado deixa o job rodando
   contra o vazio. Sintomas: `hc_<SID>.dat` com data parada, alert log minusculo,
   sem `spfile`/`init<SID>.ora`, `ORA-01078`/`LRM-00109` ao subir, e `Target
   ONLINE` com `State OFFLINE` no clusterware.
2. **Existe INFRAESTRUTURA de backup no servidor?** Um banco de 82 TB passou anos
   sem backup porque o agente nunca foi instalado. Checagem de uma linha:
   `ls /opt/tivoli/tsm/client/oracle/bin64/libobk.so`. No RMAN, o sinal e
   `SHOW ALL` inteiro em `# default` e **nenhum canal `SBT_TAPE`**.
3. **Ha quanto tempo falha?** Logs de **tamanho identico** semana apos semana
   denunciam a mesma falha repetida. E perceba a **ausencia** de log, que nao
   gera alerta: houve sete semanas sem execucao nenhuma num dos casos.
4. **O catalogo e a autoridade, nao o controlfile.** `control_file_record_keep_time`
   trunca o historico; o catalogo (`OPRCAT_RMAN`) nao. `LIST BACKUP` e
   `REPORT NEED BACKUP` la sao a fonte definitiva de "existe backup?".
5. **A recusa do script pode estar certa.** `cannot run a HOT backup on a
   NOARCHIVELOG database` e guarda deliberado: o defeito esta no cadastro do job,
   nao na execucao.

E a cascata que fecha o circulo, vista em dois casos: backup parado -> archive
sem drenagem -> destino enche -> banco cai ou alguem apaga archive na mao ->
Data Guard morre. Backup nao e so recuperabilidade; e o que **drena** archive.

## Data Guard: o que a evidencia diz, e o que ela NAO diz

Aprendido nos INC071379230 e INC071587619 (29-30/09/2026).

- **`TRANSF_GAP` igual a `APPLY_GAP`**, com `LAST_SEQ` = `APPLIED_SEQ` e
  `ARC_GAP` zero, significa problema de **TRANSPORTE**, nao de apply: o standby
  aplicou tudo o que recebeu. Descarta de saida toda a investigacao de MRP.
- **`v$archive_dest` com `STATUS=DEFERRED` e `ERROR` vazio** = desligado de
  proposito, nao falha. Erro preenchido nomeia a causa (`ORA-12541` listener,
  `ORA-16191` password file, `ORA-00257` espaco no destino).
- **O Broker reporta `SUCCESS` com o transporte desligado**, porque
  `TRANSPORT-OFF` e para ele o estado DESEJADO. Quem monitora
  `Configuration Status` nao ve nada — um gap de 79 h passou despercebido assim.
  Monitore **`Intended State`** contra o esperado.
- **`ENABLE DATABASE` so funciona conectado ao PRIMARY** (do standby
  desabilitado da `ORA-16688`). O `DISABLE` funciona dos dois lados — ou seja,
  depois de desabilitar, o membro perde a capacidade de se reabilitar.
- **Para medir apply no standby**: `v$dataguard_stats` e `v$managed_standby`.
  Consulta com `gv$archived_log ... applied='YES'` filtrando `dest_id=1` engana —
  no standby, `dest_id=1` e o arquivamento LOCAL dele, e a coluna `applied`
  demora a atualizar. Na mesma coleta, uma dizia "85 de atraso" e a outra "lag
  zero"; a segunda estava certa.
- **`open_mode = READ ONLY`** e diferente de **`READ ONLY WITH APPLY`**: o
  primeiro e standby aberto SEM apply. No Broker o equivalente e
  `Real Time Query: ON`.
- **`dba_feature_usage_statistics`** prova se Active Data Guard ja era usado
  (`Real-Time Query on Physical Standby`, com `detected_usages` e
  `last_usage_date`) — util para decidir se abrir em leitura e restaurar o
  estado original ou introduzir consumo novo de licenca.
- **MRP travado em `WAIT_FOR_GAP` nao obedece ao Broker.** `APPLY-OFF` retorna
  `Succeeded` e o processo continua de pe (`ORA-16765`); encerre por SQL com
  `ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL`.

## RECOVER STANDBY DATABASE FROM SERVICE: o que esperar

Medido no INC071379230 (CDB de 82 TB, 1.528 datafiles, 19c).

- Fases: restaura controlfile (segundos) -> cataloga as copias do standby
  (~8 min) -> percorre `x$kccfn` duas vezes, fase **silenciosa** sem log nem MB
  (~45 min, a mais enganosa) -> `switch to datafile copy` e renomeia redo logs ->
  recover incremental.
- **Gera volume enorme de alert log**: o controlfile vem do primary e o Oracle
  emite um `MUST_RENAME_THIS_DATAFILE` por datafile. Medido ~10 MB de log por
  datafile, com picos de **45 GB/h**. Dimensione `diagnostic_dest` antes.
- **Nao honrou `PARALLELISM 4`** — alocou um unico canal.
- **`standby_file_management` fica em `MANUAL` e nao volta sozinho** para `AUTO`.
- Duracao real: **2h06 para 82 TB**, muito abaixo da estimativa por volume,
  porque o incremental de 17 dias era pequeno. Medir e melhor que estimar.
- Pre-voo que valida tudo em segundos, sem escrever nada:
  `RESTORE DATAFILE 1 VALIDATE FROM SERVICE <alias>` — prova alias, listener,
  SID, password file e canal de servico.

## Remocao de arquivo em ASM: nunca por nome digitado

`asmcmd rm` foi o que destruiu um Data Guard (INC071365211). Quando for
inevitavel, o metodo que funcionou (96 GB de redo orfao removidos, zero erro):

1. Liste o ASM para arquivo (`asmcmd ls <dir> > /tmp/asm.txt`).
2. Extraia a lista do que PRESERVAR **do proprio banco** (`v$logfile`,
   `v$datafile`).
3. Gere a exclusao por DIFERENCA: `grep -v -F -x -f keep.txt asm.txt`.
4. Confira a aritmetica (total - preservar = excluir) **e** o cruzamento
   (`grep -F -x -f keep.txt del.txt` tem de vir VAZIO).
5. Execute em lotes pequenos, conferindo espaco e saude entre eles.

Armadilha real: nomes se repetem entre diskgroups com numeros de encarnacao
diferentes (`group_3.270.1243334505` num, `...507` no outro). Copiar comando de
um diskgroup para o outro apaga o arquivo errado.

## Operacao longa: proteja a sessao e o disco

- **`nohup` com `cmdfile` e `log`.** Queda de SSH mata o RMAN, e varios hosts do
  parque nao tem `tmux` nem `screen`.
- **Linha de base ANTES**, com limiar de parada **absoluto** acordado, e
  monitoramento durante. Sem base, "esta impactando?" vira opiniao.
- **Vigia de espaco automatico** quando a operacao dura horas e consome log:
  script sob `nohup`, PID gravado em arquivo, log em filesystem **diferente** do
  vigiado. E **pendencia obrigatoria de reversao ao final** — automacao de
  limpeza esquecida vira incidente meses depois.
- **Operacao irreversivel muda o calculo do paralelismo**: se nao ha rollback,
  encurtar a janela encurta o risco.

## Descomissionamento: o DROP e a parte facil

INC071584140. Um `DROP DATABASE` autorizado deixou para tras job de backup,
entrada no `/etc/oratab`, recurso e servicos no clusterware (com `Target ONLINE`,
ainda pedindo o banco de volta), arquivos em `dbs`, monitoramento do EM e
registro no catalogo RMAN — gerando alerta semanal de "falha de backup" por 15
semanas em um banco que nao existia.

Sintomas que identificam esse cenario antes de tentar consertar:
`hc_<SID>.dat` com data parada, alert log minusculo, ausencia de
`spfile`/`init<SID>.ora`, `ORA-01078`/`LRM-00109` ao subir, e `Target ONLINE` com
`State OFFLINE` no `crsctl stat res -t`. Confirme no alert log
(`Completed: DROP DATABASE`) antes de qualquer acao.

## Scripts do parque que transformam falha em silencio

`manage.ksh` (uid 996, 2021, distribuido pelo parque). Dois defeitos que ja
custaram diagnostico:

- `srvctl start database ... > /dev/null 2>&1` seguido de
  `echo "Issued database startup"` **incondicional** — esconde o erro real
  (escondeu um `ORA-01078`) e anuncia sucesso sem verificar.
- `return 1` dentro do laco de bancos: se o primeiro ja estiver no ar, **encerra
  a funcao** e os demais nunca sobem. Deveria ser `continue`.

Nao corrija localmente: e script distribuido, e a proxima redistribuicao
sobrescreve. Leve o achado a quem mantem. Para diagnosticar, chame `srvctl`
direto — ele mostra o erro que o script engole.
