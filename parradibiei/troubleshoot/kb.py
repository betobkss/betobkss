"""
Base de conhecimento (KB): playbooks de troubleshooting passo a passo.

Por que existe: o framework diz O QUE esta errado (achado) e O QUE fazer
primeiro (plano). Faltava o COMO - o passo a passo que o DBA experiente tem na
cabeca ou num .sql perdido numa pasta. A KB guarda esse caminho de forma
executavel e revisavel.

Decisao de desenho - CONHECIMENTO COMO DADO: cada playbook e um JSON em `kb/`.
Acrescentar procedimento nao exige tocar em codigo, e o material pode ser
revisado por outro DBA lendo JSON, nao Python. Mesma escolha de
`mcp/best_practices.json` e `mcp/erros_ora.json`.

O QUE TORNA ISTO DIFERENTE DE UM ARQUIVO DE SCRIPTS:

1. **Todo passo declara o risco.** Um `SELECT` e um `rm -rf` num diskgroup de
   archive nao podem aparecer com o mesmo peso na tela. Ver `RISCOS`.
2. **A ordem e read-only primeiro.** O passo que muda o ambiente so vem depois
   de o diagnostico estar fechado, e carrega o que validar antes.
3. **Cada passo diz o que olhar** no resultado - senao o comando vira ritual.
4. **Armadilha explicita** quando o procedimento tem um jeito de dar errado que
   so se aprende apanhando.

Ligacao com o resto: `sugerir_para_secao()` liga um achado do relatorio ao
playbook correspondente, para o DBA sair do "esta ruim" para o "faca isto".
"""
from __future__ import annotations

import glob
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(BASE_DIR, "kb")

# Grau de risco de EXECUTAR o passo. A tela e o relatorio usam isto para
# separar visualmente o que e seguro do que exige janela e aprovacao.
RISCOS = {
    "leitura": 0,      # read-only: nao muda nada, pode rodar em producao
    "mudanca": 1,      # muda configuracao/estado, reversivel
    "destrutivo": 2,   # pode causar perda de dado ou indisponibilidade
}

# Plataformas que a KB aceita. A persona DBA Master conduz todas; o framework
# de COLETA e so de Oracle - e a distincao e proposital, nao uma pendencia:
# playbook de MSSQL vale como procedimento revisado mesmo sem coletor proprio.
#
# 'oracle' e o PADRAO de quem nao declara. Os 31 playbooks escritos antes desta
# divisao nao precisaram ser tocados, e um playbook sem o campo continua
# valendo - o que evita que a introducao de plataforma vire uma migracao.
PLATAFORMAS = {
    "oracle": "Oracle Database",
    "mssql": "SQL Server",
    "db2": "IBM Db2",
    "postgres": "PostgreSQL",
    "mysql": "MySQL / MariaDB",
    "redis": "Redis",
    "mongodb": "MongoDB",
}
PLATAFORMA_PADRAO = "oracle"

# QUANTO deste playbook foi realmente conferido. Nasceu ao abrir a KB para
# plataformas que esta maquina nao tem como executar: sem Docker, sem psql,
# sem redis-cli e sem sqlcmd, o SQL de um playbook de PostgreSQL e texto bem
# fundamentado - nao e consulta testada. Tratar os dois como a mesma coisa
# repetiria em escala o que ja aconteceu uma vez aqui: na primeira rodada do
# VALIDAR_KB, 8 de 110 consultas ORACLE tinham coluna inexistente ou erro de
# sintaxe, escritas com cuidado por quem conhecia o assunto.
#
# O nivel NAO e declarado a mao para 'homologacao': quem carimba e o
# `VALIDAR_KB.py --conn <banco> --marcar`, depois de a consulta ter rodado de
# verdade. Assim o campo nao vira promessa.
VERIFICACAO = {
    "estrutura": "contrato offline apenas - SQL nunca executado",
    "homologacao": "SQL de leitura executado contra banco real de homologacao",
    "producao": "usado em caso real de producao",
}
VERIFICACAO_PADRAO = "estrutura"

