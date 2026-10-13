"""
Analisador de CONFORMIDADE (best practices Oracle, estilo ORAchk).

Compara a configuracao real com o recomendado pela Oracle:

- `bp_params.txt`     parametros de instancia x recomendado
- `bp_database.txt`   archivelog, force logging, BCT, autobackup do controlfile
- `bp_multiplex.txt`  controlfile e redo com um unico membro
- `bp_instalacao.txt` o que ficou como veio da instalacao: pfile, compatible,
                      DST pendente, parametros underscore, autotask e dbms_stats
- `bp_ha.txt`         recuperabilidade e HA: transporte, flashback, undo/temp
- `bp_seguranca.txt`  senha default, profile sem limite, grant a PUBLIC, auditoria
- `bp_estrutura.txt`  objeto de usuario em SYSTEM, tablespace antiga, sequence
                      sem cache

Cada arquivo `bp_<familia>.txt` casa com a secao de mesmo nome em
`mcp/best_practices.json` - acrescentar familia e acrescentar dado, nao codigo.

Diferenca em relacao aos outros analisadores: aqui nao ha sintoma. O banco pode
estar rapido e saudavel HOJE com `force_logging` desligado - o preco vem no
failover. Por isso as regras vivem em `mcp/best_practices.json` (dado, nao
codigo) e cada desvio sai com **porque** e o comando de correcao.

Duas cautelas deliberadas:
1. Regra com `somente_se` so vale se o fato coletado confirmar o contexto
   (ex.: FORCE LOGGING so e cobrado quando existe standby configurado).
2. Nada aqui e executado: parametro com `SCOPE=SPFILE` exige restart e isso e
   dito no texto, para o desvio nao virar incidente na madrugada.
"""
from __future__ import annotations

import json
import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHO_REGRAS = os.path.join(BASE_DIR, "mcp", "best_practices.json")

# arquivo coletado -> (secao em best_practices.json, marcador no cabecalho).
# `bp_multiplex` tem tratamento proprio (o pior grupo de redo, nao par direto).
_ALVOS = {
    "bp_params": ("parametros", "PARAMETROS X RECOMENDADO"),
    "bp_database": ("banco", "CONFIGURACAO X RECOMENDADO"),
    "bp_multiplex": ("multiplexacao", "MULTIPLEXACAO"),
    "bp_instalacao": ("instalacao", "CONFIGURACAO DE INSTALACAO"),
    "bp_ha": ("ha", "RECUPERABILIDADE E HA"),
    "bp_seguranca": ("seguranca", "SEGURANCA E CONFORMIDADE"),
    "bp_estrutura": ("estrutura", "ESTRUTURA E CONTENCAO"),
}

# "TO RECOVERY WINDOW OF 7 DAYS" -> 7
_RETENCAO = re.compile(r"RECOVERY\s+WINDOW\s+OF\s+(\d+)\s+DAYS?", re.I)

# Sentinelas que a coleta grava quando o dicionario da versao nao responde.
_NAO_AVALIAVEL = {"DESCONHECIDO", "N/A"}

_CACHE: dict | None = None


