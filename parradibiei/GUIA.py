"""
GUIA - troubleshooting guiado: um passo por vez, com risco declarado.

O fluxo do DBA Master conduzido pela ferramenta: escolhe o playbook a partir do
que a EVIDENCIA ja mostrou, apresenta um passo de cada vez (objetivo, comando, o
que olhar, armadilha), retem o comando que muda o ambiente atras da Matriz de
Risco e registra a acao no caso sozinho.

O passo de VERIFICACAO roda aqui (`--executar`), pela mesma trava read-only da
coleta: a saida vira evidencia do caso e o motor repontua as hipoteses na hora,
sem ninguem copiar e colar. Passo que MUDA o ambiente nao executa por aqui em
nenhuma hipotese - segue atras da Matriz de Risco, executado por gente, e
registrado com `--confirmar`.

Exemplos:
  # o que a evidencia sugere para este caso
  python GUIA.py INC000987654

  # rodar a verificacao do passo atual no proprio framework (read-only)
  python GUIA.py INC000987654 --executar
  python GUIA.py INC000987654 --executar --param sql_id=7ktb2sm9ug3xw

  # rodar de uma vez todos os passos de verificacao do playbook
  python GUIA.py INC000987654 --executar-tudo

  # comecar um playbook e andar
  python GUIA.py INC000987654 --iniciar 02_sessoes_locks
  python GUIA.py INC000987654 --avancar "sem bloqueio na janela"
  python GUIA.py INC000987654 --pular  "sem janela aprovada"

  # registrar que o passo foi executado (grava a acao no caso, com o risco)
  python GUIA.py INC000987654 --confirmar "kill da sessao 1234"

  # checklist pos-acao
  python GUIA.py INC000987654 --pendencias
  python GUIA.py INC000987654 --feito recoletar --nota "score voltou a 92"
"""
from __future__ import annotations

import argparse
import os
import sys

from troubleshoot import caso as mod_caso
from troubleshoot import conn_repo, executor, guia, raciocinio
from troubleshoot import senha as mod_senha

LARG = 70


def _titulo(txt: str) -> None:
    print("=" * LARG)
    print(f"  {txt}")
    print("=" * LARG)


def _bloco(rotulo: str, texto: str) -> None:
    if not texto:
        return
    print(f"  {rotulo}:")
    for linha in str(texto).splitlines():
        print(f"    {linha}")


_VERDITO = {"sustentada": "SUSTENTADA", "possivel": "possivel",
            "consequencia": "sintoma", "refutada": "DESCARTADA"}

# O caso aberto por lentidao nao pode terminar sem alguem ver que o banco esta
# em NOARCHIVELOG - por isso a classe vai no rotulo, nao numa nota de rodape.
_CLASSE = {raciocinio.CLASSE_PARADA: "  <<< PODE PARAR O BANCO",
           raciocinio.CLASSE_PERDA: "  <<< RISCO DE PERDA DE DADO"}


def _mostrar_hipoteses(caso: str) -> None:
    """As hipoteses vivas e as descartadas, com o que sustenta cada uma.

    A descartada e impressa junto de proposito: quem assume o plantao precisa
    saber o que ja foi eliminado, senao refaz o caminho andado.
    """
    hs = [h for h in raciocinio.hipoteses(caso)
          if h["veredito"] != "sem_evidencia"]
    if not hs:
        print("  A evidencia nao aponta um gargalo dentro do banco.")
        print("  Isso tambem e resposta: verifique rede, cliente e aplicacao")
        print("  (SQL*Net), ou colete/analise o caso antes de conduzir.")
        return
    print("  " + raciocinio.narrativa(caso))
    print("\n  Hipoteses:")
    for h in hs:
        selo = _CLASSE.get(h["classe"], "") if h["veredito"] == "sustentada" else ""
        print(f"\n  [{_VERDITO.get(h['veredito'], h['veredito'])}] "
              f"{h['rotulo']}{selo}")
        if h["veredito"] == "consequencia" and h.get("causa"):
            print(f"      -> explicado por: {h['causa']} "
                  f"(tratar a causa, nao este sintoma)")
        for p in h["a_favor"][:3]:
            print(f"      + {p['achado']}")
        for p in h["contra"][:2]:
            print(f"      - {p['achado']}  ({p['marca']})")
        if h["veredito"] != "refutada":
            print(f"      o que decide: {h['decide']}")
            print(f"      coletar:      {h['coletar']}")


