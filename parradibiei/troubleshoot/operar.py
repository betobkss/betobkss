"""
Aba "Como operar" do dashboard: passo a passo de instalacao e o mapeamento
completo dos comandos (linguagem natural no chat <-> comando no PowerShell).

Cada comando tem um botao que COPIA para a area de transferencia e abre um
modal com as instrucoes de execucao - no mesmo espirito do "Lanzador" do
dashboard historico, mas com o escopo deste framework (troubleshooting
read-only de um caso).

Fonte de verdade: os comandos aqui refletem as flags reais de COLETAR.py /
TROUBLESHOOT.py e os aliases .bat da raiz do projeto.
"""
from __future__ import annotations

import html


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


# ── Instalacao em outra maquina ───────────────────────────────────────────
_INSTALL = [
    ("Copie a pasta do projeto <b>sem o <code>.venv</code></b>",
     "O <code>.venv</code> guarda caminhos absolutos da maquina de origem e nao "
     "funciona em outro PC. Pode pular <code>output/</code> e "
     "<code>troubleshooting/</code> tambem (sao resultados).", ""),
    ("Crie o ambiente, instale e valide",
     "Cria o <code>.venv</code>, instala as dependencias (cai no "
     "<code>wheelhouse/</code> se nao houver rede), roda os testes e o doutor.",
     ".\\install.bat"),
    ("Limpe qualquer senha antiga guardada nesta maquina",
     "Versoes anteriores ofereciam gravar a senha em <code>ORA_PASSWORD</code>, "
     "em <b>texto puro no registro do Windows</b>. Hoje a senha e digitada a "
     "cada execucao; este comando remove o que ficou para tras.",
     ".\\limpar_senha.bat"),
    ("Confira que ficou tudo pronto",
     "Diagnostico da instalacao sem tocar em banco: Python, venv, oracledb, "
     "catalogo MCP, conexoes, senha e permissao de escrita.",
     ".\\verificar.bat"),
]

_PRE = (
    "<b>Pre-requisito unico:</b> Python 3.11+ na maquina. "
    "<b>Nao</b> precisa de Oracle Client (o <code>oracledb</code> roda em modo "
    "thin) nem de internet (o <code>wheelhouse/</code> cobre - leve ele junto se "
    "a maquina de destino for isolada).<br>"
    "<b>Deve viajar junto:</b> o codigo, <code>mcp/catalog.json</code>, "
    "<code>stringConexao/</code> e <code>requirements*.txt</code>.<br>"
    "<b>Antes de copiar o <code>stringConexao/</code>:</b> o framework nunca "
    "grava senha (ela e pedida no prompt), mas o export do SQL Developer traz "
    "o campo <code>password</code> de toda conexao salva com <i>Save "
    "Password</i>. Confira o arquivo antes de leva-lo para fora da maquina."
)