_CACHE: list | None = None
_CACHE_ASSINATURA: tuple | None = None


def _assinatura(kb_dir: str) -> tuple:
    """Nome + mtime + tamanho de cada playbook.

    Serve para o cache se invalidar sozinho quando alguem edita ou acrescenta um
    JSON: sem isto, o painel em execucao continuaria mostrando a KB antiga e o
    autor acharia que o playbook novo nao funcionou.
    """
    try:
        return tuple(sorted(
            (e.name, e.stat().st_mtime_ns, e.stat().st_size)
            for e in os.scandir(kb_dir) if e.name.endswith(".json")))
    except OSError:
        return ()


def _norm(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "").strip().lower())


def plataforma_de(pb: dict) -> str:
    """Plataforma do playbook. Ausente = Oracle, por compatibilidade."""
    return str(pb.get("plataforma") or PLATAFORMA_PADRAO).strip().lower()


def verificacao_de(pb: dict) -> str:
    """Nivel de verificacao. Ausente = o menor, nunca o maior: silencio tem de
    significar 'nao conferido', senao o campo protege exatamente ninguem."""
    v = pb.get("verificado")
    if isinstance(v, dict):
        v = v.get("nivel")
    return str(v or VERIFICACAO_PADRAO).strip().lower()


def _validar(pb: dict, origem: str) -> list:
    """Erros estruturais do playbook. KB errada e pior que KB ausente: manda o
    DBA executar um passo perigoso sem o aviso."""
    erros = []
    for campo in ("id", "titulo", "quando_usar", "passos"):
        if not pb.get(campo):
            erros.append(f"{origem}: falta '{campo}'")
    # Plataforma escrita errada e pior que ausente: 'sqlserver' em vez de
    # 'mssql' faz o playbook sumir do filtro e da apostila daquela tecnologia
    # sem erro nenhum - fica invisivel achando que foi publicado.
    plat = plataforma_de(pb)
    if plat not in PLATAFORMAS:
        erros.append(f"{origem}: plataforma desconhecida: "
                     f"{pb.get('plataforma')!r} (use uma de "
                     f"{', '.join(sorted(PLATAFORMAS))})")
    # `erros_ora` passa por normalizar(), que transforma numero solto em
    # ORA-xxxxx. Um playbook de SQL Server declarando o erro 1205 (deadlock)
    # viraria ORA-01205 e passaria a ser oferecido a quem pesquisa esse ORA-
    # num banco Oracle. Codigo de erro de outra plataforma vai em 'tags'.
    if verificacao_de(pb) not in VERIFICACAO:
        erros.append(f"{origem}: nivel de verificacao desconhecido: "
                     f"{pb.get('verificado')!r} (use um de "
                     f"{', '.join(VERIFICACAO)})")
    if plat != PLATAFORMA_PADRAO and pb.get("erros_ora"):
        erros.append(f"{origem}: plataforma '{plat}' nao pode usar "
                     "'erros_ora' (o campo e normalizado como ORA-xxxxx e "
                     "poluiria a busca do Oracle) - use 'tags'")
    for i, p in enumerate(pb.get("passos") or [], 1):
        if not p.get("titulo"):
            erros.append(f"{origem}: passo {i} sem titulo")
        risco = p.get("risco")
        if risco not in RISCOS:
            erros.append(f"{origem}: passo {i} com risco invalido: {risco!r}")
        if risco in ("mudanca", "destrutivo") and not p.get("antes_de_executar"):
            erros.append(f"{origem}: passo {i} e '{risco}' e nao diz o que "
                         "validar antes de executar")
    return erros


