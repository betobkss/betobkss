"""
PLANO DE ACAO priorizado.

Um caso real chega com dezenas de achados. Lista-los por severidade nao basta:
"tablespace em 91%" e "6 blocos corrompidos" sao ambos criticos, mas nao se
tratam na mesma ordem. Este modulo responde a unica pergunta que o DBA faz na
frente do relatorio: **o que eu faco primeiro?**

Criterio de ordenacao (nesta ordem de peso):

1. **Classe da falha** - dado corrompido ou banco irrecuperavel vem antes de
   qualquer performance. Nao adianta o banco estar rapido se o dado se perdeu.
2. **Severidade** - critico antes de atencao.
3. **Previsibilidade** - falha com data marcada (senha que expira, tablespace
   que enche) sobe: e a que da para evitar sem incidente.

Cada acao sai com **risco** e **como validar**, porque recomendacao sem risco
declarado e convite a executar no lugar errado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import CaseResult, Severity

# ── Classes de falha ──────────────────────────────────────────
# peso, rotulo e as secoes que caem em cada classe. A ordem aqui E a ordem de
# tratamento: integridade primeiro, performance por ultimo.
CLASSES = [
    ("integridade", 100, "Integridade do dado",
     ("blocos corrompidos", "datafiles fora de online", "corrup")),
    # Erro interno / archiver travado / evicao vem logo depois de corrupcao:
    # sem o dicionario de erros, o ORA-00600 caia em "performance" e era
    # tratado por ultimo.
    ("erro_do_banco", 95, "Erro do banco",
     ("alert log", "erros ora-", "erro ora")),
    ("recuperabilidade", 90, "Recuperabilidade",
     ("destinos de archive", "idade do backup", "gap de archivelog",
      "rman", "backup", "fra")),
    ("disponibilidade", 80, "Disponibilidade",
     ("contas expirando", "undo", "bloqueio", "lock", "tablespace",
      "espaco", "limites de recurso", "resource", "data guard", "dataguard",
      "goldengate", "jobs do scheduler")),
    ("infraestrutura", 60, "Infraestrutura",
     ("recursos do host", "asm", "i/o", "io ", "interconnect", "exadata")),
    # Conformidade e risco aceito, nao incidente em curso: entra depois do que
    # ja esta doendo. Mas os desvios CRITICOS (statistics_level=BASIC, banco
    # sem archivelog) sobem sozinhos pelo peso da severidade.
    ("conformidade", 20, "Conformidade",
     ("conformidade", "best practice", "praticas oracle")),
    # SEMPRE por ultimo: `classificar` usa CLASSES[-1] como padrao para o que
    # nao casa com nenhuma marca.
    ("performance", 40, "Performance",
     ()),
]

PESO_SEV = {Severity.CRIT: 100, Severity.WARN: 40, Severity.INFO: 5,
            Severity.OK: 0}

# Horizonte por classe+severidade. "agora" = nao espere janela.
HORIZONTES = ("agora", "hoje", "esta semana", "planejar")

# ── Risco da acao ─────────────────────────────────────────────
# Deriva do que a recomendacao manda fazer. Serve para o DBA saber, antes de
# ler, se aquilo exige janela e aprovacao (Matriz de Risco do DBA Master).
_ALTO = re.compile(
    r"\b(recover|restore|blockrecover|drop|kill|shutdown|startup|failover|"
    r"switchover|rebuild|truncate|delete)\b", re.I)
_MEDIO = re.compile(
    r"\b(alter\s+(system|database|user|tablespace|session)|backup\s+database|"
    r"gather|dbms_stats|resize|autoextend|add\s+datafile|amplie|aumente)\b",
    re.I)

RISCOS = {"nenhum": 0, "baixo": 1, "medio": 2, "alto": 3}


def classificar(origem: str) -> tuple:
    """(chave, peso, rotulo) da classe de falha a partir do titulo da secao."""
    t = (origem or "").lower()
    for chave, peso, rotulo, marcas in CLASSES:
        if marcas and any(m in t for m in marcas):
            return chave, peso, rotulo
    chave, peso, rotulo, _ = CLASSES[-1]
    return chave, peso, rotulo


def avaliar_risco(recomendacao: str) -> str:
    """Risco de EXECUTAR a recomendacao (nao o risco de ignorar)."""
    r = recomendacao or ""
    if _ALTO.search(r):
        return "alto"
    if _MEDIO.search(r):
        return "medio"
    return "nenhum"


def _horizonte(classe: str, severidade: str) -> str:
    if severidade == Severity.CRIT:
        if classe in ("integridade", "recuperabilidade", "disponibilidade"):
            return "agora"
        return "hoje"
    if severidade == Severity.WARN:
        return "esta semana"
    return "planejar"


def _previsivel(titulo: str, detalhe: str) -> bool:
    """Falha com data marcada: da para agir antes de virar incidente."""
    t = f"{titulo} {detalhe}".lower()
    return any(k in t for k in ("expira", "venceu", "vencida", "dias",
                                "projecao", "estimativa", "esgota"))


def _validacao(classe: str, caso: str, conn: str) -> str:
    recoleta = (f"COLETAR.py {caso} --conn {conn or '<conexao>'} --completo"
                if caso else "recolete o caso")
    especifico = {
        "integridade": "Confirme zero linhas em v$database_block_corruption "
                       "e datafiles ONLINE em v$datafile_header.",
        "erro_do_banco": "Confirme que o codigo ORA- parou de aparecer no "
                         "alert log depois da acao (e nao apenas que o sintoma "
                         "sumiu).",
        "recuperabilidade": "Confirme STATUS=VALID em v$archive_dest_status e "
                            "o novo RPO por datafile (v$backup_datafile).",
        "disponibilidade": "Reconecte a aplicacao e confirme o indicador que "
                           "gerou o achado (expiracao, espaco, bloqueio).",
        "infraestrutura": "Meca de novo o load/memoria do host (v$osstat) na "
                          "mesma faixa de horario.",
        "performance": "Compare o DB time do SQL/evento antes e depois, na "
                       "mesma janela de carga.",
        "conformidade": "Releia o parametro em V$PARAMETER (ou a propriedade em "
                        "V$DATABASE) e confirme que ficou no valor recomendado "
                        "- inclusive apos o proximo restart.",
    }
    return f"{especifico.get(classe, '')} Depois, `{recoleta}` e confira que o achado saiu do relatorio."


@dataclass
class Acao:
    """Uma acao do plano, ja priorizada."""
    ordem: int = 0
    titulo: str = ""
    o_que_fazer: str = ""
    por_que: str = ""
    severidade: str = Severity.INFO
    classe: str = "performance"
    classe_rotulo: str = "Performance"
    origens: list = field(default_factory=list)
    horizonte: str = "planejar"
    risco: str = "nenhum"
    prioridade: int = 0
    validar: str = ""

    @property
    def origem(self) -> str:
        return ", ".join(self.origens[:3])


def montar_plano(result: CaseResult, limite: int = 0) -> list:
    """Constroi o plano priorizado a partir dos achados do caso.

    Acoes com a MESMA recomendacao viram uma so (o DBA nao vai rodar duas
    vezes o mesmo comando porque dois achados pediram).
    """
    meta = result.meta or {}
    conn = meta.get("conn", "")
    por_rec: dict = {}

    for sec in result.sections:
        for f in sec.findings:
            if f.severity not in (Severity.CRIT, Severity.WARN):
                continue
            if not f.recommendation:
                continue
            classe, peso_classe, rotulo = classificar(sec.title)
            chave = re.sub(r"\s+", " ", f.recommendation.strip().lower())[:120]
            prio = peso_classe + PESO_SEV.get(f.severity, 0)
            if _previsivel(f.title, f.detail):
                prio += 5

            a = por_rec.get(chave)
            if a is None:
                por_rec[chave] = Acao(
                    titulo=f.title,
                    o_que_fazer=f.recommendation,
                    por_que=(f.detail or f.impact or "").strip(),
                    severidade=f.severity,
                    classe=classe, classe_rotulo=rotulo,
                    origens=[sec.title],
                    horizonte=_horizonte(classe, f.severity),
                    risco=avaliar_risco(f.recommendation),
                    prioridade=prio,
                    validar=_validacao(classe, result.case, conn),
                )
            else:
                # Achado repetido: fica com a maior prioridade e registra a
                # outra origem - a acao e a mesma, o motivo e mais forte.
                if sec.title not in a.origens:
                    a.origens.append(sec.title)
                if prio > a.prioridade:
                    a.prioridade = prio
                    a.severidade = f.severity
                    a.classe, a.classe_rotulo = classe, rotulo
                    a.horizonte = _horizonte(classe, f.severity)

    ordem_h = {h: i for i, h in enumerate(HORIZONTES)}
    acoes = sorted(
        por_rec.values(),
        key=lambda a: (-a.prioridade, ordem_h.get(a.horizonte, 9),
                       RISCOS.get(a.risco, 0), a.titulo))
    for i, a in enumerate(acoes, 1):
        a.ordem = i
    return acoes[:limite] if limite else acoes


def resumo_plano(acoes: list) -> str:
    """Uma linha honesta sobre o tamanho do trabalho."""
    if not acoes:
        return "Sem acao pendente: nenhum achado critico ou de atencao."
    agora = sum(1 for a in acoes if a.horizonte == "agora")
    alto = sum(1 for a in acoes if a.risco == "alto")
    partes = [f"{len(acoes)} acao(oes) priorizada(s)"]
    if agora:
        partes.append(f"{agora} para AGORA")
    if alto:
        partes.append(f"{alto} de risco alto (exige janela e aprovacao)")
    return "; ".join(partes) + "."


def agrupar_por_horizonte(acoes: list) -> list:
    """Agrupa as acoes por horizonte: [(horizonte, [acoes]), ...].

    Existe porque `montar_plano` ordena por PRIORIDADE primeiro e horizonte
    depois - o que e correto para a fila de trabalho, mas faz o horizonte se
    repetir na sequencia. Quem consumia a lista imprimindo um cabecalho a cada
    troca de valor produzia "HOJE ... AGORA ... HOJE" no mesmo plano: em 12 dos
    37 casos reais da frota, e num deles o bloco HOJE aparecia ANTES do AGORA.
    Para quem le "o que eu faco primeiro?", isso inverte a resposta.

    Os grupos saem na ordem de HORIZONTES (agora -> planejar); dentro do grupo,
    a ordem de prioridade original e preservada. Horizonte sem acao nao vira
    grupo vazio.
    """
    por_h: dict = {}
    for a in acoes:
        por_h.setdefault(a.horizonte, []).append(a)
    # horizonte desconhecido (defensivo) vai para o fim, em ordem estavel
    conhecidos = [(h, por_h[h]) for h in HORIZONTES if h in por_h]
    extras = [(h, v) for h, v in por_h.items() if h not in HORIZONTES]
    return conhecidos + extras


def plano_markdown(result: CaseResult, limite: int = 12) -> str:
    """Plano em Markdown para o relatorio do Template Padrao."""
    acoes = montar_plano(result, limite=limite)
    if not acoes:
        return ("- Nenhuma acao priorizada: sem achado critico ou de atencao "
                "com recomendacao.")
    linhas = [f"_{resumo_plano(acoes)}_", ""]
    for horizonte, grupo in agrupar_por_horizonte(acoes):
        linhas.append(f"**{horizonte.upper()}**")
        linhas.append("")
        for a in grupo:
            linhas.append(
                f"{a.ordem}. **[{a.classe_rotulo}] {a.titulo}** "
                f"(risco de executar: {a.risco})")
            linhas.append(f"   - **Fazer**: {a.o_que_fazer}")
            if a.por_que:
                linhas.append(f"   - **Porque**: {a.por_que}")
            linhas.append(f"   - **Validar**: {a.validar}")
            linhas.append("")
    return "\n".join(linhas).rstrip()
