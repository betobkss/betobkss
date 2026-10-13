"""
COLETAR - coletor read-only de troubleshooting (conexao direta ao banco).

Conecta no Oracle (python-oracledb thin, sem client), roda TODAS as coletas em
sessao READ ONLY e gera o pacote completo (dashboard + PPTX + relatorio) num so
comando. NAO executa nada destrutivo.

Repositorio de conexoes: le stringConexao/*.json (export do SQL Developer). Na
selecao interativa, basta digitar parte do nome do servidor que ele preenche
host/porta/service/usuario automaticamente (senha nunca e usada do arquivo).

Conexao Oracle: sempre por usuario + service name; porta padrao 1529.

Uso (INTERATIVO RAPIDO - basta rodar sem argumentos):
  python COLETAR.py
    -> pergunta: caso, HOST, service name, usuario (padrao dbaread) e senha,
       conecta e ja gera o relatorio COMPLETO (pack conforme a licenca).
    Por padrao e sempre COMPLETO; use --parcial para a coleta leve (sem
    consumir Diagnostics/Tuning Pack).

Uso rapido (atalhos por flag):
  python COLETAR.py <caso> --host db01 --service DBSRV114        (pede a senha)
  python COLETAR.py <caso> --conn DBSRV114                        (busca no repositorio)
  python COLETAR.py <caso> --host db01 --service SVC --parcial    (sem pack)
  python COLETAR.py <caso> --conn DBSRV114 --rapido               (triagem rapida)
  python COLETAR.py <caso> --conn DBSRV114 --advisor               (roda Tuning Advisor)

SQL Health Check de regressao (read-only, automatico na coleta completa):
gera regressions.txt (top 10 por impacto), planos.txt (plano antigo x novo) e
acoes.sql (DBMS_STATS e indices candidatos, PRONTOS mas nao executados).
--advisor adiciona advisor.txt: NAO e read-only e consome Tuning Pack.

Desempenho: a coleta roda em paralelo (--workers, padrao 4 conexoes read-only),
com timeout por query (--timeout, padrao 120s) e modo --rapido que pula os
checks de historico do AWR (DBA_HIST_*) e o relatorio ADDM.

Gerir o repositorio de conexoes (opcional):
  python COLETAR.py --list-conns
  python COLETAR.py --add --conn NOME --host H --port 1529 --service SVC --user dbaread

Credenciais: a senha e SEMPRE digitada na hora - no terminal (sem eco) ou na
caixa mascarada do painel. Nao ha --password, arquivo nem variavel de ambiente
que a substitua; ela morre com o processo. Ver troubleshoot/senha.py.
Recomenda-se um usuario SOMENTE LEITURA (ex.: SELECT_CATALOG_ROLE).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from TROUBLESHOOT import CASES_DIR, run_case
from troubleshoot import conn_repo, dialogo
from troubleshoot import senha as mod_senha
from troubleshoot.collector import collect_direct


def gravar_meta_do_caso(case_dir: str, conn: str = "", dsn: str = "",
                        user: str = "") -> str:
    """Registra em meta.json qual banco originou as evidencias.

    Sem isso o relatorio nao sabe de onde vieram os dados, e os comandos de
    drill-down que ele sugere (JANELA/DETALHAR_SQL) sairiam com '<conexao>' em
    vez do banco de verdade. Preserva o que ja existir no arquivo (title,
    subtitle, kind postos a mao).
    """
    caminho = os.path.join(case_dir, "meta.json")
    dados = {}
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as fh:
                dados = json.load(fh) or {}
        except Exception:
            dados = {}
    if conn:
        dados["conn"] = conn
        # Perfil do ambiente gravado NA COLETA, e nao deduzido na analise.
        # A razao e reprodutibilidade: um caso analisado hoje nao pode mudar de
        # conclusao porque alguem editou `servidores.txt` amanha. O meta.json e
        # a declaracao do que foi assumido quando a evidencia foi tirada.
        from troubleshoot import perfil as _perfil
        dados["perfil"] = _perfil.do_servidor(conn)
    if dsn:
        dados["dsn"] = dsn
    if user:
        dados["user"] = user
    dados["collected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(case_dir, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2)
    return caminho


def _interactive() -> bool:
    """True se ha ALGUEM para responder: um terminal ou o painel.

    O painel executa o CLI com o stdin num pipe, onde `isatty()` da False. Ele
    avisa que esta do outro lado pela variavel do `dialogo` - sem esse aviso,
    pipe continua significando execucao automatizada (bateria, agendamento), e
    ai perguntar travaria o processo.
    """
    if dialogo.habilitado():
        return True
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def diagnostico_conexao(erro, dsn: str = "", user: str = "") -> tuple:
    """Traduz uma falha de conexao em causa provavel + o que fazer.

    Sem isso o DBA recebe "DPY-6005: cannot connect to database" e nao sabe se
    o problema e VPN, nome do service ou senha - os tres pedem acoes opostas.
    Retorna (causa, [acoes]); causa vazia = erro nao reconhecido.
    """
    txt = str(erro)
    low = txt.lower()
    host = (dsn or "").split(":")[0] or "<host>"

    # ORA- primeiro: sao mais especificos que os DPY- de transporte.
    if "ora-01017" in low:
        return ("Usuario ou senha invalidos (a conexao TCP funcionou).",
                [f"confirme o usuario (usando: {user or '?'})",
                 "rode de novo e digite a senha com atencao",
                 "cuidado com senha expirada ou trocada recentemente"])
    if "ora-28000" in low or "ora-28001" in low:
        return ("A conta esta bloqueada ou com a senha expirada.",
                [f"peca ao DBA:  ALTER USER {user or '<user>'} ACCOUNT UNLOCK",
                 "senha expirada exige troca antes de conectar"])
    if "ora-01045" in low:
        return (f"O usuario {user or '<user>'} nao tem CREATE SESSION.",
                ["a coleta e read-only, mas ainda precisa de sessao + SELECT",
                 "grants minimos: CREATE SESSION, SELECT_CATALOG_ROLE"])
    if "ora-12514" in low or "ora-12528" in low:
        return ("O listener respondeu, mas nao conhece esse SERVICE_NAME.",
                ["confira o service (parte depois da '/' no DSN)",
                 "em CDB, o service da PDB e diferente do da CDB",
                 "liste com:  lsnrctl services  (no servidor)"])
    if "ora-12541" in low:
        return ("Nao ha listener escutando nesse host/porta.",
                ["confirme a porta (o padrao aqui e 1529, nao 1521)",
                 "o listener pode estar parado ou o banco em manutencao"])
    if "dpy-4011" in low:
        return ("O banco ou a rede fechou a conexao no meio da coleta.",
                ["pode ser queda de VPN, restart do banco ou killed session",
                 "se repetir sempre no mesmo check, reduza:  --workers 1"])
    if "ora-00257" in low:
        return ("Archiver travado: a area de archive/FRA esta cheia.",
                ["e um incidente real do banco, nao da coleta",
                 "libere a FRA ou amplie DB_RECOVERY_FILE_DEST_SIZE"])

    # Transporte: separar "nome nao resolve" de "resolve mas nao conecta" e o
    # que mais economiza tempo - as acoes sao completamente diferentes.
    if ("getaddrinfo" in low or "errno 11001" in low
            or "name or service not known" in low):
        return (f"O nome '{host}' nao resolve no DNS desta maquina.",
                ["pode faltar o sufixo de dominio no nome do host",
                 "se for endpoint AWS/RDS, exige a VPN que enxerga a VPC",
                 f"teste:  Resolve-DnsName {host}",
                 "confira o cadastro:  .\\coletar.bat --list-conns"])
    if "timed out" in low or "timeout" in low:
        return (f"O nome resolveu, mas nao houve resposta de '{host}'.",
                ["tipicamente VPN desconectada, firewall ou falta de rota",
                 f"teste:  Test-NetConnection {host} -Port 1529",
                 "se outro servidor da mesma rede funciona, o problema e no host"])
    if "dpy-6005" in low or "refused" in low:
        return (f"Nao foi possivel abrir conexao com '{host}'.",
                ["VPN ativa? a rota deste PC alcanca esse segmento?",
                 f"teste:  Test-NetConnection {host} -Port 1529"])
    return ("", [])


def _ask(label: str, default: str = "") -> str:
    """Pergunta no prompt; aceita default entre colchetes.
    Sem terminal interativo, usa o default ou aborta com mensagem clara
    (em vez de travar esperando input que nunca vem)."""
    sfx = f" [{default}]" if default else ""
    if dialogo.habilitado():
        return dialogo.perguntar(label, default)
    if not _interactive():
        if default:
            print(f"{label}{sfx}: {default}  (sem terminal: usando o padrao)")
            return default
        print(f"\n[ERRO] Falta informar '{label}' e nao ha terminal interativo.")
        print("       Passe por flag, ex.: --conn DBSRV600 / --host H --service SVC")
        sys.exit(2)
    val = input(f"{label}{sfx}: ").strip()
    return val or default


# A sugestao de nome mora em conn_repo porque o painel mostra a MESMA lista.
# Reexportado aqui para nao quebrar quem ja chamava COLETAR.parecidas().
parecidas = conn_repo.parecidas


def _pick_connection(query: str, permitir_prompt: bool = True):
    """Escolhe uma conexao do repositorio (stringConexao/). Retorna Conn ou None.

    permitir_prompt=False quando o servidor veio EXPLICITO na linha de comando
    (`--conn`): nesse caso cair num menu e errado - quem pediu DBSRV600 nao quer
    escolher outro banco, quer saber que o nome nao existe.
    """
    conns = conn_repo.load_connections()
    if not conns:
        return None
    if query is None:
        if not permitir_prompt:
            return None
        query = _ask(f"Servidor (nome/parte; {len(conns)} no repositorio, "
                     f"Enter p/ digitar manual)")
    if not query:
        return None
    matches = conn_repo.find(query)
    if not matches:
        print(f"  Nenhuma conexao encontrada para '{query}'.")
        sugestoes = parecidas(query, [c["name"] for c in conns])
        if sugestoes:
            print(f"  Voce quis dizer: {', '.join(sugestoes)}?")
        if not permitir_prompt:
            return None
        return _pick_connection(None)
    if len(matches) == 1:
        return matches[0]
    # Nome exato (case-insensitive) resolve sozinho: evita prompt quando o
    # repositorio tem a mesma conexao em maiuscula/minuscula.
    exact = [c for c in matches if str(c.get("name", "")).strip().lower() == query.strip().lower()]
    if exact:
        return exact[0]
    # Mesmo DSN em todas as opcoes: sao equivalentes, escolhe a primeira.
    if len({str(c.dsn).lower() for c in matches}) == 1:
        return matches[0]
    print(f"  {len(matches)} conexoes encontradas:")
    for i, c in enumerate(matches[:40], 1):
        print(f"   [{i}] {c}")
    if not permitir_prompt:
        # Ambiguo com o nome vindo por --conn: quem chamou (bateria, painel,
        # agendamento) nao tem como escolher, e abrir o menu aqui consumiria a
        # proxima linha do stdin - que e a SENHA.
        print("  Nome ambiguo: use o nome exato da conexao.")
        return None
    sel = _ask("Escolha o numero (ou Enter p/ refinar a busca)")
    if sel.isdigit() and 1 <= int(sel) <= len(matches):
        return matches[int(sel) - 1]
    return _pick_connection(None)


def main():
    ap = argparse.ArgumentParser(
        description="Coletor read-only de troubleshooting Oracle (interativo)")
    ap.add_argument("case", nargs="?", help="nome do caso (ex.: INC000987654)")
    ap.add_argument("--dsn", help="DSN completo host:porta/service")
    ap.add_argument("--host")
    ap.add_argument("--port")
    ap.add_argument("--service")
    ap.add_argument("--user")
    ap.add_argument("--pack", default="auto",
                    help="licenca: auto (detecta) | T (Tuning+Diag) | D (Diagnostics) | "
                         "N (nenhum - so v$, sem consumir Diagnostics/Tuning Pack)")
    ap.add_argument("--conn", help="nome/parte da conexao no repositorio stringConexao/")
    ap.add_argument("--list-conns", action="store_true", help="lista as conexoes do repositorio e sai")
    ap.add_argument("--add", action="store_true", help="adiciona/atualiza uma conexao no repositorio e sai")
    ap.add_argument("--title")
    ap.add_argument("--subtitle")
    ap.add_argument("--completo", action="store_true",
                    help="[padrao] gera o relatorio completo no template padrao")
    ap.add_argument("--parcial", action="store_true",
                    help="coleta leve, sem consumir Diagnostics/Tuning Pack (so v$)")
    ap.add_argument("--rapido", action="store_true",
                    help="triagem rapida: pula os checks de historico AWR (DBA_HIST_*) e o ADDM")
    ap.add_argument("--workers", type=int, default=4,
                    help="conexoes read-only paralelas (padrao 4; 1 = sequencial)")
    ap.add_argument("--timeout", type=int, default=120,
                    help="limite em segundos por query (padrao 120; 0 = sem limite)")
    ap.add_argument("--amostragem", type=float, default=0.0, metavar="SEGUNDOS",
                    help="janela de amostragem de gv$session (padrao 15s). "
                         "Janela maior = mais amostras = mais resolucao: 15s "
                         "sao 30 amostras e cada uma vale 3,3 pontos do "
                         "perfil. Use 120 para investigar um sintoma")
    ap.add_argument("--advisor", action="store_true",
                    help="roda o SQL Tuning Advisor nos SQL elegiveis (troca de "
                         "plano ou impacto >= 300s, no maximo 5). ATENCAO: NAO e "
                         "read-only (cria/executa tuning tasks) e consome Tuning "
                         "Pack. Recomendacoes vao para advisor.txt e acoes.sql")
    ap.add_argument("--no-pptx", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--no-report", action="store_true", help="apenas coleta, nao gera relatorio")
    args = ap.parse_args()

    # Listar conexoes do repositorio
    if args.list_conns:
        conns = conn_repo.load_connections()
        print(f"{len(conns)} conexao(oes) em {conn_repo.REPO_DIR}:")
        for c in conns:
            print(f"  {c}")
        return

    # Adicionar/atualizar conexao no repositorio
    if args.add:
        name = args.conn or _ask("Nome da conexao")
        host = args.host or _ask("Host / IP")
        port = args.port or _ask("Porta", "1529")
        service = args.service or _ask("Service name", name)
        user = args.user or _ask("Usuario (leitura)", "dbaread")
        folder = _ask("Grupo/pasta (NAV_FOLDER)", "PRODUCAO")
        try:
            path = conn_repo.add_or_update(name, host, service, user, port, folder)
        except ValueError as e:
            print(f"[ERRO] {e}")
            sys.exit(2)
        print(f"[OK] Conexao '{name}' salva em {path}")
        return

    print("=" * 70)
    print("  COLETAR - coleta read-only de troubleshooting Oracle")
    print("  (Enter aceita o valor padrao entre colchetes)")
    print("=" * 70)

    # Caso
    case = args.case or _ask("Nome do caso (ex.: INC000987654)")
    while not case:
        case = _ask("Nome do caso (obrigatorio)")

    # Conexao: DSN explicito > host/service explicitos > repositorio > manual
    dsn = args.dsn
    user = args.user or os.environ.get("ORA_USER")
    conn_name = ""      # nome do banco, para o meta.json e o drill-down
    if not dsn and args.host and args.service:
        dsn = f"{args.host}:{args.port or '1529'}/{args.service}"
        conn_name = args.service
    if not dsn and args.conn:
        # Nome EXPLICITO nunca abre menu, em nenhum modo. Seguir para o prompt
        # de host+service faria o processo morrer duas telas adiante com uma
        # mensagem que nao fala do servidor errado; e, com o dialogo ligado
        # (painel/bateria), o menu ainda leria a linha da senha como resposta.
        conn = _pick_connection(args.conn, permitir_prompt=False)
        if conn:
            dsn = conn.dsn
            user = user or conn["user"]
            conn_name = conn["name"]
            print(f"  -> {conn}")
        else:
            print(f"\n[ERRO] Servidor '{args.conn}' nao existe no repositorio "
                  f"stringConexao/.")
            print("       Confira o nome na lista do painel ou rode: "
                  "COLETAR.py --list-conns")
            sys.exit(2)
    if not dsn:
        # Caminho rapido: digitar host + service direto (sem busca no repositorio)
        host = _ask("Host / IP do banco")
        while not host:
            host = _ask("Host / IP do banco (obrigatorio)")
        port = args.port or _ask("Porta", "1529")
        service = _ask("Service name / SID")
        while not service:
            service = _ask("Service name (obrigatorio)")
        dsn = f"{host}:{port}/{service}"
        conn_name = service

    # Credenciais
    if not user:
        user = _ask("Usuario Oracle (somente leitura)", "dbaread")
    while not user:
        user = _ask("Usuario Oracle (obrigatorio)")
    # Sempre digitada, a cada execucao. Ver troubleshoot/senha.py para o porque
    # de nao existir mais atalho por ambiente ou por argumento.
    try:
        password = mod_senha.pedir(user)
    except mod_senha.SemComoPerguntar:
        for linha in mod_senha.explicar_falta(user):
            print(linha)
        sys.exit(2)
    if not password:
        print("\n[ERRO] Senha vazia - nada a fazer.")
        sys.exit(2)

    # Pack: por padrao usa a licenca completa detectada (auto = tudo que o banco
    # licencia). --parcial faz a coleta leve, sem consumir Diagnostics/Tuning Pack.
    pack = "N" if args.parcial else args.pack
    modo = "PARCIAL (sem pack)" if args.parcial else "COMPLETO (pack conforme licenca)"
    if args.rapido:
        modo += " | RAPIDO (sem historico AWR)"
    if args.advisor:
        modo += " | ADVISOR (escreve + Tuning Pack)"

    print("=" * 70)
    print(f"  COLETAR (read-only) - caso: {case}  [{modo}]")
    print(f"  DSN: {dsn} | user: {user}")
    print("=" * 70)

    try:
        written = collect_direct(case, dsn, user, password, pack=pack,
                                 workers=args.workers, fast=args.rapido,
                                 timeout=args.timeout, advisor=args.advisor,
                                 amostragem_segundos=args.amostragem)
    except Exception as e:
        print(f"\n[ERRO] Coleta falhou: {e}")
        causa, acoes = diagnostico_conexao(e, dsn, user)
        if causa:
            print(f"[causa] {causa}")
            for a in acoes:
                print(f"        - {a}")
        sys.exit(1)

    print(f"\n[OK] {len(written)} evidencia(s) coletada(s): {', '.join(written)}")

    # Guarda a origem das evidencias: e o que permite ao relatorio sugerir os
    # comandos de drill-down apontando para o banco certo.
    gravar_meta_do_caso(os.path.join(CASES_DIR, case), conn_name, dsn, user)

    if args.no_report:
        print("Coleta concluida (--no-report). Rode o TROUBLESHOOT quando quiser.")
        return

    # Sempre gera o relatorio COMPLETO (template padrao Diagnostico+RCA).
    print("\n--- Gerando relatorio ---")
    run_case(case, args.title, args.subtitle,
             make_pptx=not args.no_pptx, do_open=args.open, completo=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
