# COMANDOS — referência rápida

Mapa único do que dá para pedir **no chat** e o comando equivalente **no prompt**.
Rode sempre com o diretório atual na **raiz do projeto** (a pasta que contém
`COLETAR.py`).

> **Princípio:** todo o fluxo é **read-only**. A única exceção está na seção
> [Advisor](#advisor--a-única-exceção-escreve-no-banco), sempre com opt-in.

Para o panorama da plataforma (o que faz, por que é assim, números e checklist
de validação): **[`PLATAFORMA.md`](PLATAFORMA.md)**.

---

## 1. Instalação (uma vez por máquina)

| Comando | O que faz |
|---|---|
| `.\install.bat` | Cria o `.venv`, instala dependências (usa o `wheelhouse\` se estiver offline) e roda os testes. |
| `.\verificar.bat` | *Doctor*: confere venv, dependências, catálogo e conexões. **Não acessa banco.** É por onde começar. |
| `.\limpar_senha.bat` | Apaga a senha que o **antigo** `guardar_senha.bat` gravou no registro. Rode uma vez por máquina. |

**Não existe senha a configurar.** Ela é digitada a cada conexão (sem eco no
terminal, ou numa caixa mascarada no painel), vai direto para o processo e morre
com ele: não há `--password`, arquivo nem variável de ambiente que a substitua.
Sem terminal interativo, o `COLETAR.py` **falha rápido** com a instrução — nunca
trava esperando input.

> ⚠ O `guardar_senha.bat` foi **removido** (02/08/2026): ele gravava a senha em
> **texto puro** em `HKCU\Environment\ORA_PASSWORD`, permanente e legível por
> qualquer processo seu. Se esta máquina já o usou, rode `.\limpar_senha.bat` e
> **troque a senha** — o `verificar.bat` acusa isso como **[ERRO]** enquanto
> durar.

---

## 2. Fluxo principal

O caminho curto, do chamado ao texto entregue:

```powershell
.\painel.bat                                     # tudo pela tela (127.0.0.1)

.\healthcheck.bat DBSRV600                       # 1. coletar (read-only)
.\.venv\Scripts\python.exe GUIA.py HC_DBSRV600   # 2. conduzir, um passo por vez
.\redija.bat HC_DBSRV600                         # 3. redigir/regerar o relatorio
```

| No chat | No prompt |
|---|---|
| "healthcheck dbsrv600" | `.\healthcheck.bat DBSRV600` |
| "triagem rápida no dbsrv600" | `.\healthcheck.bat DBSRV600 --rapido` |
| "coletar INC123 em DBSRV600" | `.\coletar.bat INC123 --conn DBSRV600 --completo` |
| "redija INC123" | `.\redija.bat INC123` |
| "detalhar INC123" | *(leitura)* `output\troubleshooting\INC123\INC123_relatorio.md` |
| "listar conexões" | `.\coletar.bat --list-conns` |

`healthcheck.bat <SRV>` é atalho de `COLETAR.py HC_<SRV> --conn <SRV> --completo`.

**Coleta + relatório num passo** (`COLETAR.py`) ou **só relatório** de evidências
já em disco (`TROUBLESHOOT.py`):

```powershell
.\.venv\Scripts\python.exe COLETAR.py INC123 --conn DBSRV600 --completo --open
.\.venv\Scripts\python.exe TROUBLESHOOT.py INC123 --completo
.\.venv\Scripts\python.exe TROUBLESHOOT.py --list
.\.venv\Scripts\python.exe TROUBLESHOOT.py --new INC123
```

Saída em `output\troubleshooting\<caso>\`: dashboard HTML, PPTX,
`<caso>_relatorio.md` (Template Padrão DBA Master) e exports JSON/CSV.

---

## 3. Conduzir o caso (guiado) e colar a saída de volta

| Comando | O que faz |
|---|---|
| `GUIA.py <caso>` | Onde parei, o passo atual e o que a **evidência** sugere (não um menu fixo). |
| `GUIA.py <caso> --iniciar <playbook>` | Começa um roteiro da `kb\`. |
| `GUIA.py <caso> --avancar "o que vi"` | Conclui o passo com a observação. |
| `GUIA.py <caso> --confirmar "matei a sessao 1234"` | Grava a **ação** com o risco daquele passo. |
| `GUIA.py <caso> --pendencias` | Checklist pós-ação em aberto. |
| `GUIA.py <caso> --executar` | **Roda a verificação do passo no próprio framework** (read-only) e repontua as hipóteses. |
| `GUIA.py <caso> --executar --param sql_id=7ktb2sm9ug3xw` | O mesmo, informando o `&parâmetro` que o passo pede. |
| `GUIA.py <caso> --executar-tudo` | Roda de uma vez todos os passos de verificação do playbook. |
| `GUIA.py <caso> --executar --pack N` | O mesmo, **sem consultar nada de Diagnostics/Tuning Pack**. |
| `GUIA.py <caso> --saida saida.txt` | **Lê o retorno do comando** e repontua as hipóteses. |
| `type saida.txt \| GUIA.py <caso> --saida -` | O mesmo, por *pipe*. |
| `CASO.py <caso>` | Ciclo de vida: hipótese, status, causa raiz, linha do tempo. |

Cada passo mostra a **matriz de risco antes do comando** (impacto, o que validar
antes, rollback, se exige aprovação formal). A saída colada vira **evidência do
caso**: o motor diz *o que mudou* nas hipóteses, e **consulta que volta vazia
também conta** (`no rows selected` na verificação de bloqueio **descarta** a
hipótese). O que não for reconhecido **não vira achado** — a tela diz que não
entendeu em vez de inventar leitura. No painel é a aba **Guiado** (`Ctrl+Enter`
interpreta; o `<details>` lista tudo o que o motor sabe ler).

**Nada é executado aqui.** O comando roda no seu terminal; o framework mostra,
lê e registra.

---

## 4. Drill-down — o próximo passo de cada achado

O dashboard traz o comando pronto em **"Próximo passo"**, dentro do Guia de
Sugestões, e o `_relatorio.md` repete em "Próximos passos sugeridos".

| No chat | No prompt |
|---|---|
| "detalhar o sql 7ktb2sm9ug3xw" | `.\.venv\Scripts\python.exe DETALHAR_SQL.py <caso> <sql_id> --conn <SRV>` |
| "analisar a janela das 14h" | `.\.venv\Scripts\python.exe JANELA.py <caso> --inicio "2026-07-29 14:00" --fim "2026-07-29 15:00" --conn <SRV>` |

Se você não disser o servidor, o banco sai do `troubleshooting\<caso>\meta.json`
(campo `conn`), gravado pela coleta. `JANELA.py` exige **Diagnostics Pack** (usa
ASH histórico).

---

## 5. Advisor — a única exceção (escreve no banco)

```powershell
.\coletar.bat <caso> --conn <SRV> --advisor
```

- **Não é read-only:** cria e executa tuning tasks (`DBMS_SQLTUNE`).
- **Consome Tuning Pack.** Ignorado se o banco não tiver a licença habilitada.
- **Roda só onde rende:** troca de plano **ou** impacto ≥ 300s, no máximo 5 SQL.
  Numa regressão de tempo pequena ele só diria "colete estatísticas" — o que o
  `acoes.sql` já entrega de graça e sem licença.
- **Demora:** até 5 min por SQL. Roda depois dos checks read-only, não em paralelo.
- **Resultado:** `advisor.txt` mais as recomendações anexadas ao `acoes.sql`,
  **comentadas**. Nada é aplicado.

Quando um achado justifica o advisor, o relatório mostra o comando no bloco
**"Investigação avançada (escreve no banco)"** — separado dos read-only de
propósito, para não ser copiado por reflexo.

> Se o advisor propuser um **SQL Profile**, a tuning task é **mantida** no banco
> (o `accept_sql_profile` referencia o nome dela). O `acoes.sql` traz o `DROP`
> correspondente para você limpar depois de decidir.

---

## 6. Desempenho e licença

| Flag | Efeito |
|---|---|
| `--rapido` | Pula histórico AWR (`DBA_HIST_*`) e ADDM. Triagem em ~3s. |
| `--workers N` | Conexões read-only paralelas (padrão 4; `1` = sequencial). |
| `--timeout S` | Limite por query (padrão 120s; `0` = sem limite). |
| `--parcial` | Só `v$`/dicionário: **não consome** Diagnostics/Tuning Pack. |
| `--pack T\|D\|N` | Força o nível de licença em vez de detectar (`auto` é o padrão). |
| `--no-pptx` / `--open` / `--no-report` | Controlam a saída. |

O log fecha com `[tempo] coleta em Xs | mais lentos: ...` — use para diagnosticar
lentidão antes de mexer em flag.

### Perfil do ambiente — `servidores.txt` muda a severidade

Um servidor **listado em `servidores.txt`** é tratado como **homologação**; qualquer
outro é **produção**. Quem não está declarado é produção **sempre** — o padrão é o
lado seguro do erro.

Fora de produção, três famílias de desvio saem como **atenção** em vez de crítico,
com o motivo escrito no detalhe do achado:

| Família | Exemplo |
|---|---|
| Recuperabilidade | `log_mode = NOARCHIVELOG`, backup antigo, datafile nunca copiado, redo com um membro |
| Política de senha | conta com senha vencida ou expirada |

Medido na frota de 23 servidores: **41 dos 76 críticos (54%)** eram configuração
esperada num ambiente que não é produção. Um crítico que não é crítico queima a
credibilidade de todo crítico.

**Sintoma nunca relaxa**: deadlock, contenção de lock, tablespace cheia, SQL sem
bind e job falhando seguem críticos em qualquer ambiente — um banco de teste parado
por lock também está parado.

O perfil é gravado no `meta.json` **na coleta**, não deduzido na análise: um caso
analisado hoje não muda de conclusão porque alguém editou `servidores.txt` amanhã.
O dashboard mostra qual perfil assumiu, e só quando **não** é produção.

---

## 7. Conexões

```powershell
.\coletar.bat --list-conns                      # lista o repositório
.\.venv\Scripts\python.exe COLETAR.py --add --conn DBSRV600   # cadastra/atualiza
.\coletar.bat INC123 --host 10.1.2.3 --service DBSRV600       # sem repositório
```

Repositório em `stringConexao\Parra.json` (porta padrão **1529**). O framework
nunca grava senha, mas o export do SQL Developer pode trazer o campo
`password` das conexões salvas com *Save Password* — confira antes de copiar o
arquivo para fora da máquina (ver `INSTALL.md`).

---

## 8. Catálogo de checks e coleta por Citrix

```powershell
.\.venv\Scripts\python.exe -m mcp.loader --list
.\.venv\Scripts\python.exe -m mcp.loader --categories
.\.venv\Scripts\python.exe -m mcp.loader --show <id_do_check>
.\.venv\Scripts\python.exe -m mcp.loader --scripts
.\.venv\Scripts\python.exe -m mcp.loader --gen-sql queries\coletor.sql
```

`mcp\catalog.json` é a **fonte única** dos checks. Sem SQL*Net (Citrix), gere o
`queries\coletor.sql`, rode no SQL*Plus e traga a saída para
`troubleshooting\<caso>\`; depois `.\redija.bat <caso>`.

O **servidor MCP** (`mcp\server.py`) está **desativado** e não é usado por
nenhum comando acima.

---

## 9. Manutenção e apresentação (offline, sem banco)

```powershell
.\.venv\Scripts\python.exe MELHORIA.py --tudo      # corpus de regressao + higiene + radar de fontes
.\.venv\Scripts\python.exe MELHORIA.py --corpus    # so o corpus
.\.venv\Scripts\python.exe VALIDAR_KB.py           # valida os playbooks da kb/
.\.venv\Scripts\python.exe APRESENTAR.py --vendas  # deck de 3 slides (tempo ganho + vantagens)
.\.venv\Scripts\python.exe APRESENTAR.py --impacto # deck de impacto + roteiro de fala
.\.venv\Scripts\python.exe APRESENTAR.py --medir   # cronometra o pipeline e TRAVA a medicao
.\.venv\Scripts\python.exe EMPACOTAR.py --zipar    # versao portatil
.\.venv\Scripts\python.exe BLUEPRINT.py            # especificacao para reconstruir do zero
```

### Passar o framework para outro DBA

```powershell
.\.venv\Scripts\python.exe EMPACOTAR.py --unico --distribuicao --sem-conexoes
```

Um **único `.py` auto-extraível** (~1,2 MB), que cabe em e-mail ou anexo de
chamado. Quem recebe roda `python ParraDibiei_UNICO.py`.

Leva o framework, os playbooks da `kb/` **e a persona** — `.devin/rules`, a
skill `framework-troubleshoot`, os workflows e o `dba_master_prompt.txt.modelo`.
Até 09/2026 o `.devin/` ficava de fora e o colega recebia a ferramenta sem o DBA
Master que sabe usá-la.

Não leva: `stringConexao/` (com `--sem-conexoes`), a bateria de homologação,
os testes, nem arquivo local (`*.bak`, `*.local.*`). O
`test_empacotar.py::TestNadaIgnoradoViaja` compara o pacote com o `.gitignore`
de verdade e reprova se algo ignorado entrar.

Na máquina nova falta só um passo, que o `INSTALL.md` repete:
`copy dba_master_prompt.txt.modelo dba_master_prompt.txt`. Sem ele a regra da
persona carrega apontando para um arquivo que não existe — nada quebra, e o
comportamento fica pela metade.

**O que NÃO viaja, de propósito:** `ambientes.json`, `historico.json`,
`servidores.txt` e as conexões. Cada DBA acumula o próprio parque; a ferramenta
e o conhecimento é que são comuns.

O **corpus** reanalisa evidência **já gravada** e compara com o baseline: mudança
de código que altere diagnóstico aparece antes de ir para o campo. A **higiene**
roda a suíte e cobra buraco no catálogo (check sem SQL, coleta sem analisador,
limiar sem fonte citada). Nenhuma delas conecta em banco.

### Inventário de frota — o que sabemos de cada ambiente

```powershell
.\.venv\Scripts\python.exe AMBIENTES.py --atualizar         # regera a partir dos topology.json
.\.venv\Scripts\python.exe AMBIENTES.py                     # a frota inteira, com os totais
.\.venv\Scripts\python.exe AMBIENTES.py APPHO600            # detalha um ambiente
.\.venv\Scripts\python.exe AMBIENTES.py --hosts             # bancos que dividem o mesmo host
.\.venv\Scripts\python.exe AMBIENTES.py --nota APPHO600 "listener nao sobe sozinho apos reboot"
.\.venv\Scripts\python.exe AMBIENTES.py --registrar DBBPMIVLCL01 --host dbbpmivlcl01
```

Toda coleta detecta a topologia e grava `topology.json` no caso — mas esse
conhecimento morria ali. O `AMBIENTES.py` agrega tudo em `ambientes.json` e
responde, sem abrir caso nenhum: quantos CDB existem, quem está em qual versão,
**quais bancos dividem host** (métrica de host é comum aos dois — contar como
dois sinais independentes de infraestrutura infla qualquer placar de frota).

O arquivo tem duas camadas que não se misturam: `detectado` é derivado e
regerável (não edite à mão), e `notas` é conhecimento humano que **sobrevive à
regeração** — é onde entra o que nenhum `SELECT` descobre. Ambiente sem coleta
também cabe (`--registrar`), e não ganha bloco `detectado`: não se inventa dado
coletado. Offline, não conecta em banco. O `ambientes.json` **não é versionado**
(tem FQDN e nome de banco reais, como o `servidores.txt`); o modelo é
`ambientes.json.modelo`.

### Histórico — desde quando sobe, e quando estoura

```powershell
.\.venv\Scripts\python.exe HISTORICO.py --atualizar          # regera as series da evidencia
.\.venv\Scripts\python.exe HISTORICO.py                      # resumo: pontos, horizonte, familias
.\.venv\Scripts\python.exe HISTORICO.py APPHO600             # series de um ambiente, com tendencia
.\.venv\Scripts\python.exe HISTORICO.py APPHO600 --familia espaco
.\.venv\Scripts\python.exe HISTORICO.py --previsao 90         # o que chega a 90% em 90 dias
.\.venv\Scripts\python.exe HISTORICO.py --marco APPHO600 2026-09-07 "entrou o batch da folha"
```

Onde o `AMBIENTES.py` responde *o que este ambiente é*, o `HISTORICO.py` responde
*como ele evoluiu*. Cada pasta em `troubleshooting\` é uma foto datada; acumular
as fotos em `historico.json` resolve duas limitações reais:

- **O AWR lembra pouco** (8 a 15 dias). A união das coletas estica o horizonte
  para a idade do projeto.
- **A previsão de crescimento do catálogo (`forecast.txt`) exige Diagnostics
  Pack.** A série de espaço daqui sai do `space.txt`, que lê `dba_data_files` —
  **sem pacote de licença**.

A tendência é regressão linear e vem acompanhada de **r²**: série que cresce em
degraus tem inclinação positiva e r² baixo, e extrapolar uma reta ali produz uma
data com cara de precisão. Com menos de 4 coletas espaçadas o comando **não
responde** em vez de chutar — hoje a frota tem 1 ponto por tablespace, então a
previsão de espaço só passa a valer depois de algumas coletas.

O `--marco` é o par do salto de carga: o analisador diz **quando** o patamar
mudou, o marco diz **o que** mudou naquele dia. Sem ele a mesma investigação é
refeita do zero no mês seguinte. Mesma separação de camadas do inventário:
`series` é derivado e regerável, `marcos` é conhecimento humano e sobrevive à
regeração. Offline. O `historico.json` **não é versionado**; o modelo é
`historico.json.modelo`.

### Treinamento — o conhecimento do time vira apostila

```powershell
.\.venv\Scripts\python.exe TREINAMENTO.py                       # todas as tecnologias
.\.venv\Scripts\python.exe TREINAMENTO.py --plataforma mssql    # apostila de UMA tecnologia
.\.venv\Scripts\python.exe TREINAMENTO.py --tema archive        # um tema so
.\.venv\Scripts\python.exe TREINAMENTO.py --quiz                # perguntas, resposta oculta
.\.venv\Scripts\python.exe TREINAMENTO.py --cobertura           # quanto do raciocinio se perde
.\.venv\Scripts\python.exe TREINAMENTO.py --publico             # mascara marcas_locais.txt
```

O melhor material didático do projeto já existia e ninguém via: cada passo da
`kb/` declara uma **armadilha** — "o jeito de dar errado que só se aprende
apanhando". São **270**. O `TREINAMENTO.py` as organiza numa trilha (fundamentos
→ espaço/recuperabilidade → disponibilidade → integridade) e marca visualmente o
que é leitura, o que muda o ambiente e o que é destrutivo.

A segunda fonte é mais valiosa e depende de você: o **raciocínio** dos casos
reais, principalmente a **hipótese descartada e o motivo**. Procedimento se copia
da KB; *por que a hipótese óbvia estava errada* só existe se alguém registrou no
`CASO.py` durante o incidente. Por isso `--cobertura` mede isso e a apostila
**não finge** ter material que não tem — se a seção sair vazia, ela ensina a
registrar em vez de sumir em silêncio.

Saída em `output/treinamento/`. Offline. O padrão é **uso interno** (mantém nome
de servidor, que é o que torna o material reconhecível); use `--publico` quando
o material atravessar a fronteira da empresa.

### A KB é multiplataforma

Cada playbook declara `"plataforma"` (ausente = `oracle`, por compatibilidade):
`oracle`, `mssql`, `db2`, `postgres`, `mysql`, `redis`, `mongodb`. O **framework
de coleta continua só de Oracle** — o que muda é que um caso de SQL Server ou
Db2 agora vira **playbook reutilizável** e entra na apostila daquela tecnologia.

Três travas que isso exigiu, e o porquê de cada uma:

| Trava | Sem ela |
|---|---|
| `kb.buscar(..., plataforma=)` | "deadlock" não existe em nenhum playbook Oracle (lá o assunto é `ORA-00060`), então o playbook de SQL Server virava a **única** resposta num caso Oracle |
| `erros_ora` proibido fora do Oracle | o erro 1205 do SQL Server seria normalizado como `ORA-01205` e apareceria para quem pesquisasse esse ORA- |
| `sugerir_para_secao` só devolve Oracle | as seções vêm do relatório, que só existe para Oracle — sugerir T-SQL ali mandaria rodar T-SQL num banco Oracle |

O contrato de `risco: leitura` vale para todas: `VALIDAR_KB.read_only()` conhece
os casos em que o verbo inicial **mente** — `CONFIG GET` lê e `CONFIG SET`
escreve; `DBCC SQLPERF` lê e `DBCC SHRINKFILE` fragmenta o índice inteiro.
`TestContratoReadOnly` audita essa lista com 13 comandos que devem passar e 18
que devem ser reprovados.

### Nível de verificação — a KB não promete o que não provou

```powershell
.\.venv\Scripts\python.exe VALIDAR_KB.py --cobertura            # quanto foi conferido
.\.venv\Scripts\python.exe VALIDAR_KB.py --conn APPHO600 --marcar  # carimba o que rodou
```

Cada playbook declara `verificado`: `estrutura` (contrato offline apenas — SQL
nunca executado), `homologacao` (SQL de leitura rodou contra banco real) ou
`producao` (usado em caso real). **Ausente = `estrutura`**: silêncio significa
"não conferido", senão o campo não protege ninguém.

O nível `homologacao` **não se escreve à mão** — quem carimba é o
`VALIDAR_KB.py --conn <banco> --marcar`, e só nos playbooks cujo SQL rodou sem
falha. Assim o campo não vira promessa.

Isso existe porque a KB passou a conter procedimento que **esta máquina não tem
como executar**: não há Docker, `psql`, `redis-cli`, `sqlcmd`, `mysql` nem `db2`
aqui, e o único driver instalado é o `oracledb`. Tratar texto bem fundamentado e
consulta testada como a mesma coisa repetiria em escala o que o cabeçalho do
`VALIDAR_KB.py` registra: **8 de 110 consultas Oracle**, escritas com cuidado por
quem conhecia o assunto, tinham coluna inexistente ou erro de sintaxe.

A apostila reflete isso: o cabeçalho diz a proporção real de temas verificados e
cada tema não verificado traz o aviso **antes** do procedimento, com o motivo.

### Bateria de homologação (a única que conecta, e só quando você pede)

```powershell
.\.venv\Scripts\python.exe TESTE_HOMOLOG.py --file servidores.txt --comparar
.\.venv\Scripts\python.exe TESTE_HOMOLOG.py APPHO600 --rapido   # um servidor
```

Roda a coleta read-only em todos os servidores da lista. A senha é pedida **uma
vez** e vai a cada servidor pelo stdin — nunca por linha de comando nem para o
disco. `--advisor` não existe aqui e há `assert` no código barrando.

Ela fica **fora do painel**, de propósito: dispara dezenas de conexões e só deve
rodar quando você pedir, no terminal.

Ao final, roda sozinha a **auditoria de diagnóstico** — os mesmos invariantes que
a suíte aplica, agora sobre a evidência recém-coletada (código ORA vindo do título
em vez do dado, espera sustentando falta de espaço, plano lido como tabela, leitor
que explode, evidência de coleta anterior). O resultado sai na tela e no
`output\homolog\RESUMO.md`. Use `--sem-auditoria` se houver pressa.

Status por servidor: `OK`, `CHECK` (query quebrou ou estourou timeout — falta
evidência no relatório), `ARTEFATO`, `FALHA`, `BUG` (traceback nosso) e
`INALCANCAVEL` (DNS/rota/VPN — ambiente, não conta como problema e não reprova a
bateria). Credencial recusada **para na hora**, para não bloquear a conta.

Para o deck usar o logo **em imagem**, copie os arquivos para `assets\` (nomes
aceitos em `assets\LEIA-ME.md`); sem eles a marca é desenhada em vetor.

---

## 10. Persona DBA Master

| Comando | O que faz |
|---|---|
| `/parra` | Entra no modo DBA Master (read-only primeiro, uma hipótese por vez). |
| `novo` / `coletar` / `proximo` | Inicia caso, pede checklist de coleta, descarta a hipótese atual. |
| `redija` | Resumo no Template Padrão (Diagnóstico + RCA). |
| `redija -completo` | Dashboard + PPTX + relatório (`.devin\workflows\redija-completo.md`). |
| `detalhar` | RCA profundo da causa raiz. |

---

## 11. Resumo: o que escreve e o que não escreve

| Comando | Acessa banco | Escreve | Consome pack |
|---|---|---|---|
| `verificar.bat` | Não | Não | Não |
| `TROUBLESHOOT.py`, `redija.bat` | Não | Não | Não |
| `GUIA.py`, `CASO.py` (inclusive `--saida`) | Não | Não | Não |
| `GUIA.py --executar` / `--executar-tudo` | Sim | Não | Conforme licença (`--pack`, padrão `auto`) |
| `PAINEL.py` / `painel.bat` | Não (só dispara os comandos acima) | Não | Não |
| `MELHORIA.py`, `APRESENTAR.py`, `VALIDAR_KB.py` | Não | Não | Não |
| `COLETAR.py --parcial` | Sim | Não | Não |
| `COLETAR.py` (padrão) | Sim | Não | Diagnostics/Tuning conforme licença |
| `DETALHAR_SQL.py`, `JANELA.py` | Sim | Não | Diagnostics |
| **`COLETAR.py --advisor`** | Sim | **Sim** | **Tuning Pack** |

Toda sessão de coleta abre com `SET TRANSACTION READ ONLY` e é identificada no
banco como `TROUBLESHOOT-RO` (`dbms_application_info`). O advisor usa uma conexão
separada, marcada como `TROUBLESHOOT-ADVISOR`.
