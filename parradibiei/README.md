# Framework de Troubleshooting Oracle (DBA Master / Parra)

Ferramenta **on-demand, read-only, sem agente e license-aware** para
troubleshooting e RCA de bancos Oracle (standalone, RAC, Exadata, Data Guard,
GoldenGate). Entra no banco, coleta evidencias **somente leitura**, analisa e
gera **sempre o mesmo relatorio padronizado**: dashboard HTML + PPTX +
relatorio no Template Padrao DBA Master.

## O que ele faz

- **Coleta read-only** (`COLETAR.py`): `python-oracledb` em modo *thin* (sem
  Oracle Client), sessao `SET TRANSACTION READ ONLY`, so `SELECT`/`WITH`,
  identificada em `V$SESSION` como `MODULE=TROUBLESHOOT-RO`.
- **Deteccao de topologia**: descobre se e SINGLE/RAC/Exadata/DG/GoldenGate e
  **desliga os checks que nao se aplicam** ao ambiente.
- **Licenca/Pack**: detecta `CONTROL_MANAGEMENT_PACK_ACCESS` e pula ASH/AWR/ADDM
  /SQL Monitor quando nao ha Diagnostics/Tuning Pack (`--pack auto|T|D|N`).
- **Analise**: ~20 analisadores (ASH, top SQL, locks, espaco, I/O, RAC, Exadata,
  Data Guard, RMAN, alert log, estatisticas, regressao de plano, forecast,
  mudancas, health score) + analisador generico para qualquer evidencia.
- **Alem de performance**: corrupcao de bloco, datafile fora de ONLINE, destino
  de archive com erro, **RPO real por datafile**, gap de standby, contas
  expirando (ORA-28001), jobs falhando, ORA-01555 e recursos do host. Falha,
  bug e erro de infraestrutura entram no mesmo relatorio.
- **Perfil de esperas sem Diagnostics Pack**: amostragem de `gv$session`
  (tecnica do Snapper/YaASHs) - banco sem licenca deixa de ficar sem o dado
  central do Method R.
- **Base de conhecimento** (`kb/`): 28 playbooks passo a passo para os incidentes
  recorrentes. O framework diz *o que* esta errado e *o que* fazer primeiro; a
  KB diz **como**. Todo passo declara o risco de executa-lo, o que olhar no
  resultado e a armadilha. Disponivel no painel, com busca por sintoma ou ORA-,
  e **sugerida automaticamente** a partir dos achados do caso selecionado.
- **Plano de acao priorizado**: responde *o que faco primeiro* - ordena por
  classe de falha (integridade do dado antes de performance), severidade e
  previsibilidade, com o **risco de executar** e **como validar depois**.
- **Destaques do caso** (`troubleshoot/destaques.py`): responde a outra pergunta,
  *o que aconteceu neste servidor* - bloqueio (incluindo cadeia em **cascata**),
  regressao de SQL, SQL pesado e carga acima do baseline. Nao e o mesmo que o
  plano: a acao mais urgente pode ser trocar uma senha que expira, enquanto o
  FATO do incidente foi o bloqueio que parou 40 sessoes. Alimenta a "Causa Raiz"
  do relatorio **marcada como analise automatica a confirmar** - o que o DBA
  escreveu a mao no `CASO.py` sempre vence.
- **Dicionario de erros ORA-** (`mcp/erros_ora.json`): traduz o codigo do alert
  log em causa, acao concreta e nota MOS, e marca o que termina em SR.
- **Correlacao com mudancas**: liga o achado a STATS/DDL/parametro/patch/restart
  recente como **hipotese a confirmar**, nunca como causa provada.
- **Conformidade estilo ORAchk** (`mcp/best_practices.json`): compara ~50 itens
  com o recomendado pela Oracle, em sete familias - parametros, configuracao do
  banco, multiplexacao de controlfile/redo, o que ficou como veio da instalacao
  (pfile, COMPATIBLE, DST pendente, parametro underscore, autotask e dbms_stats),
  recuperabilidade/HA, seguranca (senha default, profile sem limite, grant a
  PUBLIC) e estrutura (objeto em SYSTEM, tablespace antiga, sequence sem cache).
  Devolve um **percentual de conformidade**, e cada desvio sai com *porque*,
  comando e referencia. Regra que nao se aplica ao ambiente nao e cobrada:
  FORCE LOGGING e STANDBY_FILE_MANAGEMENT so viram achado se existir standby, e
  `control_file_record_keep_time` e comparado com a retencao REAL do RMAN
  daquele banco, nao com um numero fixo. Acrescentar regra e editar JSON.
