# MCP — Repositório Híbrido de Diagnóstico e Tuning Oracle

Catálogo **estilo MCP** (Model Context Protocol) de _checks_ **READ-ONLY** de
diagnóstico e tuning Oracle. É a **fonte única de verdade** das consultas,
thresholds e recomendações — consumida pelos fluxos **offline** (SQL*Plus/Citrix)
e **online** (`oracledb` direto), garantindo que ambos rodem exatamente o mesmo
conjunto de análises.

## Por que "estilo MCP"

Cada _check_ é uma "ferramenta" declarativa com metadados: `id`, `category`,
`purpose`, `read_only`, `min_privs`, `thresholds`, `references` e o `sql`. Um
agente (ou o nosso coletor) descobre e executa essas ferramentas de forma
uniforme — offline ou online — sem lógica espalhada pelo código.

## Estrutura

```
mcp/
├── catalog.json     # fonte unica: todos os checks read-only por dominio
├── loader.py        # API + CLI: listar/filtrar e gerar o coletor.sql offline
├── playbooks/       # procedimentos por cenario (Method R / MAA)
└── README.md
```

## Fluxo híbrido

```
                       ┌───────────────────┐
                       │   mcp/catalog.json │  (fonte unica)
                       └─────────┬─────────┘
              online │                        │ offline
        ┌────────────▼───────────┐   ┌────────▼─────────────────┐
        │ COLETAR.py (oracledb)  │   │ python -m mcp.loader      │
        │ sessao READ ONLY       │   │   --gen-sql queries/...    │
        │ roda os checks online  │   │ -> coletor.sql (Citrix)    │
        └────────────┬───────────┘   └────────┬─────────────────┘
                     │                          │ (SQL*Plus @coletor.sql)
                     │                          ▼
                     │                 coletor_troubleshoot.txt
                     ▼                          │
             troubleshooting/<caso>/  ◄─────────┘  (copiar p/ a pasta do caso)
                     │
                     ▼
             TROUBLESHOOT.py --completo  → dashboard + PPTX + relatorio padrao
```

## Uso

```powershell
# Listar / explorar o catalogo
python -m mcp.loader --categories
python -m mcp.loader --list
python -m mcp.loader --list --cat waits
python -m mcp.loader --show ash_wait_classes

# OFFLINE (Citrix): regerar o coletor.sql a partir do catalogo
python -m mcp.loader --gen-sql queries/coletor.sql
#   -> no SQL*Plus: @coletor.sql  -> copiar coletor_troubleshoot.txt p/ o caso

# ONLINE (direto): coletar tudo read-only e gerar o relatorio
python COLETAR.py <caso> --conn <nome_no_Parra.json> --completo --open
```

## Domínios cobertos (categorias)

- **waits** — perfil de esperas/ASH, latência de I/O (Method R, USE).
- **sql** — Top SQL por DB time, SQL Monitor, ADDM.
- **space** — tablespaces, ASM, UNDO, TEMP, FRA.
- **ha_dg** — Data Guard lag e managed standby (MAA).
- **backup** — RMAN jobs (RPO).
- **rac** — Global Cache / interconnect.
- **sessions** — bloqueios e sessões ativas.
- **stats** — estatísticas STALE do otimizador.
- **config** — saúde da instância, memória, alert log.
- **security** — objetos inválidos.

## Princípios

- **Somente leitura**: todo `sql` é consulta de dicionário/`v$`. A sessão é aberta
  como `READ ONLY`. Nenhum `DML/DDL/PLSQL` destrutivo.
- **Privilégio mínimo**: usuário de leitura (`SELECT_CATALOG_ROLE`), ex.: `dbaread`.
- **Metodologia**: priorização por _DB time_ e _wait class_ (Millsap), saturação de
  recursos (USE) e boas práticas Oracle MAA/Tuning Guide (ver `references`).
- **Sem drift**: editar o `catalog.json` e **regerar** o `coletor.sql`; não editar
  o SQL em dois lugares.

## Como adicionar um novo check

1. Acrescente um objeto em `catalog.json` → `checks[]` com `id`, `category`,
   `title`, `purpose`, `output_file`, `analyzer`, `subtitle`, `sql`, `thresholds`,
   `references` e `optional` (se puder falhar em bancos sem o recurso).
2. Regere o offline: `python -m mcp.loader --gen-sql queries/coletor.sql`.
3. (Online) O `COLETAR.py` pode consumir `mcp.online_items()` para rodar o mesmo
   conjunto — mantendo os dois fluxos sincronizados.
