"""
raciocinio.py - hipoteses concorrentes, pontuadas pela evidencia coletada.

O que este modulo faz que o `guia.py` nao fazia: em vez de escolher UM ramo e
seguir, ele mantem **varias hipoteses vivas ao mesmo tempo**, pontua cada uma
com a evidencia que existe, e - o que mais importa - **refuta**. A frase que
resume o metodo esta no prompt do DBA Master: *uma hipotese por vez, testada
com dados; se os dados nao confirmam, avance*. Avancar exige saber o que NAO
confirma, e isso e a metade que faltava.

Por que determinístico, e nao um modelo de linguagem
----------------------------------------------------
O que faz um diagnostico ser bom aqui nao e fluencia: e o encadeamento
hipotese -> evidencia -> confirmacao ou descarte. Isso e regra, e regra tem
tres propriedades que nenhum modelo local entrega: **reproduz** (mesmo caso,
mesma resposta), **audita** (da para apontar o achado que sustentou cada
conclusao) e **nao inventa** nome de view, parametro ou comando. Num framework
cuja regra numero um e "nao de palpite sem evidencia", um palpite fluente e
pior que nenhum palpite.

Os estados que importam
-----------------------
- **sustentada** - ha achado grave que so aquela causa produz;
- **consequencia** - o sinal existe, mas outra causa ja explica. E a
  distincao que separa o DBA senior do classificador: I/O alto sob uma
  regressao de plano nao e problema de storage, e o plano lendo demais.
  Trocar disco nesse caso e gastar sem resolver;
- **refutada** - ha achado que a EXCLUI. Vale tanto quanto confirmar:
  elimina caminho e encurta o incidente;
- **sem evidencia** - o caso nao respondeu. Nao e falha do motor. "Sem wait
  dominante e mesmo assim lento" e um diagnostico legitimo, e manda olhar
  rede, cliente e aplicacao - fora do banco.

O que para o banco vem antes do que o deixa lento
-------------------------------------------------
Cada hipotese declara uma `classe`: **parada** (o banco deixa de aceitar
transacao), **perda** (recuperabilidade comprometida) ou **performance**. Um
chamado aberto por lentidao nao pode terminar sem alguem ver que aquele banco
esta em NOARCHIVELOG - por isso o risco estrutural sai destacado na narrativa,
separado do incidente que motivou a chamada.

O motor le SO o `<caso>_findings.json` que a analise ja gravou. Nao reanalisa,
nao abre conexao e nao executa nada.
"""
from __future__ import annotations

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")

# Peso por severidade do achado que sustenta (ou refuta) a hipotese.
# INFO nao decide nada: um achado informativo pode aparecer em banco saudavel,
# e deixar 'info' votar encheria a lista de hipoteses plausiveis e inuteis.
PESO_SEV = {"crit": 3, "warn": 2, "info": 0, "ok": 0}

# A partir de quantos pontos a hipotese e considerada SUSTENTADA. Tres = um
# achado critico, ou dois de atencao: abaixo disso e "possivel", nao "e isto".
CORTE_SUSTENTADA = 3

VEREDITOS = ("sustentada", "possivel", "consequencia", "refutada",
             "sem_evidencia")

# Classe de risco. Governa o DESTAQUE, nao a pontuacao: evidencia continua
# sendo evidencia. Serve para o caso aberto por lentidao nao terminar sem
# ninguem notar que o banco esta sem backup ha 30 dias.
CLASSE_PARADA = "parada"          # o banco deixa de aceitar transacao
CLASSE_PERDA = "perda"            # recuperabilidade comprometida
CLASSE_PERF = "performance"       # esta lento, mas esta de pe


def _norm(txt: str) -> str:
    """Texto do achado em minusculas, para casar marca."""
    return " ".join(str(txt or "").lower().split())


def _texto(r: dict) -> str:
    """O que foi MEDIDO no achado: titulo e impacto.

    Secao e recomendacao ficam de fora, e os dois motivos vieram do primeiro
    caso real (HC_APPHO114, 11/08):

    - a **recomendacao** fala do que CONFERIR, nao do que foi observado. Um
      achado sobre idade de backup recomendava "...confira falhas em
      v$rman_backup_job_details e o uso da FRA" - e a palavra FRA ali fez o
      motor sustentar "FRA sem espaco" num banco onde ninguem mediu a FRA;
    - a **secao** e generica por natureza ("Backups (RMAN) e archivelog"
      cobre dois assuntos). Foi o mesmo defeito que ja tinha mordido a
      sugestao de playbook.

    Evidencia e o que o analisador mediu. O resto e contexto.
    """
    return _norm(" ".join(str(r.get(k) or "") for k in ("title", "impact")))


# ── numeros que aparecem no texto do achado ───────────────────
_PCT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")


def percentuais(texto: str) -> list:
    """Percentuais citados no achado ('User I/O concentra 62% do DB time').

    Serve para separar "aparece" de "domina": 62% de DB time num wait class e
    uma hipotese forte; 4% e ruido que estaria presente em qualquer banco
    saudavel. Sem isto, a mesma marca textual valeria o mesmo nos dois casos.
    """
    out = []
    for m in _PCT_RE.finditer(texto or ""):
        try:
            v = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if 0 <= v <= 100:
            out.append(v)
    return out


