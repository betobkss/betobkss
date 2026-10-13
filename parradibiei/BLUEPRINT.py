r"""
BLUEPRINT.py - gera a especificacao do framework em UM arquivo.

    .\.venv\Scripts\python.exe BLUEPRINT.py
    .\.venv\Scripts\python.exe BLUEPRINT.py --sem-dados    # so a engenharia
    .\.venv\Scripts\python.exe BLUEPRINT.py --open

Sai em `output/blueprint/PARRADIBIEI_BLUEPRINT.md`: manifesto, principios,
arquitetura, contratos, invariantes (cada uma com o incidente que a criou),
ordem de reconstrucao e os dados de dominio literais (checks SQL, playbooks,
regras, erros ORA-).

Para que serve: entregar a UM modelo de linguagem com a tarefa "recrie este
framework". Nao e backup e nao e clone - o codigo recriado nao sai identico. Se
o que voce quer e a copia exata, use `EMPACOTAR.py --unico`, que gera um arquivo
auto-extraivel com o projeto inteiro dentro.

NAO leva evidencia: o blueprint descreve o framework, nunca os casos coletados.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from troubleshoot import blueprint


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Gera a especificacao do framework em um arquivo unico.")
    ap.add_argument("--sem-dados", action="store_true",
                    help="omite os checks SQL e os playbooks (arquivo leve, "
                         "mas o framework recriado fica sem conhecimento "
                         "Oracle nenhum)")
    ap.add_argument("--destino", default="",
                    help="pasta de saida (padrao: output/blueprint)")
    ap.add_argument("--open", action="store_true", dest="abrir",
                    help="abre o arquivo ao terminar")
    a = ap.parse_args(argv)

    com_dados = not a.sem_dados
    caminho = blueprint.gerar(a.destino, com_dados=com_dados)
    kb = os.path.getsize(caminho) / 1024

    print(f"[OK] {caminho}  ({kb:.0f} KB, ~{kb * 1024 / 4 / 1000:.0f} k tokens)")
    if com_dados:
        print("     inclui os dados de dominio literais (secao 8)")
    else:
        print("     [AVISO] sem os dados: quem reconstruir a partir deste "
              "arquivo vai INVENTAR os checks SQL")
    if a.abrir:
        try:
            if sys.platform.startswith("win"):
                os.startfile(caminho)
            else:
                subprocess.run(["xdg-open", caminho], check=False)
        except OSError as e:
            print(f"[AVISO] nao consegui abrir: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
