"""
perfil.py - o ambiente do banco: producao, homologacao, desenvolvimento.

POR QUE EXISTE. O framework tratava todo banco como se fosse producao, e isso
custava caro em credibilidade. Medido na bateria de 13/09, sobre 23 servidores
de homologacao: dos 76 achados CRITICOS, 41 (54%) eram configuracao ESPERADA
num ambiente que nao e producao:

    14  log_mode = NOARCHIVELOG
    16  conta com a senha vencida / expirada
    11  backup antigo ou datafile nunca copiado pelo RMAN

Em producao, `NOARCHIVELOG` e catastrofe - nao existe recuperacao a um ponto no
tempo. Em homologacao e decisao de custo, tomada de proposito. O framework
gritava CRITICO em 14 de 23 servidores por uma escolha deliberada.

O custo nao e o ruido em si: e que um CRITICO que nao e critico queima a
credibilidade de TODO critico. O APPHO114 tem contencao real de `enq: TX` numa
tabela nomeada e dois deadlocks registrados - e esse achado estava na mesma
lista de 76, com a mesma cor, do NOARCHIVELOG esperado.

DE ONDE VEM O PERFIL, E POR QUE NAO DO NOME DA PASTA. O repositorio de conexoes
tem um campo `folder` com valores como `PRO_EMPRESA`, `HK_EMPRESA`,
`EXADATA_CL`, `Homologacao (bateria)`. E tentador derivar dali - e seria errado.
O nome e convencao humana, nao declaracao; `HK` PARECE homologacao mas ninguem
garante. E o erro tem direcao: tratar PRODUCAO como homologacao silencia
NOARCHIVELOG e backup ausente no banco onde isso derruba a empresa.

Por isso a fonte e a DECLARACAO EXPLICITA que ja existe: `servidores.txt`, que
o DBA mantem e cujo cabecalho diz ser a lista da bateria de homologacao. O
`melhoria.servidores_homolog()` ja a lia para outro fim.

Quem nao esta declarado e PRODUCAO. Sempre. O padrao e o lado seguro do erro:
um alarme a mais num banco de teste custa atencao; um alarme a menos em
producao custa o banco.
"""
from __future__ import annotations

import os

PRODUCAO = "producao"
HOMOLOGACAO = "homologacao"
DESENVOLVIMENTO = "desenvolvimento"

PERFIS = (PRODUCAO, HOMOLOGACAO, DESENVOLVIMENTO)

# Perfis em que NAO se cobra recuperabilidade, retencao de backup e politica de
# senha com o mesmo rigor de producao.
_NAO_PRODUTIVOS = frozenset({HOMOLOGACAO, DESENVOLVIMENTO})