def carregar(kb_dir: str = "") -> list:
    """Todos os playbooks de `kb/`. Ausencia da pasta nao derruba o framework:
    a KB e apoio, nao dependencia do diagnostico."""
    global _CACHE, _CACHE_ASSINATURA
    dir_efetivo = kb_dir or KB_DIR
    if not kb_dir:
        assinatura = _assinatura(dir_efetivo)
        if _CACHE is not None and assinatura == _CACHE_ASSINATURA:
            return _CACHE
        _CACHE_ASSINATURA = assinatura
    out = []
    for path in sorted(glob.glob(os.path.join(dir_efetivo, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                pb = json.load(fh)
        except Exception:
            continue
        if not isinstance(pb, dict):
            continue
        pb["_arquivo"] = os.path.basename(path)
        out.append(pb)
    out.sort(key=lambda p: p.get("ordem", 99))
    if not kb_dir:
        _CACHE = out
    return out


def validar_tudo(kb_dir: str = "") -> list:
    erros = []
    for pb in carregar(kb_dir):
        erros += _validar(pb, pb.get("_arquivo", pb.get("id", "?")))
    return erros


def por_id(pid: str, kb_dir: str = "") -> dict:
    for pb in carregar(kb_dir):
        if pb.get("id") == pid:
            return pb
    return {}


def por_plataforma(plataforma: str = "", kb_dir: str = "") -> list:
    """Playbooks de uma plataforma; vazio devolve todas.

    Filtro separado de `carregar()` de proposito: o cache daquela funcao e
    indexado pela pasta, e receber um argumento que muda o RESULTADO faria a
    segunda chamada com outro filtro devolver a lista da primeira.
    """
    pbs = carregar(kb_dir)
    alvo = (plataforma or "").strip().lower()
    return pbs if not alvo else [pb for pb in pbs if plataforma_de(pb) == alvo]


def plataformas(kb_dir: str = "") -> dict:
    """{plataforma: quantidade} do que a KB cobre hoje, mais cheias primeiro."""
    cont: dict = {}
    for pb in carregar(kb_dir):
        p = plataforma_de(pb)
        cont[p] = cont.get(p, 0) + 1
    return dict(sorted(cont.items(), key=lambda x: (-x[1], x[0])))


def _texto_busca(pb: dict) -> str:
    partes = [pb.get("titulo", ""), pb.get("quando_usar", ""),
              " ".join(pb.get("sintomas", []) or []),
              " ".join(pb.get("erros_ora", []) or []),
              " ".join(pb.get("tags", []) or [])]
    for p in pb.get("passos") or []:
        partes += [p.get("titulo", ""), p.get("objetivo", ""),
                   p.get("comando", "")]
    return _norm(" ".join(partes))


def buscar(termo: str, kb_dir: str = "", plataforma: str = "") -> list:
    """Busca simples por relevancia. Titulo e sintoma pesam mais que o corpo:
    quem digita 'ORA-00257' quer o playbook de archive, nao toda mencao a
    archive espalhada nos passos.

    Codigo de erro e tratado a parte, por normalizacao e nao por substring: o
    banco escreve 'ORA-257' no alert log e 'ORA-00257' na documentacao. Quem
    copia do alert log tem de achar o mesmo playbook - com busca textual pura,
    nao acha.

    A identificacao considera TODAS as familias (ORA-, PLS-, TNS-, DPY-...) e
    tambem a MENSAGEM do driver, que e o que o DBA realmente cola. Antes disso,
    "No more data to read from socket" pontuava por palavra solta e devolvia
    datapump/sql_lento/cursores - tres playbooks errados - enquanto o
    'ORA-03113' equivalente devolvia o certo. Ver erros.carregar_mensagens().

    `plataforma` restringe o resultado. Com mais de uma tecnologia na KB isso
    deixa de ser conveniencia e vira seguranca: 'deadlock' nao existia em
    nenhum playbook Oracle (la o assunto se chama ORA-00060), entao o primeiro
    playbook de SQL Server passou a ser a UNICA resposta para quem digitasse a
    palavra durante um caso Oracle - e o proximo passo seria rodar T-SQL num
    banco Oracle. Quando o caso tem plataforma conhecida, passe-a aqui.
    """
    q = _norm(termo)
    base = por_plataforma(plataforma, kb_dir)
    if not q:
        return base
    palavras = [p for p in q.split() if p]
    achados = []
    # casamento por codigo de erro vem primeiro: e o mais especifico que existe
    from .erros import codigos_no_texto
    por_codigo = []
    for m in codigos_no_texto(termo or ""):
        for pb in por_erro_ora(m, kb_dir):
            if pb in base and pb not in por_codigo:
                por_codigo.append(pb)
    for pb in base:
        titulo = _norm(pb.get("titulo", ""))
        cabeca = _norm(" ".join([pb.get("quando_usar", "")]
                                + (pb.get("sintomas", []) or [])
                                + (pb.get("erros_ora", []) or [])
                                + (pb.get("tags", []) or [])))
        corpo = _texto_busca(pb)
        score = 0
        for w in palavras:
            if w in titulo:
                score += 10
            if w in cabeca:
                score += 5
            elif w in corpo:
                score += 1
        # Frase exata vale mais que a soma das palavras soltas. Sem isto,
        # 'commit force' (o comando do 2PC) caía no playbook de redo, que tem
        # 'commit lento' no titulo: uma palavra no titulo pesava mais que a
        # expressao inteira declarada nas tags de quem realmente trata o caso.
        if len(palavras) > 1:
            if q in titulo:
                score += 15
            elif q in cabeca:
                score += 12
            elif q in corpo:
                score += 6
        if pb in por_codigo:
            score += 20
        if score:
            achados.append((score, pb))
    achados.sort(key=lambda x: (-x[0], x[1].get("ordem", 99)))
    return [pb for _, pb in achados]


def por_erro_ora(codigo: str, kb_dir: str = "") -> list:
    """Playbooks que tratam um ORA- especifico. Usa o mesmo cuidado do
    dicionario de erros: ORA-600 e ORA-00600 sao o mesmo erro."""
    from .erros import normalizar
    alvo = normalizar(codigo)
    if not alvo:
        return []
    out = []
    for pb in carregar(kb_dir):
        if any(normalizar(c) == alvo for c in (pb.get("erros_ora") or [])):
            out.append(pb)
    return out


def _casa_marca(marca: str, texto: str) -> bool:
    """Marca casa com o titulo da secao respeitando FRONTEIRA DE PALAVRA.

    Substring solta gera sugestao errada e mina a confianca na tela: a marca
    'Temp' casava dentro de 'regressao de plano/tempo' e o painel oferecia o
    playbook de espaco para um caso de troca de plano. Verificado nos relatorios
    reais do projeto.
    """
    m = _norm(marca)
    if not m:
        return False
    return re.search(rf"(?<!\w){re.escape(m)}(?!\w)", texto) is not None


def sugerir_para_secao(titulo_secao: str, kb_dir: str = "") -> list:
    """Playbooks ligados a uma secao do relatorio (o pulo do 'esta ruim' para o
    'faca isto'). O casamento e por marcas declaradas no proprio playbook.

    SO ORACLE: as secoes vem do relatorio, que e gerado pela coleta Oracle. Um
    playbook de SQL Server que declarasse a marca 'Deadlock' apareceria como
    sugestao num relatorio Oracle e mandaria o DBA rodar T-SQL num banco
    Oracle. O filtro fecha essa porta na origem.
    """
    t = _norm(titulo_secao)
    if not t:
        return []
    out = []
    for pb in por_plataforma(PLATAFORMA_PADRAO, kb_dir):
        if any(_casa_marca(marca, t) for marca in (pb.get("secoes", []) or [])):
            out.append(pb)
    return out


def resumo(kb_dir: str = "") -> list:
    """Lista enxuta para a tela: sem o corpo dos passos."""
    out = []
    for pb in carregar(kb_dir):
        passos = pb.get("passos") or []
        out.append({
            "id": pb.get("id", ""),
            "titulo": pb.get("titulo", ""),
            "quando_usar": pb.get("quando_usar", ""),
            "tags": pb.get("tags", []) or [],
            "erros_ora": pb.get("erros_ora", []) or [],
            "passos": len(passos),
            "tem_risco": max([RISCOS.get(p.get("risco"), 0) for p in passos]
                             or [0]),
        })
    return out
