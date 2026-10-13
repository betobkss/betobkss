"""
auditoria.py - invariantes de DIAGNOSTICO sobre a evidencia ja coletada.

Por que existe: a bateria (`TESTE_HOMOLOG.py`) sempre mediu a COLETA - se o
processo terminou, se gravou os arquivos, se alguma query quebrou no banco. Ela
nunca olhou o que o framework CONCLUI da evidencia nova. E e ali que moram os
defeitos que importam: nas duas ultimas varreduras, todos os bugs encontrados
(codigo ORA- do titulo virando achado, marca `temp` casando em "tempo", plano
de execucao lido como ocupacao de espaco) sairam de sondas rodadas a mao DEPOIS
da bateria, por alguem que sabia o que procurar.

Este modulo transforma aquelas sondas em invariante executavel. Sao afirmacoes
sobre CLASSES de defeito, nao sobre casos conhecidos - foi assim que os tres
acima apareceram, e e a unica forma de pegar o quarto.

Usado por:
  - `TESTE_HOMOLOG.py --auditar` (roda junto da bateria, sobre a coleta nova);
  - `tests/test_corpus_real.py` (roda na suite, sobre a evidencia da maquina).

Fonte unica de proposito: um invariante que vale na suite e nao vale na bateria
(ou o contrario) e um invariante em que ninguem confia.

NADA aqui acessa banco. Le arquivo ja gravado, e nao escreve nada.
"""
from __future__ import annotations

import datetime as _dt
import glob
import hashlib
import json
import os
import re

from . import erros as dic_erros
from . import raciocinio
from . import saida as leitor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR_PADRAO = os.path.join(BASE_DIR, "troubleshooting")

# Arquivos da pasta do caso que NAO sao evidencia do banco (registro nosso).
_NAO_E_EVIDENCIA = ("coleta.log",)
_EXT = (".txt", ".lst", ".out", ".html", ".htm")


def evidencias(caso: str, cases_dir: str = "") -> list:
    d = os.path.join(cases_dir or CASES_DIR_PADRAO, caso)
    if not os.path.isdir(d):
        return []
    return sorted(p for p in glob.glob(os.path.join(d, "*"))
                  if os.path.isfile(p)
                  and os.path.basename(p).lower() not in _NAO_E_EVIDENCIA
                  and p.lower().endswith(_EXT))


def coletado_em(caso: str, cases_dir: str = ""):
    """Data da coleta, do meta.json. Referencia para medir IDADE.

    Sem ela, a idade de estatistica e a deteccao de "mudanca recente" sao
    medidas contra o relogio de hoje, e o tempo decorrido desde a coleta entra
    na conta - a mesma evidencia passa a dizer outra coisa conforme o
    calendario. Ver `saida.interpretar(referencia=...)`.
    """
    p = os.path.join(cases_dir or CASES_DIR_PADRAO, caso, "meta.json")
    try:
        with open(p, encoding="utf-8") as fh:
            quando = (json.load(fh).get("collected_at") or "")[:10]
        return _dt.datetime.strptime(quando, "%Y-%m-%d").date()
    except (OSError, ValueError, AttributeError, TypeError):
        return None


# Cache da INTERPRETACAO, por (caminho, mtime, referencia).
#
# Sem ele, cada invariante relia e reinterpretava os mesmos arquivos: 6
# invariantes x 26 casos x ~60 arquivos = a auditoria levava 2,4 min na suite.
# `interpretar` e funcao pura de (texto, pergunta, referencia), entao o
# resultado e cacheavel sem risco - e o mtime no lugar da chave faz o cache se
# invalidar sozinho quando a coleta regrava a evidencia, que e exatamente o que
# acontece na bateria.
#
# Suite lenta e suite que alguem pula, e invariante que ninguem roda nao
# protege nada.
_CACHE: dict = {}
MAX_CACHE = 4000


def limpar_cache() -> None:
    """Esvazia o cache de interpretacao (usado por teste que troca o leitor)."""
    _CACHE.clear()