# ── Catalogo de hipoteses ─────────────────────────────────────
# Cada entrada e uma causa candidata. Os campos existem para responder, na
# ordem, as perguntas que um DBA senior faz:
#
#   confirma  - o que eu veria SE fosse isto
#   refuta    - o que eu veria se NAO fosse isto (o descarte)
#   decide    - a evidencia que ainda falta para fechar (o proximo passo)
#   tags      - playbooks que tratam a causa (token exato, ver guia._playbooks_por_tag)
#
# 'confirma' e 'refuta' sao marcas de texto casadas contra secao+titulo+impacto
# +recomendacao do achado. Marca comprida e melhor que curta: 'io' casaria
# dentro de 'bloqueio' - foi um bug real na sugestao de playbook.
HIPOTESES = [
    {
        "id": "bloqueio",
        "rotulo": "Concorrencia: sessoes presas em lock",
        "classe": CLASSE_PERF,
        "explica": "Uma sessao segura o recurso e as outras esperam. O banco "
                   "esta saudavel em CPU e I/O: o que falta e a linha ser "
                   "liberada.",
        "confirma": ["sessao bloqueada", "bloqueio em cascata", "cadeia de bloqueio",
                     "sessoes bloqueadas", "enq: tx", "enq: tm", "row lock",
                     "deadlock", "blocking session"],
        # A ausencia tambem e evidencia: a consulta de bloqueio que volta VAZIA
        # elimina a hipotese. E o passo que parecia inutil e o que fecha o
        # caminho - por isso a frase canonica de `saida.py` entra aqui.
        # Duas formas, porque os DOIS leitores produzem a evidencia negativa
        # com palavras diferentes: `_ler_vazio` escreve "sem sessao bloqueada
        # no momento da verificacao" (consulta sem linhas) e `_ler_bloqueio`
        # escreve "nenhuma sessao bloqueada na verificacao" (coluna presente,
        # linhas vindas, ninguem apontando bloqueador - que e a prova mais
        # forte das duas).
        #
        # A segunda faltava, e o efeito foi medido: com `enq: TX` a 41% no top
        # de esperas e o passo seguinte mostrando a coluna VAZIA, a hipotese
        # continuava SUSTENTADA com `contra: []`. O achado que FECHA o caminho
        # existia, estava na tela, e o motor nao o reconhecia - defeito
        # silencioso, porque nada falha e a hipotese certa segue de pe.
        "refuta": ["sem sessao bloqueada", "nenhuma sessao bloqueada"],
        "decide": "Quem e a sessao raiz (NIVEL 1) da cadeia e ha quanto tempo "
                  "ela esta ativa sem commit.",
        "coletar": "gv$session / blocking_session (check locks_blocking)",
        "tags": ("lock", "bloqueio", "sessao", "enqueue", "kill"),
    },
    {
        "id": "io",
        "rotulo": "I/O: storage ou volume lido demais",
        "classe": CLASSE_PERF,
        "explica": "O tempo esta indo para leitura de disco. Pode ser o "
                   "storage lento ou um plano que le muito mais do que "
                   "precisa - e a diferenca entre chamar a infra e corrigir "
                   "um SQL.",
        "confirma": ["user i/o", "system i/o", "db file sequential read",
                     "db file scattered read", "direct path read",
                     "full table scan",
                     "latencia de leitura", "io por arquivo"],
        # Espera de I/O alta com latencia BAIXA por requisicao nao e storage
        # lento: e volume. O storage esta entregando; alguem esta pedindo
        # demais. Trocar disco nesse caso e gastar sem resolver.
        "refuta": ["latencia normal", "storage dentro do esperado"],
        # 'storage dentro do esperado na verificacao' (saida colada) cai na
        # marca acima por substring - nao precisa de entrada propria.
        "decide": "A latencia por requisicao esta alta (storage) ou o numero "
                  "de leituras e que subiu (plano/volume)?",
        "coletar": "v$iostat_file + top SQL por leituras fisicas",
        "tags": ("io", "plano", "tuning"),
    },
    {
        "id": "plano",
        "rotulo": "Regressao de plano de execucao",
        "classe": CLASSE_PERF,
        "explica": "O mesmo SQL passou a custar mais depois de uma troca de "
                   "plano. E a causa mais comum de 'ontem estava bom'.",
        "confirma": ["regressao", "mudou de plano", "plano regrediu",
                     "plan_hash_value", "novo plano", "troca de plano",
                     "custo por execucao"],
        # Duas refutacoes, e a segunda e a mais forte que existe aqui: se o
        # custo POR EXECUCAO nao mudou, o SQL nao piorou - mesmo que o plano
        # tenha trocado. Sem ela, uma troca de plano inofensiva (plano novo,
        # custo igual) segurava a investigacao no SQL errado.
        # "nenhuma troca de plano" e a forma do leitor que compara
        # plan_hash_value POR sql_id (`_ler_planos`); "plano estavel" e a do
        # que le um SQL so. Sem as duas, a verificacao que mostra um plano
        # unico por SQL nao descartava regressao de plano.
        "refuta": ["plano estavel", "custo por execucao estavel",
                   "nenhuma troca de plano"],
        "decide": "Qual plan_hash_value era usado antes e qual passou a ser, "
                  "e o que mudou entre os dois (estatistica, bind, indice).",
        "coletar": "DETALHAR_SQL.py <caso> <sql_id> (AWR: historico de planos)",
        "tags": ("sql", "plano", "regressao", "tuning"),
    },
    # ── Tuning: carga, custo por execucao e mudanca de aplicacao ──
    # Faltava a pergunta que muda TUDO no tuning: o banco ficou mais lento ou
    # passou a fazer mais? Sem separar as duas coisas, o mesmo sintoma
    # ("esta pesado") leva a tunar SQL que nao mudou nada - enquanto a origem
    # da carga segue crescendo.
    {
        "id": "carga",
        "rotulo": "Aumento de carga (mais trabalho, nao mais lento)",
        "classe": CLASSE_PERF,
        "explica": "Cada execucao custa o mesmo de sempre e o TOTAL dobrou. "
                   "Isso nao se resolve tunando SQL: se resolve contendo ou "
                   "dimensionando a origem da carga.",
        "confirma": ["aumento de carga", "sessoes por hora", "carga dobrou",
                     "pico de sessoes", "mais execucoes", "logons por hora",
                     "concorrencia subiu", "execucoes por hora",
                     # workload.py escreve "<modulo>: carga +94%" e
                     # "<modulo>: salto em <data>". O DOIS-PONTOS antes de
                     # "salto" nao e enfeite: sem ele casaria tambem o
                     # impacto do achado de MUDANCA ("salto em <modulo>"),
                     # que e evidencia de outra hipotese.
                     "carga +", ": salto em"],
        # Serie por hora estavel elimina a hipotese: o volume nao mudou, entao
        # o que mudou foi o CUSTO - e a investigacao vira para plano/SQL.
        "refuta": ["carga estavel"],
        "decide": "A carga por hora comparada com o mesmo periodo de antes, e "
                  "se o custo POR EXECUCAO ficou igual (carga) ou subiu "
                  "(regressao) - cole `executions` + `elapsed_time`.",
        "coletar": "sessoes/execucoes por hora (ASH ou AWR por snapshot)",
        "tags": ("carga", "sessao", "capacidade", "tuning"),
    },
    {
        "id": "mudanca_app",
        "rotulo": "Mudanca na aplicacao (deploy que ninguem avisou)",
        "classe": CLASSE_PERF,
        "explica": "SQL novo, objeto recompilado ou modulo diferente surgindo "
                   "na mesma hora do problema. Quase nunca vem no chamado - e "
                   "e a resposta com mais frequencia do que qualquer um admite.",
        "confirma": ["sql novo", "first_load_time", "last_ddl_time",
                     "objeto recompilado", "modulo novo", "deploy",
                     "versao nova da aplicacao",
                     # changes.py: "12 mudanca(s) na janela do salto de
                     # carga de 2026-09-01"
                     "mudanca(s) na janela"],
        "refuta": ["sem mudanca recente"],
        "decide": "O que entrou em producao nas ultimas 24h e se o SQL novo "
                  "aparece no topo do DB time (nao basta ser novo).",
        "coletar": "v$sql (first_load_time) + dba_objects (last_ddl_time)",
        "tags": ("aplicacao", "mudanca", "sql", "tuning"),
    },
    {
        "id": "parse",
        "rotulo": "Parse / shared pool (SQL sem bind)",
        "classe": CLASSE_PERF,
        "explica": "O banco gasta o tempo montando plano em vez de executar. "
                   "Tipico de aplicacao que concatena literal na query.",
        "confirma": ["hard parse", "sem bind", "literal", "shared pool",
                     "cursor: pin", "library cache", "parse elapsed"],
        "refuta": [],
        "decide": "A proporcao hard/soft parse e quais SQL diferem so por "
                  "literal (FORCE_MATCHING_SIGNATURE).",
        "coletar": "check parse_ratio + literais em v$sql",
        "tags": ("parse", "cpu", "shared", "cursor", "bind"),
    },
    {
        "id": "commit",
        "rotulo": "Redo / commit lento (log file sync)",
        "classe": CLASSE_PERF,
        "explica": "A aplicacao espera o commit ser gravado. Costuma ser "
                   "disco do redo, ou commit por linha em laco.",
        "confirma": ["log file sync", "log file parallel write", "commit lento",
                     "lgwr", "redo por segundo", "switch de redo",
                     # prosa dos analisadores deste framework (redo.py):
                     "redo pequeno", "trocas de redo", "log file switch",
                     "checkpoint incompleto"],
        "refuta": [],
        "decide": "O tempo esta no LGWR gravando (disco) ou na quantidade de "
                  "commits (aplicacao em laco)?",
        "coletar": "check redo_waits + taxa de commits por segundo",
        "tags": ("redo", "commit", "checkpoint", "lgwr"),
    },
    {
        "id": "archive",
        "rotulo": "Archive / FRA sem espaco (banco pode parar)",
        "classe": CLASSE_PARADA,
        "explica": "Sem espaco para archive o banco PARA de aceitar "
                   "transacao. Nao e lentidao: e indisponibilidade a caminho.",
        "confirma": ["ora-00257", "archive cheia", "fra sem espaco",
                     "fra em ", "flash recovery area", "espaco para archive",
                     "archive destination full",
                     # archive.py: "Rajada de archive: 417 MB em uma hora
                     # contra 26 MB de mediana". Volume de archive e
                     # evidencia de PRESSAO na FRA - nao prova que encheu,
                     # e por isso entra em confirma e nao fecha a hipotese.
                     "rajada de archive"],
        # Banco em NOARCHIVELOG nao gera archive: a FRA nao vai encher disso.
        # Sem este refutador o motor mandava "liberar espaco de archive" num
        # banco cujo problema e o oposto - nao ter archive nenhum.
        "refuta": ["noarchivelog", "archive/fra sem pendencia"],
        "decide": "Quanto resta na FRA e qual a taxa de geracao de archive por "
                  "hora (quanto tempo ate parar).",
        "coletar": "check archive_rate + v$recovery_file_dest",
        "tags": ("archive", "espaco", "backup"),
    },
    {
        "id": "espaco",
        "rotulo": "Espaco em tablespace / undo / temp",
        "classe": CLASSE_PARADA,
        "explica": "Objeto sem espaco para crescer derruba transacao com "
                   "erro, mesmo com o banco no ar.",
        # 'temp' NAO pode entrar solto: casa dentro de "tempo", e o impacto de
        # todo achado de espera e escrito exatamente assim ("41.2% do tempo").
        # Medido na evidencia real de APPHO600: QUALQUER espera dominante
        # sustentava "Espaco em tablespace / undo / temp" com 4 pontos - e esta
        # hipotese e classe PARADA, entao um caso de lentidao ganhava selo de
        # "PODE PARAR O BANCO" que a evidencia nao sustentava. A armadilha e
        # vocabulario tecnico em ingles (TEMP) dentro de prosa em portugues
        # (tempo); as tres formas abaixo nao a tem. Manter o espaco final em
        # 'temp ' e deliberado - e a mesma defesa que 'asm ', 'cdb ' e 'pdb '
        # ja usavam aqui.
        "confirma": ["tablespace", "sem espaco", "undo", "temp ",
                     "ora-01653", "ora-01654", "ora-30036", "ora-01555"],
        "refuta": ["espaco dentro do esperado"],
        "decide": "Qual tablespace, quanto falta e se ha autoextend com teto "
                  "no datafile.",
        "coletar": "check space_tablespace / space_datafiles",
        "tags": ("espaco", "tablespace", "undo"),
    },
    {
        # Faltava a causa que chega como "a aplicacao nao conecta" e que NAO e
        # lentidao: sem processo ou sessao livre o banco RECUSA a conexao. Sem
        # esta hipotese o caso era investigado como performance, e o relogio da
        # indisponibilidade corria enquanto se olhava wait event.
        "id": "limite",
        "rotulo": "Limite de processes/sessions (banco recusa conexao)",
        "classe": CLASSE_PARADA,
        "explica": "Encostar no limite nao deixa o banco lento: deixa o banco "
                   "INACESSIVEL para quem tenta conectar agora. O sintoma "
                   "chega pela aplicacao, nao pelo grafico de espera.",
        "confirma": ["ora-00020", "limite de processes", "limite de sessions",
                     "sem sessao disponivel", "processes esgotado",
                     "maximum number of processes",
                     # O produtor e o analisador GENERICO, sobre o
                     # resource_limits.txt: ele escreve o subtitulo do
                     # arquivo seguido do recurso -
                     # "LIMITES (PROCESSES/SESSIONS): sessions".
                     # Nenhum dos termos acima casava com isso.
                     "limites (processes"],
        # Uso longe do teto responde a pergunta e devolve a investigacao para
        # performance - onde ela provavelmente comecou.
        "refuta": ["limite de conexao com folga"],
        "decide": "Quanto falta para o teto de processes/sessions e QUEM abriu "
                  "as sessoes (pool vazando, job em loop, retry da aplicacao).",
        "coletar": "v$resource_limit + gv$session por module/machine",
        "tags": ("sessao", "recurso", "capacidade", "conexao"),
    },
    {
        "id": "cpu",
        "rotulo": "CPU saturada (host ou instancia)",
        "classe": CLASSE_PERF,
        "explica": "Nao ha espera de recurso do banco: ha fila por "
                   "processador. Ajustar SQL ainda ajuda, mas o teto e o host.",
        "confirma": ["cpu saturada", "load average", "resmgr:cpu",
                     "cpu do host", "os cpu", "run queue",
                     # operacao.py: "Host com load 7.2 para 8 CPU(s)",
                     # impacto "load/CPU 0.90". Nenhum dos termos acima
                     # casava com o proprio achado do framework.
                     "host com load", "load/cpu"],
        "refuta": [],
        "decide": "A CPU e consumida pelo banco ou por outro processo do host, "
                  "e ha mais sessao ativa que core.",
        "coletar": "check host_load + sessoes ativas por wait class",
        "tags": ("cpu", "parse", "recurso"),
    },
    {
        "id": "backup",
        "rotulo": "Recuperabilidade comprometida (backup falhando)",
        "classe": CLASSE_PERDA,
        "explica": "Nao afeta a performance de hoje e e o achado mais grave "
                   "que existe: sem backup valido, qualquer outro incidente "
                   "vira perda de dado.",
        "confirma": ["backup falhou", "rman", "sem backup", "backup mais antigo",
                     "recuperabilidade", "noarchivelog", "rpo de"],
        "refuta": [],
        "decide": "Data do ultimo backup FULL valido e se o archive necessario "
                  "ainda existe.",
        "coletar": "check rman_backups / v$rman_status",
        "tags": ("backup", "rman", "recuperabilidade"),
    },

    # ── Ambientes: RAC, ASM, Data Guard, multitenant, Exadata ──
    # Ate aqui o catalogo so enxergava single-instance. Num parque com RAC e
    # standby, o mesmo sintoma ("commit lento") tem causa DIFERENTE - e quem
    # conduz precisa ver a hipotese certa oferecida, nao deduzi-la sozinho.
    {
        "id": "rac_interconnect",
        "rotulo": "RAC: interconnect / cache fusion",
        "classe": CLASSE_PERF,
        "explica": "O bloco esta viajando entre instancias em vez de ser lido "
                   "local. Costuma ser rede de interconnect, ou a mesma tabela "
                   "quente sendo escrita nos dois nos ao mesmo tempo.",
        "confirma": ["gc buffer busy", "gc cr block", "gc current block",
                     "gc cr multi block", "interconnect", "cache fusion",
                     "gcs log flush", "cluster wait"],
        "refuta": ["instancia unica", "single-instance", "nao rac"],
        "decide": "A latencia do interconnect esta alta (rede) ou o mesmo "
                  "objeto e escrito nos dois nos (afinidade de servico)?",
        "coletar": "gv$instance_cache_transfer + servico por no",
        "tags": ("rac", "cluster", "interconnect"),
    },
    {
        "id": "rac_no",
        "rotulo": "RAC: no fora do cluster ou servico desbalanceado",
        "classe": CLASSE_PERF,
        "explica": "Com um no fora, a carga inteira cai no que sobrou - e o "
                   "banco 'fica lento' sem nada ter mudado na aplicacao.",
        "confirma": ["fora do cluster", "instancia fora", "no do cluster",
                     "crs ", "evicted", "instancia inativa",
                     "servico sem instancia", "reconfiguration",
                     # topologia.py escreve "2 de 3 instancia(s) do
                     # cluster fora do ar" e "Carga concentrada na
                     # instancia 1: 98% das sessoes". O vocabulario
                     # dizia "fora do cluster" e o analisador escreve
                     # "do cluster fora do ar" - perto, e sem casar.
                     "cluster fora do ar", "instancia no ar", "instancia(s) no ar",
                     "carga concentrada na instancia"],
        # `gv$instance` com TODAS em OPEN responde a pergunta desta hipotese.
        # Sem isto, um caso de RAC saudavel ficava carregando "no fora do
        # cluster" como possibilidade ate o fim do plantao.
        "refuta": ["instancias todas abertas"],
        "decide": "Todas as instancias estao ABERTAS e o servico esta "
                  "distribuido conforme o esperado?",
        "coletar": "gv$instance + srvctl status service",
        "tags": ("rac", "cluster", "servico"),
    },
    {
        "id": "asm",
        "rotulo": "ASM: diskgroup sem espaco ou em rebalance",
        "classe": CLASSE_PARADA,
        "explica": "Diskgroup cheio impede o datafile de crescer, e rebalance "
                   "em curso concorre por I/O com a carga do banco.",
        "confirma": ["diskgroup", "asm ", "rebalance", "ora-15041",
                     "disco offline", "redundancia"],
        "refuta": [],
        "decide": "Espaco livre USavel no diskgroup (nao o bruto) e se ha "
                  "rebalance em andamento.",
        "coletar": "v$asm_diskgroup (usable_file_mb) + v$asm_operation",
        "tags": ("asm", "espaco", "storage"),
    },
    {
        "id": "dataguard",
        "rotulo": "Data Guard: standby atrasado ou com gap",
        "classe": CLASSE_PERDA,
        "explica": "O standby que nao aplica nao protege. O gap so aparece no "
                   "dia do failover, quando ja e tarde para descobrir.",
        "confirma": ["gap de archive", "standby", "apply lag", "transport lag",
                     "mrp", "ora-16", "data guard", "dataguard"],
        "refuta": ["standby sem gap"],
        "decide": "Qual o apply lag e o transport lag agora, e se o MRP esta "
                  "rodando no standby.",
        "coletar": "v$dataguard_stats + v$managed_standby",
        "tags": ("standby", "dataguard", "replicacao", "ha"),
    },
    {
        # A medicao de cobertura expos esta lacuna: existia hipotese para
        # Data Guard e para Exadata, e NAO para GoldenGate - embora o
        # framework tenha tres checks de GG, um analisador proprio e um
        # playbook (`golden_gate`). Na pratica, GG parado aparecia no
        # dashboard e NAO chegava ao raciocinio: o guiado nunca apontava para
        # la, porque o playbook so podia ser alcancado por casamento de secao.
        #
        # Classe PERDA, igual ao Data Guard e pelo mesmo motivo: replicacao
        # parada nao deixa o banco lento, deixa o DESTINO divergente. O dano
        # aparece no dia do switch, quando ja e tarde para descobrir.
        "id": "goldengate",
        "rotulo": "GoldenGate: replicacao parada ou atrasada",
        "classe": CLASSE_PERDA,
        "explica": "Capture ou apply fora de ENABLED significa destino "
                   "divergindo da origem. Nao ha sintoma no banco de origem - "
                   "e por isso que passa semanas sem ninguem notar.",
        # 'goldengate' cobre os achados de processo ("N processo(s) de capture
        # do GoldenGate fora de ENABLED"); 'lag de apply' cobre o de atraso.
        #
        # A ordem das palavras NAO e detalhe: o Data Guard escreve "apply lag
        # elevado" e o GoldenGate "Lag de apply de N min". As duas marcas sao
        # distintas de proposito - trocar por um 'apply' solto faria achado de
        # Data Guard votar em GoldenGate e vice-versa, que e a armadilha de
        # marca curta que ja custou caro neste catalogo (ver 'temp'/"tempo").
        "confirma": ["goldengate", "golden gate", "lag de apply",
                     "ogg-", "extract", "replicat", "abend"],
        "refuta": ["capture, apply e lag sem alerta"],
        "decide": "Capture e apply estao ENABLED e qual o lag de apply agora; "
                  "se houver erro, o numero em DBA_APPLY_ERROR.",
        "coletar": "dba_capture + dba_apply + dba_apply_progress "
                   "(checks gg_capture / gg_apply / gg_lag)",
        "tags": ("goldengate", "golden gate", "replicacao", "ogg", "lag"),
    },
    {
        "id": "memoria",
        "rotulo": "Memoria: SGA/PGA insuficiente (ORA-04031 / swap)",
        "classe": CLASSE_PARADA,
        "explica": "Sem memoria contigua o banco recusa alocacao e derruba "
                   "sessao. Com swap no host, tudo fica lento por igual.",
        "confirma": ["ora-04031", "ora-04030", "swap", "shared pool sem",
                     "pga excede", "memory_target", "sga_target",
                     "memoria insuficiente",
                     # operacao.py: "Host com 2.6% de memoria livre"
                     "memoria livre", "ja paginou"],
        "refuta": [],
        "decide": "Qual componente estourou (shared pool, large pool, PGA) e "
                  "se o host esta em swap.",
        "coletar": "v$memory_resize_ops + v$pgastat + vmstat do host",
        "tags": ("memoria", "sga", "pga", "shared"),
    },
    {
        "id": "estatisticas",
        "rotulo": "Estatisticas velhas ou ausentes",
        "classe": CLASSE_PERF,
        "explica": "O otimizador decide com o que sabe. Estatistica velha "
                   "produz plano ruim com aparencia de bug - e e a causa mais "
                   "comum ATRAS de uma regressao de plano.",
        "confirma": ["estatistica", "stale", "sem estatistica", "dbms_stats",
                     "gather", "coleta de estatisticas", "num_rows"],
        # `last_analyzed` de ontem descarta a hipotese - e o que evita mandar
        # rodar DBMS_STATS num banco que acabou de coletar.
        "refuta": ["estatisticas recentes"],
        "decide": "Quando a tabela envolvida foi analisada pela ultima vez e "
                  "quanto ela mudou desde entao (stale_percent).",
        "coletar": "dba_tab_statistics (last_analyzed) + dba_tab_modifications",
        "tags": ("estatisticas", "stats", "plano", "tuning"),
    },
    {
        "id": "multitenant",
        "rotulo": "Multitenant: PDB ou Resource Manager limitando",
        "classe": CLASSE_PERF,
        "explica": "A PDB pode estar sendo contida por plano de recurso do "
                   "CDB. O SQL nao mudou: a fatia dele encolheu.",
        "confirma": ["resmgr:", "resource manager", "pdb ", "cdb ",
                     "container", "cpu_count da pdb", "plano de recurso"],
        "refuta": ["nao-cdb", "non-cdb"],
        "decide": "Ha plano de recurso ativo no CDB e a PDB esta batendo no "
                  "limite de CPU/sessao?",
        "coletar": "v$rsrc_consumer_group + dba_cdb_rsrc_plans",
        "tags": ("pdb", "multitenant", "recurso", "cdb"),
    },
    {
        "id": "exadata",
        "rotulo": "Exadata: offload / smart scan nao acontecendo",
        "classe": CLASSE_PERF,
        "explica": "Sem smart scan a celula devolve o bloco inteiro e o "
                   "Exadata vira um storage caro. Costuma ser plano que perdeu "
                   "o full scan direto, ou celula degradada.",
        "confirma": ["cell smart", "smart scan", "cell single block",
                     "offload", "exadata", "flash cache"],
        "refuta": [],
        "decide": "O plano ainda faz full scan com direct path (condicao do "
                  "offload) e as celulas estao todas online?",
        "coletar": "v$sql (io_cell_offload_eligible_bytes) + estado das cells",
        "tags": ("exadata", "io", "plano", "offload"),
    },
    {
        "id": "rede",
        "rotulo": "Fora do banco: rede, cliente ou aplicacao",
        "classe": CLASSE_PERF,
        "explica": "O banco responde rapido e a espera esta em conversar com "
                   "o cliente. Otimizar SQL aqui nao devolve nada: o tempo "
                   "esta no caminho, no round-trip ou na propria aplicacao.",
        "confirma": ["sql*net", "sqlnet", "message from client",
                     "more data from client", "round-trip", "latencia de rede",
                     "tns-", "timeout de conexao"],
        "refuta": [],
        "decide": "O tempo esta em 'message FROM client' (aplicacao pensando) "
                  "ou em 'more data' (volume trafegado / rede)?",
        "coletar": "perfil de espera por classe Network + tcp ping do cliente",
        "tags": ("rede", "sqlnet", "aplicacao", "conexao"),
    },
]

