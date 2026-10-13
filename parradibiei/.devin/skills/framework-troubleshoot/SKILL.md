---
name: framework-troubleshoot
description: Comandos do Framework de Troubleshooting Oracle deste projeto - traduz o pedido em linguagem natural (coletar, healthcheck, redija, detalhar, hipotese, fim do dia, painel, inventario) para o comando certo e executa
triggers:
  - user
  - model
---

# Framework de Troubleshooting Oracle — comandos

Sempre que o usuário pedir para **coletar**, **healthcheck**, **redija**, **detalhar**,
registrar hipótese/ação ou consultar o inventário, traduza a frase para o comando
correto e execute diretamente.

Use sempre os **aliases `.bat`** da raiz (mais curtos e já apontam para o `.venv`),
com o diretório de trabalho na **raiz do projeto** (a pasta que contém `COLETAR.py`).

## Caso novo — topologia primeiro, SEMPRE

**Regra do usuário (28/09/2026), vale para todo incidente.** Antes de medir o
sintoma e antes de propor qualquer coisa, estabeleça o ambiente: papel
(primary/standby), RAC ou single, CDB/PDB, standby e Broker, quem divide o host.

Comece pelo que é offline e já pode estar mapeado:

```powershell
.\.venv\Scripts\python.exe AMBIENTES.py <AMBIENTE>     # criticidade, versão, RAC/CDB, notas
.\.venv\Scripts\python.exe AMBIENTES.py --hosts        # quem divide host com quem
```

Se não estiver no inventário, colete (read-only) e **registre depois** com
`AMBIENTES.py --registrar` + `--nota`:

```sql
SELECT name, db_unique_name, database_role, open_mode, log_mode, cdb,
       protection_mode, force_logging, flashback_on FROM v$database;
SELECT inst_id, instance_name, host_name, status FROM gv$instance ORDER BY inst_id;
SELECT con_id, name, open_mode FROM v$pdbs ORDER BY con_id;
SELECT dest_id, dest_name, status, target, error FROM v$archive_dest_status
 WHERE status <> 'INACTIVE';
SELECT name, value FROM v$parameter
 WHERE name IN ('cluster_database','dg_broker_start','log_archive_config','fal_server');
```

Por que isso não é formalidade: o mesmo `shutdown` que para uma instância para o
cluster inteiro; o mesmo `NOARCHIVELOG` que é escolha legítima em
desenvolvimento é impossível com Data Guard. **A topologia muda o significado do
sintoma e o alcance da ação** — sem ela, o diagnóstico acerta por sorte.

## Caso novo — a abertura tem três comandos, nesta ordem

Quando chegar um chamado/alerta novo (**"novo INCxxxxx ..."**), não improvise:
não leia o JSON do playbook à mão, e não monte o bloco de SQL de cabeça. Isso
já existe pronto, com "o que olhar" e a **armadilha** de cada passo.

```powershell
# 1. registra o caso com o TEXTO do alerta e o host (o host define o rigor)
.\.venv\Scripts\python.exe CASO.py <CASO> --evento "<titulo do alerta>" ^
    --hostname <host ou banco> --status Investigando

# 2. o que o texto do alerta sugere (pista, nao diagnostico)
.\.venv\Scripts\python.exe GUIA.py <CASO> --sugerir

# 3. conduz passo a passo, com risco declarado
.\.venv\Scripts\python.exe GUIA.py <CASO> --iniciar <playbook>
```

- **`--hostname` no passo 1 não é opcional.** Sem ele a criticidade é `?`, e `?`
  significa *pergunte antes de agir*. O `CASO.py` avisa, mas o certo é já
  registrar: é a criticidade que decide se o próximo passo precisa de janela.
- **`--playbooks`** lista o catálogo inteiro por plataforma, sem precisar de caso:
  `GUIA.py --playbooks`. Use quando o sintoma não estiver claro — playbook que
  ninguém sabe que existe não é usado.
- **Não conclua pela lista do `--sugerir`.** Ela casa palavras do alerta, e o
  texto do alerta descreveu outra coisa em **4 dos 5 casos** de 22 a 24/09/2026.
  O passo 1 de todo playbook é medição read-only — é ela que decide.

## Coleta e relatório