- **Alert log direto do banco**: le `V$DIAG_ALERT_EXT` por SQL - sem acesso ao
  sistema de arquivos e sem ninguem colar o arquivo - e traduz cada ORA- pelo
  dicionario.
- **Bugs e manutencao**: idade do ultimo Release Update (a primeira pergunta da
  Oracle num SR), componentes/objetos de SYS invalidos (sobra de upgrade) e
  backups RMAN que **falharam**.
- **Drill-down**: cada achado sugere o **proximo passo** com o comando pronto.
- **Ciclo de vida do caso** (`CASO.py`): hipotese atual + historico de hipoteses
  descartadas, acoes com nivel de risco, status, escalonamento, RCA e
  consolidado "Fim do dia" em CSV. A **linha do tempo** desses eventos sai no
  painel E na **pagina de historico** (`<caso>_historico.html`), a um clique do
  dashboard - o HTML e o arquivo que vai anexado no chamado.
- **Reincidencia entre casos**: cruza o historico de todos os casos do **mesmo
  servidor** e avisa quando um achado ja apareceu antes ("3x em 30 dias").
  Achado novo e incidente; achado repetido pede tratamento da **causa**. Conta
  uma vez por caso e por dia, entao regerar o relatorio nao infla o numero.
- **Diario de coleta**: cada coleta grava `troubleshooting/<caso>/coleta.log`
  com carimbo de hora - da para responder, semanas depois, se aquele check ja
  falhava antes ou passou a falhar agora.

## Requisitos

- **Python 3.11+** (testado ate 3.14). Nao precisa de Oracle Client.
- Acesso de rede ao listener Oracle (porta **1529**/1521) para coleta online;
  sem rede direta, use o modo Citrix (`queries/coletor.sql`).
- Usuario **somente leitura** no banco (ex.: `dbaread` com `SELECT_CATALOG_ROLE`).
- Dependencias: `oracledb` e `python-pptx` (ver `requirements.txt`).

## Instalacao

```powershell
cd "C:\projetos\ParraDibiei - checklist"   # ajuste para o seu caminho
.\install.bat
```

Ou manualmente:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Confirme que a maquina esta pronta - **nao toca em banco nenhum**:

```powershell
.\verificar.bat
```