_POR_ID = {h["id"]: h for h in HIPOTESES}


def por_id(hid: str) -> dict:
    """A hipotese do catalogo, ou {} se o id nao existir."""
    return _POR_ID.get(hid) or {}


# ── Cadeia causal: quem e causa e quem e sintoma ──────────────
# `A: (B, C)` = quando A esta sustentada, B e C passam a ser lidos como
# CONSEQUENCIA dela, e nao como problemas independentes.
#
# E o raciocinio que separa o DBA senior do classificador. Os tres pares que
# mais custam caro na pratica:
#
# - **bloqueio -> io/cpu**: sessao em 'enq: TX - row lock contention' aparece
#   ativa sem CPU e sem I/O. Quem olha so o grafico de espera conclui "deve ser
#   o storage" e abre chamado para a infra enquanto a cadeia segue de pe;
# - **plano/estatisticas -> io**: plano ruim le dez vezes mais blocos. O I/O
#   sobe porque ALGUEM esta pedindo demais - o storage esta entregando. Trocar
#   disco aqui e gastar sem resolver;
# - **estatisticas -> plano**: a regressao de plano e o sintoma; a estatistica
#   velha e a causa. Fixar o plano antigo resolve o SQL de hoje e deixa a causa
#   viva para o proximo.
#
# Consequencia NAO e refutacao: o sinal e real e continua na tela, so deixa de
# ser candidato a causa raiz. Tratar sintoma e legitimo quando urgente - mas
# tem de ser uma decisao consciente, nao um engano de leitura.
EXPLICA = {
    "bloqueio": ("io", "cpu"),
    "plano": ("io", "cpu"),
    "estatisticas": ("plano", "io"),
    "parse": ("cpu",),
    "memoria": ("io", "parse"),
    "rac_no": ("cpu", "io"),
    "asm": ("io",),
    "exadata": ("io",),
    # Carga que dobrou explica I/O, CPU e commit lento ao mesmo tempo: os tres
    # sobem juntos porque ha MAIS trabalho, nao porque cada um piorou. Sem
    # esta linha, o mesmo caso abria tres frentes de tuning em paralelo.
    "carga": ("io", "cpu", "commit"),
    # Deploy explica plano novo e parse: SQL que nao existia ontem nao tem
    # historico de plano nem cursor compartilhado.
    "mudanca_app": ("plano", "parse"),
}