- **healthcheck / health check / checar saúde / verificar <servidor>**  
  `.\healthcheck.bat <SERVIDOR>`  
  (equivale a `COLETAR.py HC_<SERVIDOR> --conn <SERVIDOR> --completo`)  
  Coleta completa + relatório. Leva ~7s.

- **triagem rápida / rápido / só o essencial <servidor>**  
  `.\healthcheck.bat <SERVIDOR> --rapido`  
  Pula os checks de histórico AWR (`DBA_HIST_*`) e o ADDM. Leva ~3s.
  Use quando o pedido for urgente ou for só um "está tudo bem?".

- **coletar / rodar COLETAR <caso> em <servidor>**  
  `.\coletar.bat <caso> --conn <servidor> --completo`

- **redija / redija -completo / gerar relatório <caso>**  
  `.\redija.bat <caso>`

  **PREFERÊNCIA DO USUÁRIO (23/09/2026): o "redija" tem de ser CURTO.**
  Mantenha todos os campos do Template Padrão — eles são o formato do chamado —
  mas escreva cada um em 1 a 3 linhas. O texto vai para um chamado que outras
  pessoas leem com pressa; parágrafo longo não é lido.
  - `Dados coletados` e `Ação Support Platform`: só os números e horários que
    sustentam a conclusão, em linhas curtas. Não transcreva a coleta inteira.
  - `Timeline`: uma linha por evento, `HH:MM - fato`.
  - O detalhe fica no `meta.json` do caso, que já guarda tudo. O chamado é resumo.

- **listar conexões / mostrar conexões / Parra.json**  
  `.\coletar.bat --list-conns`

- **detalhar / analisar / explicar <caso>**  
  Leia `output/troubleshooting/<caso>/<caso>_relatorio.md` (e o
  `<caso>_findings.csv`, se precisar do detalhe) e explique: Health Score,
  top findings, causa raiz e próximos passos. **Não recolete** se as evidências
  já existirem em `troubleshooting/<caso>/`.

## Drill-down

- **detalhar o sql <sql_id> / analisar o SQL <sql_id> [no caso <caso>]**  
  `.\.venv\Scripts\python.exe DETALHAR_SQL.py <caso> <sql_id> --conn <servidor>`  
  Detalhamento read-only de um SQL_ID. É o "detalhar" da persona DBA Master
  aplicado a um SQL específico — use quando houver um SQL_ID em mãos.

- **analisar a janela das HH / o pico das HH / entre HH e HH [no caso <caso>]**  
  `.\.venv\Scripts\python.exe JANELA.py <caso> --inicio "YYYY-MM-DD HH:MI" --fim "YYYY-MM-DD HH:MI" --conn <servidor>`  
  Análise read-only de uma janela de tempo pelo ASH histórico (exige
  Diagnostics Pack). Use quando o achado apontar uma hora específica.

  Nesses dois, se o usuário não disser o servidor, **leia
  `troubleshooting/<caso>/meta.json`** (campo `conn`) em vez de perguntar: o
  `COLETAR.py` grava ali o banco de origem das evidências. O dashboard do caso
  também já mostra esses comandos prontos, em "Próximo passo", dentro do
  Guia de Sugestões.

## Ciclo de vida do caso

- **hipotese / minha hipotese e ... / proximo (descartar a hipotese) <caso>**  
  `.\.venv\Scripts\python.exe CASO.py <caso> --hipotese "..." [--motivo "..."]`  
  Registra a hipotese atual; a anterior vai para o historico de descartadas
  (e o comando **proximo** da persona). Aparece no relatorio e no `index.html`.

- **registrar acao / fiz ... / anotar o que foi feito <caso>**  
  `.\.venv\Scripts\python.exe CASO.py <caso> --acao "..." --risco nenhum|baixo|medio|alto`  
  Use `--risco alto` para acao critica (kill, flush, rebuild, shrink, drop).

- **escalar / status / resolver o caso <caso>**  
  `.\.venv\Scripts\python.exe CASO.py <caso> --status Escalado --escalar "<time>"`  
  `.\.venv\Scripts\python.exe CASO.py <caso> --resolver --causa "..." --solucao "..." --prevencao "..."`  
  Sem argumentos (`CASO.py <caso>`) so mostra o estado atual do caso.