def _ler(caminho: str, referencia=None) -> tuple:
    try:
        chave = (caminho, os.stat(caminho).st_mtime_ns, referencia)
    except OSError:
        chave = None
    if chave is not None and chave in _CACHE:
        return _CACHE[chave]

    with open(caminho, encoding="utf-8", errors="replace") as fh:
        texto = fh.read()
    pergunta = os.path.basename(caminho).rsplit(".", 1)[0].replace("_", " ")
    saido = (texto, leitor.interpretar(texto, pergunta, referencia=referencia))
    if chave is not None:
        if len(_CACHE) >= MAX_CACHE:
            _CACHE.clear()      # teto simples: a auditoria e de vida curta
        _CACHE[chave] = saido
    return saido


# ── invariantes ───────────────────────────────────────────────
# Cada um devolve lista de violacoes (string vazia = tudo certo). A assinatura
# recebe o caso inteiro porque alguns precisam cruzar arquivos.

def inv_ora_vem_dos_dados(caso: str, cases_dir: str = "") -> list:
    """Codigo ORA- citado num achado tem de estar nos DADOS da saida.

    O subtitulo de cada check vem do `mcp/catalog.json`: um `ORA-` ali declara
    o que o check PROCURA, nunca o que ele ACHOU. Ler o titulo como ocorrencia
    INVERTE o diagnostico - `deadlocks.txt` com a tabela vazia reportava
    "Deadlock detectado".
    """
    ref = coletado_em(caso, cases_dir)
    fora = []
    for p in evidencias(caso, cases_dir):
        texto, d = _ler(p, ref)
        nos_dados = set(dic_erros.extrair(leitor._texto_dos_dados(texto)))
        for a in d["achados"]:
            for cod in dic_erros.extrair(a["title"]):
                if cod not in nos_dados:
                    fora.append(f"{os.path.basename(p)}: achado cita {cod}, "
                                f"ausente dos dados (so no titulo/cabecalho)")
    return fora


def inv_espera_nao_sustenta_espaco(caso: str, cases_dir: str = "") -> list:
    """Espera dominante nao e prova de tablespace cheia.

    A marca `temp` casava em "tempo" e o impacto de toda espera e "X% do
    tempo": qualquer caso de performance sustentava uma hipotese de classe
    PARADA, e o plantao era mandado conferir espaco durante incidente de lock.
    """
    ref = coletado_em(caso, cases_dir)
    fora = []
    for p in evidencias(caso, cases_dir):
        _, d = _ler(p, ref)
        esperas = [a for a in d["achados"]
                   if "espera" in a["title"]
                   or "% do tempo" in (a.get("impact") or "")]
        if not esperas:
            continue
        for h in raciocinio.avaliar(esperas):
            if h["id"] == "espaco" and h["veredito"] in ("sustentada",
                                                         "possivel"):
                fora.append(f"{os.path.basename(p)}: espera sustentou "
                            f"'espaco' por {[x['marca'] for x in h['a_favor']]}")
    return fora


def inv_plano_nao_e_tabela(caso: str, cases_dir: str = "") -> list:
    """Saida de `dbms_xplan` / SQL Monitor nao e tabela de dados.

    A moldura usa '|', o cabecalho tem '%' embutido (`Cost (%CPU)`) e os
    predicados carregam nomes de coluna e literais SQL. Isso produzia
    "tablespace |*2 | HASH em 99% de uso" em CRITICO - e, pior, o inverso:
    "espaco dentro do esperado", que e a marca de REFUTACAO, fazendo um dump de
    plano DESCARTAR uma tablespace realmente cheia.
    """
    ref = coletado_em(caso, cases_dir)
    fora = []
    for p in evidencias(caso, cases_dir):
        texto, d = _ler(p, ref)
        if not leitor._e_saida_de_plano(texto):
            continue
        for a in d["achados"]:
            if a.get("leitor") in ("espaco", "estatisticas"):
                fora.append(f"{os.path.basename(p)}: saida de PLANO gerou "
                            f"achado de {a['leitor']}: {a['title'][:70]}")
    return fora


def inv_leitor_nao_explode(caso: str, cases_dir: str = "") -> list:
    """Nenhum arquivo pode derrubar o leitor.

    Ele roda em plantao sobre saida que ninguem revisou - e o executor agora o
    alimenta com o que o banco devolveu, sem humano no meio.
    """
    ref = coletado_em(caso, cases_dir)
    fora = []
    for p in evidencias(caso, cases_dir):
        try:
            _, d = _ler(p, ref)
            raciocinio.avaliar(d["achados"])
        except Exception as e:
            fora.append(f"{os.path.basename(p)}: {type(e).__name__}: {e}")
    return fora