def achados(caso: str, out_dir: str = "") -> list:
    """Achados exportados pela analise. Caso nao analisado devolve []."""
    base = out_dir or OUTPUT_DIR
    p = os.path.join(base, caso, f"{caso}_findings.json")
    try:
        with open(p, encoding="utf-8") as fh:
            dados = json.load(fh)
    except (OSError, ValueError):
        return []
    regs = dados.get("findings")
    return regs if isinstance(regs, list) else []


def _casa(marcas, texto: str) -> str:
    """Primeira marca presente no texto ('' se nenhuma).

    O texto e comparado COM UM ESPACO EM CADA PONTA. Isso existe para que uma
    marca possa exigir delimitador a direita simplesmente terminando em espaco
    (`'temp '`) e ainda casar quando a palavra e a ULTIMA do achado
    ("consumindo 41.2 GB de TEMP") - sem o preenchimento, a defesa funcionaria
    no meio da frase e falharia no fim, que e o pior dos dois mundos.

    Por que nao um `\\b` dos dois lados, que seria o obvio: ha marcas de PREFIXO
    deliberadas no catalogo - `ora-16` cobre a familia ORA-16xxx do Data Guard,
    `tns-` cobre os TNS-xxxxx. Um limite a direita as quebraria em silencio,
    justamente nas hipoteses que menos aparecem e que ninguem testaria de novo.
    """
    alvo = f" {texto} "
    for m in marcas or []:
        if m in alvo:
            return m
    return ""


