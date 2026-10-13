r"""
AMBIENTES - inventario de frota a partir do que ja foi coletado.

A deteccao de topologia roda em toda coleta e grava `topology.json` no caso -
mas esse conhecimento fica preso ali. Este comando agrega tudo em
`ambientes.json` e responde, sem abrir caso nenhum: quantos CDB existem, quais
bancos dividem host, quem esta em qual versao, quem e cluster.

O arquivo tem DUAS camadas e elas nao se misturam:
  - 'detectado': derivado dos topology.json. Regerado por --atualizar.
  - 'notas'    : conhecimento humano, que nenhum SELECT descobre. Preservado.

USO:
  .\.venv\Scripts\python.exe AMBIENTES.py                    # lista a frota
  .\.venv\Scripts\python.exe AMBIENTES.py --atualizar        # regera o detectado
  .\.venv\Scripts\python.exe AMBIENTES.py <SERVIDOR>         # detalha um ambiente
  .\.venv\Scripts\python.exe AMBIENTES.py --nota <SERVIDOR> "listener nao sobe sozinho"
  .\.venv\Scripts\python.exe AMBIENTES.py --registrar <SERVIDOR> --host <host>
  .\.venv\Scripts\python.exe AMBIENTES.py --hosts            # bancos que dividem host

100% OFFLINE: nao abre conexao com banco nenhum.
"""
from __future__ import annotations

import argparse
import json
import sys

from troubleshoot import ambientes as A

_SIGLA_CRIT = {"PRODUCAO": "PROD", "HOMOLOGACAO": "homol", "?": "?"}


def _confirmada(ent: dict) -> str:
    return ((ent.get("criticidade_confirmada") or {}).get("valor") or "")


def _crit_de(chave: str, ent: dict) -> str:
    """Criticidade efetiva do ambiente, ja considerando a confirmacao humana.

    Existe para os tres pontos de exibicao nao divergirem: lista de frota,
    detalhe e contagem tinham de chamar a mesma coisa, senao um mostra '?' e o
    outro mostra PRODUCAO para o mesmo ambiente.
    """
    return A.criticidade(chave, _hosts_de(ent), _confirmada(ent))


def _linha(chave: str, ent: dict) -> str:
    det = ent.get("detectado") or {}
    n = len(ent.get("notas") or [])
    marca = "*" if n else " "
    # Coluna curta e em MAIUSCULA so para producao: a lista tem dezenas de
    # linhas e o olho precisa achar as criticas sem ler o resto.
    crit = _SIGLA_CRIT.get(_crit_de(chave, ent), "?")
    if not det:
        return (f"{marca}{chave:<22} {crit:<6} {'(sem coleta)':<14} "
                f"{'-':<16} manual")
    hosts = ", ".join(det.get("hosts") or []) or "-"
    tags = ",".join(t for t in (det.get("tags") or [])
                    if t not in ("archivelog",))
    return (f"{marca}{chave:<22} {crit:<6} {(det.get('kind') or '?'):<14} "
            f"{(det.get('versao') or '?'):<16} {hosts.split('.')[0]:<22} {tags}")


def _hosts_de(ent: dict) -> list:
    """Hosts do ambiente, venham da coleta ou do cadastro manual."""
    det = ent.get("detectado") or {}
    hosts = list(det.get("hosts") or [])
    for k in ("host", "hosts"):
        v = ent.get(k)
        if isinstance(v, str) and v:
            hosts.append(v)
        elif isinstance(v, list):
            hosts.extend(str(x) for x in v if x)
    return hosts


