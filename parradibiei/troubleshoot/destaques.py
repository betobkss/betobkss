"""
DESTAQUES do caso: os poucos achados que explicam o incidente.

Um caso real chega com dezenas de achados. O `plano.py` responde "o que eu faco
primeiro?" ordenando por classe de falha (integridade antes de performance).
Este modulo responde uma pergunta diferente: **o que aconteceu neste servidor?**

Sao perguntas distintas e a resposta certa muda. Um caso pode ter como acao
prioritaria "renovar senha que expira em 3 dias" (previsivel, barato de evitar)
e, ao mesmo tempo, ter como FATO principal "bloqueio em cascata parou 40
sessoes". O plano acerta a ordem de execucao; o relatorio precisa do fato.

Quatro categorias, que sao as que respondem a maioria dos chamados de
performance em producao:

- **BLOQUEIO** - sessao esperando lock (>= 60s) e, pior, cadeia em cascata.
- **REGRESSAO** - SQL que piorou (limiar de materialidade em `regression.py`).
- **PESADA** - SQL que mais consome (DB time, CPU, amostras).
- **CARGA** - carga do servidor acima do baseline dos 7 dias.

## Como o ranking e feito (e por que nao e por percentual)

A ordenacao usa, em ordem:

1. **Severidade** - e a moeda comum entre categorias. Cada analisador calibrou a
   sua com limiares proprios e justificados (300s/60s de DB time na regressao;
   60s/300s de espera no bloqueio; a carga e limitada a ATENCAO de proposito,
   porque aumento de uma taxa nao e, isoladamente, incidente). Comparar
   percentual entre categorias seria comparar coisas diferentes: +200% de
   `logons/s` nao e pior que +40% num SQL que sustenta o faturamento.
2. **Segundos de DB time** - quando os dois lados tem tempo absoluto, o maior
   vence (Method R / Millsap). E o mesmo critério do `regression.py`: 5% num SQL
   que roda 10.000x/dia custa mais que 700% num que roda 2x.
3. **Peso da categoria** - so como desempate. Bloqueio vem antes porque esta
   acontecendo AGORA com gente parada; carga vem por ultimo porque e tendencia.

## O que este modulo NAO faz

Nao decide causa raiz. Ele entrega FATO OBSERVADO com numero e origem
rastreavel. Quem transforma fato em causa e o DBA - por isso o texto que vai
para o campo "Causa Raiz" do relatorio sai marcado como analise automatica a
confirmar, e qualquer valor registrado a mao (`CASO.py`) continua vencendo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .model import CaseResult, Finding, Severity

# ── categorias ────────────────────────────────────────────────
BLOQUEIO = "bloqueio"
REGRESSAO = "regressao"
PESADA = "pesada"
CARGA = "carga"

# (chave, peso do desempate, rotulo, marcas no titulo da SECAO)
# O peso so desempata: severidade e tempo absoluto decidem antes.
CATEGORIAS = [
    (BLOQUEIO, 40, "Bloqueio / lock",
     ("bloqueio", "bloqueios", "cadeia de bloqueio", "lock")),
    (REGRESSAO, 30, "Regressao de SQL",
     ("regressao", "regressoes", "compare period")),
    # NAO inclui "distribuicao de esperas (ASH)": aquilo e perfil de espera do
    # banco (User I/O, CPU, Concurrency), nao consumo de uma query. Estava aqui
    # e o relatorio do DEMO saiu com "Espera 'User I/O' concentra 50%" rotulado
    # como SQL de maior consumo - rotulo errado no campo Causa Raiz.
    (PESADA, 20, "SQL de maior consumo",
     ("top sql", "sql monitor", "sql lento")),
    (CARGA, 10, "Carga acima do baseline",
     ("carga", "workload", "tendencia de carga", "recursos do host")),
]

_PESO = {c: p for c, p, _r, _m in CATEGORIAS}
_ROTULO = {c: r for c, _p, r, _m in CATEGORIAS}

# Quantos destaques por categoria entram na secao detalhada. O 3 em PESADA e o
# pedido explicito de "top 3 queries pesadas"; nas outras, 2 basta para mostrar
# o padrao sem transformar a secao em nova lista completa de achados.
LIMITE_POR_CATEGORIA = {BLOQUEIO: 3, REGRESSAO: 2, PESADA: 3, CARGA: 2}

TOP_PADRAO = 2

MARCA_AUTO = "[ANALISE AUTOMATICA - confirmar]"

_PESO_SEV = {Severity.CRIT: 3, Severity.WARN: 2, Severity.INFO: 1, Severity.OK: 0}

# ── leitura do campo `impact` ─────────────────────────────────
# Os analisadores escrevem `impact` como texto para o relatorio ("+300s",
# "+180%", "12 min/7d", "3 niveis, 40 sessao(oes)"). Ranquear exige o numero de
# volta. Ler o texto e feio, mas a alternativa - mudar o `Finding` para carregar
# valor estruturado - tocaria os ~29 analisadores de uma vez; fica para quando
# houver segundo consumidor desses numeros.
_NUM = r"([-+]?\d+(?:[.,]\d+)?)"
_RE_MIN = re.compile(_NUM + r"\s*min", re.I)
_RE_SEG = re.compile(_NUM + r"\s*s(?![a-z])", re.I)
_RE_PCT = re.compile(_NUM + r"\s*%")


def _float(txt: str) -> float:
    try:
        return float(str(txt).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def segundos(impacto: str) -> float:
    """Segundos de DB time no texto de impacto, ou 0.0 se nao houver.

    Minutos vem primeiro porque "12 min/7d" tambem casaria com o padrao de
    segundos na letra 's' de nada - e a ordem errada daria 0.
    """
    t = impacto or ""
    m = _RE_MIN.search(t)
    if m:
        return abs(_float(m.group(1))) * 60.0
    m = _RE_SEG.search(t)
    if m:
        return abs(_float(m.group(1)))
    return 0.0


def percentual(impacto: str) -> float:
    m = _RE_PCT.search(impacto or "")
    return abs(_float(m.group(1))) if m else 0.0


# ── destaque ──────────────────────────────────────────────────
@dataclass
class Destaque:
    categoria: str
    secao: str
    achado: Finding

    @property
    def segundos(self) -> float:
        return segundos(self.achado.impact)

    @property
    def percentual(self) -> float:
        return percentual(self.achado.impact)

    @property
    def severidade(self) -> str:
        return self.achado.severity

    @property
    def rotulo(self) -> str:
        return _ROTULO.get(self.categoria, self.categoria)

    @property
    def ordem(self) -> tuple:
        """Chave de ordenacao decrescente (ver docstring do modulo)."""
        return (_PESO_SEV.get(self.severidade, 0),
                self.segundos,
                self.percentual,
                _PESO.get(self.categoria, 0))

    def texto(self) -> str:
        """Uma linha: fato + numero. E o que vai para o corpo do relatorio."""
        imp = (self.achado.impact or "").strip()
        return self.achado.title + (f" ({imp})" if imp else "")


def classificar(secao: str, achado: Finding) -> str | None:
    """Categoria do achado, ou None se ele nao e um dos quatro destaques.

    Casa pelo titulo da SECAO (que vem do analisador e e estavel) e, como
    reforco, pelo titulo do achado. Achado OK/INFO nunca e destaque: o relatorio
    destaca problema, e "esta tudo bem" nao disputa espaco com bloqueio ativo.
    """
    if achado.severity not in (Severity.CRIT, Severity.WARN):
        return None
    alvo = f"{secao} {achado.title}".lower()
    for chave, _peso, _rot, marcas in CATEGORIAS:
        if any(m in alvo for m in marcas):
            return chave
    return None


def coletar(result: CaseResult) -> list:
    """Todos os destaques do caso, do mais relevante para o menos."""
    out = []
    for s in result.sections:
        for f in s.findings:
            cat = classificar(s.title, f)
            if cat:
                out.append(Destaque(categoria=cat, secao=s.title, achado=f))
    out.sort(key=lambda d: d.ordem, reverse=True)
    return out


def top(result: CaseResult, n: int = TOP_PADRAO) -> list:
    """Os `n` destaques principais, sem repetir categoria.

    Sem o corte por categoria, um caso com 8 sessoes bloqueadas devolvia 2
    linhas do MESMO bloqueio como "top 2" - e o relatorio deixava de mencionar a
    regressao de SQL que estava logo atras. Um fato por categoria mostra a
    largura do problema, que e a informacao util no cabecalho do chamado.
    """
    vistas, escolhidos = set(), []
    for d in coletar(result):
        if d.categoria in vistas:
            continue
        vistas.add(d.categoria)
        escolhidos.append(d)
        if len(escolhidos) >= n:
            break
    return escolhidos


def por_categoria(result: CaseResult) -> dict:
    """{categoria: [destaques]} respeitando LIMITE_POR_CATEGORIA."""
    agrupado: dict = {}
    for d in coletar(result):
        fila = agrupado.setdefault(d.categoria, [])
        if len(fila) < LIMITE_POR_CATEGORIA.get(d.categoria, 2):
            fila.append(d)
    return agrupado


# ── textos para o relatorio ───────────────────────────────────
def causa_proposta(result: CaseResult, n: int = TOP_PADRAO) -> str:
    """Candidatos a causa raiz, SEMPRE marcados como automaticos.

    A marca nao e decoracao: este texto vai para o campo "Causa Raiz" de um
    relatorio que entra em chamado. Sem ela, a leitura da ferramenta ficaria
    indistinguivel de uma conclusao assinada pelo DBA - e, quando a heuristica
    errasse, o erro sairia com a assinatura dele.
    """
    ds = top(result, n)
    if not ds:
        return ""
    if len(ds) == 1:
        return f"{MARCA_AUTO} {ds[0].texto()}"
    corpo = "; ".join(f"({i + 1}) {d.texto()}" for i, d in enumerate(ds))
    return f"{MARCA_AUTO} candidatos por impacto: {corpo}"


def impacto_proposto(result: CaseResult) -> str:
    """Impacto medido, somando o que e somavel."""
    ds = coletar(result)
    if not ds:
        return ""
    partes = []
    seg = sum(d.segundos for d in ds if d.categoria == REGRESSAO)
    if seg:
        partes.append(f"{seg:.0f}s de DB time perdidos em regressao de SQL")
    bloq = [d for d in ds if d.categoria == BLOQUEIO]
    if bloq:
        pior = max(bloq, key=lambda d: d.segundos)
        partes.append("bloqueio com espera de ate "
                      f"{pior.segundos:.0f}s" if pior.segundos else
                      "sessoes bloqueadas no momento da coleta")
    carga = [d for d in ds if d.categoria == CARGA]
    if carga:
        pior = max(carga, key=lambda d: d.percentual)
        partes.append(f"carga +{pior.percentual:.0f}% sobre o baseline")
    return "; ".join(partes)


# ── contorno vindo dos playbooks ──────────────────────────────
# O playbook nao tem campo "contorno": tem `passos`, cada um com `risco`. O passo
# de contorno e, por definicao, o primeiro que MUDA algo - os anteriores sao
# leitura (diagnostico). Derivar assim, em vez de criar um campo novo em 28
# arquivos, mantem a KB como fonte unica e ja funciona para playbook futuro.
_RISCO_MUDA = ("mudanca", "destrutivo")


def contorno_do_playbook(pb: dict) -> dict:
    """Primeiro passo que muda o ambiente, com o que validar antes.

    Devolve {} quando o playbook e so de diagnostico. Isso e informacao, nao
    falha: dizer "sem contorno definido" e honesto, enquanto promover um passo
    de leitura a contorno entregaria um `SELECT` como se resolvesse o incidente.
    """
    for p in pb.get("passos") or []:
        if p.get("risco") in _RISCO_MUDA:
            return {"passo": p.get("titulo", ""),
                    "objetivo": p.get("objetivo", ""),
                    "comando": p.get("comando", ""),
                    "antes_de_executar": p.get("antes_de_executar", ""),
                    "risco": p.get("risco", ""),
                    "armadilha": p.get("armadilha", "")}
    return {}


def propostas(registros: list) -> list:
    """Propostas de preenchimento para a tela, a partir dos achados exportados.

    Recebe os registros do `<caso>_findings.json` (o painel ja os tem: nao
    reanalisa nada e nao toca em banco) e devolve, para cada destaque, o FATO e o
    CONTORNO tirado do playbook que casa com a secao.

    Cada proposta e uma caixinha na tela. Marcar a caixinha e um ato do DBA - por
    isso o texto aplicado nao leva a marca de automatico: ali ele escolheu. A
    origem fica registrada junto, para o historico do caso nao perder de onde
    veio a decisao.
    """
    from . import kb as kb_mod

    ds = []
    for reg in registros or []:
        f = Finding(title=reg.get("title", ""),
                    severity=reg.get("severity", ""),
                    impact=reg.get("impact", "") or "",
                    recommendation=reg.get("recommendation", "") or "")
        secao = reg.get("section", "") or ""
        cat = classificar(secao, f)
        if cat:
            ds.append(Destaque(categoria=cat, secao=secao, achado=f))
    ds.sort(key=lambda d: d.ordem, reverse=True)

    out, por_cat = [], {}
    for d in ds:
        n = por_cat.get(d.categoria, 0)
        if n >= LIMITE_POR_CATEGORIA.get(d.categoria, 2):
            continue
        por_cat[d.categoria] = n + 1
        pbs = kb_mod.sugerir_para_secao(d.secao)
        pb = pbs[0] if pbs else {}
        out.append({
            "id": f"{d.categoria}-{len(out)}",
            "categoria": d.categoria,
            "rotulo": d.rotulo,
            "severidade": d.severidade,
            "severidade_label": Severity.LABEL.get(d.severidade, ""),
            "secao": d.secao,
            "causa": d.texto(),
            "recomendacao": d.achado.recommendation,
            "playbook": ({"id": pb.get("id", ""),
                          "titulo": pb.get("titulo", ""),
                          "passos": len(pb.get("passos") or [])} if pb else {}),
            "contorno": contorno_do_playbook(pb) if pb else {},
        })
    return out


def texto_da_selecao(escolhidas: list) -> dict:
    """Causa raiz e solucao de contorno a partir das caixinhas marcadas.

    Junta o que o DBA selecionou em dois campos do Template Padrao. A solucao
    carrega o risco e o "antes de executar" de cada passo: recomendacao sem risco
    declarado, num texto que vai para chamado, e convite a executar no lugar
    errado.
    """
    if not escolhidas:
        return {"causa_raiz": "", "solucao": ""}
    if len(escolhidas) == 1:
        causa = escolhidas[0].get("causa", "")
    else:
        causa = "; ".join(f"({i + 1}) {p.get('causa', '')}"
                          for i, p in enumerate(escolhidas))
    partes = []
    for i, p in enumerate(escolhidas, 1):
        c = p.get("contorno") or {}
        pb = p.get("playbook") or {}
        if c:
            txt = f"({i}) {c.get('passo', '')}"
            if c.get("risco"):
                txt += f" [risco: {c['risco']}]"
            if c.get("antes_de_executar"):
                txt += f" - validar antes: {c['antes_de_executar']}"
            if pb.get("titulo"):
                txt += f" (playbook: {pb['titulo']})"
        elif pb.get("titulo"):
            # Playbook de diagnostico: nao tem passo de contorno. Dizer isso e
            # melhor que oferecer um passo de leitura como se fosse solucao.
            txt = (f"({i}) Seguir o playbook '{pb['titulo']}' - diagnostico, "
                   "sem passo de contorno definido")
        else:
            txt = f"({i}) {p.get('recomendacao', '') or 'a definir'}"
        partes.append(txt)
    return {"causa_raiz": causa, "solucao": " ".join(partes)}


def markdown(result: CaseResult) -> str:
    """A secao "Destaques automaticos" do relatorio."""
    agrupado = por_categoria(result)
    if not agrupado:
        return ("- Nenhum achado de carga, regressao, SQL pesado ou bloqueio "
                "nesta coleta. (Se o sintoma existe, a evidencia dessas "
                "categorias pode nao ter sido coletada - confira a lista de "
                "*Dados coletados*.)")
    linhas = []
    for chave, _peso, rotulo, _marcas in CATEGORIAS:
        fila = agrupado.get(chave)
        if not fila:
            continue
        linhas.append(f"**{rotulo}**")
        for d in fila:
            sev = Severity.LABEL.get(d.severidade, "")
            linhas.append(f"- [{sev}] {d.texto()}")
            if d.achado.recommendation:
                linhas.append(f"  - Acao: {d.achado.recommendation}")
        linhas.append("")
    return "\n".join(linhas).strip()