def _mostrar_sugestoes(caso: str) -> int:
    _titulo(f"GUIA - {caso}")
    _mostrar_hipoteses(caso)
    subs = guia.playbooks_sugeridos(caso)
    if subs:
        print("\n  Playbooks que tratam a hipotese:")
        for s in subs[:5]:
            print(f"    {s['id']:<24} {s['titulo']}")
            print(f"    {'':<24} ({s['porque']})")
        print(f"\n  Comece com:  {_py()} GUIA.py {caso} --iniciar {subs[0]['id']}")
    else:
        _pistas_do_evento(caso)
    return 0


def _pistas_do_evento(caso: str) -> None:
    """Sem evidencia, a pista e o TEXTO DO ALERTA - explicitamente como pista.

    Este e o minuto zero do caso: a coleta pede senha e vem depois, entao
    `playbooks_sugeridos` (que le a evidencia) nao tem o que dizer. Ate
    24/09/2026 a saida aqui era "nenhum playbook sugerido" mais um `python -c`
    para listar os 39 a mao - a hora de maior necessidade de direcao era a de
    menor oferta.

    O rotulo importa tanto quanto a lista: nos 5 casos de 22 a 24/09 o texto do
    alerta descrevia outra coisa em 4. Por isso sai como "o alerta MENCIONA", e
    com o aviso de que o passo 1 e medicao - nunca como "o problema E".
    """
    evento = (mod_caso.resumo(caso) or {}).get("evento") or ""
    pistas = guia.playbooks_por_texto(evento) if evento else []
    if not evento:
        print("\n  Sem evidencia coletada e sem evento registrado - nao ha por "
              "onde comecar.")
        print(f"    {_py()} CASO.py {caso} --evento \"<texto do alerta>\"")
        print(f"    {_py()} GUIA.py {caso} --playbooks     (ver o catalogo)")
        return
    print("\n  Sem evidencia coletada ainda. Pelo TEXTO DO ALERTA, os playbooks")
    print("  que MENCIONAM os mesmos termos (pista de partida, nao diagnostico):")
    for s in pistas[:5]:
        plat = f"[{s['plataforma']}] " if s.get("plataforma") else ""
        print(f"    {s['id']:<24} {plat}{s['titulo']}")
        print(f"    {'':<24} ({s['porque']})")
    if not pistas:
        print("    (nenhum termo do alerta casou com a KB)")
    print("\n  [!] O texto do alerta descreveu OUTRA coisa em 4 dos 5 casos de")
    print("      22 a 24/09/2026. Todo playbook comeca por passo read-only - e")
    print("      a medicao que decide, nao esta lista.")
    if pistas:
        print(f"\n  Comece com:  {_py()} GUIA.py {caso} --iniciar {pistas[0]['id']}")
    print(f"  Catalogo:    {_py()} GUIA.py {caso} --playbooks")


def _listar_playbooks() -> int:
    """O catalogo inteiro, por plataforma.

    Existia so como `python -c` na mensagem de erro do --sugerir: 39 playbooks e
    339 passos que ninguem via sem escrever Python. Playbook que o DBA nao sabe
    que existe nao e usado - o de roll-forward incremental ficou invisivel
    assim.
    """
    from troubleshoot import kb
    itens = kb.resumo()
    por_plat: dict = {}
    for p in itens:
        pb = kb.por_id(p["id"]) or {}
        por_plat.setdefault(kb.plataforma_de(pb), []).append(p)
    total_passos = sum(p.get("passos", 0) for p in itens)
    _titulo(f"CATALOGO DA KB - {len(itens)} playbook(s), {total_passos} passo(s)")
    for plat in sorted(por_plat):
        lista = por_plat[plat]
        print(f"\n  {plat.upper()}  ({len(lista)})")
        for p in lista:
            risco = "  [tem passo de mudanca]" if p.get("tem_risco") else ""
            print(f"    {p['id']:<26} {p['titulo'][:60]}{risco}")
    print("\n  Para seguir um deles:")
    print(f"    {_py()} GUIA.py <CASO> --iniciar <id>")
    return 0


