"""
copiloto.py - o caso empacotado para colar num LLM (Copilot do Teams).

## Por que uma ponte por copiar/colar, e nao uma integracao

O Copilot que ja esta logado no Teams **nao e um LLM programavel**. O token
daquela sessao e do Teams e nao serve para um processo local. A alternativa
oficial e a Microsoft 365 Copilot **Chat API** (Graph, `/beta/copilot/...`), que
hoje: esta em preview e nao suportada em producao; so aceita permissao
**delegada** (app-only nao existe); exige registro de app no Entra com consenso
de admin sobre permissoes largas (`Mail.Read`, `Chat.Read`, `Sites.Read.All`...);
e exige licenca add-on por usuario. Num banco, isso e processo de governanca -
nao configuracao.

Enquanto isso, o valor pratico e quase todo capturavel sem integracao nenhuma:
montar o bloco de texto certo e deixar o DBA colar. Ganhos que nao sao pequenos:
funciona hoje, com a licenca que ja existe; nada sai da maquina por iniciativa
da ferramenta; e quem cola VE o que esta colando.

## O que este modulo cuida

1. **Cabe na caixa.** Colar o prompt vivo inteiro mais 80 achados nao ajuda: a
   janela e finita e o essencial afunda. O bloco leva a persona resumida, o
   estado do caso e os achados **graves**, do pior para o melhor.
2. **Diz o que se espera de volta.** Sem o pedido explicito, o LLM devolve aula
   sobre Oracle. O bloco fecha pedindo confirmacao/refutacao da hipotese atual e
   o proximo dado a coletar.
3. **Anonimizacao opcional.** `anonimizar=True` troca o hostname/servidor por um
   marcador. Evidencia de banco de producao tem nome de servidor, e nem todo
   destino merece esse detalhe.

Nada aqui chama rede. E montagem de texto.
"""
from __future__ import annotations

import json
import os

from . import caso as caso_lifecycle

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")

# Quantos achados vao no bloco. 12 e o ponto em que a lista ainda cabe na tela
# do chat sem rolar muito; alem disso o topo (que e o que importa) sai de vista.
MAX_ACHADOS = 12

MARCADOR_SERVIDOR = "<SERVIDOR>"

# A persona condensada. O prompt vivo completo tem varias paginas - colado num
# chat, empurra a evidencia para fora da janela util. Fica so o que muda a
# RESPOSTA: metodo, disciplina de hipotese e a regra de acao destrutiva.
PERSONA = (
    "Voce e DBA Master (Parra), DBA senior Oracle em ambiente corporativo de "
    "grande porte (Exadata, RAC, CDB/PDB).\n"
    "Regras que valem nesta conversa:\n"
    "- Priorize por DB time e wait class (Method R / Millsap). Nao conclua por "
    "ratio isolado nem por contador absoluto.\n"
    "- Uma hipotese por vez, testada com os dados abaixo. Se os dados nao "
    "confirmam, diga isso e proponha a proxima.\n"
    "- Nao de palpite sem evidencia. Se faltar dado, diga QUAL dado falta.\n"
    "- Toda acao que muda o ambiente vem com impacto, plano de rollback e "
    "aviso de que exige aprovacao/janela."
)

PEDIDO = (
    "O QUE EU PRECISO DE VOCE:\n"
    "1. A hipotese atual se sustenta com os achados acima? Sim ou nao, e por que.\n"
    "2. Se nao se sustenta, qual e a proxima hipotese mais provavel?\n"
    "3. Qual e o PROXIMO DADO a coletar para decidir entre as duas?\n"
    "4. Se propuser acao que muda o ambiente, declare impacto e rollback."
)

_ORDEM_SEV = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
_ROTULO_SEV = {"crit": "CRITICO", "warn": "ATENCAO", "info": "INFO", "ok": "OK"}