def avaliar(registros: list) -> list:
    """Todas as hipoteses do catalogo, avaliadas contra os achados.

    Devolve a lista COMPLETA, inclusive as refutadas e as sem evidencia - e
    isso e proposital. O que foi descartado, e por que, vale tanto quanto o que
    sobrou: e o que impede o proximo plantao de refazer o caminho ja andado.
    """
    resultado = []
    for h in HIPOTESES:
        pontos = 0
        a_favor, contra = [], []
        for r in registros or []:
            texto = _texto(r)
            peso = PESO_SEV.get((r.get("severity") or "").lower(), 0)
            titulo = str(r.get("title") or "").strip()

            marca = _casa(h["refuta"], texto)
            if marca:
                contra.append({"achado": titulo, "marca": marca,
                               "severidade": r.get("severity") or ""})
                continue
            marca = _casa(h["confirma"], texto)
            if not marca or peso <= 0:
                continue
            # Percentual citado no proprio achado separa "domina" de "aparece".
            pcts = percentuais(texto)
            extra = 1 if pcts and max(pcts) >= 40 else 0
            pontos += peso + extra
            a_favor.append({"achado": titulo, "marca": marca,
                            "severidade": r.get("severity") or "",
                            "peso": peso + extra})

        resultado.append({
            "id": h["id"], "rotulo": h["rotulo"], "explica": h["explica"],
            "classe": h.get("classe", CLASSE_PERF),
            "pontos": pontos, "a_favor": a_favor, "contra": contra,
            "decide": h["decide"], "coletar": h["coletar"],
            "tags": list(h["tags"]),
            "veredito": _veredito(pontos, contra),
            "causa": "",          # preenchido quando virar consequencia
        })

    _aplicar_cadeia(resultado)
    # Ordem: sustentada primeiro, depois por pontos; a ordem do catalogo
    # desempata (concorrencia antes de I/O - gente parada AGORA vem antes de
    # lentidao distribuida).
    ordem_cat = {h["id"]: i for i, h in enumerate(HIPOTESES)}
    rank = {"sustentada": 0, "possivel": 1, "consequencia": 2,
            "sem_evidencia": 3, "refutada": 4}
    resultado.sort(key=lambda x: (rank[x["veredito"]], -x["pontos"],
                                  ordem_cat[x["id"]]))
    return resultado


