"""
Gerador de relatorios padronizados (dashboard HTML + PPTX) a partir de um
CaseResult. Mantem o visual consistente para qualquer tipo de troubleshooting.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re

from . import caso as caso_lifecycle
from . import destaques as destaques_mod
from . import guia, marca, operar, raciocinio, reincidencia
from .correlacao import correlacao_markdown
from .model import CaseResult, Finding, Section, Severity
from .nextstep import advisor_command, advisor_ja_rodou, next_command
from .plano import agrupar_por_horizonte, montar_plano, plano_markdown, resumo_plano

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")


def _case_dir(case: str) -> str:
    """Subpasta dedicada do caso em output/troubleshooting/<caso>/ (criada se nao existir)."""
    d = os.path.join(OUTPUT_DIR, case)
    os.makedirs(d, exist_ok=True)
    return d


# ── Helpers ────────────────────────────────────────────────
def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


# Cores de severidade para o TEMA CLARO do dashboard. Sao versoes escurecidas
# das de Severity.COLOR: as originais (#f59e0b, #22c55e) nao tem contraste
# suficiente como texto sobre fundo branco. Severity.COLOR continua sendo a
# fonte para o PPTX (fundo escuro dos boxes).
_SEV_HTML = {
    Severity.OK: "#15803d",
    Severity.WARN: "#b45309",
    Severity.CRIT: "#b91c1c",
    Severity.INFO: "#0369a1",
}


def _sc(sev: str) -> str:
    """Cor de uma severidade no dashboard (tema claro)."""
    return _SEV_HTML.get(sev, "#64748b")


def _chip(sev: str) -> str:
    color = _sc(sev)
    label = Severity.LABEL.get(sev, "")
    return f'<span class="chip" style="background:{color}1a;color:{color};border:1px solid {color}44">{label}</span>'


def _score_color(score: int) -> str:
    return "#15803d" if score >= 90 else "#b45309" if score >= 70 else "#b91c1c"


# A DATA e parte da identidade do achado: um salto de carga no dia 29 e outro
# no dia 30 sao achados diferentes, e normalizar os dois para "N-N-N" os
# fundiria. Ela e protegida antes de normalizar as medidas.
_RE_DATA_ISO = re.compile(r"\d{4}-\d\d-\d\d")
# Numero SOLTO: nao preso a letra, digito ou underscore. Pega "97.0%", "(43x)"
# e "10209s", e deixa em paz `TS_KPVI_01` (o `01` vem depois de `_`) e o
# sql_id `3kaytwa0p3xqt`. Medido na frota: normalizar TODO numero fundia
# tablespaces cujo nome difere so no sufixo - trocaria o erro de lado.
_RE_MEDIDA = re.compile(r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?")
_SENTINELA = "\x00"


def chave_do_achado(titulo: str) -> str:
    """Identidade ESTAVEL de um achado: o texto sem as medidas.

    A data sai de cena por um marcador SEM DIGITO e volta depois. A primeira
    versao trocava so os hifens (`2026-08-29` -> `2026<x>08<x>29`) e os
    NUMEROS da data continuavam sendo normalizados: dois saltos de carga, um no
    dia 29 e outro no dia 30, caiam na mesma chave. O marcador tem de esconder
    o digito, nao o separador.
    """
    datas: list = []

    def _guarda(m):
        datas.append(m.group(0))
        return f"{_SENTINELA}{chr(64 + len(datas))}{_SENTINELA}"

    protegido = _RE_DATA_ISO.sub(_guarda, titulo or "")
    chave = _RE_MEDIDA.sub("N", protegido)
    for i, d in enumerate(datas, 1):
        chave = chave.replace(f"{_SENTINELA}{chr(64 + i)}{_SENTINELA}", d, 1)
    return chave


def _separar_variacao(diff: dict, antes: dict, agora: dict) -> None:
    """Tira de `new`/`resolved` o que e o MESMO achado com outra medida.

    O diff identificava achado pelo TITULO cru, e o titulo carrega o valor
    medido. Numa recoleta, "TS_A com 97.0% de uso" virava "com 91.0%" e o mesmo
    problema saia como UM novo mais UM resolvido.

    "Resolvido" e palavra forte - diz que o problema ACABOU. Medido nas duas
    ultimas execucoes dos 26 casos da frota: de 282 "novos" e 312
    "resolvidos", 79 pares (27% do que o dashboard anunciava) eram o mesmo
    achado com outro numero. Os dois piores:

        resolvido: ORA-00060 (43x) - Deadlock detectado
        novo:      ORA-00060 (40x) - Deadlock detectado

        resolvido: SQL 3sajd6ry4kc63 custoso: 10209s de duracao
        novo:      SQL 3sajd6ry4kc63 custoso: 10330s de duracao

    No segundo o SQL PIOROU (10209 -> 10330s) e o dashboard dizia "resolvido".

    A reclassificacao e feita DEPOIS do diff por titulo, e nao no lugar dele,
    de proposito: o casamento exato continua valendo para tudo o mais, e so o
    par (mesma chave, MESMA severidade) muda de balde. Severidade diferente
    continua em `changed`, que e onde ela sempre esteve.
    """
    por_chave_novo: dict = {}
    for t in diff.get("new") or []:
        por_chave_novo.setdefault(chave_do_achado(t), []).append(t)
    variou = []
    restam_res = []
    for t in diff.get("resolved") or []:
        k = chave_do_achado(t)
        fila = por_chave_novo.get(k) or []
        # so une quando a severidade tambem e a mesma: mudou de crit para warn
        # e informacao, nao variacao de medida
        par = next((n for n in fila if agora.get(n) == antes.get(t)), None)
        if par is None:
            restam_res.append(t)
            continue
        fila.remove(par)
        variou.append((t, par))
    diff["resolved"] = restam_res
    diff["new"] = [t for fila in por_chave_novo.values() for t in fila]
    # `varied` e novo no contrato: quem consome pode ignorar sem quebrar, e
    # quem quiser dizer "5 mudaram de valor" tem o dado.
    diff["varied"] = variou


def record_and_diff(result: CaseResult) -> dict:
    """Grava um snapshot do caso em history.json (subpasta do caso) e retorna o
    diff vs a execucao anterior: novos, resolvidos, mudanca de severidade e delta
    de score. Preenche result.diff."""
    cdir = _case_dir(result.case)
    hpath = os.path.join(cdir, "history.json")
    hist = []
    if os.path.exists(hpath):
        try:
            hist = json.load(open(hpath, encoding="utf-8"))
        except Exception:
            hist = []
    prev = hist[-1] if hist else None
    curr = {f.title: f.severity for s in result.sections for f in s.findings}
    diff: dict = {}
    if prev:
        pf = {d["title"]: d["severity"] for d in prev.get("findings", [])}
        diff = {
            "prev_at": prev.get("generated_at"),
            "prev_score": prev.get("score", 0),
            "score": result.score,
            "new": [t for t in curr if t not in pf],
            "resolved": [t for t in pf if t not in curr],
            "changed": [(t, pf[t], curr[t]) for t in curr
                        if t in pf and curr[t] != pf[t]],
        }
        _separar_variacao(diff, pf, curr)
    hist.append({
        "generated_at": result.generated_at,
        "severity": result.severity,
        "score": result.score,
        "findings": [{"title": f.title, "severity": f.severity}
                     for s in result.sections for f in s.findings],
    })
    if diff:
        diff["trend"] = [h.get("score") for h in hist[-8:]]
    try:
        json.dump(hist[-20:], open(hpath, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception:
        pass
    result.diff = diff
    return diff


def _diff_html(result: CaseResult) -> str:
    d = result.diff
    if not d:
        return ""
    delta = (d.get("score") or 0) - (d.get("prev_score") or 0)
    arrow = "&#9650;" if delta > 0 else ("&#9660;" if delta < 0 else "&#8226;")
    dcolor = _sc(Severity.OK) if delta > 0 else (_sc(Severity.CRIT) if delta < 0 else "#64748b")
    parts = [f'<div style="margin-bottom:8px">Score <b>{d.get("score")}</b>/100 '
             f'<span style="color:{dcolor};font-weight:700">{arrow} {delta:+d}</span> '
             f'<span class="ic-meta">(anterior {d.get("prev_score")} em '
             f'{_esc(d.get("prev_at"))})</span></div>']

    def _bloco(titulo, cor, itens):
        if not itens:
            return ""
        lis = "".join(f"<li>{_esc(x)}</li>" for x in itens[:15])
        return (f'<div style="color:{cor};font-weight:700;margin-top:6px">{titulo} '
                f'({len(itens)})</div><ul style="margin:4px 0">{lis}</ul>')

    parts.append(_bloco("Novos", _sc(Severity.CRIT), d.get("new")))
    parts.append(_bloco("Resolvidos", _sc(Severity.OK), d.get("resolved")))
    if d.get("changed"):
        lis = "".join(
            f'<li>{_esc(t)}: {Severity.LABEL.get(a,"")} &rarr; {Severity.LABEL.get(b,"")}</li>'
            for t, a, b in d["changed"][:15])
        parts.append(f'<div style="color:{_sc(Severity.WARN)};font-weight:700;margin-top:6px">'
                     f'Mudanca de severidade</div><ul style="margin:4px 0">{lis}</ul>')
    # Variacao de MEDIDA tem bloco proprio, e nao pode faltar: sem ele o
    # dashboard silenciaria o fato de um SQL ter ido de 10209s para 10330s -
    # o achado e o mesmo, a situacao PIOROU. Antes isso saia como
    # "+1 novo, -1 resolvido", que dizia o oposto.
    if d.get("varied"):
        lis = "".join(f"<li>{_esc(a)}<br>&rarr; {_esc(b)}</li>"
                      for a, b in d["varied"][:15])
        parts.append(
            f'<div style="color:{_sc(Severity.WARN)};font-weight:700;'
            f'margin-top:6px">Mesmo achado, outra medida '
            f'({len(d["varied"])})</div><ul style="margin:4px 0">{lis}</ul>')
    if not (d.get("new") or d.get("resolved") or d.get("changed")
            or d.get("varied")):
        parts.append('<div class="ic-meta">Sem mudancas de achados desde a ultima analise.</div>')
    trend = d.get("trend") or []
    if len(trend) >= 2:
        seq = " &rarr; ".join(f'<span style="color:{_score_color(v)}">{v}</span>' for v in trend)
        parts.append(f'<div style="margin-top:8px">Tendencia do score (ultimas '
                     f'{len(trend)}): {seq}</div>')
    return ('<div class="section"><h3>Mudancas desde a ultima analise</h3>'
            + "".join(parts) + "</div>")


def _img_data_uri(path: str) -> str | None:
    try:
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/{ext};base64,{b64}"
    except Exception:
        return None


# ── Dashboard HTML ─────────────────────────────────────────
_CSS = """
:root{--bg:#f1f5f9;--bg2:#ffffff;--bg3:#e8edf3;--card:#ffffff;--card2:#f8fafc;
--line:#e2e8f0;--line2:#cbd5e1;--txt:#0f172a;--mut:#64748b;
--acc:#2563eb;--brand:#ec0000;--green:#15803d;--orange:#b45309;--red:#b91c1c;
--mono:#0f766e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Roboto,system-ui,sans-serif;background:var(--bg);color:var(--txt);font-size:14px}
a{color:var(--acc)}

/* Header */
.header{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:16px;padding:12px 32px;
background:linear-gradient(135deg,#ffffff,#eef2f7);border-bottom:3px solid var(--brand);
box-shadow:0 1px 3px rgba(15,23,42,.08)}
.header h1{font-size:17px;font-weight:600}
.header .sub{color:var(--mut);font-size:12px;margin-top:2px}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:16px}
.hdr-score{text-align:right;line-height:1.1}
.hdr-score .v{font-size:26px;font-weight:800}
.hdr-score .k{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.6px}
.gen{color:var(--mut);font-size:11px;text-align:right;line-height:1.5}
.btn{background:var(--bg2);color:var(--txt);border:1px solid var(--line2);border-radius:6px;padding:6px 12px;cursor:pointer;font-size:12px}
.btn:hover{background:var(--acc);border-color:var(--acc);color:#fff}
.badge{display:inline-block;padding:5px 12px;border-radius:20px;font-weight:700;font-size:12px}

/* Tabs */
.main-tabs{display:flex;background:var(--bg2);border-bottom:1px solid var(--line);padding:0 32px;
position:sticky;top:61px;z-index:40;overflow-x:auto}
.main-tab{padding:11px 18px;cursor:pointer;color:var(--mut);font-size:13px;white-space:nowrap;
border-bottom:2px solid transparent;transition:.2s;user-select:none}
.main-tab:hover{color:var(--txt)}
.main-tab.active{color:var(--acc);border-bottom-color:var(--acc)}
.main-tab .n{background:var(--bg3);border-radius:9px;padding:0 6px;font-size:10px;margin-left:6px;font-weight:700}
.main-tab .n.crit{background:rgba(239,68,68,.2);color:var(--red)}

/* Controls */
.controls{padding:11px 32px;background:var(--bg2);border-bottom:1px solid var(--line);
display:flex;gap:12px;align-items:center;flex-wrap:wrap;position:sticky;top:100px;z-index:35}
.controls>label{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.cb-group{display:flex;gap:10px;align-items:center}
.cb-group label{display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px}
.cb-group input[type=checkbox]{accent-color:var(--acc)}
.search{background:var(--bg3);border:1px solid var(--line2);color:var(--txt);border-radius:6px;
padding:6px 10px;font-size:12px;min-width:250px}
.search::placeholder{color:var(--mut)}
.count{margin-left:auto;color:var(--mut);font-size:11px}

.content{max-width:1320px;margin:0 auto;padding:20px 32px 60px}
[hidden]{display:none!important}
.empty{color:var(--mut);font-size:13px;padding:24px;text-align:center;
background:var(--card);border:1px dashed var(--line2);border-radius:12px}

/* ── Resumo objetivo ──────────────────────────────────────────
   O resumo responde tres perguntas e para: como esta, o que quebrou, o que
   faco primeiro. Detalhe fica a um clique - nao empilhado na primeira tela. */
.hero{display:flex;align-items:center;gap:26px;background:var(--card);
border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin-bottom:16px;
box-shadow:0 1px 2px rgba(15,23,42,.05);flex-wrap:wrap}
.hero .sc{display:flex;align-items:baseline;gap:7px;line-height:1}
.hero .sc .v{font-size:46px;font-weight:800;letter-spacing:-1px}
.hero .sc .m{color:var(--mut);font-size:13px}
.hero .hs{display:flex;flex-direction:column;gap:4px}
.hero .hl{font-size:15px;font-weight:600}
.hero .hd{color:var(--mut);font-size:12px}
.hero .stats{margin-left:auto;display:flex;gap:10px;flex-wrap:wrap}
/* Contador clicavel: leva direto para a lista ja filtrada por aquela severidade */
.stat{border:1px solid var(--line);border-radius:10px;padding:8px 14px;text-align:center;
min-width:78px;background:var(--card2);cursor:pointer;transition:.15s}
.stat:hover{border-color:var(--acc);background:#eff6ff;transform:translateY(-1px)}
.stat .n{font-size:22px;font-weight:800;line-height:1.1}
.stat .l{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
/* Panorama por categoria: chip no lugar de uma linha de texto por categoria */
.cats{display:flex;flex-wrap:wrap;gap:8px}
.cat{display:flex;align-items:center;gap:7px;border:1px solid var(--line);
border-radius:20px;padding:6px 13px 6px 10px;background:var(--card2);cursor:pointer;
font-size:12.5px;transition:.15s}
.cat:hover{border-color:var(--acc);background:#eff6ff;transform:translateY(-1px)}
.cat .cn{font-weight:600}
.cat .cq{color:var(--mut);font-size:11px}
.cat .cd{width:9px;height:9px;border-radius:50%;flex:none}
.cat.ok-cat{opacity:.72}
.toggle-ok{background:none;border:1px dashed var(--line2);color:var(--mut);
border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px}
.toggle-ok:hover{border-color:var(--acc);color:var(--acc)}
/* Prioridade em linha compacta que abre no clique */
.prio{border:1px solid var(--line);border-left-width:3px;border-radius:0 10px 10px 0;
background:var(--card2);margin:8px 0;overflow:hidden}
.prio>.ph{display:flex;align-items:center;gap:10px;padding:10px 13px;cursor:pointer;
user-select:none}
.prio>.ph:hover{background:#eff6ff}
.prio .pt{font-weight:600;font-size:13px;flex:1;min-width:0}
.prio .pc{color:var(--mut);font-size:11px;white-space:nowrap}
.prio .pk{color:var(--mut);font-size:11px;transition:.2s;flex:none}
.prio.open .pk{transform:rotate(180deg)}
.prio .pb{display:none;padding:0 13px 12px;font-size:13px;line-height:1.6}
.prio.open .pb{display:block;animation:fadein .2s ease}
@keyframes fadein{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
.blk{font-size:12px;color:var(--mut);margin-bottom:14px;line-height:1.6}
@media (prefers-reduced-motion:reduce){
.stat:hover,.cat:hover{transform:none}.prio.open .pb{animation:none}}

/* Resumo / guia */
.summary{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;
margin-bottom:18px;line-height:1.55;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.summary h2{margin:0 0 8px;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:1px}
.summary ul.sumlist{margin:0;padding:0;list-style:none}
.summary .sumlist li{padding:7px 0;border-bottom:1px solid var(--line)}
.summary .sumlist li:last-child{border-bottom:none}
.summary .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:9px;vertical-align:middle}
.summary .sumcat{font-weight:600;margin-right:4px}
.guide{border-left:4px solid var(--brand)}
.guide .rec-sub{color:var(--mut);font-size:12px;margin:-4px 0 10px}
.guide ol.reclist{margin:0;padding-left:0;list-style:none;counter-reset:rec}
.guide .reclist li{counter-increment:rec;padding:11px 0;border-bottom:1px solid var(--line)}
.guide .reclist li:last-child{border-bottom:none}
.guide .rec-h{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.guide .rec-h b::before{content:counter(rec) ". ";color:var(--mut);font-weight:600}
.guide .rec-b{opacity:.9;margin:6px 0 4px 4px;font-size:13px}
.guide .rec-imp{color:var(--mut);font-size:12px}
.guide .rec-cmd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:6px 0 7px 4px}
.guide .rec-cmd-l{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut)}
.guide .rec-cmd code{font-size:12px;background:var(--bg2);border:1px solid var(--line);
  border-radius:6px;padding:4px 8px;white-space:nowrap;overflow-x:auto;max-width:100%}
.guide .rec-cmd.adv .rec-cmd-l{color:var(--orange)}
.guide .rec-cmd.adv code{border-color:var(--orange)}
.guide .rec-cmd-w{font-size:11px;color:var(--orange);margin:-3px 0 7px 4px;
  max-width:78ch;line-height:1.45}
.guide .rec-chk{font-size:12px;color:var(--mut);cursor:pointer;user-select:none}
.guide .rec-chk input{margin-right:5px;vertical-align:middle;accent-color:var(--acc)}
.guide .rec-chk input:checked + span{text-decoration:line-through;opacity:.6}

/* Sections */
.section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;
margin-bottom:16px;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.section>h3{margin:0;font-size:15px;display:flex;align-items:center;gap:10px}
.section>h3 .caret{margin-left:auto;color:var(--mut);font-size:11px;transition:.2s}
.section.clp>h3 .caret{transform:rotate(-90deg)}
.section.clp .secbody{display:none}
.section .secbody{margin-top:13px}
.section.hdr-click>h3{cursor:pointer;user-select:none}
.chip{font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;letter-spacing:.5px;white-space:nowrap}
.metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:11px;margin-bottom:8px}
.metric{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.metric .k{color:var(--mut);font-size:11px}.metric .v{font-size:19px;font-weight:700;margin-top:4px}
.metric .h{color:var(--mut);font-size:11px;margin-top:4px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}

/* Tabelas: cabecalho fixo e rolagem propria */
.tbl-wrap{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin:8px 0}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--bg3);padding:8px 10px;text-align:left;border-bottom:2px solid var(--line2);
font-weight:700;position:sticky;top:0;z-index:2;white-space:nowrap;text-transform:uppercase;
font-size:10px;letter-spacing:.5px;color:#334155}
td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}
tbody tr:hover td{background:#eff6ff}
tr.crit td:first-child{box-shadow:inset 3px 0 0 var(--red)}
tr.warn td:first-child{box-shadow:inset 3px 0 0 var(--orange)}
tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--green)}
.pill{display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:700;min-width:46px;text-align:center}
.pill-ok{background:#dcfce7;color:var(--green)}
.pill-warn{background:#fef3c7;color:var(--orange)}
.pill-crit{background:#fee2e2;color:var(--red)}
.pill-info{background:#e2e8f0;color:#475569}
.trunc{color:var(--mut);font-size:11px;padding:6px 10px}

/* Achados */
.finding{border-left:3px solid var(--line);padding:11px 14px;margin:10px 0;background:var(--card2);border-radius:0 10px 10px 0}
.finding .ft{font-weight:700;margin-bottom:3px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.finding .fd{color:#334155;font-size:13px;white-space:pre-wrap}
.finding .fr{margin-top:7px;font-size:13px;color:#0f766e}
.finding .fi{color:var(--mut);font-size:12px;margin-top:3px}
.finding .src{color:var(--mut);font-size:11px;font-weight:400}

/* Grupos da aba Achados: severidade > categoria.
   O cabecalho de severidade e sticky porque com 40 achados o grupo passa da
   altura da tela - sem ele, no meio da rolagem nao da mais para saber se o
   que esta na frente e critico ou informativo. */
.fgrp{margin:0 0 22px}
.fgh{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:9px;padding:8px 2px 7px;background:var(--card);border-bottom:2px solid var(--line)}
.fgd{width:10px;height:10px;border-radius:50%;flex:0 0 auto}
.fgt{font-weight:800;font-size:12px;letter-spacing:.09em;text-transform:uppercase}
.fgn{margin-left:auto;font-size:11.5px;color:var(--mut);background:var(--card2);border:1px solid var(--line);border-radius:99px;padding:1px 9px}
.fcat{margin:12px 0 0}
.fch{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:var(--mut);padding:5px 2px;text-transform:uppercase;letter-spacing:.04em}
.fcn{font-weight:600;font-size:11px;background:var(--card2);border:1px solid var(--line);border-radius:99px;padding:0 7px}
.fcat .finding{margin:7px 0 7px 12px}

/* Grafico de tendencia (SVG inline, sem dependencia externa) */
.chart-box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}
.chart-box h3{font-size:14px;margin-bottom:12px}
.cx-lbl{fill:var(--mut);font-size:10px}.cx-val{fill:var(--txt);font-size:10px;font-weight:700}
.cx-grid{stroke:var(--line2);stroke-width:1}

/* Raciocinio: a cadeia hipotese -> evidencia -> veredito na aba Resumo.
   A descartada FICA visivel, apagada e riscada: saber o que ja foi eliminado
   e o que impede a proxima pessoa de refazer o caminho. */
.rc-nota{color:var(--mut);font-size:12px;margin-bottom:12px;line-height:1.55}
.rc-vazio{color:var(--mut);font-size:13px;line-height:1.6}
.rc-h{border:1px solid var(--line2);border-left:3px solid var(--line2);
border-radius:11px;padding:11px 13px;margin-bottom:9px;font-size:13px}
.rc-h.sustentada{border-left-color:#dc2626;background:#dc26260a}
.rc-h.possivel{border-left-color:#d97706}
.rc-h.consequencia{border-left-style:dashed}
.rc-h.refutada{opacity:.62}
.rc-cab{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}
.rc-rot{font-weight:700}
.rc-h.refutada .rc-rot{text-decoration:line-through}
.rc-v{margin-left:auto;font-size:10px;font-weight:800;text-transform:uppercase;
letter-spacing:.04em;border-radius:20px;padding:2px 9px;white-space:nowrap;
border:1px solid var(--line2);color:var(--mut)}
.rc-v.sustentada{color:#fff;background:#dc2626;border-color:#dc2626}
.rc-v.possivel{color:#d97706;border-color:#d97706}
/* Classe de risco: o que PARA o banco nao pode ter a mesma cara de "lento". */
.rc-cl{font-size:9.5px;font-weight:800;text-transform:uppercase;
letter-spacing:.04em;border-radius:20px;padding:2px 8px;white-space:nowrap;
background:#dc26261a;color:#dc2626}
.rc-cl.perda{background:#7c3aed1a;color:#7c3aed}
.rc-ex{color:var(--mut);margin-top:5px;line-height:1.55}
.rc-li{margin-top:5px;display:flex;gap:7px;align-items:baseline;line-height:1.5}
.rc-li.pro>span:first-child{color:#dc2626;font-weight:800}
.rc-li.con>span:first-child{color:var(--mut);font-weight:800}
.rc-gat{margin-top:7px;padding:7px 10px;border-radius:9px;line-height:1.55;
background:#d977061a;border:1px solid #d9770633}
.rc-gat code{font-size:12px}
.rc-dec{margin-top:7px;padding-top:7px;border-top:1px dashed var(--line2);
line-height:1.55}
.rc-q{color:var(--mut);font-size:11.5px}
.rc-estr{margin-bottom:11px;padding:10px 13px;border-radius:11px;
background:#7c3aed12;border:1px solid #7c3aed40;line-height:1.55;font-size:13px}

/* Plano de acao priorizado: agrupado por horizonte.
   As classes antigas (.recs/.rec-i/.plano-h) nao tinham CSS nenhum - o plano
   caia no <ol> cru do navegador, e o separador de horizonte recebia numero de
   item como se fosse uma acao. */
.pl-res{font-size:13px;color:var(--txt);background:var(--card2);
border:1px solid var(--line);border-radius:10px;padding:10px 13px;margin-bottom:12px}
/* Trilha: o tamanho do trabalho por prazo, sem abrir nada */
.pl-trilha{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.pl-t{display:flex;align-items:center;gap:7px;background:var(--bg2);cursor:pointer;
border:1px solid var(--line2);border-left:3px solid var(--c);border-radius:9px;
padding:6px 12px;font:inherit;color:var(--txt);transition:background .15s,transform .15s}
.pl-t:hover{background:var(--card2);transform:translateY(-1px)}
.pl-t .n{font-size:16px;font-weight:800;color:var(--c);line-height:1}
.pl-t .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
/* Grupo por horizonte */
.pl-g{border:1px solid var(--line);border-left:4px solid var(--c);
border-radius:10px;margin-bottom:10px;background:var(--bg2);overflow:hidden}
.pl-g>h4{display:flex;align-items:center;gap:10px;flex-wrap:wrap;cursor:pointer;
margin:0;padding:11px 14px;font-size:12px;background:var(--card2);user-select:none}
.pl-g>h4:hover{filter:brightness(.985)}
.pl-ar{color:var(--c);font-size:11px;transition:transform .2s;flex:none}
.pl-g.ab .pl-ar{transform:rotate(90deg)}
.pl-h{font-size:12.5px;font-weight:800;color:var(--c);letter-spacing:.6px}
.pl-q{color:var(--mut);font-weight:400;font-size:11.5px}
.pl-alto{margin-left:auto;font-size:10.5px;font-weight:700;color:var(--red);
background:#fef2f2;border:1px solid #fecaca;border-radius:20px;padding:2px 9px}
/* Recolhido por padrao em prazo longo: grid-template-rows anima a altura sem
   precisar saber o tamanho do conteudo. */
.pl-l2{list-style:none;margin:0;padding:0;display:grid;grid-template-rows:0fr;
transition:grid-template-rows .25s ease}
.pl-g.ab .pl-l2{grid-template-rows:1fr}
.pl-l2>li{overflow:hidden}
.pl-i{border-top:1px solid var(--line);padding:11px 14px 12px}
.pl-l2>.pl-i:first-child{border-top:none}
.pl-ih{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.pl-n{flex:none;min-width:22px;height:22px;display:inline-flex;align-items:center;
justify-content:center;border-radius:6px;background:var(--card2);
border:1px solid var(--line2);color:var(--mut);font-size:11px;font-weight:700}
.pl-tt{font-size:13.5px;font-weight:700;flex:1;min-width:200px}
.pl-cl{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
.pl-r{font-size:10.5px;font-weight:700;color:var(--r);background:var(--bg2);
border:1px solid var(--r);border-radius:20px;padding:1px 9px;opacity:.9}
.pl-do{font-size:13px;margin:6px 0 0 31px;line-height:1.55}
/* O "porque"/"validar" fica atras de um clique: e o que deixava o plano pesado */
.pl-mais{margin:7px 0 0 31px}
.pl-mais>summary{cursor:pointer;font-size:11.5px;color:var(--acc);
list-style:none;display:inline-block;padding:2px 0}
.pl-mais>summary::-webkit-details-marker{display:none}
.pl-mais>summary::before{content:"+ ";font-weight:700}
.pl-mais[open]>summary::before{content:"\2212 "}
.pl-mais[open]>summary{margin-bottom:5px}
.pl-l{font-size:12.5px;color:#334155;margin:3px 0;line-height:1.5}
.pl-l>span{display:inline-block;min-width:62px;color:var(--mut);font-size:10.5px;
text-transform:uppercase;letter-spacing:.5px}
@media (prefers-reduced-motion:reduce){
.pl-l2,.pl-ar,.pl-t{transition:none}}
@media print{.pl-l2{grid-template-rows:1fr!important}}

/* Comandos prontos */
.code{background:#f8fafc;border:1px solid var(--line2);border-radius:10px;padding:14px;
overflow:auto;max-height:520px;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;line-height:1.55;white-space:pre}
.code .c{color:#94a3b8}.code .x{color:var(--mono);font-weight:600}
.cmd-row{display:flex;gap:8px;align-items:flex-start;margin:6px 0}
.cmd-row .cmd{flex:1;background:#f8fafc;border:1px solid var(--line2);border-radius:8px;padding:9px 11px;
color:var(--mono);font-family:ui-monospace,Consolas,monospace;font-size:11.5px;overflow:auto;white-space:pre}
.warnbox{background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;padding:12px 14px;margin-bottom:14px;font-size:13px}
.warnbox b{color:var(--orange)}

/* Imagens + modal */
.imgs{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin-top:10px}
.imgs img{width:100%;border:1px solid var(--line);border-radius:10px;cursor:zoom-in}
.modal{display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:200;
justify-content:center;align-items:flex-start;padding:26px}
.modal.on{display:flex}
.modal-in{background:var(--bg2);border:1px solid var(--line2);border-radius:12px;padding:20px;
max-width:94vw;max-height:90vh;overflow:auto;line-height:1.7;font-size:13px;
box-shadow:0 20px 50px rgba(15,23,42,.3)}
.modal-in img{max-width:100%;border-radius:8px}
.modal-in h2{font-size:17px;margin-bottom:14px}
.modal-in h4{color:var(--acc);margin:14px 0 6px;font-size:13px}
.modal-in code{background:#f1f5f9;border:1px solid var(--line);padding:1px 5px;border-radius:4px;font-size:12px}
.modal-x{float:right;position:sticky;top:0}

/* Aba Como operar */
.op-steps{margin:0;padding-left:0;list-style:none;counter-reset:op}
.op-steps>li{counter-increment:op;padding:12px 0 12px 40px;border-bottom:1px solid var(--line);position:relative}
.op-steps>li:last-child{border-bottom:none}
.op-steps>li::before{content:counter(op);position:absolute;left:0;top:12px;width:26px;height:26px;
border-radius:50%;background:var(--acc);color:#fff;font-weight:800;font-size:12px;
display:flex;align-items:center;justify-content:center}
.op-steps .op-t{font-weight:700;margin-bottom:3px}
.op-steps .op-d{color:var(--mut);font-size:13px;line-height:1.6}
.op-steps.op-slim>li{padding-top:9px;padding-bottom:9px}
.op-chat{color:var(--txt)}
.op-cmd{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:var(--mono);font-weight:600;
background:#f8fafc;border:1px solid var(--line2);border-radius:5px;padding:2px 7px;display:inline-block}
.op-persona{list-style:none;margin:0}
.op-persona li{padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.op-persona li:last-child{border-bottom:none}
.op-persona code{background:#fffbeb;border:1px solid #fcd34d;border-radius:5px;
padding:2px 7px;color:var(--orange);font-weight:600;font-size:12px}
.op-persona span{color:var(--mut)}
.mdl-cmd{background:#f8fafc;border:1px solid var(--acc);border-radius:8px;padding:11px 13px;color:var(--mono);
font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:pre-wrap;word-break:break-all;margin:8px 0}

.note{color:var(--mut);font-size:13px;margin-top:8px}
.foot{color:var(--mut);font-size:12px;text-align:center;margin-top:30px}

/* Linha do tempo do caso (mesma leitura do painel: o dashboard e o painel
   contam a MESMA historia, entao contam com o mesmo desenho) */
.tl{list-style:none;margin:6px 0 0;padding:0 0 0 18px;border-left:2px solid var(--line2)}
.tl li{position:relative;padding:0 0 13px 14px;font-size:12.5px;line-height:1.55}
.tl li::before{content:'';position:absolute;left:-23px;top:4px;width:9px;height:9px;
border-radius:50%;background:var(--mut);border:2px solid var(--bg2)}
.tl li.t-hipotese_inicial::before,.tl li.t-hipotese_atual::before{background:var(--acc)}
.tl li.t-hipotese_descartada::before{background:var(--line2)}
.tl li.t-acao::before{background:var(--green)}
.tl li.t-acao.r-alto::before{background:var(--red)}
.tl li.t-escalado::before{background:var(--orange)}
.tl li.t-resolvido::before{background:var(--green)}
.tl li.t-hipotese_descartada{opacity:.65;text-decoration:line-through}
.tl .q{color:var(--mut);font-size:11px;margin-right:6px;font-variant-numeric:tabular-nums}
.tl .tag{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
letter-spacing:.4px;margin-right:5px}
.tl .det{color:var(--mut);font-size:11px;display:block}
.tl .r{font-size:10px;font-weight:700;padding:1px 7px;border-radius:20px;
background:var(--bg3);color:var(--mut);margin-left:5px}
.tl .r.alto{background:#fee2e2;color:var(--red)}

/* Reincidencia: achado que ja apareceu antes no mesmo servidor */
.rei{list-style:none;margin:6px 0 0;padding:0}
.rei li{display:flex;gap:10px;align-items:baseline;padding:8px 0;
border-bottom:1px solid var(--line);font-size:13px}
.rei li:last-child{border-bottom:none}
.rei .nx{font-weight:800;color:var(--red);background:#fee2e2;border-radius:20px;
padding:1px 9px;font-size:11px;white-space:nowrap}
.rei .qd{color:var(--mut);font-size:11.5px;display:block}
""" + marca.CSS + """
@media print{.main-tabs,.controls,.btn,.modal{display:none!important}
.tabview{display:block!important}.section,.hero{break-inside:avoid}
.header{position:static}body{background:#fff}
.section,.hero,.summary{box-shadow:none;border-color:#ccc}
.tbl-wrap{max-height:none;overflow:visible}
/* No papel nao ha clique: o que nasce recolhido na tela tem de sair inteiro,
   senao o anexo do chamado entrega menos evidencia do que o dashboard. */
.section.clp .secbody{display:block!important}
.section>h3 .caret,.cats-ok[hidden]{display:none!important}
.cats-ok[hidden]{display:flex!important}}
"""

_JS = """
function tab(t){
  document.querySelectorAll('.main-tab').forEach(e=>e.classList.toggle('active',e.dataset.t===t));
  document.querySelectorAll('.tabview').forEach(e=>e.hidden=(e.dataset.t!==t));
  window.scrollTo(0,0);
}
function filtrar(){
  var q=(document.getElementById('q')||{}).value||'';
  q=q.toLowerCase().trim();
  var sevs=[...document.querySelectorAll('.fsev:checked')].map(c=>c.value);
  var vis=0,tot=0;
  document.querySelectorAll('#findList .finding').forEach(function(el){
    tot++;
    var okS=sevs.indexOf(el.dataset.sev)>=0;
    var okQ=!q||el.dataset.txt.indexOf(q)>=0;
    el.hidden=!(okS&&okQ);
    if(!el.hidden)vis++;
  });
  /* Cabecalho de grupo sem achado visivel e pior que nao ter grupo: promete
     conteudo que a busca ja tirou da tela. Some junto, e a contagem passa a
     mostrar o que RESTOU - so assim o numero ao lado do titulo continua
     dizendo a verdade depois de filtrar. */
  document.querySelectorAll('#findList .fcat').forEach(function(g){
    var n=g.querySelectorAll('.finding:not([hidden])').length;
    g.hidden=!n;
    var b=g.querySelector('.fcn'); if(b)b.textContent=n;
  });
  document.querySelectorAll('#findList .fgrp').forEach(function(g){
    var n=g.querySelectorAll('.finding:not([hidden])').length;
    g.hidden=!n;
    var b=g.querySelector('.fgn'); if(b)b.textContent=n;
  });
  var c=document.getElementById('fcount');
  if(c)c.textContent=vis+' de '+tot+' achado(s)';
  // Secoes de evidencia: esconde as que nao casam com a busca e ABRE as que
  // casam. Sem reabrir, procurar um termo que so existe numa secao recolhida
  // devolvia o titulo com o corpo fechado - a pagina "achou" e nao mostrou.
  secoes().forEach(function(s){
    var casa=!q||s.dataset.txt.indexOf(q)>=0;
    s.hidden=!casa;
    if(q&&casa)s.classList.remove('clp');
  });
}
function secoes(){return document.querySelectorAll('.tabview[data-t="evid"] .section')}
function clp(h){h.parentElement.classList.toggle('clp')}

/* ── Resumo interativo ────────────────────────────────────────
   O resumo mostra pouco de proposito; estas funcoes sao a ponte para o
   detalhe, para que "leve" nao vire "escondido". */

/* Contador do hero -> aba Achados ja filtrada naquela severidade. */
function verAchados(sev){
  tab('achados');
  document.querySelectorAll('.fsev').forEach(function(c){
    c.checked = sev ? (c.value===sev) : (c.value!=='info');
  });
  filtrar();
}
/* Chip de categoria -> aba Evidencias, secao aberta e destacada.
   Busca por ID, nao por posicao: a aba de evidencias e ordenada por severidade,
   entao a ordem do DOM nao e mais a de `result.sections` - por indice, cada
   chip abriria a secao errada. */
function irSecao(i){
  tab('evid');
  var s=document.getElementById('sec'+i);
  if(!s)return;
  s.classList.remove('clp');
  s.hidden=false;
  s.scrollIntoView({behavior:'smooth',block:'center'});
  /* piscada curta: sem isso, em pagina longa, o usuario nao acha para onde
     foi levado */
  var o=s.style.boxShadow;
  s.style.transition='box-shadow .3s';
  s.style.boxShadow='0 0 0 3px rgba(37,99,235,.45)';
  setTimeout(function(){s.style.boxShadow=o},1300);
}
/* Categorias sem achado: escondidas por padrao, a um clique de distancia.
   Usa o irmao do proprio botao em vez de um id global - o bloco so existe
   quando ha categoria OK, e id que as vezes nao existe vira funcao muda. */
function verOk(b){
  var e=b.nextElementSibling;
  if(!e)return;
  e.hidden=!e.hidden;
  b.textContent=(e.hidden?'+ ':'- ')+b.textContent.slice(2);
}
/* Linha de prioridade abre no clique. */
function pexp(h){h.parentElement.classList.toggle('open')}
/* Plano de acao: grupo por horizonte abre/fecha; prazo longo nasce fechado
   para o AGORA nao se perder no meio de um plano de 24 acoes. */
function plTog(i){
  var g=document.getElementById('plg'+i);
  if(!g)return;
  var ab=g.classList.toggle('ab');
  var h=g.querySelector('h4');
  if(h)h.setAttribute('aria-expanded',ab?'true':'false');
  return ab;
}
/* Trilha: leva ao grupo do prazo e garante que ele esteja aberto. */
function plIr(i){
  var g=document.getElementById('plg'+i);
  if(!g)return;
  if(!g.classList.contains('ab'))plTog(i);
  g.scrollIntoView({behavior:'smooth',block:'center'});
}
function clpAll(v){secoes().forEach(function(s){s.classList.toggle('clp',v)})}
function zoom(src){
  document.getElementById('mBody').innerHTML='<img src="'+src+'">';
  document.getElementById('modal').classList.add('on');
}
function ajuda(){
  document.getElementById('mBody').innerHTML=document.getElementById('helpTpl').innerHTML;
  document.getElementById('modal').classList.add('on');
}
function fecha(){document.getElementById('modal').classList.remove('on')}
function copiar(el,btn){
  var t=(typeof el==='string'?document.getElementById(el).innerText:el);
  navigator.clipboard.writeText(t).then(function(){
    var o=btn.textContent;btn.textContent='copiado!';
    setTimeout(function(){btn.textContent=o},1400);
  });
}
/* Copiar comando + janela com o passo a passo (estilo Lanzador).
   O handler do botao e ligado por JS (nao por onclick inline) para nao ter
   aspas aninhadas dentro da string - foi assim que o script quebrou uma vez. */
function cmdModal(cmd,titulo){
  document.getElementById('mBody').innerHTML=
   '<h2>&#128640; '+titulo+'</h2>'
  +'<p><b>O comando ja esta copiado para a area de transferencia.</b> Para executar:</p>'
  +'<ol style="padding-left:20px;margin:10px 0">'
  +'<li>Abra o <b>PowerShell na raiz do projeto</b> (ParraDibiei - checklist).</li>'
  +'<li>Cole o comando (<b>Ctrl+V</b> ou clique com o botao direito).</li>'
  +'<li>Pressione <b>Enter</b>.</li></ol>'
  +'<div style="color:var(--mut);font-size:11px">Comando (PowerShell):</div>'
  +'<div class="mdl-cmd" id="mdlCmd"></div>'
  +'<button class="btn" id="mdlCopy">copiar de novo</button>'
  +'<div class="note">A coleta e read-only. Ao terminar, recarregue esta pagina (F5) '
  +'para ver o relatorio novo.</div>';
  document.getElementById('mdlCmd').textContent=cmd;
  document.getElementById('mdlCopy').onclick=function(){copiar('mdlCmd',this)};
  document.getElementById('modal').classList.add('on');
}
document.addEventListener('click',function(e){
  var b=e.target.closest?e.target.closest('.copyb'):null;
  if(!b)return;
  var c=b.dataset.cmd;
  if(navigator.clipboard)navigator.clipboard.writeText(c);
  cmdModal(c,b.dataset.titulo||'Comando');
});
document.addEventListener('keydown',function(e){if(e.key==='Escape')fecha()});
"""


# Colunas cujo conteudo e um status (viram "pill" colorida na tabela, como no
# dashboard de referencia) e o mapa de valor -> classe.
_STATUS_COLS = ("status", "severidade", "estado", "alert", "recomenda",
                "backup_status", "stats_status", "stale_stats")
_PILL_MAP = {
    "CRITICAL": "crit", "FATAL": "crit", "FAILED": "crit", "ERROR": "crit",
    "WARNING": "warn", "OUTDATED": "warn", "UNUSABLE": "warn", "NO STATS": "warn",
    "STALE": "warn", "YES": "warn", "OK": "ok", "NORMAL": "ok", "NO": "ok",
    "INFO": "info",
}


def _cell_html(header: str, value) -> str:
    """Renderiza a celula. Colunas de status saem como pill colorida."""
    txt = "" if value is None else str(value).strip()
    if txt and str(header).strip().lower() in _STATUS_COLS:
        cls = _PILL_MAP.get(txt.upper())
        if cls:
            return f'<span class="pill pill-{cls}">{_esc(txt)}</span>'
    return _esc(txt)


def _finding_html(f: Finding, origem: str = "", mostrar_origem: bool = True) -> str:
    """`mostrar_origem=False` quando a categoria ja e o cabecalho do grupo -
    repetir em cada achado so gasta linha. Ela CONTINUA no `data-txt`: quem
    digita 'tablespace' na busca espera achar os achados daquela categoria,
    mesmo que a palavra nao apareca no titulo de nenhum deles.
    """
    color = _sc(f.severity)
    txt = " ".join(str(x) for x in (f.title, f.impact, f.detail,
                                    f.recommendation, origem) if x).lower()
    parts = [f'<div class="finding" data-sev="{f.severity}" '
             f'data-txt="{_esc(txt)}" style="border-left-color:{color}">',
             f'<div class="ft">{_chip(f.severity)} <span>{_esc(f.title)}</span>'
             + (f'<span class="src">&middot; {_esc(origem)}</span>'
                if origem and mostrar_origem else "")
             + "</div>"]
    if f.impact:
        parts.append(f'<div class="fi">Impacto: {_esc(f.impact)}</div>')
    if f.detail:
        parts.append(f'<div class="fd">{_esc(f.detail)}</div>')
    if f.recommendation:
        parts.append(f'<div class="fr">&#10148; {_esc(f.recommendation)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _achados_agrupados_html(findings, origem: dict) -> str:
    """A aba Achados em dois niveis: severidade e, dentro dela, categoria.

    Era uma lista corrida. Num caso real sao 30 a 50 achados, e a lista corrida
    tem dois defeitos que so aparecem com volume: nao da para ver ONDE o
    problema esta concentrado (seis achados da mesma categoria parecem seis
    frentes diferentes), e nao existe ponto de parada - o olho percorre tudo
    igual, do critico ao informativo.

    Severidade vem primeiro porque e a ordem de ataque, e e a mesma dimensao do
    filtro do topo: grupo inteiro some quando a caixa e desmarcada, sem deixar
    cabecalho orfao. Dentro do grupo, a categoria com MAIS achados vem antes -
    concentracao costuma ser sintoma de causa unica.
    """
    if not findings:
        return '<div class="empty">Nenhum achado classificado.</div>'

    ordem = (Severity.CRIT, Severity.WARN, Severity.OK, Severity.INFO)
    out = []
    for sev in ordem:
        doss = [f for f in findings if f.severity == sev]
        if not doss:
            continue
        cats: dict = {}
        for f in doss:
            cats.setdefault(origem.get(id(f), "Sem categoria"), []).append(f)
        cor = _sc(sev)
        out.append(
            f'<div class="fgrp" data-sev="{sev}">'
            f'<div class="fgh"><span class="fgd" style="background:{cor}"></span>'
            f'<span class="fgt">{_esc(Severity.LABEL.get(sev, sev))}</span>'
            f'<span class="fgn">{len(doss)}</span></div>')
        for cat, lst in sorted(cats.items(), key=lambda p: (-len(p[1]), p[0])):
            out.append(
                f'<div class="fcat"><div class="fch">{_esc(cat)}'
                f'<span class="fcn">{len(lst)}</span></div>'
                + "".join(_finding_html(f, cat, mostrar_origem=False)
                          for f in lst) + "</div>")
        out.append("</div>")
    return "".join(out)


def _section_html(sec: Section, collapsible: bool = True, idx: int | None = None,
                  recolhida: bool = False) -> str:
    """HTML de uma secao.

    `idx` fixa o `id` da secao no indice ORIGINAL de `result.sections`. Sem ele,
    os chips do panorama achavam a secao pela posicao no DOM - e reordenar a aba
    de evidencias por severidade faria cada chip abrir a secao errada.

    `recolhida` deixa a secao fechada de saida (usado para as que nao tem
    achado grave): ela continua na pagina, a um clique.
    """
    n_crit = sum(1 for f in sec.findings if f.severity == Severity.CRIT)
    n_warn = sum(1 for f in sec.findings if f.severity == Severity.WARN)
    tags = ""
    if n_crit:
        tags += f'<span class="pill pill-crit">{n_crit} crit</span>'
    if n_warn:
        tags += f'<span class="pill pill-warn">{n_warn} atencao</span>'
    if sec.table_rows:
        tags += f'<span class="pill pill-info">{len(sec.table_rows)} linha(s)</span>'

    # texto para a busca livre da barra de controles
    busca = " ".join([sec.title, sec.note or ""]
                     + [str(h) for h in (sec.table_headers or [])]
                     + [f.title for f in sec.findings]).lower()

    cls = "section hdr-click" if collapsible else "section"
    if recolhida and collapsible:
        cls += " clp"
    onclick = ' onclick="clp(this)"' if collapsible else ""
    ident = f' id="sec{idx}"' if idx is not None else ""
    parts = [f'<div class="{cls}"{ident} data-txt="{_esc(busca)}">',
             f'<h3{onclick}>{_esc(sec.title)} {_chip(sec.severity)} {tags}'
             + ('<span class="caret">&#9660;</span>' if collapsible else "")
             + "</h3>", '<div class="secbody">']

    if sec.metrics:
        parts.append('<div class="metrics">')
        for m in sec.metrics:
            color = _sc(m.severity)
            parts.append(
                f'<div class="metric"><div class="k">{_esc(m.name)}</div>'
                f'<div class="v"><span class="dot" style="background:{color}"></span>{_esc(m.display)}</div>'
                + (f'<div class="h">{_esc(m.hint)}</div>' if m.hint else "")
                + "</div>"
            )
        parts.append("</div>")

    if sec.table_headers and sec.table_rows:
        parts.append('<div class="tbl-wrap"><table><thead><tr>')
        for h in sec.table_headers:
            parts.append(f"<th>{_esc(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(sec.table_rows):
            rcls = sec.row_severity[i] if i < len(sec.row_severity) else ""
            parts.append(f'<tr class="{rcls}">')
            for j, cell in enumerate(row):
                hdr = sec.table_headers[j] if j < len(sec.table_headers) else ""
                parts.append(f"<td>{_cell_html(hdr, cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table></div>")

    for f in sec.findings:
        parts.append(_finding_html(f))

    if sec.images:
        parts.append('<div class="imgs">')
        for img in sec.images:
            uri = _img_data_uri(img)
            if uri:
                parts.append(f'<img src="{uri}" alt="{_esc(os.path.basename(img))}" '
                             f'onclick="zoom(this.src)"/>')
        parts.append("</div>")

    if sec.note:
        parts.append(f'<div class="note">{_esc(sec.note)}</div>')

    parts.append("</div></div>")
    return "".join(parts)


def _guide_html(result: CaseResult) -> str:
    """Guia consolidado de sugestoes de melhoria (todas as recomendacoes dos
    achados, ordenadas por severidade) para serem avaliadas."""
    recs = [f for f in _all_findings(result) if f.recommendation]
    if not recs:
        return ""
    conn = (result.meta or {}).get("conn", "")
    advisor_feito = advisor_ja_rodou(_evidence_dir(result))
    lis = []
    for i, f in enumerate(recs):
        color = _sc(f.severity)
        label = Severity.LABEL.get(f.severity, "")
        imp = f' <span class="rec-imp">({_esc(f.impact)})</span>' if f.impact else ""
        # Drill-down: quando o achado traz SQL_ID ou hora, oferece o comando
        # read-only que investiga o proximo nivel. O botao referencia o ID do
        # elemento (nunca o comando inline): o comando da JANELA tem aspas
        # duplas dentro e quebraria o onclick.
        cmd = next_command(f, result.case, conn)
        cmd_html = ""
        if cmd:
            cmd_html = (
                f'<div class="rec-cmd"><span class="rec-cmd-l">Proximo passo</span>'
                f'<code id="nc{i}">{_esc(cmd)}</code>'
                f'<button class="btn" onclick="copiar(\'nc{i}\',this)">copiar</button>'
                f'</div>')
        # O advisor e a UNICA sugestao do relatorio que escreve no banco: vem
        # depois do drill-down read-only e com o custo declarado, para nao ser
        # copiada por reflexo junto com as outras.
        adv = advisor_command(f, result.case, conn, ja_rodou=advisor_feito)
        if adv:
            cmd_html += (
                f'<div class="rec-cmd adv">'
                f'<span class="rec-cmd-l">Se quiser ir alem</span>'
                f'<code id="ac{i}">{_esc(adv)}</code>'
                f'<button class="btn" onclick="copiar(\'ac{i}\',this)">copiar</button>'
                f'</div>'
                f'<div class="rec-cmd-w">&#9888; O SQL Tuning Advisor ESCREVE no '
                f'dicionario (cria/executa tuning task) e consome Tuning Pack. '
                f'As recomendacoes entram no acoes.sql, comentadas.</div>')
        lis.append(
            f'<li><div class="rec-h">'
            f'<span class="chip" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55">{label}</span>'
            f'<b>{_esc(f.title)}</b>{imp}</div>'
            f'<div class="rec-b">&#128161; {_esc(f.recommendation)}</div>'
            f'{cmd_html}'
            f'<label class="rec-chk"><input type="checkbox"><span>marcar como avaliado</span></label>'
            f'</li>')
    n_crit = sum(1 for f in recs if f.severity == Severity.CRIT)
    n_warn = sum(1 for f in recs if f.severity == Severity.WARN)
    sub = (f"{len(recs)} sugestao(oes)"
           + (f" &middot; {n_crit} critica(s)" if n_crit else "")
           + (f" &middot; {n_warn} em atencao" if n_warn else "")
           + " &middot; priorizadas por severidade")
    return (
        '<div class="section guide">'
        '<h3>Guia de Sugestoes de Melhoria '
        '<span style="font-weight:400;color:var(--mut);font-size:13px">(a avaliar)</span></h3>'
        f'<div class="rec-sub">{sub}</div>'
        f'<ol class="reclist">{"".join(lis)}</ol></div>'
    )


def _evidence_dir(result: CaseResult) -> str:
    """Pasta de evidencias do caso (troubleshooting/<caso>/)."""
    return (result.meta or {}).get("case_dir") or \
        os.path.join(BASE_DIR, "troubleshooting", result.case)


def _hero(result: CaseResult) -> str:
    """Faixa unica no lugar dos oito cartoes: como esta o banco, em uma linha.

    Os oito cartoes anteriores repetiam o que o cabecalho e as outras abas ja
    diziam (severidade, metricas de destaque) e empurravam o conteudo util para
    baixo da dobra. Aqui ficam so score, veredito e tres contadores - e os
    contadores levam para a lista ja filtrada.
    """
    findings = _all_findings(result)
    n_crit = sum(1 for f in findings if f.severity == Severity.CRIT)
    n_warn = sum(1 for f in findings if f.severity == Severity.WARN)
    score, sev = result.score, result.severity

    d = result.diff or {}
    delta = (d.get("score") or 0) - (d.get("prev_score") or 0) if d else 0
    if d:
        seta = "&#9650;" if delta > 0 else ("&#9660;" if delta < 0 else "&#8226;")
        cor = (_sc(Severity.OK) if delta > 0 else
               _sc(Severity.CRIT) if delta < 0 else "#64748b")
        sub = (f'<span style="color:{cor}">{seta} {delta:+d}</span> '
               f'desde a execucao anterior ({d.get("prev_score")}/100)')
    else:
        sub = "primeira execucao deste caso"

    # Veredito em uma frase: e a leitura que o gestor faz sem abrir mais nada.
    if n_crit:
        vered = f"{n_crit} achado(s) critico(s) exigem acao imediata"
    elif n_warn:
        vered = f"{n_warn} ponto(s) de atencao para a proxima janela"
    else:
        vered = "nenhum achado critico ou de atencao"

    stats = [("crit", n_crit, "criticos", _sc(Severity.CRIT if n_crit else Severity.OK)),
             ("warn", n_warn, "atencao", _sc(Severity.WARN if n_warn else Severity.OK)),
             ("", len(result.sections), "categorias", "")]
    chips = "".join(
        f'<div class="stat" onclick="verAchados(\'{k}\')" '
        f'title="ver na aba Achados">'
        f'<div class="n"{f" style=color:{c}" if c else ""}>{n}</div>'
        f'<div class="l">{lbl}</div></div>'
        for k, n, lbl, c in stats)

    # O perfil ASSUMIDO tem de aparecer, e nao so agir. Fora de producao o
    # framework rebaixa desvio de recuperabilidade e de senha de critico para
    # atencao (ver troubleshoot/perfil.py) - decidir isso em silencio seria
    # deixar quem le sem como discordar. Producao e o padrao e nao polui a
    # faixa: so aparece quando NAO e producao, que e o caso excepcional.
    nota = (result.meta or {}).get("perfil_nota") or ""
    faixa_perfil = ""
    if nota and (result.meta or {}).get("perfil") != "producao":
        faixa_perfil = (f'<div class="hd" style="margin-top:4px">'
                        f'&#9873; {_esc(nota)}</div>')

    return (f'<div class="hero"><div class="sc">'
            f'<span class="v" style="color:{_score_color(score)}">{score}</span>'
            f'<span class="m">/100</span></div>'
            f'<div class="hs"><div class="hl" style="color:{_sc(sev)}">'
            f'{Severity.LABEL.get(sev, "")} &middot; {_esc(vered)}</div>'
            f'<div class="hd">{sub}</div>{faixa_perfil}</div>'
            f'<div class="stats">{chips}</div></div>')


def _raciocinio_html(result: CaseResult) -> str:
    """O porque da causa raiz, na aba Resumo - antes da lista de achados.

    O dashboard mostrava O QUE foi encontrado e deixava a ligacao entre os
    achados por conta de quem lia. Este bloco e o encadeamento: a causa, a
    evidencia que a sustenta, o que e apenas SINTOMA dela (para ninguem abrir
    chamado para o storage por um I/O que o plano produziu), o que foi
    descartado e o risco estrutural que nao explica o incidente e mesmo assim
    nao pode passar batido.
    """
    try:
        lista = raciocinio.avaliar(raciocinio.de_resultado(result))
        gats = {g["hipotese"]: g for g in raciocinio.gatilhos(result)}
    except Exception:
        # O raciocinio e leitura sobre os achados: se falhar, o dashboard
        # continua entregando a analise. Silencio aqui e melhor que pagina meia.
        return ""

    vivas = [h for h in lista if h["veredito"] in ("sustentada", "possivel")]
    if not vivas:
        return ('<div class="section"><h3>Raciocinio</h3><div class="secbody">'
                '<div class="rc-vazio">A evidencia coletada nao aponta gargalo '
                'dentro do banco. Isso tambem e resposta: investigue rede, '
                'cliente e aplicacao (SQL*Net, tempo de resposta fora do banco) '
                'antes de procurar causa no servidor.</div></div></div>')

    _SELO = {raciocinio.CLASSE_PARADA: "pode PARAR o banco",
             raciocinio.CLASSE_PERDA: "risco de PERDA de dado"}
    itens = []
    for h in vivas[:4]:
        selo = _SELO.get(h["classe"], "") if h["veredito"] == "sustentada" else ""
        cab = (f'<div class="rc-cab"><span class="rc-rot">{_esc(h["rotulo"])}</span>'
               + (f'<span class="rc-cl {_esc(h["classe"])}">{_esc(selo)}</span>'
                  if selo else "")
               + f'<span class="rc-v {_esc(h["veredito"])}">'
                 f'{_esc(h["veredito"])}</span></div>')
        corpo = [f'<div class="rc-ex">{_esc(h["explica"])}</div>']
        for p in h["a_favor"][:3]:
            corpo.append(f'<div class="rc-li pro"><span>+</span>'
                         f'<span>{_esc(p["achado"])}</span></div>')
        g = gats.get(h["id"])
        if g:
            corpo.append(
                f'<div class="rc-gat"><b>Gatilho provavel:</b> {_esc(g["tipo"])}'
                f' de {_esc(g["quando"])} ({g["dias"]} dia(s) antes) em '
                f'<code>{_esc(g["objeto"])}</code> &mdash; {_esc(g["porque"])}'
                f' <span class="rc-q">(confianca {_esc(g["confianca"])}; '
                f'correlacao no tempo, nao causa provada)</span></div>')
        corpo.append(f'<div class="rc-dec"><b>O que decide:</b> '
                     f'{_esc(h["decide"])} <span class="rc-q">'
                     f'({_esc(h["coletar"])})</span></div>')
        itens.append(f'<div class="rc-h {_esc(h["veredito"])}">{cab}'
                     + "".join(corpo) + "</div>")

    for h in [x for x in lista if x["veredito"] == "consequencia"][:3]:
        itens.append(
            '<div class="rc-h consequencia"><div class="rc-cab">'
            f'<span class="rc-rot">{_esc(h["rotulo"])}</span>'
            '<span class="rc-v consequencia">sintoma, nao causa</span></div>'
            f'<div class="rc-ex">Explicado por <b>{_esc(h["causa"])}</b>: '
            'tratar a causa, nao este sintoma.</div></div>')

    for h in [x for x in lista if x["veredito"] == "refutada"][:3]:
        itens.append(
            '<div class="rc-h refutada"><div class="rc-cab">'
            f'<span class="rc-rot">{_esc(h["rotulo"])}</span>'
            '<span class="rc-v refutada">descartada</span></div>'
            f'<div class="rc-li con"><span>&minus;</span><span>'
            f'{_esc(h["contra"][0]["achado"])} '
            f'<span class="rc-q">({_esc(h["contra"][0]["marca"])})</span>'
            '</span></div></div>')

    estrutural = [h for h in lista if h["veredito"] == "sustentada"
                  and h["classe"] in (raciocinio.CLASSE_PARADA,
                                      raciocinio.CLASSE_PERDA)
                  and h["id"] != vivas[0]["id"]]
    aviso = ""
    if estrutural:
        aviso = ('<div class="rc-estr"><b>ALEM DO INCIDENTE &mdash; risco '
                 'estrutural neste servidor.</b> Nao explica o sintoma que '
                 'abriu o caso e e mais grave que ele: '
                 + _esc("; ".join(h["rotulo"] for h in estrutural[:3]))
                 + ".</div>")

    return ('<div class="section"><h3>Raciocinio &mdash; por que esta e a causa'
            f'<span class="pill">{len(vivas)} hipotese(s) viva(s)</span></h3>'
            '<div class="secbody">'
            '<div class="rc-nota">Hipoteses concorrentes pontuadas pela '
            'evidencia deste caso. A descartada fica na lista de proposito: '
            'saber o que ja foi eliminado, e por que, e o que impede a proxima '
            'pessoa de refazer o caminho.</div>'
            + aviso + "".join(itens) + "</div></div>")


def _incompleto_html(result: CaseResult) -> str:
    """Faixa de 'analise incompleta' quando algum analisador falhou.

    `analisar_caso` engole a excecao de um analisador para nao perder as outras
    categorias - decisao certa. So que o erro so aparecia no log do terminal e
    no corpus: o dashboard mostrava Health Score alto e nenhum achado daquela
    dimensao, e um score verde por FALTA de analise e pior do que um score
    ruim. Aqui ele fica na primeira faixa da aba Resumo.
    """
    faltou = getattr(result, "coleta_incompleta", []) or []
    if not result.erros and not faltou:
        return ""
    cor = _sc(Severity.WARN)
    blocos = []
    # Dois motivos diferentes para o mesmo efeito - a categoria nao aparece no
    # relatorio - e com acoes opostas: analisador que explode se conserta no
    # codigo; check que nao rodou se resolve recoletando. Juntar os dois num
    # titulo so mandaria metade das pessoas para o lugar errado.
    if result.erros:
        itens = "".join(f"<li>{_esc(e)}</li>" for e in result.erros)
        blocos.append(f'<p><b>{len(result.erros)} analisador(es) falharam</b> '
                      f'(defeito do framework):</p><ul>{itens}</ul>')
    if faltou:
        itens = "".join(f"<li>{_esc(e)}</li>" for e in faltou)
        blocos.append(
            f'<p><b>{len(faltou)} check(s) nao foram coletados</b> - a query '
            'nao rodou no banco. A secao correspondente NAO aparece abaixo, e '
            'a ausencia dela nao significa ausencia de problema:</p>'
            f'<ul>{itens}</ul>')
    n = len(result.erros) + len(faltou)
    return (f'<div class="section" style="border-left:4px solid {cor}">'
            f'<h3 style="color:{cor}">Analise incompleta &mdash; '
            f'{n} lacuna(s)</h3>'
            '<div class="secbody"><p>O Health Score e os achados abaixo NAO '
            'cobrem estas categorias. Trate o resultado como parcial:</p>'
            + "".join(blocos) + '</div></div>')


def _categorias_html(result: CaseResult) -> str:
    """Panorama por categoria em chips.

    Antes era uma lista com uma linha de texto por categoria - 15 a 21 linhas
    nos casos reais, com as categorias OK ocupando o mesmo espaco das que
    quebraram. Chip mostra o mesmo em uma faixa, e as OK ficam atras de um
    botao: quem esta lendo um incidente nao precisa ver 12 linhas de 'tudo bem'.
    """
    if not result.sections:
        return ""
    ordem = {Severity.CRIT: 0, Severity.WARN: 1, Severity.INFO: 2, Severity.OK: 3}
    secs = sorted(enumerate(result.sections),
                  key=lambda p: ordem.get(p[1].severity, 3))
    problema, ok = [], []
    for i, s in secs:
        nc = sum(1 for f in s.findings if f.severity == Severity.CRIT)
        nw = sum(1 for f in s.findings if f.severity == Severity.WARN)
        q = ", ".join(x for x in (f"{nc} crit" if nc else "",
                                  f"{nw} atencao" if nw else "") if x)
        chip = (f'<div class="cat{"" if q else " ok-cat"}" '
                f'onclick="irSecao({i})" title="abrir esta categoria">'
                f'<span class="cd" style="background:{_sc(s.severity)}"></span>'
                f'<span class="cn">{_esc(s.title)}</span>'
                + (f'<span class="cq">{q}</span>' if q else "") + "</div>")
        (problema if q else ok).append(chip)

    h = ['<div class="section"><h3>Panorama por categoria</h3>'
         '<div class="secbody"><div class="cats">']
    h.append("".join(problema) or
             '<span class="hd" style="color:var(--mut);font-size:12.5px">'
             'nenhuma categoria com achado grave.</span>')
    if ok:
        h.append(f'<button class="toggle-ok" onclick="verOk(this)">'
                 f'+ {len(ok)} categoria(s) sem achado</button>'
                 f'<span class="cats-ok" hidden>{"".join(ok)}</span>')
    h.append("</div></div></div>")
    return "".join(h)


def _prioridades_html(result: CaseResult, origem: dict) -> str:
    """O pior achado de CADA categoria, em linha compacta que abre no clique.

    Continua sendo um por categoria (listar os 5 piores da fila mostraria 5
    tablespaces cheios e esconderia as outras frentes). O que muda e a forma:
    antes vinham seis achados inteiros abertos, com impacto, detalhe e
    recomendacao - so isso ja passava de 3.000 caracteres.
    """
    findings = _all_findings(result)
    graves = [f for f in findings if f.severity in (Severity.CRIT, Severity.WARN)]
    top, vistos = [], set()
    for f in graves:
        cat = origem.get(id(f), "")
        if cat in vistos:
            continue
        vistos.add(cat)
        top.append(f)
        if len(top) == 6:
            break
    if not top:
        return ""

    linhas = []
    for f in top:
        cor = _sc(f.severity)
        corpo = []
        if f.impact:
            corpo.append(f'<div class="fi">Impacto: {_esc(f.impact)}</div>')
        if f.detail:
            corpo.append(f'<div class="fd">{_esc(f.detail)}</div>')
        if f.recommendation:
            corpo.append(f'<div class="fr">&#10148; {_esc(f.recommendation)}</div>')
        if not corpo:
            corpo.append('<div class="fi">Sem detalhe adicional.</div>')
        linhas.append(
            f'<div class="prio" style="border-left-color:{cor}">'
            f'<div class="ph" onclick="pexp(this)">{_chip(f.severity)}'
            f'<span class="pt">{_esc(f.title)}</span>'
            f'<span class="pc">{_esc(origem.get(id(f), ""))}</span>'
            f'<span class="pk">&#9660;</span></div>'
            f'<div class="pb">{"".join(corpo)}</div></div>')

    resto = len(graves) - len(top)
    nota = (f"+{resto} achado(s) grave(s) na aba Achados."
            if resto > 0 else "Lista completa na aba Achados.")
    return ('<div class="section"><h3>O que tratar primeiro '
            '<span style="font-weight:400;color:var(--mut);font-size:12px">'
            '(o pior de cada categoria &middot; clique para abrir)</span></h3>'
            '<div class="secbody">' + "".join(linhas)
            + f'<div class="note">{nota}</div></div></div>')


def _sparkline(trend: list) -> str:
    """Grafico de tendencia do score em SVG inline (sem dependencia externa)."""
    pts = [v for v in trend if isinstance(v, (int, float))]
    if len(pts) < 2:
        return ""
    w, h, pad = 720, 170, 30
    n = len(pts)
    dx = (w - 2 * pad) / (n - 1)
    def y(v):
        return pad + (100 - v) / 100.0 * (h - 2 * pad)
    poly = " ".join(f"{pad + i * dx:.1f},{y(v):.1f}" for i, v in enumerate(pts))
    grid = "".join(f'<line class="cx-grid" x1="{pad}" y1="{y(g):.1f}" x2="{w-pad}" '
                   f'y2="{y(g):.1f}"/><text class="cx-lbl" x="4" y="{y(g)+3:.1f}">{g}</text>'
                   for g in (0, 50, 100))
    dots = "".join(
        f'<circle cx="{pad + i * dx:.1f}" cy="{y(v):.1f}" r="4" fill="{_score_color(v)}"/>'
        f'<text class="cx-val" x="{pad + i * dx:.1f}" y="{y(v)-10:.1f}" '
        f'text-anchor="middle">{v}</text>'
        for i, v in enumerate(pts))
    return (
        '<div class="chart-box"><h3>Tendencia do Health Score '
        f'<span style="color:var(--mut);font-weight:400;font-size:12px">'
        f'(ultimas {n} execucoes)</span></h3>'
        f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">{grid}'
        f'<polyline points="{poly}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        f'{dots}</svg></div>')


_HORIZ_COR = {"agora": "#b91c1c", "hoje": "#c2410c",
              "esta semana": "#b45309", "planejar": "#0369a1"}
_RISCO_COR = {"alto": "#b91c1c", "medio": "#b45309", "nenhum": "#15803d"}


def _plano_html(result: CaseResult) -> str:
    """Plano priorizado no topo da aba: a ordem em que o DBA deve agir.

    Existe porque listar achado por severidade nao responde "o que primeiro?":
    corrupcao de bloco e tablespace em 91% sao ambos criticos e nao se tratam
    na mesma ordem.
    """
    acoes = montar_plano(result)
    if not acoes:
        return ""
    grupos = agrupar_por_horizonte(acoes)

    # Trilha de horizontes: quantas acoes em cada prazo, antes de abrir nada.
    trilha = "".join(
        f'<button class="pl-t" data-h="{i}" onclick="plIr({i})" '
        f'style="--c:{_HORIZ_COR.get(h, "#64748b")}">'
        f'<span class="n">{len(g)}</span>'
        f'<span class="l">{_esc(h)}</span></button>'
        for i, (h, g) in enumerate(grupos))

    blocos = []
    for i, (horizonte, grupo) in enumerate(grupos):
        cor = _HORIZ_COR.get(horizonte, "#64748b")
        # Prazo curto abre; o resto fica recolhido. Um plano de 24 acoes aberto
        # de uma vez e a razao de "o resumo esta cheio de coisa": o que exige
        # acao imediata some no meio do que pode esperar.
        # O PRIMEIRO grupo abre sempre, mesmo sendo de prazo longo: ha casos
        # reais (SQLHC_DBSRV600, TESTE_DBSRV600) com um unico grupo "esta
        # semana" - so a regra do prazo deixaria o plano inteiro recolhido, e o
        # DBA abriria a aba num bloco vazio.
        aberto = i == 0 or horizonte in ("agora", "hoje")
        n_alto = sum(1 for a in grupo if a.risco == "alto")
        selo = (f'<span class="pl-alto">{n_alto} de risco alto</span>'
                if n_alto else "")
        itens = []
        for a in grupo:
            rcor = _RISCO_COR.get(a.risco, "#64748b")
            itens.append(
                f'<li class="pl-i" style="--r:{rcor}">'
                f'<div class="pl-ih"><span class="pl-n">{a.ordem}</span>'
                f'<b class="pl-tt">{_esc(a.titulo)}</b>'
                f'<span class="pl-cl">{_esc(a.classe_rotulo)}</span>'
                f'<span class="pl-r">risco {_esc(a.risco)}</span></div>'
                f'<div class="pl-do">{_esc(a.o_que_fazer)}</div>'
                '<details class="pl-mais"><summary>por que e como validar</summary>'
                + (f'<div class="pl-l"><span>Porque</span>{_esc(a.por_que)}</div>'
                   if a.por_que else "")
                + f'<div class="pl-l"><span>Validar</span>{_esc(a.validar)}</div>'
                + f'<div class="pl-l"><span>Origem</span>{_esc(a.origem)}</div>'
                '</details></li>')
        blocos.append(
            f'<section class="pl-g{" ab" if aberto else ""}" id="plg{i}" '
            f'style="--c:{cor}">'
            f'<h4 onclick="plTog({i})" aria-expanded="{"true" if aberto else "false"}">'
            f'<span class="pl-ar">&#9656;</span>'
            f'<span class="pl-h">{_esc(horizonte.upper())}</span>'
            f'<span class="pl-q">{len(grupo)} acao(oes)</span>{selo}</h4>'
            f'<ol class="pl-l2">{"".join(itens)}</ol></section>')

    return ('<div class="section"><h3>Plano de acao priorizado '
            f'<span class="pill">{len(acoes)} acao(oes)</span></h3>'
            '<div class="secbody">'
            f'<div class="pl-res">{_esc(resumo_plano(acoes))}</div>'
            f'<div class="pl-trilha">{trilha}</div>'
            f'{"".join(blocos)}'
            '<div class="ic-meta" style="margin-top:10px">Ordem: integridade do '
            'dado, recuperabilidade, disponibilidade, infraestrutura e por fim '
            'performance. O numero e a posicao na fila geral de prioridade.</div>'
            '</div></div>')


def _pendencias_html(result: CaseResult) -> str:
    """Checklist pos-acao em aberto (do troubleshooting guiado).

    Vem PRIMEIRO na aba de plano, antes do que ainda ha para fazer no banco:
    acao ja executada e verificacao pendente e um estado pior do que achado
    conhecido e nao tratado - o ambiente ja mudou e ninguem confirmou o efeito.
    """
    try:
        abertas = guia.pendencias_abertas(result.case)
    except Exception:      # pragma: no cover - defensivo
        return ""
    if not abertas:
        return ""
    lis = "".join(
        f'<li><b>{_esc(p.get("texto", ""))}</b>'
        f'<div class="note" style="margin-top:2px">{_esc(p.get("porque", ""))}</div>'
        "</li>" for p in abertas)
    return ('<div class="section" style="border-left:4px solid '
            f'{_sc(Severity.WARN)}"><h3 style="color:{_sc(Severity.WARN)}">'
            'Checklist pos-acao &mdash; '
            f'{len(abertas)} verificacao(oes) em aberto</h3>'
            '<div class="secbody"><p>Uma acao que MUDOU o ambiente foi '
            'registrada neste caso e a validacao ainda nao foi feita. '
            'Sem isto, "resolvido" e opiniao.</p>'
            f'<ul style="margin:8px 0 0 18px;line-height:1.7">{lis}</ul>'
            '<div class="note">Concluir pela tela (aba <b>Guiado</b>) ou por '
            f'<code>GUIA.py {_esc(result.case)} --feito &lt;id&gt;</code>.'
            '</div></div></div>')


def _acoes_html(result: CaseResult) -> str:
    """Aba Plano de Acao: pendencias pos-acao + plano priorizado + guia de
    sugestoes + comandos prontos do acoes.sql gerado pelo SQL Health Check
    (quando existir). Nada aqui foi executado pela coleta."""
    parts = []
    pend = _pendencias_html(result)
    if pend:
        parts.append(pend)
    plano = _plano_html(result)
    if plano:
        parts.append(plano)
    guide = _guide_html(result)
    if guide:
        parts.append(guide)

    caminho = os.path.join(_evidence_dir(result), "acoes.sql")
    if os.path.exists(caminho):
        try:
            txt = open(caminho, encoding="utf-8", errors="replace").read()
        except Exception:
            txt = ""
        if txt.strip():
            ativos = [l for l in txt.splitlines()
                      if l.strip() and not l.strip().startswith("--")]
            linhas = "".join(
                (f'<span class="c">{_esc(l)}</span>\n' if l.strip().startswith("--")
                 else f'<span class="x">{_esc(l)}</span>\n')
                for l in txt.splitlines())
            parts.append(
                '<div class="section"><h3>Comandos prontos (acoes.sql) '
                f'<span class="pill pill-ok">{len(ativos)} executavel(is)</span></h3>'
                '<div class="secbody">'
                '<div class="warnbox"><b>Nada aqui foi executado pela coleta.</b> '
                'Linhas comentadas (<code>--</code>) sao contexto ou acao que exige '
                'validacao previa: criar indice e comentado de proposito. '
                'Revise e execute na janela combinada, com plano de rollback.</div>'
                '<div style="display:flex;gap:8px;margin-bottom:8px">'
                '<button class="btn" onclick="copiar(\'acoesSql\',this)">'
                'copiar tudo</button>'
                '<button class="btn" onclick="copiar(ATIVOS,this)">'
                'copiar so os executaveis</button></div>'
                f'<div class="code" id="acoesSql">{linhas}</div></div></div>')
            parts.append("<script>var ATIVOS="
                         + json.dumps("\n".join(ativos)) + ";</script>")
    if not parts:
        return ('<div class="empty">Sem plano de acao: nenhum achado gerou '
                'recomendacao e o SQL Health Check nao produziu acoes.sql.</div>')
    return "".join(parts)


_HELP = """
<h2>Como ler este dashboard</h2>
<h4>Abas</h4>
<p><b>Resumo</b>: Health Score, KPIs e o resumo executivo por categoria.<br>
<b>Achados</b>: todos os achados em uma lista, com filtro por severidade e busca
livre &mdash; use para triagem rapida.<br>
<b>Evidencias</b>: as tabelas coletadas (read-only), por categoria.<br>
<b>Plano de acao</b>: recomendacoes priorizadas e os comandos prontos.</p>
<h4>Historico</h4>
<p>Fica em pagina separada, no botao <b>Historico</b> do topo: tendencia do
score, o que mudou desde a ultima execucao, achados cronicos e a linha do
tempo do caso. Sai do caminho de quem abre o relatorio para responder "o que
esta quebrado agora", sem deixar de acompanhar o anexo.</p>
<h4>Health Score (0-100)</h4>
<p>Penaliza por <b>categoria</b>, com teto por categoria e retorno decrescente:
9 tablespaces acima do limite sao <i>um</i> problema de espaco, nao nove. Serve
para <b>comparar execucoes</b> do mesmo caso ao longo do tempo.</p>
<h4>Severidades</h4>
<p><span class="pill pill-crit">CRITICO</span> acao imediata &nbsp;
<span class="pill pill-warn">ATENCAO</span> avaliar na proxima janela &nbsp;
<span class="pill pill-ok">OK</span> dentro do esperado &nbsp;
<span class="pill pill-info">INFO</span> contexto</p>
<h4>Coleta</h4>
<p>Tudo aqui vem de coleta <b>read-only</b> (<code>SET TRANSACTION READ ONLY</code>),
respeitando a licenca dos packs. Nenhum comando de escrita foi executado.</p>
<h4>Regerar</h4>
<pre><code>.\\healthcheck.bat &lt;servidor&gt;   # coleta + relatorio
.\\redija.bat &lt;caso&gt;           # so o relatorio, sem recoletar</code></pre>
"""


_TL_TAG = {"criado": "inicio", "hipotese_inicial": "hipotese",
           "hipotese_atual": "hipotese", "hipotese_descartada": "descartada",
           "acao": "acao", "escalado": "escalado", "resolvido": "resolvido",
           # Verificacao feita durante o passo a passo. Rotulo proprio para o
           # chamado nao confundir o que foi OBSERVADO com o que foi FEITO.
           "observado": "verificado"}


def _timeline_html(result: CaseResult) -> str:
    """Linha do tempo do caso no dashboard - a mesma que o painel ja mostra.

    Vem de `caso.linha_do_tempo`, e nao de uma remontagem local: painel e
    relatorio precisam contar a MESMA historia, na mesma ordem. Sem isto, o HTML
    (que e o arquivo anexado no chamado e enviado por e-mail) mostrava so o que
    a ferramenta observou e escondia o que o DBA fez - justamente o que uma
    auditoria procura primeiro.
    """
    ev = caso_lifecycle.linha_do_tempo(result.case)
    if not ev:
        return ('<div class="section"><h3>Linha do tempo do caso</h3>'
                '<div class="secbody"><div class="empty">Nenhuma hipotese ou '
                'acao registrada. Registre com <code>CASO.py '
                f'{_esc(result.case)} --hipotese "..." --acao "..."</code> ou '
                'pelo painel.</div></div></div>')
    lis = []
    for e in ev:
        risco = e.get("risco", "")
        cls = f"t-{_esc(e.get('tipo', ''))}" + (" r-alto" if risco == "alto" else "")
        chip = (f'<span class="r{" alto" if risco == "alto" else ""}">risco '
                f'{_esc(risco)}</span>') if risco and risco != "nenhum" else ""
        det = ""
        if e.get("motivo"):
            det = f'<span class="det">motivo: {_esc(e["motivo"])}</span>'
        elif e.get("solucao"):
            det = f'<span class="det">solucao: {_esc(e["solucao"])}</span>'
        lis.append(f'<li class="{cls}"><span class="q">'
                   f'{_esc(e.get("em") or "sem hora")}</span>'
                   f'<span class="tag">'
                   f'{_esc(_TL_TAG.get(e.get("tipo", ""), e.get("tipo", "")))}'
                   f'</span>{_esc(e.get("texto", ""))}{chip}{det}</li>')
    return ('<div class="section"><h3>Linha do tempo do caso'
            f'<span style="font-weight:400;color:var(--mut);font-size:12px">'
            f'&nbsp;({len(ev)} evento(s))</span></h3>'
            f'<div class="secbody"><ul class="tl">{"".join(lis)}</ul></div></div>')


def _reincidencia_html(result: CaseResult) -> str:
    """Achados que ja apareceram antes no mesmo servidor.

    Um achado novo e um incidente; o mesmo achado pela quarta vez em 30 dias e
    um cronico, e a acao correta deixa de ser "tratar de novo" para ser "tratar
    a causa". Nao vira Finding e nao mexe no Health Score: e leitura de
    historico, nao evidencia desta coleta.
    """
    try:
        reis = reincidencia.do_resultado(result)
    except Exception:  # historico corrompido nao pode derrubar o relatorio
        return ""
    if not reis:
        return ""
    srv = reis[0].get("servidor") or result.case
    lis = "".join(
        f'<li><span class="nx">{r["ocorrencias"]}x</span><span>'
        f'<b>{_esc(r["titulo"])}</b>'
        f'<span class="qd">de {_esc(r["primeira"])} a {_esc(r["ultima"])} '
        f'&middot; casos: {_esc(", ".join(r["casos"]))}</span></span></li>'
        for r in reis)
    return ('<div class="section"><h3>Reincidencia em '
            f'{_esc(srv)}<span style="font-weight:400;color:var(--mut);'
            f'font-size:12px">&nbsp;({len(reis)} achado(s) repetido(s) em '
            f'{reincidencia.DIAS_PADRAO} dias)</span></h3>'
            f'<div class="secbody"><ul class="rei">{lis}</ul>'
            '<div class="note">Achado que se repete pede tratamento da causa, '
            'nao do sintoma. A contagem e por caso e por dia: regerar o '
            'relatorio no mesmo dia nao conta de novo.</div></div></div>')


def _historico_blocos(result: CaseResult) -> list[str]:
    """Blocos da pagina de historico, na ordem em que respondem as perguntas:
    como evoluiu o score, o que mudou desde a ultima analise, o que ja e cronico
    neste servidor e o que foi feito pelo DBA."""
    return [_sparkline((result.diff or {}).get("trend") or []),
            _diff_html(result) or
            '<div class="empty">Primeira execucao deste caso: sem historico para '
            'comparar. Rode novamente depois das acoes para ver a evolucao.</div>',
            _reincidencia_html(result),
            _timeline_html(result)]


def historico_path(out_path: str) -> str:
    """Caminho da pagina de historico ao lado do dashboard.

    Derivado do out_path (e nao do nome do caso) para o par continuar junto
    quando o dashboard e gerado com nome customizado - senao o link do botao
    aponta para um arquivo que nao existe.
    """
    return os.path.splitext(out_path)[0] + "_historico.html"


def build_historico_html(result: CaseResult, out_path: str,
                         voltar_para: str = "") -> str:
    """Pagina separada com o historico do caso.

    Sai do dashboard porque as duas leituras tem publicos e momentos
    diferentes: quem abre o anexo no meio de um incidente quer o que esta
    quebrado AGORA. A evolucao entre coletas e acompanhamento, nao triagem.
    """
    title = f"Historico - {result.case}"
    voltar = voltar_para or os.path.basename(
        out_path.replace("_historico.html", ".html"))
    head = [
        '<div class="header">',
        marca.assinatura(),
        f'<div><h1>Historico do caso</h1><div class="sub">'
        f'{_esc(result.case)} &middot; {_esc(result.subtitle)}</div></div>',
        '<div class="hdr-right">',
        f'<div class="gen">{_esc(result.generated_at)}</div>',
        f'<a class="btn" href="{_esc(voltar)}">&larr; Voltar ao dashboard</a>',
        '<button class="btn" onclick="window.print()">Imprimir</button>',
        "</div></div>",
        '<div class="content">' + "".join(_historico_blocos(result)) + "</div>",
    ]
    head.append('<div class="content">' + marca.rodape(
        "<span>Framework de Troubleshooting Oracle &middot; coleta read-only</span>")
        + "</div>")
    doc = ("<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
           + "".join(head)
           + f"<script>{_JS}</script></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def build_html(result: CaseResult, out_path: str | None = None) -> str:
    out_path = out_path or os.path.join(_case_dir(result.case), f"{result.case}.html")

    # O historico sai do dashboard e vira pagina propria, linkada no header.
    hist_path = historico_path(out_path)
    build_historico_html(result, hist_path,
                         voltar_para=os.path.basename(out_path))

    sev = result.severity
    bc = _sc(sev)
    title = result.title or f"Troubleshooting - {result.case}"
    score = result.score
    findings = _all_findings(result)
    n_crit = sum(1 for f in findings if f.severity == Severity.CRIT)

    # ── header ──
    head = [
        '<div class="header">',
        marca.assinatura(),
        f'<div><h1>{_esc(title)}</h1><div class="sub">{_esc(result.subtitle)}</div></div>',
        '<div class="hdr-right">',
        f'<span class="badge" style="background:{bc}22;color:{bc};'
        f'border:1px solid {bc}55">{Severity.LABEL.get(sev,"")}</span>',
        f'<div class="hdr-score"><div class="v" style="color:{_score_color(score)}">{score}'
        f'<span style="font-size:12px;color:var(--mut)">/100</span></div>'
        '<div class="k">Health Score</div></div>',
        f'<div class="gen">{_esc(result.generated_at)}<br>caso: {_esc(result.case)} '
        f'&middot; tipo: {_esc(result.kind)}</div>',
        f'<a class="btn" href="{_esc(os.path.basename(hist_path))}">Historico</a>',
        '<button class="btn" onclick="ajuda()">? Ajuda</button>',
        '<button class="btn" onclick="window.print()">Imprimir</button>',
        "</div></div>",
    ]

    origem = {id(f): s.title for s in result.sections for f in s.findings}
    n_rec = sum(1 for f in findings if f.recommendation)

    # ── abas ──
    abas = [("resumo", "Resumo", ""),
            ("achados", "Achados", f'<span class="n{" crit" if n_crit else ""}">'
                                   f'{len(findings)}</span>'),
            ("evid", "Evidencias", f'<span class="n">{len(result.sections)}</span>'),
            ("acoes", "Plano de acao",
             f'<span class="n">{n_rec}</span>' if n_rec else ""),
            ("operar", "Como operar", "")]
    head.append('<div class="main-tabs">' + "".join(
        f'<div class="main-tab{" active" if i == 0 else ""}" data-t="{k}" '
        f'onclick="tab(\'{k}\')">{lbl}{n}</div>'
        for i, (k, lbl, n) in enumerate(abas)) + "</div>")

    # ── aba Resumo ──
    # Tres blocos e para: como esta (hero), o que tratar primeiro, panorama por
    # categoria. O resumo executivo em texto vira um bloco recolhido no fim -
    # continua disponivel, mas nao e mais a primeira coisa que aparece.
    # O raciocinio vem logo depois do hero e ANTES das prioridades: primeiro
    # por que o banco esta assim, depois o que tratar. Na ordem inversa, o DBA
    # ja escolheu o que fazer antes de ler a explicacao.
    resumo = [_incompleto_html(result),
              _hero(result),
              _raciocinio_html(result),
              _prioridades_html(result, origem),
              _categorias_html(result)]
    if result.summary_items:
        lis = "".join(
            f'<li><span class="dot" style="background:{_sc(s)}"></span>'
            f'<span class="sumcat">{_esc(t)}</span>&mdash; {_esc(txt)}</li>'
            for t, txt, s in result.summary_items)
        resumo.append('<div class="section clp hdr-click">'
                      '<h3 onclick="clp(this)">Resumo executivo por categoria'
                      '<span style="font-weight:400;color:var(--mut);'
                      f'font-size:12px">&nbsp;({len(result.summary_items)} '
                      'linhas)</span><span class="caret">&#9660;</span></h3>'
                      '<div class="secbody"><div class="summary" '
                      'style="margin:0;border:none;box-shadow:none;padding:0">'
                      f'<ul class="sumlist">{lis}</ul></div></div></div>')
    elif result.summary:
        resumo.append('<div class="summary"><h2>Resumo executivo</h2>'
                      f'{_esc(result.summary)}</div>')

    # ── aba Achados (com filtro + busca) ──
    achados = [
        '<div class="controls">',
        "<label>Severidade</label><div class=\"cb-group\">",
        "".join(f'<label><input type="checkbox" class="fsev" value="{k}" '
                f'{"checked" if k != Severity.INFO else ""} onchange="filtrar()"> '
                f'{Severity.LABEL.get(k,"")}</label>'
                for k in (Severity.CRIT, Severity.WARN, Severity.OK, Severity.INFO)),
        "</div>",
        '<input class="search" id="q" placeholder="buscar em achados e evidencias..." '
        'oninput="filtrar()">',
        '<span class="count" id="fcount"></span>',
        "</div>",
        '<div class="content"><div id="findList">',
        _achados_agrupados_html(findings, origem),
        "</div></div>",
    ]

    # ── aba Evidencias ──
    # Ordenada por severidade e com as secoes sem achado grave recolhidas. Num
    # caso real sao 41 secoes, das quais 34 so dizem "tudo bem": abertas e em
    # ordem de analisador, as 7 que importam ficavam soterradas. Recolher nao e
    # esconder - o titulo, o semaforo e a contagem continuam visiveis, e o corpo
    # esta a um clique.
    ordem_sev = {Severity.CRIT: 0, Severity.WARN: 1, Severity.INFO: 2,
                 Severity.OK: 3}

    def _grave(s: Section) -> bool:
        return any(f.severity in (Severity.CRIT, Severity.WARN)
                   for f in s.findings)

    secs = sorted(enumerate(result.sections),
                  key=lambda p: (ordem_sev.get(p[1].severity, 3), p[0]))
    n_grave = sum(1 for _i, s in secs if _grave(s))
    n_quieta = len(secs) - n_grave
    evid = ['<div class="content">']
    if secs:
        resumo_evid = (f"{n_grave} secao(oes) com achado"
                       + (f" &middot; {n_quieta} sem achado, recolhida(s)"
                          if n_quieta else ""))
        evid.append(
            '<div style="display:flex;gap:8px;align-items:center;'
            'margin-bottom:14px;flex-wrap:wrap">'
            '<button class="btn" onclick="clpAll(false)">expandir tudo</button>'
            '<button class="btn" onclick="clpAll(true)">colapsar tudo</button>'
            f'<span class="count" style="margin-left:auto">{resumo_evid}</span>'
            "</div>")
    evid += [_section_html(s, idx=i, recolhida=not _grave(s))
             for i, s in secs] or \
        ['<div class="empty">Nenhuma evidencia analisada.</div>']
    evid.append("</div>")

    views = [("resumo", resumo, True), ("achados", achados, False),
             ("evid", evid, False), ("acoes", [_acoes_html(result)], True),
             ("operar", [operar.build(result.case)], True)]
    body = head
    for k, conteudo, envolver in views:
        inner = "".join(conteudo)
        if envolver:
            inner = f'<div class="content">{inner}</div>'
        body.append(f'<div class="tabview" data-t="{k}"'
                    + ("" if k == "resumo" else " hidden") + f">{inner}</div>")

    # Uma linha so no pe da pagina: a assinatura JA diz de onde veio o
    # relatorio. Manter o texto antigo alem dela seria dizer duas vezes.
    body.append('<div class="content">' + marca.rodape(
        "<span>Framework de Troubleshooting Oracle &middot; coleta read-only</span>")
        + "</div>")
    body.append('<div class="modal" id="modal" onclick="fecha()">'
                '<div class="modal-in" onclick="event.stopPropagation()">'
                '<button class="btn modal-x" onclick="fecha()">fechar</button>'
                '<div id="mBody"></div></div></div>')
    body.append(f'<template id="helpTpl">{_HELP}</template>')

    doc = ("<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
           + "".join(body)
           + f"<script>{_JS}\nfiltrar();</script></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


# ── Relatorio no Template Padrao (redija) ──────────────────
def _all_findings(result: CaseResult):
    out = []
    for s in result.sections:
        out.extend(s.findings)
    order = {Severity.CRIT: 0, Severity.WARN: 1, Severity.OK: 2, Severity.INFO: 3}
    return sorted(out, key=lambda f: order.get(f.severity, 3))


def build_incident_md(result: CaseResult, out_path: str | None = None) -> str:
    """Gera o relatorio no Template Padrao do prompt DBA Master (Diagnostico +
    RCA) preenchido a partir dos achados do caso."""
    out_path = out_path or os.path.join(_case_dir(result.case), f"{result.case}_relatorio.md")

    m = result.meta or {}
    findings = _all_findings(result)
    crit = [f for f in findings if f.severity == Severity.CRIT]
    warn = [f for f in findings if f.severity == Severity.WARN]
    sev = result.severity

    hostname = m.get("hostname") or result.subtitle or "<hostname>"
    evento = m.get("evento") or result.title or "<sintoma principal>"
    dados = ", ".join(s.title for s in result.sections) or "<evidencias>"

    # Causa e solucao registradas a mao (CASO.py) tem prioridade sobre a
    # deducao a partir dos achados: quem conduziu o caso sabe mais que a
    # heuristica de "pegue o achado mais critico".
    # A ordem importa: (1) o que o DBA registrou, (2) os destaques ranqueados por
    # impacto, (3) o achado mais severo. O passo (2) e novo - antes caia direto
    # em `crit[0]`, que e o primeiro critico da lista e nao o mais relevante:
    # num caso de bloqueio, um "tablespace em 91%" tambem critico podia virar a
    # causa raiz do chamado. O texto do (2) sai marcado como automatico.
    # (2b) o motor de raciocinio ENVOLVE o destaque em vez de disputar com ele:
    # os dois respondem perguntas diferentes. O destaque mede impacto e traz o
    # numero ("SQL 9xy: 23s -> 407s"); o motor nomeia o mecanismo ("regressao
    # de plano") e o gatilho provavel ("STATS de 08/08 em KIPREV.RE_COBRANCA").
    # Com so o primeiro, o chamado descreve o sintoma e nao diz a causa; com so
    # o segundo, afirma a causa sem o numero que a sustenta.
    causa = m.get("causa_raiz") \
        or raciocinio.causa_com_porque(result,
                                       destaques_mod.causa_proposta(result)) \
        or (crit[0].title if crit else (warn[0].title if warn else
                                        "A confirmar com os dados coletados"))
    solucao = m.get("solucao") \
        or " ".join(f"({i+1}) {f.recommendation}" for i, f in enumerate(crit[:3]) if f.recommendation) \
        or " ".join(f"({i+1}) {f.recommendation}" for i, f in enumerate(warn[:3]) if f.recommendation) \
        or "A definir conforme causa raiz."
    prevencao = m.get("prevencao") or (
        "Monitoramento continuo do indicador afetado; baseline via AWR; "
        "revisao de estatisticas/planos e capacidade.")
    status = m.get("status") or ("Investigando" if sev in (Severity.CRIT, Severity.WARN) else "Resolvido")
    escalado = m.get("escalado") or ("Sim" if any("ORA-00600" in f.title or "ORA-07445" in f.title
                                                  or "corrup" in f.title.lower() for f in crit) else "N/A")

    # linhas de achados para "Ação/Impacto"
    ach_lines = "\n".join(
        f"  - [{Severity.LABEL.get(f.severity,'')}] {f.title}"
        + (f" (impacto: {f.impact})" if f.impact else "")
        + (f" -> {f.recommendation}" if f.recommendation else "")
        for f in findings[:12]) or "  - (sem achados classificados)"

    # Acoes executadas pelo DBA (CASO.py). Vem ANTES dos achados automaticos:
    # numa auditoria o que importa primeiro e o que foi feito no banco, nao o
    # que a ferramenta observou. Risco ALTO fica marcado.
    acoes_manuais = m.get("acoes") or []
    if acoes_manuais:
        exec_lines = "\n".join(
            f"  - {a.get('em','')} [{'RISCO ALTO' if a.get('risco') == 'alto' else a.get('risco','')}]"
            f" {a.get('descricao','')}" for a in acoes_manuais)
        ach_lines = (f"  Executado pelo DBA ({len(acoes_manuais)} acao(oes)):\n"
                     f"{exec_lines}\n  Observado pela analise:\n{ach_lines}")

    # Hipoteses descartadas: evita que outra pessoa (ou voce em duas semanas)
    # refaca um caminho que ja foi testado e nao explicou o sintoma.
    hips = m.get("hipoteses") or []
    descartadas = ("\n".join(
        f"- ~~{h.get('texto','')}~~ - descartada em {h.get('descartada_em','')}: "
        f"{h.get('motivo','')}" for h in hips)
        if hips else "- Nenhuma hipotese descartada até agora.")

    # resumo executivo como lista por categoria (com severidade)
    if result.summary_items:
        resumo = "\n".join(
            f"- **{t}** ({Severity.LABEL.get(s,'')}): {txt}"
            for t, txt, s in result.summary_items)
    else:
        resumo = result.summary

    # Proximos passos: os comandos de drill-down dos achados que trazem SQL_ID
    # ou hora. Ficam no .md de proposito - e este arquivo que o chat le quando
    # pedem "detalhar <caso>", entao a sugestao chega junto com a analise.
    _conn = m.get("conn", "")
    _advisor_feito = advisor_ja_rodou(_evidence_dir(result))
    _cmds = []
    for f in findings:
        c = next_command(f, result.case, _conn)
        if c and all(c != x[1] for x in _cmds):
            _cmds.append((f.title, c))
    proximos = "\n".join(f"- **{t}**\n  ```\n  {c}\n  ```" for t, c in _cmds[:8]) \
        or "- Nenhum achado apontou SQL_ID ou janela de tempo para aprofundar."

    # O advisor entra em bloco proprio, nunca misturado aos comandos read-only
    # acima: e a unica sugestao do relatorio que escreve no banco.
    _adv = []
    for f in findings:
        c = advisor_command(f, result.case, _conn, ja_rodou=_advisor_feito)
        if c and all(c != x for x in _adv):
            _adv.append(c)
    if _adv:
        avancado = ("Houve troca de plano ou regressao de alto impacto. O SQL "
                    "Tuning Advisor pode propor um SQL Profile que recupera o "
                    "plano bom:\n\n```\n" + _adv[0] + "\n```\n\n"
                    "> **ATENCAO** - nao e read-only: cria e executa tuning "
                    "tasks e consome Tuning Pack. As recomendacoes sao "
                    "anexadas ao `acoes.sql`, comentadas; nada e aplicado.")
    elif _advisor_feito:
        avancado = ("SQL Tuning Advisor ja executado neste caso - veja "
                    "`advisor.txt` e a secao do advisor no `acoes.sql`.")
    else:
        avancado = ("Nenhum achado justifica o SQL Tuning Advisor "
                    "(criterio: troca de plano ou impacto >= 300s).")

    # Plano priorizado: a resposta para "o que eu faco primeiro?". Vem antes
    # dos proximos passos porque drill-down e investigacao, nao acao.
    plano_txt = plano_markdown(result)
    correlacao_txt = correlacao_markdown(result)
    raciocinio_txt = raciocinio.markdown(result)
    destaques_txt = destaques_mod.markdown(result)
    _impacto_medido = destaques_mod.impacto_proposto(result)

    d = result.diff
    if d:
        _delta = (d.get("score") or 0) - (d.get("prev_score") or 0)
        mudancas = (f"Score {d.get('score')}/100 ({_delta:+d} vs anterior {d.get('prev_score')}). "
                    f"Novos: {len(d.get('new', []))}; Resolvidos: {len(d.get('resolved', []))}; "
                    f"Mesmo achado c/ outra medida: {len(d.get('varied', []))}; "
                    f"Mudanca de severidade: {len(d.get('changed', []))}.")
    else:
        mudancas = "Primeira analise do caso (sem historico para comparar)."

    # Timeline do RCA: os eventos REAIS do caso quando existirem. O campo
    # manual (`meta['timeline']`) continua vencendo - quem escreveu a mao
    # descreveu o incidente, nao so o rastro da ferramenta.
    _ev = caso_lifecycle.linha_do_tempo(result.case)
    if m.get("timeline"):
        timeline_txt = m["timeline"]
    elif _ev:
        timeline_txt = "\n" + "\n".join(
            f"  - {e.get('em') or 'sem hora'} [{_TL_TAG.get(e.get('tipo',''), e.get('tipo',''))}]"
            f" {e.get('texto','')}"
            + (" (RISCO ALTO)" if e.get("risco") == "alto" else "")
            for e in _ev)
    else:
        timeline_txt = "Ver timestamps das evidencias coletadas."

    try:
        reincidentes = reincidencia.do_resultado(result)
    except Exception:
        reincidentes = []
    reincidencia_txt = reincidencia.markdown(reincidentes)

    # Analisador que falhou nao pode virar silencio: este .md e o texto que vai
    # para o chamado, e um Health Score alto por falta de analise induz quem le
    # a fechar o incidente. O aviso vai no topo, antes do diagnostico.
    if result.erros:
        incompleto = (
            f"\n> **ATENCAO - analise incompleta.** {len(result.erros)} "
            "analisador(es) falharam; o Health Score e os achados abaixo NAO "
            "cobrem estas categorias:\n"
            + "\n".join(f"> - {e}" for e in result.erros) + "\n")
    else:
        incompleto = ""

    txt = f"""# Relatorio de Troubleshooting - {result.case}
_Template Padrao DBA Master | gerado em {result.generated_at} | severidade: {Severity.LABEL.get(sev,'')} | Health Score: {result.score}/100_
{incompleto}
## Diagnostico Support Platform
- **Hostname de referencia:** {hostname}
- **Evento de referencia:** {evento}
- **Hipotese inicial:** {m.get('hipotese_inicial', causa)}
- **Hipotese atual:** {m.get('hipotese_atual', causa)}
- **Dados coletados:** {dados}
- **Acao Support Platform:**
{ach_lines}
- **Riscos mitigados:** {m.get('riscos', 'Acoes destrutivas (kill/rebuild/shrink) exibidas com aviso e rollback planejado antes de executar.')}
- **Escalado:** {escalado}
- **Status:** {status}
- **Criado em:** {m.get('criado_em', result.generated_at)}
- **Resolvido em:** {m.get('resolvido_em', '-')}

## RCA
- **Timeline:** {timeline_txt}
- **Impacto:** {m.get('impacto') or _impacto_medido or ('Degradacao/indisponibilidade conforme severidade ' + Severity.LABEL.get(sev,'') + '.')}
- **Causa Raiz:** {causa}
- **Solucao:** {solucao}
- **Prevencao:** {prevencao}

## Raciocinio - por que esta e a causa
{raciocinio_txt}

## Hipoteses ja descartadas
{descartadas}

## Resumo executivo
{resumo}

## Destaques da analise (carga, regressao, SQL pesado, bloqueio)
{destaques_txt}

## Plano de acao priorizado
{plano_txt}

## Correlacao com mudancas recentes
{correlacao_txt}

## Mudancas desde a ultima analise
{mudancas}

## Reincidencia no mesmo servidor
{reincidencia_txt}

## Proximos passos sugeridos (read-only)
{proximos}

## Investigacao avancada (escreve no banco)
{avancado}

---
_Dashboard: {result.case}.html | Apresentacao: {result.case}.pptx_
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(txt)
    return out_path


def build_exports(result: CaseResult) -> tuple:
    """Exporta os findings do caso em JSON e CSV (para BI/integracao/ticket).
    Grava na subpasta do caso. Retorna (json_path, csv_path)."""
    import csv
    cdir = _case_dir(result.case)
    conn = (result.meta or {}).get("conn", "")
    advisor_feito = advisor_ja_rodou(_evidence_dir(result))
    rows = [{
        "case": result.case,
        "section": s.title,
        "severity": f.severity,
        "severity_label": Severity.LABEL.get(f.severity, ""),
        "title": f.title,
        "impact": f.impact,
        "recommendation": f.recommendation,
        "next_command": next_command(f, result.case, conn),
        "advisor_command": advisor_command(f, result.case, conn,
                                           ja_rodou=advisor_feito),
    } for s in result.sections for f in s.findings]

    jpath = os.path.join(cdir, f"{result.case}_findings.json")
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump({
            "case": result.case, "title": result.title, "subtitle": result.subtitle,
            "generated_at": result.generated_at, "severity": result.severity,
            "score": result.score, "n_findings": len(rows), "findings": rows,
        }, fp, indent=2, ensure_ascii=False)

    cpath = os.path.join(cdir, f"{result.case}_findings.csv")
    with open(cpath, "w", newline="", encoding="utf-8-sig") as fp:  # BOM p/ Excel PT-BR
        w = csv.writer(fp, delimiter=";")
        w.writerow(["case", "severity", "section", "title", "impact",
                    "recommendation", "next_command", "advisor_command"])
        for r in rows:
            w.writerow([r["case"], r["severity_label"], r["section"],
                        r["title"], r["impact"], r["recommendation"],
                        r["next_command"], r["advisor_command"]])
    return jpath, cpath


# ── PPTX ───────────────────────────────────────────────────
def _rgb(hexstr: str):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(hexstr.lstrip("#"))


# Paleta do deck: a MESMA do dashboard, clara. O tema escuro anterior era
# ilegivel impresso e destoava do relatorio - e as cores vivas do semaforo
# (#f59e0b, #22c55e) nao tem contraste sobre branco, por isso o deck usa a
# versao escurecida `_sc()`, igual a tela.
_P = {
    "bg": "FFFFFF", "surf": "F8FAFC", "surf2": "F1F5F9",
    "line": "E2E8F0", "line2": "CBD5E1",
    "txt": "0F172A", "mut": "64748B", "brand": "EC0000", "acc": "2563EB",
}


def _tinta(cor: str, f: float = 0.88) -> str:
    """Versao clara de uma cor, misturando com branco (f = quanto de branco).

    Serve para o fundo do icone e para o trilho das barras: a mesma cor do
    acento, so que suave. Sem isso cada bloco decorativo escolheria um cinza
    proprio e o deck perderia a unidade que o dashboard tem.
    """
    c = cor.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda v: round(v + (255 - v) * f)
    return f"{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def _pptx_kit(prs):
    """Primitivas de layout do deck.

    Concentradas aqui para que TODO slide use a mesma grade, o mesmo card e a
    mesma tipografia - a bagunca do deck anterior vinha de cada slide posicionar
    caixa solta com numero magico.
    """
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    k = {}
    # grade de 12 colunas com margem de 0,6" - todo bloco encaixa nela
    MARG, GUT, COLS = 0.6, 0.18, 12
    LARG = 13.333
    col_w = (LARG - 2 * MARG - GUT * (COLS - 1)) / COLS

    def x(col):
        return MARG + col * (col_w + GUT)

    def w(n):
        return n * col_w + (n - 1) * GUT

    k["x"], k["w"], k["MARG"] = x, w, MARG

    def fundo(slide, cor=None):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(cor or _P["bg"])

    def texto(slide, l, t, ww, h, txt, size=12, bold=False, cor=None,
              align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, espaco=1.0):
        tb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(ww), Inches(h))
        # Sem isto a caixa herda o efeito do tema do Office. Num template
        # corporativo que define sombra em texto, o deck inteiro sai borrado
        # no projetor - e a culpa parece do arquivo, nao do tema.
        tb.shadow.inherit = False
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = 0
        tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = anchor
        for i, linha in enumerate(str(txt).split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.line_spacing = espaco
            r = p.add_run()
            r.text = linha
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = "Segoe UI"
            r.font.color.rgb = _rgb(cor or _P["txt"])
        return tb

    def card(slide, l, t, ww, h, fill=None, borda=None, raio=0.06):
        """Retangulo arredondado sem sombra - o 'cartao' do deck."""
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                    Inches(l), Inches(t), Inches(ww), Inches(h))
        sh.adjustments[0] = raio
        sh.fill.solid()
        sh.fill.fore_color.rgb = _rgb(fill or _P["surf"])
        sh.line.color.rgb = _rgb(borda or _P["line"])
        sh.line.width = Pt(0.75)
        sh.shadow.inherit = False
        sh.text_frame.text = ""
        return sh

    def barra(slide, l, t, ww, h, cor):
        """Faixa solida (acento lateral do card, regua do cabecalho)."""
        sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                    Inches(l), Inches(t), Inches(ww), Inches(h))
        sh.fill.solid()
        sh.fill.fore_color.rgb = _rgb(cor)
        sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def ponto(slide, l, t, d, cor):
        sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(l), Inches(t),
                                    Inches(d), Inches(d))
        sh.fill.solid()
        sh.fill.fore_color.rgb = _rgb(cor)
        sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def _forma(slide, tipo, l, t, ww, h, cor, borda=None, ajustes=()):
        """Forma nativa preenchida, sem sombra. Base de todo icone."""
        sh = slide.shapes.add_shape(tipo, Inches(l), Inches(t),
                                    Inches(ww), Inches(h))
        for i, v in enumerate(ajustes):
            try:
                sh.adjustments[i] = v
            except (IndexError, ValueError):
                break
        sh.fill.solid()
        sh.fill.fore_color.rgb = _rgb(cor)
        if borda:
            sh.line.color.rgb = _rgb(borda)
            sh.line.width = Pt(1)
        else:
            sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def pilula(slide, l, t, ww, h, cor):
        """Barra de cantos totalmente arredondados.

        Retangulo reto num slide de 2026 parece planilha. O raio maximo do
        ROUNDED_RECTANGLE e 0.5 da menor dimensao - com isso a barra vira
        pilula sozinha, qualquer que seja a altura.
        """
        return _forma(slide, MSO_SHAPE.ROUNDED_RECTANGLE, l, t, ww, h, cor,
                      ajustes=(0.5,))

    # Icones desenhados com formas NATIVAS do PowerPoint, nao com imagem nem
    # com fonte de simbolos. Tres razoes, nesta ordem: (1) o deck e aberto no
    # PowerPoint do banco, onde uma fonte de icones pode simplesmente nao
    # existir e virar quadradinho; (2) imagem exigiria dependencia nova e
    # arquivo externo, e o deck viaja por e-mail; (3) vetor nao borra no
    # projetor nem ao imprimir.
    def icone(slide, l, t, tam, nome, cor, com_fundo=True):
        """Icone de `tam` polegadas dentro de um disco claro da mesma cor."""
        if com_fundo:
            _forma(slide, MSO_SHAPE.OVAL, l, t, tam, tam, _tinta(cor, 0.86))
        # area util do glifo, centrada no disco
        g = tam * 0.52
        gl, gt = l + (tam - g) / 2, t + (tam - g) / 2

        if nome == "tempo":
            _forma(slide, MSO_SHAPE.OVAL, gl, gt, g, g, cor)
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.16, gt + g * 0.16,
                   g * 0.68, g * 0.68, _tinta(cor, 0.86))
            # ponteiros: um vertical, um horizontal, partindo do centro
            _forma(slide, MSO_SHAPE.RECTANGLE, gl + g * 0.46, gt + g * 0.26,
                   g * 0.08, g * 0.28, cor)
            _forma(slide, MSO_SHAPE.RECTANGLE, gl + g * 0.5, gt + g * 0.46,
                   g * 0.26, g * 0.08, cor)
        elif nome == "escudo":
            _forma(slide, MSO_SHAPE.HEXAGON, gl, gt - g * 0.04, g, g * 1.08,
                   cor, ajustes=(0.28,))
        elif nome == "raio":
            _forma(slide, MSO_SHAPE.LIGHTNING_BOLT, gl + g * 0.16, gt,
                   g * 0.68, g, cor)
        elif nome == "banco":
            _forma(slide, MSO_SHAPE.FLOWCHART_MAGNETIC_DISK,
                   gl + g * 0.06, gt + g * 0.1, g * 0.88, g * 0.8, cor)
        elif nome == "doc":
            _forma(slide, MSO_SHAPE.FLOWCHART_DOCUMENT, gl + g * 0.08, gt,
                   g * 0.84, g, cor)
        elif nome == "engrenagem":
            _forma(slide, MSO_SHAPE.GEAR_6, gl, gt + g * 0.08, g, g * 0.84, cor)
        elif nome == "alvo":
            _forma(slide, MSO_SHAPE.OVAL, gl, gt, g, g, cor)
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.2, gt + g * 0.2,
                   g * 0.6, g * 0.6, _tinta(cor, 0.86))
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.36, gt + g * 0.36,
                   g * 0.28, g * 0.28, cor)
        elif nome == "grafico":
            for i, hh in enumerate((0.42, 0.7, 1.0)):
                _forma(slide, MSO_SHAPE.ROUNDED_RECTANGLE,
                       gl + i * g * 0.36, gt + g * (1 - hh), g * 0.24,
                       g * hh, cor, ajustes=(0.4,))
        elif nome == "fila":
            for i in range(3):
                _forma(slide, MSO_SHAPE.ROUNDED_RECTANGLE, gl,
                       gt + i * g * 0.38, g, g * 0.24, cor, ajustes=(0.5,))
        elif nome == "pessoas":
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.28, gt, g * 0.44,
                   g * 0.44, cor)
            _forma(slide, MSO_SHAPE.ROUNDED_RECTANGLE, gl + g * 0.06,
                   gt + g * 0.52, g * 0.88, g * 0.48, cor, ajustes=(0.4,))
        elif nome == "seta":
            _forma(slide, MSO_SHAPE.RIGHT_ARROW, gl, gt + g * 0.22, g,
                   g * 0.56, cor)
        elif nome == "check":
            _forma(slide, MSO_SHAPE.OVAL, gl, gt, g, g, cor)
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.18, gt + g * 0.18,
                   g * 0.64, g * 0.64, _tinta(cor, 0.86))
        else:  # ponto neutro: melhor um disco do que um icone errado
            _forma(slide, MSO_SHAPE.OVAL, gl + g * 0.2, gt + g * 0.2,
                   g * 0.6, g * 0.6, cor)

    def anel(slide, l, t, d, fracao, cor, espessura=0.2):
        """Anel de progresso: trilho claro + arco cheio a partir das 12h.

        `fracao` 0..1. O arco e um BLOCK_ARC nativo, cujos ajustes sao angulos
        em GRAUS medidos no sentido horario a partir das 3 horas - por isso o
        inicio e 270 (meio-dia). Fracao cheia e limitada a 0.999: em 360 graus
        exatos o inicio encosta no fim e o PowerPoint desenha um anel vazio.
        """
        _forma(slide, MSO_SHAPE.DONUT, l, t, d, d, _tinta(cor, 0.86),
               ajustes=(espessura,))
        f = max(0.0, min(0.999, float(fracao)))
        if f <= 0:
            return
        _forma(slide, MSO_SHAPE.BLOCK_ARC, l, t, d, d, cor,
               ajustes=(270.0, (270.0 + 360.0 * f) % 360.0, espessura))

    def cabecalho(slide, titulo, sub="", cor=None):
        """Cabecalho padrao: regua de acento + titulo + subtitulo."""
        barra(slide, MARG, 0.42, 0.055, 0.42, cor or _P["brand"])
        texto(slide, MARG + 0.16, 0.40, 9.4, 0.45, titulo, 21, True)
        if sub:
            texto(slide, MARG + 0.16, 0.86, 11.0, 0.3, sub, 10.5, False, _P["mut"])
        return 1.32 if sub else 1.05

    def rodape(slide, n, caso):
        barra(slide, 0, 7.30, 13.333, 0.012, _P["line"])
        texto(slide, MARG, 7.05, 8.0, 0.25, caso, 8.5, False, _P["mut"])
        texto(slide, 11.0, 7.05, 1.733, 0.25, str(n), 8.5, False, _P["mut"],
              align=PP_ALIGN.RIGHT)

    k.update(fundo=fundo, texto=texto, card=card, barra=barra, ponto=ponto,
             cabecalho=cabecalho, rodape=rodape,
             forma=_forma, pilula=pilula, icone=icone, anel=anel,
             tinta=_tinta, SHAPE=MSO_SHAPE,
             ALIGN=PP_ALIGN, ANCHOR=MSO_ANCHOR, Inches=Inches, Pt=Pt, Emu=Emu)
    return k


def _pptx_corta(txt: str, n: int) -> str:
    t = " ".join(str(txt or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "\u2026"


# Quantas categorias ganham slide proprio. O deck anterior fazia um slide por
# secao: 46 slides num caso real, que ninguem apresenta. O corte nao esconde
# nada - o panorama mostra TODAS as categorias, e o detalhe completo esta no
# HTML, que e o artefato de consulta.
MAX_SLIDES_DETALHE = 8


def build_pptx(result: CaseResult, out_path: str | None = None) -> str:
    """Deck executivo do caso.

    Regra que organiza o deck: ele responde "como esta, o que trato primeiro e
    em que ordem" para quem tem 10 minutos. Profundidade fica no HTML.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt

    out_path = out_path or os.path.join(_case_dir(result.case), f"{result.case}.pptx")

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    k = _pptx_kit(prs)
    texto, card, barra, ponto = k["texto"], k["card"], k["barra"], k["ponto"]
    cabecalho, rodape, fundo = k["cabecalho"], k["rodape"], k["fundo"]
    ALIGN, MARG = k["ALIGN"], k["MARG"]
    x, w = k["x"], k["w"]

    findings = _all_findings(result)
    n_crit = sum(1 for f in findings if f.severity == Severity.CRIT)
    n_warn = sum(1 for f in findings if f.severity == Severity.WARN)
    sev, score = result.severity, result.score
    cor_score = _score_color(score).lstrip("#")
    caso_rot = f"{result.case}  |  {result.generated_at}"
    n_slide = [0]

    def novo(titulo="", sub="", com_rodape=True):
        s = prs.slides.add_slide(blank)
        fundo(s)
        topo = cabecalho(s, titulo, sub) if titulo else 0.5
        n_slide[0] += 1
        if com_rodape:
            rodape(s, n_slide[0], caso_rot)
        return s, topo

    # ── 1. Capa ───────────────────────────────────────────────
    s = prs.slides.add_slide(blank)
    fundo(s)
    n_slide[0] += 1
    barra(s, 0, 0, 13.333, 0.28, _P["brand"])
    # A capa assina igual as telas HTML. Com o logo oficial em `assets/`, ele
    # entra como imagem; sem o arquivo, a assinatura continua sendo o nome em
    # texto - o deck TEM de sair igual numa maquina sem os assets, que e a
    # regra do projeto para tudo que circula por e-mail.
    #
    # A restricao antiga era o SVG, que o python-pptx nao embute. Com o PNG
    # isso deixou de valer: nao ha conversao nem dependencia nova.
    logo = marca.logo_arquivo()
    if logo:
        alt_logo = 0.92
        s.shapes.add_picture(logo, Inches(MARG), Inches(0.62),
                             height=Inches(alt_logo))
        y_sub = 0.62 + alt_logo + 0.06
    else:
        texto(s, MARG, 0.75, 6, 0.35, "PARRADIBIEI", 12, True, _P["brand"])
        y_sub = 1.15
    texto(s, MARG, y_sub, 5.6, 0.3, "Framework de Troubleshooting Oracle",
          10.5, False, _P["mut"])
    texto(s, MARG, 2.35, 9.4, 1.5,
          result.title or f"Troubleshooting - {result.case}", 36, True,
          espaco=1.08)
    if result.subtitle:
        texto(s, MARG, 3.95, 9.4, 0.5, _pptx_corta(result.subtitle, 120),
              14, False, _P["mut"])

    # cartao do score, ancorado a direita
    card(s, 9.9, 2.35, 2.84, 2.1, _P["surf"], _P["line"])
    texto(s, 9.9, 2.62, 2.84, 0.9, str(score), 54, True, cor_score,
          align=ALIGN.CENTER)
    texto(s, 9.9, 3.52, 2.84, 0.3, "HEALTH SCORE / 100", 9, True, _P["mut"],
          align=ALIGN.CENTER)
    barra(s, 10.35, 3.92, 1.94, 0.028, _P["line2"])
    texto(s, 9.9, 4.02, 2.84, 0.3, Severity.LABEL.get(sev, ""), 12, True,
          _sc(sev).lstrip("#"), align=ALIGN.CENTER)

    # tres numeros da capa
    for i, (n, rot, c) in enumerate((
            (n_crit, "CRITICOS", _sc(Severity.CRIT) if n_crit else _P["mut"]),
            (n_warn, "ATENCAO", _sc(Severity.WARN) if n_warn else _P["mut"]),
            (len(result.sections), "CATEGORIAS", _P["mut"]))):
        bx = MARG + i * 2.35
        texto(s, bx, 4.95, 2.1, 0.5, str(n), 26, True, str(c).lstrip("#"))
        texto(s, bx, 5.45, 2.1, 0.3, rot, 9, True, _P["mut"])

    barra(s, MARG, 6.25, 12.13, 0.012, _P["line"])
    texto(s, MARG, 6.42, 8.0, 0.3,
          f"Gerado em {result.generated_at}   |   caso: {result.case}"
          f"   |   tipo: {result.kind}", 9.5, False, _P["mut"])
    texto(s, 8.6, 6.42, 4.13, 0.3, "Coleta read-only", 9.5, False, _P["mut"],
          align=ALIGN.RIGHT)

    # ── 2. Panorama executivo ────────────────────────────────
    if n_crit:
        vered = (f"{n_crit} achado(s) critico(s) exigem acao imediata. "
                 "A ordem sugerida esta no plano de acao.")
    elif n_warn:
        vered = (f"{n_warn} ponto(s) de atencao para tratar na proxima "
                 "janela. Nenhum achado critico.")
    else:
        vered = "Nenhum achado critico ou de atencao nesta execucao."

    d = result.diff or {}
    if d:
        delta = (d.get("score") or 0) - (d.get("prev_score") or 0)
        seta = "+" if delta > 0 else ""
        evol = (f"{seta}{delta} ponto(s) desde a execucao anterior "
                f"({d.get('prev_score')}/100)")
    else:
        evol = "Primeira execucao deste caso: sem base de comparacao."

    s, topo = novo("Panorama", "como o banco esta agora")
    card(s, MARG, topo, w(4), 2.5, _P["surf"], _P["line"])
    texto(s, MARG, topo + 0.42, w(4), 1.1, str(score), 60, True, cor_score,
          align=ALIGN.CENTER)
    texto(s, MARG, topo + 1.52, w(4), 0.3, "HEALTH SCORE / 100", 9.5, True,
          _P["mut"], align=ALIGN.CENTER)
    texto(s, MARG, topo + 1.92, w(4), 0.35, Severity.LABEL.get(sev, ""), 13,
          True, _sc(sev).lstrip("#"), align=ALIGN.CENTER)

    for i, (n, rot, c) in enumerate((
            (n_crit, "Criticos", _sc(Severity.CRIT) if n_crit else _P["mut"]),
            (n_warn, "Em atencao", _sc(Severity.WARN) if n_warn else _P["mut"]),
            (len(result.sections), "Categorias", _P["acc"]),
            (len(findings), "Achados", _P["mut"]))):
        cx = x(4) + i * (w(2) + 0.18)
        card(s, cx, topo, w(2), 1.16, _P["surf"], _P["line"])
        texto(s, cx, topo + 0.2, w(2), 0.55, str(n), 26, True,
              str(c).lstrip("#"), align=ALIGN.CENTER)
        texto(s, cx, topo + 0.78, w(2), 0.28, rot, 9.5, False, _P["mut"],
              align=ALIGN.CENTER)

    card(s, x(4), topo + 1.34, w(8), 1.16, _P["surf"], _P["line"])
    barra(s, x(4), topo + 1.34, 0.05, 1.16, _sc(sev))
    texto(s, x(4) + 0.22, topo + 1.52, w(8) - 0.45, 0.4, "Veredito", 10, True,
          _P["mut"])
    texto(s, x(4) + 0.22, topo + 1.80, w(8) - 0.45, 0.6, vered, 12.5, False,
          espaco=1.25)

    yy = topo + 2.75
    texto(s, MARG, yy, 8.0, 0.3, "EVOLUCAO", 9.5, True, _P["mut"])
    texto(s, MARG, yy + 0.3, 12.13, 0.35, evol, 12)

    trend = [v for v in (d.get("trend") or []) if isinstance(v, (int, float))]
    if len(trend) >= 2:
        yy += 0.9
        texto(s, MARG, yy, 8.0, 0.3,
              f"ULTIMAS {len(trend)} EXECUCOES", 9.5, True, _P["mut"])
        yy += 0.36
        bw, gap = 0.62, 0.16
        base, alt = yy + 1.45, 1.25
        for i, v in enumerate(trend):
            h = max(0.06, alt * v / 100.0)
            bx = MARG + i * (bw + gap)
            barra(s, bx, base - h, bw, h, _score_color(v).lstrip("#"))
            texto(s, bx - 0.1, base + 0.06, bw + 0.2, 0.25, str(v), 8.5, True,
                  _P["mut"], align=ALIGN.CENTER)

    # ── 3. O que tratar primeiro ─────────────────────────────
    origem = {id(f): sec.title for sec in result.sections for f in sec.findings}
    graves = [f for f in findings
              if f.severity in (Severity.CRIT, Severity.WARN)]
    top, vistos = [], set()
    for f in graves:
        cat = origem.get(id(f), "")
        if cat in vistos:
            continue
        vistos.add(cat)
        top.append(f)
        if len(top) == 5:
            break

    if top:
        s, topo = novo("O que tratar primeiro",
                       "o pior achado de cada categoria afetada")
        h = 0.98
        for i, f in enumerate(top):
            ty = topo + i * (h + 0.14)
            c = _sc(f.severity)
            card(s, MARG, ty, 12.13, h, _P["surf"], _P["line"])
            barra(s, MARG, ty, 0.05, h, c)
            ponto(s, MARG + 0.24, ty + 0.30, 0.15, c)
            texto(s, MARG + 0.5, ty + 0.16, 8.3, 0.35,
                  _pptx_corta(f.title, 78), 13, True)
            texto(s, MARG + 0.5, ty + 0.54, 8.3, 0.34,
                  _pptx_corta(f.recommendation or f.impact or f.detail
                              or "Ver detalhe no relatorio.", 105),
                  10.5, False, _P["mut"])
            texto(s, 9.3, ty + 0.16, 3.3, 0.3, Severity.LABEL.get(f.severity, ""),
                  10, True, c, align=ALIGN.RIGHT)
            texto(s, 9.3, ty + 0.52, 3.3, 0.3,
                  _pptx_corta(origem.get(id(f), ""), 40), 9.5, False,
                  _P["mut"], align=ALIGN.RIGHT)
        resto = len(graves) - len(top)
        if resto > 0:
            texto(s, MARG, topo + 5 * (h + 0.14) + 0.06, 12.13, 0.3,
                  f"+{resto} achado(s) grave(s) no relatorio completo.",
                  10, False, _P["mut"])

    # ── 4. Plano de acao priorizado ──────────────────────────
    try:
        acoes = montar_plano(result)
    except Exception:
        acoes = []
    if acoes:
        s, topo = novo("Plano de acao",
                       "ordem: integridade do dado, recuperabilidade, "
                       "disponibilidade, infraestrutura e por fim performance")
        col_w = w(3)
        horizontes = ["agora", "hoje", "esta semana", "planejar"]
        por_h = {hz: [a for a in acoes if a.horizonte == hz]
                 for hz in horizontes}
        for i, hz in enumerate(horizontes):
            cx = x(i * 3)
            cor = _HORIZ_COR.get(hz, "#64748b").lstrip("#")
            itens = por_h.get(hz) or []
            card(s, cx, topo, col_w, 4.9, _P["surf"], _P["line"])
            barra(s, cx, topo, col_w, 0.05, cor)
            texto(s, cx + 0.16, topo + 0.2, col_w - 0.32, 0.3, hz.upper(),
                  11, True, cor)
            texto(s, cx + 0.16, topo + 0.5, col_w - 0.32, 0.25,
                  f"{len(itens)} acao(oes)", 9, False, _P["mut"])
            if not itens:
                texto(s, cx + 0.16, topo + 0.92, col_w - 0.32, 0.3, "nada aqui",
                      9.5, False, _P["mut"])
                continue
            iy = topo + 0.9
            for a in itens[:6]:
                texto(s, cx + 0.16, iy, col_w - 0.32, 0.32,
                      _pptx_corta(a.titulo, 52), 10, True, espaco=1.12)
                texto(s, cx + 0.16, iy + 0.34, col_w - 0.32, 0.28,
                      f"risco {a.risco}", 8.5, False,
                      _RISCO_COR.get(a.risco, "#64748b").lstrip("#"))
                iy += 0.72
                if iy > topo + 4.4:
                    break
            if len(itens) > 6:
                texto(s, cx + 0.16, topo + 4.55, col_w - 0.32, 0.25,
                      f"+{len(itens) - 6} no relatorio", 8.5, False, _P["mut"])

    # ── 5. Panorama por categoria (TODAS cabem aqui) ─────────
    if result.sections:
        ordem_sev = {Severity.CRIT: 0, Severity.WARN: 1,
                     Severity.INFO: 2, Severity.OK: 3}
        secs = sorted(result.sections,
                      key=lambda z: (ordem_sev.get(z.severity, 3), z.title))
        por_pag = 32
        paginas = [secs[i:i + por_pag] for i in range(0, len(secs), por_pag)]
        for pi, pag in enumerate(paginas):
            sub = "verde = sem achado; laranja = atencao; vermelho = critico"
            if len(paginas) > 1:
                sub += f"   ({pi + 1}/{len(paginas)})"
            s, topo = novo("Panorama por categoria", sub)
            linhas = 8
            cw, ch = w(3), 0.62
            for i, sec in enumerate(pag):
                col, lin = i // linhas, i % linhas
                cx, cy = x(col * 3), topo + lin * (ch + 0.07)
                nc = sum(1 for f in sec.findings
                         if f.severity == Severity.CRIT)
                nw = sum(1 for f in sec.findings
                         if f.severity == Severity.WARN)
                card(s, cx, cy, cw, ch, _P["surf"], _P["line"])
                ponto(s, cx + 0.16, cy + 0.235, 0.15, _sc(sec.severity))
                texto(s, cx + 0.42, cy + 0.1, cw - 0.6, 0.28,
                      _pptx_corta(sec.title, 34), 9.5, True)
                q = ", ".join(t for t in (f"{nc} crit" if nc else "",
                                          f"{nw} atencao" if nw else "") if t)
                texto(s, cx + 0.42, cy + 0.34, cw - 0.6, 0.24,
                      q or "sem achado", 8.5, False,
                      _sc(sec.severity).lstrip("#") if q else _P["mut"])

    # ── 6. Detalhe das categorias que doem ───────────────────
    criticas = [sec for sec in result.sections
                if sec.severity in (Severity.CRIT, Severity.WARN)]
    criticas.sort(key=lambda z: (0 if z.severity == Severity.CRIT else 1,
                                 z.title))
    for sec in criticas[:MAX_SLIDES_DETALHE]:
        c = _sc(sec.severity)
        s, topo = novo(_pptx_corta(sec.title, 62),
                       _pptx_corta(sec.note, 130))
        # etiqueta de severidade: fundo neutro com borda e texto na cor - o
        # preenchimento colorido chapado brigaria com os cards do corpo
        card(s, 11.0, 0.40, 1.73, 0.42, _P["bg"], c)
        texto(s, 11.0, 0.50, 1.73, 0.3, Severity.LABEL.get(sec.severity, ""),
              10, True, c, align=ALIGN.CENTER)
        y = topo

        mets = [m for m in sec.metrics][:4]
        if mets:
            for i, m in enumerate(mets):
                cx = x(i * 3)
                mc = _sc(m.severity) if m.severity in (
                    Severity.CRIT, Severity.WARN) else _P["txt"]
                card(s, cx, y, w(3), 1.0, _P["surf"], _P["line"])
                texto(s, cx + 0.16, y + 0.14, w(3) - 0.32, 0.26,
                      _pptx_corta(m.name, 34), 9, False, _P["mut"])
                texto(s, cx + 0.16, y + 0.42, w(3) - 0.32, 0.42,
                      _pptx_corta(m.display, 22), 17, True, mc.lstrip("#"))
            y += 1.2

        achados = sec.findings[:3]
        if achados:
            for f in achados:
                fc = _sc(f.severity)
                card(s, MARG, y, 12.13, 0.78, _P["surf"], _P["line"])
                barra(s, MARG, y, 0.05, 0.78, fc)
                texto(s, MARG + 0.22, y + 0.1, 11.6, 0.3,
                      _pptx_corta(f.title, 88), 11.5, True)
                sub = f.recommendation or f.impact or f.detail
                texto(s, MARG + 0.22, y + 0.42, 11.6, 0.28,
                      _pptx_corta(sub, 118), 9.5, False, _P["mut"])
                y += 0.9
            y += 0.05

        if sec.table_headers and sec.table_rows and y < 5.9:
            livre = 6.95 - y
            max_linhas = max(2, min(len(sec.table_rows),
                                    int(livre / 0.29) - 1, 9))
            rows = sec.table_rows[:max_linhas]
            cols = sec.table_headers[:7]
            alt = 0.32 + 0.28 * len(rows)
            tbl = s.shapes.add_table(len(rows) + 1, len(cols),
                                     k["Inches"](MARG), k["Inches"](y),
                                     k["Inches"](12.13),
                                     k["Inches"](alt)).table
            tbl.first_row = True
            for ci, hh in enumerate(cols):
                cell = tbl.cell(0, ci)
                cell.text = _pptx_corta(hh, 22)
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(_P["surf2"])
                cell.margin_left = cell.margin_right = k["Emu"](45720)
                for p in cell.text_frame.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)
                        r.font.bold = True
                        r.font.name = "Segoe UI"
                        r.font.color.rgb = _rgb("334155")
            for ri, row in enumerate(rows):
                rsev = (sec.row_severity[ri]
                        if ri < len(sec.row_severity) else "")
                for ci in range(len(cols)):
                    val = row[ci] if ci < len(row) else ""
                    cell = tbl.cell(ri + 1, ci)
                    cell.text = _pptx_corta(val, 30)
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = _rgb(
                        _P["bg"] if ri % 2 == 0 else _P["surf"])
                    cell.margin_left = cell.margin_right = k["Emu"](45720)
                    for p in cell.text_frame.paragraphs:
                        for r in p.runs:
                            r.font.size = Pt(8.5)
                            r.font.name = "Segoe UI"
                            r.font.color.rgb = _rgb(
                                _sc(rsev).lstrip("#")
                                if ci == 0 and rsev in (Severity.CRIT,
                                                        Severity.WARN)
                                else _P["txt"])
            if len(sec.table_rows) > len(rows):
                texto(s, MARG, y + alt + 0.06, 12.13, 0.25,
                      f"mostrando {len(rows)} de {len(sec.table_rows)} "
                      "linha(s) - lista completa no relatorio HTML",
                      8.5, False, _P["mut"])

    if len(criticas) > MAX_SLIDES_DETALHE:
        s, topo = novo("Demais categorias com achado",
                       "detalhe completo no relatorio HTML")
        resto = criticas[MAX_SLIDES_DETALHE:]
        for i, sec in enumerate(resto[:24]):
            col, lin = i // 8, i % 8
            cx, cy = x(col * 4), topo + lin * 0.66
            ponto(s, cx, cy + 0.1, 0.15, _sc(sec.severity))
            texto(s, cx + 0.26, cy, w(4) - 0.3, 0.3,
                  _pptx_corta(sec.title, 42), 10.5, True)
            nc = sum(1 for f in sec.findings if f.severity == Severity.CRIT)
            nw = sum(1 for f in sec.findings if f.severity == Severity.WARN)
            texto(s, cx + 0.26, cy + 0.28, w(4) - 0.3, 0.26,
                  ", ".join(t for t in (f"{nc} critico(s)" if nc else "",
                                        f"{nw} em atencao" if nw else "")
                            if t) or "sem achado",
                  9, False, _P["mut"])

    # ── 7. Encerramento ──────────────────────────────────────
    s, topo = novo("Como este material foi produzido",
                   "e onde esta o detalhe")
    blocos = [
        ("Coleta read-only",
         "Toda a evidencia vem de consulta com SET TRANSACTION READ ONLY, "
         "respeitando a licenca dos packs. Nenhum comando de escrita foi "
         "executado no banco."),
        ("Health Score 0-100",
         "Penaliza por CATEGORIA, com teto por categoria e retorno "
         "decrescente: nove tablespaces acima do limite sao um problema de "
         "espaco, nao nove. Serve para comparar execucoes do mesmo caso."),
        ("Onde esta o detalhe",
         f"Relatorio HTML completo em output/troubleshooting/{result.case}/ "
         "com todos os achados, evidencias, plano de acao e historico. "
         "Este deck e o resumo executivo."),
        ("Proximo passo",
         "Tratar o plano de acao na ordem apresentada e reexecutar o caso "
         "para comparar o Health Score - a evolucao fica registrada no "
         "historico."),
    ]
    for i, (tit, corpo) in enumerate(blocos):
        col, lin = i % 2, i // 2
        cx = x(col * 6)
        cy = topo + lin * 2.3
        card(s, cx, cy, w(6), 2.05, _P["surf"], _P["line"])
        barra(s, cx, cy, 0.05, 2.05, _P["acc"])
        texto(s, cx + 0.24, cy + 0.22, w(6) - 0.5, 0.35, tit, 13, True)
        texto(s, cx + 0.24, cy + 0.64, w(6) - 0.5, 1.2, corpo, 10.5, False,
              _P["mut"], espaco=1.3)

    prs.save(out_path)
    return out_path


# ── Indice dos casos ───────────────────────────────────────
_INDEX_JSON = "index.json"


def update_index(result: CaseResult) -> None:
    """Registra/atualiza o caso no index.json de output/troubleshooting."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, _INDEX_JSON)
    data = {}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            data = {}
    data[result.case] = {
        "case": result.case,
        "title": result.title,
        "subtitle": result.subtitle,
        "kind": result.kind,
        "severity": result.severity,
        "score": result.score,
        "generated_at": result.generated_at,
        "html": f"{result.case}/{result.case}.html",
        "pptx": f"{result.case}/{result.case}.pptx",
        "n_findings": sum(len(s.findings) for s in result.sections),
    }
    json.dump(data, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def build_index(out_path: str | None = None) -> str:
    """Gera output/troubleshooting/index.html listando todos os casos."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, _INDEX_JSON)
    data = {}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            data = {}
    out_path = out_path or os.path.join(OUTPUT_DIR, "index.html")

    # Casos apagados a mao ficariam no index.json para sempre, virando card com
    # link quebrado. Descarta quem nao tem mais o HTML e regrava o indice.
    vivos = {k: v for k, v in data.items()
             if os.path.exists(os.path.join(OUTPUT_DIR, v.get("html", "")))}
    if len(vivos) != len(data):
        data = vivos
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    order = {"crit": 0, "warn": 1, "ok": 2, "info": 3}
    items = sorted(data.values(),
                   key=lambda d: (order.get(d.get("severity", "info"), 3),
                                  d.get("generated_at", "")), reverse=False)

    cards = []
    n_abertos = 0
    for it in items:
        sev = it.get("severity", "info")
        color = _sc(sev)
        sc = it.get("score")
        sc_html = ""
        if sc is not None:
            sc_html = (f'<span style="font-weight:800;color:{_score_color(sc)}">{sc}'
                       f'<span style="font-size:10px;color:var(--mut)">/100</span></span>')

        # Ciclo de vida lido AO VIVO do meta.json, nao do index.json: o caso
        # continua evoluindo (hipotese nova, acao registrada) entre duas
        # analises, e o histórico tem de mostrar o estado de agora.
        ciclo = caso_lifecycle.resumo(it.get("case", ""))
        if ciclo["status"] in caso_lifecycle.STATUS_ABERTOS:
            n_abertos += 1
        vida = ""
        if ciclo["tem_ciclo_de_vida"] or ciclo["status"] != "-":
            partes = [f'<span class="st">{_esc(ciclo["status"])}</span>']
            if ciclo["acoes"]:
                alto = (f' <span class="risco">{ciclo["acoes_risco_alto"]} de risco alto</span>'
                        if ciclo["acoes_risco_alto"] else "")
                partes.append(f'{ciclo["acoes"]} acao(oes){alto}')
            if ciclo["hipoteses_descartadas"]:
                partes.append(f'{ciclo["hipoteses_descartadas"]} hipotese(s) descartada(s)')
            hip = ciclo["hipotese_atual"]
            vida = f'<div class="ic-vida">{" &middot; ".join(partes)}</div>'
            if hip:
                vida += f'<div class="ic-hip">Hipotese: {_esc(hip)}</div>'

        cards.append(
            f'<a class="ic" href="{_esc(it.get("html"))}">'
            f'<div class="ic-top"><span class="badge" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55">{Severity.LABEL.get(sev,"")}</span>'
            f'{sc_html}'
            f'<span class="kind">{_esc(it.get("kind",""))}</span></div>'
            f'<div class="ic-title">{_esc(it.get("title") or it.get("case"))}</div>'
            f'<div class="ic-sub">{_esc(it.get("subtitle",""))}</div>'
            f'{vida}'
            f'<div class="ic-meta">{_esc(it.get("generated_at",""))} &middot; '
            f'{it.get("n_findings",0)} achado(s) &middot; '
            f'<span class="pptx">PPTX: {_esc(it.get("pptx"))}</span></div></a>'
        )
    if not cards:
        cards.append('<div class="empty">Nenhum caso gerado ainda. '
                     'Rode: <code>.\\redija.bat &lt;caso&gt;</code></div>')

    css = _CSS + """
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.ic{display:block;text-decoration:none;color:var(--txt);background:var(--card);
border:1px solid var(--line);border-radius:14px;padding:16px 18px;transition:.15s;
box-shadow:0 1px 2px rgba(15,23,42,.05)}
.ic:hover{border-color:var(--acc);transform:translateY(-2px);box-shadow:0 6px 16px rgba(15,23,42,.1)}
.ic-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.ic .kind{color:var(--mut);font-size:12px;text-transform:uppercase}
.ic-title{font-weight:700;font-size:15px;margin-bottom:3px}
.ic-sub{color:var(--mut);font-size:13px;margin-bottom:8px}
.ic-meta{color:var(--mut);font-size:11px}.pptx{color:var(--mut)}
.ic-vida{font-size:11px;color:var(--mut);margin-bottom:4px}
.ic-vida .st{font-weight:700;color:var(--txt)}
.ic-vida .risco{color:#b91c1c;font-weight:700}
.ic-hip{font-size:12px;margin-bottom:8px;padding-left:8px;border-left:2px solid var(--line)}
"""
    doc = (
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Troubleshooting - Casos</title><style>{css}</style></head><body>"
        '<div class="header">' + marca.assinatura() +
        '<div><h1>Troubleshooting - Casos</h1>'
        '<div class="sub">Relatorios padronizados gerados</div></div>'
        f'<div class="hdr-right"><div class="gen">{len(cards)} caso(s)'
        + (f' &middot; {n_abertos} em aberto' if n_abertos else '')
        + '</div></div></div>'
        f'<div class="content"><div class="grid">{"".join(cards)}</div>'
        + marca.rodape(
            "<span>Framework de Troubleshooting Oracle</span>") +
        "</div></body></html>"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path