def regras(caminho: str = "") -> dict:
    """Regras de best practice. Ausencia do arquivo nao pode derrubar a
    analise do caso - vira simplesmente 'sem regras a aplicar'."""
    global _CACHE
    if _CACHE is None or caminho:
        try:
            with open(caminho or CAMINHO_REGRAS, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            dados = {}
        if caminho:
            return dados
        _CACHE = dados
    return _CACHE


def _conforme(valor: str, regra: dict) -> bool:
    """Verdadeiro se o valor atual atende a regra.

    Suporta quatro formas: lista de valores aceitos, minimo e maximo numericos
    e lista de valores proibidos. Valor desconhecido/nao numerico e tratado como
    conforme para nao gerar alarme falso em ambiente atipico - varias familias
    contam ocorrencias e a contagem pode nem existir na versao do banco.
    """
    v = (valor or "").strip()
    # A coleta marca assim o que a versao do banco nao sabe responder (view que
    # so existe no 12c+, linha ausente no dicionario). Cobrar aqui seria acusar
    # desvio a partir de ausencia de evidencia. '(nulo)' NAO entra: parametro
    # vazio e um valor real, e ha regra que proibe justamente isso.
    if v.upper() in _NAO_AVALIAVEL:
        return True
    if "esperado" in regra:
        aceitos = [str(x).upper() for x in regra["esperado"]]
        return v.upper() in aceitos
    if "proibido" in regra:
        return v.upper() not in [str(x).upper() for x in regra["proibido"]]
    if "minimo" in regra:
        n = num(v)
        return True if n is None else n >= float(regra["minimo"])
    if "maximo" in regra:
        n = num(v)
        return True if n is None else n <= float(regra["maximo"])
    return True


class ConformidadeAnalyzer(Analyzer):
    kind = "conformidade"
    title = "Conformidade com as praticas Oracle"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, (_secao, marcador) in _ALVOS.items():
            if base == chave or marcador in h:
                return 95
        return 0

    # ── leitura das evidencias ────────────────────────────────
    def _ler_params(self, headers, rows) -> dict:
        ci_n = find_col(headers, "name", "nome")
        ci_v = find_col(headers, "valor", "value")
        out = {}
        for r in rows:
            if 0 <= ci_n < len(r) and 0 <= ci_v < len(r):
                out[r[ci_n].strip().lower()] = r[ci_v].strip()
        return out

    def _ler_itens(self, headers, rows) -> dict:
        """bp_database.txt e bp_multiplex.txt: pares item/valor."""
        ci_i = find_col(headers, "item")
        ci_v = find_col(headers, "valor", "qtd", "value")
        out = {}
        for r in rows:
            if 0 <= ci_i < len(r) and 0 <= ci_v < len(r):
                out[r[ci_i].strip().lower()] = r[ci_v].strip()
        return out

    # ── avaliacao ─────────────────────────────────────────────
    def _retencao_rman(self, fatos: dict):
        """Janela de retencao do RMAN, quando declarada na evidencia."""
        m = _RETENCAO.search(fatos.get("rman_retention_policy", "") or "")
        return int(m.group(1)) if m else None

    def _ajustar(self, regra: dict, fatos: dict) -> dict:
        """Especializa a regra com o que o proprio banco declara.

        Cobrar `control_file_record_keep_time >= 30` num banco cujo RMAN retem
        7 dias e discutir com o DBA sem razao: o que importa e que o controlfile
        guarde o registro por MAIS tempo do que a retencao do backup.
        """
        if not regra.get("comparar_com_retencao_rman"):
            return regra
        dias = self._retencao_rman(fatos)
        if dias is None:
            return regra
        ajustada = dict(regra)
        ajustada["minimo"] = dias + 1
        ajustada["porque"] = (
            f"A retencao do RMAN e de {dias} dia(s), mas o controlfile guarda o "
            f"registro do backup por menos tempo: o RMAN passa a 'esquecer' "
            f"backup que ainda existe em midia.")
        ajustada["acao"] = (
            f"ALTER SYSTEM SET control_file_record_keep_time={dias + 1} "
            f"SCOPE=BOTH; (retencao do RMAN + 1)")
        return ajustada

    def _aplicavel(self, regra: dict, fatos: dict) -> bool:
        cond = regra.get("somente_se")
        if not cond:
            return True
        atual = (fatos.get(str(cond.get("item", "")).lower(), "") or "").upper()
        return atual == str(cond.get("valor", "")).upper()

    def _avaliar(self, atuais: dict, catalogo: dict, fatos: dict) -> list:
        """Retorna [(item, atual, esperado, ok, regra)] apenas do que existe na
        evidencia: cobrar parametro que o banco nem tem gera ruido."""
        res = []
        for item, regra in catalogo.items():
            if item not in atuais or not self._aplicavel(regra, fatos):
                continue
            regra = self._ajustar(regra, fatos)
            atual = atuais[item]
            res.append((item, atual, self._esperado_txt(regra),
                        _conforme(atual, regra), regra))
        return res

    def _esperado_txt(self, regra: dict) -> str:
        if "esperado" in regra:
            return " ou ".join(str(x) for x in regra["esperado"])
        if "proibido" in regra:
            return "diferente de " + "/".join(str(x) for x in regra["proibido"])
        if "minimo" in regra:
            return f">= {regra['minimo']}"
        if "maximo" in regra:
            return f"<= {regra['maximo']}"
        return "-"

    def _multiplex(self, itens: dict, cat: dict, fatos: dict) -> list:
        """Multiplexacao nao e par item/valor direto: cada grupo de redo e uma
        linha propria, e o que importa e o PIOR grupo."""
        res = []
        if "controlfiles" in itens and "controlfiles" in cat:
            regra = cat["controlfiles"]
            res.append(("controlfiles", itens["controlfiles"],
                        self._esperado_txt(regra),
                        _conforme(itens["controlfiles"], regra), regra))
        grupos = {k: v for k, v in itens.items() if k.startswith("redo_grupo_")}
        if grupos and "redo_membros" in cat:
            regra = cat["redo_membros"]
            pior_n, pior_k = None, ""
            for k, v in grupos.items():
                n = num(v)
                if n is not None and (pior_n is None or n < pior_n):
                    pior_n, pior_k = n, k
            if pior_n is not None:
                res.append((f"membros por grupo de redo (pior: {pior_k})",
                            f"{pior_n:.0f}", self._esperado_txt(regra),
                            pior_n >= float(regra["minimo"]), regra))
        if "standby_redo" in itens and "standby_redo" in cat:
            regra = cat["standby_redo"]
            if self._aplicavel(regra, fatos):
                res.append(("standby_redo", itens["standby_redo"],
                            self._esperado_txt(regra),
                            _conforme(itens["standby_redo"], regra), regra))
        return res

    # ── analise ───────────────────────────────────────────────
    def analyze(self, files, meta):
        cat = regras()
        # secao do catalogo -> itens lidos da evidencia correspondente
        lidos: dict = {}

        for path in files:
            base = os.path.basename(path).lower()
            alvo = next((s for chave, (s, _m) in _ALVOS.items() if chave in base),
                        "")
            if not alvo:
                continue
            texto = read_text(path)
            for headers, rows in parse_fixed_width_tables(texto):
                if not headers or not rows:
                    continue
                itens = (self._ler_params(headers, rows) if alvo == "parametros"
                         else self._ler_itens(headers, rows))
                lidos.setdefault(alvo, {}).update(itens)

        itens_db = lidos.get("banco", {})
        itens_mx = lidos.get("multiplexacao", {})

        # Os fatos condicionam as regras (standby, papel, flashback) e valem
        # entre familias: `standby_file_management` so e cobrado se o
        # `standby_configurado` que veio no bp_database disser que ha standby.
        fatos: dict = {}
        for alvo, itens in lidos.items():
            if alvo != "parametros":
                fatos.update(itens)

        avaliados = []
        for alvo, itens in lidos.items():
            if alvo == "multiplexacao":
                continue
            avaliados += self._avaliar(itens, cat.get(alvo, {}), fatos)
        avaliados += self._multiplex(itens_mx, cat.get("multiplexacao", {}), fatos)

        # Autobackup do controlfile e ausencia de configuracao: o RMAN so
        # registra o que foi alterado, entao "nao ha linha" significa OFF.
        if itens_db and "rman_controlfile_autobackup" not in itens_db:
            regra = (cat.get("banco", {}) or {}).get("rman_controlfile_autobackup")
            if regra:
                avaliados.append(("rman_controlfile_autobackup", "OFF (padrao)",
                                  self._esperado_txt(regra), False, regra))

        if not avaliados:
            return [], ""

        ordem = {Severity.CRIT: 0, Severity.WARN: 1, Severity.INFO: 2}
        desvios = [a for a in avaliados if not a[3]]
        # Desempate pelo nome do item: com sete familias, ordenar so por
        # severidade deixava o corte dos findings a merce da ordem de leitura
        # dos arquivos - o mesmo caso trocava de achados entre execucoes.
        desvios.sort(key=lambda a: (ordem.get(a[4].get("severidade", "info"), 3),
                                    a[0]))

        headers = ["Item", "Atual", "Recomendado", "Status"]
        rows, row_sev = [], []
        for item, atual, esperado, ok, regra in sorted(
                avaliados, key=lambda a: (a[3], ordem.get(
                    a[4].get("severidade", "info"), 3), a[0])):
            sev = Severity.OK if ok else regra.get("severidade", Severity.INFO)
            rows.append([item, atual, esperado, "OK" if ok else "DESVIO"])
            row_sev.append(sev)

        sec = Section(title="Conformidade com as praticas Oracle",
                      table_headers=headers, table_rows=rows,
                      row_severity=row_sev)

        total = len(avaliados)
        conformes = total - len(desvios)
        pct = round(100.0 * conformes / total) if total else 100
        n_crit = sum(1 for d in desvios if d[4].get("severidade") == Severity.CRIT)
        sec.metrics.append(Metric("Conformidade", str(pct), unit="%",
                                  severity=(Severity.CRIT if pct < 60 else
                                            Severity.WARN if pct < 85 else
                                            Severity.OK)))
        sec.metrics.append(Metric("Itens avaliados", str(total)))
        sec.metrics.append(Metric("Desvios", str(len(desvios)),
                                  severity=(Severity.CRIT if n_crit else
                                            Severity.WARN if desvios else
                                            Severity.OK)))

        # A tabela lista todos os desvios; o detalhamento fica nos primeiros.
        # Sem teto, um banco nunca revisado geraria dezenas de achados e
        # afogaria o motivo pelo qual o caso foi aberto.
        for item, atual, esperado, _ok, regra in desvios[:15]:
            ref = regra.get("referencia", "")
            sec.findings.append(Finding(
                title=f"{item} = {atual} (recomendado: {esperado})",
                detail=regra.get("porque", ""),
                severity=regra.get("severidade", Severity.INFO),
                impact="desvio de best practice Oracle",
                recommendation=(regra.get("acao", "")
                                + (f" Referencia: {ref}." if ref else "")),
            ))

        sec.note = ("Comparacao com o recomendado pela Oracle (estilo ORAchk). "
                    "Desvio aqui nao significa incidente agora: significa risco "
                    "aceito. Confirme cada mudanca na sua realidade antes de "
                    "aplicar - parametro com SCOPE=SPFILE exige restart.")

        overall = worst(row_sev)
        resumo = (f"Conformidade {pct}% ({conformes}/{total} itens). "
                  + (f"{len(desvios)} desvio(s), {n_crit} critico(s). "
                     if desvios else "Nenhum desvio. ")
                  + ("Ha desvio critico de configuracao: trate antes de "
                     "investigar performance." if overall == Severity.CRIT else
                     "Desvios a avaliar em janela."
                     if overall == Severity.WARN else ""))
        return [sec], resumo
