"""
Melhoria continua: logica do corpus de regressao e da higiene do projeto.

Ideia central - **o corpus de regressao**. Os casos ja coletados (producao e
homologacao) sao o melhor conjunto de teste que existe: sao dados reais, com as
esquisitices reais de cada versao e topologia. Reanalisar essa evidencia a cada
mudanca responde a pergunta que teste unitario nao responde: *a minha alteracao
mudou o diagnostico de algum caso que ja foi entregue?*

REGRA DE OURO (definida com o DBA): caso de **producao e so leitura de
resultado** - nunca se reconecta ao banco de producao para isto. A reanalise
usa exclusivamente a evidencia gravada em `troubleshooting/<caso>/` e **nao
escreve nada** na pasta do caso. Conectar so em **homologacao**, e isso e
trabalho do `TESTE_HOMOLOG.py`.

O que este modulo faz (tudo offline):
- `snapshot_caso`  - retrato do diagnostico de um caso (score, severidade,
  achados, erros de analisador);
- `comparar`       - diff entre o retrato de hoje e o baseline aceito;
- `checar_dependencias` / `checar_catalogo` - higiene do projeto.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR_PADRAO = os.path.join(BASE_DIR, "troubleshooting")
OUT_DIR = os.path.join(BASE_DIR, "output", "melhoria")
BASELINE = os.path.join(OUT_DIR, "baseline.json")

# Casos que a bateria de homologacao regera a cada execucao: entram no corpus
# como "voluteis" e sozinhos nao reprovam a rotina - o ambiente muda entre
# coletas, e a diferenca ali nao e sinal de bug no nosso codigo.
PREFIXO_HOMOLOG = "HOMOLOG_"
SERVIDORES_TXT = os.path.join(BASE_DIR, "servidores.txt")


def servidores_homolog(caminho: str = "") -> set:
    """Servidores de homologacao (`servidores.txt`), em maiusculas."""
    p = caminho or SERVIDORES_TXT
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8") as f:
        return {ln.strip().upper() for ln in f
                if ln.strip() and not ln.lstrip().startswith("#")}


def _volatil(case: str, conn: str = "", homolog: set | None = None) -> bool:
    """Heuristica de apoio: o caso vem de servidor de homologacao?

    Historia: o primeiro uso real da rotina acusou "regressao" em `HC_DBSRV114`
    porque o caso tinha sido recoletado a mao num servidor de homologacao -
    mudou o BANCO, nao o codigo. A primeira correcao foi julgar pela origem
    (servidor em `servidores.txt`), mas isso ainda e palpite pelo nome: mata a
    deteccao de regressao real em caso de homologacao parado.

    Hoje quem decide e `impressao_evidencia()`. Esta funcao so entra como
    desempate para baseline antigo, gravado antes da impressao existir.
    """
    if case.startswith(PREFIXO_HOMOLOG):
        return True
    srvs = homolog if homolog is not None else servidores_homolog()
    alvo = (conn or "").strip().upper()
    nome = case.upper()
    return any(s == alvo or s in nome for s in srvs)


def impressao_evidencia(case_dir: str) -> str:
    """Impressao digital da evidencia do caso: nome, tamanho e mtime de cada
    arquivo.

    E o discriminador objetivo da rotina. Se a impressao **nao mudou** e o
    diagnostico mudou, so sobra uma explicacao: mudou o nosso codigo - isso e
    regressao. Se a impressao mudou, o caso foi recoletado e o diagnostico
    diferente e esperado, seja o servidor de homologacao ou nao.

    Sem isso a rotina julgava pelo NOME do caso (heuristica), e um caso
    recoletado a mao virava acusacao contra o codigo.
    """
    if not os.path.isdir(case_dir):
        return ""
    h = hashlib.sha1()
    for nome in sorted(os.listdir(case_dir)):
        p = os.path.join(case_dir, nome)
        # meta.json muda a cada anotacao do caso e coleta.log ganha uma linha a
        # cada execucao: sao rastro do caso, nao evidencia do banco. Conta-los
        # aqui faria a rotina ver "evidencia nova" onde nada foi recoletado.
        if not os.path.isfile(p) or nome in ("meta.json", "history.json",
                                             "coleta.log"):
            continue
        st = os.stat(p)
        h.update(f"{nome}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    return h.hexdigest()[:16]


def snapshot_caso(case: str, cases_dir: str = "",
                  homolog: set | None = None) -> dict:
    """Retrato do diagnostico de um caso, a partir da evidencia ja gravada.

    Nao acessa banco e nao escreve nada (`expandir=False` justamente para nao
    criar arquivo na pasta do caso).
    """
    import TROUBLESHOOT  # import tardio: evita ciclo e custo no import do pacote

    r = TROUBLESHOOT.analisar_caso(case, cases_dir=cases_dir, expandir=False,
                                   quieto=True)
    return {
        "evidencia": impressao_evidencia(
            os.path.join(cases_dir or CASES_DIR_PADRAO, case)),
        "caso": case,
        "score": r.score,
        "severidade": r.severity,
        "n_secoes": len(r.sections),
        "n_achados": sum(len(s.findings) for s in r.sections),
        "achados": sorted(f"{f.severity}|{f.title}"
                          for s in r.sections for f in s.findings),
        "erros": list(r.erros),
        "conn": r.meta.get("conn", ""),
        "volatil": _volatil(case, r.meta.get("conn", ""), homolog),
    }


# O que conta como evidencia DO BANCO na pasta do caso. Alinhado com o
# `TEXT_EXT` do TROUBLESHOOT.py, que e quem de fato analisa.
_EXT_EVIDENCIA = (".txt", ".lst", ".out", ".log", ".html", ".htm",
                  ".mht", ".mhtml")

# Arquivos que moram na pasta do caso e NAO sao evidencia: sao registro nosso.
# `coleta.log` e o unico que exige cuidado - ele termina em `.log`, extensao
# que o analisador aceita por causa do alert log, e e o UNICO arquivo que
# sobra quando a conexao falha. Sem esta excecao, `tem_evidencia` diria "sim"
# justamente para o caso que nao tem nada.
_NAO_E_EVIDENCIA = ("coleta.log",)


def tem_evidencia(case: str, cases_dir: str = "") -> bool:
    """O caso tem o que comparar? (pasta inexistente conta como SIM - ver abaixo)

    Coleta que falhou na CONEXAO deixa a pasta com o `coleta.log` e mais nada.
    Isso nao e um ponto de regressao - e uma coleta que nao aconteceu.

    Medido na bateria de 26 servidores: 3 nao respondem (DNS/rede) e criavam
    `HOMOLOG_*` vazios. No corpus eles fariam duas coisas ruins, as duas
    silenciosas: entrariam no baseline como caso sem score, gerando ruido em
    toda comparacao futura; e, no dia em que a rede voltasse e a coleta desse
    certo, o caso ganharia score e achados de uma vez - lido pelo `comparar`
    como REGRESSAO, quando foi o oposito. O corpus tem de conter so caso que
    tem o que comparar.
    """
    base = cases_dir or CASES_DIR_PADRAO
    d = os.path.join(base, case)
    if not os.path.isdir(d):
        # Pasta INEXISTENTE nao e o mesmo que pasta vazia, e a diferenca
        # importa: aqui devolvemos True para o caso seguir adiante e explodir
        # em `snapshot_caso`, virando `erro_fatal` - visivel. Nome errado na
        # lista, ou pasta apagada por engano, e problema que alguem precisa
        # ver; coleta que falhou na conexao, nao.
        return True
    for nome in os.listdir(d):
        if nome.lower() in _NAO_E_EVIDENCIA:
            continue
        if nome.lower().endswith(_EXT_EVIDENCIA):
            return True
    return False


def snapshot_corpus(casos: list, cases_dir: str = "") -> dict:
    """Retrato de varios casos. Um caso que explode nao derruba a rotina: vira
    achado (`erro_fatal`), que e exatamente o tipo de regressao a caçar.

    Caso SEM evidencia fica fora (ver `tem_evidencia`): coleta que nao
    aconteceu nao e dado de regressao.
    """
    homolog = servidores_homolog()
    out = {}
    for c in casos:
        if not tem_evidencia(c, cases_dir):
            continue
        try:
            out[c] = snapshot_caso(c, cases_dir, homolog)
        except Exception as e:
            out[c] = {"caso": c, "erro_fatal": f"{type(e).__name__}: {e}",
                      "volatil": _volatil(c, "", homolog)}
    return out


def comparar(baseline: dict, atual: dict) -> dict:
    """Diff entre o baseline aceito e o retrato de agora.

    Devolve, por caso, o que mudou; e separa em `regressoes` (o que exige olhar
    antes de seguir) e `informativo` (caso novo, sumido ou volatil).
    """
    regressoes, informativo = [], []

    for caso in sorted(set(baseline) | set(atual)):
        b, a = baseline.get(caso), atual.get(caso)
        if b is None:
            informativo.append({"caso": caso, "tipo": "novo",
                                "detalhe": "caso ainda nao estava no baseline"})
            continue
        if a is None:
            informativo.append({"caso": caso, "tipo": "sumiu",
                                "detalhe": "pasta do caso nao existe mais"})
            continue

        if a.get("erro_fatal"):
            regressoes.append({"caso": caso, "tipo": "erro_fatal",
                               "detalhe": a["erro_fatal"]})
            continue

        novos_erros = [e for e in a.get("erros", []) if e not in b.get("erros", [])]
        if novos_erros:
            regressoes.append({"caso": caso, "tipo": "analisador_quebrou",
                               "detalhe": "; ".join(novos_erros)})

        ab, aa = set(b.get("achados", [])), set(a.get("achados", []))
        mudou_score = a.get("score") != b.get("score")
        if not (mudou_score or ab != aa):
            continue

        det = []
        if mudou_score:
            det.append(f"score {b.get('score')} -> {a.get('score')}")
        if aa - ab:
            det.append(f"+{len(aa - ab)} achado(s)")
        if ab - aa:
            det.append(f"-{len(ab - aa)} achado(s)")
        item = {"caso": caso, "tipo": "diagnostico_mudou",
                "detalhe": ", ".join(det),
                "novos": sorted(aa - ab)[:10],
                "sumiram": sorted(ab - aa)[:10]}

        # A impressao digital da evidencia decide, e vence a heuristica de
        # origem: mesma evidencia + diagnostico diferente = mudou o CODIGO,
        # ainda que o caso seja de homologacao (caso de homologacao parado e
        # material de corpus tao bom quanto o de producao). A volatilidade por
        # origem so vale quando nao ha impressao dos dois lados - baseline
        # gravado antes deste criterio existir.
        ea, eb = a.get("evidencia", ""), b.get("evidencia", "")
        if ea and eb:
            if ea != eb:
                item["tipo"] = "recoletado"
                item["detalhe"] += " (evidencia recoletada: nao e o codigo)"
                informativo.append(item)
            else:
                item["detalhe"] += " (mesma evidencia: mudou o codigo)"
                regressoes.append(item)
            continue
        vol = a.get("volatil") or b.get("volatil")
        if vol:
            item["detalhe"] += " (homologacao: evidencia recoletavel)"
        (informativo if vol else regressoes).append(item)

    return {"regressoes": regressoes, "informativo": informativo,
            "n_casos": len(atual)}


def ler_baseline(caminho: str = "") -> dict:
    p = caminho or BASELINE
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("casos", {})
    except Exception:
        return {}


def gravar_baseline(atual: dict, caminho: str = "", quando: str = "") -> str:
    p = caminho or BASELINE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"aceito_em": quando, "casos": atual}, f,
                  indent=2, ensure_ascii=False)
    return p


# ── higiene do projeto ────────────────────────────────────────
_REQ = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==\s*([0-9][^\s#]*)")


def ler_requirements(caminho: str) -> dict:
    """{pacote: versao pinada} a partir de um requirements.txt."""
    pins = {}
    if not os.path.exists(caminho):
        return pins
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            m = _REQ.match(linha)
            if m:
                pins[m.group(1).lower()] = m.group(2)
    return pins


def versoes_instaladas(pacotes: list, python: str = "") -> dict:
    """Versao realmente instalada de cada pacote (via importlib.metadata)."""
    from importlib import metadata
    out = {}
    for p in pacotes:
        try:
            out[p] = metadata.version(p)
        except Exception:
            out[p] = ""
    return out


def checar_dependencias(req_path: str = "") -> list:
    """Compara o que esta pinado com o que esta instalado.

    Nao vai a rede: em ambiente corporativo o indice e o Artifactory interno e
    consultar versao nova e passo manual da rotina (ver o workflow /melhoria).
    """
    req = req_path or os.path.join(BASE_DIR, "requirements.txt")
    pins = ler_requirements(req)
    inst = versoes_instaladas(list(pins))
    achados = []
    for pkg, pin in sorted(pins.items()):
        atual = inst.get(pkg, "")
        if not atual:
            achados.append({"pacote": pkg, "pin": pin, "instalado": "",
                            "situacao": "nao instalado"})
        elif atual != pin:
            achados.append({"pacote": pkg, "pin": pin, "instalado": atual,
                            "situacao": "divergente do pin"})
    return achados


def checar_catalogo(catalogo: str = "") -> dict:
    """Buracos no catalogo de checks - a fonte unica da coleta.

    Os tres sinais que interessam a melhoria continua:
    - `sem_sql`: check declarado que nao coleta nada;
    - `sem_analisador`: coleta evidencia que **ninguem le** - o arquivo cai na
      pasta do caso e nao vira achado. E o buraco mais comum ao crescer rapido;
    - `sem_referencia`: limiar (`thresholds`) sem fonte citada. Numero magico
      sem documentacao/comunidade por tras e opiniao, nao criterio.
    """
    p = catalogo or os.path.join(BASE_DIR, "mcp", "catalog.json")
    if not os.path.exists(p):
        return {"erro": f"catalogo nao encontrado: {p}"}
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    checks = d.get("checks", d if isinstance(d, list) else [])
    if isinstance(checks, dict):
        checks = list(checks.values())

    def _id(c):
        return c.get("id") or c.get("name") or "?"

    return {
        "total": len(checks),
        "sem_sql": [_id(c) for c in checks if not (c.get("sql") or c.get("query"))],
        "sem_analisador": [_id(c) for c in checks if not c.get("analyzer")],
        "sem_referencia": [_id(c) for c in checks
                           if c.get("thresholds") and not c.get("references")],
        "com_pack": len([c for c in checks if c.get("requires_pack")]),
    }


def rodar_testes(python: str = "", alvo: list | None = None) -> dict:
    """Roda a suite. E o piso da rotina: sem verde aqui, nada mais importa.

    `alvo` limita a arquivos especificos (usado pelo proprio teste desta
    funcao, que senao chamaria a suite inteira de dentro dela mesma).
    """
    py = python or sys.executable
    # `-o addopts=` zera o addopts do pyproject (que ja traz `-q`): com dois -q
    # o pytest vira -qq e **some com a linha de resumo**, e a rotina reportava
    # "0 passaram" numa suite verde.
    r = subprocess.run([py, "-m", "pytest", "-q", "-o", "addopts=", *list(alvo or [])], cwd=BASE_DIR,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    saida = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+) passed", saida)
    f = re.search(r"(\d+) failed", saida)
    return {"rc": r.returncode, "passaram": int(m.group(1)) if m else 0,
            "falharam": int(f.group(1)) if f else 0,
            "cauda": saida.strip()[-1500:]}
