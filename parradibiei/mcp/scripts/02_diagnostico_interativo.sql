-- ############################################################
-- # 02 - DIAGNOSTICO INTERATIVO (READ-ONLY)
-- # Scripts com binds do SQL*Plus (&sql_id, &texto, &time).
-- # Complementa a coleta automatica (mcp/catalog.json).
-- ############################################################
SET LINESIZE 250 PAGESIZE 1000 TRIMSPOOL ON WRAP OFF FEEDBACK OFF

-- --- Top SQL por atividade (ASH, ultimos 5 min) [DIAGNOSTIC PACK] ---
PROMPT === TOP SQL POR ATIVIDADE (5 min) ===
SELECT * FROM (
  SELECT ash.sql_id,
         ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS activity_pct,
         ash.sql_opname AS tipo, s.username, SUBSTR(s.module,1,25) AS modulo, ash.event
  FROM gv$active_session_history ash
  JOIN gv$session s ON ash.session_id=s.sid AND ash.session_serial#=s.serial# AND ash.inst_id=s.inst_id
  WHERE ash.sql_id IS NOT NULL AND ash.sample_time > SYSDATE - 5/1440
  GROUP BY ash.sql_id, ash.sql_opname, s.username, s.module, ash.event
  ORDER BY 2 DESC
) WHERE ROWNUM <= 10;

-- --- Sessoes ativas por tempo de execucao atual (read-only) ---
PROMPT === SESSOES ATIVAS POR ELAPSED ===
SELECT * FROM (
  SELECT s.inst_id, s.sid, s.serial#, s.sql_id,
         ROUND((SYSDATE - s.sql_exec_start)*86400,1) AS elapsed_s,
         SUBSTR(q.sql_text,1,50) AS sql_50, s.event, s.machine
  FROM gv$session s JOIN gv$sql q
    ON s.sql_id=q.sql_id AND s.sql_child_number=q.child_number
  WHERE s.sql_id IS NOT NULL AND s.status='ACTIVE'
  ORDER BY elapsed_s DESC
) WHERE ROWNUM <= 10;

-- --- Sessoes longas com progresso (v$session_longops) ---
PROMPT === SESSOES LONGAS (PROGRESSO) ===
SELECT s.sid, s.serial#, s.username, s.sql_id,
       ROUND(op.elapsed_seconds/60,1) AS min_decorridos,
       ROUND(op.time_remaining/60,1)  AS min_restantes,
       ROUND(op.sofar*100/NULLIF(op.totalwork,0),1) AS pct
FROM v$session s JOIN v$session_longops op
  ON s.sid=op.sid AND s.serial#=op.serial#
WHERE op.totalwork > 0 AND op.sofar < op.totalwork
ORDER BY pct DESC;

-- --- Achar SQL_ID por trecho do texto ---
PROMPT === ACHAR SQL_ID POR TEXTO (informe &texto) ===
SELECT sql_id, ROUND(elapsed_time/1e6,2) AS elapsed_s, executions,
       TO_CHAR(last_active_time,'DD/MM HH24:MI') AS ult_exec, SUBSTR(sql_text,1,80) AS sql_80
FROM v$sql
WHERE sql_text LIKE '%&texto%' AND elapsed_time > 0
ORDER BY last_active_time DESC FETCH FIRST 15 ROWS ONLY;

-- --- Texto completo do SQL ---
PROMPT === SQL FULLTEXT (informe &sql_id) ===
SET LONG 100000
SELECT sql_fulltext FROM v$sql WHERE sql_id = '&sql_id' FETCH FIRST 1 ROWS ONLY;

-- --- Plano de execucao atual do cursor ---
PROMPT === PLANO DE EXECUCAO (informe &sql_id) ===
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('&sql_id', NULL, 'ALLSTATS LAST'));

-- --- Historico de execucao por snapshot AWR [DIAGNOSTIC PACK] ---
PROMPT === HISTORICO AWR POR SNAP (informe &sql_id e &time em minutos) ===
SELECT ss.snap_id, ss.instance_number AS node,
       TO_CHAR(sn.begin_interval_time,'DD/MM HH24:MI') AS inicio,
       ss.plan_hash_value AS plan,
       NVL(ss.executions_delta,0) AS execs,
       ROUND(ss.elapsed_time_delta/1e6,2) AS elapsed_s,
       ROUND(ss.cpu_time_delta/1e6,2)     AS cpu_s,
       ss.buffer_gets_delta AS lios, ss.disk_reads_delta AS pios
FROM dba_hist_sqlstat ss
JOIN dba_hist_snapshot sn
  ON ss.snap_id=sn.snap_id AND ss.instance_number=sn.instance_number
WHERE ss.sql_id='&sql_id'
  AND sn.begin_interval_time > SYSDATE - (&time/1440)
ORDER BY ss.snap_id, ss.instance_number;
