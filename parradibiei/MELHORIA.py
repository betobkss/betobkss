r"""
MELHORIA.py - rotina de melhoria continua do framework.

Melhoria continua costuma morrer como boa intencao. Aqui ela e um comando que
sempre roda as mesmas tres frentes e deixa um relatorio datado:

  1. CORPUS (offline)   reanalisa a evidencia dos casos JA coletados e compara
                        com o baseline aceito. Responde: "a mudanca de hoje
                        alterou o diagnostico de algum caso ja entregue?"
  2. HIGIENE (offline)  suite de testes, pins de dependencia e catalogo.
  3. FONTES (offline)   radar da documentacao oficial e da comunidade Oracle,
                        com data da ultima revisao e a nota do que foi aplicado.

A rotina e 100% OFFLINE: nao abre conexao com banco nenhum.

REGRA DE OURO, definida com o DBA:
  - caso de **producao**: usa-se **so o resultado ja coletado**. A rotina nunca
    reconecta em producao e nunca escreve na pasta do caso.
  - a bateria contra bancos reais (`TESTE_HOMOLOG.py`) NAO faz parte desta
    rotina nem do framework distribuido: e ferramenta interna, rodada sob
    demanda no terminal quando ha update ou correcao de bug a validar.
  - `--advisor` nunca entra: escreve no banco e consome Tuning Pack.

USO:
  .\.venv\Scripts\python.exe MELHORIA.py                 # as tres frentes
  .\.venv\Scripts\python.exe MELHORIA.py --corpus
  .\.venv\Scripts\python.exe MELHORIA.py --aceitar       # vira o novo baseline
  .\.venv\Scripts\python.exe MELHORIA.py --revisei oracle-base --nota "..."
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from troubleshoot import caso as caso_lifecycle
from troubleshoot import melhoria as M

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "output", "melhoria")
FONTES_JSON = os.path.join(OUT_DIR, "fontes.json")

# Radar curado: o que vale a pena reler para manter o framework atual. Fica em
# codigo (e nao so na cabeca de quem toca o projeto) para a rotina cobrar.
FONTES = [
    ("oracle-docs", "Oracle Database Documentation (Performance Tuning Guide, "
     "Reference: v$/dba_ views novas por versao)",
     "https://docs.oracle.com/en/database/oracle/oracle-database/"),
    ("mos", "My Oracle Support - notas e bugs dos ORA- que aparecerem na coleta",
     "https://support.oracle.com"),
    ("oracle-base", "oracle-base.com (Tim Hall) - artigos praticos por versao",
     "https://oracle-base.com"),
    ("tanel-poder", "tanelpoder.com - ASH/wait interface, snapper, scripts",
     "https://tanelpoder.com/posts/"),
    ("jonathan-lewis", "Jonathan Lewis - otimizador e leitura de planos",
     "https://jonathanlewis.wordpress.com"),
    ("optimizer-blog", "Oracle Optimizer Blog (Nigel Bayliss) - mudancas de plano",
     "https://blogs.oracle.com/optimizer/"),
    ("asktom", "AskTOM (Connor McDonald / Chris Saxon)",
     "https://asktom.oracle.com"),
    ("mike-dietrich", "Mike Dietrich - upgrade/migracao e regressoes conhecidas",
     "https://mikedietrichde.com"),
    ("franck-pachot", "Franck Pachot - internals, execucao e cloud",
     "https://dev.to/franckpachot"),
    ("ahf-orachk", "Oracle AHF / orachk - checks de saude oficiais (comparar com "
     "o nosso mcp/catalog.json)",
     "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2550798.1"),
    ("python-oracledb", "Release notes do python-oracledb (driver da coleta)",
     "https://python-oracledb.readthedocs.io/en/latest/release_notes.html"),
    ("method-r", "Method R (Cary Millsap) - priorizacao por DB time",
     "https://method-r.com"),
]


def _agora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _casos(cases_dir: str = "") -> list:
    return caso_lifecycle.listar_casos(cases_dir)


# ── 1. corpus ─────────────────────────────────────────────────
def fase_corpus(aceitar: bool = False) -> dict:
    casos = _casos()
    print(f"[corpus] reanalisando {len(casos)} caso(s) a partir da evidencia "
          "gravada (sem tocar em banco)")
    atual = M.snapshot_corpus(casos)
    base = M.ler_baseline()
    if not base:
        print("[corpus] ainda nao ha baseline: gravando o retrato de hoje como "
              "ponto de partida")
        M.gravar_baseline(atual, quando=_agora())
        return {"primeira_vez": True, "n_casos": len(atual),
                "regressoes": [], "informativo": []}

    d = M.comparar(base, atual)
    for r in d["regressoes"]:
        print(f"  [REGRESSAO] {r['caso']}: {r['tipo']} - {r['detalhe']}")
    for i in d["informativo"]:
        print(f"  [info] {i['caso']}: {i['tipo']} - {i['detalhe']}")
    if not d["regressoes"] and not d["informativo"]:
        print("  sem mudanca: o diagnostico de todos os casos esta igual ao baseline")
    if aceitar:
        p = M.gravar_baseline(atual, quando=_agora())
        print(f"[corpus] novo baseline aceito: {p}")
    d["primeira_vez"] = False
    return d


# ── 2. higiene ────────────────────────────────────────────────
def fase_higiene() -> dict:
    print("[higiene] rodando a suite de testes")
    t = M.rodar_testes()
    print(f"  testes: {t['passaram']} passaram, {t['falharam']} falharam "
          f"(rc={t['rc']})")
    deps = M.checar_dependencias()
    for d in deps:
        print(f"  [dep] {d['pacote']}: pin {d['pin']} x instalado "
              f"{d['instalado'] or '(ausente)'} - {d['situacao']}")
    cat = M.checar_catalogo()
    if cat.get("erro"):
        print(f"  [catalogo] {cat['erro']}")
    else:
        print(f"  catalogo: {cat['total']} checks ({cat['com_pack']} exigem pack)")
        if cat["sem_sql"]:
            print(f"  [gap] {len(cat['sem_sql'])} check(s) sem SQL: "
                  + ", ".join(cat["sem_sql"][:8]))
        if cat["sem_analisador"]:
            print(f"  [gap] {len(cat['sem_analisador'])} check(s) coletam sem "
                  "ninguem analisar: " + ", ".join(cat["sem_analisador"][:8]))
        if cat["sem_referencia"]:
            print(f"  [gap] {len(cat['sem_referencia'])} limiar(es) sem fonte "
                  "citada: " + ", ".join(cat["sem_referencia"][:8]))
    return {"testes": t, "dependencias": deps, "catalogo": cat}


# ── 3. fontes ─────────────────────────────────────────────────
def _ler_fontes() -> dict:
    if not os.path.exists(FONTES_JSON):
        return {}
    try:
        with open(FONTES_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _gravar_fontes(d: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(FONTES_JSON, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def marcar_revisao(chave: str, nota: str = "") -> bool:
    if chave not in {c for c, _, _ in FONTES}:
        return False
    d = _ler_fontes()
    reg = d.setdefault(chave, {"historico": []})
    reg["ultima_revisao"] = _agora()
    if nota:
        reg["historico"] = ([{"em": _agora(), "nota": nota}, *reg.get("historico", [])])[:10]
    _gravar_fontes(d)
    return True


def fase_fontes(dias: int = 30) -> dict:
    """Nao faz scraping: o proxy bloqueia e leitura tecnica exige criterio. A
    rotina cobra a revisao e guarda o que foi aplicado."""
    d = _ler_fontes()
    hoje = dt.datetime.now()
    vencidas = []
    print(f"[fontes] radar da documentacao e da comunidade (revisar a cada "
          f"{dias} dias)")
    for chave, desc, url in FONTES:
        ult = (d.get(chave) or {}).get("ultima_revisao", "")
        idade = None
        if ult:
            try:
                idade = (hoje - dt.datetime.strptime(ult, "%Y-%m-%d %H:%M")).days
            except ValueError:
                idade = None
        if idade is None or idade >= dias:
            vencidas.append({"chave": chave, "desc": desc, "url": url,
                             "ultima_revisao": ult or "nunca"})
            print(f"  [ ] {chave:<16} {ult or 'nunca revisado'}  {url}")
        else:
            print(f"  [x] {chave:<16} ha {idade} dia(s)")
    if vencidas:
        print(f"  -> {len(vencidas)} fonte(s) a revisar. Ao aplicar algo: "
              "MELHORIA.py --revisei <chave> --nota \"o que mudou aqui\"")
    return {"vencidas": vencidas, "total": len(FONTES)}


# ── relatorio ─────────────────────────────────────────────────
def escrever_relatorio(res: dict, quando: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f"RELATORIO_{quando[:10]}.md")
    L = [f"# Melhoria continua - {quando}", ""]

    c = res.get("corpus")
    if c:
        L += ["## 1. Corpus de regressao (offline, sem tocar em producao)", ""]
        if c.get("primeira_vez"):
            L += [f"Baseline criado com {c['n_casos']} caso(s). "
                  "As proximas execucoes vao comparar contra ele.", ""]
        else:
            L += [f"{c.get('n_casos', 0)} caso(s) reanalisados | "
                  f"**{len(c['regressoes'])} regressao(oes)** | "
                  f"{len(c['informativo'])} informativo(s)", ""]
            for r in c["regressoes"]:
                L.append(f"- **{r['caso']}** ({r['tipo']}): {r['detalhe']}")
                for n in r.get("novos", []):
                    L.append(f"  - novo: `{n}`")
                for s in r.get("sumiram", []):
                    L.append(f"  - sumiu: `{s}`")
            if not c["regressoes"]:
                L.append("- Nenhuma regressao: o diagnostico dos casos ja "
                         "entregues continua igual.")
            L.append("")

    h = res.get("higiene")
    if h:
        t = h["testes"]
        cat = h["catalogo"]
        L += ["## 2. Higiene", "",
              f"- Testes: **{t['passaram']} passaram**, {t['falharam']} falharam",
              f"- Catalogo: {cat.get('total', '?')} checks "
              f"({cat.get('com_pack', '?')} exigem pack)"]
        for chave, texto in (("sem_sql", "sem SQL"),
                             ("sem_analisador", "coletados sem analisador"),
                             ("sem_referencia", "com limiar sem fonte citada")):
            itens = cat.get(chave) or []
            if itens:
                L.append(f"- Gap - {len(itens)} check(s) {texto}: "
                         + ", ".join(f"`{i}`" for i in itens[:12]))
        for d in h["dependencias"]:
            L.append(f"- Dependencia `{d['pacote']}`: pin {d['pin']}, instalado "
                     f"{d['instalado'] or '(ausente)'} - {d['situacao']}")
        if not h["dependencias"]:
            L.append("- Dependencias: todas iguais ao pin")
        L.append("")

    f = res.get("fontes")
    if f:
        L += ["## 3. Fontes a revisar", ""]
        for v in f["vencidas"]:
            L.append(f"- [ ] **{v['chave']}** - {v['desc']} "
                     f"(ultima: {v['ultima_revisao']}) <{v['url']}>")
        if not f["vencidas"]:
            L.append("- Tudo revisado dentro da janela.")
        L.append("")

    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotina de melhoria continua")
    ap.add_argument("--corpus", action="store_true", help="so o corpus de regressao")
    ap.add_argument("--higiene", action="store_true", help="so testes/deps/catalogo")
    ap.add_argument("--fontes", action="store_true", help="so o radar de fontes")
    ap.add_argument("--tudo", action="store_true", help="as tres frentes")
    ap.add_argument("--aceitar", action="store_true",
                    help="aceita o retrato de hoje como novo baseline do corpus")
    ap.add_argument("--dias", type=int, default=30,
                    help="janela de revisao das fontes (padrao 30)")
    ap.add_argument("--revisei", metavar="CHAVE",
                    help="marca uma fonte como revisada hoje")
    ap.add_argument("--nota", default="", help="o que voce aplicou da fonte")
    args = ap.parse_args()

    if args.revisei:
        if not marcar_revisao(args.revisei, args.nota):
            print(f"[ERRO] fonte desconhecida: {args.revisei}")
            print("       validas: " + ", ".join(c for c, _, _ in FONTES))
            return 1
        print(f"[OK] {args.revisei} marcada como revisada em {_agora()}")
        return 0

    so_uma = args.corpus or args.higiene or args.fontes
    fazer = {
        "corpus": args.tudo or args.corpus or not so_uma,
        "higiene": args.tudo or args.higiene or not so_uma,
        "fontes": args.tudo or args.fontes or not so_uma,
    }

    quando = _agora()
    print("=" * 70)
    print(f"  MELHORIA CONTINUA - {quando}")
    print("  rotina 100% offline: so evidencia ja coletada, sem conexao")
    print("=" * 70)

    res = {}
    if fazer["corpus"]:
        res["corpus"] = fase_corpus(args.aceitar)
    if fazer["higiene"]:
        res["higiene"] = fase_higiene()
    if fazer["fontes"]:
        res["fontes"] = fase_fontes(args.dias)

    p = escrever_relatorio(res, quando)
    print(f"\n[OK] Relatorio: {p}")

    reg = res.get("corpus", {}).get("regressoes") or []
    testes_ok = res.get("higiene", {}).get("testes", {}).get("rc", 0) == 0
    # Com `--aceitar` o DBA ja olhou e assumiu a mudanca: mandar "investigue
    # antes de aceitar" depois de aceitar e ruido, e sair com 1 quebraria
    # qualquer automacao que use o comando para virar o baseline.
    if reg and not args.aceitar:
        print(f"[ATENCAO] {len(reg)} regressao(oes) no corpus. Investigue antes "
              "de aceitar o baseline (--aceitar).")
    elif reg:
        print(f"[OK] {len(reg)} mudanca(s) de diagnostico aceita(s) como novo "
              "baseline. Explique no commit POR QUE o diagnostico mudou.")
    return 1 if ((reg and not args.aceitar) or not testes_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
