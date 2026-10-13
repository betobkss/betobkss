"""
Analisador de saude e capacidade.

Cobre 7 checks que eram coletados e caiam no analisador generico: instancia,
FRA, redo, datafiles sem autoextend, objetos invalidos, UNDO e TEMP.

O generico so cria achado quando a tabela traz uma coluna STATUS/SEVERIDADE com
veredito - e nenhum destes traz (o STATUS de instance.txt vale 'OPEN', que nao
mapeia para severidade). Resultado: a evidencia virava tabela no relatorio sem
achado, severidade nem recomendacao. No FRA era pior que silencio: o catalogo
DECLARA thresholds (warn 80 / crit 90) que nenhum codigo lia.

Por que cada um importa:
- NOARCHIVELOG num PRIMARY: sem recovery point-in-time e sem backup online.
- FRA cheia derruba o banco (ORA-00257, archiver stuck).
- Redo trocando a cada minuto = pressao de checkpoint e 'log file switch'.
- Datafile com AUTOEXTEND OFF nao cresce: tablespace a 97% com autoextend
  desligado nao e "monitorar", e "vai estourar" (ORA-01653).
- PACKAGE BODY invalido quebra a aplicacao no proximo call.
- SNAP_TOO_OLD / NO_SPACE no UNDO sao evidencia direta de ORA-01555.
- TEMP: gv$sort_usage traz uma linha por segmento; sem agregar por sessao a
  tabela crua repete o mesmo SID e esconde quem esta consumindo.

Fora daqui de proposito: hitratio.txt e sgacomp.txt ja aplicam o limiar dentro
do proprio SQL (CASE WHEN ... 'WARNING'), e o generico mapeia essa coluna. Hit
ratio, alem disso, e metrica desacreditada - sobe com SQL ruim lendo mais.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from functools import partial
from typing import ClassVar

from ..model import Finding, Metric, Section, Severity, worst
from .base import (
    Analyzer,
    coletado_em,
    find_col,
    num,
    parse_fixed_width_tables,
    read_text,
)

# Defaults que espelham mcp/catalog.json. O catalogo e a fonte de verdade; isto
# e o fallback se ele nao abrir. Duplicar o numero aqui e proposital: sem isto o
# analisador silenciaria caso o catalogo faltasse.
PADROES = {
    ("fra.txt", "pct_used"): {"warn": 80.0, "crit": 90.0},
    ("redo.txt", "switches_per_hour"): {"warn": 12.0, "crit": 30.0},
    ("temp.txt", "sessao_temp_gb"): {"warn": 10.0, "crit": 50.0},
    ("instance.txt", "uptime_dias"): {"info": 7.0},
}

_SUBT = re.compile(r"^===\s*(.+?)\s*===\s*$", re.M)
_CACHE: dict = {}


def limiar(arquivo: str, chave: str) -> dict:
    """Limiares declarados no catalogo MCP para um check (cache em modulo).

    Ler daqui em vez de fixar no codigo evita o bug que a bateria expos: o
    catalogo declarava threshold do FRA que nenhum analisador consultava.
    """
    ck = (arquivo, chave)
    if ck not in _CACHE:
        val = dict(PADROES.get(ck, {}))
        try:
            from mcp.loader import load_catalog
            for chk in load_catalog().get("checks", []):
                if chk.get("output_file") == arquivo:
                    for k, v in ((chk.get("thresholds") or {}).get(chave)
                                 or {}).items():
                        val[k] = float(v)
                    break
        except Exception:
            pass
        _CACHE[ck] = val
    return _CACHE[ck]


def _fra_thresholds() -> tuple:
    t = limiar("fra.txt", "pct_used")
    return (t.get("warn", 80.0), t.get("crit", 90.0))


def _soma(rows, ci) -> float:
    if ci < 0:
        return 0.0
    return sum((num(r[ci]) or 0.0) for r in rows if ci < len(r))


class SaudeAnalyzer(Analyzer):
    kind = "saude"
    title = "Saude e capacidade"

    # arquivo -> marcador do subtitulo gerado pelo catalogo
    _ALVOS: ClassVar[dict] = {
        "fra": "FRA USAGE",
        "df_noautoext": "DATAFILES AUTOEXTEND OFF",
        "invalid": "OBJETOS INVALIDOS",
        "undo": "UNDO USAGE",
        "instance": "SAUDE DA INSTANCIA",
        "redo": "REDO LOG SWITCHES",
        "temp": "TEMP USAGE POR SESSAO",
    }

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, marcador in self._ALVOS.items():
            if base == chave or marcador in h:
                return 90
        return 0

    # ── FRA ───────────────────────────────────────────────────
    def _fra(self, headers, rows) -> Section:
        """No v$recovery_file_dest / v$flash_recovery_area_usage cada linha e um
        FILE_TYPE e o PCT_USED e percentual DO TOTAL da FRA: o que importa e a
        SOMA, nao a maior linha. Ler linha a linha esconderia uma FRA a 95%."""
        warn, crit = _fra_thresholds()
        ci_tipo = find_col(headers, "file_type", "tipo")
        ci_pct = find_col(headers, "pct_used", "percent_space_used", "pct")
        ci_rec = find_col(headers, "pct_reclaimable", "reclaim")

        total = _soma(rows, ci_pct)
        recl = _soma(rows, ci_rec)
        efetivo = max(0.0, total - recl)

        sev = (Severity.CRIT if total >= crit else
               Severity.WARN if total >= warn else Severity.OK)
        sec = Section(title="FRA (Fast Recovery Area)",
                      table_headers=headers, table_rows=rows[:20])
        sec.metrics.append(Metric("FRA usada", f"{total:.1f}%", severity=sev))
        sec.metrics.append(Metric("Reciclavel", f"{recl:.1f}%"))
        sec.metrics.append(Metric("Nao reciclavel", f"{efetivo:.1f}%"))

        if sev in (Severity.WARN, Severity.CRIT):
            maiores = sorted(
                ((r[ci_tipo] if 0 <= ci_tipo < len(r) else "?",
                  num(r[ci_pct]) or 0.0) for r in rows),
                key=lambda x: -x[1])[:3]
            det = "; ".join(f"{t}: {p:.1f}%" for t, p in maiores if p > 0)
            # Reciclavel alto = o proprio banco libera ao precisar; o risco real
            # e o percentual que NAO pode ser reciclado.
            if recl >= 20:
                rec = ("Boa parte e reciclavel: confirme que o backup do "
                       "archivelog esta rodando (o RMAN e que marca como "
                       "reciclavel). Se estiver, o banco libera sozinho.")
            else:
                rec = ("Pouco espaco reciclavel: faca BACKUP ARCHIVELOG e "
                       "DELETE INPUT, ou amplie DB_RECOVERY_FILE_DEST_SIZE. "
                       "FRA cheia para o banco com ORA-00257.")
            sec.findings.append(Finding(
                title=f"FRA em {total:.1f}% de uso",
                detail=det, severity=sev,
                impact=f"{efetivo:.1f}% nao reciclavel",
                recommendation=rec))
        return sec

    # ── Datafiles sem autoextend ──────────────────────────────
    def _autoextend(self, headers, rows) -> Section:
        ci_ts = find_col(headers, "tablespace")
        ci_sz = find_col(headers, "size_mb", "size", "mb")
        ci_ae = find_col(headers, "autoexten", "autoextend")

        off = [r for r in rows
               if not (0 <= ci_ae < len(r)) or r[ci_ae].strip().upper() != "YES"]
        sec = Section(title="Datafiles sem AUTOEXTEND",
                      table_headers=headers, table_rows=rows[:25])
        sec.metrics.append(Metric("Datafiles fixos", str(len(off)),
                                  severity=Severity.WARN if off else Severity.OK))
        if not off:
            return sec

        ts = sorted({r[ci_ts].strip() for r in off if 0 <= ci_ts < len(r)})
        # Ordena pelos menores: um datafile fixo de 70 MB estoura muito antes
        # de um de 37 GB, e e nele que o incidente comeca.
        pequenos = sorted(
            ((r[ci_ts] if 0 <= ci_ts < len(r) else "?", num(r[ci_sz]) or 0.0)
             for r in off), key=lambda x: x[1])[:5]
        det = "Menores: " + "; ".join(f"{t} ({p:.0f} MB)" for t, p in pequenos)
        sec.findings.append(Finding(
            title=f"{len(off)} datafile(s) com AUTOEXTEND OFF em "
                  f"{len(ts)} tablespace(s)",
            detail=det, severity=Severity.WARN,
            impact=", ".join(ts[:6]) + ("..." if len(ts) > 6 else ""),
            recommendation=(
                "Cruze com a secao de Tablespaces: se algum destes estiver perto "
                "de 100%, ele NAO vai crescer sozinho e o erro sera "
                "ORA-01653/ORA-01654. Ligue o autoextend com MAXSIZE definido "
                "(ALTER DATABASE DATAFILE ... AUTOEXTEND ON NEXT ... MAXSIZE ...) "
                "ou adicione datafile."),
        ))
        return sec

    # ── Objetos invalidos ─────────────────────────────────────
    def _invalid(self, headers, rows) -> Section:
        ci_own = find_col(headers, "owner")
        ci_tipo = find_col(headers, "object_type", "tipo")
        ci_qtd = find_col(headers, "qtd", "count", "total")

        total = int(_soma(rows, ci_qtd)) if ci_qtd >= 0 else len(rows)
        sec = Section(title="Objetos invalidos",
                      table_headers=headers, table_rows=rows[:25])
        sec.metrics.append(Metric("Objetos invalidos", str(total),
                                  severity=Severity.WARN if total else Severity.OK))
        if not total:
            return sec

        # PACKAGE BODY / FUNCTION / PROCEDURE invalidos quebram no proximo call;
        # VIEW/SYNONYM invalidos costumam ser residuo. A acao e diferente.
        exec_tipos = [r for r in rows
                      if 0 <= ci_tipo < len(r)
                      and re.search(r"PACKAGE|FUNCTION|PROCEDURE|TRIGGER",
                                    r[ci_tipo], re.I)]
        n_exec = int(_soma(exec_tipos, ci_qtd)) if ci_qtd >= 0 else len(exec_tipos)
        donos = sorted({r[ci_own].strip() for r in rows if 0 <= ci_own < len(r)})
        sev = Severity.WARN if n_exec else Severity.INFO
        sec.findings.append(Finding(
            title=f"{total} objeto(s) invalido(s) em {len(donos)} schema(s)",
            detail=(f"{n_exec} sao codigo executavel (package/function/"
                    f"procedure/trigger). Schemas: " + ", ".join(donos[:8])),
            severity=sev, impact=f"{n_exec} objeto(s) executavel(is)",
            recommendation=(
                "Recompile e reveja o que sobrar: "
                "EXEC DBMS_UTILITY.COMPILE_SCHEMA('<owner>', FALSE); "
                "ou UTL_RECOMP.RECOMP_PARALLEL. O que nao compilar tem erro "
                "real - veja DBA_ERRORS. Invalido em package body quebra a "
                "aplicacao no proximo call."),
        ))
        return sec

    # ── UNDO ──────────────────────────────────────────────────
    def _undo(self, headers, rows) -> Section:
        ci_sto = find_col(headers, "snap_too_old", "snapshot_too_old", "ssolderrcnt")
        ci_nos = find_col(headers, "no_space", "nospaceerrcnt")
        ci_mql = find_col(headers, "maxquerylen")
        ci_ret = find_col(headers, "tuned_undoretention", "undoretention")

        sto = int(_soma(rows, ci_sto))
        nos = int(_soma(rows, ci_nos))
        maxq = max([(num(r[ci_mql]) or 0.0) for r in rows
                    if 0 <= ci_mql < len(r)] or [0.0])
        ret = max([(num(r[ci_ret]) or 0.0) for r in rows
                   if 0 <= ci_ret < len(r)] or [0.0])

        sec = Section(title="UNDO (retencao e erros)",
                      table_headers=headers, table_rows=rows[:20])
        sec.metrics.append(Metric("ORA-01555 (snap too old)", str(sto),
                                  severity=Severity.WARN if sto else Severity.OK))
        sec.metrics.append(Metric("Falta de espaco no UNDO", str(nos),
                                  severity=Severity.CRIT if nos else Severity.OK))
        if maxq:
            sec.metrics.append(Metric("Query mais longa", f"{maxq:.0f}s"))
        if ret:
            sec.metrics.append(Metric("Retencao ajustada", f"{ret:.0f}s"))

        if nos:
            sec.findings.append(Finding(
                title=f"UNDO ficou sem espaco {nos} vez(es) na janela",
                severity=Severity.CRIT, impact=f"{nos} ocorrencia(s)",
                recommendation=(
                    "Transacao falha por falta de UNDO. Amplie o tablespace de "
                    "UNDO (ou ligue autoextend nele) e investigue transacao "
                    "longa sem commit."),
            ))
        if sto:
            sec.findings.append(Finding(
                title=f"ORA-01555 (snapshot too old) {sto} vez(es) na janela",
                detail=(f"Query mais longa {maxq:.0f}s contra retencao de "
                        f"{ret:.0f}s." if maxq and ret else ""),
                severity=Severity.WARN, impact=f"{sto} ocorrencia(s)",
                recommendation=(
                    "Aumente UNDO_RETENTION e garanta UNDO grande o bastante "
                    "(com RETENTION GUARANTEE se o relatorio longo for "
                    "critico), ou reduza a duracao da query."),
            ))
        elif maxq and ret and maxq > ret:
            # Ainda nao falhou, mas a janela nao cobre a query mais longa.
            sec.findings.append(Finding(
                title="Query mais longa excede a retencao de UNDO",
                detail=f"{maxq:.0f}s de query contra {ret:.0f}s de retencao.",
                severity=Severity.WARN,
                impact=f"folga negativa de {maxq - ret:.0f}s",
                recommendation=(
                    "Risco de ORA-01555 antes de haver erro registrado: "
                    "eleve UNDO_RETENTION ou o tamanho do tablespace de UNDO."),
            ))
        return sec

    # ── Instancia: log mode, status e uptime ──────────────────
    def _instance(self, headers, rows, ref: datetime | None = None) -> Section:
        """NOARCHIVELOG num PRIMARY e o achado que mais custa caro aqui: sem
        archive nao existe recovery point-in-time nem backup online. A tabela
        traz a coluna STATUS ('OPEN'), que o generico nao sabe mapear - por isso
        o LOG_MODE passava batido."""
        ci_nome = find_col(headers, "instance_name")
        ci_st = find_col(headers, "status")
        ci_lm = find_col(headers, "log_mode")
        ci_rl = find_col(headers, "database_role")
        ci_up = find_col(headers, "startup")
        ci_ver = find_col(headers, "version")

        def cel(r, ci):
            return r[ci].strip() if 0 <= ci < len(r) else ""

        sec = Section(title="Saude da instancia",
                      table_headers=headers, table_rows=rows[:20])
        papel = cel(rows[0], ci_rl) if rows else ""
        modo = cel(rows[0], ci_lm).upper() if rows else ""
        if ci_ver >= 0 and rows:
            sec.metrics.append(Metric("Versao", cel(rows[0], ci_ver)))
        if papel:
            sec.metrics.append(Metric("Papel", papel))
        if modo:
            sec.metrics.append(Metric(
                "Log mode", modo,
                severity=(Severity.WARN if "NOARCHIVELOG" in modo
                          else Severity.OK)))
        if len(rows) > 1:
            sec.metrics.append(Metric("Instancias (RAC)", str(len(rows))))

        # Instancia que nao esta OPEN e indisponibilidade, nao configuracao.
        fora = [cel(r, ci_nome) or "?" for r in rows
                if ci_st >= 0 and cel(r, ci_st).upper() not in ("OPEN", "")]
        if fora:
            sec.findings.append(Finding(
                title=f"{len(fora)} instancia(s) nao estao OPEN",
                detail="Instancia(s): " + ", ".join(fora),
                severity=Severity.CRIT, impact=", ".join(fora),
                recommendation=("Verifique o alert log e o CRS "
                                "(srvctl status database -d <db>)."),
            ))

        if "NOARCHIVELOG" in modo:
            prim = "PRIMARY" in papel.upper()
            sec.findings.append(Finding(
                title=f"Banco em NOARCHIVELOG{' (PRIMARY)' if prim else ''}",
                detail=("Sem archive nao ha recovery point-in-time, backup "
                        "online (RMAN quente) nem Data Guard. So restaura no "
                        "ultimo backup consistente, perdendo tudo desde ele."),
                severity=Severity.WARN if prim else Severity.INFO,
                impact="sem PITR e sem backup online",
                recommendation=(
                    "Aceitavel em ambiente descartavel. Se este banco tem dado "
                    "que nao pode ser perdido, ligue o archive "
                    "(ALTER DATABASE ARCHIVELOG) em janela e configure o "
                    "destino + expurgo antes, senao a FRA enche e para o banco."),
            ))

        # Uptime: restart recente e contexto obrigatorio para 'a carga mudou'.
        # Medido contra o momento da COLETA (`ref`), nao contra agora: o uptime
        # e um fato daquele instante. Com `now()` o titulo do achado crescia
        # sozinho a cada dia e a bateria de regressao do corpus acusava dezenas
        # de "regressoes" que eram so o calendario virando.
        dias = None
        if ci_up >= 0 and rows:
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", cel(rows[0], ci_up))
            if m:
                try:
                    d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    dias = ((ref or datetime.now()) - d).days
                except ValueError:
                    dias = None
        if dias is not None:
            sec.metrics.append(Metric("Uptime", f"{dias} dia(s)"))
            lim = limiar("instance.txt", "uptime_dias").get("info", 7.0)
            if dias <= lim:
                sec.findings.append(Finding(
                    title=f"Instancia reiniciada ha {dias} dia(s)",
                    detail=("Metricas de AWR/ASH anteriores ao restart nao "
                            "estao mais em v$; comparacoes de carga podem "
                            "refletir o restart, nao a aplicacao."),
                    severity=Severity.INFO, impact=f"uptime de {dias} dia(s)",
                    recommendation=("Confirme se o restart foi planejado "
                                    "(alert log). Se nao, procure a causa."),
                ))
        return sec

    # ── Redo: frequencia de switch ────────────────────────────
    def _redo(self, headers, rows) -> Section:
        """Boa pratica: switch a cada 15-20 min (~3-4/hora). O que importa e o
        PICO horario, nao a media do dia: uma hora com 62 switches (1 por
        minuto) e pressao de checkpoint mesmo que a media diaria pareca sadia."""
        t = limiar("redo.txt", "switches_per_hour")
        warn, crit = t.get("warn", 12.0), t.get("crit", 30.0)
        ci_sw = find_col(headers, "switches")
        ci_h = find_col(headers, "hora", "hour")
        if ci_sw < 0:
            return Section(title="Redo log switches",
                           table_headers=headers, table_rows=rows[:24])

        pares = [((r[ci_h].strip() if 0 <= ci_h < len(r) else "?"),
                  num(r[ci_sw]) or 0.0) for r in rows]
        pares = [(h, v) for h, v in pares if v > 0]
        sec = Section(title="Redo log switches (24h)",
                      table_headers=headers, table_rows=rows[:24])
        if not pares:
            return sec
        pico_h, pico = max(pares, key=lambda x: x[1])
        media = sum(v for _, v in pares) / len(pares)
        sev = (Severity.CRIT if pico >= crit else
               Severity.WARN if pico >= warn else Severity.OK)
        sec.metrics.append(Metric("Pico por hora", f"{pico:.0f}", severity=sev))
        sec.metrics.append(Metric("Media por hora", f"{media:.1f}"))
        sec.metrics.append(Metric("Horas observadas", str(len(pares))))

        if sev in (Severity.WARN, Severity.CRIT):
            acima = [h for h, v in pares if v >= warn]
            intervalo = 3600.0 / pico if pico else 0
            sec.findings.append(Finding(
                title=f"{pico:.0f} trocas de redo em 1 hora (pico em {pico_h})",
                detail=(f"Um switch a cada ~{intervalo:.0f}s no pico, contra a "
                        f"referencia de 15-20 min. {len(acima)} hora(s) acima "
                        f"de {warn:.0f}; media de {media:.1f}/h."),
                severity=sev, impact=f"pico de {pico:.0f}/h",
                recommendation=(
                    "Redo log pequeno para a carga: aumente o TAMANHO dos "
                    "grupos (nao a quantidade) para cair em ~4-6 switches/h. "
                    "Switch excessivo gera 'log file switch (checkpoint "
                    "incomplete)' e trava DML. Confira tambem se houve carga "
                    "em massa na janela do pico."),
            ))
        return sec

    # ── TEMP por sessao ───────────────────────────────────────
    def _temp(self, headers, rows) -> Section:
        """gv$sort_usage devolve UMA LINHA POR SEGMENTO de sort: a mesma sessao
        aparece repetida e a tabela crua parece defeito. Agregar por sessao e o
        que mostra o consumo real."""
        t = limiar("temp.txt", "sessao_temp_gb")
        warn_gb, crit_gb = t.get("warn", 10.0), t.get("crit", 50.0)
        ci_sid = find_col(headers, "sid")
        ci_usr = find_col(headers, "username", "usuario")
        ci_mb = find_col(headers, "temp_mb", "mb")
        ci_sql = find_col(headers, "sql_id")

        sec = Section(title="TEMP por sessao",
                      table_headers=headers, table_rows=rows[:25])
        if ci_mb < 0:
            return sec

        agg: dict = {}
        for r in rows:
            sid = r[ci_sid].strip() if 0 <= ci_sid < len(r) else "?"
            usr = r[ci_usr].strip() if 0 <= ci_usr < len(r) else "?"
            sql = r[ci_sql].strip() if 0 <= ci_sql < len(r) else ""
            mb = num(r[ci_mb]) or 0.0
            k = (sid, usr)
            a = agg.setdefault(k, {"mb": 0.0, "seg": 0, "sql": sql})
            a["mb"] += mb
            a["seg"] += 1
            if sql and not a["sql"]:
                a["sql"] = sql

        if not agg:
            return sec
        total_gb = sum(a["mb"] for a in agg.values()) / 1024.0
        (sid, usr), top = max(agg.items(), key=lambda kv: kv[1]["mb"])
        top_gb = top["mb"] / 1024.0
        sev = (Severity.CRIT if top_gb >= crit_gb else
               Severity.WARN if top_gb >= warn_gb else Severity.OK)

        sec.metrics.append(Metric("Sessoes com TEMP", str(len(agg))))
        sec.metrics.append(Metric("TEMP total em uso", f"{total_gb:.1f} GB"))
        sec.metrics.append(Metric("Maior sessao", f"{top_gb:.1f} GB",
                                  severity=sev))
        if sev in (Severity.WARN, Severity.CRIT):
            share = (top_gb / total_gb * 100.0) if total_gb else 0.0
            sec.findings.append(Finding(
                title=f"Sessao {sid} ({usr}) consumindo {top_gb:.1f} GB de TEMP",
                detail=(f"{top['seg']} segmento(s) de sort; {share:.0f}% do TEMP "
                        f"em uso no momento da coleta."
                        + (f" SQL_ID {top['sql']}." if top["sql"] else "")),
                severity=sev, impact=f"{top_gb:.1f} GB",
                recommendation=(
                    "Ache o SQL que derrama (V$SQL_WORKAREA_ACTIVE): quase "
                    "sempre e sort/hash sem indice ou PGA insuficiente. Tune o "
                    "SQL antes de aumentar a TEMP - TEMP cheia derruba a "
                    "sessao com ORA-01652."
                    + (f" Detalhe: .\\detalhar.bat {top['sql']}"
                       if top["sql"] else "")),
            ))
        return sec

    def analyze(self, files, meta):
        sections = []
        for path in files:
            texto = read_text(path)
            base = os.path.basename(path).lower()
            base = base[:-4] if base.endswith(".txt") else base
            marcadores = " ".join(_SUBT.findall(texto)).upper()
            tabelas = parse_fixed_width_tables(texto)

            if base == "fra" or "FRA USAGE" in marcadores:
                fn = self._fra
            elif base == "df_noautoext" or "AUTOEXTEND OFF" in marcadores:
                fn = self._autoextend
            elif base == "invalid" or "OBJETOS INVALIDOS" in marcadores:
                fn = self._invalid
            elif base == "undo" or "UNDO USAGE" in marcadores:
                fn = self._undo
            elif base == "instance" or "SAUDE DA INSTANCIA" in marcadores:
                # partial em vez de closure: prende o path DESTA volta do laco
                # sem o risco de a funcao ler o ultimo arquivo da lista.
                fn = partial(self._instance, ref=coletado_em(path))
            elif base == "redo" or "REDO LOG SWITCHES" in marcadores:
                fn = self._redo
            elif base == "temp" or "TEMP USAGE POR SESSAO" in marcadores:
                fn = self._temp
            else:
                continue

            if not tabelas:
                # Cabecalho sem linhas: o check rodou e nada foi encontrado.
                titulo = (_SUBT.findall(texto) or [base])[0]
                s = Section(title=titulo,
                            note="Sem ocorrencias: o check nao retornou linhas.")
                s.metrics.append(Metric("Ocorrencias", "0", severity=Severity.OK))
                sections.append(s)
                continue
            for headers, rows in tabelas:
                if headers and rows:
                    sections.append(fn(headers, rows))
                    break

        if not sections:
            return [], ""
        achados = [f for s in sections for f in s.findings]
        pior = worst([s.severity for s in sections])
        if achados:
            resumo = (f"{len(achados)} achado(s) de saude/capacidade: "
                      + "; ".join(f.title for f in achados[:4]) + ".")
        else:
            resumo = ("Instancia, FRA, redo, autoextend, objetos invalidos, "
                      "UNDO e TEMP sem alerta.")
        if pior == Severity.CRIT:
            resumo += " Ha item critico de disponibilidade."
        return sections, resumo
