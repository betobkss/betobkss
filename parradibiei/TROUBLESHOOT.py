"""
TROUBLESHOOT.py - Orquestrador do framework de troubleshooting padronizado
==========================================================================

Le as evidencias de troubleshooting/<caso>/ , roteia cada arquivo para o
analisador certo (SQL Monitor, ADDM, Locks, Espaco), agrega tudo num
CaseResult e gera relatorio padronizado (dashboard HTML + PPTX) em
output/troubleshooting/ .

Coloque na pasta do caso:
  - saidas de query em .txt/.lst/.log  (locks, espaco, SQL monitor texto)
  - relatorios .html                    (ADDM, SQL Monitor, AWR/ASH)
  - prints .png/.jpg                     (evidencias visuais / Citrix)
  - (opcional) meta.json com {"title": "...", "subtitle": "...", "kind": "..."}

USO:
  python TROUBLESHOOT.py <caso>
  python TROUBLESHOOT.py <caso> --title "INC000987654 - SQL lento" --subtitle "DC1 / 23-07"
  python TROUBLESHOOT.py <caso> --no-pptx --open
  python TROUBLESHOOT.py --list
  python TROUBLESHOOT.py --new <caso>     # cria a pasta do caso
"""
from __future__ import annotations

import json
import os
import re
import sys