def _veredito(pontos: int, contra: list) -> str:
    if contra:
        return "refutada"
    if pontos >= CORTE_SUSTENTADA:
        return "sustentada"
    return "possivel" if pontos > 0 else "sem_evidencia"


def _aplicar_cadeia(lista: list) -> None:
    """Causa sustentada rebaixa o que ela explica a CONSEQUENCIA.

    Sem isto o motor lista 'I/O' e 'bloqueio' lado a lado num caso de bloqueio
    puro - e o DBA abre chamado para o storage por causa de uma espera que a
    propria cadeia de lock produz.

    Duas travas para nao virar censura de evidencia:

    1. hipotese com evidencia PROPRIA e forte (sustentada) nunca e rebaixada -
       dois problemas reais podem coexistir, e esconder o segundo seria trocar
       um erro de leitura por outro;
    2. o rebaixamento e para 'consequencia', nao para 'refutada': o sinal
       continua verdadeiro e visivel, so deixa de ser candidato a causa raiz.
    """
    indice = {h["id"]: h for h in lista}
    for causa, efeitos in EXPLICA.items():
        c = indice.get(causa)
        if not c or c["veredito"] != "sustentada":
            continue
        for eid in efeitos:
            e = indice.get(eid)
            if not e or e["veredito"] != "possivel":
                continue
            e["veredito"] = "consequencia"
            e["causa"] = c["rotulo"]


