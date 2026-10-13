"""
CASO - ciclo de vida do caso de troubleshooting (hipoteses, acoes, status, RCA).

Registra o RACIOCINIO ao lado da EVIDENCIA. O que for gravado aqui aparece
preenchido no relatorio (`<caso>_relatorio.md`), no dashboard de casos
(`output/troubleshooting/index.html`) e no consolidado do "Fim do dia" - em
lugar do texto genérico que o relatorio usa quando nao sabe a hipotese.

Grava em troubleshooting/<caso>/meta.json, junto das evidencias, preservando o
que a coleta escreveu (conn, dsn, title...).

Exemplos:
  # ver o estado do caso
  python CASO.py INC000987654

  # hipotese inicial; depois 'proximo' (descarta a atual e guarda no historico)
  python CASO.py INC000987654 --hipotese "Contencao de row lock no batch noturno"
  python CASO.py INC000987654 --hipotese "Plano trocou apos coleta de stats" \
                              --motivo "sem locks na janela do incidente"

  # acoes, com nivel de risco (nenhum | baixo | medio | alto)
  python CASO.py INC000987654 --acao "Coletado ASH da janela" --risco nenhum
  python CASO.py INC000987654 --acao "Kill da sessao 1234" --risco alto

  # RCA e fechamento
  python CASO.py INC000987654 --impacto "3 aplicacoes criticas lentas por 40min"
  python CASO.py INC000987654 --resolver --causa "Indice ausente em TAB_X" \
                              --solucao "Criado IDX_TAB_X_DT" \
                              --prevencao "Revisao de plano antes do deploy"

  # o caso no Template Padrao, para colar no chamado
  python CASO.py INC000987654 --redija

  # consolidacao do dia (Fim do dia) e lista geral
  python CASO.py --fim-do-dia
  python CASO.py --listar
"""
from __future__ import annotations

import argparse
import sys

from troubleshoot import caso as cl

# Conduta por criticidade, na linguagem da regra always-on. Sai junto do rotulo
# porque rotulo sozinho ("PRODUCAO") nao muda comportamento de ninguem - o que
# muda e a exigencia escrita ao lado dele.
_CONDUTA = {
    "PRODUCAO": "janela, aprovacao, rollback escrito e backup conferido",
    "HOMOLOGACAO": "pode agir direto; o read-only antes continua valendo",
    "?": "PERGUNTE antes de agir - o nome nao revela o ambiente",
}


def _linha_criticidade(crit: str) -> str:
    return f"  {'Criticidade':18}: {crit}   <<< {_CONDUTA.get(crit, '')}"


def _avisa_criticidade(nome: str, crit: str) -> None:
    """Sai em TODA escrita: quem acaba de registrar hipotese e o proximo a
    propor acao, e e aqui que a exigencia de janela precisa aparecer.

    Ate 24/09/2026 dava para abrir caso, registrar hipotese e propor acao sem
    nunca informar o ambiente, e nada avisava. O risco nao e simetrico: tratar
    homologacao como producao custa uma janela; o inverso custa incidente.
    """
    if crit == "?":
        print(f"  [!] Criticidade '?' - {_CONDUTA['?']}")
        # Ate 28/09/2026 esta mensagem dizia "Sem hostname nem conn no caso"
        # sempre - inclusive quando o host FOI informado e simplesmente nao
        # revela o ambiente. Mandava registrar o que ja estava registrado, tres
        # vezes na mesma semana. Sao duas situacoes com saidas diferentes.
        d = cl.ler(nome)
        if d.get("hostname") or d.get("conn"):
            alvo = d.get("conn") or d.get("hostname")
            print(f"      O host esta registrado ({alvo}) e nao revela o "
                  "ambiente. Se voce SABE qual e, confirme no inventario:")
            print("      python AMBIENTES.py --criticidade <AMBIENTE> "
                  "PRODUCAO|HOMOLOGACAO")
        else:
            print("      Sem hostname nem conn no caso. Registre e o rigor fica "
                  f"definido:\n      python CASO.py {nome} "
                  "--hostname <host ou banco>")
        return
    print(f"  Criticidade: {crit}   <<< {_CONDUTA[crit]}")