def _mostrar_passo(caso: str) -> int:
    pr = guia.progresso(caso)
    if not pr:
        return _mostrar_sugestoes(caso)
    p = guia.passo_atual(caso)
    _titulo(f"GUIA - {caso}   [{pr['titulo']}]")
    print(f"  Progresso: {pr['concluidos']} concluido(s), {pr['pulados']} "
          f"pulado(s) de {pr['total']} passo(s)")
    if pr["pendencias_abertas"]:
        print(f"  !! {pr['pendencias_abertas']} pendencia(s) do checklist "
              f"pos-acao em aberto (--pendencias)")
    if not p:
        return _fechar_roteiro(caso, pr)

    print(f"\n  PASSO {p['indice'] + 1}/{p['total']}: {p['titulo']}")
    _bloco("Objetivo", p.get("objetivo", ""))
    m = p.get("matriz_risco") or {}
    if m:
        # A matriz vem ANTES do comando de proposito: quem le de cima para
        # baixo topa com o risco antes de topar com algo para copiar.
        print("\n  " + "!" * (LARG - 4))
        print(f"  MATRIZ DE RISCO - acao de risco {m['risco'].upper()}")
        _bloco("Impacto", m["impacto"])
        _bloco("Antes de executar", m.get("antes_de_executar", ""))
        _bloco("Rollback", m["rollback"])
        if m["exige_aprovacao"]:
            print("    EXIGE aprovacao formal e janela combinada.")
        print("  " + "!" * (LARG - 4))
    _bloco("Comando", p.get("comando", ""))
    _bloco("O que olhar", p.get("olhar", ""))
    _bloco("Armadilha", p.get("armadilha", ""))
    _rodape(caso, p)
    return 0


def _fechar_roteiro(caso: str, pr: dict) -> int:
    """O fim do roteiro tem de CONCLUIR, nao so avisar que acabou.

    Antes saia uma linha: "Roteiro concluido. Proximo passo: redigir o caso."
    Depois de oito passos de evidencia coletada, isso deixa no colo de quem
    conduziu justamente o trabalho que o framework existe para fazer - dizer o
    que a evidencia sustenta.

    Aqui o motor de raciocinio e chamado uma ultima vez sobre TUDO que foi
    observado no roteiro: o que ficou de pe, o que foi DERRUBADO (que costuma
    valer mais - e o caminho que ninguem precisa mais percorrer) e o que segue
    em aberto. So entao vem o proximo comando.
    """
    print("\n  Roteiro concluido.")
    if pr.get("pulados"):
        # Passo pulado nao e passo feito: se a conclusao ignorar isso, ela
        # afirma sobre evidencia que ninguem olhou.
        print(f"  Atencao: {pr['pulados']} passo(s) foram PULADOS - a "
              f"conclusao abaixo nao cobre o que eles veriam.")

    try:
        hip = raciocinio.avaliar(guia.observados(caso))
    except Exception:
        hip = []

    de_pe = [h for h in hip if h["veredito"] == "sustentada"]
    possiveis = [h for h in hip if h["veredito"] == "possivel"]
    fora = [h for h in hip if h["veredito"] == "refutada"]

    if de_pe or possiveis or fora:
        print("\n  O QUE A CONDUCAO ESTABELECEU")
    for h in de_pe:
        print(f"    [SUSTENTADA] {h['rotulo']}")
        for prova in h["a_favor"][:3]:
            print(f"                 <- {prova['marca']}")
    for h in possiveis:
        print(f"    [possivel]   {h['rotulo']}")
    if fora:
        # A refutacao vem depois, e nomeada: e o ganho menos obvio da conducao
        # e o que impede alguem de reabrir o mesmo caminho amanha.
        print(f"    [descartado] {', '.join(h['rotulo'] for h in fora[:4])}")

    if not de_pe and not possiveis:
        print("    Nenhuma hipotese ficou de pe com o que foi observado.")
        print("    Isso NAO e resultado vazio: os caminhos percorridos estao")
        print("    descartados e o proximo roteiro comeca de outro lugar.")

    py = _py()
    print("\n  DAQUI:")
    if de_pe:
        print(f"    1. Aprofundar a causa:  {py} GUIA.py {caso} --sugerir")
        print(f"    2. Redigir o caso:      {py} CASO.py {caso} --redija")
    else:
        print(f"    1. Outro roteiro:       {py} GUIA.py {caso} --sugerir")
        print(f"    2. Redigir o caso:      {py} CASO.py {caso} --redija")
    if pr.get("pendencias_abertas"):
        print(f"    3. Fechar pendencias:   {py} GUIA.py {caso} --pendencias")
    return 0


