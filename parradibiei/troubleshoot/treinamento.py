"""
Treinamento: transforma o que o time ja sabe em material para quem ainda nao sabe.

Por que existe: o pedido e antigo - "guarde tudo e depois monte um treinamento
para os outros DBAs". O que faltava nao era vontade, era perceber que **o
material ja estava escrito e invisivel**. Cada playbook da `kb/` declara, por
passo, um campo `armadilha`: o jeito de dar errado que so se aprende apanhando.
Sao 270 deles. Nenhum DBA fora desta ferramenta jamais leu um.

DUAS FONTES, e elas ensinam coisas diferentes:

1. **KB (`kb/*.json`)** - o procedimento certo e, principalmente, a armadilha.
   E conhecimento destilado: vale para qualquer Oracle, ja passou pelo
   `VALIDAR_KB.py` e nao envelhece com o incidente.
2. **Casos reais (`troubleshooting/*/meta.json`)** - o raciocinio: a hipotese
   que se levantou, a que se DESCARTOU e por que. Ensina o que procedimento
   nenhum ensina, que e errar com metodo.

A fonte 2 e a mais valiosa e hoje esta quase vazia - por isso `cobertura()`
existe e e chamada na apostila: um treinamento que finge ter material que nao
tem forma DBA que decora passo e nao sabe decidir.

SANITIZACAO e opt-in (`--publico`), nao padrao. Apostila para o time de dentro
com o nome dos servidores mascarado vira charada; o mascaramento serve para
quando o material atravessa a fronteira da empresa. Forcar sempre tornaria o
default inutil, e nao forcar nunca deixaria o vazamento acontecer calado.
"""
from __future__ import annotations

import datetime as dt
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARCAS = os.path.join(BASE_DIR, "marcas_locais.txt")
OUT_DIR = os.path.join(BASE_DIR, "output", "treinamento")

# Ordem didatica POR PLATAFORMA: o aluno precisa saber ONDE o tempo esta indo
# antes de aprender a mexer em qualquer coisa. Playbooks fora destas listas
# entram depois, na ordem do proprio catalogo - filtrar pela trilha esconderia
# playbook novo sem avisar ninguem.
TRILHAS = {
    "oracle": [
        ("Fundamentos: onde o tempo esta indo",
         ["checklist_diario", "sessoes_locks", "sql_lento", "recursos_so"]),
        ("Espaco e recuperabilidade (o que para o banco)",
         ["archive_cheio", "espaco_tablespace", "undo_ora1555", "backup_rpo",
          "redo_log_switch"]),
        ("Disponibilidade e cluster",
         ["instancia_travada", "conexao_listener", "clusterware_gi",
          "rac_global_cache", "dataguard_gap"]),
        ("Integridade e erro interno",
         ["erro_interno", "corrupcao_bloco", "objetos_invalidos"]),
    ],
    "mssql": [
        ("Fundamentos: concorrencia e espera", ["mssql_bloqueio"]),
    ],
    "postgres": [
        ("Fundamentos: concorrencia, MVCC e vacuum", ["postgres_bloqueio"]),
    ],
    "db2": [
        ("Fundamentos: concorrencia e escalation", ["db2_bloqueio"]),
    ],
    "mysql": [
        ("Fundamentos: concorrencia InnoDB e metadados", ["mysql_bloqueio"]),
    ],
    "redis": [
        ("Fundamentos: latencia, memoria e evicao", ["redis_latencia"]),
    ],
}


def _agora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ── sanitizacao ───────────────────────────────────────────────
def marcas(caminho: str = "") -> list:
    """Termos que nao podem sair daqui (`marcas_locais.txt`).

    Ausencia do arquivo devolve lista vazia DE PROPOSITO: quem clonou o projeto
    nao tem marca a proteger, e abortar deixaria a apostila refem de um arquivo
    local.
    """
    try:
        with open(caminho or MARCAS, encoding="utf-8") as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def sanitizar(texto: str, termos: list) -> str:
    """Mascara os termos proibidos preservando a legibilidade do resto.

    Termos MAIORES primeiro. Quando um termo e SUFIXO de outro - o dominio
    completo e o pedaco final dele, por exemplo -, mascarar o curto antes
    deixa o comeco do longo intacto e o resultado ainda entrega o que deveria
    esconder. O caso e real e esta na lista de marcas deste projeto; o exemplo
    concreto fica de fora daqui de proposito, porque o nome nao pode entrar no
    repositorio nem dentro da funcao que existe para remove-lo.
    """
    out = texto or ""
    for t in sorted({t for t in termos if t}, key=len, reverse=True):
        out = re.sub(re.escape(t), "<omitido>", out, flags=re.I)
    return out


