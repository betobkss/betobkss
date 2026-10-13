"""
Analisador de topologia e de cluster (RAC / RAC One Node).

Le topology.txt (fase 1 da coleta, ver troubleshoot/topologia.py) e
rac_nodes.txt (sessoes por no, servicos ativos, interconnect).

O motivo de existir: o MESMO numero significa coisas opostas dependendo do
ambiente, e sem isso a plataforma erra de duas formas simetricas.

- "1 instancia no ar" e o normal em standalone e em RAC One Node, e e um
  INCIDENTE num RAC de 3 nos. Comparar contra CLUSTER_DATABASE_INSTANCES sem
  olhar o tipo do banco produziria falso positivo em todo RAC One Node do
  parque; nao comparar deixaria passar dois nos caidos num RAC.
- gv$instance mostra apenas quem esta NO AR. Um no fora nao aparece como linha
  com STATUS='DOWN' - ele simplesmente nao vem. Por isso a unica forma de
  detectar no caido e contra o numero de instancias CONFIGURADAS.
- Em RAC One Node, ver 2 instancias no ar nao e problema: e relocacao
  (online relocation) em andamento, que dura poucos minutos.
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text

# Defaults espelhando mcp/catalog.json (fallback se o catalogo nao abrir).
PADROES = {
    ("rac_nodes.txt", "skew_pct"): {"warn": 60.0, "crit": 80.0},
    ("rac_nodes.txt", "blocks_lost"): {"warn": 1.0, "crit": 100.0},
}

_SUBT = re.compile(r"^===\s*(.+?)\s*===\s*$", re.M)
_CACHE: dict = {}


def limiar(arquivo: str, chave: str) -> dict:
    """Limiares do catalogo MCP (fonte de verdade), com cache em modulo."""
    ck = (arquivo, chave)
    if ck not in _CACHE:
        val = dict(PADROES.get(ck, {}))
        try:
            from mcp.loader import load_catalog
            for chk in load_catalog().get("checks", []):
                if chk.get("output_file") == arquivo:
                    for k, v in ((chk.get("thresholds") or {}).get(chave) or {}).items():
                        val[k] = float(v)
        except Exception:
            pass
        _CACHE[ck] = val
    return _CACHE[ck]


def _item_valor(headers, rows) -> dict:
    """Converte a tabela ITEM/VALOR do topology.txt em dict."""
    ci_i = find_col(headers, "item")
    ci_v = find_col(headers, "valor", "value")
    if ci_i < 0 or ci_v < 0:
        return {}
    out = {}
    for r in rows:
        if ci_i < len(r):
            out[r[ci_i].strip().lower()] = (r[ci_v].strip() if ci_v < len(r) else "")
    return out


class TopologiaAnalyzer(Analyzer):
    kind = "topologia"
    title = "Topologia e cluster"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        if base in ("topology", "topologia") or "TOPOLOGIA DO AMBIENTE" in h:
            return 95
        if base == "rac_nodes" or "SESSOES POR INSTANCIA" in h \
                or "SERVICOS ATIVOS" in h or "INTERCONNECT" in h:
            return 95
        return 0

    # ── topology.txt ──────────────────────────────────────────
    def _topologia(self, tabelas) -> Section:
        info, instancias = {}, (None, [])
        for headers, rows in tabelas:
            if find_col(headers, "item") >= 0:
                info.update(_item_valor(headers, rows))
            elif find_col(headers, "inst_id") >= 0:
                instancias = (headers, rows)

        perfil = info.get("perfil", "")
        # Sem a tabela ITEM/VALOR (caminho Citrix, onde topology.txt traz apenas
        # gv$instance) nao ha como saber o tipo nem quantas instancias existem
        # configuradas. Nesse caso a analise fica informativa: contar linhas e
        # deduzir 'no caido' seria chute.
        kind = (info.get("tipo") or "").upper()
        no_ar = int(num(info.get("nos no ar", "")) or len(instancias[1]) or 1)
        cfg = num(info.get("nos configurados", "-"))
        cfg = int(cfg) if cfg else None
        if not kind:
            kind = "SINGLE" if no_ar <= 1 else "RAC"

        sec = Section(title="Topologia do ambiente",
                      table_headers=instancias[0] or ["ITEM", "VALOR"],
                      table_rows=instancias[1] or [])
        if perfil:
            sec.metrics.append(Metric("Perfil", perfil))
        sec.metrics.append(Metric("Instancias no ar", str(no_ar)))
        if cfg:
            sec.metrics.append(Metric("Instancias configuradas", str(cfg)))

        # No caido: so faz sentido em RAC real. Em RAC One Node uma unica
        # instancia ativa e o desenho do produto, nao uma falha.
        if kind == "RAC" and cfg and no_ar < cfg:
            faltando = cfg - no_ar
            sec.findings.append(Finding(
                title=f"{faltando} de {cfg} instancia(s) do cluster fora do ar",
                detail=f"gv$instance mostra {no_ar} instancia(s). Uma instancia "
                       f"fora nao aparece como linha 'DOWN' - ela simplesmente "
                       f"nao retorna, por isso a comparacao e contra "
                       f"CLUSTER_DATABASE_INSTANCES.",
                severity=Severity.CRIT, impact=f"{faltando} no(s)",
                recommendation=(
                    "Verificar no servidor: srvctl status database -d <db> e "
                    "crsctl stat res -t. Checar o alert log do no ausente e se "
                    "houve evicao (ORA-29740). Ate voltar, a capacidade do "
                    "cluster esta reduzida e nao ha protecao a falha do no que "
                    "sobrou."),
            ))
        elif kind == "CLUSTER_1NO":
            # Ambiguidade real: nao ha SQL que separe RAC One Node de RAC com no
            # caido. Emitir CRIT aqui acusaria falsamente todo RAC One Node;
            # ficar em silencio esconderia um cluster degradado. A saida honesta
            # e WARN dizendo exatamente qual comando resolve a duvida.
            sec.findings.append(Finding(
                title="Cluster com apenas 1 instancia no ar - tipo nao "
                      "determinavel por SQL",
                detail=(f"cluster_database=TRUE e {no_ar} instancia no ar"
                        + (f" de {cfg} configurada(s). " if cfg else ". ")
                        + "Isso e o normal em RAC One Node e um incidente em "
                          "RAC. O tipo e atributo do Grid Infrastructure e nao "
                          "aparece em v$/gv$."),
                severity=Severity.WARN,
                impact="sem redundancia de no no momento da coleta",
                recommendation=(
                    "Confirmar no servidor: srvctl config database -d <db> | "
                    "findstr /i type. Se 'Type: RACOneNode', esta correto. Se "
                    "'Type: RAC', ha no(s) caido(s) - ver crsctl stat res -t e "
                    "o alert log do no ausente."),
            ))
        elif kind == "RACONENODE" and no_ar > 1:
            sec.findings.append(Finding(
                title=f"RAC One Node com {no_ar} instancias no ar",
                detail="Esperado em relocacao online (online relocation), que "
                       "dura poucos minutos.",
                severity=Severity.INFO,
                recommendation="Confirmar com srvctl status database se a "
                               "relocacao terminou; se persistir por muito "
                               "tempo, investigar.",
            ))

        # Instancia no ar mas fora de OPEN (MOUNTED/NOMOUNT/STARTED)
        if instancias[0]:
            ci_st = find_col(instancias[0], "status")
            ci_nm = find_col(instancias[0], "instance_name")
            fora = [r for r in instancias[1]
                    if 0 <= ci_st < len(r) and r[ci_st].strip().upper() != "OPEN"]
            if fora:
                nomes = ", ".join(
                    f"{r[ci_nm] if 0 <= ci_nm < len(r) else '?'}"
                    f" ({r[ci_st].strip()})" for r in fora)
                sec.findings.append(Finding(
                    title=f"{len(fora)} instancia(s) no ar mas nao em OPEN",
                    detail=nomes, severity=Severity.CRIT,
                    recommendation="Instancia em MOUNTED/NOMOUNT nao atende "
                                   "aplicacao. Ver alert log e concluir o open.",
                ))

        papel = (info.get("papel") or "").upper()
        if papel and papel != "PRIMARY":
            sec.metrics.append(Metric("Papel", papel, severity=Severity.INFO))
        return sec

    # ── rac_nodes.txt ─────────────────────────────────────────
    def _sessoes_por_no(self, headers, rows) -> Section:
        """Skew de carga: um no concentrando sessoes indica balanceamento
        quebrado (servico preferencial, TNS sem LOAD_BALANCE, CLB/RLB)."""
        t = limiar("rac_nodes.txt", "skew_pct")
        warn, crit = t.get("warn", 60.0), t.get("crit", 80.0)
        ci_inst = find_col(headers, "inst_id", "inst")
        ci_ses = find_col(headers, "sessoes", "sessions", "total")

        pares = []
        for r in rows:
            if 0 <= ci_inst < len(r) and 0 <= ci_ses < len(r):
                pares.append((r[ci_inst].strip(), num(r[ci_ses]) or 0.0))
        total = sum(v for _, v in pares)

        sec = Section(title="Distribuicao de sessoes entre os nos",
                      table_headers=headers, table_rows=rows)
        sec.metrics.append(Metric("Sessoes totais", f"{total:.0f}"))
        # Com um unico no nao existe distribuicao a avaliar - 100% num no e o
        # esperado, e alarmar aqui seria falso positivo garantido.
        if len(pares) < 2 or total <= 0:
            sec.metrics.append(Metric("Nos com sessoes", str(len(pares))))
            return sec

        inst, maior = max(pares, key=lambda x: x[1])
        share = 100.0 * maior / total
        sev = (Severity.CRIT if share >= crit else
               Severity.WARN if share >= warn else Severity.OK)
        sec.metrics.append(Metric("Maior concentracao", f"{share:.0f}% (inst {inst})",
                                  severity=sev))
        if sev in (Severity.WARN, Severity.CRIT):
            sec.findings.append(Finding(
                title=f"Carga concentrada na instancia {inst}: {share:.0f}% das sessoes",
                detail="; ".join(f"inst {i}: {v:.0f}" for i, v in sorted(pares)),
                severity=sev, impact=f"{maior:.0f} de {total:.0f} sessoes",
                recommendation=(
                    "Cluster desbalanceado nao entrega a capacidade contratada e "
                    "o no sobrecarregado vira gargalo. Revisar: servico com "
                    "instancia preferencial unica (srvctl config service), TNS "
                    "sem LOAD_BALANCE=ON, e CLB_GOAL/RLB do servico."),
            ))
        return sec

    def _servicos(self, headers, rows) -> Section:
        ci_inst = find_col(headers, "inst_id", "inst")
        ci_srv = find_col(headers, "servico", "service", "name")
        sec = Section(title="Servicos ativos por instancia",
                      table_headers=headers, table_rows=rows[:30])
        srvs = {r[ci_srv].strip() for r in rows if 0 <= ci_srv < len(r)}
        sec.metrics.append(Metric("Servicos ativos", str(len(srvs))))
        # Servico rodando em uma unica instancia de um cluster com varios nos:
        # nao e erro (pode ser desenho), mas explica skew - registra como INFO.
        if ci_inst >= 0:
            por_srv: dict = {}
            for r in rows:
                if 0 <= ci_srv < len(r) and 0 <= ci_inst < len(r):
                    por_srv.setdefault(r[ci_srv].strip(), set()).add(r[ci_inst].strip())
            nos = {i for s in por_srv.values() for i in s}
            solo = sorted(s for s, i in por_srv.items() if len(i) == 1)
            if solo and len(nos) > 1:
                sec.metrics.append(Metric("Servicos em um so no", str(len(solo)),
                                          severity=Severity.INFO))
                sec.findings.append(Finding(
                    title=f"{len(solo)} servico(s) ativo(s) em uma unica instancia",
                    detail=", ".join(solo[:8]),
                    severity=Severity.INFO,
                    recommendation="Confirmar se e intencional (servico "
                                   "singleton) ou se o servico deveria estar "
                                   "em mais nos - costuma explicar o skew de "
                                   "sessoes.",
                ))
        return sec

    def _interconnect(self, headers, rows) -> Section:
        t = limiar("rac_nodes.txt", "blocks_lost")
        warn, crit = t.get("warn", 1.0), t.get("crit", 100.0)
        ci_nome = find_col(headers, "name", "nome")
        ci_val = find_col(headers, "value", "valor")
        ci_inst = find_col(headers, "inst_id", "inst")

        perdidos = 0.0
        det = []
        for r in rows:
            v = num(r[ci_val]) if 0 <= ci_val < len(r) else None
            nome = r[ci_nome].strip() if 0 <= ci_nome < len(r) else ""
            if v and v > 0:
                perdidos += v
                inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else "?"
                det.append(f"inst {inst} {nome}={v:.0f}")

        sec = Section(title="Interconnect (blocos perdidos)",
                      table_headers=headers, table_rows=rows[:20])
        sev = (Severity.CRIT if perdidos >= crit else
               Severity.WARN if perdidos >= warn else Severity.OK)
        sec.metrics.append(Metric("Blocos perdidos", f"{perdidos:.0f}", severity=sev))
        if sev in (Severity.WARN, Severity.CRIT):
            sec.findings.append(Finding(
                title=f"{perdidos:.0f} bloco(s) perdido(s) no interconnect",
                detail="; ".join(det[:6]), severity=sev,
                impact=f"{perdidos:.0f} blocos",
                recommendation=(
                    "Bloco perdido no interconnect e problema de REDE, nao de "
                    "banco: checar MTU/jumbo frames consistente em todos os "
                    "nos, erros e descartes na NIC privada, HAIP e saturacao "
                    "do switch. E o precursor tipico de evicao de no "
                    "(ORA-29740)."),
            ))
        return sec

    def analyze(self, files, meta):
        sections = []
        for path in files:
            texto = read_text(path)
            base = os.path.basename(path).lower()
            base = base[:-4] if base.endswith(".txt") else base
            tabelas = parse_fixed_width_tables(texto)

            if base in ("topology", "topologia") or "TOPOLOGIA" in texto.upper()[:200]:
                if tabelas:
                    sections.append(self._topologia(tabelas))
                continue

            # rac_nodes.txt tem 3 blocos; cada um vira uma secao propria.
            marcadores = _SUBT.findall(texto)
            for idx, (headers, rows) in enumerate(tabelas):
                if not (headers and rows):
                    continue
                marc = (marcadores[idx] if idx < len(marcadores) else "").upper()
                if "SESSOES POR INSTANCIA" in marc or find_col(headers, "sessoes") >= 0:
                    sections.append(self._sessoes_por_no(headers, rows))
                elif "SERVICOS" in marc or find_col(headers, "servico") >= 0:
                    sections.append(self._servicos(headers, rows))
                elif "INTERCONNECT" in marc or find_col(headers, "value") >= 0:
                    sections.append(self._interconnect(headers, rows))

        if not sections:
            return [], ""
        achados = [f for s in sections for f in s.findings]
        pior = worst([s.severity for s in sections])
        perfil = ""
        for s in sections:
            for m in s.metrics:
                if m.name == "Perfil":
                    perfil = m.value
        if achados:
            resumo = ((f"{perfil}. " if perfil else "")
                      + f"{len(achados)} achado(s) de topologia/cluster: "
                      + "; ".join(f.title for f in achados[:3]) + ".")
        else:
            resumo = ((f"{perfil}. " if perfil else "")
                      + "Topologia consistente e cluster sem alerta.")
        if pior == Severity.CRIT:
            resumo += " Ha item critico de disponibilidade do cluster."
        return sections, resumo
