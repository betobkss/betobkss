r"""
HISTORICO - serie temporal da frota a partir do que ja foi coletado.

Cada pasta em `troubleshooting/` e uma foto datada. Este comando acumula essas
fotos em `historico.json` e responde o que uma coleta isolada nao responde:
"desde quando isso vem subindo?" e "quando estoura?".

Por que importa: o AWR lembra de 8 a 15 dias, e a previsao de crescimento do
catalogo (`tablespace_forecast`) exige Diagnostics Pack. A serie de ESPACO
daqui sai do `space.txt`, que le `dba_data_files` e nao consome pacote nenhum -
o horizonte deixa de ser a retencao do banco e passa a ser a idade do projeto.

O arquivo tem DUAS camadas e elas nao se misturam:
  - 'series' : derivado da evidencia. Regerado por --atualizar.
  - 'marcos' : o que aconteceu no ambiente e nenhum SELECT explica. Preservado.

O marco e o par do salto de carga: o analisador diz QUANDO o patamar mudou, e o
marco diz O QUE mudou naquele dia.

USO:
  .\.venv\Scripts\python.exe HISTORICO.py                      # resumo da frota
  .\.venv\Scripts\python.exe HISTORICO.py --atualizar           # regera as series
  .\.venv\Scripts\python.exe HISTORICO.py APPHO600              # detalha um ambiente
  .\.venv\Scripts\python.exe HISTORICO.py APPHO600 --familia espaco
  .\.venv\Scripts\python.exe HISTORICO.py --previsao 90         # o que enche em 90 dias
  .\.venv\Scripts\python.exe HISTORICO.py --marco APPHO600 2026-09-07 "batch novo da folha"

100% OFFLINE: nao abre conexao com banco nenhum.
"""
from __future__ import annotations

import argparse
import json
import sys

from troubleshoot import historico as H


def _resumo(hist: dict) -> int:
    r = H.resumo(hist)
    if not r["ambientes"]:
        print("  historico.json vazio. Rode:  HISTORICO.py --atualizar")
        return 0
    print("=" * 70)
    print("  HISTORICO DA FROTA")
    print("=" * 70)
    print(f"  ambientes ...... {r['ambientes']}")
    print(f"  pontos ......... {r['pontos']}")
    print(f"  marcos ......... {r['marcos']}")
    if r["primeiro_dia"]:
        print(f"  horizonte ...... {r['primeiro_dia']} a {r['ultimo_dia']} "
              f"({r['dias_distintos']} dias distintos)")
    print("  por familia .... " + ", ".join(
        f"{k}={v}" for k, v in r["por_familia"].items()))
    print("-" * 70)
    print("  Detalhar:  HISTORICO.py <ambiente>")
    return 0


def _detalhar(hist: dict, nome: str, familia: str) -> int:
    chave = nome.strip().upper()
    ent = (hist.get("ambientes") or {}).get(chave)
    if not ent:
        print(f"[nao encontrado] {chave}")
        # O `or` precisa envolver so a LISTA. Escrito como
        # `"texto: " + join(...) or "(nenhum)"`, o `or` recebia a concatenacao
        # inteira - que nunca e vazia - e o ramo "(nenhum)" era codigo morto:
        # com historico vazio a mensagem terminava no dois-pontos.
        conhecidos = sorted(hist.get("ambientes") or {})
        print("  ambientes com historico: "
              + (", ".join(conhecidos) if conhecidos else "(nenhum ainda -"
                 " rode --atualizar)"))
        return 1
    print("=" * 70)
    print(f"  {chave}")
    print("=" * 70)
    for fam, nomes in (ent.get("series") or {}).items():
        if familia and fam != familia:
            continue
        print(f"\n  [{fam}]")
        for serie_nome, pts in nomes.items():
            pontos = sorted(pts.items())
            t = H.tendencia(pontos)
            atual = pontos[-1][1] if pontos else "-"
            if t:
                # r2 junto da inclinacao de proposito: serie em degraus tem
                # inclinacao positiva e r2 baixo, e extrapolar ali produz uma
                # data com cara de precisao que ninguem deveria usar.
                extra = (f"  tendencia {t['por_dia']:+.3f}/dia "
                         f"(r2 {t['r2']:.2f}, {t['pontos']}p/{t['dias']}d)")
            else:
                extra = (f"  (amostra insuficiente: {len(pontos)} ponto(s); "
                         f"precisa de {H.MIN_PONTOS} em {H.MIN_DIAS_JANELA}+ dias)")
            print(f"    {serie_nome[:34]:34} atual {atual:>9}{extra}")
    marcos = ent.get("marcos") or []
    if marcos:
        print("\n  [marcos]")
        for m in marcos:
            print(f"    {m.get('dia')}  {m.get('texto')}")
    return 0