ROTULO = {
    PRODUCAO: "producao",
    HOMOLOGACAO: "homologacao",
    DESENVOLVIMENTO: "desenvolvimento",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _declarados_homolog(caminho: str = "") -> set:
    """Servidores declarados em `servidores.txt` (a lista da bateria).

    Reusa a MESMA leitura do `melhoria.servidores_homolog` de proposito: duas
    listas de "quem e homologacao" acabariam discordando, e a que discordasse
    em silencio seria a que decide se um alarme de backup aparece.
    """
    from . import melhoria
    return melhoria.servidores_homolog(caminho)


def do_servidor(conn: str, caminho_lista: str = "") -> str:
    """Perfil do servidor `conn`. Desconhecido => PRODUCAO (lado seguro)."""
    nome = (conn or "").strip().upper()
    if not nome:
        return PRODUCAO
    return HOMOLOGACAO if nome in _declarados_homolog(caminho_lista) else PRODUCAO


def do_caso(meta: dict | None, caminho_lista: str = "") -> str:
    """Perfil de um caso ja analisado.

    Le `perfil` do `meta.json` quando a COLETA o gravou - e a forma correta,
    porque congela no momento da coleta o que foi assumido, e a analise de um
    caso antigo nao muda de conclusao porque alguem editou `servidores.txt`
    depois. Sem isso, cai na derivacao pelo `conn`.
    """
    meta = meta or {}
    declarado = str(meta.get("perfil") or "").strip().lower()
    if declarado in PERFIS:
        return declarado
    return do_servidor(meta.get("conn", ""), caminho_lista)


def e_producao(p: str) -> bool:
    return p not in _NAO_PRODUTIVOS


def aplica(exigido, perfil: str) -> bool:
    """A regra vale neste perfil?

    `exigido` e o campo `perfis` da regra: ausente significa "vale em todos" -
    o padrao tem de ser cobrar, senao esquecer o campo silencia a regra.
    """
    if not exigido:
        return True
    if isinstance(exigido, str):
        exigido = [exigido]
    return perfil in {str(x).strip().lower() for x in exigido}


# ── o que deixa de ser critico fora de producao ───────────────
#
# Declarado como DADO, num lugar so, e nao espalhado pelos analisadores. Foram
# tres familias diferentes a produzir os 41 criticos medidos (uma regra do
# catalogo, o analisador de contas e o de RMAN); editar os tres significaria
# tres lugares para discordarem no futuro.
#
# O casamento e por (secao, marca no titulo) porque e o que sobrevive a
# mudanca de texto: a secao vem do analisador e a marca e o termo tecnico.
#
# O que NAO entra aqui, de proposito: nada que seja sintoma. Deadlock,
# contencao de lock, tablespace cheia, SQL sem bind e espera dominante
# continuam iguais em qualquer perfil - um banco de teste parado por lock
# tambem esta parado, e quem vai depurar precisa do alarme.
RELAXAVEIS = (
    # (rotulo da familia, marcas na secao, marcas no titulo)
    ("recuperabilidade",
     ("idade do backup", "rpo", "backup", "rman", "archive destinos",
      "conformidade com as praticas oracle"),
     ("noarchivelog", "nunca copiado", "backup mais antigo",
      "force logging", "flashback", "retencao",
      # Redo/controlfile com um membro so e a MESMA decisao de custo que o
      # archivelog desligado: perder o grupo custa o banco em producao e custa
      # uma reinstalacao em teste. Entrou depois de medir - sobrou como
      # critico em 7 dos 23 servidores na primeira passada.
      "membros por grupo", "multiplex")),
    ("politica de senha",
     ("contas expirando", "contas ", "seguranca"),
     ("senha ja vencida", "senha expirada", "senha vencida",
      "sem limite de tentativas", "senha sem expiracao",
      "profiles_sem_limite", "profiles_senha")),
)


def _familia_relaxavel(secao: str, titulo: str) -> str:
    s, t = (secao or "").lower(), (titulo or "").lower()
    for rotulo, marcas_sec, marcas_tit in RELAXAVEIS:
        if any(m in s for m in marcas_sec) and any(m in t for m in marcas_tit):
            return rotulo
    return ""


def ajustar_severidade(sev: str, secao: str, titulo: str, perfil: str) -> tuple:
    """(severidade nova, motivo). Fora de producao, rebaixa o que e esperado.

    Rebaixa para `warn`, nao para `info`: o desvio CONTINUA sendo desvio e
    continua no relatorio. O que muda e a promessa que a cor faz - critico quer
    dizer "pare o que esta fazendo", e isso nao vale para um banco de teste sem
    archivelog por decisao de custo.
    """
    if e_producao(perfil) or sev != "crit":
        return sev, ""
    familia = _familia_relaxavel(secao, titulo)
    if not familia:
        return sev, ""
    return "warn", (f"{familia}: esperado em {ROTULO.get(perfil, perfil)} - "
                    f"em producao este desvio seria CRITICO")


def explicar(perfil: str) -> str:
    """Uma linha para o relatorio: o que foi assumido e de onde veio."""
    if perfil == PRODUCAO:
        return ("perfil producao (padrao): recuperabilidade, retencao de backup "
                "e politica de senha sao cobradas integralmente")
    return (f"perfil {ROTULO.get(perfil, perfil)} (declarado em servidores.txt): "
            "desvio de recuperabilidade e de politica de senha sai como "
            "informativo, nao como critico")