# ── licoes a partir da KB ─────────────────────────────────────
def licoes(kb_dir: str = "", plataforma: str = "") -> list:
    """Uma licao por playbook, na ordem da trilha didatica da plataforma."""
    from . import kb as mod_kb

    pbs = {pb.get("id"): pb
           for pb in mod_kb.por_plataforma(plataforma, kb_dir)}
    if plataforma:
        trilha = TRILHAS.get(plataforma, [])
    else:
        trilha = [t for ts in TRILHAS.values() for t in ts]

    vistos, ordenado = set(), []
    for _tema, ids in trilha:
        for pid in ids:
            if pid in pbs and pid not in vistos:
                vistos.add(pid)
                ordenado.append(pbs[pid])
    # Playbook fora da trilha entra depois, NUNCA e descartado: filtrar pela
    # trilha faria playbook novo sumir da apostila sem erro nenhum.
    ordenado += [pb for pid, pb in pbs.items() if pid not in vistos]

    out = []
    for pb in ordenado:
        passos = pb.get("passos") or []
        out.append({
            "id": pb.get("id", ""),
            "plataforma": mod_kb.plataforma_de(pb),
            "verificado": mod_kb.verificacao_de(pb),
            "verificado_obs": (pb.get("verificado") or {}).get("observacao", "")
            if isinstance(pb.get("verificado"), dict) else "",
            "verificado_ressalva": (pb.get("verificado") or {}).get(
                "nao_verificados", 0)
            if isinstance(pb.get("verificado"), dict) else 0,
            "titulo": pb.get("titulo", ""),
            "quando_usar": pb.get("quando_usar", ""),
            "nota": pb.get("nota", ""),
            "sintomas": pb.get("sintomas") or [],
            "erros_ora": pb.get("erros_ora") or [],
            "passos": passos,
            "armadilhas": [(p.get("titulo", ""), p.get("armadilha", ""))
                           for p in passos if (p.get("armadilha") or "").strip()],
            "acoes_de_risco": [p for p in passos
                               if p.get("risco") in ("mudanca", "destrutivo")],
        })
    return out


def tema_de(pid: str, plataforma: str = "") -> str:
    trilhas = ([TRILHAS.get(plataforma, [])] if plataforma
               else list(TRILHAS.values()))
    for trilha in trilhas:
        for tema, ids in trilha:
            if pid in ids:
                return tema
    return "Outros temas"


# ── o que os casos reais ensinam (e o que se perdeu) ──────────
def cobertura(cases_dir: str = "") -> dict:
    """Quanto do raciocinio dos casos foi REGISTRADO.

    E a metrica honesta do treinamento: procedimento se copia da KB, mas
    "por que a hipotese obvia estava errada" so existe se alguem escreveu no
    momento do incidente. O que nao foi registrado nao e recuperavel depois -
    a evidencia fica, o raciocinio nao.
    """
    from . import caso as cl

    nomes = cl.listar_casos(cases_dir)
    com_hipotese, com_acao, com_causa, descartadas, ricos = 0, 0, 0, 0, []
    for n in nomes:
        d = cl.ler(n, cases_dir) or {}
        hips = d.get("hipoteses") or []
        acoes = d.get("acoes") or []
        causa = (d.get("causa_raiz") or "").strip()
        if d.get("hipotese_atual"):
            com_hipotese += 1
        if acoes:
            com_acao += 1
        if causa:
            com_causa += 1
        descartadas += len(hips)
        if hips or causa:
            ricos.append({
                "caso": n,
                "evento": d.get("evento", ""),
                "hipotese_atual": d.get("hipotese_atual", ""),
                "descartadas": hips,
                "causa_raiz": causa,
                "solucao": (d.get("solucao") or "").strip(),
                "prevencao": (d.get("prevencao") or "").strip(),
                "acoes": acoes,
            })
    total = len(nomes)
    return {
        "total": total,
        "com_hipotese": com_hipotese,
        "com_acao": com_acao,
        "com_causa_raiz": com_causa,
        "hipoteses_descartadas": descartadas,
        "sem_raciocinio": total - max(com_hipotese, com_acao, com_causa),
        "casos_ricos": ricos,
    }


