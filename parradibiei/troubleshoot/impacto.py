"""
Numeros de impacto: checklist na mao x framework, por nivel de senioridade.

Este modulo existe para uma coisa so: **nenhum numero do deck de impacto pode
ser inventado**. Quem apresenta vai ouvir "de onde saiu esse numero?" na
primeira reuniao, e uma resposta ruim derruba o resto da apresentacao junto.

Por isso os numeros aqui tem exatamente tres origens, e cada slide diz qual e:

1. **Contado do repositorio** - quantos checks o catalogo tem hoje
   (`apresentacao.numeros()`). Nao envelhece: e recontado a cada geracao.
2. **Medido** - a duracao real das coletas ja executadas, lida dos
   `coleta.log` dos casos (`medir_coleta()`). Se nao houver historico, cai
   para a premissa declarada e AVISA que caiu (`medido=False`).
3. **Premissa declarada** - o tempo do trabalho manual, que ninguem cronometra.
   Fica em `PREMISSAS`, num lugar so, com referencia publica em `FONTES` e
   impressa num slide do deck. Premissa escondida e chute; premissa na tela e
   argumento - a plateia pode discordar do numero sem discordar do metodo.

Regra de ouro do modelo: **na duvida, favorecer o lado manual**. A cobertura
manual usa a lista mais longa que se acha publicada (16 itens), o tempo por
check usa o ritmo de quem ja tem o script pronto, e o nosso lado inclui o
tempo humano de ler o dashboard. Se a comparacao ainda ganha com o placar
inclinado contra ela, o resultado se defende sozinho.
"""
from __future__ import annotations

import glob
import os
import re
import statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(BASE_DIR, "troubleshooting")

NIVEIS = ("junior", "pleno", "senior")
ROTULO = {"junior": "Junior", "pleno": "Pleno", "senior": "Senior"}

# Quantos itens a rotina de checklist diario cobre QUANDO FEITA NA MAO.
# 16 e o tamanho da maior lista publica que achei (ver FONTES); as outras vao
# de 10 a 14. Usar a maior e deliberado: infla o lado manual na comparacao.
CHECKS_NA_MAO = 16

# Minutos. Um dicionario por nivel para que ajustar o modelo seja mexer em um
# lugar so - e para que o slide de premissas seja gerado DESTES numeros, nunca
# redigitado ao lado deles.
PREMISSAS = {
    "junior": {
        # manual: preparar o ambiente, rodar cada check, escrever o chamado
        "manual_preparo": 15.0,   # achar a conexao, abrir sessao, juntar scripts
        "manual_por_check": 4.0,  # lembrar/procurar a query, rodar, ler, anotar
        "manual_redigir": 25.0,   # diagnostico + RCA em texto livre
        # framework: o que continua sendo humano
        "fw_leitura": 10.0,       # ler o dashboard e o plano priorizado
        "fw_redigir": 2.0,        # revisar o template e colar no chamado
    },
    "pleno": {
        "manual_preparo": 10.0,
        "manual_por_check": 2.5,
        "manual_redigir": 18.0,
        "fw_leitura": 8.0,
        "fw_redigir": 2.0,
    },
    "senior": {
        "manual_preparo": 5.0,
        "manual_por_check": 1.5,
        "manual_redigir": 12.0,
        "fw_leitura": 5.0,
        "fw_redigir": 2.0,
    },
}

# Usado quando ainda nao ha historico suficiente para medir (maquina limpa,
# primeiro uso). Conservador de proposito: melhor prometer menos do que o
# medido - hoje a mediana real das coletas fica MUITO abaixo destes 3 minutos.
COLETA_PADRAO_MIN = 3.0

# Abaixo disso o numero nao vira "medido" no deck. Uma unica execucao nao e
# medicao: pode ter sido um banco pequeno, numa rede boa, num horario calmo.
# O diario de coleta (`coleta.log`) e recente, entao os casos antigos nao tem
# essa linha - a amostra cresce sozinha conforme a ferramenta e usada.
MIN_AMOSTRAS = 3

