# Instalação e Portabilidade — Framework de Troubleshooting

Como levar o projeto para **outra máquina** (laptop de outro DBA, servidor de
jump, etc.) e fazer funcionar. O núcleo é **portável**: todos os caminhos são
relativos à raiz do projeto — não há paths fixos de usuário no código.

## Pré-requisitos

- **Python 3.11+** (funciona no 3.14). Verifique: `python --version`.
- Acesso de rede ao **listener Oracle** (porta 1529/1521) para a coleta *online*.
  Sem rede direta, use o modo **Citrix** (`queries/coletor.sql`).
- **Não precisa de Oracle Client** — o `oracledb` roda em modo *thin*.
- (Opcional) acesso ao PyPI ou a um índice interno (Artifactory) para instalar deps.

## Levar para outra máquina — 3 passos

```powershell
# 1) copie a pasta do projeto (SEM a subpasta .venv - veja abaixo)
# 2) instale o ambiente
.\install.bat
# 3) se este PC ja usou o antigo guardar_senha.bat, limpe o que ficou gravado
.\limpar_senha.bat
```

Não há senha a configurar: ela é **digitada a cada execução**.

Depois, confirme que está tudo pronto (não acessa banco nenhum):

```powershell
.\verificar.bat
```

> **Não copie a pasta `.venv`.** Um ambiente virtual guarda o caminho absoluto
> do Python de origem no `pyvenv.cfg`, então um `.venv` copiado **não funciona**
> em outra máquina. O `install.bat` detecta esse caso e recria o ambiente
> automaticamente; os atalhos `.bat` avisam com mensagem clara em vez de falhar
> de forma obscura. Copiar sem o `.venv` também reduz ~107 MB.
>
> Vale pular também `output/` e `troubleshooting/` (resultados, regeneráveis).

**Sem acesso ao PyPI?** O `install.bat` tenta o PyPI e, se o `pip` falhar
(proxy/rede corporativa), cai **automaticamente** nos wheels locais de
`wheelhouse/` — **se essa pasta existir**. Ela é gitignorada (wheels são
grandes e específicas da versão do Python), então num clone novo ela **não
vem junto**: gere antes, na máquina que tem rede (veja *Instalação OFFLINE*).
Para forçar: `.\setup.ps1 -Offline`.

O que **precisa** viajar junto: o código, `mcp/catalog.json`,
`stringConexao/` (hosts e usuários — **confira as senhas antes**, veja abaixo),
`requirements*.txt` e `wheelhouse/` (se a máquina destino não tiver rede).

## Alternativa: versão portátil (nada a instalar)

Se a máquina de destino **não tem Python** ou você não quer instalar nada, gere
o pacote autocontido na sua máquina e entregue a pasta pronta:

```powershell
.\.venv\Scripts\python.exe EMPACOTAR.py --zipar
```

Sai `dist\ParraTroubleshoot\` (~83 MB) com o Python isolado (`._pth`), as
dependências em `libs\` e o framework. Quem recebe roda `INICIAR.bat` (painel)
ou `TERMINAL.bat` (comandos completos, inclusive `--advisor`). Sem `.venv`, sem
admin e sem tocar no Python da máquina.

Detalhes que importam:

- O pacote **nunca leva evidência**: `troubleshooting\` e `output\` nascem
  vazios. Trate o que for coletado depois como dado de produção.
- `--sem-conexoes` deixa `stringConexao\` de fora (hosts e usuários de produção).
- Por padrão o script tenta o **Python embeddable** do python.org e, se o proxy
  bloquear o download (é o caso aqui: HTTP 403), cai automaticamente para copiar
  a **instalação local**. Para forçar: `--origem local` ou `--origem embeddable`.
  Com o zip já baixado à mão: `--embed C:\tmp\python-3.14.0-embed-amd64.zip`.
- Distribua por **SharePoint ou unidade de rede**, não por pendrive (DLP).

## Instalação rápida (Windows)

A forma mais fácil é rodar o `install.bat`, que executa o `setup.ps1` sem
precisar alterar a política de execução do PowerShell:

```powershell
.\install.bat
```

Se você já pode executar scripts `.ps1`, use diretamente:

```powershell
.\setup.ps1
```

O script detecta automaticamente o Python 3.11+, cria o `.venv`, instala as
dependências e roda os testes. Variações:

```powershell
.\setup.ps1 -Full     # instala o pacote em modo editavel (pip install -e .)
.\setup.ps1 -Offline  # somente wheelhouse/ local (sem rede; forneca a pasta)
.\setup.ps1 -Help     # mostra as opcoes
```

### Manual (qualquer SO)

```bash
# 1) criar o ambiente virtual
python -m venv .venv

