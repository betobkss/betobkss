"""
APRESENTAR - gera o deck de apresentacao do FRAMEWORK (nao de um caso).

    .\\.venv\\Scripts\\python.exe APRESENTAR.py                 # os tres decks
    .\\.venv\\Scripts\\python.exe APRESENTAR.py --gestao        # so o executivo
    .\\.venv\\Scripts\\python.exe APRESENTAR.py --tecnico       # so o tecnico
    .\\.venv\\Scripts\\python.exe APRESENTAR.py --impacto       # so o de impacto

Sai em output/apresentacao/. Os numeros dos slides sao contados do repositorio
na hora de gerar, e nenhum dado de banco real entra no arquivo - o deck circula
por e-mail.

O deck de IMPACTO compara o checklist na mao com o framework por nivel de
senioridade. As premissas do lado manual ficam em troubleshoot/impacto.py, num
lugar so, e o proprio deck imprime um slide dizendo quais sao - se o time achar
que o tempo por check e outro, ajuste la e regere.
"""
from __future__ import annotations

import argparse
import os
import sys

from troubleshoot.apresentacao import PUBLICOS, build_deck, numeros


def main() -> None:
    ap = argparse.ArgumentParser(description="Deck de apresentacao do framework")
    ap.add_argument("--gestao", action="store_true",
                    help="so o deck executivo (10 min)")
    ap.add_argument("--tecnico", action="store_true",
                    help="so o deck tecnico (20 min)")
    ap.add_argument("--impacto", action="store_true",
                    help="so o deck de impacto: tempo por senioridade (10 min)")
    ap.add_argument("--vendas", action="store_true",
                    help="so o pitch de 3 slides (tempo ganho + vantagens)")
    ap.add_argument("--abrir", action="store_true",
                    help="abre o(s) arquivo(s) ao final")
    ap.add_argument("--medir", action="store_true",
                    help="cronometra o pipeline (offline) e TRAVA a medicao "
                         "em output/apresentacao/medicao.json")
    args = ap.parse_args()

    if args.medir:
        _medir()

    alvos = [p for p, on in (("gestao", args.gestao),
                             ("tecnico", args.tecnico),
                             ("impacto", args.impacto)) if on]
    # O pitch fica FORA da lista de publicos: tem tres slides e vida propria.
    # Pedir so ele nao pode arrastar os decks longos junto.
    if args.vendas and not alvos:
        _pitch(args.abrir)
        _checklist(["vendas"])
        return
    alvos = alvos or list(PUBLICOS)

    n = numeros()
    print(f"Numeros lidos do repositorio: {n['checks']} checks, "
          f"{n['analisadores']} analisadores, {n['playbooks']} playbooks, "
          f"{n['testes']} testes")
    if "impacto" in alvos:
        _resumo_impacto(n["checks"])
    for publico in alvos:
        try:
            path = build_deck(publico)
        except ImportError:
            print("[ERRO] python-pptx nao esta instalado neste ambiente "
                  "(pip install -r requirements.txt)")
            sys.exit(2)
        except PermissionError as e:
            # Acontece toda vez que se regera o deck com ele aberto no
            # PowerPoint. O traceback de dentro do zipfile nao ajuda ninguem
            # a entender que basta fechar a janela.
            print(f"[ERRO] nao consegui gravar o deck '{publico}': o arquivo "
                  "esta aberto em outro programa (PowerPoint?). Feche e rode "
                  f"de novo.\n       {e.filename or e}")
            sys.exit(3)
        print(f"[OK] {publico}: {path}")
        if args.abrir:
            os.startfile(path)
        # O roteiro sai junto com o deck de impacto, sempre. Separar os dois
        # comandos garantiria que um dia alguem apresentasse com o deck novo e
        # o roteiro velho - dois numeros diferentes para a mesma coisa.
        if publico == "impacto":
            from troubleshoot.roteiro import build_roteiro
            rot = build_roteiro()
            print(f"[OK] roteiro de fala: {rot}")
            if args.abrir:
                os.startfile(rot)
    if args.vendas:
        _pitch(args.abrir)
    _checklist(alvos)


