r"""
blueprint.py - gera a ESPECIFICACAO do framework em UM arquivo.

O alvo nao e um humano: e um modelo de linguagem com a tarefa "recrie este
framework". Por isso o texto e afirmativo, cita contrato e nao estilo, e explica
o PORQUE de cada regra - regra sem motivo e a primeira a ser "melhorada" por
quem reconstroi.

O que este arquivo NAO promete: identidade. O codigo recriado a partir daqui
nao sai byte a byte igual e nao passa nos 1500+ testes de primeira. O que ele
preserva e o que importa: os contratos, as invariantes e o CONHECIMENTO DE
DOMINIO (os checks SQL e os playbooks), que viajam literais porque nao podem
ser reinventados - um SQL plausivel contra producao e pior que nenhum SQL.

Todo numero aqui e RECONTADO do repositorio na hora (reusa
`apresentacao.numeros`), pela mesma razao dos decks: numero escrito na mao
envelhece calado.
"""
from __future__ import annotations

import ast
import glob
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_PADRAO = os.path.join(BASE_DIR, "output", "blueprint")
NOME_PADRAO = "PARRADIBIEI_BLUEPRINT.md"

# Dados que viajam LITERAIS. Sao a parte irreproduzivel: os checks SQL apurados
# em campo (com bugs reais ja corrigidos) e os playbooks. Descrever "tenha um
# catalogo de checks" faria o modelo escrever SQL plausivel - e plausivel, aqui,
# mente sobre producao com cara de verdade.
DADOS = ["mcp/catalog.json", "mcp/best_practices.json", "mcp/erros_ora.json"]