def inv_achado_declara_porque(caso: str, cases_dir: str = "") -> list:
    """Achado sem 'porque' e conclusao sem lastro.

    O DBA precisa poder discordar do que foi entendido, e para isso tem de ver
    de onde a leitura saiu.
    """
    ref = coletado_em(caso, cases_dir)
    fora = []
    for p in evidencias(caso, cases_dir):
        _, d = _ler(p, ref)
        for a in d["achados"]:
            if not a.get("porque"):
                fora.append(f"{os.path.basename(p)}: sem porque: "
                            f"{a['title'][:70]}")
            if a.get("severity") not in ("crit", "warn", "info", "ok"):
                fora.append(f"{os.path.basename(p)}: severidade invalida "
                            f"{a.get('severity')!r}")
    return fora


def inv_analise_idempotente(caso: str, cases_dir: str = "") -> list:
    """A mesma evidencia, analisada duas vezes, da o mesmo diagnostico.

    Se nao der, nenhum numero do relatorio se defende - e a comparacao entre
    execucoes, que e o que o framework promete, deixa de significar algo.
    """
    import TROUBLESHOOT

    def retrato():
        r = TROUBLESHOOT.analisar_caso(caso, cases_dir=cases_dir,
                                       expandir=False, quieto=True)
        d = {"score": r.score, "sev": r.severity,
             "secoes": [[s.title, [[f.severity, f.title, f.impact or ""]
                                   for f in s.findings]]
                        for s in r.sections]}
        bruto = json.dumps(d, ensure_ascii=False)
        return hashlib.sha256(bruto.encode()).hexdigest()[:16], r.score

    try:
        h1, s1 = retrato()
        h2, s2 = retrato()
    except Exception as e:
        return [f"analise explodiu: {type(e).__name__}: {e}"]
    if h1 != h2:
        return [f"diagnostico MUDOU entre passadas (score {s1} -> {s2}, "
                f"hash {h1} != {h2})"]
    return []


def inv_evidencia_da_coleta_certa(caso: str, cases_dir: str = "") -> list:
    """Todo arquivo do caso e da coleta que o `meta.json` declara?

    A pasta do caso e reaproveitada entre coletas. Quando um check falhava, o
    coletor saia sem escrever e o arquivo da coleta ANTERIOR ficava - lido
    depois como evidencia de agora, com o carimbo de hoje.

    Medido em HOMOLOG_APPHO114 (bateria de 13/09): 63 arquivos do dia e DOIS de
    tres dias antes, que eram exatamente os dois checks que falharam. O
    `collected_at` dizia 13/09.

    A causa foi corrigida no coletor (`_descartar_obsoleto`). Este invariante e
    a REDE: pega caso antigo que ja esta em disco com a mistura, e pegaria uma
    reintroducao por outro caminho - o coletor tem varios pontos de saida.

    Tolerancia de um dia: coleta iniciada 23h50 e terminada 00h10 e uma coleta
    so, e reprovar nisso ensinaria o time a ignorar o aviso.
    """
    ref = coletado_em(caso, cases_dir)
    if ref is None:
        return []       # sem meta nao ha o que comparar
    fora = []
    for p in evidencias(caso, cases_dir):
        try:
            quando = _dt.date.fromtimestamp(os.path.getmtime(p))
        except OSError:
            continue
        dias = (ref - quando).days
        if dias > 1:
            fora.append(f"{os.path.basename(p)}: gravado em {quando} mas o "
                        f"meta.json diz coleta de {ref} ({dias} dias de "
                        f"diferenca) - evidencia de coleta ANTERIOR")
    return fora


INVARIANTES = (
    ("ora_dos_dados", "codigo ORA- vem dos dados, nao do titulo",
     inv_ora_vem_dos_dados),
    ("espera_x_espaco", "espera dominante nao sustenta falta de espaco",
     inv_espera_nao_sustenta_espaco),
    ("plano_x_tabela", "saida de plano nao e lida como tabela de dados",
     inv_plano_nao_e_tabela),
    ("leitor_robusto", "nenhuma evidencia derruba o leitor",
     inv_leitor_nao_explode),
    ("achado_com_porque", "todo achado declara o porque e severidade valida",
     inv_achado_declara_porque),
    ("idempotente", "a mesma evidencia da o mesmo diagnostico",
     inv_analise_idempotente),
)