FONTES = [
    ("Rotina de health check diario com scripts prontos: 10-15 min por base, "
     "cobrindo ~10 verificacoes",
     "github.com/uusen01/oracle-health-checks"),
    ("Listas publicas de checklist diario de DBA Oracle: de 10 a 16 itens "
     "(usamos 16, a maior, a favor do lado manual)",
     "ittutorial.org / halimdba.blogspot.com"),
    ("MTTR medio de 3 a 5 horas entre industrias; menos de 1 hora e "
     "best-in-class; 55% dos incidentes passam de 2 horas",
     "SleekOps / base de 150 mil incidentes"),
    ("Alvo de MTTR no setor financeiro (BFSI): 1 a 2 horas",
     "Opsio - MTTD/MTTR benchmarks por industria"),
    ("A coleta de contexto consome os primeiros 30 a 60 minutos do incidente",
     "PagerDuty / Opsgenie, via sixta.ai"),
    ("Base de conhecimento e roteiro padronizado elevaram a resolucao no "
     "primeiro contato de 48% para 72% e cortaram 55% dos escalonamentos",
     "Supportbench - staffing L1/L2/L3"),
    ("64% das organizacoes com observabilidade relatam ganho de 25% ou mais "
     "no MTTR",
     "New Relic - Observability Forecast"),
]


def medir_coleta() -> dict:
    """Duracao real das coletas ja executadas, lida dos `coleta.log`.

    O coletor grava `[tempo] coleta em 12.3s` ao fim de cada execucao. Isso
    torna a metade do comparativo uma MEDICAO, nao uma promessa - e e o unico
    numero do deck que melhora sozinho conforme a ferramenta e usada.

    Usa a MEDIANA, nao a media: uma coleta que pegou o banco no meio de um
    backup, ou uma que caiu na rede, joga a media para cima e nao representa
    o dia normal. Devolve tambem `amostras` para o slide poder dizer em quantas
    execucoes o numero se apoia - numero sem tamanho de amostra nao e medicao.

    ATENCAO ao contrato: `segundos` e sempre o tempo REAL quando existe pelo
    menos uma execucao registrada; `medido` e so o rotulo de CONFIANCA
    (amostra >= MIN_AMOSTRAS). Ja foram a mesma coisa, e o efeito foi absurdo:
    com uma coleta real de 10s o deck anunciava os 3 minutos da premissa - ou
    seja, mentia contra a propria ferramenta. Premissa conservadora serve para
    o caso de nao haver medicao nenhuma, nao para substituir a que existe.

    Nao le nome de caso, de servidor nem conteudo de evidencia: so o segundo.
    """
    segundos = []
    padrao = re.compile(r"\[tempo\]\s+coleta em\s+([\d.]+)s")
    for p in glob.glob(os.path.join(CASES_DIR, "*", "coleta.log")):
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for linha in fh:
                    m = padrao.search(linha)
                    if m:
                        segundos.append(float(m.group(1)))
        except OSError:
            continue
    if not segundos:
        return {"medido": False, "amostras": 0,
                "minutos": COLETA_PADRAO_MIN,
                "segundos": COLETA_PADRAO_MIN * 60}
    med = statistics.median(segundos)
    return {"medido": len(segundos) >= MIN_AMOSTRAS,
            "amostras": len(segundos), "minutos": med / 60.0,
            "segundos": med}


def medir_maquina(limite: int = 0, cases_dir: str = "",
                  repeticoes: int = 3) -> dict:
    """Cronometra o pipeline INTEIRO sobre a evidencia ja gravada.

    A coleta e so a primeira metade do que a maquina faz. Depois dela vem a
    analise, o dashboard, o PPTX e o relatorio no Template Padrao - e e esse
    conjunto que substitui o trabalho manual. Medir so a coleta subestimava o
    nosso lado e, pior, deixava o numero mais impressionante do deck de fora.

    Roda **offline e sem escrever na pasta do caso**: `analisar_caso` nao
    grava nada e os artefatos vao para um diretorio temporario. E a mesma
    regra de ouro do corpus de regressao - caso de producao nunca e tocado.

    Mediana, nunca media: um caso gigante (ou o primeiro, que paga o import
    dos modulos) nao pode definir o numero que vai para o slide.

    `repeticoes` existe porque a maquina que roda isto pode ter poucos casos
    gravados, e uma unica execucao nao e medicao. Repetir o mesmo caso e
    legitimo aqui - o que esta sendo cronometrado e o PIPELINE, nao o caso -
    desde que o slide diga quantas execucoes sobre quantos casos, que e
    exatamente o que `amostras` e `casos` carregam.
    """
    import tempfile
    import time

    from . import caso as caso_lifecycle

    casos = caso_lifecycle.listar_casos(cases_dir)
    casos = [c.get("caso") if isinstance(c, dict) else c for c in casos]
    casos = [c for c in casos if c]
    if limite:
        casos = casos[:limite]

    import TROUBLESHOOT

    from .report import build_html, build_incident_md, build_pptx

    tempos, falhas, ok = [], 0, set()
    with tempfile.TemporaryDirectory(prefix="medicao_") as tmp:
        for _ in range(max(1, repeticoes)):
            for c in casos:
                try:
                    t0 = time.perf_counter()
                    r = TROUBLESHOOT.analisar_caso(c, cases_dir=cases_dir,
                                                   expandir=False, quieto=True)
                    build_html(r, os.path.join(tmp, f"{c}.html"))
                    build_pptx(r, os.path.join(tmp, f"{c}.pptx"))
                    build_incident_md(r, os.path.join(tmp, f"{c}.md"))
                    tempos.append(time.perf_counter() - t0)
                    ok.add(c)
                except Exception:
                    # Caso torto nao derruba a medicao - so fica fora da amostra.
                    falhas += 1

    if not tempos:
        return {"medido": False, "amostras": 0, "casos": 0, "falhas": falhas,
                "segundos": 0.0}
    # O primeiro caso paga o import tardio de pptx/analisadores. Com amostra
    # suficiente ele e so mais um ponto, e a mediana ja o descarta sozinha.
    return {"medido": len(tempos) >= MIN_AMOSTRAS, "amostras": len(tempos),
            "casos": len(ok), "falhas": falhas,
            "segundos": statistics.median(tempos),
            "mais_lento": max(tempos), "mais_rapido": min(tempos)}