def _mostrar(nome: str) -> int:
    d = cl.ler(nome)
    if not d:
        print(f"[!] Caso '{nome}' nao encontrado em troubleshooting\\{nome}\\")
        print("    Colete primeiro:  python COLETAR.py " + nome + " --conn <banco>")
        return 1
    r = cl.resumo(nome)
    print("=" * 68)
    print(f"  CASO {nome}   [{r['status']}]")
    print("=" * 68)
    # Criticidade ANTES de tudo: e ela que decide se o proximo passo precisa de
    # janela e aprovacao, entao ler o resto do caso sem ela ja e ler na ordem
    # errada.
    print(_linha_criticidade(r["criticidade"]))
    for rot, val in (("Hostname/banco", r["hostname"]), ("Evento", r["evento"]),
                     ("Hipotese inicial", r["hipotese_inicial"]),
                     ("Hipotese atual", r["hipotese_atual"]),
                     ("Causa raiz", r["causa_raiz"]),
                     ("Escalado", r["escalado"]),
                     ("Criado em", r["criado_em"]),
                     ("Resolvido em", r["resolvido_em"])):
        if val:
            print(f"  {rot:18}: {val}")

    hips = d.get("hipoteses") or []
    if hips:
        print(f"\n  Hipoteses descartadas ({len(hips)}):")
        for h in hips:
            print(f"    - {h.get('texto','')}")
            print(f"      descartada em {h.get('descartada_em','')}: {h.get('motivo','')}")

    acoes = d.get("acoes") or []
    if acoes:
        altas = sum(1 for a in acoes if a.get("risco") == "alto")
        print(f"\n  Acoes ({len(acoes)}"
              + (f", {altas} de risco ALTO" if altas else "") + "):")
        for a in acoes:
            marca = "[!]" if a.get("risco") == "alto" else "   "
            print(f"    {marca} {a.get('em','')}  ({a.get('risco','')})  {a.get('descricao','')}")
    if not r["tem_ciclo_de_vida"]:
        print("\n  (sem hipotese nem acao registrada - use --hipotese / --acao)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ciclo de vida do caso de troubleshooting (hipoteses, acoes, RCA)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Sem argumentos de acao, apenas mostra o estado do caso.")
    ap.add_argument("caso", nargs="?", help="nome do caso (ex.: INC000987654)")
    ap.add_argument("--hipotese", help="define a hipotese inicial ou avanca para "
                                       "a proxima (a anterior vai para o historico)")
    ap.add_argument("--motivo", default="", help="por que a hipotese anterior foi descartada")
    ap.add_argument("--acao", help="registra uma acao executada")
    ap.add_argument("--risco", default="nenhum",
                    help="nivel de risco da acao: nenhum | baixo | medio | alto "
                         "(alto = acao critica: kill, flush, rebuild, shrink, drop)")
    ap.add_argument("--status", help="Investigando | Aguardando | Escalado | Resolvido")
    ap.add_argument("--evento", help="sintoma principal (evento de referencia)")
    ap.add_argument("--hostname", help="servidor de referencia")
    ap.add_argument("--timeline", help="timeline do problema (RCA)")
    ap.add_argument("--impacto", help="impacto no negocio/infra (RCA)")
    ap.add_argument("--prevencao", help="medidas preventivas (RCA)")
    ap.add_argument("--escalar", metavar="PARA", nargs="?", const="",
                    help="marca o caso como escalado (opcional: para quem)")
    ap.add_argument("--resolver", action="store_true", help="fecha o caso")
    ap.add_argument("--causa", default="",
                    help="causa raiz (com ou sem --resolver: caso escalado pode "
                         "ter causa conhecida e solucao pendente)")
    ap.add_argument("--solucao", default="", help="solucao aplicada")
    ap.add_argument("--fim-do-dia", dest="fim_do_dia", action="store_true",
                    help="consolida TODOS os casos num CSV (Excel)")
    ap.add_argument("--dia", default="", help="filtra o consolidado por dia (YYYY-MM-DD)")
    ap.add_argument("--redija", action="store_true",
                    help="imprime o caso no Template Padrao (Diagnostico + RCA), "
                         "pronto para colar no chamado")
    ap.add_argument("--familia", help="agrupa o caso numa familia de sintoma "
                                      "(texto livre, reaproveite a grafia que "
                                      "--familias mostrar)")
    ap.add_argument("--familias", action="store_true",
                    help="lista as familias e quantos casos cada uma tem, "
                         "marcando as que ja dao para virar playbook")
    ap.add_argument("--listar", action="store_true", help="lista os casos existentes")
    ap.add_argument("--todos", action="store_true",
                    help="em --listar e --fim-do-dia, inclui tambem as fotos de "
                         "coleta (healthcheck, bateria de homologacao), que por "
                         "padrao nao contam como caso")
    args = ap.parse_args()

    if args.fim_do_dia:
        destino, linhas = cl.consolidar_dia(args.dia, todos=args.todos)
        altas = sum(int(l["acoes_risco_alto"] or 0) for l in linhas)
        abertos = sum(1 for l in linhas if l["status"] in cl.STATUS_ABERTOS)
        print(f"[OK] Consolidado: {destino}")
        print(f"     {len(linhas)} caso(s) | {abertos} em aberto | "
              f"{altas} acao(oes) de risco ALTO")
        if not args.todos:
            print("     (so investigacao; --todos inclui as fotos de coleta)")
        return 0

    if args.familias:
        fam = cl.familias()
        if not fam:
            print("Nenhuma familia registrada ainda.")
            print("  Marque um caso com:  python CASO.py <CASO> "
                  "--familia \"<nome>\"")
            return 0
        print(f"{'FAMILIA':<38} {'N':>3}  CASOS")
        prontas = 0
        for chave, casos in fam.items():
            marca = " <<<" if len(casos) >= cl.MIN_PARA_PLAYBOOK else ""
            prontas += 1 if len(casos) >= cl.MIN_PARA_PLAYBOOK else 0
            print(f"{chave:<38} {len(casos):>3}  {', '.join(casos)}{marca}")
        print(f"\n  {cl.MIN_PARA_PLAYBOOK}+ casos = ja da para escrever playbook "
              "com base em repeticao, nao em anedota.")
        if prontas:
            print(f"  {prontas} familia(s) marcada(s) com <<< atingiram o minimo.")
        return 0

    if args.listar:
        casos = cl.listar_casos(so_troubleshooting=not args.todos)
        if not casos:
            print("Nenhum caso de investigacao em troubleshooting\\"
                  if not args.todos else "Nenhum caso em troubleshooting\\")
            if not args.todos:
                print("  (--todos mostra tambem as fotos de coleta)")
            return 0
        print(f"{'CASO':<28} {'CRIT':<6} {'STATUS':<14} {'ACOES':>5}  HIPOTESE ATUAL")
        for nome in casos:
            r = cl.resumo(nome)
            hip = (r["hipotese_atual"] or "-")[:38]
            crit = {"PRODUCAO": "PROD", "HOMOLOGACAO": "homol"}.get(
                r["criticidade"], "?")
            print(f"{nome:<28} {crit:<6} {r['status']:<14} {r['acoes']:>5}  {hip}")
        if not args.todos:
            ocultos = len(cl.listar_casos()) - len(casos)
            if ocultos:
                print(f"\n  + {ocultos} foto(s) de coleta ocultada(s). "
                      "Veja com --todos.")
        return 0

    if not args.caso:
        ap.print_help()
        return 2

    # Antes das escritas: 'redija' e leitura pura e nao deve mudar o caso ao
    # ser combinado por engano com outra flag.
    if args.redija:
        if not cl.ler(args.caso):
            print(f"[!] Caso '{args.caso}' nao encontrado em "
                  f"troubleshooting\\{args.caso}\\")
            return 1
        print(cl.texto_padrao(args.caso), end="")
        return 0

    mudou = False
    try:
        if args.hipotese:
            d = cl.nova_hipotese(args.caso, args.hipotese, args.motivo)
            n = len(d.get("hipoteses") or [])
            print(f"[OK] Hipotese atual: {d['hipotese_atual']}"
                  + (f"  ({n} descartada(s) no historico)" if n else ""))
            mudou = True
        if args.acao:
            cl.registrar_acao(args.caso, args.acao, args.risco)
            aviso = "  <<< ACAO CRITICA" if args.risco.lower() == "alto" else ""
            print(f"[OK] Acao registrada (risco {args.risco.lower()}){aviso}")
            mudou = True
        livres = {k: getattr(args, k) for k in
                  ("status", "evento", "hostname", "timeline", "impacto",
                   "prevencao", "familia")
                  if getattr(args, k)}
        # Causa raiz e solucao SEM --resolver: um caso escalado pode ter causa
        # estabelecida e solucao pendente de terceiro (capacidade, fornecedor,
        # outra equipe). Ate 22/09/2026 --causa era ignorada em silencio sem
        # --resolver, e o DBA tinha duas saidas ruins: fechar um caso que nao
        # acabou, ou mandar o chamado com "<preencher>" na Causa Raiz que ele
        # ja conhecia. O `resolver` continua gravando os dois de uma vez.
        if not args.resolver:
            if args.causa:
                livres["causa_raiz"] = args.causa
            if args.solucao:
                livres["solucao"] = args.solucao
        if livres:
            cl.definir(args.caso, **livres)
            print(f"[OK] Atualizado: {', '.join(sorted(livres))}")
            mudou = True
        if args.escalar is not None:
            d = cl.escalar(args.caso, args.escalar)
            print(f"[OK] Escalado: {d['escalado']}")
            mudou = True
        if args.resolver:
            cl.resolver(args.caso, args.causa, args.solucao, args.prevencao)
            print("[OK] Caso resolvido")
            mudou = True
    except ValueError as e:
        print(f"[ERRO] {e}")
        return 2

    if mudou:
        _avisa_criticidade(args.caso, cl.criticidade(args.caso))
        print(f"     meta.json: {cl.caminho_meta(args.caso)}")
        print("     Regere o relatorio para refletir:  "
              f".\\redija.bat {args.caso}")
        return 0
    return _mostrar(args.caso)


if __name__ == "__main__":
    sys.exit(main())
