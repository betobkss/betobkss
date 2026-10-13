"""
Reincidencia de achados entre casos do MESMO servidor.

Por que existe: o `history.json` responde "este caso melhorou ou piorou desde a
ultima analise", mas nao responde a pergunta que decide prioridade de verdade -
"este achado ja apareceu antes neste banco?". Um `ORA-00257` que aparece pela
primeira vez e um incidente; o mesmo `ORA-00257` pela quarta vez em 30 dias e um
problema cronico, e tratar de novo o sintoma nao vai resolver.

De onde vem o dado (nada novo e coletado):
  - `output/troubleshooting/<caso>/history.json` - um snapshot por execucao de
    relatorio, com `generated_at` e a lista de achados (titulo + severidade).
  - `troubleshooting/<caso>/meta.json` - o campo `conn` (ou `hostname`) e o que
    diz de que servidor aquele caso veio.

Duas decisoes que evitam contagem inflada:

1. **Uma ocorrencia por caso e por DIA.** Regerar o relatorio do mesmo caso tres
   vezes na mesma tarde acrescenta tres snapshots ao `history.json`; sem essa
   deduplicacao, o achado passaria a "3 ocorrencias" sem nada ter acontecido no
   banco. Reincidencia e sobre o banco, nao sobre quantas vezes clicamos.

2. **O "agora" e a data da analise (`ref`), nao o relogio.** Mesma regra do
   `analyzers/base.coletado_em`: reabrir um caso de marco nao pode passar a
   contar a janela a partir de hoje.

Isto NAO gera `Finding`: entra como secao propria no relatorio. Achado e o que a
evidencia daquela coleta mostra; reincidencia e leitura de historico e nao pode
mexer no Health Score nem no corpus de regressao.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import caso as caso_lifecycle

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")
CASES_DIR = os.path.join(BASE_DIR, "troubleshooting")

# Janela e piso padrao. 30 dias cobre o ciclo tipico de um mes de operacao;
# 2 ocorrencias e o minimo que caracteriza repeticao (1 e a primeira vez).
DIAS_PADRAO = 30
MINIMO_PADRAO = 2
_FMT = "%Y-%m-%d %H:%M"


def _quando(txt: str):
    """Converte 'YYYY-MM-DD HH:MM' (ou so a data) em datetime; None se nao der.

    Corta pelo tamanho do formato porque o carimbo pode vir com segundos.
    """
    t = (txt or "").strip()
    if not t:
        return None
    for fmt, n in ((_FMT, 16), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(t[:n], fmt)
        except ValueError:
            continue
    return None


def historico(caso: str, out_dir: str = "") -> list:
    """Snapshots ja gravados do caso (mais antigo primeiro). [] se nao houver."""
    p = os.path.join(out_dir or OUTPUT_DIR, caso, "history.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return []
    return [d for d in dados if isinstance(d, dict)] if isinstance(dados, list) else []


def servidor_do_caso(caso: str, cases_dir: str = "") -> str:
    """Banco de origem do caso, em maiusculas. '' quando o caso nao tem meta.json.

    `conn` e o nome da conexao usada pelo COLETAR (o que o DBA reconhece);
    `hostname` so entra como reserva, para caso antigo que so tem essa chave.
    """
    d = caso_lifecycle.ler(caso, cases_dir)
    return ((d.get("conn") or d.get("hostname") or "").strip()).upper()


def casos_do_servidor(servidor: str, cases_dir: str = "") -> list:
    """Casos cuja evidencia veio do mesmo servidor (ordem alfabetica)."""
    servidor = (servidor or "").strip().upper()
    if not servidor:
        return []
    base = cases_dir or CASES_DIR
    if not os.path.isdir(base):
        return []
    return sorted(n for n in os.listdir(base)
                  if os.path.isdir(os.path.join(base, n))
                  and servidor_do_caso(n, base) == servidor)


def analisar(caso: str, titulos, ref: str = "", dias: int = DIAS_PADRAO,
             minimo: int = MINIMO_PADRAO, out_dir: str = "",
             cases_dir: str = "") -> list:
    """Quais dos achados ATUAIS (`titulos`) ja tinham aparecido no mesmo servidor.

    Devolve, por achado reincidente e do mais repetido para o menos:
      titulo, ocorrencias, dias_distintos, casos, servidor, primeira, ultima.

    `ref` e a data da analise ('YYYY-MM-DD HH:MM'); sem ela, a janela e contada
    a partir do snapshot mais recente encontrado - nunca do relogio de hoje.
    """
    # Dedupe preservando a ordem: dois analisadores podem emitir o mesmo titulo
    # no mesmo caso, e isso nao pode virar duas linhas de reincidencia.
    titulos = list(dict.fromkeys(t.strip() for t in (titulos or []) if t))
    if not titulos:
        return []
    alvo = set(titulos)
    servidor = servidor_do_caso(caso, cases_dir)
    # O proprio caso sempre entra: mesmo sem meta.json, a repeticao dentro de um
    # unico caso ao longo de varias analises ja e reincidencia legitima.
    casos = casos_do_servidor(servidor, cases_dir) or []
    if caso not in casos:
        casos = [caso, *casos]

    # titulo -> {(caso, dia)} : a chave e o par, e nao o snapshot, para que
    # regerar o relatorio no mesmo dia nao vire "ocorrencia" nova.
    vistos: dict[str, dict] = {}
    for c in casos:
        for snap in historico(c, out_dir):
            q = _quando(snap.get("generated_at", ""))
            if not q:
                continue
            for f in snap.get("findings") or []:
                t = (f.get("title") or "").strip()
                if t in alvo:
                    vistos.setdefault(t, {})[(c, q.date())] = q

    limite = _quando(ref)
    if limite is None:
        todos = [q for m in vistos.values() for q in m.values()]
        limite = max(todos) if todos else None

    saida = []
    for t in titulos:
        pares = vistos.get(t) or {}
        if limite is not None:
            pares = {k: v for k, v in pares.items()
                     if 0 <= (limite.date() - k[1]).days <= dias}
        if len(pares) < minimo:
            continue
        datas = sorted(pares.values())
        saida.append({
            "titulo": t,
            "servidor": servidor,
            "ocorrencias": len(pares),
            "dias_distintos": len({k[1] for k in pares}),
            "casos": sorted({k[0] for k in pares}),
            "primeira": datas[0].strftime(_FMT),
            "ultima": datas[-1].strftime(_FMT),
            "janela_dias": dias,
        })
    saida.sort(key=lambda r: (-r["ocorrencias"], r["titulo"]))
    return saida


def do_resultado(result, dias: int = DIAS_PADRAO, minimo: int = MINIMO_PADRAO,
                 out_dir: str = "", cases_dir: str = "") -> list:
    """Atalho para o relatorio: reincidencia dos achados de um CaseResult."""
    titulos = [f.title for s in result.sections for f in s.findings]
    return analisar(result.case, titulos, ref=result.generated_at, dias=dias,
                    minimo=minimo, out_dir=out_dir, cases_dir=cases_dir)


def frase(r: dict) -> str:
    """Uma linha em portugues para o achado reincidente."""
    onde = (f" (em {len(r['casos'])} casos)" if len(r["casos"]) > 1
            else " (sempre no mesmo caso)")
    return (f"{r['ocorrencias']}x em {r['janela_dias']} dias{onde} - "
            f"de {r['primeira']} a {r['ultima']}")


def markdown(reincidentes: list) -> str:
    """Secao do relatorio .md. Texto explicito quando nao ha reincidencia."""
    if not reincidentes:
        return ("- Nenhum achado atual ja havia aparecido neste servidor na "
                f"janela de {DIAS_PADRAO} dias (ou nao ha historico suficiente).")
    srv = reincidentes[0].get("servidor") or "este servidor"
    linhas = [f"Achados que **ja se repetiram** em {srv} - tratar a causa, "
              "nao o sintoma:", ""]
    linhas += [f"- **{r['titulo']}** - {frase(r)}" for r in reincidentes]
    linhas += ["", "> Casos com o mesmo achado: "
               + "; ".join(f"{r['titulo']}: {', '.join(r['casos'])}"
                           for r in reincidentes[:5])]
    return "\n".join(linhas)