def _ler(rel: str) -> str:
    try:
        with open(os.path.join(BASE_DIR, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _numeros() -> dict:
    """Numeros do repositorio. Reusa o contador dos decks: uma fonte so."""
    try:
        from . import apresentacao
        return apresentacao.numeros()
    except Exception:
        return {}


def _docstring(path: str) -> str:
    """Primeira linha util da docstring do modulo, sem importar nada.

    `ast` em vez de import: o blueprint tem de poder ser gerado num ambiente
    sem oracledb/pptx instalados, e importar 53 modulos para ler uma frase
    quebraria justamente onde ele mais serve (maquina limpa).
    """
    try:
        with open(path, encoding="utf-8") as f:
            arvore = ast.parse(f.read())
    except (OSError, SyntaxError):
        return ""
    doc = ast.get_docstring(arvore) or ""
    for linha in doc.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        # os modulos comecam com "nome.py - o que faz": o nome ja esta na tabela
        return linha.split(" - ", 1)[-1].strip() if " - " in linha else linha
    return ""


def _modulos() -> list:
    """(caminho relativo, o que faz) de cada modulo, lido do proprio codigo.

    Gerado, e nao escrito na mao: a lista de modulos muda toda semana e uma
    tabela manual viraria mentira em duas sessoes.
    """
    itens = []
    for padrao in ("*.py", "troubleshoot/*.py", "troubleshoot/analyzers/*.py",
                   "mcp/*.py"):
        for p in sorted(glob.glob(os.path.join(BASE_DIR, padrao))):
            nome = os.path.basename(p)
            if nome.startswith("_") and nome != "__init__.py":
                continue
            if nome == "__init__.py":
                continue
            rel = os.path.relpath(p, BASE_DIR).replace("\\", "/")
            itens.append((rel, _docstring(p)))
    return itens


def _kb_arquivos() -> list:
    return sorted(glob.glob(os.path.join(BASE_DIR, "kb", "*.json")))


COMO_USAR = """\
## Como usar este arquivo

Voce vai reconstruir um framework de troubleshooting Oracle. Este documento e a
especificacao completa: contratos, invariantes e os dados de dominio literais.

Ordem obrigatoria de leitura: **1 (manifesto) -> 2 (principios) -> 5
(invariantes) -> 6 (ordem de reconstrucao)**. As secoes 3 e 4 sao referencia
durante a escrita; a 8 e dado para copiar sem alterar.

Tres avisos que decidem se o resultado presta:

1. **Os dados da secao 8 sao copiados, nao reescritos.** Sao {checks} consultas
   SQL apuradas contra bancos de producao reais e {playbooks} playbooks.
   Reescrever "com melhorias" e o erro mais caro possivel aqui: SQL plausivel que
   devolve numero errado sobre producao tem cara de verdade e ninguem confere.
2. **As invariantes da secao 5 nao sao preferencias de estilo.** Cada uma nasceu
   de um incidente, e o incidente esta escrito junto. Quando bater a duvida
   "isso importa?", a resposta esta na frase que comeca com "Origem:".
3. **Nao tente sair identico.** Este documento nao carrega o codigo. O alvo e um
   framework que se comporta pelas mesmas regras, nao um clone. Se quer o clone
   exato, o caminho e outro: `EMPACOTAR.py --unico` gera um arquivo
   auto-extraivel com o projeto inteiro.
"""

MANIFESTO = """\
## 1. Manifesto: que problema isto resolve

Um DBA senior atende incidente em Oracle (Exadata, RAC, CDB/PDB) em ambiente
corporativo de grande porte. Sob pressao, tres coisas dao errado, sempre as
mesmas:

- **Palpite em vez de evidencia.** "Deve ser IO" vira acao em producao sem um
  numero que sustente. O framework existe para inverter isso: coleta primeiro,
  conclusao depois.
- **Coleta manual irregular.** Cada um coleta o que lembra, no formato que
  prefere. Dois incidentes iguais geram evidencias incomparaveis, e ninguem
  consegue dizer "isto ja aconteceu antes".
- **O diagnostico morre no chat.** O que foi visto, testado e descartado nao
  vira registro; o proximo plantao comeca do zero e o chamado e fechado com
  "reiniciamos e normalizou".

O framework e um **pipeline read-only**: coleta padronizada -> evidencia em
arquivo -> analise deterministica -> achados com severidade -> relatorio
(dashboard HTML, PPTX, texto de chamado) e historico por servidor.

Duas frases resumem a filosofia e valem mais que qualquer detalhe de
implementacao:

> **Nao se toca em producao para descobrir o que esta acontecendo.**
> Observacao read-only vem antes de qualquer acao. Sempre.

> **Prioriza-se por DB time e wait class, nao por sintoma.**
> Method R (Cary Millsap): o que consome tempo de banco e o que importa; o resto
> e ruido com aparencia de problema.
"""

PRINCIPIOS = """\
## 2. Principios inegociaveis

Estes sete pontos definem o framework. Um framework que viole qualquer um deles
pode ate rodar, mas nao e este - e, em producao, e perigoso.

**2.1 Read-only por padrao, escrita por excecao explicita.**
Toda consulta abre com `SET TRANSACTION READ ONLY`. Cada check declara
`read_only: true` e passa por um validador que recusa o que nao for `SELECT`
(ou `WITH ... SELECT`). O unico recurso que escreve no banco - o SQL Tuning
Advisor, que cria tuning tasks - fica atras de uma flag `--advisor`, desligada,
que exige confirmacao e **nunca** pode ser disparada por automacao, tela ou
varredura.

**2.2 Licenca e restricao tecnica, nao detalhe juridico.**
Diagnostics Pack e Tuning Pack (`v$active_session_history`, `dba_hist_*`,
`dbms_sqltune`) sao cobrados por uso. Consultar sem licenca gera passivo real.
Cada check declara `requires_pack`, e existe um modo que pula esses checks e
registra `[skip]` em vez de rodar. Silenciosamente consumir pack e falha grave.

**2.3 A senha e digitada a cada execucao e nunca fica guardada.**
Sem variavel de ambiente persistida, sem arquivo, sem registro do Windows, sem
cache. Terminal usa `getpass`; interface grafica usa um canal proprio. A senha
nunca vai por linha de comando (`argv` e legivel por outro processo do mesmo
usuario) - vai pelo **stdin** do processo filho.

**2.4 Evidencia e fotografia, nao janela ao vivo.**
A analise nunca reconecta. Ela le os arquivos gravados na coleta. Consequencia
que se erra com facilidade: **o "agora" da analise e o mtime do arquivo de
evidencia, nao `datetime.now()`**. Toda idade derivada (uptime, quando as
estatisticas foram coletadas, idade do backup) parte dai. Reanalisar um caso de
tres dias atras tem de dar o MESMO resultado de quando foi coletado.

**2.5 Conhecimento e DADO, nao codigo.**
Os checks vivem num catalogo JSON; os playbooks, em JSON; as regras de boa
pratica, em JSON; o dicionario de erros ORA-, em JSON. Acrescentar um check ou
uma regra **nao exige tocar em codigo Python**. Isto nao e preciosismo: e o que
permite que o conhecimento do DBA entre no framework sem virar um pull request.

**2.6 Uma hipotese por vez, registrada.**
O caso guarda hipotese atual, hipoteses descartadas com motivo, acoes tomadas
com nivel de risco, escalonamento e causa raiz. Registrar hipotese nova
**descarta a anterior** explicitamente. O relatorio final e transcricao desse
registro - nao um texto escrito do zero no fim.

**2.7 Acao destrutiva exige impacto, rollback e aprovacao.**
Cada passo de playbook declara risco: `leitura` (nao muda nada), `mudanca`
(altera configuracao, reversivel) ou `destrutivo` (pode causar perda ou
indisponibilidade). A ferramenta **mostra** o comando para o humano revisar e
rodar onde for devido; ela nao executa acao destrutiva.
"""

ARQUITETURA = """\
## 3. Arquitetura

### 3.1 O pipeline

```
   [1] COLETA (unico ponto que conecta no banco)
       catalogo de checks -> SET TRANSACTION READ ONLY -> valida SELECT
       -> executa -> grava 1 arquivo de texto por check
                             |
                             v
   [2] EVIDENCIA (pasta do caso, em disco)
       troubleshooting/<caso>/*.txt  +  meta.json (contexto)  +  history.json
                             |
                             v
   [3] ANALISE (offline, nunca reconecta)
       {analisadores} analisadores especializados; cada um diz se sabe ler um
       arquivo (matches) e produz uma Secao com metricas, tabela e achados
                             |
                             v
   [4] ACHADOS + HEALTH SCORE
       severidade por achado -> severidade da secao -> score 0-100 do caso
                             |
                             v
   [5] SAIDA
       dashboard HTML (abas) + pagina de historico + PPTX + texto do chamado
       + indice de casos + consolidado do dia (CSV)
```

Regra de ouro do pipeline: **as etapas 3 a 5 nao abrem conexao**. Isso permite
reanalisar evidencia antiga, gerar relatorio numa maquina sem rede e - o que
mais importa - garante que ninguem "conserta" um numero reconsultando o banco.

### 3.2 Separacao de responsabilidades

| Camada | Responsabilidade | Regra |
|---|---|---|
| CLI (raiz) | receber comando, pedir senha, imprimir progresso | `print` aqui E a interface |
| nucleo | coletar, analisar, pontuar, gerar saida | nao imprime; devolve dados |
| dados (JSON) | checks, playbooks, regras, erros ORA- | editavel sem tocar em codigo |
| interface local | disparar os mesmos CLIs e mostrar o resultado | nao reimplementa regra |

A interface grafica e um servidor HTTP **local** (loopback) que dispara os
mesmos executaveis de linha de comando e transmite a saida. Ela **nao** duplica
logica de analise: se a tela precisa de um numero, ela le o JSON que o nucleo ja
gravou. Toda vez que essa fronteira foi violada, o resultado foi tela e
relatorio discordando sobre o mesmo caso.

### 3.3 Modulos (lidos do repositorio nesta geracao)
"""

CONTRATOS = """\
## 4. Contratos de dados

Estes formatos sao a interface entre as etapas. Mude-os e o resto para de casar.

### 4.1 Achado, metrica, secao, resultado (nucleo da analise)

```python
SEVERIDADE = "ok" | "warn" | "crit" | "info"
# ordem para agregacao: info(0) < ok(1) < warn(2) < crit(3)
# a severidade de uma SECAO e a MAIOR dos seus achados
# cores: ok=#22c55e  warn=#f59e0b  crit=#ef4444  info=#38bdf8
# rotulos em pt-BR: OK / ATENCAO / CRITICO / INFO

Metric:  name, value, unit="", severity="info", hint=""
Finding: title, detail="", severity="info", impact="", recommendation=""
Section: title, metrics[], findings[], table_headers[], table_rows[][],
         row_severity[], note="", images[]
CaseResult: case, kind, title, subtitle, summary, summary_items[],
            sections[], generated_at, meta{}, diff{}, erros[]
```

`impact` existe para ordenar por dor (ex.: "42% DB time"), nao para enfeitar.
`erros[]` guarda analisador que estourou: sem isso, uma excecao vira uma secao
ausente que ninguem nota.

### 4.2 Check do catalogo

```json
{
  "id": "identificador_estavel",
  "title": "titulo curto",
  "subtitle": "o que a saida mostra",
  "category": "agrupamento no relatorio",
  "purpose": "por que este check existe",
  "sql": "SELECT ... (uma unica instrucao, sem ';')",
  "output_file": "nome_do_arquivo.txt",
  "analyzer": "qual analisador le esta saida",
  "read_only": true,
  "requires_pack": null | "diagnostics" | "tuning",
  "min_privs": ["privilegio necessario"],
  "thresholds": {"warn": <numero>, "crit": <numero>},
  "references": ["doc oficial / MOS / fonte"]
}
```

`read_only` **nao** e confianca: um validador reprova qualquer SQL que nao seja
`SELECT`/`WITH...SELECT` antes de executar. Declaracao e validacao sao camadas
diferentes; as duas tem de existir.

### 4.3 Playbook e passo

```json
{
  "id": "ora_00060_deadlock",
  "titulo": "...",
  "quando_usar": "...",
  "sintomas": ["..."],
  "tags": ["..."],
  "secoes": ["secao do relatorio que sugere este playbook"],
  "ordem": 10,
  "nota": "...",
  "fontes": ["..."],
  "passos": [{
    "titulo": "...",
    "objetivo": "o que este passo responde",
    "onde": "onde rodar (SQL*Plus, shell do servidor, ...)",
    "comando": "o comando exato",
    "olhar": "o que observar na saida",
    "armadilha": "o erro comum neste passo",
    "risco": "leitura" | "mudanca" | "destrutivo"
  }]
}
```

`secoes` e o que liga **achado -> playbook**: o relatorio encontra um problema
numa secao e a interface oferece o passo a passo correspondente, sem o DBA
precisar saber que aquele playbook existe. `armadilha` e obrigatorio na pratica:
e o campo que impede o passo de ser seguido ao pe da letra no contexto errado.

### 4.4 Estado do caso

```
troubleshooting/<caso>/
    *.txt              evidencia bruta (uma por check)
    meta.json          conn, hostname, versao, modo de coleta, quando
    history.json       ciclo de vida: hipoteses, acoes, escalonamento, resolucao
output/troubleshooting/<caso>/
    <caso>.html            dashboard (abas)
    <caso>_historico.html  pagina de historico (sparkline, diff, reincidencia)
    <caso>_findings.json    achados + score, consumido pela interface
    <caso>.pptx / .md       deck e texto do chamado
output/troubleshooting/index.json   severidade e score por caso
```

A interface le `<caso>_findings.json`. Ela nunca recalcula severidade nem score
- e o que garante que tela e relatorio digam a mesma coisa.
"""

INVARIANTES = """\
## 5. Invariantes (cada uma nasceu de um incidente)

Esta e a secao mais valiosa deste documento. Sao erros que **ja aconteceram** e
custaram tempo. Um framework reconstruido sem elas repete todos.

**5.1 O "agora" da analise e o mtime da evidencia.**
Origem: idade de backup e uptime calculados com `datetime.now()` mudavam a cada
reanalise - o mesmo caso "envelhecia" sozinho e um relatorio regerado
contradizia o anterior. Regra: existe **uma** funcao que define o instante de
referencia, e ela le o mtime do arquivo. Nenhum analisador chama `now()`.

**5.2 Melhora nao e regressao.**
Origem: a secao de regressao acusava planos que tinham **melhorado**, porque
comparava valor absoluto sem olhar o sinal. Regra: regressao exige piora E
materialidade (um piso percentual, ex.: 30%), com excecao para o que passa de um
limite absoluto de tempo. Achado de baixa materialidade ensina o leitor a
ignorar a secao inteira - e ai ele perde o achado que importa.

**5.3 A mesma constatacao nao pode ser contada duas vezes.**
Origem: dois analisadores diferentes (regressao e baseline) reportavam a MESMA
troca de plano como dois achados, dobrando o peso no score. Regra: quando duas
secoes podem falar do mesmo objeto, o piso de materialidade fica num lugar so e
e **importado** pela outra. Se as duas divergirem, uma acusa e a outra cala
sobre a mesma linha.

**5.4 O framework nao pode se acusar.**
Origem: a analise de carga por modulo reportava as sessoes do proprio coletor
como consumidoras. Regra: as sessoes do framework saem da analise.

**5.5 Caso recoletado nao e regressao do codigo.**
Origem: um caso apareceu como "regressao" no corpus porque o DBA recoletou o
mesmo caso no meio do dia - mudou o banco, nao o codigo. Regra: quem decide e a
**impressao digital da evidencia** (nome, tamanho e mtime dos arquivos,
excluindo os arquivos de anotacao do caso, que mudam quando o DBA escreve).
Mesma impressao + diagnostico diferente = regressao de verdade.

**5.6 A senha nunca vai por `argv`.**
Origem: `Win32_Process.CommandLine` e legivel por outro processo do mesmo
usuario - senha em linha de comando e senha exposta. Regra: stdin do filho, e um
protocolo proprio quando quem pergunta e uma tela sem terminal.

**5.7 Quem pergunta pelo stdin tem de ser retomavel.**
Origem: recarregar a pagina (F5) durante uma coleta deixava o processo filho
esperando senha para sempre, com a tela dizendo "rodando" e sem campo nenhum.
Regra: o acompanhamento de um trabalho em andamento e retomado ao carregar a
tela, nao apenas por quem clicou no botao.

**5.8 Ferramenta interna nao vai na distribuicao.**
Origem: a bateria de teste contra os servidores de homologacao do time ia no
pacote; quem recebia via um card que so sabia falhar e um inventario de
servidores que nao era dele. Regra: o que e interno e barrado no empacotamento
**e** escondido na interface **e** recusado na rota - esconder o botao nao basta,
porque a rota aceita chamada de qualquer origem local.

**5.9 Interface local nao dispensa validacao.**
Origem: nome de caso e nome de lista chegam de um formulario e viram caminho de
arquivo. Regra: nome de caso passa por lista branca de caracteres; caminho e
sempre resolvido e conferido contra a pasta base (nada de `..`); a flag que
escreve no banco e barrada **por comando**, nao por processo.

**5.10 Erro de analisador tem de aparecer.**
Origem: excecao num analisador virava um `[AVISO]` no terminal e sumia; o caso
saia com uma secao a menos e ninguem percebia. Regra: o erro e registrado no
resultado do caso e acusado depois pela rotina de verificacao.

**5.11 A pasta pode nao ser um ambiente instalado.**
Origem: a versao portatil existe para nao instalar nada, mas cinco dos sete
atalhos exigiam o ambiente virtual e mandavam "rode o instalador". Regra: todo
atalho tenta o ambiente virtual e **cai** para o interpretador que acompanha a
pasta; mensagem de erro cita os dois cenarios.

**5.12 Numero em documento e recontado, nao escrito.**
Origem: contagens de checks e testes citadas na mao envelheceram caladas em
varios documentos. Regra: material gerado reconta do repositorio na hora.
"""

ORDEM = """\
## 6. Ordem de reconstrucao

Nao comece pela interface. Esta ordem existe porque cada etapa vira o teste da
seguinte.

**Passo 1 - Modelo e severidade.** Os tipos da secao 4.1 e a agregacao de
severidade. Sem isto nada mais tem onde encaixar.

**Passo 2 - Catalogo e validador.** Carregue os checks da secao 8 e escreva o
validador que recusa SQL que nao seja leitura. Teste com um `UPDATE`
disfarcado em `WITH`. Este e o guarda que protege producao: escreva-o antes de
qualquer codigo que conecte.

**Passo 3 - Coleta.** Conexao, `SET TRANSACTION READ ONLY`, um arquivo por
check, `[skip]` para o que exige pack sem licenca, senha por stdin. Grave
`meta.json` com o contexto.

**Passo 4 - Analisadores.** Comece por dois (sessoes e espaco) para firmar o
contrato `matches`/`analyze`, e so depois multiplique. Todos leem arquivo;
nenhum conecta; nenhum chama `now()`.

**Passo 5 - Score e achados.** Severidade -> secao -> score 0-100. Exporte o
JSON de achados: e o que a interface vai ler.

**Passo 6 - Relatorio.** Dashboard com abas, historico em pagina separada,
depois PPTX e texto de chamado.

**Passo 7 - Ciclo de vida do caso.** Hipotese (descartando a anterior), acao com
risco, escalonamento, resolucao. O texto do chamado e transcricao disto.

**Passo 8 - Playbooks.** Carregue os da secao 8 e ligue achado -> playbook pelo
campo `secoes`.

**Passo 9 - Interface local.** Loopback, dispara os CLIs, le os JSONs. Nenhuma
regra nova aqui.

**Passo 10 - Testes.** Na verdade: escreva o teste de cada invariante da secao 5
**junto** com o passo correspondente. A invariante 5.1 (o "agora" da evidencia)
e 5.2 (melhora nao e regressao) sao as que voltam a quebrar sozinhas - deixe as
duas travadas por teste desde o inicio.
"""

NAO_INVENTAR = """\
## 7. O que nao inventar

- **Os SQLs.** Estao na secao 8. Foram apurados contra bancos reais e tres bugs
  silenciosos ja foram corrigidos neles (uma metrica de memoria que reportava
  fracao errada, um padrao de erro que perdia `ORA-600`, e um comando de
  verificacao de backup classificado como leitura quando escreve).
- **Os limites (thresholds).** Vem no catalogo. Numero redondo inventado gera
  alarme falso, e alarme falso treina o DBA a ignorar o relatorio.
- **Os playbooks.** Sao {passos} passos com armadilha documentada. O campo
  `armadilha` e o que separa um passo util de um comando perigoso fora de
  contexto.
- **O dicionario de erros ORA-.** Traducao com causa provavel e primeira acao.
- **As regras de boa pratica.** Sao {regras} regras em {familias} familias
  (parametros, configuracao do banco, multiplexacao, instalacao, HA, seguranca,
  estrutura), cada uma com motivo, acao e referencia. Sao dado, nao `if`
  espalhado no codigo: e o que permite acrescentar regra sem tocar em analisador.

Se algum destes faltar, o framework roda e parece completo - e e exatamente por
isso que a ausencia e perigosa. O modo de descobrir e um diagnostico que
verifica os dados carregados e reclama quando um conjunto esta vazio.
"""


def _regras_bp() -> tuple:
    """(quantas regras, quantas familias) de best practice.

    Recontado do JSON pelo mesmo motivo do resto: a familia nova entrou como
    dado, e um numero escrito na mao aqui envelheceria calado - exatamente a
    invariante 5.12 que este documento prega. A contagem vive em
    `numeros_doc`, que e a mesma que abastece o bloco do README: duas
    contagens do mesmo dado sao duas oportunidades de discordar.
    """
    from . import numeros_doc
    return numeros_doc.regras_bp()


def _passos_kb() -> int:
    from . import numeros_doc
    return numeros_doc.passos_kb()


def _cabecalho(num: dict, com_dados: bool) -> str:
    passos = _passos_kb()
    return (
        "# ParraDibiei - especificacao para reconstrucao\n\n"
        "> Framework de troubleshooting Oracle read-only. Este arquivo e a\n"
        "> especificacao completa para recriar a plataforma do zero.\n\n"
        f"- **Gerado em**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"- **Checks no catalogo**: {num.get('checks', '?')}\n"
        f"- **Playbooks / passos**: {num.get('playbooks', '?')} / {passos}\n"
        f"- **Analisadores**: {num.get('analisadores', '?')}\n"
        f"- **Erros ORA- catalogados**: {num.get('erros_ora', '?')}\n"
        f"- **Funcoes de teste no projeto original**: {num.get('testes', '?')}"
        " (o executor reporta mais, por parametrizacao)\n"
        f"- **Dados de dominio literais nesta copia**: "
        f"{'SIM (secao 8)' if com_dados else 'NAO (gerado com --sem-dados)'}\n\n"
        "---\n\n")


def _tabela_modulos() -> str:
    linhas = ["", "| Modulo | Papel |", "|---|---|"]
    for rel, doc in _modulos():
        doc = (doc or "").replace("|", "/")
        linhas.append(f"| `{rel}` | {doc[:110]} |")
    return "\n".join(linhas) + "\n"


def _secao_dados() -> str:
    """Os dados de dominio, literais.

    Vao como conteudo bruto dos arquivos, sem reserializar: qualquer
    transformacao aqui (reindentar, reordenar chaves, normalizar acento) e uma
    chance de alterar um SQL sem querer. O que este documento promete sobre
    estes bytes e que eles sao os mesmos que rodam em producao.
    """
    partes = ["## 8. Dados de dominio (copiar literalmente)\n",
              "Cada bloco abaixo e o conteudo integral de um arquivo do projeto "
              "original. Recrie os arquivos com exatamente este conteudo.\n"]
    n = 0
    for rel in DADOS:
        txt = _ler(rel)
        if not txt:
            continue
        n += 1
        partes.append(f"\n### 8.{n} `{rel}`\n\n```json\n{txt.rstrip()}\n```\n")
    arquivos = _kb_arquivos()
    if arquivos:
        partes.append(f"\n### 8.{n + 1} Playbooks - `kb/` "
                      f"({len(arquivos)} arquivos)\n")
        for p in arquivos:
            nome = os.path.basename(p)
            try:
                with open(p, encoding="utf-8") as f:
                    txt = f.read()
            except OSError:
                continue
            partes.append(f"\n#### `kb/{nome}`\n\n```json\n{txt.rstrip()}\n```\n")
    return "".join(partes)


def montar(com_dados: bool = True) -> str:
    """O blueprint inteiro, como texto."""
    num = _numeros()
    regras, familias = _regras_bp()
    # Todo numero da prosa sai daqui, recontado. O blueprint prega na invariante
    # 5.12 que numero escrito na mao envelhece calado - ele nao pode ser o
    # primeiro a desobedecer (e ja foi: citava 74 checks depois de virarem 78).
    corpo = [
        _cabecalho(num, com_dados),
        COMO_USAR.format(checks=num.get("checks", 0),
                         playbooks=num.get("playbooks", 0)), "\n---\n\n",
        MANIFESTO, "\n",
        PRINCIPIOS, "\n",
        ARQUITETURA.format(analisadores=num.get("analisadores", 0)),
        _tabela_modulos(), "\n",
        CONTRATOS, "\n",
        INVARIANTES, "\n",
        ORDEM, "\n",
        NAO_INVENTAR.format(passos=_passos_kb(), regras=regras,
                            familias=familias), "\n",
    ]
    if com_dados:
        corpo.append(_secao_dados())
    else:
        corpo.append(
            "## 8. Dados de dominio\n\n"
            "**Ausentes nesta copia** (gerada com `--sem-dados`). Sem eles o\n"
            "framework recriado tem a arquitetura certa e nenhum conhecimento\n"
            "Oracle: nao ha checks para rodar nem playbooks para sugerir. Gere\n"
            "de novo sem a flag para incluir os dados.\n")
    return "".join(corpo)


def gerar(destino: str = "", com_dados: bool = True) -> str:
    """Escreve o blueprint e devolve o caminho."""
    pasta = destino or DEST_PADRAO
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, NOME_PADRAO)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(montar(com_dados=com_dados))
    return caminho