# Onde a medicao fica TRAVADA. O deck le daqui em vez de cronometrar na hora:
# gerar o deck passaria a levar minutos, e - pior - o numero mudaria conforme
# a maquina de quem gerou. Medicao com data e amostra e evidencia; numero que
# muda a cada execucao e ruido.
MEDICAO_JSON = os.path.join(BASE_DIR, "output", "apresentacao", "medicao.json")


def gravar_medicao(caminho: str = "", limite: int = 0,
                   repeticoes: int = 3) -> dict:
    """Mede e grava. Chamado por `APRESENTAR.py --medir`, nunca pelo deck."""
    import json
    from datetime import datetime

    dados = {
        "medido_em": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "coleta": medir_coleta(),
        "maquina": medir_maquina(limite=limite, repeticoes=repeticoes),
    }
    p = caminho or MEDICAO_JSON
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2)
    return dados


def ler_medicao(caminho: str = "") -> dict:
    """A medicao travada, ou {} se ainda nao foi feita.

    Nunca mede sozinha: se o arquivo nao existe, o deck cai na premissa
    conservadora e diz na tela que caiu. Silenciosamente cronometrar durante a
    geracao do deck seria a pior das opcoes - lento e nao reproduzivel.
    """
    import json
    p = caminho or MEDICAO_JSON
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def tempo_maquina() -> dict:
    """Minutos que a MAQUINA leva, de ponta a ponta, e de onde o numero vem.

    Coleta + analise + dashboard + PPTX + relatorio. Quando ha medicao
    travada, e medicao; senao, e a premissa conservadora - e o campo `medido`
    diz qual dos dois, para o slide nao poder mentir por omissao.
    """
    m = ler_medicao()
    col, maq = m.get("coleta") or {}, m.get("maquina") or {}
    # O pipeline exige amostra (da para repetir a vontade, offline). A coleta
    # nao da para repetir sem um banco do outro lado, entao basta UMA execucao
    # real - e o slide imprime quantas foram, para ninguem confundir uma
    # execucao registrada com uma mediana robusta.
    if maq.get("medido") and (col.get("amostras") or 0) >= 1:
        seg = col["segundos"] + maq["segundos"]
        return {"medido": True, "segundos": seg, "minutos": seg / 60.0,
                "coleta_s": col["segundos"], "pipeline_s": maq["segundos"],
                "coleta_amostras": col["amostras"],
                "pipeline_amostras": maq["amostras"],
                "pipeline_casos": maq.get("casos", 0),
                "medido_em": m.get("medido_em", "")}
    # Sem medicao completa, o numero conservador vale para o conjunto todo.
    return {"medido": False, "segundos": COLETA_PADRAO_MIN * 60,
            "minutos": COLETA_PADRAO_MIN,
            "coleta_s": (col.get("segundos") or 0.0),
            "pipeline_s": (maq.get("segundos") or 0.0),
            "coleta_amostras": col.get("amostras", 0),
            "pipeline_amostras": maq.get("amostras", 0),
            "pipeline_casos": maq.get("casos", 0),
            "medido_em": m.get("medido_em", "")}


