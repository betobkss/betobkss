# Biblioteca de Scripts DBA (MCP)

Coleção curada de scripts Oracle de uso diário, **melhorados e padronizados**, para
serem usados quando oportuno durante diagnóstico e manutenção. Complementa o
catálogo automático (`mcp/catalog.json`): o catálogo roda a coleta read-only sem
intervenção; esta biblioteca reúne scripts **interativos** (com binds `&sql_id`,
`&time`, etc.), relatórios do Oracle e **ações de manutenção**.

## Organização e classificação de risco

| Arquivo | Categoria | Read-only | Observação |
|---------|-----------|-----------|------------|
| `01_checklist_saude.sql`        | diagnóstico | SIM | Rodar no `CDB$ROOT`. |
| `02_sql_performance.sql`        | diagnóstico | SIM | Top SQL por CPU/atividade, sessões longas. |
| `03_awr_e_planos.sql`           | diagnóstico | SIM | Histórico AWR e planos (`DBMS_XPLAN`). |
| `04_espaco.sql`                 | diagnóstico | SIM | Tablespaces/ASM/UNDO/TEMP + **geradores** de comando. |
| `05_sessoes_locks.sql`          | diagnóstico | SIM | Sessões ativas/inativas e bloqueios. |
| `10_acoes_sql_tuning.sql`       | **ação**    | **NÃO** | Tuning Advisor, SQL Profile, `GATHER_STATS`. |
| `11_acoes_espaco_manutencao.sql`| **ação**    | **NÃO** | Resize/add/move datafile, TEMP/UNDO, purge, expurgo. |

## REGRA DE SEGURANÇA (Matriz de Risco DBA Master)

- Arquivos `01`–`05` são **somente leitura** (diagnóstico) — seguros.
- Arquivos `10`–`11` contêm **ações destrutivas/mutantes** (DDL/DML/`DBMS_*`).
  **NUNCA** execute sem: (1) entender o estado atual, (2) **aviso** do impacto,
  (3) **plano de rollback**, (4) **aprovação** explícita. Cada comando destrutivo
  está comentado com `-- [ACAO]` e um aviso.
- Preferir sempre observar (read-only) antes de agir.

## Descoberta programática

```powershell
python -m mcp.loader --scripts              # lista todos os scripts
python -m mcp.loader --scripts --cat espaco # filtra por categoria
```

O índice com metadados fica em `scripts_index.json` (id, categoria, read_only,
destructive, binds exigidos, onde rodar e finalidade).

## Convenções

- Binds do SQL*Plus: `&sql_id`, `&time` (minutos), `&TABLESPACE_NAME`, etc.
- Muitos scripts pedem os valores na execução (`ACCEPT`/`&`).
- Formatação (`SET LINESIZE/PAGESIZE`) já embutida por bloco.
