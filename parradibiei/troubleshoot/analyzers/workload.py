"""
Analisador de Carga / Workload Trend.

Detecta AUMENTO de carga/utilizacao comparando janela RECENTE (24h) x BASELINE
(7 dias) — estilo SolarWinds DPA/Datadog. Cobre dois angulos:
- **workload_trend**: metricas do sistema (Average Active Sessions, CPU, execucoes/s,
  logons/s, user calls/s, I/O, redo, transacoes/s) que subiram na ultima janela.
- **workload_module**: qual MODULE/aplicacao aumentou atividade (candidato a
  mudanca da aplicacao/deploy que nao foi avisada ao DBA).

Entrada: saidas das queries workload_* (mcp/catalog.json) com colunas
<NOME> (METRICA/MODULO), AVG_BASE/BASE_DIA, AVG_REC/REC_DIA, VAR_PCT, STATUS.
Fonte: DBA_HIST (requer Diagnostics Pack).
"""
from __future__ import annotations

import datetime
import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import (
    Analyzer,
    cell,
    find_col,
    num,
    parse_fixed_width_tables,
    read_text,
    saturacao_do_host,
)


class WorkloadAnalyzer(Analyzer):
    kind = "workload"
    title = "Carga / Workload Trend"

    SALTO_RATIO = 1.5      # valor do dia >= 1.5x a mediana dos dias anteriores
    JANELA_BASE = 7

    # ── quantos dias anteriores fazem uma BASE ────────────────
    # O laco comecava em i=2, ou seja julgava o terceiro dia da serie contra a
    # mediana de DOIS dias. Mediana de dois pontos nao e base: e a media de uma
    # amostra que nao tem como ter dispersao, e qualquer oscilacao passa de
    # 1.5x. O efeito foi medido e era dominante:
    #
    #   47 saltos na frota, 28 deles (60%) exatamente no indice 2.
    #
    # Pior que o volume, o resultado ENGANAVA: como todas as coletas da frota
    # comecam a serie no mesmo dia da semana, o indice 2 caia sempre numa
    # terca, e 23 dos 47 saltos (49%) apareciam como terca-feira - contra 14%
    # esperados. O relatorio dizia "salto em 2026-09-01" e mandava o DBA
    # procurar uma mudanca naquele dia; o que a data realmente significava era
    # "o terceiro dia da janela que coletamos".
    #
    # Com 4 dias a distribuicao por dia da semana volta a ser plausivel e
    # sobram 11 dias avaliaveis nos 15 coletados.
    JANELA_MIN = 4

    # ── piso de materialidade por FAMILIA DE UNIDADE ──────────
    # Percentual isolado engana, e aqui engana mais que em qualquer outro
    # analisador: a serie diaria compara o dia com a mediana dos 7 anteriores,
    # e sobre base minuscula toda oscilacao vira multiplo. Medido na frota de
    # 26 casos: 37 dos 90 achados de carga (41%) nascem de base irrelevante -
    # 11 deles com o banco praticamente PARADO, como "Average Active Sessions:
    # salto em 2026-08-30 (0.02 -> 0.39, +1850%)". AAS de 0,39 significa que,
    # em media, menos de meia sessao estava ativa no dia.
    #
    # Warn sobre isso nao e so ruido: ele dilui os 53 achados de carga que sao
    # reais. E o mesmo remedio que `saida.py` (CARGA_MIN_PICO) e
    # `baseline.py` (MIN_AVG_S / MIN_DELTA_S) ja aplicam, com a justificativa
    # escrita nos dois: "pico de 8 contra mediana de 2 e aritmetica, nao
    # mudanca de patamar".
    #
    # Por FAMILIA e nao um numero so porque as escalas nao se comparam: AAS 10
    # e um banco ocupadissimo e `Executions Per Sec` 10 e quase ocioso. A
    # familia sai do NOME da metrica, que e o que a saida traz.
    #
    # AVISO HONESTO: o unico piso com significado direto e o de AAS - abaixo de
    # 1, menos de uma sessao esteve ativa em media, e nao ha carga a discutir.
    # Os outros dois sao conservadores e merecem revisao de quem conhece o
    # parque; por isso ficam DECLARADOS aqui, num lugar so, e nao espalhados.
    PISO_POR_FAMILIA = (
        (("active session",), 1.0),       # AAS: < 1 = menos de uma sessao ativa
        (("(%)", "utilization"), 20.0),   # escala 0-100
        (("per sec", "por segundo"), 10.0),
    )
    # Serie por MODULO: os valores sao contagem de amostras do ASH. Um punhado
    # de amostras num dia inteiro nao e presenca de modulo nenhum.
    PISO_MODULO = 10.0

    @classmethod
    def piso(cls, nome: str, is_module: bool) -> float:
        """Piso absoluto de materialidade para a serie de `nome`."""
        if is_module:
            return cls.PISO_MODULO
        baixo = (nome or "").lower()
        for marcas, valor in cls.PISO_POR_FAMILIA:
            if any(m in baixo for m in marcas):
                return valor
        return 0.0      # metrica desconhecida: nao inventa piso

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"workload|carga|sysmetric|throughput", fn):
            score += 60
        h = head.upper()
        if "VAR_PCT" in h and ("AVG_REC" in h or "REC_DIA" in h):
            score += 40
        if "CARGA" in h or "WORKLOAD" in h or "METRICA" in h:
            score += 20
        return min(score, 100)

    # Aumento a partir do qual a carga pode chegar a CRITICO - e so quando o
    # host estiver SATURADO (ver `_sev`).
    CRIT_VAR_PCT = 100.0

    def _sev(self, var_pct, host_saturado: bool = False) -> str:
        """Severidade do aumento de carga.

        Carga sozinha e ADVISORIA: um aumento percentual de uma metrica de taxa
        nao e, isoladamente, um incidente - o banco pode ter absorvido a carga
        sem ninguem sentir. Por isso o teto era ATENCAO.

        O que faltava era o outro lado da conta: dobrar a carga num host que JA
        esta enfileirando processo e um incidente, e sai com a mesma cor de uma
        oscilacao inofensiva. Metodo USE - utilizacao nao e saturacao, e e a
        saturacao que o usuario sente.

        A legenda da metrica tambem prometia ">=100% critico" enquanto o codigo
        limitava em ATENCAO: legenda e o que o leitor usa para interpretar a
        cor, e prometer uma cor que nunca aparece ensina a desconfiar da
        escala inteira.
        """
        v = var_pct if var_pct is not None else 0
        if v >= self.CRIT_VAR_PCT and host_saturado:
            return Severity.CRIT
        if v >= 50:
            return Severity.WARN
        return Severity.OK

    @staticmethod
    def _median(vals):
        s = sorted(vals)
        n = len(s)
        if n == 0:
            return 0
        m = n // 2
        return s[m] if n % 2 else (s[m - 1] + s[m]) / 2

    @classmethod
    def _agrupa_serie(cls, rows, ci_name, ci_dia, ci_val) -> dict:
        """{nome: [(dia, valor), ...]} a partir das linhas de uma serie diaria."""
        groups: dict = {}
        for r in rows:
            nome = str(r[ci_name]).strip() if 0 <= ci_name < len(r) else "?"
            dia = str(r[ci_dia]).strip() if 0 <= ci_dia < len(r) else ""
            val = num(r[ci_val]) if 0 <= ci_val < len(r) else None
            if val is not None and dia:
                groups.setdefault(nome, []).append((dia, val))
        return groups

    @staticmethod
    def _fim_de_semana(dia: str):
        """True/False para sabado-domingo, ou None se a data nao for legivel."""
        try:
            return datetime.date.fromisoformat(dia.strip()).weekday() >= 5
        except (ValueError, AttributeError):
            return None

    @classmethod
    def _base_comparavel(cls, serie: list, i: int) -> list:
        """Dias anteriores que servem de base para julgar o dia `i`.

        Compara dia UTIL com dia util e fim de semana com fim de semana. Sem
        isso, uma segunda-feira e julgada contra uma mediana que inclui sabado
        e domingo: num banco que fica ocioso no fim de semana, a base afunda e
        a segunda aparece como salto de carga todo santo dia 1 da semana.

        Medido na frota: 8 das 26 series tem MEDIANA ZERO no fim de semana, e
        no decil superior o dia util roda 2,45x o fim de semana. Nesta frota
        (homologacao) o efeito agregado e pequeno - a mediana geral da razao e
        1,00x -, e o ajuste vale pelo que evita numa frota de producao, onde o
        padrao semanal e a regra e nao a excecao.

        Cai para "todos os dias anteriores" quando nao ha dias do mesmo tipo em
        quantidade suficiente, ou quando a data nao e legivel. Base pequena e
        do mesmo tipo e pior que base maior e misturada: a exigencia de
        `JANELA_MIN` existe justamente porque mediana de dois pontos nao e
        base.
        """
        prior = serie[max(0, i - cls.JANELA_BASE):i]
        fds = cls._fim_de_semana(serie[i][0])
        if fds is None:
            return prior
        mesmo_tipo = [(d, v) for d, v in prior
                      if cls._fim_de_semana(d) is fds]
        return mesmo_tipo if len(mesmo_tipo) >= cls.JANELA_MIN else prior

    @classmethod
    def detecta_saltos(cls, groups: dict, material=0.0) -> list:
        """Series com salto de carga: [(nome, dia, base, valor, serie_ordenada)].

        FONTE UNICA do limiar de salto - usada pela secao diaria e tambem pelo
        ChangesAnalyzer, que correlaciona a DATA do salto com as mudancas do
        ambiente. Duplicar esse limiar faria as duas secoes discordarem.

        `material` aceita um numero (piso igual para todas as series) OU uma
        funcao `nome -> piso`. A segunda forma existe porque as metricas nao
        compartilham escala: AAS 10 e um banco ocupadissimo e `Executions Per
        Sec` 10 e quase ocioso, entao um piso unico seria errado para uma das
        duas. Numero continua aceito para nao quebrar quem ja chamava assim.
        """
        out = []
        for nome, serie in groups.items():
            minimo = material(nome) if callable(material) else material
            serie = sorted(serie, key=lambda x: x[0])
            candidatos = []
            for i in range(len(serie)):
                dia, val = serie[i]
                prior = cls._base_comparavel(serie, i)
                if len(prior) < cls.JANELA_MIN:
                    continue
                base = cls._median([v for _, v in prior])
                if base and val >= base * cls.SALTO_RATIO and val >= minimo:
                    candidatos.append((dia, val / base, base, val))
            episodio = cls._maior_episodio(candidatos)
            if episodio:
                dia, base, val = episodio
                out.append((nome, dia, base, val, serie))
        return out

    @staticmethod
    def _dias_consecutivos(a: str, b: str) -> bool:
        try:
            return (datetime.date.fromisoformat(b.strip())
                    - datetime.date.fromisoformat(a.strip())).days == 1
        except (ValueError, AttributeError):
            return False

    @classmethod
    def _maior_episodio(cls, candidatos: list):
        """Do conjunto de dias que passaram do limiar, qual REPORTAR.

        Carga que sobe e fica alta marca vários dias seguidos - é um episodio,
        nao N saltos. E o que se precisa saber de um episodio e QUANDO ELE
        COMECOU, porque e a data que se cruza com deploy, job novo e mudanca de
        ambiente. Reportar o dia de maior razao daria o segundo ou terceiro dia
        do degrau e mandaria procurar mudanca no dia errado.

        Havia um `break` no primeiro dia que passasse do limiar, o que e outra
        coisa: com varios episodios na janela, o primeiro nao e o maior. Medido
        na frota, em 13 series o primeiro salto nao era o maior - e num caso o
        relatorio anunciava 3,3x e ficava calado sobre um 6,6x tres dias
        depois, o dobro do tamanho.

        Regra: agrupa dias consecutivos em episodios, escolhe o episodio de
        maior PICO e devolve o PRIMEIRO dia dele.
        """
        if not candidatos:
            return None
        episodios: list = []
        for dia, ratio, base, val in sorted(candidatos):
            if episodios and cls._dias_consecutivos(episodios[-1][-1][0], dia):
                episodios[-1].append((dia, ratio, base, val))
            else:
                episodios.append([(dia, ratio, base, val)])
        melhor = max(episodios, key=lambda ep: max(r for _d, r, _b, _v in ep))
        dia, _ratio, base, val = melhor[0]
        return dia, base, val

    def _daily_section(self, headers, rows, ci_name, ci_dia, ci_val, is_module):
        """Serie diaria (DIA, NOME, VALOR): detecta o dia do salto de carga."""
        groups = self._agrupa_serie(rows, ci_name, ci_dia, ci_val)

        titulo = ("Carga por modulo - serie diaria (14d)" if is_module
                  else "Carga - serie diaria por metrica (14d)")
        sec = Section(title=titulo, table_headers=headers, table_rows=rows[:60])
        # Piso de materialidade, em duas camadas:
        #  - RELATIVO (so modulos): ignora quem e insignificante frente ao maior
        #    consumidor - evita falso-positivo de base minuscula (0.1 -> 2.0);
        #  - ABSOLUTO (todos): o valor tem de significar algo na unidade dele.
        #    Ver `PISO_POR_FAMILIA` - e o que barra "AAS 0.02 -> 0.39, +1850%".
        allv = [v for s in groups.values() for _, v in s]
        relativo = 0.10 * max(allv) if (is_module and allv) else 0.0

        def _piso(nome: str) -> float:
            return max(relativo, self.piso(nome, is_module))

        for nome, dia, base, val, serie in self.detecta_saltos(groups, _piso):
            ratio = val / base
            spark = " ".join(f"{d[5:] if len(d) >= 5 else d}={v:g}" for d, v in serie)
            sec.findings.append(Finding(
                title=f"{nome}: salto em {dia} "
                      f"({base:g} -> {val:g}, +{(ratio - 1) * 100:.0f}%)",
                severity=Severity.WARN,
                impact=f"+{(ratio - 1) * 100:.0f}% no dia {dia}",
                detail="serie: " + spark,
                recommendation=("Casar a DATA do salto com deploy/mudanca da aplicacao, "
                                "novos jobs/batch e volume; alinhar capacidade com o time."),
            ))
        order = {Severity.CRIT: 0, Severity.WARN: 1}
        sec.findings.sort(key=lambda f: order.get(f.severity, 2))
        sec.metrics.append(Metric("Series com salto", str(len(sec.findings)),
                                  severity=worst([f.severity for f in sec.findings])
                                  if sec.findings else Severity.OK))
        return sec

    def analyze(self, files, meta):
        sections = []
        summary_bits = []
        overall = Severity.INFO
        # Estado do host, lido uma vez da pasta do caso. O precedente e o
        # `baseline.py`, que consulta o `regressions.txt` irmao para nao contar
        # a mesma regressao duas vezes: analisador pode olhar a evidencia ao
        # lado quando o veredito depende dela.
        sat = saturacao_do_host(
            os.path.dirname(os.path.abspath(files[0]))) if files else {}
        # `fila_agora` e nao `saturado`: promover a CRITICO exige que o recurso
        # esteja enfileirando AGORA. Ver `saturacao_do_host`.
        host_saturado = bool(sat.get("fila_agora"))

        for path in files:
            fname = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
            is_module = "module" in fname or "modulo" in fname
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_name = find_col(headers, "metrica", "modulo", "module", "metric", "name")
                ci_name = max(ci_name, 0)

                # Formato SERIE DIARIA (DIA, NOME, VALOR): detecta o dia do salto
                ci_dia = find_col(headers, "dia", "data", "day")
                ci_val = find_col(headers, "valor", "value", "samples", "media")
                if ci_dia >= 0 and ci_val >= 0:
                    sec = self._daily_section(headers, rows, ci_name, ci_dia, ci_val, is_module)
                    sections.append(sec)
                    overall = worst([overall, sec.severity])
                    if sec.findings:
                        summary_bits.append(f"{len(sec.findings)} serie(s) com salto de carga")
                    continue

                # Formato COMPARE (janela BASE x REC)
                ci_var = find_col(headers, "var_pct", "variacao", "var")
                ci_base = find_col(headers, "avg_base", "base_dia", "base")
                ci_rec = find_col(headers, "avg_rec", "rec_dia", "rec")
                if ci_rec < 0 and ci_var < 0:
                    continue

                titulo = ("Carga por modulo/aplicacao" if is_module
                          else "Tendencia de carga (metricas do sistema)")
                sec = Section(title=titulo, table_headers=headers, table_rows=rows[:25])
                # Piso de materialidade (so p/ modulos): 10% do maior consumidor
                material = 0.0
                if is_module and ci_rec >= 0:
                    recs = [num(r[ci_rec]) for r in rows if ci_rec < len(r)]
                    recs = [x for x in recs if x is not None]
                    material = 0.10 * max(recs) if recs else 0.0
                row_sev, worst_var = [], 0
                for r in rows:
                    nome = str(cell(r, ci_name)).strip()
                    base = num(cell(r, ci_base)) if ci_base >= 0 else None
                    rec = num(cell(r, ci_rec)) if ci_rec >= 0 else None
                    var_pct = num(cell(r, ci_var)) if ci_var >= 0 else None
                    if var_pct is None and base and rec:
                        var_pct = (rec - base) / base * 100.0
                    sev = self._sev(var_pct, host_saturado)
                    # O MESMO piso absoluto da serie diaria. Sem ele, esta
                    # secao contradizia a de baixo no mesmo relatorio: a
                    # diaria barrava a base irrelevante e a compare deixava
                    # passar "carga +61% (3.1->5.0)". Duas secoes discordando
                    # sobre a mesma medicao e pior que as duas erradas.
                    if rec is not None and rec < self.piso(nome, is_module):
                        sev = Severity.OK
                    # modulo insignificante frente ao maior: nao gera alerta
                    if is_module and (rec is None or rec < material):
                        sev = Severity.OK
                    row_sev.append(sev)
                    if var_pct is not None and sev != Severity.OK:
                        worst_var = max(worst_var, var_pct)
                    if sev in (Severity.WARN, Severity.CRIT):
                        det = f"{base}->{rec}" if (base is not None and rec is not None) else ""
                        if is_module:
                            reco = ("Aumento de atividade da aplicacao sem aviso? Confirmar "
                                    "deploy/mudanca de comportamento, novos jobs/batch e volume; "
                                    "alinhar com o time da aplicacao (capacity).")
                        else:
                            reco = ("Metrica de carga subiu na ultima janela. Correlacionar com "
                                    "deploy/mudanca da aplicacao, jobs e volume; avaliar capacidade "
                                    "(CPU/memoria/IO) e SQL ofensor.")
                        # A cor precisa vir acompanhada do porque. CRITICO aqui
                        # nao sai da carga sozinha, e sim do cruzamento com a
                        # saturacao do host - e quem le tem de poder conferir.
                        if sev == Severity.CRIT:
                            detalhe = (
                                f"A carga subiu {var_pct:.0f}% E o host esta "
                                f"saturado: {sat.get('motivo')}. Nao e mais "
                                "tendencia a acompanhar - o recurso ja esta em "
                                "fila, e o tempo de resposta piora para todas "
                                "as sessoes, inclusive as de SQL bem escrito.")
                        elif host_saturado:
                            detalhe = (f"Host com saturacao ({sat.get('motivo')}), "
                                       "porem o aumento ficou abaixo de "
                                       f"{self.CRIT_VAR_PCT:.0f}%.")
                        elif sat:
                            detalhe = ("O host ABSORVEU o aumento (sem fila de CPU "
                                       "e sem paginacao relevante): e tendencia de "
                                       "capacidade, nao incidente - por isso "
                                       "ATENCAO e nao critico.")
                        else:
                            # Sem osstat.txt nao ha como afirmar nem negar
                            # saturacao. Dizer isso e melhor que deixar o
                            # silencio parecer "host saudavel".
                            detalhe = ("Sem evidencia do host (osstat.txt ausente): "
                                       "nao foi possivel verificar se ha saturacao "
                                       "de CPU ou memoria junto com o aumento.")
                        sec.findings.append(Finding(
                            title=f"{nome}: carga +{var_pct:.0f}%"
                                  + (f" ({det})" if det else ""),
                            severity=sev,
                            detail=detalhe,
                            impact=f"+{var_pct:.0f}%" if var_pct is not None else "aumento",
                            recommendation=reco,
                        ))
                sec.row_severity = row_sev[:25]
                if worst_var:
                    sec.metrics.append(Metric(
                        "Maior aumento", f"+{worst_var:.0f}", "%",
                        severity=worst(row_sev) if row_sev else Severity.INFO,
                        # A legenda dizia ">=100% critico" e o codigo nunca
                        # chegava la. Agora diz a condicao inteira - e quando o
                        # host nao esta saturado, avisa que o teto e ATENCAO,
                        # para ninguem procurar um critico que nao vai existir.
                        hint=(f">=50% atencao, >={self.CRIT_VAR_PCT:.0f}% critico "
                              "(host saturado)" if host_saturado else
                              ">=50% atencao; critico so com saturacao do host")))
                sec.metrics.append(Metric("Itens em alta", str(len(sec.findings))))
                sections.append(sec)
                overall = worst([overall, sec.severity])
                if sec.findings:
                    alvo = "modulo(s)/app" if is_module else "metrica(s)"
                    summary_bits.append(f"{len(sec.findings)} {alvo} com aumento de carga")

        if not sections:
            return [], ""
        summary = ("; ".join(summary_bits[:2]) + ". " if summary_bits else "")
        summary += ("Aumento critico de carga: investigar mudanca da aplicacao/capacidade."
                    if overall == Severity.CRIT else
                    "Aumento de carga em atencao: monitorar e alinhar com a aplicacao."
                    if overall == Severity.WARN else
                    "Carga estavel entre as janelas.")
        return sections, summary


