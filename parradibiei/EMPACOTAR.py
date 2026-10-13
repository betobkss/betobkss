r"""
EMPACOTAR.py - gera a versao portatil do framework (roda sem .venv e sem admin).

    .\.venv\Scripts\python.exe EMPACOTAR.py
    .\.venv\Scripts\python.exe EMPACOTAR.py --zipar          # gera tambem o .zip
    .\.venv\Scripts\python.exe EMPACOTAR.py --embed C:\tmp\python-embed.zip

O resultado fica em `dist\ParraTroubleshoot\`: Python embeddable + as
dependencias em `libs\` + o projeto. Quem receber a pasta so roda `INICIAR.bat`.

Por que assim, e nao PyInstaller: `--onefile` cria um executavel desconhecido que
AppLocker/antivirus corporativo costuma bloquear. E `.venv` NAO e portavel - o
`pyvenv.cfg` guarda o caminho absoluto da maquina que criou.

O pacote NUNCA leva evidencia (`troubleshooting\`, `output\`): sao dados de
producao (schemas, SQL de negocio, hostnames) e nao podem sair da maquina.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DESTINO_PADRAO = os.path.join(ROOT, "dist", "ParraTroubleshoot")

# Entra no pacote (o que o framework precisa para rodar).
# `kb` e DADO, nao codigo (28 playbooks .json): sem ela o pacote roda, mas o
# relatorio perde a ligacao achado -> passo a passo. Ficou de fora do pacote por
# um tempo justamente porque "roda sem" nao chama atencao - o doctor e quem
# denuncia ("nenhum playbook em kb/").
# `.devin` entrou em 09/2026, quando deixou de ser configuracao pessoal do
# editor e passou a carregar a PERSONA: a regra always-on do DBA Master, a
# skill com os comandos do framework e os workflows (/parra, /redija-completo).
# Sem ela o pacote entrega a ferramenta e nao entrega o DBA que sabe usa-la -
# quem recebia tinha COLETAR.py e nenhum comportamento. Custa 49 KB em 2,3 MB.
PASTAS = ["troubleshoot", "mcp", "queries", "kb", ".devin"]
PASTA_CONEXOES = "stringConexao"
# `limpar_senha.ps1` viaja porque `limpar_senha.bat` viaja e o chama. Regra
# geral: todo arquivo citado por um `.bat` do pacote tem de estar no pacote -
# `tests/test_empacotar.py::TestDependenciasDosBats` verifica isso sozinho, para
# um `.bat` novo nao repetir o bug de comando que quebra so na mao de quem recebe.
# `dba_master_prompt.txt.modelo` viaja pelo mesmo motivo do `.devin`: a regra
# always-on da persona e um RESUMO e aponta para o prompt completo. O prompt de
# verdade nao e versionado (cita a empresa), entao sem o modelo a persona chega
# na maquina nova referenciando um arquivo que nao existe - com a ferramenta
# inteira funcionando e o comportamento pela metade, que e o pior dos dois
# mundos porque nada quebra e ninguem percebe.
# `AGENTS.md` viaja pelo mesmo motivo do `.devin`: e regra ALWAYS-ON, lida
# automaticamente pelo agente na raiz do projeto. Ela guarda o que nao esta na
# persona nem na skill - que o shell do agente e bash e nao PowerShell, e o que
# TRAVA se for executado (qualquer comando que peca senha: o guarda usa isatty
# e para num prompt invisivel, em vez de errar rapido). Sem esse arquivo o
# agente na maquina nova repete os dois erros no primeiro comando.
ARQUIVOS_EXTRA = ["requirements.txt", "requirements-troubleshoot.txt",
                  "README.md", "COMANDOS.md", "INSTALL.md", "AGENTS.md",
                  "limpar_senha.ps1", "dba_master_prompt.txt.modelo"]

# Pastas de topo que ficam FORA do pacote e nao sao evidencia (essas estao em
# NUNCA): sao ferramentaria de desenvolvimento. Declaradas aqui para que o teste
# de cobertura force uma decisao quando o projeto ganhar uma pasta nova - o
# buraco antigo era `PASTAS` ser fixa, e pasta nova simplesmente nao viajar.
# `assets/` guarda imagem de marca para o deck de APRESENTACAO (logo, banner).
# Fica FORA do pacote de proposito: o pacote portatil e a ferramenta de plantao -
# quem apresenta gera o deck do repositorio. E as imagens tem licenca propria
# (marca de terceiro), entao nao viajam em copia distribuida.
PASTAS_FORA = {".github", "githooks", ".worktrees", "assets"}

# ARQUIVO local, dentro de pasta que viaja. Ate `.devin` entrar, o criterio
# "que pasta e esta?" bastava, porque nenhuma das pastas incluidas tinha
# arquivo ignorado dentro. `.devin` tem dois, e os dois sao exatamente o tipo
# de coisa que nao pode ser distribuida:
#
#   config.local.json      preferencia de quem desenvolve (o sufixo `.local.`
#                          e a convencao do proprio Devin para isso)
#   troubleshoot.md.bak    a regra APOSENTADA em 09/2026 - e ela contem a
#                          orientacao errada sobre senha que foi corrigida na
#                          versao nova. O pacote sairia com as duas lado a
#                          lado e quem recebesse nao teria como saber qual vale
#
# Filtro por NOME, e nao por `git check-ignore`: o empacotador precisa rodar a
# partir de uma copia sem `.git`. O teste
# `test_empacotar.py::TestNadaIgnoradoViaja` cobre a diferenca, comparando o
# pacote com o `.gitignore` de verdade quando ha repositorio.
SUFIXOS_FORA = (".bak", ".swp", ".orig", ".rej", ".tmp")
INFIXOS_FORA = (".local.",)

# Nao entram na copia da stdlib: nao servem para o framework e so incham o pacote.
STDLIB_FORA = {"test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip",
               "lib2to3", "pydoc_data", "site-packages", "__pycache__", "venv"}

# NUNCA entra: evidencia de producao, ambientes locais, lixo de build.
NUNCA = {"output", "troubleshooting", ".venv", "venv", "dist", "build",
         ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "wheelhouse",
         ".worktrees", "tests", "legado", "_backup_"}

# Ferramenta INTERNA: nao vai na distribuicao. A bateria e o teste de campo do
# NOSSO framework (roda a matriz de coleta contra os servidores de homologacao
# do time) - nao e funcionalidade para quem recebe o pacote. Levaria junto o
# habito de disparar N coletas contra bancos que nao sao dele, e o proprio
# `servidores.txt` ja e o inventario que decidimos nao distribuir.
# `servidores.txt` nao entra por outro caminho: so `.py`, `.bat` e ARQUIVOS_EXTRA
# saem da raiz. Esta lista trata do codigo.
SO_INTERNO = {"TESTE_HOMOLOG.py"}

# So faz sentido na instalacao a partir do FONTE. `install.bat` chama o
# `setup.ps1` para criar um `.venv` - no pacote portatil isso e duas vezes
# errado: o `setup.ps1` nao viaja (o `.bat` quebraria com erro de PowerShell) e o
# pacote existe exatamente para NAO instalar nada. Era o mesmo conselho errado do
# bug de 07/08, agora pela porta do arquivo em vez da mensagem.
SO_FONTE = {"install.bat"}


def url_embeddable(versao: str = "") -> str:
    v = versao or ".".join(str(x) for x in sys.version_info[:3])
    return f"https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"


def ajustar_pth(texto: str) -> str:
    """Liga o `site` e poe o projeto e o `libs\\` no sys.path do embeddable.

    Sem isso o Python embeddable ignora tudo que nao esta no proprio zip: nao
    acharia nem `oracledb` nem o pacote `troubleshoot`.
    """
    linhas = [l.rstrip() for l in texto.splitlines()]
    for l in ("..", "..\\libs"):
        if l not in linhas:
            linhas.append(l)
    saida = []
    for l in linhas:
        if l.strip() in ("#import site", "# import site"):
            l = "import site"
        saida.append(l)
    if "import site" not in saida:
        saida.append("import site")
    return "\n".join(saida) + "\n"


def deve_copiar(rel: str) -> bool:
    """rel e o caminho relativo a raiz do projeto, com barras normalizadas.

    Dois criterios, e o segundo nasceu com a entrada do `.devin`: alem da
    PASTA (NUNCA), o nome do ARQUIVO decide. Ver SUFIXOS_FORA/INFIXOS_FORA.
    """
    caminho = rel.replace("\\", "/")
    partes = caminho.split("/")
    if any(p in NUNCA or p.startswith("_backup_") for p in partes):
        return False
    nome = partes[-1].lower()
    if nome.endswith(SUFIXOS_FORA) or any(i in nome for i in INFIXOS_FORA):
        return False
    return True


# MIGRACAO: o conhecimento acumulado SOBRE ESTE PARQUE. Fica fora do pacote
# normal de proposito - `kb/` e `.devin/` sao generalizaveis e podem circular,
# mas isto aqui e host, IP, falha de producao e o raciocinio de cada caso.
#
# A distincao que faltava: "distribuir para outro DBA" e "migrar para o MEU
# outro PC" sao operacoes diferentes e o pacote so sabia fazer a primeira. Sem
# estes itens a maquina nova recebe os playbooks e a persona, mas comeca sem
# saber nada do parque - o inventario de 28 ambientes e os casos com hipotese,
# acao e RCA ficam para tras, que e justamente a parte que nao se reconstroi.
#
# `troubleshooting` esta em NUNCA, entao precisa de excecao explicita no walk.
CONHECIMENTO_PASTAS = ["troubleshooting"]
CONHECIMENTO_ARQUIVOS = ["ambientes.json", "servidores.txt",
                         "marcas_locais.txt", "dba_master_prompt.txt"]


def arquivos_do_projeto(root: str = ROOT, com_conexoes: bool = True,
                        migracao: bool = False) -> list:
    """Caminhos relativos que entram no pacote.

    `migracao=True` acrescenta o conhecimento local (inventario e casos). O
    padrao e False: o pacote nasce distribuivel, e quem quer o contrario pede.
    """
    itens = []
    for nome in sorted(os.listdir(root)):
        p = os.path.join(root, nome)
        if nome in SO_INTERNO or nome in SO_FONTE:
            continue
        if os.path.isfile(p) and (nome.endswith((".py", ".bat")) or
                                  nome in ARQUIVOS_EXTRA):
            itens.append(nome)
    if migracao:
        for nome in CONHECIMENTO_ARQUIVOS:
            if os.path.isfile(os.path.join(root, nome)):
                itens.append(nome)
        for pasta in CONHECIMENTO_PASTAS:
            base = os.path.join(root, pasta)
            for dirpath, _dirs, filenames in os.walk(base):
                for f in filenames:
                    itens.append(os.path.relpath(
                        os.path.join(dirpath, f), root))
    pastas = list(PASTAS) + ([PASTA_CONEXOES] if com_conexoes else [])
    for pasta in pastas:
        base = os.path.join(root, pasta)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in NUNCA]
            for f in filenames:
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                if deve_copiar(rel):
                    itens.append(rel)
    # `deve_copiar` reprova o conhecimento (troubleshooting esta em NUNCA), e e
    # ele que protege o pacote de distribuicao. Na migracao a excecao e
    # explicita e passa por fora do filtro, em vez de afrouxa-lo para todos.
    guardar = set(CONHECIMENTO_ARQUIVOS) if migracao else set()
    return [i for i in itens
            if deve_copiar(i) or i in guardar
            or (migracao and i.replace("\\", "/").split("/")[0]
                in CONHECIMENTO_PASTAS)]


INICIAR_BAT = """@echo off
:: Painel de Troubleshooting Oracle - versao portatil (sem instalacao)
:: Abre http://127.0.0.1:8765/ no navegador. Ctrl+C encerra.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%python\\python.exe"
if not exist "%PY%" (
    echo [ERRO] pacote incompleto: falta python\\python.exe
    exit /b 1
)
"%PY%" "%ROOT%PAINEL.py" %*
endlocal
"""

TERMINAL_BAT = """@echo off
:: Terminal do framework portatil: abre um prompt com o Python do pacote.
:: Ex.:  python COLETAR.py HC_DBSRV600 --conn DBSRV600 --completo
setlocal
set "ROOT=%~dp0"
doskey python="%ROOT%python\\python.exe" $*
echo Python do pacote: %ROOT%python\\python.exe
echo Ex.: python COLETAR.py HC_DBSRV600 --conn DBSRV600 --completo
echo.
echo Este terminal e o unico lugar do pacote com --advisor (escreve no banco
echo e consome Tuning Pack). O painel nao tem esse comando.
cmd /k
"""

LEIAME = """# Framework de Troubleshooting Oracle - versao portatil