Detalhes, instalacao offline (`wheelhouse\`) e portabilidade para outra maquina:
[`INSTALL.md`](INSTALL.md).

### Versao portatil (sem instalar nada)

```powershell
.\.venv\Scripts\python.exe EMPACOTAR.py --zipar
```

Gera `dist\ParraTroubleshoot\` (~84 MB): Python isolado + `libs\` + o framework.
Quem receber **copia a pasta e clica num `.bat`** - sem `.venv`, sem admin e sem
mexer no Python da maquina. O pacote **nunca leva evidencia**
(`troubleshooting\` e `output\` nascem vazios).

Comece por `verificar.bat`, que diz se a maquina esta pronta sem tocar em banco.
Depois `INICIAR.bat` (painel), `TERMINAL.bat` (comandos completos, inclui
`--advisor`), `healthcheck.bat <servidor>`, `coletar.bat` e `redija.bat`.
**Todos** os `.bat` procuram o `.venv` e, nao achando, caem para o
`python\python.exe` que vem no pacote - por isso funcionam nos dois cenarios.

Se o pacote vai circular, gere sem o inventario de servidores:

```powershell
.\.venv\Scripts\python.exe EMPACOTAR.py --sem-conexoes --zipar
```

Sem isso o `stringConexao\` viaja junto (host, porta, service e usuario dos
bancos - sem senha, mas e o mapa do parque). Com a flag, o `verificar.bat`
acusa um AVISO de "nenhuma conexao" e quem recebe cadastra a sua com
`coletar.bat --add`. Use `--origem local` para montar sem baixar nada quando o
proxy bloquear o download do Python embeddable.

A **bateria de homologacao** (`TESTE_HOMOLOG.py` + `servidores.txt`) e
ferramenta **interna de manutencao** e nao faz parte do produto: ela roda a
matriz de coleta contra os servidores de homologacao do nosso time, no
terminal, quando ha update ou correcao de bug a validar. Nao aparece no painel,
nao tem rota HTTP e nao acompanha o pacote.

Distribua por SharePoint ou unidade de rede, **nao por pendrive**: USB costuma
ser bloqueado por DLP e levaria dado de producao para fora.

### Levar o projeto em UM arquivo

Quando o canal aceita so um anexo (e-mail, chamado), o pacote de ~84 MB nao
passa. `--unico` gera um `.py` de texto (~1 MB) com o codigo-fonte dentro:

```powershell
.\.venv\Scripts\python.exe EMPACOTAR.py --unico
python ParraDibiei_UNICO.py            # quem recebe roda isto
```

E **texto**, nao `.exe` nem `.zip`: executavel desconhecido e barrado por
AppLocker e `.zip` com codigo dentro costuma ser removido pelo antivirus. O
proprio arquivo confere o SHA-256 antes de escrever - anexo truncado extrairia
"quase tudo" em silencio, e metade de um framework parece um framework.

Leva codigo, testes e configuracao; **nao** leva Python nem bibliotecas (depois
de extrair, rode `install.bat`). Os testes vao junto de proposito: rodar a suite
no destino e a unica prova de que o transporte deu certo. Nao leva evidencia,
`servidores.txt` nem `caminhos_locais.txt`.

Para mandar a terceiro, `--distribuicao` aplica o mesmo criterio do pacote (sem
a bateria interna) e omite os testes.

### Especificacao para reconstruir do zero

```powershell
.\.venv\Scripts\python.exe BLUEPRINT.py
```

Gera `output\blueprint\PARRADIBIEI_BLUEPRINT.md` (~517 KB, ~130 k tokens): o que
o framework resolve, os principios, os contratos de dados, as **invariantes com
o incidente que criou cada uma**, a ordem de reconstrucao e os dados de dominio
literais (os checks SQL, os playbooks, as regras, o dicionario ORA-).

Serve para entregar a um modelo de linguagem com a tarefa "recrie este
framework". **Nao e backup**: o codigo recriado nao sai identico. Para copia
fiel, use `--unico` acima.

Os numeros e a lista de modulos sao recontados do repositorio a cada geracao, e
os dados vao como bytes originais (reserializar arriscaria alterar um SQL).
`--sem-dados` corta a secao 8, mas ai quem reconstruir **inventa** os checks e os
limites - o arquivo avisa isso em vez de calar.

| Saida | Tamanho | Para que |
|---|---|---|
| `EMPACOTAR.py --zipar` | ~84 MB | **usar** sem instalar nada (leva Python) |
| `EMPACOTAR.py --unico` | ~1 MB | **transportar** o projeto por um canal de 1 arquivo |
| `BLUEPRINT.py` | ~0,5 MB | **reconstruir** do zero com um modelo de linguagem |

## Como operar (o caminho curto)

Do zero ao chamado escrito, sem decorar comando. **Nao existe senha a
configurar**: ela e digitada a cada conexao e morre com o processo.

```powershell
.\painel.bat                                   # abre o painel em 127.0.0.1
```

No painel: escolha o servidor, dispare a coleta (a senha e pedida numa caixa
mascarada) e a barra de etapas conduz o resto - **1 Coletar -> 2 Conduzir ->
3 Redigir**. Quem preferir terminal, o mesmo caminho em quatro linhas:

```powershell
.\healthcheck.bat DBSRV600                       # 1. coleta read-only + relatorio
.\.venv\Scripts\python.exe GUIA.py HC_DBSRV600   # 2. conduz: um passo por vez
.\.venv\Scripts\python.exe GUIA.py HC_DBSRV600 --saida saida.txt   # le o retorno do comando
.\redija.bat HC_DBSRV600                         # 3. regera o relatorio do caso
```

O que resolve cada situacao:

| Situacao | Comando |
|---|---|
| Chamado de lentidao chegou agora | `.\coletar.bat INC123 --conn DBSRV600 --completo --open` |
| Rotina do dia num servidor | `.\healthcheck.bat DBSRV600` |
| Sem rede direta ao banco (Citrix) | rode `queries\coletor.sql` no SQL*Plus e traga a saida |
| Nao sei o proximo passo | `GUIA.py <caso>` - o roteiro sai dos **achados**, nao de um menu fixo |
| Rodei um comando e quero que a IA leia | `GUIA.py <caso> --saida arquivo.txt` (ou a caixa da aba **Guiado**) |
| Preciso do texto do chamado | `.\redija.bat <caso>` ou o botao **Redigir** |
| Passagem de plantao | botao **Fim do dia (CSV)** no painel |
| Um SQL especifico esta pesado | `DETALHAR_SQL.py <caso> <sql_id> --conn <servidor>` |
| A ferramenta parece errada | `MELHORIA.py --tudo` (offline) e `verificar.bat` |
| Apresentar o framework | `APRESENTAR.py --vendas` (3 slides) ou `--impacto` |

Regra que vale para tudo: **o framework nunca executa acao no banco**. Ele
coleta (somente `SELECT`), organiza, prioriza e mostra o comando com o risco
declarado - executar continua sendo decisao humana, no seu terminal. A unica
excecao e o `--advisor`, que **escreve** e por isso vem desmarcado, so no modo
Completo e com confirmacao explicita.

Referencia completa de comandos: [`COMANDOS.md`](COMANDOS.md). O detalhe de cada
etapa vem a seguir.

## Como se executa

### 0. Painel (interface grafica local)

```powershell
.\painel.bat
```

Abre `http://127.0.0.1:8765/` no navegador. A tela segue a **ordem do trabalho**,
uma etapa por vez - `1 Coletar`, `2 Conduzir`, `3 Redigir e consolidar` -, com a
lista de casos fixa na lateral e a faixa de **Execucao** presa no rodape. A etapa
acompanha o que foi feito (escolher um caso leva para *Conduzir*; resolver leva
para *Redigir*), entao quem ja domina quase nunca clica na barra.

O que da para fazer sem digitar comando: disparar a coleta com log ao vivo,
conduzir o caso (hipotese, acao com risco, escalar, resolver, fim do dia), ver a
**linha do tempo**, a **reincidencia** dos achados naquele servidor e o
**historico de execucoes** da sessao, fazer **drill-down read-only**
(`DETALHAR_SQL.py` por SQL_ID e `JANELA.py` por periodo, com o servidor lido do
`meta.json`).

Tres coisas que evitam sair do painel no meio do caso:

- **Fechamento em primeiro plano** - resolver o caso sobe o Template Padrao num
  quadro por cima da tela, com **Copiar**. O texto e editavel para ajustar antes
  de colar; o caso continua como esta.
- **Artefatos do caso** - Dashboard, Historico, Apresentacao (PPTX),
  Relatorio (.md), Achados (JSON) e a Pasta do caso, cada um no seu botao. O que ainda nao foi
  gerado fica apagado, dizendo o motivo. O dashboard abre em aba; o resto vai
  para o programa padrao do Windows (o navegador baixaria o `.pptx`).
- **Servidor fora do repositorio** - o nome e conferido **antes** de abrir
  processo. Se nao existir, a tela oferece os nomes parecidos (um clique corrige
  e coleta) ou o cadastro pedindo host, porta, service name e usuario, gravando
  em `stringConexao/` no formato do SQL Developer e seguindo com a coleta.

O painel e uma **camada fina**: monta a linha de comando e chama os mesmos
`COLETAR.py`/`TROUBLESHOOT.py` e o modulo `troubleshoot.caso`. Nenhuma regra de
negocio vive nele. Escuta so em `127.0.0.1`, a senha e digitada numa caixa
mascarada e vai pelo **stdin** do processo filho (nunca em `argv`, nunca em
disco, nunca no log) e **`--advisor` so existe como opt-in do modo Completo** -
desmarcado por padrao, com confirmacao antes de rodar, por escrever no banco.

### 1. Health check / coleta de um servidor

```powershell
.\healthcheck.bat DBSRV600
```

Equivalente completo (interativo se faltar argumento):

```powershell
.\.venv\Scripts\python.exe COLETAR.py INC000987654 --conn DBSRV600 --completo --open
```

Parametros uteis do `COLETAR.py`:

| Parametro | Para que serve |
|---|---|
| `--conn NOME` | conexao do repositorio `stringConexao\*.json` (aceita parte do nome) |
| `--pack auto\|T\|D\|N` | controle de licenca; `N` = so `v$`, sem consumir pack |
| `--rapido` / `--parcial` | triagem curta (pula `DBA_HIST_*`/ADDM) / coleta leve |
| `--workers N` / `--timeout S` | paralelismo (padrao 4) e limite por query (padrao 120s) |
| `--completo` / `--open` / `--no-pptx` | relatorio completo / abrir no fim / sem PPTX |
| `--list-conns` / `--add` | lista ou cadastra conexoes no repositorio |
| `--advisor` | **NAO e read-only**: roda SQL Tuning Advisor (consome Tuning Pack) |

**Senha: sempre digitada, a cada execucao** - como numa sessao de SuperPuTTY.
No terminal vem por prompt sem eco; pelo painel, numa caixa mascarada no cartao
**Execucao**, que aparece quando o comando pergunta e manda a resposta pelo
`stdin` do processo. Nao existe `--password`, arquivo, nem variavel de ambiente
que a substitua, e ela **nunca** e lida do arquivo de conexoes.

Na bateria interna a senha e pedida **uma unica vez** no terminal e vale para os
N servidores da lista (cada filho a recebe pelo proprio `stdin`) - perguntar 26
vezes so empurraria alguem a escrever a senha dentro de um `.bat`.

Ate 02/08/2026 existia um `guardar_senha.bat` que gravava a senha em **texto
puro no registro do Windows** (`HKCU\Environment\ORA_PASSWORD`). Ele foi
removido, mas **nao apaga o que ja gravou**: rode `.\limpar_senha.bat` uma vez
por maquina. O `.\verificar.bat` acusa isso como **[ERRO]** enquanto durar.

### 2. Gerar/regerar o relatorio de um caso ja coletado

```powershell
.\.venv\Scripts\python.exe TROUBLESHOOT.py INC000987654 --completo --open
.\.venv\Scripts\python.exe TROUBLESHOOT.py --list      # lista os casos
.\.venv\Scripts\python.exe TROUBLESHOOT.py --index     # regera so o indice
```

### 3. Conduzir o caso (ciclo de vida)

```powershell
.\.venv\Scripts\python.exe CASO.py INC000987654                        # estado atual
.\.venv\Scripts\python.exe CASO.py INC000987654 --hipotese "IO saturado no ASM"
.\.venv\Scripts\python.exe CASO.py INC000987654 --acao "coletei ASH 1h" --risco nenhum
.\.venv\Scripts\python.exe CASO.py INC000987654 --status Escalado --escalar "Storage"
.\.venv\Scripts\python.exe CASO.py INC000987654 --resolver --causa "..." --solucao "..." --prevencao "..."
.\.venv\Scripts\python.exe CASO.py INC000987654 --redija                # Template Padrao para o chamado
.\.venv\Scripts\python.exe CASO.py --fim-do-dia                        # CSV consolidado
```

O `--redija` imprime o caso no **Template Padrao** (Diagnostico Support
Platform + RCA) em texto puro, para colar no chamado. No painel e o botao
**Redigir** da aba *Conduzir* - e ele abre sozinho ao resolver o caso, com um
botao **Copiar**. So transcreve o que foi registrado: campo sem registro sai
como `<preencher>`, em vez de um texto plausivel inventado.

### 4. Troubleshooting guiado (um passo por vez)

```powershell
.\.venv\Scripts\python.exe GUIA.py INC000987654                        # onde parei + o que a evidencia sugere
.\.venv\Scripts\python.exe GUIA.py INC000987654 --iniciar bloqueio_lock
.\.venv\Scripts\python.exe GUIA.py INC000987654 --avancar "nenhuma sessao bloqueada na janela"
.\.venv\Scripts\python.exe GUIA.py INC000987654 --confirmar "matei a sessao 1234"   # grava a acao com o risco do passo
.\.venv\Scripts\python.exe GUIA.py INC000987654 --pendencias                        # checklist pos-acao em aberto
.\.venv\Scripts\python.exe GUIA.py INC000987654 --saida saida.txt                   # le o retorno do comando e reavalia as hipoteses
```

No painel e a aba **Guiado**. O roteiro sugerido sai dos **achados do
relatorio**, nao de um menu fixo: sem relatorio gerado, a tela diz isso em vez
de chutar um caminho. Cada passo mostra a **matriz de risco antes do comando**
(impacto, o que validar antes, rollback, se exige aprovacao formal).

**Executei** grava a acao no ciclo de vida com o risco daquele passo - a mesma
acao que ja aparecia na linha do tempo, no relatorio e no consolidado do dia.
Passo que **muda o ambiente** abre o **checklist pos-acao**, que fica visivel no
chamado (`Checklist pós-ação: PENDENTE ...`) e no topo do Plano de Acao do
dashboard ate alguem confirmar: acao executada com validacao pendente e um
estado pior do que achado conhecido e nao tratado.

**Cole a saida do comando de volta.** O passo diz o que rodar; o retorno do
terminal colado na caixa **vira evidencia do caso** e o motor repontua as
hipoteses na hora, mostrando *o que mudou* ("Concorrencia: sessoes presas em
lock foi DESCARTADA pela saida observada"). Le codigos `ORA-`, cadeia de
bloqueio, eventos de espera com percentual, valor de parametro, **tabela de
ocupacao de espaco** (tablespace, FRA, diskgroup ASM e ate `df -h`, com ou sem
o sinal de `%` - o SQL*Plus imprime o numero seco), **apply/transport lag do
Data Guard** no formato de intervalo (`+00 02:15:33`), **MRP parado** no
standby, **swap do host** (`free -m`), **idade do `LAST_ANALYZED`**, **estado
das instancias** do cluster (`gv$instance`), **top SQL por DB time** (nomeia o
`sql_id` que domina) e **limite de processes/sessions** (`v$resource_limit`, o
risco de `ORA-00020` que chega como "a aplicacao nao conecta") - e, o que mais
economiza tempo, **consulta que volta vazia tambem conta**: `no rows selected`
na verificacao de bloqueio e a prova que elimina a hipotese, nao um passo
perdido. O que nao for reconhecido nao vira achado nenhum; a tela diz que nao
entendeu, em vez de inventar leitura. Cada linha lida mostra **de onde saiu**, a
saida bruta fica guardada no caso e a verificacao entra na linha do tempo como
`verificado` (separada do que foi *executado*), chegando ao chamado e ao bloco
do copiloto - assim o LLM nao manda conferir o que voce acabou de conferir.
Nada e executado aqui: o comando continua rodando no seu terminal.

**Pacote de tuning: ficou mais lento, ou passou a fazer mais?** Essa e a
pergunta que decide o rumo - e responder errado gasta o plantao tunando um SQL
que nao mudou nada enquanto a origem da carga cresce. Cole a serie por hora
(`GROUP BY TO_CHAR(sample_time,'HH24')`) e o motor compara o pico com a mediana:
pico ao dobro sustenta **aumento de carga** (e explica o I/O e a CPU como
*consequencia*, em vez de abrir tres frentes de tuning); serie estavel **refuta**
e joga a investigacao para o custo. Cole o historico de `PLAN_HASH_VALUE` e dois
planos viram **troca de plano**, um so **refuta a regressao**. Cole
`FIRST_LOAD_TIME` / `LAST_DDL_TIME` e o framework acha **o deploy que ninguem
avisou** - SQL novo ou objeto recompilado nas ultimas 48h, que explica plano novo
e parse. E o plano do `dbms_xplan` colado aponta o **full table scan** com o nome
da tabela.

O **discriminador** e o custo por execucao: cole `executions` + `elapsed_time`
(do `v$sqlstats` ou do `dba_hist_sqlstat`) e o motor compara o custo de CADA
execucao. Estavel = o SQL nao piorou, alguem passou a chama-lo mais, e a
regressao de plano e **descartada mesmo com `plan_hash_value` diferente** (troca
de plano com custo igual e inofensiva). Subiu = o volume e desculpa. O playbook
**Tuning: ficou mais lento ou passou a fazer mais?** conduz esses passos na ordem
que fecha o diagnostico, e cada passo devolve justamente uma saida que o motor
sabe ler.

### 5. Drill-down

```powershell
.\.venv\Scripts\python.exe DETALHAR_SQL.py INC000987654 9xy1abcd2ef3g --conn DBSRV600
.\.venv\Scripts\python.exe JANELA.py INC000987654 --inicio "2026-07-29 09:00" --fim "2026-07-29 10:00" --conn DBSRV600
```

### 6. Bateria de homologacao (interna, varios servidores, read-only)

```powershell
.\.venv\Scripts\python.exe TESTE_HOMOLOG.py --file servidores.txt --comparar
```

So para quem **mantem** o framework, no terminal, antes de liberar um update ou
depois de corrigir um bug: confirma que a coleta e a analise seguem de pe contra
banco real. Nao esta no painel nem no pacote distribuido, e nunca roda
`--advisor`. `servidores.txt` e **repositorio vivo**: servidor novo entra
acrescentando a lista.

Na primeira vez o arquivo nao existe - nome de host e inventario de
infraestrutura e nao viaja no repositorio. Copie o modelo e edite:

```powershell
copy servidores.txt.modelo servidores.txt
```

### 7. Melhoria continua

```powershell
.\.venv\Scripts\python.exe MELHORIA.py            # corpus + higiene + fontes (offline)
.\.venv\Scripts\python.exe MELHORIA.py --corpus --aceitar   # novo baseline
```

Tres frentes, todas **offline**: **corpus de regressao** (reanalisa os casos ja
coletados e compara com o baseline), **higiene** (testes, pins, buracos do
catalogo) e **radar** da documentacao oficial e da comunidade Oracle. A rotina
nao abre conexao com banco nenhum - a bateria contra bancos reais e um comando
separado, rodado a mao.

Regra de ouro: caso de **producao** e reanalisado **so a partir da evidencia ja
gravada** - a rotina nunca reconecta em producao e nao escreve na pasta do caso.
Ritual completo em `.devin\workflows\melhoria-continua.md`.

### 8. Apresentar o framework

```powershell
.\.venv\Scripts\python.exe APRESENTAR.py             # os tres decks
.\.venv\Scripts\python.exe APRESENTAR.py --gestao    # so o executivo (10 min)
.\.venv\Scripts\python.exe APRESENTAR.py --tecnico   # so o tecnico (20 min)
.\.venv\Scripts\python.exe APRESENTAR.py --impacto   # so o de impacto (10 min)
```

Gera em `output/apresentacao/` o deck de apresentacao **do framework** (nao de
um caso). Os numeros dos slides sao contados do repositorio na hora de gerar, e
cada slide traz a **fala sugerida** nas notas do apresentador. Nenhum dado de
banco real entra no arquivo - ha teste garantindo isso, porque o deck circula
por e-mail.

O deck **`--impacto`** compara o checklist feito na mao com o framework por
nivel de senioridade (junior / pleno / senior) e cobre cobertura,
assertividade e padronizacao. Ele e o unico que faz conta, entao separa em
tres origens e diz no proprio slide qual e qual:

- **contado** do repositorio (checks do catalogo, testes da suite);
- **medido** - o tempo de MAQUINA por caso: coleta (mediana do
  `[tempo] coleta em Ns` dos `coleta.log`) mais o pipeline de analise,
  dashboard, PPTX e relatorio, cronometrado offline sobre a evidencia ja
  gravada;
- **premissa** para o lado manual, que ninguem cronometra.

```powershell
.\.venv\Scripts\python.exe APRESENTAR.py --medir --impacto
```

`--medir` cronometra e **trava** o resultado em
`output/apresentacao/medicao.json` (com data e tamanho de amostra). O deck le
esse arquivo em vez de medir na hora - benchmark durante a geracao deixaria o
comando lento e faria o mesmo deck sair com numeros diferentes em cada
maquina. Sem medicao travada, o deck usa a premissa conservadora e **diz na
tela** que esta usando. O cronometro nao toca em banco e nao escreve na pasta
do caso (os artefatos vao para um diretorio temporario) - ha teste garantindo.

A medicao travada **nao se atualiza sozinha**: ela e uma foto do momento em que
`--medir` rodou. Depois de coletar casos novos, rode `--medir` de novo, senao o
deck segue apoiado na amostra antiga. A coleta so recebe o rotulo *medido* a
partir de `impacto.MIN_AMOSTRAS` (3) execucoes registradas - abaixo disso o
numero real e usado, mas o slide diz em quantas execucoes ele se apoia.

Junto com esse deck sai o **roteiro de fala** em
`output/apresentacao/ROTEIRO_IMPACTO.md`: preparacao (D-2, D-1, 10 min antes),
os numeros para saber de cor, o passo a passo slide a slide com a frase literal
de abertura de cada um, as frases que valem decorar, as perguntas dificeis com
a resposta pronta e a ordem de corte se o tempo apertar. Os numeros do roteiro
vem do mesmo calculo do deck, e ha teste comparando a ordem dos topicos com a
ordem real dos slides - roteiro que descreve outro slide e pior que roteiro
nenhum.

As premissas ficam em `troubleshoot/impacto.py` (`PREMISSAS`, `CHECKS_NA_MAO`),
num lugar so, com as referencias publicas em `FONTES` - o deck imprime as duas
coisas. Se o time achar que o tempo por check e outro, ajuste la e regere; o
`APRESENTAR.py --impacto` imprime o comparativo no terminal antes de gerar,
para conferir sem abrir o PPTX.

## Entradas e saidas

```
stringConexao\*.json        entrada: repositorio de conexoes (export do SQL Developer)  [LOCAL]
servidores.txt              entrada: lista de servidores da bateria interna     [LOCAL]
marcas_locais.txt           entrada: termos proibidos nos artefatos gerados          [LOCAL]
troubleshooting\<caso>\     entrada: evidencias coletadas + meta.json (ciclo de vida)
queries\                    SQL padrao + coletor.sql (modo Citrix)
mcp\catalog.json            fonte unica dos checks (gera o coletor offline)
output\troubleshooting\     saida: <caso>.html, <caso>_historico.html, <caso>.pptx,
                            <caso>_relatorio.md, index.html
output\troubleshooting\consolidado_<data>.csv   saida: "Fim do dia"
output\homolog\ultima.json  saida: historico da bateria interna (alimenta o --comparar)
kb\*.json                   entrada: playbooks da base de conhecimento
output\melhoria\           saida: baseline do corpus, radar de fontes e relatorios
```

**[LOCAL]** = fica na sua maquina e **nao e versionado**: sao dados do seu
ambiente (host, servico, usuario, nome da empresa), nao codigo do framework.
Cada um tem um `.modelo` versionado ao lado - copie e adapte. O `.gitignore`
explica o porque de cada um.

## Estrutura

```
COLETAR.py TROUBLESHOOT.py CASO.py DETALHAR_SQL.py JANELA.py TESTE_HOMOLOG.py
PAINEL.py MELHORIA.py EMPACOTAR.py APRESENTAR.py BLUEPRINT.py
troubleshoot/
  model.py      CaseResult / Section / Finding / Metric / Severity
  collector.py  execucao read-only dos checks (gating por topologia)
  topologia.py  deteccao do ambiente
  caso.py       ciclo de vida (meta.json)
  nextstep.py   drill-down / proximo passo
  report.py     HTML + PPTX + relatorio + indice
  painel.py     painel local (GUI): servidor http.server + pagina, camada fina
  melhoria.py   corpus de regressao + higiene (rotina de melhoria continua)
  plano.py      plano de acao priorizado (o que fazer primeiro)
  destaques.py  o que aconteceu no servidor (o fato), separado do plano
  kb.py         base de conhecimento: carga, busca e ligacao achado->playbook
  erros.py      dicionario de erros ORA- (causa, acao, MOS)
  correlacao.py liga achado a mudanca recente (hipotese, nao causa)
  analyzers/conformidade.py  best practices Oracle (estilo ORAchk)
  analyzers/bugs.py          patch, dicionario e backup falhando
  marca.py      fonte unica da marca ParraDibiei (SVG inline, sem CDN)
  apresentacao.py  os tres decks (gestao / tecnico / impacto)
  impacto.py    premissas, medicao do tempo de maquina e comparativo
  blueprint.py  especificacao para reconstruir o framework do zero
  roteiro.py    roteiro de fala do deck de impacto (gerado junto com ele)
  senha.py      unico lugar que pede senha (terminal ou painel; nunca guarda)
  dialogo.py    protocolo de pergunta/resposta CLI <-> painel pelo stdin
  doctor.py sqlhc.py conn_repo.py operar.py
  analyzers/    analisadores especializados + generic.py
mcp/            catalogo de checks, dicionario ORA-, best practices,
                loader, servidor MCP, playbooks
kb/             playbooks (conhecimento como DADO, nao codigo)
tests/          suite pytest
```

### O tamanho de cada peca

<!-- NUMEROS:INICIO -->
<!-- Gerado por `python -m troubleshoot.numeros_doc`. Nao editar a mao: o teste `tests/test_numeros_doc.py` reprova divergencia. -->

| O que | Quanto | Fonte |
|---|---:|---|
| Checks no catalogo | 84 | `mcp/catalog.json` |
| Categorias de check | 18 | `mcp/catalog.json` |
| Analisadores | 27 | `troubleshoot/analyzers/` |
| Playbooks / passos | 40 / 350 | `kb/*.json` |
| Regras de conformidade / familias | 56 / 7 | `mcp/best_practices.json` |
| Erros ORA- catalogados | 50 | `mcp/erros_ora.json` |
| Funcoes de teste | 2494 | `tests/test_*.py` |

> As funcoes de teste sao contadas por `def test_`; o pytest reporta mais, porque parametrizacao vira varios casos.
<!-- NUMEROS:FIM -->

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Os dois rodam sozinhos **antes de cada commit** (hook `githooks/pre-commit`,
ativado pelo `install.bat`) e no GitHub Actions (`.github/workflows/ci.yml`)
quando o repositorio tiver remote. Para pular num commit de documentacao:
`git commit --no-verify`.

## Documentacao

- [`COMANDOS.md`](COMANDOS.md) — referencia de comandos (chat e terminal).
- [`PLATAFORMA.md`](PLATAFORMA.md) — visao geral da plataforma.
- [`README_TROUBLESHOOT.md`](README_TROUBLESHOOT.md) — detalhe do framework, tipos
  de evidencia e semaforo de severidade.
- [`COMPARATIVO.md`](COMPARATIVO.md) — comparativo com DPA / Datadog DBM / AWR.
- [`MELHORIAS.md`](MELHORIAS.md) — roadmap.
- [`INSTALL.md`](INSTALL.md) — instalacao e portabilidade.
- [`ESTADO_PROYECTO.md`](ESTADO_PROYECTO.md) — estado vivo do projeto.
- Persona e fluxo: `.devin\rules\dba-master.md` e `.devin\workflows\`.

## Seguranca

Tudo e read-only por padrao: nenhum `INSERT/UPDATE/DELETE/DDL/PLSQL`, nenhum
`COMMIT`, validador `validate_select` em cada check. A **unica** excecao e
`COLETAR.py --advisor` (opt-in explicito, cria tuning tasks e consome Tuning
Pack). Acoes corretivas sugeridas saem em `acoes.sql` **comentadas**, para o DBA
revisar e aplicar manualmente.