def dias_de_salto(paths) -> dict:
    """{dia: [nome da serie, ...]} dos saltos de carga nas series diarias.

    Exposto para o ChangesAnalyzer responder "o que mudou no dia do salto?".
    Reaproveita WorkloadAnalyzer.detecta_saltos para que as duas secoes nunca
    apontem dias diferentes.
    """
    achados: dict = {}
    for path in paths:
        fname = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
        is_module = "module" in fname or "modulo" in fname
        for headers, rows in parse_fixed_width_tables(read_text(path)):
            ci_dia = find_col(headers, "dia", "data", "day")
            ci_val = find_col(headers, "valor", "value", "samples", "media")
            if ci_dia < 0 or ci_val < 0:
                continue
            ci_name = find_col(headers, "metrica", "modulo", "module", "metric", "name")
            ci_name = max(ci_name, 0)
            groups = WorkloadAnalyzer._agrupa_serie(rows, ci_name, ci_dia, ci_val)
            material = 0.0
            if is_module:
                allv = [v for s in groups.values() for _, v in s]
                material = 0.10 * max(allv) if allv else 0.0
            for nome, dia, _base, _val, _serie in WorkloadAnalyzer.detecta_saltos(
                    groups, material):
                achados.setdefault(dia, []).append(nome)
    return achados
