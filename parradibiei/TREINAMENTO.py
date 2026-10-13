r"""
TREINAMENTO - transforma o conhecimento do time em material para os outros DBAs.

O pedido e antigo: "guarde tudo e depois monte um treinamento para compartilhar
com os outros DBAs". O que faltava nao era vontade - era perceber que **o
material ja estava escrito e invisivel**. Cada playbook da `kb/` declara, por
passo, o campo `armadilha`: o jeito de dar errado que so se aprende apanhando.
Sao 270. Ninguem fora desta ferramenta leu um so.

Duas fontes, que ensinam coisas diferentes:
  - `kb/*.json`  -> o procedimento certo e a armadilha (vale para qualquer Oracle)
  - os casos     -> o RACIOCINIO: a hipotese levantada e, sobretudo, a DESCARTADA

A segunda e a mais valiosa e hoje esta quase vazia. Por isso `--cobertura`
existe: ele mede quanto do raciocinio esta sendo perdido, caso a caso.

USO:
  .\.venv\Scripts\python.exe TREINAMENTO.py                    # apostila completa
  .\.venv\Scripts\python.exe TREINAMENTO.py --tema archive     # um tema so
  .\.venv\Scripts\python.exe TREINAMENTO.py --quiz             # perguntas
  .\.venv\Scripts\python.exe TREINAMENTO.py --cobertura        # o que se perdeu
  .\.venv\Scripts\python.exe TREINAMENTO.py --publico          # mascara as marcas

100% OFFLINE: nao abre conexao com banco nenhum.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from troubleshoot import treinamento as T


def _cobertura() -> int:
    c = T.cobertura()
    print("=" * 74)
    print("  COBERTURA DO RACIOCINIO - o que vira treinamento e o que se perde")
    print("=" * 74)
    tot = c["total"] or 1
    for rot, val in (("Casos coletados", c["total"]),
                     ("com hipotese registrada", c["com_hipotese"]),
                     ("com acao registrada", c["com_acao"]),
                     ("com causa raiz", c["com_causa_raiz"]),
                     ("hipoteses DESCARTADAS", c["hipoteses_descartadas"])):
        pct = f"  ({100.0 * val / tot:.0f}%)" if rot != "Casos coletados" else ""
        print(f"  {rot:26}: {val:>4}{pct}")
    print()
    if c["sem_raciocinio"]:
        print(f"  [!] {c['sem_raciocinio']} caso(s) sem raciocinio nenhum "
              "registrado.")
        print("      A evidencia ficou; o raciocinio, nao - e e ele que ensina.")
        print("      Durante o caso:")
        print('        CASO.py <caso> --hipotese "..."')
        print('        CASO.py <caso> --hipotese "nova" --motivo "por que a anterior caiu"')
        print('        CASO.py <caso> --acao "..." --risco baixo|medio|alto')
        print('        CASO.py <caso> --resolver --causa "..." --solucao "..."')
    if not c["hipoteses_descartadas"]:
        print()
        print("  [!] NENHUMA hipotese descartada em todo o historico.")
        print("      Hipotese que cai e o material didatico mais valioso que")
        print("      existe: ensina a errar com metodo. Use 'proximo' /")
        print("      --hipotese com --motivo em vez de sobrescrever em silencio.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Gera material de treinamento a partir da KB e dos casos",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plataforma", default="",
                    help="apostila de UMA tecnologia: oracle | mssql | db2 | "
                         "postgres | mysql | redis | mongodb")
    ap.add_argument("--tema", default="",
                    help="so um tema (id ou parte do titulo do playbook)")
    ap.add_argument("--quiz", action="store_true",
                    help="perguntas a partir das armadilhas, com resposta oculta")
    ap.add_argument("--cobertura", action="store_true",
                    help="quanto do raciocinio dos casos esta sendo registrado")
    ap.add_argument("--publico", action="store_true",
                    help="mascara os termos de marcas_locais.txt (material que "
                         "sai da empresa)")
    ap.add_argument("--stdout", action="store_true",
                    help="imprime em vez de gravar arquivo")
    args = ap.parse_args()

    if args.cobertura:
        return _cobertura()

    from troubleshoot import kb
    plat = args.plataforma.strip().lower()
    if plat and plat not in kb.PLATAFORMAS:
        print(f"[ERRO] plataforma '{plat}' desconhecida. Use uma de: "
              + ", ".join(sorted(kb.PLATAFORMAS)))
        return 2
    if plat and not kb.por_plataforma(plat):
        print(f"[!] Nenhum playbook de '{plat}' na KB ainda "
              f"({kb.PLATAFORMAS[plat]}).")
        print("    A apostila sairia vazia. Cobertura atual: "
              + ", ".join(f"{k}={v}" for k, v in kb.plataformas().items()))
        return 1

    dia = dt.date.today().isoformat()
    marca_plat = f"_{plat.upper()}" if plat else ""
    # O sufixo _PUBLICO nao e cosmetico. Sem ele as duas versoes gravam no
    # MESMO arquivo e a segunda execucao apaga a primeira em silencio (foi o
    # que aconteceu em 21/09/2026). E o estrago pior nao e perder o arquivo:
    # e nao saber QUAL versao esta na mao na hora de anexar num e-mail para
    # fora. O nome do arquivo e a unica pista que sobrevive ao download.
    marca_pub = "_PUBLICO" if args.publico else ""
    if args.quiz:
        conteudo = T.quiz(publico=args.publico, plataforma=plat)
        nome = f"QUIZ{marca_plat}{marca_pub}_{dia}.md"
    else:
        conteudo = T.apostila(publico=args.publico, tema=args.tema,
                              plataforma=plat)
        sufixo = f"_{args.tema}" if args.tema else ""
        nome = f"APOSTILA{marca_plat}{sufixo}{marca_pub}_{dia}.md"

    if args.stdout:
        print(conteudo)
        return 0

    destino = T.gravar(conteudo, nome)
    linhas = conteudo.count("\n") + 1
    print(f"[OK] {destino}")
    print(f"     {linhas} linhas"
          + ("  |  marcas mascaradas (--publico)" if args.publico
             else "  |  USO INTERNO: contem nome de servidor e de empresa"))
    if not args.quiz:
        c = T.cobertura()
        if not c["casos_ricos"]:
            print("     [!] a secao de casos reais saiu VAZIA - rode "
                  "TREINAMENTO.py --cobertura para ver o porque")
    return 0


if __name__ == "__main__":
    sys.exit(main())