def _py() -> str:
    """O interpretador que esta rodando AGORA, em forma copiavel.

    `python` nao serve, e nao e detalhe de estilo: nesta maquina o `python` do
    PATH e um 3.14 SEM o driver oracledb, enquanto o do projeto tem. O rodape
    dizia `python GUIA.py ...` e quem copiasse receberia
    "python-oracledb nao instalado" - ou, pior, um comando que funciona para
    `--confirmar` e falha so no `--executar`, o que parece defeito do banco.

    Caminho relativo quando o interpretador esta dentro do projeto (a forma que
    toda a documentacao usa) e absoluto entre aspas quando nao esta.
    """
    exe = os.path.abspath(sys.executable)
    raiz = os.path.dirname(os.path.abspath(__file__))
    try:
        dentro = os.path.commonpath([exe, raiz]) == raiz
    except ValueError:      # discos diferentes no Windows
        dentro = False
    if dentro:
        rel = os.path.relpath(exe, raiz)
        return rel if os.sep == "/" else f".\\{rel}"
    return f'"{exe}"' if " " in exe else exe


def _rodape(caso: str, passo: dict) -> None:
    """As saidas do passo. `--executar` PRIMEIRO quando o passo permite.

    Estava faltando, e o efeito foi um DBA travado: as tres opcoes antigas
    (`--confirmar`, `--avancar`, `--pular`) pressupoem todas que a consulta JA
    foi rodada em outro lugar. Quem lia "Comando: SELECT ..." e depois
    "Executou?" nao tinha para onde ir - o caminho existia (`--executar`, que
    roda a consulta pela mesma trava da coleta, grava a evidencia e reavalia as
    hipoteses) e nao era oferecido no unico lugar onde importa.

    Quando o passo NAO da para executar, o rodape diz por que. Omitir a linha
    faria a ausencia parecer esquecimento, e o DBA perguntaria de novo.
    """
    py = _py()
    try:
        prep = executor.preparar(passo or {})
    except Exception:
        prep = {"pode": False, "motivo": ""}

    # O passo ja foi executado NESTA conducao? Muda qual e a proxima acao
    # natural. Sem isso, quem acabou de rodar `--executar` via o MESMO comando
    # oferecido em primeiro lugar - o fluxo mandava repetir o que ja foi feito
    # em vez de seguir.
    ja = _ja_executado(caso, passo)

    print()
    if prep.get("pode") and not ja:
        falta = prep.get("parametros") or []
        extra = ""
        if falta:
            # Parametro pendente vira `--param`: dizer so "--executar" daria
            # erro na cara de quem copiou.
            extra = "".join(f' --param {n}=<valor>' for n in falta)
        print("  EXECUTAR AQUI (read-only):")
        print(f"    {py} GUIA.py {caso} --executar{extra}")
        print("    roda a consulta pela mesma trava da coleta, grava a "
              "evidencia no caso e reavalia as hipoteses")
        if prep.get("recusados"):
            print(f"    ({len(prep['recusados'])} comando(s) do passo ficam "
                  f"de fora - ver o motivo com --executar)")
    elif ja:
        print(f"  JA EXECUTADO aqui ({ja} leitura(s) gravada(s) neste passo).")
        print(f"    SEGUIR:  {py} GUIA.py {caso} --avancar \"o que viu\"")
        print(f"    rodar de novo, se o ambiente mudou:  "
              f"{py} GUIA.py {caso} --executar")
    else:
        motivo = prep.get("motivo") or "o passo nao e uma consulta read-only"
        print(f"  Este passo NAO roda aqui: {motivo}")
        print("    execute no seu terminal/SQL*Plus e volte com --confirmar")

    if ja:
        # As saidas secundarias continuam, mas sem competir com a principal.
        print(f"\n  Registrar acao: {py} GUIA.py {caso} --confirmar \"o que fez\"")
        print(f"  Nao serve:      {py} GUIA.py {caso} --pular \"por que\"")
        return
    print(f"\n  Ja executou?  {py} GUIA.py {caso} --confirmar \"o que voce fez\"")
    print(f"  Seguir:       {py} GUIA.py {caso} --avancar \"o que viu\"")
    print(f"  Nao serve:    {py} GUIA.py {caso} --pular \"por que\"")


def _ja_executado(caso: str, passo: dict) -> int:
    """Quantas leituras JA foram gravadas para o passo atual.

    Le as saidas que o roteiro registrou com `origem='executado'` e o indice do
    passo. Colada a mao nao conta: ali o framework nao sabe o que foi rodado
    de fato, e afirmar "ja executado" seria afirmar sobre o que nao viu.
    """
    if not isinstance(passo, dict):
        return 0
    idx = passo.get("indice")
    if idx is None:
        return 0
    try:
        saidas = guia.estado(caso).get("saidas") or []
    except Exception:
        return 0
    return sum(1 for s in saidas
               if s.get("origem") == "executado"
               and s.get("passo_executado") == idx)