- **fim do dia / consolidar os casos**  
  `.\.venv\Scripts\python.exe CASO.py --fim-do-dia [--dia YYYY-MM-DD]`  
  Gera `output/troubleshooting/consolidado_<data>.csv` (abre no Excel pt-BR).

## Inventário de frota

- **quais ambientes eu tenho / o que sei do <servidor> / quem divide host**  
  `.\.venv\Scripts\python.exe AMBIENTES.py` (frota) · `AMBIENTES.py <SERVIDOR>` (um)  
  `.\.venv\Scripts\python.exe AMBIENTES.py --hosts` (bancos no mesmo host)  
  `.\.venv\Scripts\python.exe AMBIENTES.py --atualizar` (regera dos `topology.json`)  
  `.\.venv\Scripts\python.exe AMBIENTES.py --nota <SERVIDOR> "..."` (o que nenhum SELECT descobre)  
  Offline. Consulte **antes** de perguntar ao usuário se um banco é RAC, CDB ou
  qual a versão — pode já estar registrado.

## Treinamento para os outros DBAs

- **treinamento / apostila / material para o time / quiz**  
  `.\.venv\Scripts\python.exe TREINAMENTO.py` (apostila completa)  
  `TREINAMENTO.py --plataforma mssql` (apostila de uma tecnologia)  
  `TREINAMENTO.py --tema <assunto>` · `--quiz` · `--cobertura` · `--publico`  
  Monta o material a partir das **armadilhas** da `kb/` (270) e do raciocínio
  registrado nos casos. Offline, sai em `output/treinamento/`.  
  Use `--publico` **sempre que o material sair da empresa** — sem ele, nome de
  servidor e de empresa vão junto. Se o usuário pedir "para mandar para
  fulano de fora" ou "para a Oracle", assuma `--publico`.  
  `--cobertura` responde "quanto do nosso raciocínio está sendo perdido" — vale
  citar quando o usuário fechar um caso sem registrar hipótese.

## Tela e manutenção

- **abrir o painel / interface / tela / GUI**  
  `.\painel.bat` (nao bloqueante: e um servidor, fica rodando)  
  Abre `http://127.0.0.1:8765/`. Serve para o usuario disparar coleta e conduzir
  o caso sem digitar comando. **Nao substitui** estes comandos: continue usando
  os `.bat` quando VOCE for executar algo. No painel, `--advisor` so existe
  como opt-in confirmado do modo Completo.

- **bateria / testar nos servidores de homologacao**  
  `.\.venv\Scripts\python.exe TESTE_HOMOLOG.py --file servidores.txt --comparar`  
  Read-only, nunca roda `--advisor`. Servidor novo **entra acrescentando** em
  `servidores.txt` (arquivo vivo) - nunca substitua a lista.

- **validar a KB / melhoria continua**  
  `.\.venv\Scripts\python.exe VALIDAR_KB.py` · `MELHORIA.py --corpus`  
  Ambos offline. O corpus reanalisa evidência já gravada e acusa se uma mudança
  de código alterou o diagnóstico de um caso já entregue.

## A única exceção que escreve no banco

- **rodar o advisor / tuning advisor / tunar o SQL <caso>**  
  `.\coletar.bat <caso> --conn <servidor> --advisor`  
  **PEÇA CONFIRMAÇÃO ANTES.** É o único comando do projeto que **não é
  read-only**: cria e executa tuning tasks (`DBMS_SQLTUNE`) e **consome Tuning
  Pack**. Ao confirmar, avise que pode demorar (até 5 min por SQL) e que ele só
  roda nos SQL elegíveis — troca de plano ou impacto ≥ 300s. As recomendações
  saem em `advisor.txt` e são anexadas ao `acoes.sql`, **comentadas**.  
  Não sugira por conta própria: só quando o usuário pedir, ou quando o
  relatório trouxer o bloco "Investigação avançada (escreve no banco)".

## Quando o caso NÃO é Oracle

A coleta automática (`COLETAR.py`, `TROUBLESHOOT.py`, `AMBIENTES.py`) é só de
Oracle — não tente usá-la em SQL Server, Db2, PostgreSQL, Redis ou MongoDB.
O que continua valendo para qualquer tecnologia:

- **`CASO.py`** — cria o caso do zero, sem coleta prévia. É assim que caso de
  outra plataforma (ou Oracle conduzido por copiar-e-colar) entra na base.
