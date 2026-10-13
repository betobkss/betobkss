"""
Serie temporal da frota, acumulada a partir da evidencia que ja esta no disco.

Por que existe: o framework coleta bem e analisa bem cada foto, mas o horizonte
de cada analise e o que o banco consegue lembrar - e o AWR lembra pouco. Com
`DBA_HIST` a janela tipica e de 8 a 15 dias, e a previsao de crescimento
(`tablespace_forecast`) exige **Diagnostics Pack**. Resultado: a pergunta que
um DBA mais faz em capacidade - "desde quando isso vem subindo, e quando
estoura?" - ficava sem resposta em banco nao licenciado.

A virada e simples: cada coleta guardada em `troubleshooting/<caso>/` JA e um
ponto no tempo. Vinte e sete coletas sao vinte e sete pontos. Acumulando o que
o framework mediu, o horizonte deixa de ser a retencao do AWR e passa a ser a
idade do projeto - sem agente, sem daemon, sem porta aberta e, no caso de
espaco, **sem pacote de licenca**: o `space.txt` sai de `dba_data_files`.

DUAS CAMADAS, a mesma separacao de `ambientes.py`:

1. **series** - DERIVADO da evidencia. Pode ser jogado fora e regerado a
   qualquer momento por `HISTORICO.py --atualizar`. Nunca edite a mao.
2. **marcos** - o que aconteceu e nenhum SELECT explica: "storage migrado",
   "entrou o batch da folha", "subimos o RU". SOBREVIVE a regeracao.

A camada 2 nao e enfeite. O analisador de carga aponta o DIA em que a carga
mudou de patamar; sem um lugar para registrar o que houve naquele dia, a mesma
investigacao e refeita do zero no mes seguinte. O marco transforma "salto em
2026-09-07" em "salto em 2026-09-07 - batch novo da folha", e essa e a
diferenca entre um grafico e uma explicacao.

O QUE ESTE MODULO NAO E: monitoracao. Ele nao vigia nada, nao alarma e so sabe
o que alguem coletou.

E O QUE ELE AINDA NAO FAZ, medido na frota de hoje: das 224 series de espaco,
**211 tem UM unico ponto** - porque cada servidor foi coletado essencialmente
uma vez. Com um ponto nao existe tendencia, e o modulo devolve {} em vez de
inventar uma reta. Ou seja: a previsao de capacidade sem licenca esta
implementada e correta, mas **so passa a responder depois de umas quatro
coletas espacadas** do mesmo ambiente. O valor aqui e prospectivo, e dize-lo e
parte do trabalho - prometer previsao hoje seria vender o que o dado nao
sustenta.

A familia `carga` e a excecao que ja funciona: o `workload_daily` traz 14 dias
datados DENTRO de cada coleta, entao todas as 22 series da frota ja tem amostra
suficiente. Acumular coletas ai serve para esticar o horizonte para alem da
retencao do AWR - hoje a uniao das coletas cobre 50 dias contra os 15 de uma
coleta isolada.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

from .analyzers.base import (
    coletado_em,
    find_col,
    num,
    parse_fixed_width_tables,
    read_text,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(BASE_DIR, "historico.json")
CASOS_DIR = os.path.join(BASE_DIR, "troubleshooting")

DESCRICAO = (
    "Serie temporal da frota. O bloco 'series' de cada ambiente e DERIVADO da "
    "evidencia em troubleshooting/ e e regerado por HISTORICO.py --atualizar - "
    "nao edite a mao. O bloco 'marcos' e o que aconteceu no ambiente e nenhum "
    "SELECT explica; ele sobrevive a regeracao: use HISTORICO.py --marco."
)

# ── de onde sai cada serie ────────────────────────────────────
# (familia, arquivo, aliases do NOME, aliases do VALOR, aliases do DIA)
#
# Quando a tupla do DIA e vazia, a evidencia e um valor PONTUAL (quanto a
# tablespace ocupa agora) e a data do ponto e a data do proprio ARQUIVO, nao a
# da coleta. A distincao importa: quando um check falha, o framework apaga o
# arquivo - mas um residuo de coleta antiga que sobreviva entra com a SUA data
# e vira um ponto no passado, em vez de contaminar a medicao de hoje.
#
# `carga` e o caso oposto: o `workload_daily` ja traz 14 dias datados dentro da
# propria saida, entao o dia vem da linha.
FONTES = (
    ("carga", "workload_daily.txt",
     ("metrica", "modulo"), ("valor",), ("dia", "data")),
    ("espaco", "space.txt",
     ("tablespace",), ("pct_used",), ()),
    ("fra", "fra.txt",
     ("file_type",), ("pct_used",), ()),
    ("asm", "asm.txt",
     ("name",), ("pct_used",), ()),
)

# Minimos para a tendencia valer alguma coisa. Reta sobre dois pontos passa
# sempre e nao informa nada; reta sobre pontos de um mesmo dia extrapola ruido
# de medicao para o infinito.
MIN_PONTOS = 4
MIN_DIAS_JANELA = 7


def _agora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _chave(caso_dir: str) -> str:
    """Identidade do ambiente - a MESMA regra do `ambientes.py`.

    O nome da conexao, e nao o db_name, porque e por ele que o DBA chama o
    banco no resto do projeto (stringConexao/, servidores.txt). Divergir aqui
    faria o inventario e o historico falarem de "ambientes" diferentes e nao
    cruzarem.

    Devolve "" quando o caso nao se identifica, e quem chama PULA. Nao cai para
    o nome da pasta de proposito, por dois motivos:

    1. Nome de pasta e nome de CASO, nao de ambiente. `HC_APPHO600` e
       `HOMOLOG_APPHO600` sao dois casos do MESMO banco; usar a pasta como
       chave partiria a serie do servidor em duas e cada metade teria metade
       dos pontos - justamente o que arruina uma tendencia.
    2. Pasta sem `meta.json` nem `topology.json` nao e um ambiente da frota: e
       o `DEMO`, e a pasta `teste`, e o zip que alguem descompactou ali. Dado
       sintetico entrando numa serie temporal contamina previsao de capacidade
       de um jeito que ninguem percebe depois.

    O `ambientes.py` tem o mesmo efeito por outro caminho: ele varre
    `topology.json`, entao pasta sem topologia nunca entra.
    """
    meta = _json(os.path.join(caso_dir, "meta.json"))
    topo = _json(os.path.join(caso_dir, "topology.json"))
    return str(meta.get("conn") or topo.get("db_name") or "").strip().upper()


def _dia_do_arquivo(path: str) -> str:
    return coletado_em(path).strftime("%Y-%m-%d")


def _pontos_do_arquivo(path: str, al_nome, al_valor, al_dia) -> list:
    """[(nome, dia, valor)] extraidos de uma saida de largura fixa."""
    fora = []
    dia_arquivo = _dia_do_arquivo(path)
    for headers, rows in parse_fixed_width_tables(read_text(path)):
        ci_nome = find_col(headers, *al_nome)
        ci_val = find_col(headers, *al_valor)
        ci_dia = find_col(headers, *al_dia) if al_dia else -1
        if ci_nome < 0 or ci_val < 0:
            continue
        if al_dia and ci_dia < 0:
            # A fonte promete coluna de dia e a saida nao tem: datar tudo pela
            # data do arquivo colapsaria 14 dias num ponto so e inventaria uma
            # serie que nao foi medida.
            continue
        for r in rows:
            if ci_nome >= len(r) or ci_val >= len(r):
                continue
            nome = str(r[ci_nome]).strip()
            valor = num(r[ci_val])
            if not nome or valor is None:
                continue
            dia = (str(r[ci_dia]).strip() if 0 <= ci_dia < len(r)
                   else dia_arquivo)
            if len(dia) >= 10:
                fora.append((nome, dia[:10], valor))
    return fora


def coletar(casos_dir: str = "") -> dict:
    """{ambiente: {familia: {nome: {dia: valor}}}} a partir da evidencia.

    Quando duas coletas trazem o MESMO (ambiente, familia, nome, dia) - o que e
    rotina, porque as janelas de 14 dias se sobrepoem - vence a evidencia mais
    NOVA. O criterio e a data do arquivo, e nao a ordem de leitura: ordenar por
    nome de caso daria um resultado que depende de como as pastas se chamam, e
    a regeracao deixaria de ser deterministica.
    """
    raiz = casos_dir or CASOS_DIR
    # (amb, familia, nome, dia) -> (mtime_do_arquivo, valor)
    bruto: dict = {}
    for familia, arquivo, al_nome, al_valor, al_dia in FONTES:
        for path in sorted(glob.glob(os.path.join(raiz, "*", arquivo))):
            caso_dir = os.path.dirname(path)
            amb = _chave(caso_dir)
            if not amb:
                continue
            try:
                idade = os.path.getmtime(path)
            except OSError:
                continue
            for nome, dia, valor in _pontos_do_arquivo(
                    path, al_nome, al_valor, al_dia):
                k = (amb, familia, nome, dia)
                anterior = bruto.get(k)
                if anterior is None or idade >= anterior[0]:
                    bruto[k] = (idade, valor)

    out: dict = {}
    for (amb, familia, nome, dia), (_idade, valor) in bruto.items():
        out.setdefault(amb, {}).setdefault(familia, {}).setdefault(
            nome, {})[dia] = valor
    for amb in out:
        for familia in out[amb]:
            for nome in out[amb][familia]:
                out[amb][familia][nome] = dict(
                    sorted(out[amb][familia][nome].items()))
            out[amb][familia] = dict(sorted(out[amb][familia].items()))
        out[amb] = dict(sorted(out[amb].items()))
    return dict(sorted(out.items()))


def ler(path: str = "") -> dict:
    d = _json(path or CAMINHO)
    if "ambientes" not in d:
        d = {"versao": 1, "descricao": DESCRICAO, "gerado_em": "",
             "ambientes": {}}
    return d


def gravar(d: dict, path: str = "") -> str:
    destino = path or CAMINHO
    d["descricao"] = DESCRICAO
    d["gerado_em"] = _agora()
    d["ambientes"] = dict(sorted(d.get("ambientes", {}).items()))
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return destino


def atualizar(path: str = "", casos_dir: str = "") -> tuple:
    """Regera 'series' preservando 'marcos'. Retorna (hist, novos, pontos)."""
    hist = ler(path)
    ambientes = hist.get("ambientes", {})
    derivado = coletar(casos_dir)
    novos, pontos = [], 0
    for amb, series in derivado.items():
        anterior = ambientes.get(amb) or {}
        if not anterior.get("series"):
            novos.append(amb)
        ambientes[amb] = {
            "marcos": anterior.get("marcos") or [],
            "series": series,
        }
        pontos += sum(len(v) for fam in series.values() for v in fam.values())
    hist["ambientes"] = ambientes
    return hist, novos, pontos


def marcar(ambiente: str, dia: str, texto: str, path: str = "") -> dict:
    """Registra o que aconteceu num dia - a camada que o SELECT nao alcanca.

    E o par do salto de carga: o analisador diz QUANDO o patamar mudou, e o
    marco diz O QUE mudou. Sem isso a mesma investigacao e refeita do zero no
    mes seguinte.
    """
    chave = (ambiente or "").strip().upper()
    texto = (texto or "").strip()
    if not chave:
        raise ValueError("ambiente vazio")
    if not texto:
        raise ValueError("marco sem texto")
    try:
        dia = dt.date.fromisoformat((dia or "").strip()).isoformat()
    except ValueError:
        raise ValueError(f"dia invalido (use YYYY-MM-DD): {dia!r}") from None
    hist = ler(path)
    ent = hist["ambientes"].setdefault(chave, {"marcos": [], "series": {}})
    ent.setdefault("marcos", []).append(
        {"dia": dia, "texto": texto, "registrado_em": _agora()})
    ent["marcos"] = sorted(ent["marcos"], key=lambda m: m.get("dia", ""))
    return hist


def serie(hist: dict, ambiente: str, familia: str, nome: str) -> list:
    """[(dia, valor)] ordenado por dia."""
    ent = (hist.get("ambientes") or {}).get((ambiente or "").upper()) or {}
    dados = ((ent.get("series") or {}).get(familia) or {}).get(nome) or {}
    return sorted(dados.items())


def marcos_em(hist: dict, ambiente: str, dia: str, janela: int = 1) -> list:
    """Marcos dentro de +-`janela` dias de `dia`.

    A janela existe porque a data de um evento raramente coincide com a data em
    que o efeito aparece na medicao: um deploy da noite de sexta aparece na
    carga de sabado.
    """
    ent = (hist.get("ambientes") or {}).get((ambiente or "").upper()) or {}
    try:
        alvo = dt.date.fromisoformat(dia[:10])
    except (ValueError, TypeError):
        return []
    fora = []
    for m in ent.get("marcos") or []:
        try:
            d = dt.date.fromisoformat(str(m.get("dia"))[:10])
        except ValueError:
            continue
        if abs((d - alvo).days) <= janela:
            fora.append(m)
    return fora


def tendencia(pontos: list) -> dict:
    """Reta por minimos quadrados sobre [(dia, valor)].

    Devolve `por_dia` (inclinacao), `r2` (quanto a reta explica a variacao) e
    `pontos`/`dias` da amostra. `r2` nao e enfeite estatistico: uma tablespace
    que cresce em degraus tem inclinacao positiva e r2 baixo, e extrapolar uma
    reta ali produz uma data com cara de precisao que ninguem deveria usar para
    pedir disco. Quem consome DEVE olhar o r2 - por isso ele vem junto e nao
    num metodo separado.

    Devolve {} quando a amostra nao autoriza conclusao: menos de MIN_PONTOS
    medicoes ou janela menor que MIN_DIAS_JANELA. Preferimos nao responder a
    responder com uma reta sobre dois pontos.
    """
    limpos = []
    for dia, valor in sorted(pontos or []):
        try:
            limpos.append((dt.date.fromisoformat(str(dia)[:10]), float(valor)))
        except (ValueError, TypeError):
            continue
    if len(limpos) < MIN_PONTOS:
        return {}
    d0 = limpos[0][0]
    xs = [(d - d0).days for d, _ in limpos]
    ys = [v for _, v in limpos]
    janela = xs[-1] - xs[0]
    if janela < MIN_DIAS_JANELA:
        return {}
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return {}
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 if syy == 0 else max(
        0.0, 1.0 - sum((y - (slope * x + intercept)) ** 2
                       for x, y in zip(xs, ys, strict=True)) / syy)
    return {"por_dia": slope, "r2": r2, "pontos": n, "dias": janela,
            "primeiro": limpos[0][0].isoformat(),
            "ultimo": limpos[-1][0].isoformat(),
            "valor_atual": ys[-1]}


def dias_para(pontos: list, limite: float) -> dict:
    """Quantos dias ate a serie atingir `limite`, pela reta da tendencia.

    Esta e a leitura que o `forecast.txt` da - e que exige Diagnostics Pack.
    Aqui ela sai das coletas do proprio framework, sobre `space.txt`, que le
    `dba_data_files` e nao precisa de pacote nenhum.

    Devolve {} quando nao ha tendencia utilizavel ou quando a serie nao caminha
    para o limite (inclinacao <= 0, ou ja passou). "Nao vai encher" e uma
    resposta melhor que um numero grande e inventado.
    """
    t = tendencia(pontos)
    if not t or t["por_dia"] <= 0:
        return {}
    falta = float(limite) - t["valor_atual"]
    if falta <= 0:
        return {**t, "dias_para": 0}
    return {**t, "dias_para": falta / t["por_dia"]}


def resumo(hist: dict) -> dict:
    """Contagens para a linha de comando e para o relatorio."""
    ambientes = hist.get("ambientes") or {}
    pontos = 0
    dias: set = set()
    familias: dict = {}
    marcos = 0
    for ent in ambientes.values():
        marcos += len(ent.get("marcos") or [])
        for familia, nomes in (ent.get("series") or {}).items():
            for v in nomes.values():
                pontos += len(v)
                dias |= set(v)
                familias[familia] = familias.get(familia, 0) + len(v)
    return {
        "ambientes": len(ambientes),
        "pontos": pontos,
        "marcos": marcos,
        "dias_distintos": len(dias),
        "primeiro_dia": min(dias) if dias else "",
        "ultimo_dia": max(dias) if dias else "",
        "por_familia": dict(sorted(familias.items(), key=lambda x: -x[1])),
    }
