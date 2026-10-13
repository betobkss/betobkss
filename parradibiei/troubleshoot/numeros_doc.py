"""
numeros_doc.py - o bloco de numeros da documentacao, recontado do repositorio.

Origem: em 11/08 o `ESTADO_PROYECTO.md` anunciava "1538 testes / 74 checks"
tendo o repositorio 1662 funcoes de teste e 78 checks, e ainda descrevia a
bateria de homologacao como card do painel meses depois de ela sair de la. Nao
foi descuido pontual: **todo numero digitado a mao em documento envelhece
calado**. E a mesma invariante que o `blueprint.py` e os decks ja obedecem
(`apresentacao.numeros()`); faltava valer para o README e companhia.

A solucao aqui e deliberadamente burra: um bloco delimitado por marcadores HTML
que este modulo reescreve, e um teste que reprova o commit quando o bloco
diverge da contagem real. Nada de gerar o documento inteiro - o texto ao redor
e escrito por gente e deve continuar sendo.

Uso:

    python -m troubleshoot.numeros_doc            # reescreve o bloco nos docs
    python -m troubleshoot.numeros_doc --conferir # so acusa divergencia (CI)
"""
from __future__ import annotations

import glob
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Documentos que carregam o bloco. Quem tiver os marcadores entra; quem nao
# tiver e simplesmente ignorado (nao inventamos secao em documento alheio).
DOCS = ("README.md", "ESTADO_PROYECTO.md", "PLATAFORMA.md", "INSTALL.md")

MARCA_INI = "<!-- NUMEROS:INICIO -->"
MARCA_FIM = "<!-- NUMEROS:FIM -->"

AVISO = ("<!-- Gerado por `python -m troubleshoot.numeros_doc`. "
         "Nao editar a mao: o teste `tests/test_numeros_doc.py` reprova "
         "divergencia. -->")

# (chave, rotulo, de onde sai). A terceira coluna e o que permite ao leitor
# conferir o numero sem ler este codigo.
ROTULOS = (
    ("checks", "Checks no catalogo", "`mcp/catalog.json`"),
    ("categorias", "Categorias de check", "`mcp/catalog.json`"),
    ("analisadores", "Analisadores", "`troubleshoot/analyzers/`"),
    ("playbooks", "Playbooks / passos", "`kb/*.json`"),
    ("regras_bp", "Regras de conformidade / familias", "`mcp/best_practices.json`"),
    ("erros_ora", "Erros ORA- catalogados", "`mcp/erros_ora.json`"),
    ("testes", "Funcoes de teste", "`tests/test_*.py`"),
)


def _catalogo() -> list:
    try:
        with open(os.path.join(BASE_DIR, "mcp", "catalog.json"),
                  encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return []
    return dados.get("checks") if isinstance(dados, dict) else dados


def regras_bp() -> tuple:
    """(regras, familias) de best practice, contadas do JSON.

    Fonte unica: o `blueprint.py` delega para ca em vez de reimplementar - duas
    contagens do mesmo dado sao duas oportunidades de discordar.
    """
    try:
        with open(os.path.join(BASE_DIR, "mcp", "best_practices.json"),
                  encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return 0, 0
    familias = {k: v for k, v in dados.items() if isinstance(v, dict)}
    return sum(len(v) for v in familias.values()), len(familias)


def passos_kb() -> int:
    """Passos somados de todos os playbooks."""
    n = 0
    for p in sorted(glob.glob(os.path.join(BASE_DIR, "kb", "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                n += len(json.load(f).get("passos") or [])
        except (OSError, ValueError):
            continue
    return n


def contagens() -> dict:
    """Todos os numeros do bloco, contados AGORA."""
    try:
        from . import apresentacao
        num = dict(apresentacao.numeros())
    except Exception:
        num = {}
    regras, familias = regras_bp()
    num["regras_bp"] = regras
    num["familias_bp"] = familias
    num["passos"] = passos_kb()
    # A chave e `category` (ingles), como no resto do catalogo. Errei para
    # `categoria` na primeira versao e o bloco publicou "1 categoria" com cara
    # de numero conferido - por isso o teste compara com a FONTE, e nao com um
    # literal.
    num["categorias"] = len({c.get("category") for c in _catalogo()
                             if isinstance(c, dict) and c.get("category")})
    return num


def _valor(chave: str, num: dict) -> str:
    """O texto da coluna. Dois numeros que so fazem sentido juntos viajam
    juntos: playbook sem passo e regra sem familia nao dizem nada sozinhos."""
    if chave == "playbooks":
        return f"{num.get('playbooks', 0)} / {num.get('passos', 0)}"
    if chave == "regras_bp":
        return f"{num.get('regras_bp', 0)} / {num.get('familias_bp', 0)}"
    return str(num.get(chave, 0))


def bloco(num: dict | None = None) -> str:
    """O bloco inteiro, marcadores incluidos."""
    num = contagens() if num is None else num
    linhas = [MARCA_INI, AVISO, "", "| O que | Quanto | Fonte |", "|---|---:|---|"]
    for chave, rotulo, fonte in ROTULOS:
        linhas.append(f"| {rotulo} | {_valor(chave, num)} | {fonte} |")
    linhas += ["",
               "> As funcoes de teste sao contadas por `def test_`; o pytest "
               "reporta mais, porque parametrizacao vira varios casos.",
               MARCA_FIM]
    return "\n".join(linhas)


def _substituir(texto: str, novo: str) -> str | None:
    """Troca o bloco no texto. `None` quando o documento nao tem marcadores."""
    i = texto.find(MARCA_INI)
    j = texto.find(MARCA_FIM)
    if i < 0 or j < 0 or j < i:
        return None
    return texto[:i] + novo + texto[j + len(MARCA_FIM):]


def _docs_existentes(docs=DOCS):
    for rel in docs:
        p = os.path.join(BASE_DIR, rel)
        try:
            with open(p, encoding="utf-8") as f:
                yield rel, p, f.read()
        except OSError:
            continue


def divergentes(docs=DOCS) -> list:
    """Documentos com bloco desatualizado. Documento SEM bloco nao e erro:
    so entra na conta quem optou por carregar os numeros."""
    novo = bloco()
    fora = []
    for rel, _p, txt in _docs_existentes(docs):
        if MARCA_INI not in txt:
            continue
        atual = txt[txt.find(MARCA_INI):txt.find(MARCA_FIM) + len(MARCA_FIM)]
        if atual.strip() != novo.strip():
            fora.append(rel)
    return fora


def atualizar(docs=DOCS) -> list:
    """Reescreve o bloco onde ele existe. Devolve os documentos alterados."""
    novo = bloco()
    mudados = []
    for rel, p, txt in _docs_existentes(docs):
        saida = _substituir(txt, novo)
        if saida is None or saida == txt:
            continue
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(saida)
        mudados.append(rel)
    return mudados


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--conferir" in argv:
        fora = divergentes()
        if fora:
            print("Bloco de numeros desatualizado em: " + ", ".join(fora))
            print("Corrija com: python -m troubleshoot.numeros_doc")
            return 1
        print("Bloco de numeros em dia.")
        return 0
    mudados = atualizar()
    print("Atualizado: " + ", ".join(mudados) if mudados
          else "Nada a atualizar (bloco ja em dia).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