Pasta autocontida: **nao instala nada**, nao pede admin e nao mexe no Python da
maquina. Copie a pasta inteira e rode.

## Como usar

1. `INICIAR.bat` -> abre o painel em <http://127.0.0.1:8765/>.
2. `TERMINAL.bat` -> prompt com o Python do pacote, para os comandos completos
   (`COLETAR.py`, `TROUBLESHOOT.py`, `CASO.py`, `--advisor`...).

## O que tem dentro

- `python\\` - Python embeddable (nao registra nada no Windows).
- `libs\\` - `oracledb` (modo thin, sem Oracle Client) e `python-pptx`.
- o framework e o repositorio de conexoes.

## Cuidados

- **Senha**: e pedida a cada execucao - no terminal sem eco, ou numa caixa
  mascarada no painel. Nao existe onde guarda-la, e isso e de proposito: nunca
  escreva a senha num arquivo dentro da pasta (o pacote e feito para circular).
- **O pacote nao leva evidencia**: `troubleshooting\\` e `output\\` nascem vazios.
  O que voce coletar fica na copia que estiver usando - trate como dado de
  producao.
- **Rede**: e preciso alcancar o listener Oracle (1529/1521) da maquina onde a
  pasta estiver.
"""


def baixar_com_curl(url: str, destino_zip: str) -> bool:
    """Baixa pelo curl.exe do Windows, que usa o repositorio de certificados do
    sistema (Schannel). E o unico que funciona atras do proxy corporativo com
    inspecao de TLS - o bundle do Python nao conhece a CA da empresa."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        return False
    # --ssl-revoke-best-effort: o proxy corporativo bloqueia CRL/OCSP e o
    # Schannel derruba a conexao por nao conseguir checar revogacao. O
    # certificado continua sendo validado; so a revogacao vira best-effort.
    r = subprocess.run([curl, "-sSL", "--fail", "--ssl-revoke-best-effort",
                        "-o", destino_zip, url], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    curl falhou: {(r.stderr or '').strip()[:200]}")
        return False
    return os.path.exists(destino_zip) and os.path.getsize(destino_zip) > 0


def baixar_com_powershell(url: str, destino_zip: str) -> bool:
    """Ultimo recurso: o .NET do PowerShell usa o repositorio do Windows e e mais
    tolerante com a cadeia do proxy do que o OpenSSL do Python."""
    ps = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not ps:
        return False
    r = subprocess.run(
        [ps, "-NoProfile", "-Command",
         f"Invoke-WebRequest -Uri '{url}' -OutFile '{destino_zip}' "
         f"-UseBasicParsing -TimeoutSec 180"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    powershell falhou: {(r.stderr or '').strip()[:200]}")
        return False
    return os.path.exists(destino_zip) and os.path.getsize(destino_zip) > 0


def montar_python_local(py_dir: str, base: str = "") -> str:
    """Monta o Python portatil copiando a instalacao desta maquina.

    Plano B para ambiente corporativo: aqui o proxy responde 403 ao baixar o
    zip embeddable do python.org (bloqueio de download de binario). Copiar o
    CPython ja instalado da o mesmo resultado - pasta isolada, sem instalacao,
    sem admin - e sem depender da rede.
    """
    base = base or sys.base_prefix
    print(f"[1/5] montando o Python a partir de {base} (sem download)")
    os.makedirs(py_dir, exist_ok=True)

    for nome in sorted(os.listdir(base)):
        if nome.lower().endswith((".exe", ".dll")):
            shutil.copy2(os.path.join(base, nome), os.path.join(py_dir, nome))
    dlls = os.path.join(base, "DLLs")
    if os.path.isdir(dlls):
        shutil.copytree(dlls, os.path.join(py_dir, "DLLs"), dirs_exist_ok=True)

    lib = os.path.join(base, "Lib")
    shutil.copytree(
        lib, os.path.join(py_dir, "Lib"), dirs_exist_ok=True,
        ignore=lambda d, nomes: [n for n in nomes if n in STDLIB_FORA])

    # O ._pth isola a pasta: o Python nao enxerga instalacao nem PYTHONPATH da
    # maquina que receber o pacote.
    versao = f"python{sys.version_info.major}{sys.version_info.minor}"
    with open(os.path.join(py_dir, f"{versao}._pth"), "w", encoding="utf-8") as fh:
        fh.write(ajustar_pth(".\nLib\nDLLs\n"))
    return py_dir


def baixar_embeddable(destino_zip: str, versao: str = "") -> str:
    url = url_embeddable(versao)
    print(f"[1/5] baixando {url}")
    try:
        urllib.request.urlretrieve(url, destino_zip)
        return destino_zip
    except Exception as e:
        print(f"    urllib nao passou ({type(e).__name__}); tentando o curl.exe")
    for tentativa in (baixar_com_curl, baixar_com_powershell):
        if tentativa(url, destino_zip):
            return destino_zip
    raise RuntimeError(
        "nao foi possivel baixar o Python embeddable neste ambiente.\n"
        f"       Baixe a mao:  {url}\n"
        "       e rode:  EMPACOTAR.py --embed <caminho do zip>")


def montar(destino: str, embed_zip: str = "", versao: str = "",
           com_conexoes: bool = True, root: str = ROOT,
           origem: str = "auto", migracao: bool = False) -> str:
    """origem: 'auto' (tenta o embeddable e cai para a copia local),
    'embeddable' (so o zip) ou 'local' (so a instalacao desta maquina).

    migracao: leva tambem o conhecimento local (inventario e casos). Pacote
    assim NAO e distribuivel - e copia da sua maquina para a sua maquina."""
    if os.path.exists(destino):
        shutil.rmtree(destino)
    os.makedirs(destino)
    py_dir = os.path.join(destino, "python")

    zip_local = embed_zip
    if origem != "local":
        if not zip_local:
            os.makedirs(os.path.join(root, "dist"), exist_ok=True)
            candidato = os.path.join(root, "dist", "python-embed.zip")
            if os.path.exists(candidato):
                print(f"[1/5] reaproveitando {candidato}")
                zip_local = candidato
            else:
                try:
                    zip_local = baixar_embeddable(candidato, versao)
                except RuntimeError as e:
                    if origem == "embeddable":
                        raise
                    print(f"    {e}")
                    print("    -> caindo para a copia do Python local")
                    zip_local = ""
        else:
            print(f"[1/5] usando {zip_local}")

    if zip_local:
        print("[2/5] extraindo o Python embeddable")
        with zipfile.ZipFile(zip_local) as z:
            z.extractall(py_dir)
        pth = [f for f in os.listdir(py_dir) if f.endswith("._pth")]
        if not pth:
            raise RuntimeError("zip do embeddable sem arquivo ._pth")
        alvo = os.path.join(py_dir, pth[0])
        with open(alvo, encoding="utf-8") as fh:
            texto = fh.read()
        with open(alvo, "w", encoding="utf-8") as fh:
            fh.write(ajustar_pth(texto))
    else:
        montar_python_local(py_dir)
        print("[2/5] Python local copiado e isolado pelo ._pth")

    print("[3/5] instalando as dependencias em libs\\")
    req = os.path.join(root, "requirements-troubleshoot.txt")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--target", os.path.join(destino, "libs"), "-r", req],
                   check=True)

    print("[4/5] copiando o framework"
          + (" + CONHECIMENTO LOCAL (migracao)" if migracao
             else " (sem evidencia)"))
    for rel in arquivos_do_projeto(root, com_conexoes, migracao):
        orig, dest = os.path.join(root, rel), os.path.join(destino, rel)
        os.makedirs(os.path.dirname(dest) or destino, exist_ok=True)
        shutil.copy2(orig, dest)
    for pasta in ("troubleshooting", os.path.join("output", "troubleshooting")):
        os.makedirs(os.path.join(destino, pasta), exist_ok=True)

    for nome, conteudo in (("INICIAR.bat", INICIAR_BAT),
                           ("TERMINAL.bat", TERMINAL_BAT),
                           ("LEIAME.md", LEIAME)):
        with open(os.path.join(destino, nome), "w", encoding="utf-8") as fh:
            fh.write(conteudo)
    return destino


# ── selecao para o CLONE (arquivo unico) ──────────────────────
# O clone tem outro objetivo que o pacote portatil: continuar o desenvolvimento
# em outra maquina. Por isso leva o que o pacote nao leva - testes, `.gitignore`,
# `pyproject.toml`, os `.ps1` e o `TESTE_HOMOLOG.py`.
#
# O motivo e concreto: os testes SAO a especificacao executavel, e boa parte
# deles le esses arquivos (o modelo versionado da lista, o script de limpeza de
# senha, a propria bateria). Levar os testes sem eles produz um clone que falha
# em 11 testes - e quem recebe conclui que o framework esta quebrado, quando
# quebrado estava o empacotamento.
PASTAS_CLONE = ["troubleshoot", "mcp", "queries", "kb", "tests", "githooks"]
EXT_CLONE = (".py", ".bat", ".ps1", ".md", ".toml", ".cfg", ".modelo")
ARQUIVOS_CLONE = {".gitignore", "requirements.txt",
                  "requirements-troubleshoot.txt"}

# Nao viaja nem no clone: inventario de servidores e caminhos da maquina. Sao
# dados do ambiente, nao do framework - e o clone existe para ser reaberto em
# OUTRO ambiente.
SENSIVEL = {"servidores.txt", "caminhos_locais.txt"}


def _clone_ok(rel: str) -> bool:
    """Como `deve_copiar`, mas `tests/` E bem-vindo aqui.

    Reusar `deve_copiar` direto excluiria a pasta de testes, que esta em `NUNCA`
    porque o pacote portatil nao a leva. Evidencia e ambiente local seguem
    barrados pela MESMA lista - so a regra de `tests` muda.
    """
    partes = rel.replace("\\", "/").split("/")
    bloqueados = NUNCA - {"tests"}
    if any(p in bloqueados or p.startswith("_backup_") for p in partes):
        return False
    return os.path.basename(rel) not in SENSIVEL


def arquivos_do_clone(root: str = ROOT, com_conexoes: bool = False) -> list:
    """Fonte completa para o clone: codigo, testes e configuracao."""
    itens = []
    for nome in sorted(os.listdir(root)):
        if not os.path.isfile(os.path.join(root, nome)):
            continue
        if not _clone_ok(nome):
            continue
        if nome.endswith(EXT_CLONE) or nome in ARQUIVOS_CLONE:
            itens.append(nome)
    pastas = list(PASTAS_CLONE) + ([PASTA_CONEXOES] if com_conexoes else [])
    for pasta in pastas:
        base = os.path.join(root, pasta)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in (NUNCA - {"tests"})]
            for f in filenames:
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                if _clone_ok(rel):
                    itens.append(rel)
    return itens


# ── arquivo unico auto-extraivel ──────────────────────────────
# Para canal que aceita SO um arquivo (e-mail, anexo de chamado). E um .py de
# texto, e nao um .exe nem um .zip: AppLocker bloqueia executavel desconhecido e
# o antivirus corporativo costuma remover .zip com codigo dentro. Texto passa.
#
# Nao confundir com o pacote portatil: aqui NAO vai Python nem dependencia - vai
# o codigo-fonte do framework. Quem extrair ainda precisa de Python e do
# install.bat. E CLONE, nao instalacao.
GABARITO_UNICO = '''#!/usr/bin/env python3
"""
ParraDibiei - arquivo unico auto-extraivel (clone do codigo-fonte).

    python {nome} [pasta-destino]

Recria a pasta do framework a partir deste proprio arquivo. Nao contem Python
nem bibliotecas: depois de extrair, rode `install.bat` (Windows) ou
`pip install -r requirements.txt`.

Gerado em {quando} - {n} arquivos, SHA-256 do conteudo conferido na extracao.
NAO contem evidencia de banco nenhum (casos coletados ficaram de fora).
"""
import base64
import hashlib
import io
import os
import sys
import zipfile

DESTINO_PADRAO = "{pasta}"
SHA256 = "{sha}"
DADOS = (
{dados}
)


def main(argv):
    destino = os.path.abspath(argv[0] if argv else DESTINO_PADRAO)
    bruto = base64.b64decode("".join(DADOS))

    # Confere antes de escrever: e-mail e chamado corrompem anexo com mais
    # frequencia do que se imagina, e um zip truncado extrai "quase tudo" em
    # silencio - metade de um framework parece um framework.
    achado = hashlib.sha256(bruto).hexdigest()
    if achado != SHA256:
        print("[ERRO] conteudo corrompido no transporte")
        print(f"       esperado {{SHA256}}")
        print(f"       achado   {{achado}}")
        return 2

    if os.path.exists(destino) and os.listdir(destino):
        print(f"[ERRO] a pasta ja existe e nao esta vazia: {{destino}}")
        print("       escolha outra:  python <este arquivo> C:\\\\caminho\\\\novo")
        return 3

    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        z.extractall(destino)
        n = len(z.namelist())

    print(f"[OK] {{n}} arquivos em {{destino}}")
    print("     proximo passo: install.bat  (ou pip install -r requirements.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def gerar_unico(root: str = ROOT, saida: str = "", com_conexoes: bool = False,
                distribuicao: bool = False) -> str:
    """Escreve um .py que carrega o projeto inteiro e se extrai sozinho.

    Dois modos, e a diferenca importa:

    - **clone** (padrao): fonte completa, com testes e ferramenta interna. Para
      levar o projeto a outra maquina e CONTINUAR o desenvolvimento. A suite tem
      de passar no destino - e o unico jeito de saber que o transporte deu certo.
    - **distribuicao**: mesmo criterio do pacote portatil (sem ferramenta
      interna) e **sem os testes**. Os testes ficam fora aqui porque parte deles
      testa justamente a ferramenta interna: manda-los sem ela produz 11 falhas
      e a conclusao errada de que o framework esta quebrado.

    `com_conexoes` nasce FALSO nos dois: arquivo unico circula por e-mail, e o
    inventario de servidores nao viaja por e-mail.
    """
    itens = (list(arquivos_do_projeto(root, com_conexoes)) if distribuicao
             else arquivos_do_clone(root, com_conexoes))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in sorted(set(itens)):
            z.write(os.path.join(root, rel), rel.replace("\\", "/"))
    bruto = buf.getvalue()

    b64 = base64.b64encode(bruto).decode("ascii")
    # Quebrado em linhas curtas por causa do transporte: cliente de e-mail
    # reescreve linha longa e ai o base64 chega quebrado.
    largura = 76
    linhas = "\n".join(f'    "{b64[i:i + largura]}"'
                       for i in range(0, len(b64), largura))

    nome = saida or os.path.join(root, "dist", "ParraDibiei_UNICO.py")
    os.makedirs(os.path.dirname(nome), exist_ok=True)
    with open(nome, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(GABARITO_UNICO.format(
            nome=os.path.basename(nome),
            quando=datetime.now().strftime("%Y-%m-%d %H:%M"),
            n=len(set(itens)),
            pasta="ParraDibiei",
            sha=hashlib.sha256(bruto).hexdigest(),
            dados=linhas))
    return nome


def validar(destino: str) -> bool:
    """Importa as dependencias com o Python do pacote. Sem isso o erro so
    apareceria na mao de quem recebeu a pasta."""
    print("[5/5] validando o pacote")
    py = os.path.join(destino, "python", "python.exe")
    r = subprocess.run(
        [py, "-c", "import oracledb, pptx, troubleshoot.painel as p; "
                   "print('imports OK', oracledb.__version__)"],
        cwd=destino, capture_output=True, text=True)
    print("   ", (r.stdout or r.stderr).strip()[:300])
    return r.returncode == 0


def tamanho(destino: str) -> float:
    t = sum(os.path.getsize(os.path.join(dp, f))
            for dp, _, fs in os.walk(destino) for f in fs)
    return t / (1024 * 1024)


def main() -> int:
    ap = argparse.ArgumentParser(description="Gera a versao portatil (sem .venv)")
    ap.add_argument("--destino", default=DESTINO_PADRAO)
    ap.add_argument("--embed", default="",
                    help="zip do Python embeddable ja baixado (modo offline)")
    ap.add_argument("--versao", default="",
                    help="versao do Python embeddable (padrao: a desta maquina)")
    ap.add_argument("--sem-conexoes", dest="sem_conexoes", action="store_true",
                    help="nao levar stringConexao\\ (hosts e usuarios de producao)")
    ap.add_argument("--origem", choices=("auto", "embeddable", "local"),
                    default="auto",
                    help="de onde vem o Python do pacote (padrao: auto - tenta "
                         "o embeddable e cai para a copia da instalacao local)")
    ap.add_argument("--migracao", action="store_true",
                    help="levar tambem o conhecimento local (ambientes.json, "
                         "troubleshooting\\, servidores.txt, marcas_locais.txt, "
                         "dba_master_prompt.txt). Para o SEU outro PC - o "
                         "pacote resultante NAO e distribuivel")
    ap.add_argument("--zipar", action="store_true",
                    help="gerar tambem o .zip para distribuir")
    ap.add_argument("--unico", action="store_true",
                    help="gerar SO um .py auto-extraivel com o codigo-fonte "
                         "(para e-mail/chamado; nao leva Python nem libs)")
    ap.add_argument("--com-conexoes", dest="com_conexoes", action="store_true",
                    help="com --unico: incluir stringConexao\\ (nao recomendado "
                         "para e-mail)")
    ap.add_argument("--distribuicao", action="store_true",
                    help="com --unico: versao para terceiro (sem ferramenta "
                         "interna e sem os testes)")
    args = ap.parse_args()

    if args.unico:
        alvo = gerar_unico(ROOT, com_conexoes=args.com_conexoes,
                           distribuicao=args.distribuicao)
        mb = os.path.getsize(alvo) / (1024 * 1024)
        print(f"[OK] {alvo}  ({mb:.1f} MB)")
        if args.distribuicao:
            print("     modo distribuicao: sem ferramenta interna e sem testes.")
        else:
            print("     clone completo: codigo + testes + configuracao.")
            print("     no destino, a suite tem de passar - e a prova do "
                  "transporte.")
        print(f"     quem receber roda:  python {os.path.basename(alvo)}")
        if not args.com_conexoes:
            print("     sem stringConexao\\ (use --com-conexoes para incluir)")
        return 0

    destino = montar(args.destino, args.embed, args.versao,
                     com_conexoes=not args.sem_conexoes, origem=args.origem,
                     migracao=args.migracao)
    ok = validar(destino)
    print(f"\n[{'OK' if ok else 'FALHOU'}] {destino}  ({tamanho(destino):.0f} MB)")
    if not ok:
        return 1
    if args.zipar:
        alvo = shutil.make_archive(destino, "zip", destino)
        print(f"[OK] zip: {alvo}  ({os.path.getsize(alvo)/(1024*1024):.0f} MB)")
    if args.migracao:
        # O aviso vai no artefato e na saida porque o risco nao e tecnico, e de
        # memoria: um zip de migracao e identico a um de distribuicao aos olhos
        # de quem o encontra numa pasta compartilhada tres semanas depois.
        n_casos = len(os.listdir(os.path.join(destino, "troubleshooting")))
        print(f"\n[!] PACOTE DE MIGRACAO - leva {n_casos} caso(s) e o "
              "inventario da frota.")
        print("    Contem host, IP e falha de PRODUCAO. NAO distribua, nao "
              "suba em SharePoint.")
        print("    Para circular, gere outro sem --migracao.")
    else:
        print("Distribua por SharePoint/unidade de rede. Rode INICIAR.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
