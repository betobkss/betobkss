# Playbook: Banco/instância lento

Metodologia **Method R** (Millsap): _Response Time = Service + Wait_. Sempre
comece medindo **onde o tempo está indo** antes de agir.

## 1. Perfil de tempo (onde está o gargalo)
- `ash_wait_classes` — distribuição por wait class (CPU vs User I/O vs Concurrency vs Commit...).
- `ash_top_events` — eventos dominantes.
- Interpretação:
  - **CPU alto** → SQL ineficiente / paralelismo / falta de bind → ver `top_sql_dbtime`.
  - **User I/O alto** → planos com full scan / storage lento → `io_filestat`, `sqlmon_top`.
  - **Concurrency (latches/buffer busy)** → hot blocks / contenção → `blocking_chain`.
  - **Commit (log file sync)** → redo/commit frequente / I/O de redo.
  - **gc \*** (RAC) → interconnect/global cache → `rac_gc`.

## 2. SQL ofensor
- `top_sql_dbtime` — ranquear SQL_IDs por DB time.
- `sqlmon_top` — plano + atividade do SQL mais custoso.
- Ações (sob Matriz de Risco): estatísticas (`stale_stats`), índice ausente,
  bind/cursor sharing, SQL Plan Baseline. **Nada destrutivo sem aprovação.**

## 3. Recursos (USE: Utilization/Saturation/Errors)
- `io_filestat` — latência de leitura por datafile (>20ms atenção, >40ms crítico).
- `sga_pga` — memória; `temp_usage` — sorts/hash grandes; `undo_usage`.
- `instance_health` — status/uptime/log mode.

## 4. Concorrência
- `blocking_chain` — cadeia de bloqueios; `top_active_sessions` — esperas longas.
- Blocker raiz com transação aberta → confirmar com a aplicação antes de qualquer
  `ALTER SYSTEM KILL SESSION` (destrutivo — exige aviso + aprovação).

## Coleta rápida
```powershell
python COLETAR.py <caso> --conn <servidor> --completo --open
```
O relatório já aplica o semáforo e prioriza por DB time.