# Invariantes sobre a COLETA, nao sobre a leitura. Separados porque o publico e
# outro: aqui a afirmacao e "esta coleta esta integra", e so faz sentido sobre
# coleta RECEM-FEITA. A bateria roda os dois conjuntos; a suite roda so os de
# cima.
#
# A razao e concreta: um caso coletado meses atras, antes do conserto do
# `_descartar_obsoleto`, tem a mistura em disco para sempre. Reprovar a suite
# por causa dele deixaria o vermelho permanente por uma condicao de DADO que
# nenhuma mudanca de codigo resolve - e suite cronicamente vermelha e suite que
# ninguem le. Na bateria e o oposto: ali a coleta acabou de acontecer, entao
# arquivo de outra data significa que o coletor errou AGORA.
INVARIANTES_COLETA = (
    ("evidencia_da_coleta", "nenhum arquivo e de uma coleta anterior",
     inv_evidencia_da_coleta_certa),
)


# ── sinais para revisao humana (nao sao violacao) ─────────────
_RE_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def sinais(caso: str, cases_dir: str = "") -> dict:
    """O que merece OLHO humano, sem ser violacao de invariante.

    Separado de proposito: invariante quebrado e defeito e reprova; sinal e
    material para a proxima varredura. Misturar os dois faz o time ignorar os
    dois.
    """
    ref = coletado_em(caso, cases_dir)
    crits, sem_dicionario, leitores = {}, set(), {}
    for p in evidencias(caso, cases_dir):
        texto, d = _ler(p, ref)
        nome = os.path.basename(p)
        for a in d["achados"]:
            leitores[a.get("leitor") or "(erros/eventos)"] = leitores.get(
                a.get("leitor") or "(erros/eventos)", 0) + 1
            if a["severity"] == "crit":
                # Frase normalizada: agrupa o PADRAO, nao o valor.
                crits[(nome, _RE_NUM.sub("N", a["title"]))] = crits.get(
                    (nome, _RE_NUM.sub("N", a["title"])), 0) + 1
        for cod in dic_erros.extrair(leitor._texto_dos_dados(texto)):
            if not dic_erros.conhecido(cod):
                sem_dicionario.add(cod)
    return {"crits": crits, "ora_sem_dicionario": sorted(sem_dicionario),
            "por_leitor": leitores}


def auditar(casos, cases_dir: str = "", coleta_nova: bool = False) -> dict:
    """Roda os invariantes e junta os sinais. Nao acessa banco.

    `coleta_nova=True` acrescenta os `INVARIANTES_COLETA`, que so fazem sentido
    logo depois de coletar - e o que a bateria usa. A suite roda sem eles, para
    nao ficar vermelha por causa de um caso antigo que tem a mistura em disco
    de antes do conserto.
    """
    violacoes, por_caso, agregado = [], {}, {"crits": {},
                                             "ora_sem_dicionario": set(),
                                             "por_leitor": {}}
    for caso in casos:
        if not evidencias(caso, cases_dir):
            continue
        falhas = {}
        for cid, desc, fn in (INVARIANTES + (INVARIANTES_COLETA
                                             if coleta_nova else ())):
            try:
                ruins = fn(caso, cases_dir)
            except Exception as e:
                ruins = [f"o invariante explodiu: {type(e).__name__}: {e}"]
            if ruins:
                falhas[cid] = ruins
                for r in ruins:
                    violacoes.append((caso, cid, desc, r))
        s = sinais(caso, cases_dir)
        por_caso[caso] = {"violacoes": falhas, "sinais": s}
        for k, v in s["crits"].items():
            agregado["crits"][k] = agregado["crits"].get(k, 0) + v
        agregado["ora_sem_dicionario"] |= set(s["ora_sem_dicionario"])
        for k, v in s["por_leitor"].items():
            agregado["por_leitor"][k] = agregado["por_leitor"].get(k, 0) + v
    agregado["ora_sem_dicionario"] = sorted(agregado["ora_sem_dicionario"])
    return {"violacoes": violacoes, "por_caso": por_caso,
            "agregado": agregado, "casos": len(por_caso)}