# ── Mapeamento chat <-> prompt ────────────────────────────────────────────
# (titulo do grupo, nota, [(fale assim no chat, comando no PowerShell)])
_GRUPOS = [
    ("1. Healthcheck e coleta",
     "Coleta read-only completa e ja gera o relatorio. O nome do caso vira a "
     "pasta em <code>troubleshooting/</code>.",
     [("healthcheck do dbsrv600", ".\\healthcheck.bat DBSRV600"),
      ("healthcheck rapido no dbsrv600", ".\\healthcheck.bat DBSRV600 --rapido"),
      ("coletar INC000111222 no dbsrv600",
       ".\\coletar.bat INC000111222 --conn DBSRV600 --completo"),
      ("coletar sem consumir pack",
       ".\\coletar.bat INC000111222 --conn DBSRV600 --parcial"),
      ("so coletar, sem relatorio",
       ".\\coletar.bat INC000111222 --conn DBSRV600 --no-report"),
      ("rodar o SQL Tuning Advisor (escreve no banco, consome Tuning Pack)",
       ".\\coletar.bat INC000111222 --conn DBSRV600 --advisor"),
      ("listar as conexoes", ".\\coletar.bat --list-conns"),
      ("adicionar conexao nova",
       ".\\coletar.bat --add --conn NOME --host H --port 1529 "
       "--service SVC --user dbaread"),
      # Host de exemplo generico: o dashboard e anexado em chamado e enviado
      # por e-mail, entao nao carrega nome de dominio interno.
      ("servidor que nao esta no repositorio",
       ".\\coletar.bat INC000111222 --host db01.exemplo.local --service DBSRV600 "
       "--port 1529")]),

    ("2. Relatorio (redija)",
     "Reprocessa as evidencias que ja estao na pasta do caso. <b>Nao</b> "
     "reconecta no banco - use quando ajustar algo e quiser so o relatorio.",
     [("redija {CASO}", ".\\redija.bat {CASO}"),
      ("redija -completo", ".\\redija.bat {CASO}"),
      ("detalhar {CASO}  (eu leio o relatorio e resumo, sem recoletar)",
       "type output\\troubleshooting\\{CASO}\\{CASO}_relatorio.md"),
      ("listar os casos", ".\\.venv\\Scripts\\python.exe TROUBLESHOOT.py --list"),
      ("criar pasta do caso INC123",
       ".\\.venv\\Scripts\\python.exe TROUBLESHOOT.py --new INC123"),
      ("abrir o dashboard",
       "start output\\troubleshooting\\{CASO}\\{CASO}.html")]),

    ("3. Flags de desempenho",
     "Referencia de tempo medida: coleta completa ~5s, modo rapido ~3s.",
     [("triagem rapida (pula o historico AWR)",
       ".\\healthcheck.bat DBSRV600 --rapido"),
      ("mais paralelismo", ".\\coletar.bat INC1 --conn DBSRV600 --workers 8"),
      ("cortar query travada", ".\\coletar.bat INC1 --conn DBSRV600 --timeout 60"),
      ("sem gerar PPTX", ".\\coletar.bat INC1 --conn DBSRV600 --no-pptx"),
      ("abrir o dashboard no fim", ".\\coletar.bat INC1 --conn DBSRV600 --open")]),

    ("4. Drill-down de um achado",
     "O proximo nivel de investigacao, tudo <b>read-only</b>. O relatorio ja "
     "monta esses comandos prontos em <b>Proximo passo</b>, dentro do Guia de "
     "Sugestoes - o servidor sai do <code>meta.json</code> do caso. A ultima "
     "linha e a unica que <b>escreve</b> no banco.",
     [("detalhar o sql 7ktb2sm9ug3xw",
       ".\\.venv\\Scripts\\python.exe DETALHAR_SQL.py {CASO} 7ktb2sm9ug3xw "
       "--conn DBSRV600"),
      ("analisar a janela das 14h",
       ".\\.venv\\Scripts\\python.exe JANELA.py {CASO} "
       "--inicio \"2026-07-29 14:00\" --fim \"2026-07-29 15:00\" --conn DBSRV600"),
      ("rodar o advisor neste caso (ESCREVE + Tuning Pack)",
       ".\\coletar.bat {CASO} --conn DBSRV600 --advisor")]),

    ("6. Senha e ambiente",
     "A senha e <b>sempre digitada na hora</b>, a cada execucao - no terminal "
     "(sem eco) ou na caixa mascarada do painel. Nao existe arquivo, variavel "
     "de ambiente nem parametro que a substitua, e ela morre com o processo. "
     "Na bateria, e pedida uma unica vez e vale para todos os servidores.",
     [("remover senha guardada por versoes antigas", ".\\limpar_senha.bat"),
      ("instalar / atualizar o ambiente", ".\\install.bat"),
      ("verificar a instalacao", ".\\verificar.bat")]),

    ("7. Catalogo de checks (MCP)",
     "O <code>mcp/catalog.json</code> e a fonte unica de verdade dos checks. "
     "<b>Depois de altera-lo, regere o coletor do Citrix</b> (ultimo comando) - "
     "senao o ambiente Citrix continua com a query antiga.",
     [("quais checks existem", ".\\.venv\\Scripts\\python.exe -m mcp.loader --list"),
      ("categorias de check",
       ".\\.venv\\Scripts\\python.exe -m mcp.loader --categories"),
      ("mostrar o check compare_period",
       ".\\.venv\\Scripts\\python.exe -m mcp.loader --show compare_period"),
      ("regerar o coletor do Citrix",
       ".\\.venv\\Scripts\\python.exe -m mcp.loader --gen-sql queries\\coletor.sql")]),

    ("8. Coleta pelo Citrix (sem SQL*Net)",
     "Quando nao ha rota do seu PC para o banco: rode o script no SQL*Plus do "
     "Citrix, copie o <code>coletor_troubleshoot.txt</code> para "
     "<code>troubleshooting\\{CASO}\\</code> e rode o redija.",
     [("rodar o coletor no SQL*Plus", "@coletor.sql"),
      ("gerar o relatorio a partir do arquivo", ".\\redija.bat {CASO}")]),
]

# ── Comandos da persona (so no chat) ─────────────────────────────────────
_PERSONA = [
    ("/parra ou \"novo\"", "inicia o troubleshooting do zero, read-only primeiro"),
    ("\"coletar\"", "monta o checklist de coleta para o sintoma relatado"),
    ("\"detalhar\"", "RCA profundo da causa raiz, sem recoletar"),
    ("\"proximo\"", "descarta a hipotese atual e avanca (registra no historico)"),
    ("/redija-completo", "consolida em dashboard HTML + PPTX + Template Padrao"),
    ("\"Fim do dia\"", "consolida os casos do dia"),
]