def _mostrar_um(chave: str, ent: dict) -> int:
    det = ent.get("detectado") or {}
    crit = _crit_de(chave, ent)
    print("=" * 74)
    print(f"  AMBIENTE {chave}   [origem: {ent.get('origem', '?')}]")
    print("=" * 74)
    # Primeira linha de proposito: e ela que define a conduta do resto da
    # sessao. Em producao, acao destrutiva exige janela, aprovacao e rollback
    # escrito; em homologacao o mesmo comando e rotina.
    print(f"  {'Criticidade':18}: {crit}"
          + ("   <<< producao: janela, aprovacao e rollback"
             if crit == "PRODUCAO" else
             "   <<< nome nao revela - PERGUNTE antes de agir"
             if crit == "?" else ""))
    # De ONDE veio a criticidade importa: derivada do nome e permanente,
    # confirmada por humano e uma afirmacao datada que alguem pode ter que
    # revisar. Esconder a diferenca faz as duas parecerem igualmente solidas.
    conf = ent.get("criticidade_confirmada") or {}
    if conf.get("valor"):
        vale = " (e o que vale)" if crit == conf["valor"] else \
               f" (mas vale {crit}, derivado do nome/host)"
        print(f"  {'':18}  confirmado por humano em {conf.get('em','?')}: "
              f"{conf['valor']}{vale}")
    if det:
        for rot, k in (("Perfil", "perfil"), ("Tipo", "kind"),
                       ("Banco", "db_name"), ("Versao", "versao"),
                       ("Papel", "papel"), ("Log mode", "log_mode"),
                       ("Open mode", "open_mode"), ("Ambiente", "ambiente"),
                       ("Nos no ar", "nos_no_ar"),
                       ("Nos configurados", "nos_configurados"),
                       ("PDBs", "pdbs"), ("Visto em", "visto_em")):
            v = det.get(k)
            if v not in (None, "", []):
                print(f"  {rot:18}: {v}")
        for rot, k in (("Hosts", "hosts"), ("Instancias", "instancias"),
                       ("Tags", "tags"), ("Casos", "casos")):
            v = det.get(k) or []
            if v:
                print(f"  {rot:18}: {', '.join(str(x) for x in v)}")
    else:
        print("  Sem coleta: o que se sabe esta nas notas abaixo.")
        for k, v in ent.items():
            if k not in ("origem", "notas", "detectado") and v:
                print(f"  {k:18}: {v}")
    notas = ent.get("notas") or []
    if notas:
        print(f"\n  Notas ({len(notas)}):")
        for nt in notas:
            print(f"    - [{nt.get('em', '')}] {nt.get('texto', '')}")
    else:
        print("\n  (sem notas - use --nota para registrar o que so voce sabe)")
    return 0