def _pitch(abrir: bool = False) -> None:
    """Tres slides: o ganho de tempo, a evidencia dele e as vantagens."""
    from troubleshoot.apresentacao import build_pitch

    try:
        path = build_pitch()
    except ImportError:
        print("[ERRO] python-pptx nao esta instalado neste ambiente "
              "(pip install -r requirements.txt)")
        sys.exit(2)
    except PermissionError as e:
        print("[ERRO] nao consegui gravar o pitch: o arquivo esta aberto em "
              f"outro programa (PowerPoint?).\n       {e.filename or e}")
        sys.exit(3)
    print(f"[OK] vendas (3 slides): {path}")
    if abrir:
        os.startfile(path)


def _checklist(alvos: list) -> None:
    """O que levar. Vai no fim do comando porque e o ultimo momento em que
    alguem esta olhando o terminal antes de fechar o notebook na vespera."""
    from troubleshoot import impacto as imp

    print("\n--- para a apresentacao -------------------------------------")
    print("  1. Abra o PPTX e confira as notas (fala sugerida por slide).")
    if "impacto" in alvos:
        print("  2. Leia o ROTEIRO_IMPACTO.md - preparacao, frases e as "
              "perguntas dificeis.")
        t = imp.tempo_maquina()
        if not t["medido"]:
            print("  !! SEM MEDICAO TRAVADA: rode 'APRESENTAR.py --medir' "
                  "antes de apresentar.")
        else:
            print(f"  3. Numero de abertura: {t['segundos']:.0f}s de maquina "
                  f"(medido em {t['medido_em']}).")
    print("  4. Deixe um caso JA coletado aberto e o dashboard HTML numa aba "
          "(plano B).")
    print("  5. Feche o que tiver nome de banco de producao na tela.")


def _medir() -> None:
    """Cronometra e trava o tempo real. Roda offline: nao toca em banco.

    Fica atras de uma flag de proposito. Medir a cada geracao de deck deixaria
    o comando lento e - pior - o numero mudaria conforme a maquina de quem
    gerou, o que e o oposto de uma medicao citavel num slide.
    """
    from troubleshoot import impacto as imp

    print("Medindo o pipeline sobre a evidencia ja gravada (offline, "
          "sem tocar em banco)...")
    d = imp.gravar_medicao()
    maq, col = d["maquina"], d["coleta"]
    print(f"  coleta   : {col['segundos']:.1f}s "
          f"({col['amostras']} execucao(oes) registrada(s) em coleta.log)")
    print(f"  pipeline : {maq['segundos']:.2f}s "
          f"(mediana de {maq['amostras']} execucoes sobre "
          f"{maq.get('casos', 0)} caso(s); analise + dashboard + PPTX + "
          "relatorio)")
    t = imp.tempo_maquina()
    print(f"  MAQUINA  : {t['segundos']:.1f}s por caso "
          f"[{'medido' if t['medido'] else 'premissa'}]")
    print(f"  travado em {imp.MEDICAO_JSON}")


def _resumo_impacto(checks: int) -> None:
    """Imprime o comparativo antes de gerar, para conferir sem abrir o PPTX.

    Quem vai apresentar precisa ver o numero e decidir se o defende - descobrir
    isso no meio da reuniao, com o slide projetado, e tarde demais.
    """
    from troubleshoot import impacto as imp

    t = imp.tempo_maquina()
    c = imp.comparativo(checks, t["minutos"])
    origem = (f"MEDIDO em {t['medido_em']}: {t['segundos']:.1f}s por caso "
              f"(coleta {t['coleta_s']:.1f}s + pipeline "
              f"{t['pipeline_s']:.2f}s)" if t["medido"] else
              f"premissa conservadora de {imp.mm(imp.COLETA_PADRAO_MIN)} - "
              "rode 'APRESENTAR.py --medir' para travar o tempo real")
    print(f"Impacto | maquina: {origem}")
    print(f"Impacto | cobertura: {c['checks_na_mao']} itens na mao x "
          f"{c['checks']} checks ({c['cobertura_vezes']:.1f}x)")
    for nv in imp.NIVEIS:
        v = c["niveis"][nv]
        print(f"Impacto | {imp.ROTULO[nv]:<7} {imp.mm(v['manual']):>7} na mao "
              f"-> {imp.mm(v['nosso']):>7} com o framework "
              f"({v['vezes']:.1f}x, -{imp.mm(v['economia'])})")


if __name__ == "__main__":
    main()