- **`kb/*.json` com `"plataforma"`** — o aprendizado vira playbook reutilizável.
  Códigos de erro de outras plataformas vão em `tags`, **nunca** em `erros_ora`
  (esse campo é normalizado como `ORA-xxxxx`).
- **`TREINAMENTO.py --plataforma <tec>`** — a apostila daquela tecnologia.

Ao pesquisar a KB durante um caso, **passe a plataforma**:
`kb.buscar(termo, plataforma="oracle")`. Sem isso, termo que só existe em outra
tecnologia devolve o playbook errado — e o próximo passo seria rodar o comando
de outro banco no banco do cliente.

## Regras de execução

1. **Caminhos**: rode sempre com o diretório de trabalho na raiz do projeto.
   Os `.bat` já resolvem o `.venv` internamente.

2. **Senha: você NÃO consegue rodar nada que conecte no banco.**  
   A senha é digitada pelo humano a cada execução, por decisão de segurança de
   02/08/2026: não há `--password`, não há arquivo e **`ORA_PASSWORD` não é
   mais lida** (ela era gravada em texto puro no registro do Windows). Um teste
   reprova qualquer CLI que volte a lê-la.
   - Na prática: `COLETAR.py`, `healthcheck.bat`, `DETALHAR_SQL.py`,
     `JANELA.py`, `TESTE_HOMOLOG.py` e `VALIDAR_KB.py --conn` **só rodam no
     terminal do usuário ou pelo painel**.
   - **Não tente executá-los você mesmo.** O guarda `senha.ha_como_perguntar()`
     usa `isatty()`, que devolve `True` numa sessão de agente com PTY — então
     o processo **não falha rápido, ele trava** no prompt invisível. Já
     aconteceu (18/09/2026).
   - O certo é montar o comando e **pedir ao usuário que rode**, ou sugerir o
     painel (`.\painel.bat`), que pergunta numa caixa mascarada.
   - Tudo que é offline (`TROUBLESHOOT.py`, `CASO.py`, `AMBIENTES.py`,
     `HISTORICO.py`, `TREINAMENTO.py`, `VALIDAR_KB.py` sem `--conn`,
     `MELHORIA.py`) você roda à vontade.

3. **Nunca rode comando interativo sem os dados**: sempre passe `--conn` (ou
   `--host`/`--service`) e o nome do caso na linha de comando. Rodar
   `COLETAR.py` sem argumentos em contexto automatizado é erro.

4. **Se estiver lento**: as flags de desempenho são
   `--rapido` (pula histórico AWR/ADDM), `--workers N` (paralelismo, padrão 4)
   e `--timeout S` (limite por query, padrão 120s). O log imprime
   `[tempo] coleta em Xs | mais lentos: ...` — use isso para diagnosticar.

5. **Typos/abreviações**: normalize termos como `healthycjeck`, `reddija`,
   `dbsrv600`, `conexoes`, `saude` etc. para os padrões acima.

6. **Ciclo de vida**: ao conduzir um caso, registre no `CASO.py` a hipotese e as
   acoes conforme forem acontecendo — o relatorio e o `index.html` leem o
   `meta.json` ao vivo. Uma hipotese por vez (Method R).

7. **Após rodar**: resuma em português:  
   - Health Score (se houver)  
   - Top 3 findings e severidade  
   - Causa raiz provável  
   - Próximo passo recomendado

## Exemplos de frases

- "rodar healthcheck para servidor dbsrv600" → `.\healthcheck.bat DBSRV600`
- "healthcheck rapido no dbsrv600" → `.\healthcheck.bat DBSRV600 --rapido`
- "redija teste" → `.\redija.bat teste`
- "coletar INC000111222 em DBSRV026" → `.\coletar.bat INC000111222 --conn DBSRV026 --completo`
- "minha hipotese e que o IO do ASM esta saturado, caso INC000111222"  
  → `CASO.py INC000111222 --hipotese "IO do ASM saturado"`
- "fim do dia" → `CASO.py --fim-do-dia`
- "esse banco e RAC?" → `AMBIENTES.py <SERVIDOR>` antes de perguntar
- "abrir o painel" / "quero a tela" → `.\painel.bat` (avise a URL e que a janela fica ocupada)