# 2) ativar
#   Windows:  .\.venv\Scripts\Activate.ps1
#   Linux/mac: source .venv/bin/activate

# 3) instalar
#   minimo do framework (COLETAR/TROUBLESHOOT):
python -m pip install -r requirements-troubleshoot.txt
#   OU projeto completo:
#   python -m pip install -r requirements.txt
#   OU modo editavel:
#   python -m pip install -e .

# 4) validar
python -m pytest -q
```

## Instalação OFFLINE (máquina sem internet)

O modo offline instala a partir de wheels locais em `wheelhouse/`. **Essa pasta
não é versionada** (está no `.gitignore`: são dezenas de MB e as wheels são
amarradas à versão do Python). Ou seja: num clone novo ela não existe e o
`-Offline` falha até você gerá-la.

**Passo 1 — numa máquina COM rede**, baixe as wheels:

```powershell
pip download -d wheelhouse oracledb python-pptx pytest dukpy
```

**Passo 2** — leve a pasta `wheelhouse/` junto com o projeto e rode:

```powershell
.\setup.ps1 -Offline
```

O modo offline instala também o `pytest` a partir do `wheelhouse/` e roda a
suíte de testes: instalação sem rede também se autovalida.

### Por que o `dukpy` está nessa lista

Ele é um motor de JavaScript usado **só em teste**: com ele, a suíte *executa*
as funções de renderização do painel e confere o HTML que sai. Sem ele, a
verificação da tela volta a ser "a string existe no arquivo" — que aprova mesmo
com a tela quebrada. Foi assim que passou desapercebido um separador escapado em
dobro, aprovado por todas as asserções de string.

Ele **não** é necessário para coletar, analisar ou gerar relatório, e a
instalação nunca falha por causa dele: se o wheel não estiver disponível para a
combinação de plataforma e versão do Python, o `setup.ps1` avisa e os testes de
renderização são **pulados** — o resto da suíte roda normalmente. A consequência
de omiti-lo é que aquela instalação deixa de verificar a tela.

Atenção: as wheels precisam ser da **mesma versão do Python** da máquina de
destino (e da mesma plataforma). Wheels baixadas num Python 3.14 não servem
num 3.11. Na dúvida, gere no destino ou numa máquina igual.

## Configurar as conexões

O repositório de conexões fica em `stringConexao/Parra.json` (export do SQL
Developer). Leve esse arquivo junto ou gere um novo:
```powershell
.\.venv\Scripts\python.exe COLETAR.py --add --conn NOME --host H --port 1529 --service SVC --user dbaread
.\.venv\Scripts\python.exe COLETAR.py --list-conns
```
**O framework nunca grava senha**: ela é pedida no prompt a cada execução e não
vai para disco nem para `argv`.

> ⚠ **O arquivo exportado pelo SQL Developer é outra história.** Toda conexão
> salva com *Save Password* marcada carrega um campo `password` no JSON. A
> criptografia é do SQL Developer, o algoritmo é público e o `ExportKeyChecksum`
> viaja no mesmo arquivo — trate como **recuperável**. Confira antes de copiar
> para SharePoint, pendrive ou outra máquina:
>
> ```powershell
> Select-String -Path stringConexao\*.json -Pattern '"password"' -AllMatches |
>   ForEach-Object { $_.Filename + ": " + $_.Matches.Count + " senha(s)" }
> ```
>
> Para gerar um export limpo, desmarque *Save Password* nas conexões antes de
> exportar, ou recrie o repositório com `COLETAR.py --add` (que nunca escreve
> senha). Por isso `stringConexao/*.json` está no `.gitignore`: só o
> `EXEMPLO.json.modelo` é versionado.

## Usar

### Aliases rápidos (terminal)

Após a instalação, use os `.bat` na raiz do projeto (rode da pasta do projeto):

```powershell
cd "C:\projetos\ParraDibiei - checklist"   # ajuste para o seu caminho
.\painel.bat                                        # interface local (127.0.0.1)
.\coletar.bat INC123 --conn DBSRV600 --completo --open
.\healthcheck.bat DBSRV600
.\redija.bat INC123
.\coletar.bat --list-conns
```

- `painel.bat` → sobe o painel (`PAINEL.py`) e abre o navegador.
- `coletar.bat` → chama `COLETAR.py` com qualquer argumento.
- `healthcheck.bat <servidor> [flags]` → `COLETAR.py HC_<SERVIDOR> --conn <SERVIDOR> --completo`.
- `redija.bat <caso>` → `TROUBLESHOOT.py <caso> --completo`.
- `verificar.bat` → checa a máquina **sem tocar em banco**. É por onde começar.
- `limpar_senha.bat` → apaga o que o antigo `guardar_senha.bat` gravou no registro.

O resto dos comandos é `python.exe <SCRIPT>.py`: `GUIA.py` (conduzir passo a
passo), `CASO.py` (ciclo de vida), `DETALHAR_SQL.py` e `JANELA.py`
(drill-down), `MELHORIA.py` (rotina offline), `APRESENTAR.py` (decks),
`VALIDAR_KB.py`, `EMPACOTAR.py` e `BLUEPRINT.py`.

### Desempenho

A coleta roda **em paralelo** (várias sessões read-only). Ordem de grandeza em
banco real: **poucos segundos** no modo completo e menos ainda no rápido;
relatório em ~1s. O catálogo cresce a cada release e o tempo sobe pouco (os
checks vão em paralelo), mas **não cite número de cabeça**: o log final imprime
`[tempo] coleta em Xs | mais lentos: ...`, e a medição travada com data fica em
`output/apresentacao/medicao.json` (gerada por `APRESENTAR.py --medir`, offline).
O tamanho atual do catálogo está na tabela *O tamanho de cada peça* do
[`README.md`](README.md), que é contada do repositório — número escrito à mão
envelhece calado.

```powershell
.\healthcheck.bat DBSRV600 --rapido        # pula historico AWR (DBA_HIST_*) e ADDM
.\coletar.bat INC123 --conn DBSRV600 --workers 8   # mais paralelismo
.\coletar.bat INC123 --conn DBSRV600 --timeout 60  # corta query travada em 60s
```

O log final mostra onde foi o tempo:
`[tempo] coleta em 5.6s | mais lentos: workload_trend.txt 3.9s, ...`

### Comandos naturais no chat

Com a regra `.devin/rules/troubleshoot.md` ativa, você pode falar no chat:

- "rodar healthcheck para servidor dbsrv600"  
  → executa `COLETAR.py HC_DBSRV600 --conn DBSRV600 --completo` e resume o resultado.
- "coletar INC123 em DBSRV600"  
  → executa `COLETAR.py INC123 --conn DBSRV600 --completo`.
- "redija INC123"  
  → executa `TROUBLESHOOT.py INC123 --completo` e resume o relatório.
- "detalhar INC123"  
  → analisa o relatório já gerado em `output/troubleshooting/INC123/`.

### Senha

A senha é **sempre digitada na hora**, a cada conexão com o banco — no terminal
sem eco, ou numa caixa mascarada no painel. Não existe `--password`, arquivo nem
variável de ambiente que a substitua; ela morre com o processo.

Na bateria interna (`TESTE_HOMOLOG.py`, que não acompanha o pacote) é pedida
**uma vez** e vale para todos os servidores da lista.

> **Se você já usou o `guardar_senha.bat`** (removido em 02/08/2026), sua senha
> está em **texto puro no registro do Windows**, em
> `HKCU\Environment\ORA_PASSWORD` — permanente e legível por qualquer processo
> seu. Rode `.\limpar_senha.bat` e troque a senha. O `.\verificar.bat` acusa
> isso como **[ERRO]** enquanto não for limpo.

> Referência completa de comandos (chat e prompt): **[`COMANDOS.md`](COMANDOS.md)**.

### Comandos clássicos

```powershell
# Coleta read-only + relatorio completo (online, direto)
.\.venv\Scripts\python.exe COLETAR.py INC123 --conn DBSRV600 --completo --open

# Só gerar relatorio de evidencias ja coletadas
.\.venv\Scripts\python.exe TROUBLESHOOT.py INC123 --completo

# Explorar o catalogo / scripts / gerar o coletor Citrix
.\.venv\Scripts\python.exe -m mcp.loader --list
.\.venv\Scripts\python.exe -m mcp.loader --scripts
.\.venv\Scripts\python.exe -m mcp.loader --gen-sql queries\coletor.sql
```

### Conduzir o caso e colar a saída de volta

Depois da coleta, o trabalho é conduzir — e o que o **terminal devolve** vira
evidência do caso:

```powershell
.\.venv\Scripts\python.exe GUIA.py INC123                       # onde parei + o que a evidencia sugere
.\.venv\Scripts\python.exe GUIA.py INC123 --iniciar bloqueio_lock
.\.venv\Scripts\python.exe GUIA.py INC123 --saida saida.txt      # le o retorno do comando
type saida.txt | .\.venv\Scripts\python.exe GUIA.py INC123 --saida -
```

O passo mostra a **matriz de risco antes do comando** (impacto, o que validar,
rollback, se exige aprovação). A saída colada é interpretada por leitores
determinísticos e o motor **repontua as hipóteses**, dizendo o que mudou —
inclusive quando a resposta é vazia (`no rows selected` na verificação de
bloqueio **descarta** a hipótese). O que não for reconhecido **não vira achado**:
a tela diz que não entendeu em vez de inventar leitura. Nada é executado por
aqui; o comando continua rodando no seu terminal.

No painel é a aba **Guiado**: caixa "cole aqui a saída", `Ctrl+Enter` para
interpretar, e um `<details>` listando **tudo o que o motor sabe ler**.

### Rotinas de manutenção (offline, sem banco)

```powershell
.\.venv\Scripts\python.exe MELHORIA.py --tudo      # corpus de regressao + higiene + radar de fontes
.\.venv\Scripts\python.exe VALIDAR_KB.py           # valida os playbooks da kb/
.\.venv\Scripts\python.exe APRESENTAR.py --vendas  # deck de 3 slides (tempo ganho + vantagens)
.\.venv\Scripts\python.exe APRESENTAR.py --medir   # cronometra o pipeline e TRAVA a medicao
```

Nenhuma delas conecta em banco: o corpus reanalisa **evidência já gravada** e
compara com o baseline, então mudança de código que altere diagnóstico aparece
antes de ir para o campo.

Para o deck usar o **logo em imagem** (em vez do desenho vetorial), copie os
arquivos para `assets/` — nomes aceitos e proporções em
[`assets/LEIA-ME.md`](assets/LEIA-ME.md).

## O que é portável e o que é por-máquina

| Item | Portável? | Observação |
|------|-----------|------------|
| `troubleshoot/`, `mcp/`, `COLETAR.py`, `TROUBLESHOOT.py`, `queries/` | **Sim** | Caminhos relativos (`BASE_DIR`). Funciona em qualquer pasta/SO. |
| `stringConexao/Parra.json` | **Conferir** | Pode conter senhas do export do SQL Developer (`"password"`). Verifique antes de copiar, ou recrie com `--add`. |
| `output/`, `troubleshooting/<caso>/`, `.venv/`, logs | Recriados | Estão no `.gitignore`; não precisa levar. |
| `.devin/rules` e `.devin/workflows` (persona DBA + `/parra`, `/redija-completo`) | **Sim** | Nao tem mais caminho absoluto dentro: leem `caminhos_locais.txt`. |
| `caminhos_locais.txt`, `servidores.txt`, `marcas_locais.txt`, `ambientes.json` | **Recriar** | Sao locais (nao versionados). Copie o `.modelo` de cada um e preencha. O `ambientes.json` se regenera sozinho com `AMBIENTES.py --atualizar`. |
| `assets/*.png` (logo, banner do deck) | **Levar se tiver** | Imagem de marca nao e versionada (licenca propria). Sem ela o deck desenha a marca em vetor e sai igual. |
| `dba_master_prompt.txt` | **Recriar** | Fica na **raiz do projeto** (desde 09/2026; antes vivia fora dele, sem backup). Nao e versionado: `copy dba_master_prompt.txt.modelo dba_master_prompt.txt`. |

### Integração com Windsurf/Devin na nova máquina (opcional)
As regras e workflows não contêm mais caminho absoluto: eles consultam
`caminhos_locais.txt`. Na máquina nova basta:

```powershell
copy caminhos_locais.txt.modelo caminhos_locais.txt
```

e preencher as chaves. (O framework em si — COLETAR/TROUBLESHOOT — funciona
sem isso.)

## MCP server (opcional — hoje DESATIVADO)

Expõe o catálogo a um cliente MCP (Claude Desktop, Cline, Copilot). **Não está
registrado**: o fluxo normal (COLETAR/TROUBLESHOOT + regra do chat) não usa o
servidor, e cada cliente aberto deixava um processo Python segurando a pasta do
projeto. O código e os testes seguem no repositório.

Guarda de licença: o `run_check` **recusa por padrão** qualquer check com
`requires_pack` (Diagnostics/Tuning). Quem chama o MCP é um assistente, que não
sabe o que aquele banco licencia — e consumo indevido de pack é cobrança e
achado de auditoria. Para executar mesmo assim, o cliente precisa enviar
`allow_pack: true` explicitamente.

```json
{ "mcpServers": { "oracle-troubleshoot": {
    "command": "C:/caminho/do/projeto/.venv/Scripts/python.exe",
    "args": ["-m", "mcp.server"] } } }
```

## Segurança / compliance

- Coleta **somente leitura** (SELECT/dicionário) + sessão `READ ONLY`.
- Respeita licença Oracle: `--pack auto|T|D|N` (evita consumir Diagnostics/Tuning Pack).
- Use um usuário de leitura (`dbaread` / `SELECT_CATALOG_ROLE`).
- Nunca versione senha — e não há onde guardá-la: o framework a pede a cada
  execução. O export do SQL Developer é cifrado e não serve como fonte.