def _analise(caso: str, out_dir: str = "") -> dict:
    p = os.path.join(out_dir or OUTPUT_DIR, caso, f"{caso}_findings.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _graves(registros: list) -> list:
    """Achados que merecem a janela do chat, do pior para o melhor.

    INFO/OK ficam de fora: sao contexto para o relatorio, nao material de
    diagnostico, e ocupam o lugar do que importa.
    """
    graves = [r for r in registros
              if (r.get("severity") or "").lower() in ("crit", "warn")]
    graves.sort(key=lambda r: _ORDEM_SEV.get((r.get("severity") or "").lower(), 9))
    return graves[:MAX_ACHADOS]


def montar(caso: str, anonimizar: bool = False, cases_dir: str = "",
           out_dir: str = "") -> str:
    """O bloco de texto pronto para colar no chat do Copilot.

    Funciona com o que houver: caso so registrado (sem analise) rende o bloco do
    ciclo de vida; caso analisado rende tambem os achados. O unico caso em que
    nao ha nada a colar e o caso que nao existe.
    """
    r = caso_lifecycle.resumo(caso, cases_dir)
    dados = _analise(caso, out_dir)
    registros = dados.get("findings") or []
    if not r.get("criado_em") and not registros and not r.get("evento"):
        raise ValueError(f"caso '{caso}' nao tem nada registrado nem analisado")

    host = r.get("hostname") or r.get("conn") or ""
    if anonimizar:
        host = MARCADOR_SERVIDOR

    linhas = [PERSONA, "", "=" * 60, f"CASO: {caso}"]
    if host:
        linhas.append(f"Servidor: {host}")
    if r.get("status") and r["status"] != "-":
        linhas.append(f"Status: {r['status']}")
    if dados.get("score") is not None:
        linhas.append(f"Health Score: {dados['score']}")
    if r.get("evento"):
        linhas.append(f"Evento: {r['evento']}")
    if r.get("hipotese_atual"):
        linhas.append(f"Hipotese atual: {r['hipotese_atual']}")
    elif r.get("hipotese_inicial"):
        linhas.append(f"Hipotese inicial: {r['hipotese_inicial']}")

    # Hipotese descartada e informacao de primeira: sem ela o LLM propoe de
    # volta o caminho que o DBA ja andou e descartou com dado na mao.
    d = caso_lifecycle.ler(caso, cases_dir)
    descartadas = [h.get("texto", "") for h in (d.get("hipoteses") or [])]
    if descartadas:
        linhas.append("Hipoteses JA DESCARTADAS (nao proponha de novo):")
        for h in descartadas:
            linhas.append(f"  - {h}")

    graves = _graves(registros)
    if graves:
        linhas += ["", f"ACHADOS ({len(graves)} de {len(registros)}, "
                       "do mais grave para o menos):"]
        for i, a in enumerate(graves, 1):
            sev = _ROTULO_SEV.get((a.get("severity") or "").lower(), "?")
            imp = f" [{a['impact']}]" if a.get("impact") else ""
            linhas.append(f"{i}. ({sev}) {a.get('section', '')}: "
                          f"{a.get('title', '')}{imp}")
            if a.get("recommendation"):
                linhas.append(f"   recomendacao da ferramenta: {a['recommendation']}")
    else:
        linhas += ["", "ACHADOS: o caso ainda nao foi analisado (sem "
                       "relatorio gerado)."]

    # O que foi VERIFICADO agora, no passo a passo. Vale mais que o achado da
    # coleta e por isso vem depois: a coleta e a foto de quando o caso abriu,
    # isto e o estado do banco ha minutos. Sem este bloco o LLM manda conferir
    # justamente o que o DBA acabou de conferir - e a verificacao que voltou
    # VAZIA e a que mais economiza rodada, porque ja fecha um caminho.
    verificado = []
    for s in ((d.get("guia") or {}).get("saidas") or []):
        for a in s.get("achados") or []:
            t = a.get("title", "")
            if t and t not in verificado:
                verificado.append(t)
    if verificado:
        linhas += ["", "JA VERIFICADO NO PASSO A PASSO (saida real do banco, "
                       "mais recente que os achados acima):"]
        for t in verificado[-MAX_ACHADOS:]:
            linhas.append(f"  - {t}")

    acoes = d.get("acoes") or []
    if acoes:
        linhas += ["", "ACOES JA EXECUTADAS:"]
        for a in acoes[-5:]:
            linhas.append(f"  - ({a.get('risco', 'nenhum')}) {a.get('descricao', '')}")

    linhas += ["=" * 60, "", PEDIDO]
    if anonimizar:
        linhas += ["", "(nomes de servidor omitidos de proposito)"]
    return "\n".join(linhas) + "\n"