def hipoteses(caso: str, out_dir: str = "") -> list:
    """Hipoteses do caso, ja ordenadas. Atalho de `avaliar(achados(caso))`."""
    return avaliar(achados(caso, out_dir))


def principal(caso: str, out_dir: str = "") -> dict:
    """A hipotese no topo, ou {} quando a evidencia nao aponta nada.

    O {} e resposta, nao falha: 'sem wait dominante e mesmo assim lento' manda
    olhar rede, cliente e aplicacao. Inventar um gargalo dentro do banco para
    nao devolver vazio seria o oposto do metodo.
    """
    for h in hipoteses(caso, out_dir):
        if h["veredito"] in ("sustentada", "possivel"):
            return h
    return {}


def narrativa(caso: str, out_dir: str = "") -> str:
    """O raciocinio em texto corrido, para o chamado e para a passagem de turno.

    Nao e enfeite: e o que transforma uma lista de achados na FRASE que o
    proximo plantao precisa ler - o que sustenta, o que ja foi descartado e
    o que ainda decide.
    """
    return narrar(hipoteses(caso, out_dir))


def narrar(lista: list) -> str:
    """A narrativa a partir de hipoteses JA avaliadas.

    Separado de `narrativa` porque o guiado avalia sobre a coleta MAIS o que
    foi colado no passo a passo - e reler o disco aqui devolveria o texto de
    antes da ultima observacao, contradizendo a tela ao lado.
    """
    top = [h for h in lista if h["veredito"] in ("sustentada", "possivel")]
    estrutural = [h for h in lista
                  if h["veredito"] == "sustentada"
                  and h["classe"] in (CLASSE_PARADA, CLASSE_PERDA)]

    if not top:
        base = ("A evidencia coletada nao aponta gargalo dentro do banco. "
                "Isso tambem e resposta: verifique rede, cliente e aplicacao "
                "(SQL*Net, tempo de resposta fora do banco) antes de procurar "
                "causa no servidor.")
        return base + _risco_estrutural(estrutural)

    h = top[0]
    linhas = [f"Hipotese principal: {h['rotulo']}.", h["explica"]]
    if h["a_favor"]:
        provas = "; ".join(p["achado"] for p in h["a_favor"][:3])
        linhas.append(f"Sustentada por: {provas}.")
    linhas.append(f"O que decide: {h['decide']}")

    # Sintoma explicado pela causa. Dizer isto por extenso e o que evita o
    # chamado para a infra por um I/O que o proprio plano produziu.
    efeitos = [x for x in lista if x["veredito"] == "consequencia"]
    if efeitos:
        nomes = "; ".join(x["rotulo"] for x in efeitos[:3])
        linhas.append(f"Sintoma, nao causa (ja explicado acima): {nomes}.")

    refutadas = [x for x in lista if x["veredito"] == "refutada"]
    if refutadas:
        nomes = "; ".join(f"{x['rotulo']} ({x['contra'][0]['marca']})"
                          for x in refutadas[:3])
        linhas.append(f"Descartadas pela evidencia: {nomes}.")

    outras = [x["rotulo"] for x in top[1:3]]
    if outras:
        linhas.append("Ainda em aberto: " + "; ".join(outras) + ".")
    return " ".join(linhas) + _risco_estrutural(estrutural, menos=h["id"])


def de_resultado(result) -> list:
    """Registros no formato do motor a partir de um `CaseResult` em memoria.

    Existe porque o relatorio e montado ANTES de `<caso>_findings.json` ir para
    o disco. Sem isto, a primeira analise sairia sem raciocinio e so a segunda
    teria - o tipo de diferenca que ninguem entende meses depois.
    """
    return [{"section": s.title, "severity": f.severity, "title": f.title,
             "impact": getattr(f, "impact", ""),
             "recommendation": getattr(f, "recommendation", "")}
            for s in getattr(result, "sections", []) or []
            for f in s.findings or []]


# ── Gatilho: o que MUDOU antes do que quebrou ─────────────────
# A correlacao ja liga achado -> mudanca. Aqui a ligacao sobe um nivel: mudanca
# -> HIPOTESE. A diferenca e o que o relatorio consegue afirmar.
#
#   sem isto:  "regressao de plano"  +  "houve STATS ha 3 dias" (duas listas)
#   com isto:  "regressao de plano, e a STATS de 08/08 em KIPREV.RE_COBRANCA e
#               o gatilho provavel - coleta de estatisticas muda o plano"
#
# Continua sendo HIPOTESE, nunca causa provada: coincidir no tempo e no tipo
# nao e causar. O relatorio diz isso com todas as letras, porque a alternativa
# e alguem desfazer uma mudanca inocente durante um incidente.
def gatilhos(result, agora=None) -> list:
    """Mudancas de ambiente que podem ter disparado cada hipotese viva.

    Cruza pelo TITULO do achado: o vinculo so existe quando a mesma evidencia
    sustenta a hipotese e casa com a mudanca. Sem esse aperto, qualquer STATS
    recente viraria gatilho de qualquer hipotese.
    """
    from . import correlacao

    try:
        vinculos = correlacao.correlacionar(result, agora=agora)
    except Exception:
        # Correlacao e apoio: evidencia malformada nao pode derrubar o
        # diagnostico inteiro.
        return []
    if not vinculos:
        return []

    lista = avaliar(de_resultado(result))
    out = []
    for h in lista:
        if h["veredito"] not in ("sustentada", "possivel"):
            continue
        provas = {_norm(p["achado"]) for p in h["a_favor"]}
        for v in vinculos:
            if _norm(v["achado"]) not in provas:
                continue
            out.append({
                "hipotese": h["id"], "rotulo": h["rotulo"],
                "veredito": h["veredito"], "achado": v["achado"],
                "tipo": v["tipo"], "quando": v["quando"], "objeto": v["objeto"],
                "detalhe": v["detalhe"], "dias": v["dias"],
                "confianca": v["confianca"], "porque": v["porque"],
            })
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    out.sort(key=lambda g: (0 if g["veredito"] == "sustentada" else 1,
                            ordem.get(g["confianca"], 9), g["dias"]))
    return out