def comparativo(checks: int, coleta_min: float | None = None) -> dict:
    """O comparativo completo, por nivel. `checks` vem do catalogo.

    Devolve tudo em minutos, ja com as razoes calculadas, para que o deck so
    desenhe - conta feita no slide e conta que ninguem revisa.
    """
    if coleta_min is None:
        # Nao e mais so a coleta: e TUDO que a maquina faz antes de sobrar
        # trabalho humano - coletar, analisar, montar dashboard, PPTX e o
        # relatorio no Template Padrao.
        coleta_min = tempo_maquina()["minutos"]
    linhas = {}
    for nv in NIVEIS:
        p = PREMISSAS[nv]
        manual = (p["manual_preparo"]
                  + p["manual_por_check"] * CHECKS_NA_MAO
                  + p["manual_redigir"])
        humano = p["fw_leitura"] + p["fw_redigir"]
        nosso = coleta_min + humano
        linhas[nv] = {
            "manual": manual,
            "nosso": nosso,
            # A separacao que muda a conversa: o que a MAQUINA faz (medido, em
            # segundos) e o que continua sendo trabalho de gente (premissa).
            # Sem ela, o ganho parece vir de uma estimativa; com ela, metade
            # do numero e cronometro.
            "maquina": coleta_min,
            "humano": humano,
            "economia": manual - nosso,
            "vezes": (manual / nosso) if nosso else 0.0,
            # Checks entregues por minuto gasto: junta tempo e cobertura no
            # mesmo numero. E onde a diferenca aparece inteira, porque o manual
            # nao fica so mais lento - ele tambem olha menos.
            "checks_min_manual": CHECKS_NA_MAO / manual if manual else 0.0,
            "checks_min_nosso": checks / nosso if nosso else 0.0,
        }
    return {
        "checks": checks,
        "checks_na_mao": CHECKS_NA_MAO,
        "cobertura_vezes": (checks / CHECKS_NA_MAO) if CHECKS_NA_MAO else 0.0,
        "coleta_min": coleta_min,
        "maquina_min": coleta_min,
        "maquina_seg": coleta_min * 60.0,
        "niveis": linhas,
        # Quanto o resultado depende de QUEM atendeu. No manual e a razao entre
        # o pior e o melhor tempo; a cobertura tambem varia com a pessoa. No
        # framework a cobertura e a MESMA para os tres - e esse o argumento.
        "dispersao_manual": (max(linhas[n]["manual"] for n in NIVEIS)
                             / min(linhas[n]["manual"] for n in NIVEIS)),
        "dispersao_nosso": (max(linhas[n]["nosso"] for n in NIVEIS)
                            / min(linhas[n]["nosso"] for n in NIVEIS)),
    }


# Minutos de um turno que sobram DE FATO para troubleshooting, depois de
# reuniao, passagem de plantao, demanda de projeto e interrupcao. 6h de 8h e
# uma premissa conservadora: quanto MENOR este numero, menor o ganho calculado.
JORNADA_MIN = 360.0

# Tamanho de fila usado como exemplo no slide de represamento. Nao e a fila
# real de ninguem - e um numero redondo para a plateia fazer a conta junto.
FILA_EXEMPLO = 20


def capacidade(comp: dict, jornada_min: float = JORNADA_MIN,
               fila: int = FILA_EXEMPLO) -> dict:
    """Quantos chamados cabem num turno, e em quanto tempo a fila drena.

    Tempo por caso e um numero que so convence tecnico. **Represamento** e o
    numero que convence gestao: e o mesmo dado dito na unidade em que a dor e
    sentida - fila que nao anda, chamado que envelhece, plantao que termina
    com pendencia.

    A conta e deliberadamente ingenua (jornada / tempo por caso). Nao modela
    fila de verdade (chegada, prioridade, interrupcao) porque um modelo de
    filas num slide nao seria checado por ninguem - e o que nao se checa nao
    se defende. O que ela mostra e a ORDEM DE GRANDEZA, e essa aguenta ser
    conferida no papel na frente da plateia.
    """
    out = {"jornada_min": jornada_min, "fila": fila, "niveis": {}}
    for nv in NIVEIS:
        v = comp["niveis"][nv]
        por_dia_manual = jornada_min / v["manual"] if v["manual"] else 0.0
        por_dia_nosso = jornada_min / v["nosso"] if v["nosso"] else 0.0
        out["niveis"][nv] = {
            "por_dia_manual": por_dia_manual,
            "por_dia_nosso": por_dia_nosso,
            # Dias de turno para zerar a fila, com UMA pessoa daquele nivel.
            "fila_dias_manual": (fila / por_dia_manual) if por_dia_manual else 0.0,
            "fila_dias_nosso": (fila / por_dia_nosso) if por_dia_nosso else 0.0,
            "fila_min_nosso": fila * v["nosso"],
        }
    return out


def mm(minutos: float) -> str:
    """Minutos como se fala em reuniao: '1h04' em vez de '64,0 min'."""
    m = round(minutos)
    if m < 60:
        return f"{m} min"
    return f"{m // 60}h{m % 60:02d}"