def _mostrar_pendencias(caso: str) -> int:
    abertas = guia.pendencias_abertas(caso)
    _titulo(f"CHECKLIST POS-ACAO - {caso}")
    if not abertas:
        print("  Nada em aberto.")
        return 0
    for p in abertas:
        print(f"\n  [ ] {p['id']:<12} {p['texto']}")
        print(f"      {p.get('porque', '')}")
    print(f"\n  Concluir:  {_py()} GUIA.py {caso} --feito <id> --nota \"...\"")
    return 0


def _ler_saida(caso: str, origem: str) -> int:
    """Le a saida de um comando e mostra o que ela mudou no raciocinio.

    Aceita arquivo ou '-' (entrada padrao) porque as duas formas aparecem: quem
    roda no Citrix salva a saida num .txt, quem esta no terminal faz pipe.
    """
    if origem == "-":
        texto = sys.stdin.read()
    else:
        try:
            with open(origem, encoding="utf-8", errors="replace") as fh:
                texto = fh.read()
        except OSError as e:
            print(f"[ERRO] nao consegui ler '{origem}': {e}")
            return 2

    d = guia.registrar_saida(caso, texto)
    _titulo(f"SAIDA LIDA - {caso}")
    if d["aviso"]:
        print(f"  {d['aviso']}")
    for a in d["achados"]:
        print(f"\n  [{a['severity']}] {a['title']}")
        print(f"      porque: {a['porque']}")
    if d["mudou"]:
        print("\n  O que mudou no raciocinio:")
        for m in d["mudou"]:
            print(f"    - {m['texto']}")
    elif d["achados"]:
        print("\n  Nenhuma hipotese mudou de veredito.")
    print()
    return _mostrar_passo(caso)


# ── Executar a verificacao no proprio framework ──────────────
def _resolver_conexao(caso: str, args) -> tuple:
    """(dsn, user) para o caso: o que foi pedido agora, senao o que o caso sabe.

    O caso JA guarda de onde a evidencia veio (`meta.json` traz conn/dsn/user,
    gravado pela coleta). Perguntar de novo o servidor no meio da conducao e
    pedir para o DBA errar: o passo seria executado no banco vizinho, e a saida
    entraria no caso como se fosse do banco do incidente.
    """
    meta = mod_caso.ler(caso) or {}
    dsn = args.dsn or ""
    user = args.user or ""
    if not dsn and args.conn:
        achadas = conn_repo.find(args.conn)
        if not achadas:
            print(f"[ERRO] conexao '{args.conn}' nao esta no repositorio.")
            print("       Veja a lista com: COLETAR.py --list-conns")
            return "", ""
        dsn = achadas[0].dsn
        user = user or achadas[0]["user"]
        print(f"  -> {achadas[0]}")
    if not dsn:
        dsn = meta.get("dsn") or ""
        user = user or meta.get("user") or ""
        if dsn:
            print(f"  -> do caso: {meta.get('conn') or dsn}")
    if not dsn:
        print("[ERRO] este caso nao registrou de qual banco veio a evidencia.")
        print("       Informe --conn NOME (ou --dsn host:porta/service).")
        return "", ""
    return dsn, (user or "dbaread")


def _abrir(dsn: str, user: str, password: str, rotulo: str):
    """Conexao read-only, com as MESMAS travas da coleta."""
    import oracledb

    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.callproc("dbms_application_info.set_module",
                     ["TROUBLESHOOT-RO", rotulo[:48]])
    except Exception:
        pass
    cur.execute("SET TRANSACTION READ ONLY")
    return conn, cur


def _packs_permitidos(cur, pack: str) -> tuple:
    """(conjunto de packs permitidos, de onde saiu a decisao).

    Reusa `_detect_packs`/`_PACK_LEVEL` da coleta: e a mesma pergunta ("o que a
    licenca deste banco autoriza"), e duas respostas diferentes para ela seria
    o pior dos mundos - o coletor pulando um check que o guiado executa.
    """
    from troubleshoot.collector import _PACK_LEVEL, _detect_packs

    pk = (pack or "auto").strip().lower()
    if pk in ("t", "tuning"):
        return _PACK_LEVEL["T"], "forcado (--pack T)"
    if pk in ("d", "diag", "diagnostic"):
        return _PACK_LEVEL["D"], "forcado (--pack D)"
    if pk in ("n", "none"):
        return _PACK_LEVEL["N"], "forcado (--pack N)"
    return _detect_packs(cur)