# ── montagem do material ──────────────────────────────────────
def _bloco_licao(lic: dict, nivel: int = 3) -> list:
    h = "#" * nivel
    L = [f"{h} {lic['titulo']}", ""]
    if lic["quando_usar"]:
        L += [f"**Quando usar.** {lic['quando_usar']}", ""]
    if lic["sintomas"]:
        L += ["**Como o problema chega até você:** "
              + ", ".join(f"`{s}`" for s in lic["sintomas"][:8]), ""]
    if lic["erros_ora"]:
        L += ["**Códigos associados:** "
              + ", ".join(f"`{c}`" for c in lic["erros_ora"]), ""]
    if lic["nota"]:
        L += [f"> {lic['nota']}", ""]
    # O aviso vem ANTES do procedimento, nao num rodape: quem for executar
    # precisa saber se a consulta ja rodou contra um banco de verdade ou se e
    # texto bem fundamentado que nunca foi provado.
    if lic.get("verificado") != "homologacao":
        aviso = lic.get("verificado_obs") or (
            "Contrato offline conferido (risco, ordem read-only, o que olhar). "
            "O SQL NAO foi executado contra banco nenhum.")
        L += [f"⚠ **Não verificado contra banco real.** {aviso}", ""]
    elif lic.get("verificado_ressalva"):
        # Verificado NAO e sinonimo de integralmente verificado. Esconder a
        # ressalva aqui seria o mesmo exagero que o aviso acima combate.
        L += [f"◆ Verificado contra banco real, **com ressalva**: "
              f"{lic['verificado_ressalva']} consulta(s) deste tema não podem "
              "ser executadas pelo validador (atravessam database link) e "
              "seguem sem prova.", ""]

    L += [f"{h}# O caminho, na ordem", ""]
    for i, p in enumerate(lic["passos"], 1):
        risco = p.get("risco", "leitura")
        selo = {"leitura": "leitura",
                "mudanca": "**MUDA O AMBIENTE**",
                "destrutivo": "**DESTRUTIVO**"}.get(risco, risco)
        L.append(f"{i}. **{p.get('titulo','')}** — {selo}")
        if p.get("objetivo"):
            L.append(f"   - *Para quê:* {p['objetivo']}")
        if p.get("olhar"):
            L.append(f"   - *O que olhar:* {p['olhar']}")
        if p.get("antes_de_executar"):
            L.append(f"   - *Antes de executar:* {p['antes_de_executar']}")
    L.append("")

    if lic["armadilhas"]:
        L += [f"{h}# Armadilhas (o que só se aprende apanhando)", ""]
        for titulo, arm in lic["armadilhas"]:
            L.append(f"- **{titulo}** — {arm}")
        L.append("")
    return L


def _frase_de_origem(lics: list) -> str:
    """Diz de onde o material veio, conforme o que ele REALMENTE e.

    A frase era fixa - "cada passo ja foi executado contra bancos reais" - e
    virou mentira no dia em que a KB passou a receber plataformas que esta
    maquina nao consegue executar. Cabecalho de apostila e o lugar de onde o
    leitor tira confianca; deixa-lo desatualizado e pior do que nao ter frase.
    """
    verificados = sum(1 for l in lics if l.get("verificado") == "homologacao")
    total = len(lics) or 1
    if verificados == total:
        return ("Este material **não é teoria**: cada passo já foi executado "
                "contra bancos reais e cada armadilha corresponde a um erro "
                "que alguém cometeu.")
    if verificados == 0:
        return ("**Nenhum tema deste material foi executado contra banco "
                "real ainda.** Os procedimentos vêm da documentação oficial e "
                "das fontes citadas em cada tema, e as armadilhas são o que "
                "se aprende apanhando — mas o SQL não foi provado. Rode em "
                "homologação antes de levar para produção.")
    return (f"**{verificados} de {total} temas** foram executados contra "
            "banco real; os demais vêm da documentação oficial e ainda não "
            "foram provados — cada um traz o aviso no início.")


def apostila(kb_dir: str = "", cases_dir: str = "", publico: bool = False,
             tema: str = "", plataforma: str = "") -> str:
    """Material completo, em Markdown. `plataforma` gera a apostila daquela
    tecnologia; vazio junta todas, separadas por secao."""
    from . import kb as mod_kb

    lics = licoes(kb_dir, plataforma)
    if tema:
        alvo = tema.strip().lower()
        lics = [l for l in lics
                if alvo in l["id"].lower() or alvo in l["titulo"].lower()]
    cob = cobertura(cases_dir)

    n_passos = sum(len(l["passos"]) for l in lics)
    n_arm = sum(len(l["armadilhas"]) for l in lics)
    nome_plat = mod_kb.PLATAFORMAS.get(plataforma, "") if plataforma else ""

    L = [
        f"# Treinamento DBA — troubleshooting {nome_plat or 'de banco de dados'} na prática",
        "",
        f"Gerado em {_agora()} a partir da base de conhecimento do time "
        f"({len(lics)} temas, {n_passos} passos, {n_arm} armadilhas "
        "documentadas).",
        "",
        _frase_de_origem(lics),
        "",
        "A ordem é sempre a mesma e é a lição mais importante do curso: "
        "**observação read-only antes de qualquer ação.**",
        "",
        "---",
        "",
        "## Como ler cada tema",
        "",
        "| Marca | Significa |",
        "|---|---|",
        "| leitura | Read-only. Pode rodar em produção, a qualquer hora. |",
        "| **MUDA O AMBIENTE** | Reversível, mas exige janela e aviso. |",
        "| **DESTRUTIVO** | Pode causar perda de dado ou indisponibilidade. "
        "Só com aprovação explícita e rollback planejado. |",
        "",
        "---",
        "",
    ]

    atual = None
    for lic in lics:
        plat = lic.get("plataforma", "oracle")
        t = tema_de(lic["id"], plat)
        # Sem filtro de plataforma a apostila junta tecnologias: o cabecalho
        # PRECISA dizer qual e, senao o aluno le um passo de T-SQL achando que
        # roda no Oracle - que e o mesmo risco que fez a busca ganhar filtro.
        rotulo = t if plataforma else f"{mod_kb.PLATAFORMAS.get(plat, plat)} — {t}"
        if rotulo != atual:
            atual = rotulo
            L += [f"## {rotulo}", ""]
        L += _bloco_licao(lic)
        L += ["---", ""]

    L += _bloco_casos(cob)

    texto = "\n".join(L)
    return sanitizar(texto, marcas()) if publico else texto