from troubleshoot import collector, perfil
from troubleshoot.analyzers import ANALYZERS, GENERIC
from troubleshoot.model import CaseResult, Section, Severity, worst
from troubleshoot.report import (
    build_exports,
    build_html,
    build_incident_md,
    build_index,
    build_pptx,
    record_and_diff,
    update_index,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASES_DIR = os.path.join(BASE_DIR, "troubleshooting")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
TEXT_EXT = (".txt", ".lst", ".log", ".out", ".html", ".htm", ".mht", ".mhtml")


def _head(path: str, n: int = 4000) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read(n)
    except Exception:
        return ""


_MARKER = re.compile(r"@@@FILE:\s*([^@\r\n]+?)\s*@@@")


def _expand_combined(evid: list[str], case_dir: str) -> list[str]:
    """Divide arquivos combinados (com marcadores @@@FILE:nome@@@) em arquivos
    individuais gravados na pasta do caso; roteados normalmente depois."""
    out: list[str] = []
    for path in evid:
        try:
            content = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            out.append(path)
            continue
        if "@@@FILE:" not in content:
            out.append(path)
            continue
        parts = _MARKER.split(content)  # [antes, nome1, corpo1, nome2, corpo2, ...]
        created = []
        for i in range(1, len(parts), 2):
            name = os.path.basename(parts[i].strip())
            body = parts[i + 1] if i + 1 < len(parts) else ""
            if not name or not body.strip():
                continue
            outp = os.path.join(case_dir, name)
            with open(outp, "w", encoding="utf-8") as f:
                f.write(body.strip() + "\n")
            if outp not in out:
                out.append(outp)
            created.append(name)
        print(f"  [split] {os.path.basename(path)} -> {len(created)} arquivo(s): "
              + ", ".join(created))
    return out


def _route(files: list[str]) -> dict:
    """Agrupa arquivos por analisador de maior score."""
    groups: dict = {}
    for path in files:
        head = _head(path)
        fname = os.path.basename(path)
        best, best_score = None, 0
        for az in ANALYZERS:
            sc = az.matches(fname, head)
            if sc > best_score:
                best, best_score = az, sc
        if best and best_score >= 20:
            groups.setdefault(best, []).append(path)
        else:
            groups.setdefault(None, []).append(path)
    return groups


def analisar_caso(case: str, title=None, subtitle=None, cases_dir: str = "",
                  expandir: bool = True, quieto: bool = False) -> CaseResult:
    """Le a evidencia do caso, roda os analisadores e devolve o CaseResult.

    **Nao escreve nada** (nem relatorio, nem history.json, nem indice) e nao
    acessa banco nenhum. E o que permite a rotina de melhoria continua
    reanalisar casos REAIS de producao sem reconectar e sem sujar o que ja foi
    entregue: so le o que a coleta gravou.

    expandir=False pula a divisao de arquivos combinados (`@@@FILE:`), que
    escreveria na pasta do caso.
    """
    base = cases_dir or CASES_DIR
    case_dir = os.path.join(base, case)
    if not os.path.isdir(case_dir):
        raise FileNotFoundError(case_dir)

    def _log(msg):
        if not quieto:
            print(msg)

    # meta.json opcional
    meta = {}
    meta_path = os.path.join(case_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except Exception:
            meta = {}

    # o relatorio usa a pasta de evidencias (ex.: para o acoes.sql do SQL Health Check)
    meta["case_dir"] = case_dir

    # Topologia detectada na coleta (fase 1). Entregue a TODOS os analisadores
    # via meta: e o que permite interpretar o mesmo numero de forma diferente
    # em standalone, RAC, RAC One Node e Exadata.
    topo_path = os.path.join(case_dir, "topology.json")
    if os.path.exists(topo_path):
        try:
            meta["topologia"] = json.load(open(topo_path, encoding="utf-8"))
        except Exception:
            pass

    all_files = [os.path.join(case_dir, f) for f in sorted(os.listdir(case_dir))
                 if os.path.isfile(os.path.join(case_dir, f))]
    images = [f for f in all_files if f.lower().endswith(IMG_EXT)]
    evid = [f for f in all_files if f.lower().endswith(TEXT_EXT)]
    if expandir:
        evid = _expand_combined(evid, case_dir)

    result = CaseResult(
        case=case,
        kind=meta.get("kind", "misc"),
        title=title or meta.get("title", f"Troubleshooting - {case}"),
        subtitle=subtitle or meta.get("subtitle", ""),
        meta=meta,
    )

    groups = _route(evid)
    summaries = []
    kind_counts = {}
    for az, paths in groups.items():
        if az is None:
            continue
        try:
            sections, summary = az.analyze(paths, meta)
        except Exception as e:
            _log(f"[AVISO] Analisador {az.kind} falhou: {e}")
            result.erros.append(f"{az.kind}: {type(e).__name__}: {e}")
            sections, summary = [], ""
        for s in sections:
            result.add(s)
            kind_counts[az.kind] = kind_counts.get(az.kind, 0) + 1
        if summary:
            sev = worst([s.severity for s in sections]) if sections else Severity.INFO
            summaries.append(f"[{az.title}] {summary}")
            result.summary_items.append((az.title, summary, sev))
        _log(f"  [{az.kind}] {len(paths)} arquivo(s) -> {len(sections)} secao(oes)")

    # arquivos nao classificados -> analisador generico (fallback)
    unclass = groups.get(None, [])
    if unclass:
        _log(f"  [generico] {len(unclass)} arquivo(s) nao classificado(s): "
             + ", ".join(os.path.basename(p) for p in unclass))
        try:
            gsecs, gsum = GENERIC.analyze(unclass, meta)
            for s in gsecs:
                result.add(s)
            if gsum:
                summaries.append(f"[Evidencias] {gsum}")
                result.summary_items.append(("Evidencias",  gsum,
                                             worst([s.severity for s in gsecs]) if gsecs else Severity.INFO))
        except Exception as e:
            _log(f"[AVISO] Analisador generico falhou: {e}")
            result.erros.append(f"generico: {type(e).__name__}: {e}")

    # imagens -> secao de evidencias
    if images:
        result.add(Section(title="Evidencias (capturas)", images=images,
                            note=f"{len(images)} imagem(ns) anexada(s)."))

    # resumo executivo (texto - fallback; a apresentacao usa summary_items)
    result.summary = meta.get("summary") or "  ".join(summaries) or \
        "Nenhum analisador reconheceu as evidencias. Verifique o conteudo da pasta do caso."
    if kind_counts and result.kind == "misc":
        result.kind = max(kind_counts, key=kind_counts.get)
    _aplicar_perfil(result)
    _avisar_coleta_incompleta(result, case_dir)
    return result


def _avisar_coleta_incompleta(result: CaseResult, case_dir: str) -> None:
    """Check que NAO rodou tem de aparecer no relatorio.

    Quando um check falha, o arquivo nao existe (e desde o conserto do
    `_descartar_obsoleto` o da coleta anterior tambem e apagado). A secao
    simplesmente some do dashboard - e some em silencio, que e o problema:
    quem le uma pagina sem a secao "SQL Monitor" conclui que nao ha SQL
    custoso, nao que a consulta quebrou.

    Aconteceu de verdade: no APPHO114 o `sqlmon.txt` caiu com ORA-14552 e o
    `changes_params.txt` estourou 120s. O dashboard nao dizia nada, e o
    Health Score contava as duas dimensoes como "sem achado".

    A faixa "Analise incompleta" ja existia para analisador que EXPLODE; aqui
    ela passa a cobrir tambem o check que nao foi coletado. O vocabulario do
    que conta como falha vem do `collector.checks_pulados_por_erro`, o mesmo
    que a bateria usa.
    """
    log = os.path.join(case_dir, "coleta.log")
    if not os.path.exists(log):
        return
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            texto = fh.read()
    except OSError:
        return
    # So o ULTIMO bloco de coleta interessa: a pasta e reaproveitada e um
    # erro de tres coletas atras nao diz nada sobre a evidencia de agora.
    marca = (result.meta or {}).get("collected_at", "")[:10]
    if marca and marca in texto:
        texto = texto[texto.index(marca):]
    for s in collector.checks_pulados_por_erro(texto):
        result.coleta_incompleta.append(s)


def _aplicar_perfil(result: CaseResult) -> None:
    """Rebaixa o que e CRITICO em producao e ESPERADO fora dela.

    Aqui, e nao dentro de cada analisador, porque foram TRES familias distintas
    a produzir os 41 criticos medidos na bateria de 13/09 (uma regra do
    catalogo de best practices, o analisador de contas e o de RMAN). Corrigir
    nos tres seria criar tres lugares para discordarem depois; a regra vive em
    `troubleshoot/perfil.py`, declarada como dado.

    O perfil sai do `meta.json` quando a coleta o gravou - e a forma certa,
    porque congela o que foi assumido no momento da coleta. Caso antigo nao
    muda de conclusao porque alguem editou `servidores.txt` depois.
    """
    p = perfil.do_caso(result.meta)
    result.meta["perfil"] = p
    result.meta["perfil_nota"] = perfil.explicar(p)
    if perfil.e_producao(p):
        return
    for s in result.sections:
        for f in s.findings:
            nova, motivo = perfil.ajustar_severidade(f.severity, s.title,
                                                     f.title, p)
            if nova == f.severity:
                continue
            f.severity = nova
            # O motivo entra no DETALHE e nao no titulo: o titulo e o que vai
            # para a lista e para o PPTX, e a lista precisa continuar dizendo
            # o que foi encontrado, nao o que se decidiu sobre ele.
            f.detail = (f.detail + "  " if f.detail else "") + f"[{motivo}]"


def run_case(case: str, title=None, subtitle=None, make_pptx=True, do_open=False,
             completo=False) -> CaseResult:
    try:
        result = analisar_caso(case, title, subtitle)
    except FileNotFoundError as e:
        print(f"[ERRO] Pasta do caso nao existe: {e}")
        print(f"       Colete do banco:  .\\coletar.bat {case} --conn <servidor>")
        print("       Modo Citrix (pasta vazia para colar a evidencia):")
        print(f"       .\\.venv\\Scripts\\python.exe TROUBLESHOOT.py --new {case}")
        sys.exit(1)

    # historico + diff vs execucao anterior (Health Score e mudancas)
    try:
        record_and_diff(result)
    except Exception as e:
        print(f"[AVISO] Falha no historico/diff: {e}")

    # gerar relatorios
    html_path = build_html(result)
    print(f"\n[OK] Dashboard: {html_path}")
    pptx_path = None
    if make_pptx:
        try:
            pptx_path = build_pptx(result)
            print(f"[OK] PPTX:      {pptx_path}")
        except Exception as e:
            print(f"[AVISO] Falha ao gerar PPTX: {e}")

    # relatorio no template padrao (sempre util; obrigatorio no --completo)
    try:
        md_path = build_incident_md(result)
        print(f"[OK] Relatorio: {md_path}")
    except Exception as e:
        print(f"[AVISO] Falha ao gerar relatorio: {e}")

    # export CSV/JSON dos findings (BI / integracao / ticket)
    try:
        _jp, cp = build_exports(result)
        print(f"[OK] Export:    {cp}")
    except Exception as e:
        print(f"[AVISO] Falha ao exportar CSV/JSON: {e}")

    # atualizar indice de casos
    try:
        update_index(result)
        idx = build_index()
        print(f"[OK] Indice:    {idx}")
    except Exception as e:
        print(f"[AVISO] Falha ao atualizar indice: {e}")

    if completo:
        base = f"output\\troubleshooting\\{result.case}"
        print("\n=== redija -completo: caso consolidado ===")
        print(f"  Pasta     : {base}\\")
        print(f"  Dashboard : {base}\\{result.case}.html")
        # So anuncia o PPTX se ele foi REALMENTE gerado agora. Antes a linha
        # era fixa: com --no-pptx (ou com a geracao falhando) o resumo apontava
        # um .pptx de execucao antiga como se fosse novo, e o deck errado ia
        # parar na mao do gestor.
        if pptx_path:
            print(f"  PPTX      : {base}\\{result.case}.pptx")
        else:
            print("  PPTX      : nao gerado nesta execucao (--no-pptx)")
        print(f"  Relatorio : {base}\\{result.case}_relatorio.md")
        print(f"  Severidade: {Severity.LABEL.get(result.severity,'')}  |  Health Score: {result.score}/100")
        if result.diff:
            d = result.diff
            # `varied` entra na linha porque ele SAIU de novos/resolvidos:
            # omiti-lo faria o total nao fechar para quem compara execucoes.
            print(f"  Mudancas  : +{len(d.get('new',[]))} novos, "
                  f"-{len(d.get('resolved',[]))} resolvidos, "
                  f"{len(d.get('changed',[]))} mudou severidade, "
                  f"{len(d.get('varied',[]))} mudou de medida "
                  f"(score {d.get('prev_score')} -> {d.get('score')})")

    if do_open:
        try:
            os.startfile(html_path)  # type: ignore[attr-defined]
        except Exception:
            pass
    return result


def _new_case(case: str):
    d = os.path.join(CASES_DIR, case)
    os.makedirs(d, exist_ok=True)
    meta = os.path.join(d, "meta.json")
    if not os.path.exists(meta):
        json.dump({"title": f"Troubleshooting - {case}", "subtitle": "", "kind": "misc",
                   "summary": ""}, open(meta, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[OK] Caso criado: {d}")
    print("     Coloque aqui os .txt/.html/.png de evidencia e rode:")
    print(f"       python TROUBLESHOOT.py {case}")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        if os.path.isdir(CASES_DIR):
            cases = [d for d in sorted(os.listdir(CASES_DIR))
                     if os.path.isdir(os.path.join(CASES_DIR, d))]
            print("Casos:" if cases else "Nenhum caso em troubleshooting\\")
            for c in cases:
                print(f"  - {c}")
        else:
            print("Pasta troubleshooting\\ ainda nao existe.")
        return
    if args[0] == "--new":
        if len(args) < 2:
            print("Uso: python TROUBLESHOOT.py --new <caso>")
            return
        _new_case(args[1])
        return
    if args[0] == "--index":
        print(f"[OK] Indice: {build_index()}")
        return

    case = args[0]
    title = subtitle = None
    completo = ("--completo" in args) or ("-completo" in args)
    make_pptx = "--no-pptx" not in args
    do_open = "--open" in args
    i = 1
    while i < len(args):
        if args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]; i += 2; continue
        if args[i] == "--subtitle" and i + 1 < len(args):
            subtitle = args[i + 1]; i += 2; continue
        i += 1

    print("=" * 70)
    print(f"  TROUBLESHOOT - caso: {case}" + ("  (COMPLETO)" if completo else ""))
    print("=" * 70)
    run_case(case, title, subtitle, make_pptx, do_open, completo)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