def markdown(result, agora=None) -> str:
    """O raciocinio como secao do relatorio - o `porque` junto da causa raiz.

    O relatorio ja dizia QUAL e a causa raiz. O que faltava era a defesa dela:
    o que sustenta, o que ficou de fora e por que, e o que ainda decide. Sem
    isso quem le o chamado tem de acreditar; com isso, pode discordar - que e o
    uso mais legitimo do documento.
    """
    lista = avaliar(de_resultado(result))
    vivas = [h for h in lista if h["veredito"] in ("sustentada", "possivel")]
    if not vivas:
        return ("- A evidencia coletada nao aponta um gargalo dentro do banco. "
                "Isso tambem e resposta: investigue rede, cliente e aplicacao "
                "(SQL*Net, tempo de resposta fora do banco) antes de procurar "
                "causa no servidor.")

    gats = gatilhos(result, agora=agora)
    por_hip = {}
    for g in gats:
        por_hip.setdefault(g["hipotese"], g)

    linhas = ["_Hipoteses concorrentes pontuadas pela evidencia deste caso. "
              "A refutada fica na lista de proposito: saber o que ja foi "
              "descartado, e por que, e o que impede a proxima pessoa de "
              "refazer o caminho._", ""]
    for h in vivas[:4]:
        selo = {CLASSE_PARADA: " **[PODE PARAR O BANCO]**",
                CLASSE_PERDA: " **[RISCO DE PERDA DE DADO]**"}.get(
                    h["classe"], "") if h["veredito"] == "sustentada" else ""
        linhas.append(f"- **{h['rotulo']}** - {h['veredito'].upper()}"
                      f" ({h['pontos']} ponto(s)){selo}")
        linhas.append(f"  - Porque: {h['explica']}")
        for p in h["a_favor"][:3]:
            linhas.append(f"  - Sustenta: {p['achado']}")
        g = por_hip.get(h["id"])
        if g:
            linhas.append(
                f"  - **Gatilho provavel:** {g['tipo']} de {g['quando']} "
                f"({g['dias']} dia(s) antes) em `{g['objeto']}` - "
                f"{g['porque']} (confianca {g['confianca']}; correlacao no "
                f"tempo, nao causa provada)")
        linhas.append(f"  - O que decide: {h['decide']} (`{h['coletar']}`)")

    efeitos = [h for h in lista if h["veredito"] == "consequencia"]
    if efeitos:
        linhas.append("")
        linhas.append("**Sintoma, nao causa** - o sinal e real, mas outra causa "
                      "ja o explica; tratar aqui e gastar sem resolver:")
        for h in efeitos[:4]:
            linhas.append(f"- {h['rotulo']} - explicado por **{h['causa']}**")

    refutadas = [h for h in lista if h["veredito"] == "refutada"]
    if refutadas:
        linhas.append("")
        linhas.append("**Descartadas pela evidencia:**")
        for h in refutadas[:4]:
            linhas.append(f"- ~~{h['rotulo']}~~ - {h['contra'][0]['achado']} "
                          f"({h['contra'][0]['marca']})")

    estrutural = [h for h in lista if h["veredito"] == "sustentada"
                  and h["classe"] in (CLASSE_PARADA, CLASSE_PERDA)
                  and h["id"] != vivas[0]["id"]]
    if estrutural:
        linhas.append("")
        linhas.append("> **ALEM DO INCIDENTE - risco estrutural neste "
                      "servidor.** Nao explica o sintoma que abriu o caso e e "
                      "mais grave que ele: "
                      + "; ".join(h["rotulo"] for h in estrutural[:3]) + ".")
    return "\n".join(linhas)


def causa_com_porque(result, base: str = "") -> str:
    """Uma linha para o campo 'Causa Raiz': o QUE, o POR QUE e o gatilho.

    Os destaques e o motor respondem perguntas diferentes e por isso se somam,
    em vez de disputar o campo:

    - os **destaques** medem impacto e trazem o achado concreto, com numero
      (`SQL 9xy: elapsed 23s -> 407s`) - e o que prova que doeu;
    - o **motor** nomeia o mecanismo (`Regressao de plano de execucao`) e o
      gatilho provavel (`STATS de 08/08 em KIPREV.RE_COBRANCA`) - e o que
      explica por que aconteceu.

    Com so o primeiro, o chamado descreve o sintoma com precisao e nao diz a
    causa. Com so o segundo, afirma a causa sem o numero que a sustenta.
    """
    lista = avaliar(de_resultado(result))
    vivas = [h for h in lista if h["veredito"] == "sustentada"]
    if not vivas:
        return base
    h = vivas[0]

    if base:
        txt = f"{base} - mecanismo: {h['rotulo']}"
    else:
        txt = h["rotulo"]
        if h["a_favor"]:
            txt += " (sustentada por: " + "; ".join(
                p["achado"] for p in h["a_favor"][:2]) + ")"

    g = next((x for x in gatilhos(result) if x["hipotese"] == h["id"]), None)
    if g:
        txt += (f"; gatilho provavel: {g['tipo']} de {g['quando']} em "
                f"{g['objeto']} - correlacao no tempo, a confirmar")
    return txt


def _risco_estrutural(estrutural: list, menos: str = "") -> str:
    """O que ameaca PARAR o banco ou impedir a recuperacao, dito a parte.

    Um chamado aberto por lentidao nao pode terminar sem alguem ver que aquele
    banco esta em NOARCHIVELOG ou sem backup ha 30 dias. Como esses achados nao
    explicam o incidente, eles perdem a disputa pela causa raiz e sumiriam do
    texto - justamente os dois que ninguem pode deixar passar.
    """
    itens = [h for h in estrutural if h["id"] != menos]
    if not itens:
        return ""
    nomes = "; ".join(h["rotulo"] for h in itens[:3])
    return (" ALEM DO INCIDENTE, risco estrutural neste servidor: " + nomes +
            ". Nao explica a lentidao de hoje e e mais grave que ela.")
