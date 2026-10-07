# DBA Master (Parra) no VS Code + GitHub Copilot

Este pacote leva **a persona e as regras** do DBA Master para o VS Code com
GitHub Copilot. Ele **não** contém o framework de coleta — os scripts Python
(`COLETAR.py`, `CASO.py`, `AMBIENTES.py`…) continuam vindo do projeto original e
já funcionam em qualquer IDE, porque não dependem de nenhuma.

Em resumo: o framework sempre foi portátil; o que estava preso ao Devin eram as
instruções. É isso que este pacote resolve.

---

## O que vem aqui

```
ParraDibiei-Copilot/
├── LEIAME.md                          este arquivo
├── .github/
│   └── copilot-instructions.md        a persona + os inegociáveis (injetado a cada pergunta)
├── .vscode/
│   └── settings.json                  habilita o arquivo de instruções
└── referencia/
    ├── dba_master_prompt.txt          comportamento completo
    ├── COMANDOS.md                    todos os comandos do framework
    └── AGENTS.md                      notas operacionais do parque
```

O `copilot-instructions.md` é **autocontido**: mesmo que o Copilot não abra
nenhum arquivo de `referencia/`, o comportamento essencial já está nele —
read-only antes de agir, criticidade primeiro, topologia primeiro, verificação
de espaço, formato do `redija` e os scripts que não podem ser executados.

---

## Instalação

### Cenário A — você já tem o projeto do framework

Copie as três pastas para a **raiz** do projeto existente:

```powershell
Copy-Item -Recurse .\ParraDibiei-Copilot\.github     C:\caminho\do\projeto\ -Force
Copy-Item -Recurse .\ParraDibiei-Copilot\.vscode     C:\caminho\do\projeto\ -Force
Copy-Item -Recurse .\ParraDibiei-Copilot\referencia  C:\caminho\do\projeto\ -Force
```

> Se o projeto já tem `.github/workflows/`, a cópia **não** apaga nada: só
> acrescenta o `copilot-instructions.md` ao lado. Se já existir um
> `.vscode/settings.json`, abra os dois e junte as chaves à mão, em vez de
> sobrescrever.

O `referencia/` é opcional nesse cenário — o projeto já tem `AGENTS.md`,
`COMANDOS.md` e o `dba_master_prompt.txt`. Nesse caso, edite o
`copilot-instructions.md` e troque `referencia/X` por `X` nos três caminhos da
tabela do topo.

### Cenário B — você quer só a persona, sem o framework

Copie a pasta inteira para a raiz do repositório onde vai trabalhar. Funciona
para revisar procedimento, redigir chamado e conduzir raciocínio; os comandos
descritos em `COMANDOS.md` só rodam onde o framework estiver instalado.

---

## Ligar no VS Code

**1. Requisitos:** VS Code atualizado, extensões **GitHub Copilot** e **GitHub
Copilot Chat**, e uma licença ativa.

**2. Confirme a configuração.** O `.vscode/settings.json` do pacote já traz a
chave que importa. Se preferir ativar manualmente: `Ctrl+,`, procure por
`useInstructionFiles` e marque

```
GitHub › Copilot › Chat › Code Generation: Use Instruction Files
```

**3. Recarregue a janela:** `Ctrl+Shift+P` → *Developer: Reload Window*.

---

## Como saber se pegou

Abra o Copilot Chat e pergunte algo do domínio, por exemplo:

> como investigo um gap de Data Guard?

Duas evidências de que funcionou:

- **Nas "References"/"Used N references"** da resposta aparece
  `.github/copilot-instructions.md`.
- A resposta **começa perguntando a criticidade e a topologia** em vez de já
  despejar comando. Esse é o comportamento característico da persona — se ele
  sair mandando `ALTER SYSTEM` de cara, o arquivo não foi carregado.

Um teste mais direto:

> qual o formato do redija?

Ele deve devolver exatamente os seis campos, sem RCA e sem horários.

---

## Também vale para o Copilot coding agent

O mesmo `.github/copilot-instructions.md` é lido pelo **Copilot coding agent**
no github.com quando você atribui uma issue a ele. Nada a configurar além de
versionar o arquivo no repositório.

---

## Diferenças em relação ao Devin

| | Devin | Copilot |
|---|---|---|
| Instruções sempre ativas | `.devin/rules/*.md` | `.github/copilot-instructions.md` |
| Comandos em linguagem natural | *skill* `framework-troubleshoot` | descritos em `referencia/COMANDOS.md` |
| Workflows (`/redija-completo`) | sim | não há equivalente — rode o comando à mão |
| Executar script no terminal | direto | pelo modo *Agent*, com aprovação |

A perda real é pequena: a *skill* era, na prática, um mapa de comandos, e esse
mapa é o `COMANDOS.md`, que qualquer agente consegue ler.

---

## Outras ferramentas

O mesmo `copilot-instructions.md` serve, renomeado, para:

| Ferramenta | Caminho do arquivo |
|---|---|
| Cursor | `.cursor/rules/dba-master.mdc` (acrescente no topo: `---`, `alwaysApply: true`, `---`) |
| Windsurf | `.windsurf/rules/dba-master.md` |
| Claude Code | `CLAUDE.md` na raiz |
| Codex, Jules, Zed, Aider | já leem `AGENTS.md` — basta copiar `referencia/AGENTS.md` para a raiz |

---

## Manutenção

Este pacote é uma **cópia datada**. Ele não se atualiza sozinho quando o projeto
original muda.

Se as regras evoluírem lá, a forma honesta de manter isto vivo é regerar o
pacote em vez de editar os dois lados — arquivo duplicado que ninguém sincroniza
vira, em poucos meses, duas versões que se contradizem e ninguém sabe qual vale.

Gerado a partir de `AGENTS.md`, `COMANDOS.md` e `dba_master_prompt.txt.modelo`
do projeto ParraDibiei.