_INSTRUCOES = [
    "Clique em <b>copiar</b> em qualquer comando: ele vai para a area de "
    "transferencia e abre a janela com o passo a passo.",
    "Abra o <b>PowerShell na raiz do projeto</b> "
    "(<code>ParraDibiei - checklist</code>) e cole com "
    "<b>Ctrl+V</b>, depois <b>Enter</b>. Os aliases <code>.bat</code> ja "
    "resolvem o caminho do <code>.venv</code> sozinhos.",
    "<b>Vai pedir a senha</b> - sempre, toda vez que conectar num banco. "
    "No terminal ela nao aparece enquanto voce digita; no painel, a caixa "
    "mascarada surge no cartao <b>Execucao</b> quando o comando pergunta.",
    "Ao terminar, a coleta ja gera <b>dashboard HTML, PPTX, relatorio no "
    "Template Padrao e export CSV/JSON</b> em "
    "<code>output\\troubleshooting\\&lt;caso&gt;\\</code>. Recarregue esta "
    "pagina (<b>F5</b>) para ver os dados novos.",
    "No chat voce fala natural (aceito abreviacao e typo - "
    "<i>healthycjeck</i>, <i>dbsrv600</i>, <i>reddija</i>): eu traduzo para o "
    "comando, executo com o diretorio correto e devolvo Health Score, top 3 "
    "achados, causa raiz e proximo passo.",
]


def _cmd_row(chat: str, cmd: str) -> str:
    """Linha da tabela. Recebe texto CRU: o escaping e feito aqui."""
    return (f'<tr><td class="op-chat">{_esc(chat)}</td>'
            f'<td><code class="op-cmd">{_esc(cmd)}</code></td>'
            f'<td style="text-align:right"><button class="btn copyb" '
            f'data-cmd="{_esc(cmd)}" data-titulo="{_esc(chat)}">copiar</button></td></tr>')


def build(case: str = "") -> str:
    """HTML da aba. `case` substitui {CASO} nos exemplos pelo caso atual."""
    caso = case or "<caso>"        # cru; escapado no ponto de uso
    parts = [
        '<div class="warnbox" style="background:rgba(59,130,246,.08);'
        'border-color:var(--acc)"><b>Leia-me primeiro.</b> Tudo neste framework '
        'e <b>read-only por construcao</b> (<code>SET TRANSACTION READ ONLY</code> '
        '+ validacao de SELECT). As unicas excecoes pedem opt-in explicito: '
        '<code>--advisor</code> (SQL Tuning Advisor: escreve e consome Tuning '
        'Pack) e os comandos do <code>acoes.sql</code>, que voce revisa e executa '
        'a mao.</div>',

        # instalacao
        '<div class="section"><h3>Instalar em outra maquina '
        '<span class="pill pill-ok">4 passos</span></h3><div class="secbody">'
        f'<div class="note" style="margin:0 0 12px">{_PRE}</div>'
        '<ol class="op-steps">',
    ]
    for i, (tit, desc, cmd) in enumerate(_INSTALL, 1):
        parts.append(f'<li><div class="op-t">{tit}</div>'
                     f'<div class="op-d">{desc}</div>')
        if cmd:
            parts.append(
                f'<div class="cmd-row"><div class="cmd">{_esc(cmd)}</div>'
                f'<button class="btn copyb" data-cmd="{_esc(cmd)}" '
                f'data-titulo="Passo {i}">copiar</button></div>')
        parts.append("</li>")
    parts.append("</ol></div></div>")

    # bloco da persona: entra na ordem, entre os grupos 4 e 6
    persona = (
        '<div class="section"><h3>5. Comandos da persona DBA Master '
        '<span class="pill pill-warn">so no chat</span></h3><div class="secbody">'
        '<div class="note" style="margin:0 0 10px">Estes nao tem equivalente no '
        'PowerShell: sao o fluxo de raciocinio guiado (uma hipotese por vez, '
        'sempre priorizando DB time / wait class).</div><ul class="op-persona">'
        + "".join(f'<li><code>{_esc(k)}</code> <span>&rarr; {v}</span></li>'
                  for k, v in _PERSONA)
        + "</ul></div></div>")

    # grupos de comando
    for titulo, nota, cmds in _GRUPOS:
        linhas = "".join(_cmd_row(c.replace("{CASO}", caso),
                                  p.replace("{CASO}", caso)) for c, p in cmds)
        parts.append(
            f'<div class="section"><h3>{titulo} '
            f'<span class="pill pill-info">{len(cmds)}</span></h3>'
            '<div class="secbody"><div class="note" style="margin:0 0 10px">'
            f'{nota.replace("{CASO}", _esc(caso))}</div>'
            '<div class="tbl-wrap"><table><thead><tr>'
            '<th>No chat (fale assim)</th><th>No prompt (equivalente)</th>'
            '<th style="text-align:right">acao</th></tr></thead>'
            f"<tbody>{linhas}</tbody></table></div></div></div>")
        if titulo.startswith("4."):
            parts.append(persona)

    # instrucoes finais
    parts.append(
        '<div class="section"><h3>Instrucoes</h3><div class="secbody">'
        '<ol class="op-steps op-slim">'
        + "".join(f"<li><div class=\"op-d\">{t}</div></li>" for t in _INSTRUCOES)
        + "</ol></div></div>")
    return "".join(parts)
