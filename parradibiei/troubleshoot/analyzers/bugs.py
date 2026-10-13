"""
Analisador de BUGS E FALHAS DE MANUTENCAO.

Cobre o que costuma estar por tras de erro "aleatorio" e do que nao aparece em
nenhum grafico de performance:

- `patches.txt`     versao e idade do ultimo Release Update
- `dict_health.txt` componente/objeto de SYS invalido (sobra de upgrade)
- `rman_fail.txt`   tentativas de backup que FALHARAM

Por que a idade do patch importa num troubleshooting: diante de um ORA-00600, a
primeira pergunta da Oracle e a versao. Banco parado num RU antigo esta exposto
a bug ja corrigido - e boa parte dos "incidentes misteriosos" morre aqui.
"""
from __future__ import annotations

import os

from ..model import Finding, Metric, Section, Severity
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .saude import limiar

_ALVOS = {
    "patches": "VERSAO E PATCH",
    "dict_health": "INTEGRIDADE DO DICIONARIO",
    "rman_fail": "FALHAS DE BACKUP",
}


class BugsAnalyzer(Analyzer):
    kind = "bugs"
    title = "Bugs, patches e falhas de manutencao"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, marcador in _ALVOS.items():
            if base == chave or marcador in h:
                return 95
        return 0

    # ── versao e patch ────────────────────────────────────────
    def _patches(self, headers, rows) -> Section:
        ci_i = find_col(headers, "item")
        ci_v = find_col(headers, "valor")
        ci_q = find_col(headers, "quando")
        dados, quando = {}, {}
        for r in rows:
            if 0 <= ci_i < len(r) and 0 <= ci_v < len(r):
                chave = r[ci_i].strip().lower()
                dados[chave] = r[ci_v].strip()
                if 0 <= ci_q < len(r):
                    quando[chave] = r[ci_q].strip()

        sec = Section(title="Versao e nivel de patch",
                      table_headers=headers, table_rows=rows)
        versao = dados.get("versao", "?")
        sec.metrics.append(Metric("Versao", versao))
        if dados.get("ultimo_patch"):
            sec.metrics.append(Metric("Ultimo patch",
                                      quando.get("ultimo_patch", "-")))

        dias = num(dados.get("dias_desde_patch", ""))
        if dias is None:
            # Sem registro de patch: banco nunca teve RU aplicado desde a
            # criacao, ou o dicionario de patches nao existe (pre-12c).
            sec.findings.append(Finding(
                title="Sem registro de Release Update aplicado",
                detail=f"Versao {versao}. Nao ha linha em DBA_REGISTRY_SQLPATCH.",
                severity=Severity.WARN,
                impact="exposto a bugs e vulnerabilidades ja corrigidos",
                recommendation=(
                    "Confirme com opatch lsinventory no servidor. Se realmente "
                    "nunca recebeu RU, planeje a atualizacao: e o caminho mais "
                    "barato para eliminar uma classe inteira de erros."),
            ))
            return sec

        t = limiar("patches.txt", "dias_desde_patch")
        warn, crit = t.get("warn", 270.0), t.get("crit", 540.0)
        sev = (Severity.CRIT if dias >= crit else
               Severity.WARN if dias >= warn else Severity.OK)
        sec.metrics.append(Metric("Dias desde o ultimo patch", f"{dias:.0f}",
                                  severity=sev))
        if sev != Severity.OK:
            meses = dias / 30.0
            sec.findings.append(Finding(
                title=f"Ultimo Release Update ha {dias:.0f} dias "
                      f"({meses:.0f} meses)",
                detail=(f"Versao {versao}, patch de "
                        f"{quando.get('ultimo_patch', '?')}: "
                        f"{dados.get('ultimo_patch', '')}"),
                severity=sev,
                impact="bugs e CVEs ja corrigidos continuam expostos",
                recommendation=(
                    "Planeje a aplicacao do RU mais recente em janela. Com Data "
                    "Guard, aplique primeiro no standby e depois faca switchover "
                    "para reduzir a indisponibilidade. Antes de abrir SR por erro "
                    "interno, confirme se o RU atual ja corrige o bug."),
            ))
        return sec

    # ── dicionario ────────────────────────────────────────────
    def _dicionario(self, headers, rows) -> Section:
        ci_t = find_col(headers, "tipo")
        ci_o = find_col(headers, "objeto")
        ci_d = find_col(headers, "detalhe")
        comps, objs = [], []
        for r in rows:
            tipo = r[ci_t].strip().lower() if 0 <= ci_t < len(r) else ""
            obj = r[ci_o].strip() if 0 <= ci_o < len(r) else "?"
            det = r[ci_d].strip() if 0 <= ci_d < len(r) else ""
            if "componente" in tipo:
                comps.append((obj, det))
            elif "objeto" in tipo:
                objs.append((obj, det))

        sec = Section(title="Integridade do dicionario de dados",
                      table_headers=headers, table_rows=rows[:30])
        sec.metrics.append(Metric("Componentes invalidos", str(len(comps)),
                                  severity=(Severity.CRIT if comps
                                            else Severity.OK)))
        sec.metrics.append(Metric("Objetos SYS/SYSTEM invalidos", str(len(objs)),
                                  severity=(Severity.WARN if objs
                                            else Severity.OK)))
        if comps:
            sec.findings.append(Finding(
                title=f"{len(comps)} componente(s) do banco fora de VALID",
                detail="; ".join(f"{c} ({s})" for c, s in comps[:6]),
                severity=Severity.CRIT,
                impact="upgrade/patch incompleto - erro imprevisivel",
                recommendation=(
                    "Componente do DBA_REGISTRY invalido e upgrade nao "
                    "terminado: siga a nota da Oracle do componente especifico "
                    "e rode @?/rdbms/admin/utlrp.sql. Nao trate outros sintomas "
                    "antes disto - eles podem ser consequencia."),
            ))
        if objs:
            sec.findings.append(Finding(
                title=f"{len(objs)} objeto(s) de SYS/SYSTEM invalido(s)",
                detail="; ".join(f"{o} ({t})" for o, t in objs[:8]),
                severity=Severity.WARN,
                impact="pacote interno quebrado gera erro em cascata",
                recommendation=(
                    "Rode @?/rdbms/admin/utlrp.sql em janela para recompilar. "
                    "Se voltar a invalidar, o objeto depende de algo que "
                    "faltou no ultimo patch - abra SR com a lista."),
            ))
        return sec

    # ── backup falhando ───────────────────────────────────────
    def _rman(self, headers, rows) -> Section:
        ci_op = find_col(headers, "operation", "operacao")
        ci_in = find_col(headers, "inicio", "start")
        sec = Section(title="Falhas de backup (RMAN, 7 dias)",
                      table_headers=headers, table_rows=rows[:20])
        sec.metrics.append(Metric("Falhas em 7 dias", str(len(rows)),
                                  severity=Severity.CRIT if rows else Severity.OK))
        if rows:
            amostra = "; ".join(
                f"{(r[ci_op].strip() if 0 <= ci_op < len(r) else '?')} em "
                f"{(r[ci_in].strip() if 0 <= ci_in < len(r) else '?')}"
                for r in rows[:5])
            sec.findings.append(Finding(
                title=f"{len(rows)} execucao(oes) de backup FALHARAM em 7 dias",
                detail=amostra,
                severity=Severity.CRIT,
                impact="a janela de recuperacao pode nao existir",
                recommendation=(
                    "Cheque o erro em V$RMAN_OUTPUT e no log do job. Enquanto "
                    "houver falha, o RPO real e o do ultimo backup BEM "
                    "SUCEDIDO - confirme-o antes de assumir que esta protegido "
                    "(veja a idade do backup por datafile)."),
            ))
        return sec

    # ── analise ───────────────────────────────────────────────
    def analyze(self, files, meta):
        secoes = []
        for path in files:
            base = os.path.basename(path).lower()
            for headers, rows in parse_fixed_width_tables(read_text(path)):
                if not headers or not rows:
                    continue
                if "patches" in base:
                    secoes.append(self._patches(headers, rows))
                elif "dict_health" in base:
                    secoes.append(self._dicionario(headers, rows))
                elif "rman_fail" in base:
                    secoes.append(self._rman(headers, rows))

        if not secoes:
            return [], ""

        achados = [f for s in secoes for f in s.findings]
        criticos = [f for f in achados if f.severity == Severity.CRIT]
        if criticos:
            resumo = (f"{len(criticos)} falha(s) critica(s) de manutencao: "
                      + "; ".join(f.title for f in criticos[:3]) + ".")
        elif achados:
            resumo = (f"{len(achados)} ponto(s) de atencao em patch/dicionario/"
                      "backup.")
        else:
            resumo = ("Versao, dicionario e backups sem falha registrada.")
        return secoes, resumo