def _params_do_argv(lista) -> dict:
    out = {}
    for item in lista or []:
        if "=" not in item:
            raise ValueError(f"--param espera NOME=VALOR (recebi {item!r})")
        nome, valor = item.split("=", 1)
        out[nome.strip()] = valor.strip()
    return out


def _mostrar_recusa(v: dict) -> None:
    print(f"  Este passo nao roda no framework: {v['motivo']}")
    for r in v.get("recusados") or []:
        print(f"    - {r['porque']}")
    print("\n  Rode no servidor e traga a saida - ela e lida do mesmo jeito:")
    print(f"    {_py()} GUIA.py <caso> --saida arquivo.txt")


def _reportar(caso: str, d: dict, res: dict, arquivo: str) -> None:
    print(f"  {res['consultas']} consulta(s), {res['linhas']} linha(s) lida(s)"
          + (f", {len(res['erros'])} com erro" if res["erros"] else ""))
    print(f"  evidencia: {arquivo}")
    for e in res["erros"]:
        print(f"    !! {e}")
    for a in d["achados"]:
        print(f"\n  [{a['severity']}] {a['title']}")
        print(f"      porque: {a['porque']}")
    if d["mudou"]:
        print("\n  O que mudou no raciocinio:")
        for m in d["mudou"]:
            print(f"    - {m['texto']}")
    elif d["achados"]:
        print("\n  Nenhuma hipotese mudou de veredito.")
    elif d["aviso"]:
        print(f"\n  {d['aviso']}")