def _previsao(hist: dict, dias: float, limite: float) -> int:
    """Series que atingem `limite` dentro de `dias`, pela reta da tendencia."""
    achados = []
    sem_amostra = 0
    for amb, ent in (hist.get("ambientes") or {}).items():
        for fam, nomes in (ent.get("series") or {}).items():
            if fam == "carga":       # carga nao "enche"; limite nao se aplica
                continue
            for nome, pts in nomes.items():
                d = H.dias_para(sorted(pts.items()), limite)
                if not d:
                    sem_amostra += 1
                    continue
                if d["dias_para"] <= dias:
                    achados.append((d["dias_para"], amb, fam, nome, d))
    print("=" * 70)
    print(f"  PREVISAO - series que chegam a {limite:g}% em {dias:g} dias")
    print("  (sem Diagnostics Pack: sai das coletas do proprio framework)")
    print("=" * 70)
    if not achados:
        print(f"  Nenhuma. {sem_amostra} serie(s) sem amostra suficiente para")
        print(f"  calcular tendencia (precisa de {H.MIN_PONTOS} coletas em")
        print(f"  {H.MIN_DIAS_JANELA}+ dias do mesmo ambiente).")
        print("\n  Isto NAO significa que nada vai encher - significa que o")
        print("  historico ainda nao tem pontos para afirmar. Colete de novo.")
        return 0
    for d, amb, fam, nome, t in sorted(achados):
        conf = "forte" if t["r2"] >= 0.8 else "fraca" if t["r2"] < 0.5 else "media"
        print(f"  {amb:14} {fam:7} {nome[:24]:24} {t['valor_atual']:6.1f}% "
              f"-> {limite:g}% em ~{d:5.0f} dia(s)   r2 {t['r2']:.2f} ({conf})")
    print("-" * 70)
    print("  r2 baixo = crescimento em degraus, nao em reta: a data e chute.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Serie temporal da frota a partir da evidencia coletada")
    ap.add_argument("ambiente", nargs="?", help="detalha um ambiente")
    ap.add_argument("--atualizar", action="store_true",
                    help="regera 'series' a partir de troubleshooting/")
    ap.add_argument("--familia", help="filtra a familia (carga/espaco/fra/asm)")
    ap.add_argument("--previsao", type=float, metavar="DIAS",
                    help="lista o que atinge o limite dentro de N dias")
    ap.add_argument("--limite", type=float, default=90.0,
                    help="limite da previsao em %% (padrao 90)")
    ap.add_argument("--marco", nargs=3, metavar=("AMBIENTE", "DIA", "TEXTO"),
                    help="registra o que aconteceu num dia (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true", help="saida em JSON")
    args = ap.parse_args()

    if args.marco:
        amb, dia, texto = args.marco
        try:
            hist = H.marcar(amb, dia, texto)
        except ValueError as ex:
            print(f"[ERRO] {ex}")
            return 1
        H.gravar(hist)
        print(f"[OK] marco registrado em {amb.upper()} / {dia}: {texto}")
        return 0

    if args.atualizar:
        hist, novos, pontos = H.atualizar()
        H.gravar(hist)
        print(f"[OK] historico atualizado: {pontos} ponto(s) em "
              f"{len(hist.get('ambientes', {}))} ambiente(s)")
        if novos:
            print("     novos: " + ", ".join(sorted(novos)))
        return 0

    hist = H.ler()
    if args.json:
        print(json.dumps(hist if args.ambiente is None else
                         (hist.get("ambientes") or {}).get(
                             args.ambiente.upper(), {}),
                         ensure_ascii=False, indent=2))
        return 0
    if args.previsao is not None:
        return _previsao(hist, args.previsao, args.limite)
    if args.ambiente:
        return _detalhar(hist, args.ambiente, (args.familia or "").strip())
    return _resumo(hist)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