def _listar(inv: dict, r: dict) -> int:
    ambientes = inv.get("ambientes") or {}
    if not ambientes:
        print("Inventario vazio. Rode:  AMBIENTES.py --atualizar")
        return 0
    print("=" * 96)
    print(f"  FROTA - {r['total']} ambiente(s), {r['com_coleta']} com coleta")
    print("=" * 96)
    print(f" {'AMBIENTE':<22} {'CRIT':<6} {'TIPO':<14} {'VERSAO':<16} "
          f"{'HOST':<22} TAGS")
    for chave, ent in ambientes.items():
        print(_linha(chave, ent))
    print("\n  (*) tem nota registrada")
    crits = [_crit_de(k, e) for k, e in ambientes.items()]
    print(f"  CRIT: PROD={crits.count('PRODUCAO')}  "
          f"homol={crits.count('HOMOLOGACAO')}  ?={crits.count('?')}"
          + ("   <<< '?' = o nome nao revela; pergunte antes de agir"
             if "?" in crits else ""))
    for rot, mapa in (("Tipo", r["por_kind"]), ("Versao", r["por_versao"])):
        if mapa:
            print(f"\n  Por {rot.lower()}: "
                  + "  ".join(f"{k}={v}" for k, v in mapa.items()))
    if r["por_tag"]:
        print("  Por tag:    "
              + "  ".join(f"{k}={v}" for k, v in r["por_tag"].items()))
    if r["sem_coleta"]:
        print(f"\n  Sem coleta ({len(r['sem_coleta'])}): "
              + ", ".join(r["sem_coleta"]))
    comp = r["hosts_compartilhados"]
    if comp:
        print(f"\n  [!] {len(comp)} host(s) com mais de um banco - metrica de "
              "host e COMUM, nao conte duas vezes:")
        for h, quais in comp.items():
            print(f"      {h}: {', '.join(quais)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inventario de frota a partir dos topology.json coletados",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ambiente", nargs="?", help="detalha um ambiente pelo nome")
    ap.add_argument("--atualizar", action="store_true",
                    help="regera o bloco 'detectado' a partir de troubleshooting/")
    ap.add_argument("--nota", nargs=2, metavar=("AMBIENTE", "TEXTO"),
                    help="registra conhecimento que nenhum SELECT descobre")
    ap.add_argument("--criticidade", nargs=2, metavar=("AMBIENTE", "VALOR"),
                    help="confirma a criticidade que o NOME nao revela: "
                         "PRODUCAO ou HOMOLOGACAO. Nao sobrescreve o que a "
                         "convencao de hostname ja diz")
    ap.add_argument("--registrar", metavar="NOME",
                    help="cadastra ambiente conhecido SEM coleta (origem manual)")
    ap.add_argument("--host", default="", help="host, para usar com --registrar")
    ap.add_argument("--hosts", action="store_true",
                    help="lista apenas os hosts com mais de um banco")
    ap.add_argument("--json", action="store_true", help="saida crua em JSON")
    args = ap.parse_args()

    if args.atualizar:
        inv, novos, atualizados = A.atualizar()
        destino = A.gravar(inv)
        print(f"[OK] {destino}")
        print(f"     {len(novos)} novo(s), {len(atualizados)} atualizado(s)")
        if novos:
            print("     novos: " + ", ".join(sorted(novos)))
        return 0

    if args.nota:
        alvo, texto = args.nota
        try:
            inv = A.anotar(alvo, texto)
        except ValueError as e:
            print(f"[ERRO] {e}")
            return 2
        A.gravar(inv)
        print(f"[OK] Nota registrada em {alvo.upper()}")
        return 0

    if args.criticidade:
        alvo, valor = args.criticidade
        try:
            inv = A.confirmar_criticidade(alvo, valor)
        except ValueError as e:
            print(f"[ERRO] {e}")
            return 2
        A.gravar(inv)
        chave = alvo.upper()
        ent = inv["ambientes"].get(chave) or {}
        hosts = [ent.get("host")] if ent.get("host") else []
        # O derivado do NOME vence. Se ele disser algo diferente do que o humano
        # confirmou, a confirmacao fica gravada mas nao vale - e isso tem de
        # aparecer, senao alguem acha que mudou o comportamento e nao mudou.
        efetiva = A.criticidade(chave, hosts, valor.upper())
        print(f"[OK] {chave}: criticidade confirmada como {valor.upper()}")
        if efetiva != valor.upper():
            print(f"  [!] Mas vale {efetiva}: o NOME ou o HOST ja indicam isso, "
                  "e a derivacao tem precedencia (nome nao envelhece, nota sim).")
            print("      Se o nome estiver errado, o caminho e corrigir o nome, "
                  "nao a nota.")
        return 0

    if args.registrar:
        try:
            inv = A.registrar_manual(args.registrar, host=args.host)
        except ValueError as e:
            print(f"[ERRO] {e}")
            return 2
        A.gravar(inv)
        print(f"[OK] {args.registrar.upper()} cadastrado (origem manual). "
              "Use --nota para dizer o que se sabe dele.")
        return 0

    inv = A.ler()
    r = A.resumo(inv)

    if args.json:
        print(json.dumps(inv if not args.ambiente else
                         inv["ambientes"].get(args.ambiente.upper(), {}),
                         ensure_ascii=False, indent=2))
        return 0

    if args.hosts:
        comp = r["hosts_compartilhados"]
        if not comp:
            print("Nenhum host com mais de um banco no inventario.")
            return 0
        for h, quais in comp.items():
            print(f"{h}: {', '.join(quais)}")
        return 0

    if args.ambiente:
        chave = args.ambiente.strip().upper()
        ent = (inv.get("ambientes") or {}).get(chave)
        if not ent:
            print(f"[!] '{chave}' nao esta no inventario.")
            print("    Rode AMBIENTES.py --atualizar, ou cadastre com "
                  f"--registrar {chave}")
            return 1
        return _mostrar_um(chave, ent)

    return _listar(inv, r)


if __name__ == "__main__":
    sys.exit(main())