def _executar(caso: str, args) -> int:
    """Roda a verificacao do passo atual (ou o playbook todo) no framework."""
    tudo = bool(args.executar_tudo)
    if tudo:
        alvos = guia.passos_executaveis(caso)
        if not alvos:
            print("[ERRO] nenhum passo de verificacao executavel no roteiro "
                  "em curso (ou nao ha roteiro: use --iniciar).")
            return 2
    else:
        v = guia.preparar_execucao(caso)
        if not v["pode"]:
            _titulo(f"GUIA - {caso}")
            _mostrar_recusa(v)
            return 2
        alvos = [v]

    try:
        informados = _params_do_argv(args.param)
    except ValueError as e:
        print(f"[ERRO] {e}")
        return 2

    dsn, user = _resolver_conexao(caso, args)
    if not dsn:
        return 2

    # Parametro pendente e resolvido ANTES de pedir senha: descobrir que falta
    # um valor depois de digitar a senha e de conectar em producao e uma ida ao
    # banco jogada fora, e a senha teria de ser digitada outra vez.
    if not tudo:
        v = alvos[0]
        valores = dict(v.get("sugestoes") or {})
        valores.update(informados)
        faltando = [n for n in v.get("parametros") or []
                    if not str(valores.get(n) or "").strip()]
        if faltando:
            print(f"[ERRO] este passo precisa de: "
                  f"{', '.join('&' + n for n in faltando)}")
            for n in faltando:
                print(f"       --param {n}=<valor>")
            print("       (SID e numero de instancia nao sao sugeridos de "
                  "proposito: a sessao da coleta provavelmente nao existe "
                  "mais - use o que a tela mostra agora.)")
            return 2
        try:
            alvos[0] = dict(v, statements=[executor.aplicar_parametros(s, valores)
                                           for s in v["statements"]])
        except ValueError as e:
            print(f"[ERRO] {e}")
            return 2

    try:
        password = mod_senha.pedir(user)
    except mod_senha.SemComoPerguntar:
        for linha in mod_senha.explicar_falta(user):
            print(linha)
        return 2

    try:
        import oracledb  # noqa: F401
    except ImportError:
        print("[ERRO] python-oracledb nao instalado")
        return 1

    pb = (guia.estado(caso) or {}).get("playbook") or "passo"
    _titulo(f"EXECUTAR (read-only) - {caso}")
    print(f"  {user}@{dsn} (thin, read-only) | MODULE=TROUBLESHOOT-RO")

    conn = None
    try:
        conn, cur = _abrir(dsn, user, password, f"guia {pb}")
    except Exception as e:
        print(f"[ERRO] conexao falhou: {str(e).splitlines()[0]}")
        return 1

    rc = 0
    try:
        # Licenca ANTES de qualquer consulta. Diagnostics/Tuning Pack sao
        # auditados e cobrados por CONSULTAR o objeto: 26 passos do guiado
        # tocam DBA_HIST/ASH/DBMS_SQLTUNE, e sem este portao um clique num
        # banco sem licenca consome pack que o site nao tem. Mesma deteccao e
        # mesma tabela de niveis da coleta, para os dois nunca discordarem.
        permitidos, origem = _packs_permitidos(cur, args.pack)
        rotulo = "+".join(sorted(permitidos - {"NONE"})) or "NONE (so v$/dicionario)"
        print(f"  [pack] licenca considerada: {rotulo}  [{origem}]")

        for alvo in alvos:
            indice = (alvo.get("passo") or {}).get("indice", alvo.get("indice", 0))
            titulo = ((alvo.get("passo") or {}).get("titulo")
                      or alvo.get("titulo", ""))
            usados = alvo.get("valores") or {}
            sobre = ("  [" + ", ".join(f"&{k}={v}" for k, v in usados.items())
                     + "]") if usados else ""
            # O valor usado sai SEMPRE que existir: sete respostas sem dizer
            # sobre qual objeto foram medidas nao formam diagnostico.
            print(f"\n  PASSO {int(indice) + 1}: {titulo}{sobre}")
            permitido, barrado = executor.filtrar_por_pack(
                alvo["statements"], permitidos)
            for sql, pk in barrado:
                # Recusa VISIVEL: pular calado faria o passo parecer respondido
                # com menos evidencia do que ele pede, e ninguem saberia por que.
                print(f"    [pack] consulta pulada - exige {pk} Pack, "
                      f"nao licenciado neste banco")
                print(f"           {' '.join(sql.split())[:90]}")
            if not permitido:
                print("    nenhuma consulta deste passo cabe na licenca deste "
                      "banco. Rode no servidor onde houver licenca e cole a "
                      "saida, ou force com --pack se a licenca existir.")
                continue
            res = executor.executar_no_cursor(cur, permitido)
            arquivo = executor.caminho_evidencia(caso, pb, int(indice))
            executor.gravar_evidencia(
                arquivo, f"=== PASSO {int(indice) + 1}: {titulo} ===", res["texto"])
            # O registro usa o passo ATUAL do roteiro; no modo --executar-tudo o
            # roteiro nao anda, entao a evidencia de todos os passos entra
            # vinculada ao arquivo, que carrega o indice real.
            d = guia.registrar_saida(
                caso, res["texto"], origem="executado",
                extra={"arquivo": os.path.relpath(arquivo),
                       "consultas": res["consultas"],
                       "linhas": res["linhas"],
                       "passo_executado": int(indice)})
            # Erro de UMA consulta nao reprova a execucao: ele entra na
            # evidencia como `-- ERRO:` e o leitor o trata como "o comando nao
            # executou", sem tirar conclusao dali. Reprovar o passo inteiro
            # descartaria as leituras que responderam.
            _reportar(caso, d, res, arquivo)
    finally:
        if conn is not None:
            conn.close()

    print()
    return rc or _mostrar_passo(caso)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Troubleshooting guiado (playbook passo a passo, com risco)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Sem argumentos de acao, mostra o passo atual (ou o que sugerir).")
    # `caso` deixa de ser obrigatorio por causa do --playbooks: consultar o
    # catalogo e a coisa que se faz ANTES de ter caso, ou sem caso nenhum.
    ap.add_argument("caso", nargs="?", help="nome do caso (ex.: INC000987654)")
    ap.add_argument("--playbooks", action="store_true",
                    help="lista o catalogo da KB por plataforma (nao precisa de caso)")
    ap.add_argument("--sugerir", action="store_true",
                    help="o que a evidencia aponta e quais playbooks servem; sem "
                         "evidencia, as pistas do texto do alerta")
    ap.add_argument("--hipoteses", action="store_true",
                    help="so o raciocinio: o que sustenta, o que foi "
                         "descartado e o que ainda decide")
    ap.add_argument("--iniciar", metavar="PLAYBOOK",
                    help="comeca o roteiro de um playbook (zera o anterior)")
    ap.add_argument("--avancar", nargs="?", const="", metavar="NOTA",
                    help="conclui o passo atual e vai para o proximo")
    ap.add_argument("--pular", nargs="?", const="", metavar="MOTIVO",
                    help="pula o passo atual (o motivo fica registrado)")
    ap.add_argument("--confirmar", nargs="?", const="", metavar="DESCRICAO",
                    help="registra a execucao do passo como acao do caso, "
                         "com o risco correspondente")
    ap.add_argument("--pendencias", action="store_true",
                    help="checklist pos-acao em aberto")
    ap.add_argument("--feito", metavar="ID", help="conclui uma pendencia")
    ap.add_argument("--nota", default="", help="observacao (usar com --feito)")
    ap.add_argument("--saida", metavar="ARQUIVO",
                    help="le a saida do comando (arquivo, ou '-' para colar "
                         "pela entrada padrao) e reavalia as hipoteses")
    ap.add_argument("--executar", action="store_true",
                    help="roda a VERIFICACAO do passo atual no proprio "
                         "framework (read-only) e repontua as hipoteses")
    ap.add_argument("--executar-tudo", action="store_true",
                    help="roda todos os passos de verificacao do playbook em "
                         "curso (os que nao exigem parametro)")
    ap.add_argument("--param", action="append", metavar="NOME=VALOR",
                    help="valor de um &parametro do passo (ex.: "
                         "--param sql_id=7ktb2sm9ug3xw)")
    ap.add_argument("--conn", metavar="NOME",
                    help="banco onde executar; por padrao usa o que o caso "
                         "registrou na coleta")
    ap.add_argument("--dsn", help="DSN completo host:porta/service")
    ap.add_argument("--user", help="usuario de leitura (padrao: do caso)")
    ap.add_argument("--pack", default="auto", metavar="auto|T|D|N",
                    help="licenca a considerar ao executar: 'auto' detecta "
                         "CONTROL_MANAGEMENT_PACK_ACCESS; 'N' nao consulta "
                         "nada de Diagnostics/Tuning Pack")
    ap.add_argument("--encerrar", action="store_true", help="fecha o roteiro")
    args = ap.parse_args(argv)

    # Antes de exigir caso: o catalogo e consulta pura e nao depende de nada.
    if args.playbooks:
        return _listar_playbooks()
    if not args.caso:
        ap.print_help()
        return 2

    try:
        if args.iniciar:
            g = guia.iniciar(args.caso, args.iniciar)
            print(f"[OK] Roteiro iniciado: {g['titulo']}")
            return _mostrar_passo(args.caso)
        if args.avancar is not None:
            guia.avancar(args.caso, args.avancar)
            return _mostrar_passo(args.caso)
        if args.pular is not None:
            guia.pular(args.caso, args.pular)
            return _mostrar_passo(args.caso)
        if args.confirmar is not None:
            d = guia.confirmar_acao(args.caso, args.confirmar)
            print(f"[OK] Acao registrada no caso (risco {d['risco']}): {d['acao']}")
            if d["pendencias"]:
                print(f"     {len(d['pendencias'])} pendencia(s) do checklist "
                      "pos-acao abertas - veja com --pendencias")
            # Mostrar o passo AQUI e o que fecha o circuito. `--confirmar` e o
            # comando que o proprio rodape manda usar depois de executar, e ele
            # terminava com `return 0`: quem seguia a instrucao a risca recebia
            # "[OK] registrada" e um prompt vazio. `--avancar` e `--pular` ja
            # mostravam; so este nao mostrava, e era o mais indicado dos tres.
            #
            # Ele nao AVANCA de proposito - confirmar uma acao nao conclui o
            # passo (da para confirmar varias acoes no mesmo passo). Mas tem de
            # dizer onde voce esta e quais sao as saidas.
            return _mostrar_passo(args.caso)
        if args.saida:
            return _ler_saida(args.caso, args.saida)
        if args.executar or args.executar_tudo:
            return _executar(args.caso, args)
        if args.feito:
            guia.concluir_pendencia(args.caso, args.feito, args.nota)
            return _mostrar_pendencias(args.caso)
        if args.encerrar:
            guia.encerrar(args.caso)
            print("[OK] Roteiro encerrado.")
            return 0
    except ValueError as e:
        # "nenhum passo em curso" NAO e erro: e o roteiro que acabou, ou que
        # ainda nao comecou. Sair com [ERRO] e codigo 2 para um estado NORMAL
        # faz a pessoa achar que quebrou algo - e foi o que aconteceu:
        # terminar o ultimo passo e mandar --avancar devolvia
        # "[ERRO] nenhum passo em curso" em vez da conclusao da conducao.
        if "nenhum passo em curso" in str(e):
            return _mostrar_passo(args.caso)
        print(f"[ERRO] {e}")
        return 2

    if args.hipoteses:
        _titulo(f"HIPOTESES - {args.caso}")
        _mostrar_hipoteses(args.caso)
        return 0
    if args.pendencias:
        return _mostrar_pendencias(args.caso)
    if args.sugerir:
        return _mostrar_sugestoes(args.caso)
    return _mostrar_passo(args.caso)


if __name__ == "__main__":
    sys.exit(main())
