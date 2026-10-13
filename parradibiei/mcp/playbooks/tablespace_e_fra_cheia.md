# Playbook: Tablespace / FRA cheia (espaço)

Sintomas: `ORA-01653/01688` (não estende segmento), `ORA-19809/00257` (FRA cheia,
archiver travado → banco pode "congelar").

## Diagnóstico (read-only)
- `tablespaces` — % de uso por tablespace (>80% atenção, >90% crítico).
- `asm_diskgroups` — espaço nos diskgroups ASM.
- `fra_usage` — ocupação e % reclaimable da Fast Recovery Area.
- `rman_jobs` — se a FRA está cheia de archivelogs, checar sucesso/recência do backup.
- `alertlog_errors` — confirmar ORA-257/19809/1653.

## Interpretação
- Tablespace >90% com `autoextend off` ou `maxsize` atingido → risco imediato.
- **FRA cheia** com `pct_reclaimable` alto → backups de archivelog não estão
  liberando (RMAN falhando) → risco de **archiver travado** (banco para).
- ASM diskgroup >90% → afeta todos os objetos do DG.

## Ações (sob Matriz de Risco)
- Tablespace: adicionar datafile / aumentar `autoextend`/`maxsize`; ou liberar
  espaço (mover/expurgar dados — destrutivo, exige aprovação).
- FRA: rodar backup de archivelog (`DELETE INPUT`) para reciclar; aumentar
  `DB_RECOVERY_FILE_DEST_SIZE`; nunca apagar archivelog manualmente sem RMAN.
- ASM: adicionar discos ao diskgroup.

## Coleta
```powershell
python COLETAR.py <caso> --conn <servidor> --completo --open
```
> A coleta inclui `space`, `asm`, `fra` e `rman` — o relatório sinaliza o
> crítico e recomenda a ação por objeto.