def _bloco_casos(cob: dict) -> list:
    L = ["## O que os nossos próprios casos ensinam", ""]
    if not cob["casos_ricos"]:
        L += [
            "**Nenhum caso com raciocínio registrado ainda.**",
            "",
            f"Existem {cob['total']} casos coletados, mas "
            f"{cob['sem_raciocinio']} deles não têm hipótese, ação nem causa "
            "raiz gravada. A evidência ficou; o raciocínio, não — e é ele que "
            "ensina. Procedimento se copia da KB acima; *por que a hipótese "
            "óbvia estava errada* só existe se alguém escreveu no momento do "
            "incidente.",
            "",
            "Para esta seção deixar de ficar vazia, durante o caso:",
            "",
            "```",
            'CASO.py <caso> --hipotese "..."',
            'CASO.py <caso> --hipotese "nova" --motivo "por que a anterior caiu"',
            'CASO.py <caso> --acao "..." --risco baixo|medio|alto',
            'CASO.py <caso> --resolver --causa "..." --solucao "..." --prevencao "..."',
            "```",
            "",
        ]
        return L

    L += [f"{len(cob['casos_ricos'])} caso(s) com raciocínio registrado, de "
          f"{cob['total']} coletados. "
          f"{cob['hipoteses_descartadas']} hipótese(s) descartada(s) — "
          "o material mais valioso aqui.", ""]
    for c in cob["casos_ricos"]:
        L += [f"### {c['caso']}", ""]
        if c["evento"]:
            L += [f"**Sintoma:** {c['evento']}", ""]
        if c["descartadas"]:
            L += ["**Hipóteses que NÃO se confirmaram** (e por quê):", ""]
            for h in c["descartadas"]:
                L.append(f"- ~~{h.get('texto','')}~~ — {h.get('motivo','') or 'sem motivo registrado'}")
            L.append("")
        if c["hipotese_atual"]:
            L += [f"**Hipótese que se sustentou:** {c['hipotese_atual']}", ""]
        if c["causa_raiz"]:
            L += [f"**Causa raiz:** {c['causa_raiz']}", ""]
        if c["solucao"]:
            L += [f"**Solução:** {c['solucao']}", ""]
        if c["prevencao"]:
            L += [f"**Prevenção:** {c['prevencao']}", ""]
        altas = [a for a in c["acoes"] if a.get("risco") == "alto"]
        if altas:
            L += ["**Ações de risco alto executadas:** "
                  + "; ".join(a.get("descricao", "") for a in altas), ""]
        L += ["---", ""]
    return L


def quiz(kb_dir: str = "", publico: bool = False, limite: int = 25,
         plataforma: str = "") -> str:
    """Perguntas a partir das armadilhas.

    Armadilha e a melhor pergunta que existe: ela descreve uma situacao em que
    a resposta intuitiva esta errada. Decorar comando nao separa quem sabe de
    quem nao sabe; saber por que o comando obvio falha, separa.
    """
    L = ["# Quiz — armadilhas de troubleshooting Oracle", "",
         "Responda antes de abrir a resposta. Cada item veio de um erro real.",
         ""]
    n = 0
    for lic in licoes(kb_dir, plataforma):
        for titulo, arm in lic["armadilhas"]:
            if n >= limite:
                break
            n += 1
            L += [f"**{n}. [{lic.get('plataforma','oracle')}] "
                  f"Tema: {lic['titulo']}**", "",
                  f"No passo *{titulo}*, qual é a armadilha?", "",
                  "<details><summary>Resposta</summary>", "",
                  arm, "", "</details>", ""]
        if n >= limite:
            break
    texto = "\n".join(L)
    return sanitizar(texto, marcas()) if publico else texto


def gravar(conteudo: str, nome: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    destino = os.path.join(OUT_DIR, nome)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(conteudo)
    return destino
