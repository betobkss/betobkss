-- ============================================================
-- coletor.sql - GERADO A PARTIR DE mcp/catalog.json (NAO EDITAR A MAO)
-- Regerar: python -m mcp.loader --gen-sql queries/coletor.sql
-- Uso no Citrix: @coletor.sql  ->  copie coletor_troubleshoot.txt
--   para troubleshooting\<caso>\ e rode: .\redija.bat <caso>
-- SOMENTE LEITURA: sessao aberta como READ ONLY.
-- LICENCA: blocos marcados 'requer DIAGNOSTIC/TUNING PACK' consomem
--   Oracle Diagnostics/Tuning Pack (ASH/AWR/ADDM/SQL Monitor). Se o
--   banco NAO for licenciado, remova esses blocos antes de executar.
-- ============================================================
SET LINESIZE 240 PAGESIZE 5000 TRIMSPOOL ON TRIMOUT ON
SET FEEDBACK OFF ECHO OFF VERIFY OFF HEADING ON TAB OFF LONG 2000000 LONGCHUNKSIZE 2000000
ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS';
SET TRANSACTION READ ONLY;

SPOOL coletor_troubleshoot.txt

PROMPT @@@FILE:ash.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === TOP WAIT CLASSES (ASH 60min) ===
SELECT NVL(wait_class,'CPU') AS wait_class, COUNT(*) AS samples FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 GROUP BY NVL(wait_class,'CPU') ORDER BY samples DESC;
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === TOP EVENTS (ASH 60min) ===
SELECT * FROM (SELECT NVL(event,'ON CPU') AS event, COUNT(*) AS samples FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 GROUP BY NVL(event,'ON CPU') ORDER BY samples DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:topsql.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === TOP SQL POR DB TIME (ASH 2h) ===
SELECT * FROM (SELECT sql_id, COUNT(*) AS db_time_samples, ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS pct FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/12 AND sql_id IS NOT NULL GROUP BY sql_id ORDER BY db_time_samples DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:sqlmon.txt@@@
-- ATENCAO: requer TUNING PACK (licenca Oracle) - remova se nao licenciado
SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(type => 'TEXT', report_level => 'ALL') FROM dual;

PROMPT @@@FILE:addm.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
SELECT DBMS_ADVISOR.GET_TASK_REPORT(task_name) AS report FROM (SELECT task_name FROM dba_advisor_tasks WHERE advisor_name = 'ADDM' ORDER BY created DESC) WHERE ROWNUM = 1;

PROMPT @@@FILE:locks.txt@@@
PROMPT === ARVORE DE BLOQUEIO (AGORA) ===
SELECT t.arvore, t.nivel, t.sid, t.serial, t.username, t.status, t.blocker, t.event, t.secs, t.idle_min, NVL2(o.object_id, o.owner||'.'||o.object_name, '-') AS objeto, t.sql_id FROM (SELECT LPAD(' ',2*(LEVEL-1))||s.inst_id||':'||s.sid AS arvore, LEVEL AS nivel, s.sid, s.serial, s.username, s.status, s.blocking_session AS blocker, s.event, s.seconds_in_wait AS secs, ROUND(s.last_call_et/60,1) AS idle_min, s.objid, s.sql_id, ROWNUM AS ord FROM (SELECT inst_id, sid, serial# AS serial, username, status, blocking_session, blocking_instance, event, wait_class, seconds_in_wait, last_call_et, row_wait_obj# AS objid, sql_id FROM gv$session WHERE type = 'USER') s START WITH s.blocking_session IS NULL AND (s.inst_id, s.sid) IN (SELECT NVL(blocking_instance, inst_id), blocking_session FROM gv$session WHERE blocking_session IS NOT NULL AND type = 'USER' AND wait_class <> 'Idle') CONNECT BY NOCYCLE PRIOR s.sid = s.blocking_session AND PRIOR s.inst_id = NVL(s.blocking_instance, s.inst_id) AND s.wait_class <> 'Idle' ORDER SIBLINGS BY s.seconds_in_wait DESC) t LEFT JOIN dba_objects o ON o.object_id = t.objid AND t.objid > 0 ORDER BY t.ord;

PROMPT @@@FILE:locks_hist.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === BLOQUEIOS POR HORA (ASH, 7d) ===
SELECT * FROM (SELECT TO_CHAR(TRUNC(h.sample_time,'HH'),'YYYY-MM-DD HH24') AS janela, h.event, NVL2(o.object_id, o.owner||'.'||o.object_name, '-') AS objeto, COUNT(*)*10 AS segs_bloqueado, COUNT(DISTINCT h.session_id) AS vitimas, COUNT(DISTINCT h.blocking_session) AS bloqueadores FROM dba_hist_active_sess_history h LEFT JOIN dba_objects o ON o.object_id = h.current_obj# AND h.current_obj# > 0 WHERE h.sample_time > SYSDATE - 7 AND h.blocking_session IS NOT NULL AND h.wait_class IN ('Application','Concurrency') GROUP BY TO_CHAR(TRUNC(h.sample_time,'HH'),'YYYY-MM-DD HH24'), h.event, NVL2(o.object_id, o.owner||'.'||o.object_name, '-') ORDER BY 4 DESC) WHERE ROWNUM <= 25;
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === TOP BLOQUEADORES POR MODULO (ASH, 7d) ===
SELECT * FROM (SELECT NVL(b.module,'(sem modulo)') AS modulo, NVL(SUBSTR(b.program,1,30),'-') AS programa, NVL(b.sql_id,'-') AS sql_id_bloqueador, COUNT(*)*10 AS segs_causados, COUNT(DISTINCT w.session_id) AS vitimas FROM dba_hist_active_sess_history w JOIN dba_hist_active_sess_history b ON b.dbid = w.dbid AND b.snap_id = w.snap_id AND b.sample_id = w.sample_id AND b.session_id = w.blocking_session AND b.instance_number = NVL(w.blocking_inst_id, w.instance_number) WHERE w.sample_time > SYSDATE - 7 AND b.sample_time > SYSDATE - 7 AND w.blocking_session IS NOT NULL AND w.wait_class IN ('Application','Concurrency') GROUP BY NVL(b.module,'(sem modulo)'), NVL(SUBSTR(b.program,1,30),'-'), NVL(b.sql_id,'-') ORDER BY 4 DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:sessions.txt@@@
PROMPT === TOP SESSOES ATIVAS ===
SELECT * FROM (SELECT s.inst_id AS inst, s.sid||','||s.serial# AS sid, s.username, s.status, s.event, s.seconds_in_wait AS secs, s.sql_id FROM gv$session s WHERE s.type='USER' AND s.status='ACTIVE' AND s.username IS NOT NULL ORDER BY s.seconds_in_wait DESC) WHERE ROWNUM <= 20;

PROMPT @@@FILE:iostat.txt@@@
PROMPT === IO POR ARQUIVO ===
SELECT * FROM (SELECT fs.inst_id AS inst, df.tablespace_name AS tablespace, SUBSTR(df.file_name,-40) AS file_name, fs.phyrds, fs.phywrts, ROUND(fs.readtim*10/GREATEST(fs.phyrds,1),2) AS avg_read_ms, ROUND(fs.writetim*10/GREATEST(fs.phywrts,1),2) AS avg_write_ms FROM gv$filestat fs JOIN dba_data_files df ON df.file_id = fs.file# ORDER BY avg_read_ms DESC) WHERE ROWNUM <= 20;

PROMPT @@@FILE:space.txt@@@
PROMPT === TABLESPACES ===
SELECT df.tablespace_name, ROUND(SUM(df.bytes)/1048576) AS alloc_mb, ROUND(SUM(df.bytes-NVL(fs.free,0))/1048576) AS used_mb, ROUND(SUM(CASE WHEN df.autoextensible='YES' THEN GREATEST(df.maxbytes,df.bytes) ELSE df.bytes END)/1048576) AS max_mb, ROUND(100*(1-SUM(NVL(fs.free,0))/SUM(df.bytes)),1) AS pct_alloc, ROUND(100*SUM(df.bytes-NVL(fs.free,0))/SUM(CASE WHEN df.autoextensible='YES' THEN GREATEST(df.maxbytes,df.bytes) ELSE df.bytes END),1) AS pct_used, MIN(df.autoextensible) AS autoext FROM dba_data_files df LEFT JOIN (SELECT file_id, SUM(bytes) free FROM dba_free_space GROUP BY file_id) fs ON df.file_id = fs.file_id GROUP BY df.tablespace_name ORDER BY pct_used DESC;

PROMPT @@@FILE:forecast.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === PREVISAO DE CAPACIDADE (30d) ===
WITH snaps AS (SELECT snap_id, dbid, MIN(end_interval_time) AS dt FROM dba_hist_snapshot WHERE end_interval_time > SYSDATE - 30 GROUP BY snap_id, dbid), dia AS (SELECT u.tablespace_id AS tsid, TRUNC(s.dt) AS d, MAX(u.tablespace_usedsize) AS used_blk, MAX(u.tablespace_maxsize) AS max_blk FROM dba_hist_tbspc_space_usage u JOIN snaps s ON s.snap_id = u.snap_id AND s.dbid = u.dbid GROUP BY u.tablespace_id, TRUNC(s.dt)), reg AS (SELECT tsid, COUNT(*) AS dias_hist, REGR_SLOPE(used_blk, d - TRUNC(SYSDATE)) AS slope, MAX(used_blk) KEEP (DENSE_RANK LAST ORDER BY d) AS used_blk, MAX(max_blk) KEEP (DENSE_RANK LAST ORDER BY d) AS max_blk FROM dia GROUP BY tsid) SELECT * FROM (SELECT t.name AS tablespace_name, ROUND(100*r.used_blk/GREATEST(r.max_blk,1),1) AS pct_used, ROUND(r.used_blk*ts.block_size/1048576) AS used_mb, ROUND(r.max_blk*ts.block_size/1048576) AS max_mb, ROUND(r.slope*ts.block_size/1048576,1) AS growth_mb_dia, CASE WHEN r.slope > 0 THEN ROUND((r.max_blk - r.used_blk)/r.slope) END AS dias_p_encher, r.dias_hist FROM reg r JOIN v$tablespace t ON t.ts# = r.tsid JOIN dba_tablespaces ts ON ts.tablespace_name = t.name ORDER BY CASE WHEN r.slope > 0 THEN (r.max_blk - r.used_blk)/r.slope END NULLS LAST) WHERE ROWNUM <= 20;

PROMPT @@@FILE:asm.txt@@@
PROMPT === ASM DISKGROUPS ===
SELECT name, state, type, total_mb, free_mb, ROUND(100*(1-free_mb/GREATEST(total_mb,1)),1) AS pct_used FROM v$asm_diskgroup ORDER BY pct_used DESC;

PROMPT @@@FILE:undo.txt@@@
PROMPT === UNDO USAGE (7 dias) ===
SELECT begin_time, tuned_undoretention, maxquerylen, ssolderrcnt AS snap_too_old, nospaceerrcnt AS no_space FROM v$undostat WHERE begin_time > SYSDATE - 7 ORDER BY begin_time DESC FETCH FIRST 24 ROWS ONLY;

PROMPT @@@FILE:temp.txt@@@
PROMPT === TEMP USAGE POR SESSAO ===
SELECT * FROM (SELECT s.inst_id, s.sid, s.username, u.tablespace, ROUND(u.blocks*t.block_size/1048576) AS temp_mb, s.sql_id FROM gv$sort_usage u JOIN gv$session s ON s.saddr = u.session_addr AND s.inst_id = u.inst_id JOIN dba_tablespaces t ON t.tablespace_name = u.tablespace ORDER BY temp_mb DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:fra.txt@@@
PROMPT === FRA USAGE ===
SELECT file_type, ROUND(percent_space_used,1) AS pct_used, ROUND(percent_space_reclaimable,1) AS pct_reclaimable, number_of_files FROM v$flash_recovery_area_usage ORDER BY pct_used DESC;

PROMPT @@@FILE:dataguard.txt@@@
PROMPT === DATAGUARD STATS ===
SELECT name, value, TO_CHAR(time_computed) AS time_computed FROM gv$dataguard_stats WHERE name IN ('transport lag','apply lag');
PROMPT === MANAGED STANDBY ===
SELECT inst_id, process, status, thread#, sequence#, block# FROM gv$managed_standby WHERE process LIKE 'MRP%' OR process LIKE 'RFS%' OR status <> 'IDLE' ORDER BY inst_id, process;

PROMPT @@@FILE:rman.txt@@@
PROMPT === RMAN JOBS (7 dias) ===
SELECT input_type, status, TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS start_time, TO_CHAR(end_time,'YYYY-MM-DD HH24:MI') AS end_time, ROUND(input_bytes/1073741824,1) AS input_gb, ROUND(output_bytes/1073741824,1) AS output_gb, time_taken_display AS elapsed FROM v$rman_backup_job_details WHERE start_time > SYSDATE-7 ORDER BY start_time DESC;

PROMPT @@@FILE:rac.txt@@@
PROMPT === GLOBAL CACHE WAITS ===
SELECT inst_id, event, total_waits, ROUND(time_waited_micro/1000/GREATEST(total_waits,1),2) AS avg_ms FROM gv$system_event WHERE event LIKE 'gc %' AND total_waits > 0 ORDER BY avg_ms DESC FETCH FIRST 15 ROWS ONLY;

PROMPT @@@FILE:alert.log@@@
PROMPT === ALERT LOG - ORA- (24h) ===
SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD"T"HH24:MI:SS')||'  '||message_text AS alert_line FROM v$diag_alert_ext WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '24' HOUR AND message_text LIKE '%ORA-%' ORDER BY originating_timestamp;

PROMPT @@@FILE:stats.txt@@@
PROMPT === ESTATISTICAS STALE/ANTIGAS ===
SELECT * FROM (SELECT owner||'.'||table_name AS table_name, TO_CHAR(last_analyzed,'YYYY-MM-DD HH24:MI') AS last_analyzed, stale_stats, num_rows FROM dba_tab_statistics WHERE owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','DBSNMP') AND (stale_stats='YES' OR last_analyzed < SYSDATE-7 OR last_analyzed IS NULL) ORDER BY last_analyzed NULLS FIRST) WHERE ROWNUM <= 30;

PROMPT @@@FILE:invalid.txt@@@
PROMPT === OBJETOS INVALIDOS ===
SELECT owner, object_type, COUNT(*) AS qtd FROM dba_objects WHERE status = 'INVALID' GROUP BY owner, object_type ORDER BY qtd DESC FETCH FIRST 20 ROWS ONLY;

PROMPT @@@FILE:memory.txt@@@
PROMPT === MEMORIA (SGA/PGA) ===
SELECT name, ROUND(TO_NUMBER(value DEFAULT NULL ON CONVERSION ERROR)/1048576) AS mb FROM v$parameter WHERE name IN ('sga_target','sga_max_size','pga_aggregate_target','pga_aggregate_limit','memory_target','memory_max_target') AND TO_NUMBER(value DEFAULT NULL ON CONVERSION ERROR) > 0 ORDER BY name;

PROMPT @@@FILE:instance.txt@@@
PROMPT === SAUDE DA INSTANCIA ===
SELECT i.inst_id, i.instance_name, i.host_name, i.version, i.status, TO_CHAR(i.startup_time,'YYYY-MM-DD HH24:MI') AS startup, d.log_mode, d.open_mode, d.database_role FROM gv$instance i CROSS JOIN v$database d ORDER BY i.inst_id;

PROMPT @@@FILE:resource_limits.txt@@@
PROMPT === LIMITES (PROCESSES/SESSIONS) ===
SELECT inst_id, resource_name, current_utilization AS atual, max_utilization AS pico, limit_value AS limite, CASE WHEN limit_value='UNLIMITED' THEN NULL ELSE ROUND(current_utilization/NULLIF(TO_NUMBER(limit_value),0)*100,1) END AS pct_used, CASE WHEN limit_value='UNLIMITED' THEN 'OK' WHEN current_utilization/NULLIF(TO_NUMBER(limit_value),0)*100 > 90 THEN 'CRITICAL' WHEN current_utilization/NULLIF(TO_NUMBER(limit_value),0)*100 > 80 THEN 'WARNING' ELSE 'OK' END AS status FROM gv$resource_limit WHERE resource_name IN ('processes','sessions') ORDER BY inst_id, resource_name;

PROMPT @@@FILE:hitratio.txt@@@
PROMPT === BUFFER CACHE HIT RATIO ===
SELECT ROUND((1-(phy.value/NULLIF(db.value+cons.value,0)))*100,2) AS hit_ratio_pct, CASE WHEN (1-(phy.value/NULLIF(db.value+cons.value,0)))*100 < 90 THEN 'INFO' ELSE 'OK' END AS status FROM (SELECT SUM(value) value FROM gv$sysstat WHERE name='physical reads') phy, (SELECT SUM(value) value FROM gv$sysstat WHERE name='db block gets') db, (SELECT SUM(value) value FROM gv$sysstat WHERE name='consistent gets') cons;

PROMPT @@@FILE:sgacomp.txt@@@
PROMPT === SGA DYNAMIC COMPONENTS ===
SELECT inst_id, component, ROUND(current_size/1048576) AS current_mb, ROUND(min_size/1048576) AS min_mb, ROUND(max_size/1048576) AS max_mb, oper_count AS resizes, CASE WHEN oper_count > 100 THEN 'WARNING' WHEN oper_count > 20 THEN 'INFO' ELSE 'OK' END AS status FROM gv$sga_dynamic_components WHERE current_size > 0 ORDER BY inst_id, current_size DESC;

PROMPT @@@FILE:idx_unusable.txt@@@
PROMPT === INDICES UNUSABLE ===
SELECT owner, index_name, table_name, tablespace_name, 'WARNING' AS status FROM dba_indexes WHERE status='UNUSABLE' AND owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','ORDSYS','WMSYS') FETCH FIRST 30 ROWS ONLY;

PROMPT @@@FILE:redo.txt@@@
PROMPT === REDO LOG SWITCHES (24h) ===
SELECT * FROM (SELECT thread#, TO_CHAR(first_time,'YYYY-MM-DD HH24') AS hora, COUNT(*) AS switches FROM v$log_history WHERE first_time > SYSDATE-1 GROUP BY thread#, TO_CHAR(first_time,'YYYY-MM-DD HH24') ORDER BY hora DESC) WHERE ROWNUM <= 30;

PROMPT @@@FILE:df_noautoext.txt@@@
PROMPT === DATAFILES AUTOEXTEND OFF ===
SELECT * FROM (SELECT tablespace_name, SUBSTR(file_name,-45) AS file_name, ROUND(bytes/1048576) AS size_mb, autoextensible FROM dba_data_files WHERE autoextensible='NO' ORDER BY bytes DESC) WHERE ROWNUM <= 30;

PROMPT @@@FILE:segments.txt@@@
PROMPT === TOP 10 SEGMENTOS ===
SELECT * FROM (SELECT owner, segment_name, segment_type, tablespace_name, ROUND(bytes/1048576) AS size_mb FROM dba_segments WHERE owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','ORDSYS','WMSYS') ORDER BY bytes DESC) WHERE ROWNUM <= 10;

PROMPT @@@FILE:compare_period.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === COMPARE PERIOD (regressao de plano/tempo) ===
SELECT sql_id, plano_base, plano_rec, avg_s_base, avg_s_rec, execs_rec, ROUND((avg_s_rec-avg_s_base)*execs_rec,1) AS impacto_s, ROUND((avg_s_rec-avg_s_base)/NULLIF(avg_s_base,0)*100) AS var_pct, CASE WHEN (avg_s_rec-avg_s_base)*execs_rec >= 300 THEN 'CRITICAL' WHEN plano_mudou=1 AND (avg_s_rec-avg_s_base)*execs_rec >= 60 THEN 'CRITICAL' WHEN (avg_s_rec-avg_s_base)*execs_rec >= 60 THEN 'WARNING' ELSE 'OK' END AS status FROM (SELECT sql_id, plano_base, plano_rec, avg_s_base, avg_s_rec, execs_rec, CASE WHEN NVL(plano_base,0)<>0 AND NVL(plano_rec,0)<>0 AND plano_base<>plano_rec THEN 1 ELSE 0 END AS plano_mudou FROM (SELECT sql_id, MAX(CASE WHEN periodo='BASE' THEN phv END) AS plano_base, MAX(CASE WHEN periodo='REC' THEN phv END) AS plano_rec, ROUND(SUM(CASE WHEN periodo='BASE' THEN elapsed END)/1000000/NULLIF(SUM(CASE WHEN periodo='BASE' THEN execs END),0),3) AS avg_s_base, ROUND(SUM(CASE WHEN periodo='REC' THEN elapsed END)/1000000/NULLIF(SUM(CASE WHEN periodo='REC' THEN execs END),0),3) AS avg_s_rec, SUM(CASE WHEN periodo='REC' THEN execs END) AS execs_rec FROM (SELECT ss.sql_id, ss.plan_hash_value AS phv, CASE WHEN sn.begin_interval_time > SYSDATE-1 THEN 'REC' ELSE 'BASE' END AS periodo, ss.executions_delta AS execs, ss.elapsed_time_delta AS elapsed FROM dba_hist_sqlstat ss JOIN dba_hist_snapshot sn ON ss.snap_id=sn.snap_id AND ss.instance_number=sn.instance_number WHERE sn.begin_interval_time > SYSDATE-7 AND ss.executions_delta > 0) GROUP BY sql_id HAVING SUM(CASE WHEN periodo='REC' THEN execs END) > 0 AND SUM(CASE WHEN periodo='BASE' THEN execs END) > 0)) WHERE avg_s_base IS NOT NULL AND avg_s_rec IS NOT NULL AND avg_s_rec >= 0.05 AND avg_s_rec-avg_s_base >= 0.05 AND (avg_s_rec > avg_s_base*1.3 OR plano_mudou=1) ORDER BY impacto_s DESC FETCH FIRST 20 ROWS ONLY;

PROMPT @@@FILE:workload_trend.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === TENDENCIA DE CARGA (metricas) ===
SELECT metrica, avg_base, avg_rec, ROUND((avg_rec-avg_base)/NULLIF(avg_base,0)*100) AS var_pct, CASE WHEN avg_rec > avg_base*2 THEN 'CRITICAL' WHEN avg_rec > avg_base*1.5 THEN 'WARNING' ELSE 'OK' END AS status FROM (SELECT metric_name AS metrica, ROUND(AVG(CASE WHEN periodo='BASE' THEN average END),2) AS avg_base, ROUND(AVG(CASE WHEN periodo='REC' THEN average END),2) AS avg_rec FROM (SELECT m.metric_name, m.average, CASE WHEN sn.begin_interval_time > SYSDATE-1 THEN 'REC' ELSE 'BASE' END AS periodo FROM dba_hist_sysmetric_summary m JOIN dba_hist_snapshot sn ON m.snap_id=sn.snap_id AND m.instance_number=sn.instance_number AND m.dbid=sn.dbid WHERE sn.begin_interval_time > SYSDATE-8 AND m.metric_name IN ('Host CPU Utilization (%)','Executions Per Sec','Logons Per Sec','User Calls Per Sec','Physical Reads Per Sec','User Transaction Per Sec','Redo Generated Per Sec') UNION ALL SELECT 'Average Active Sessions', ROUND(COUNT(*)*10/(CASE periodo WHEN 'REC' THEN 86400 ELSE 7*86400 END),4), periodo FROM (SELECT CASE WHEN sample_time > SYSDATE-1 THEN 'REC' ELSE 'BASE' END AS periodo FROM dba_hist_active_sess_history WHERE sample_time > SYSDATE-8) GROUP BY periodo) GROUP BY metric_name) WHERE avg_base IS NOT NULL AND avg_rec IS NOT NULL AND avg_rec > avg_base*1.3 ORDER BY var_pct DESC;

PROMPT @@@FILE:workload_module.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === AUMENTO DE CARGA POR MODULO ===
SELECT modulo, base_dia, rec_dia, ROUND((rec_dia-base_dia)/NULLIF(base_dia,0)*100) AS var_pct, CASE WHEN rec_dia > base_dia*2 THEN 'CRITICAL' WHEN rec_dia > base_dia*1.5 THEN 'WARNING' ELSE 'OK' END AS status FROM (SELECT NVL(module,'(sem modulo)') AS modulo, ROUND(SUM(CASE WHEN periodo='BASE' THEN 1 ELSE 0 END)/7,1) AS base_dia, ROUND(SUM(CASE WHEN periodo='REC' THEN 1 ELSE 0 END),1) AS rec_dia FROM (SELECT h.module, CASE WHEN h.sample_time > SYSDATE-1 THEN 'REC' ELSE 'BASE' END AS periodo FROM dba_hist_active_sess_history h WHERE h.sample_time > SYSDATE-8 AND NVL(h.module,'-') NOT LIKE 'TROUBLESHOOT-%') GROUP BY NVL(module,'(sem modulo)')) WHERE base_dia > 0 AND rec_dia > base_dia*1.3 ORDER BY var_pct DESC FETCH FIRST 20 ROWS ONLY;

PROMPT @@@FILE:workload_daily.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === CARGA - SERIE DIARIA POR METRICA (14d) ===
SELECT dia, metrica, valor FROM (SELECT TO_CHAR(TRUNC(sn.begin_interval_time),'YYYY-MM-DD') AS dia, m.metric_name AS metrica, ROUND(AVG(m.average),2) AS valor FROM dba_hist_sysmetric_summary m JOIN dba_hist_snapshot sn ON m.snap_id=sn.snap_id AND m.instance_number=sn.instance_number AND m.dbid=sn.dbid WHERE sn.begin_interval_time > SYSDATE-14 AND m.metric_name IN ('Host CPU Utilization (%)','Executions Per Sec','User Calls Per Sec','User Transaction Per Sec') GROUP BY TO_CHAR(TRUNC(sn.begin_interval_time),'YYYY-MM-DD'), m.metric_name UNION ALL SELECT TO_CHAR(TRUNC(h.sample_time),'YYYY-MM-DD') AS dia, 'Average Active Sessions' AS metrica, ROUND(COUNT(*)*10/86400,2) AS valor FROM dba_hist_active_sess_history h WHERE h.sample_time > SYSDATE-14 GROUP BY TO_CHAR(TRUNC(h.sample_time),'YYYY-MM-DD')) ORDER BY metrica, dia;

PROMPT @@@FILE:workload_module_daily.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === CARGA POR MODULO - SERIE DIARIA (14d) ===
SELECT dia, modulo, samples FROM (SELECT TO_CHAR(TRUNC(h.sample_time),'YYYY-MM-DD') AS dia, NVL(h.module,'(sem modulo)') AS modulo, COUNT(*) AS samples FROM dba_hist_active_sess_history h WHERE h.sample_time > SYSDATE-14 AND NVL(h.module,'-') NOT LIKE 'TROUBLESHOOT-%' GROUP BY TO_CHAR(TRUNC(h.sample_time),'YYYY-MM-DD'), NVL(h.module,'(sem modulo)')) d WHERE d.modulo IN (SELECT modulo FROM (SELECT NVL(module,'(sem modulo)') AS modulo, COUNT(*) c FROM dba_hist_active_sess_history WHERE sample_time > SYSDATE-14 AND NVL(module,'-') NOT LIKE 'TROUBLESHOOT-%' GROUP BY NVL(module,'(sem modulo)') ORDER BY c DESC) WHERE ROWNUM <= 6) ORDER BY modulo, dia;

PROMPT @@@FILE:changes.txt@@@
PROMPT === RESTART DE INSTANCIA ===
SELECT TO_CHAR(startup_time,'YYYY-MM-DD HH24:MI') AS quando, 'RESTART' AS tipo, 'instancia '||instance_name AS objeto, 'startup - no ar ha '||ROUND(SYSDATE-startup_time,1)||' dia(s)' AS detalhe FROM gv$instance ORDER BY inst_id;
PROMPT === DDL RECENTE (30d) ===
SELECT * FROM (SELECT TO_CHAR(o.last_ddl_time,'YYYY-MM-DD HH24:MI') AS quando, 'DDL' AS tipo, o.owner||'.'||o.object_name AS objeto, o.object_type||' ('||o.status||')' AS detalhe FROM dba_objects o WHERE o.last_ddl_time > SYSDATE - 30 AND o.owner NOT IN ('SYS','SYSTEM','XDB','WMSYS','MDSYS','CTXSYS','DBSNMP','OUTLN','ORDSYS','ORDDATA','ORDPLUGINS','OLAPSYS','APPQOSSYS','AUDSYS','LBACSYS','DVSYS','OJVMSYS','GSMADMIN_INTERNAL','ORACLE_OCM','SYSBACKUP','SYSDG','SYSKM','SYSRAC','GGSYS','DIP','ANONYMOUS','EXFSYS','SYSMAN','TSMSYS','MDDATA','FLOWS_FILES','REMOTE_SCHEDULER_AGENT','SI_INFORMTN_SCHEMA','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','XS$NULL','PDBADMIN') AND NOT (o.owner = 'PUBLIC' AND o.object_type = 'SYNONYM') AND o.object_type NOT IN ('LOB','LOB PARTITION','TABLE PARTITION','INDEX PARTITION','TABLE SUBPARTITION','INDEX SUBPARTITION') ORDER BY o.last_ddl_time DESC) WHERE ROWNUM <= 40;
PROMPT === COLETA DE ESTATISTICAS (30d) ===
SELECT * FROM (SELECT TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS quando, 'STATS' AS tipo, NVL(target,'(global)') AS objeto, operation||' - '||ROUND((CAST(end_time AS DATE)-CAST(start_time AS DATE))*1440,1)||' min' AS detalhe FROM dba_optstat_operations WHERE start_time > SYSTIMESTAMP - 30 ORDER BY start_time DESC) WHERE ROWNUM <= 30;
PROMPT === PARAMETROS MODIFICADOS NA INSTANCIA (sem data) ===
SELECT '(sem data)' AS quando, 'PARAM_MODIF' AS tipo, name AS objeto, ismodified||' - valor atual: '||SUBSTR(NVL(value,'(vazio)'),1,40) AS detalhe FROM v$system_parameter WHERE ismodified <> 'FALSE' ORDER BY name;
PROMPT === PATCHES (SQLPATCH) ===
SELECT * FROM (SELECT TO_CHAR(action_time,'YYYY-MM-DD HH24:MI') AS quando, 'PATCH' AS tipo, TO_CHAR(patch_id) AS objeto, action||' '||status||' - '||SUBSTR(NVL(description,'(sem descricao)'),1,45) AS detalhe FROM dba_registry_sqlpatch WHERE action_time > SYSTIMESTAMP - 90 ORDER BY action_time DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:changes_params.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === PARAMETROS ALTERADOS (30d) ===
WITH snaps AS (SELECT snap_id, instance_number, MIN(end_interval_time) AS dt FROM dba_hist_snapshot WHERE end_interval_time > SYSDATE - 30 AND dbid = (SELECT dbid FROM v$database) GROUP BY snap_id, instance_number), p AS (SELECT hp.snap_id, hp.instance_number, hp.con_id, hp.parameter_name, hp.value, LAG(hp.value) OVER (PARTITION BY hp.instance_number, hp.con_id, hp.parameter_name ORDER BY hp.snap_id) AS val_ant FROM dba_hist_parameter hp WHERE hp.dbid = (SELECT dbid FROM v$database) AND hp.snap_id IN (SELECT snap_id FROM snaps) AND (hp.instance_number, hp.con_id, hp.parameter_name) IN (SELECT instance_number, con_id, parameter_name FROM dba_hist_parameter WHERE dbid = (SELECT dbid FROM v$database) AND snap_id IN (SELECT snap_id FROM snaps) GROUP BY instance_number, con_id, parameter_name HAVING COUNT(DISTINCT NVL(value,'~')) > 1)) SELECT * FROM (SELECT TO_CHAR(s.dt,'YYYY-MM-DD HH24:MI') AS quando, 'PARAMETRO' AS tipo, p.parameter_name AS objeto, 'inst '||p.instance_number||' con '||p.con_id||': '||SUBSTR(NVL(p.val_ant,'(vazio)'),1,20)||' -> '||SUBSTR(NVL(p.value,'(vazio)'),1,20) AS detalhe FROM p JOIN snaps s ON s.snap_id = p.snap_id AND s.instance_number = p.instance_number WHERE p.val_ant IS NOT NULL AND UPPER(NVL(p.value,'~')) <> UPPER(NVL(p.val_ant,'~')) AND NOT (p.parameter_name = 'resource_manager_plan' AND (NVL(p.val_ant,'x') LIKE 'SCHEDULER[%' OR NVL(p.value,'x') LIKE 'SCHEDULER[%')) ORDER BY p.snap_id DESC) WHERE ROWNUM <= 25;

PROMPT @@@FILE:load_profile.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === LOAD PROFILE (AWR - ultima hora) ===
SELECT metrica, valor, unidade FROM (SELECT metrica, ROUND(AVG(average),2) AS valor, MAX(metric_unit) AS unidade FROM (SELECT m.metric_name AS metrica, m.average, m.metric_unit FROM dba_hist_sysmetric_summary m JOIN dba_hist_snapshot sn ON m.snap_id=sn.snap_id AND m.instance_number=sn.instance_number AND m.dbid=sn.dbid WHERE sn.begin_interval_time > SYSDATE - 1/24 AND m.metric_name IN ('Database CPU Time Ratio','Host CPU Utilization (%)','Executions Per Sec','User Calls Per Sec','User Transaction Per Sec','Logons Per Sec','Hard Parse Count Per Sec','Physical Reads Per Sec','Redo Generated Per Sec')) GROUP BY metrica UNION ALL SELECT 'Average Active Sessions' AS metrica, ROUND(COUNT(*)*10/3600,2) AS valor, 'Sessions' AS unidade FROM dba_hist_active_sess_history h WHERE h.sample_time > SYSDATE - 1/24) ORDER BY metrica;

PROMPT @@@FILE:activity_user.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === ATIVIDADE POR USUARIO (ASH 60min) ===
SELECT * FROM (SELECT NVL(u.username,'('||ash.user_id||')') AS usuario, COUNT(*) AS samples, ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS activity_pct FROM gv$active_session_history ash LEFT JOIN dba_users u ON u.user_id=ash.user_id WHERE ash.sample_time > SYSDATE - 1/24 GROUP BY NVL(u.username,'('||ash.user_id||')') ORDER BY samples DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:activity_object.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === ATIVIDADE POR OBJETO (ASH 60min) ===
SELECT * FROM (SELECT o.owner||'.'||o.object_name AS objeto, o.object_type AS tipo, COUNT(*) AS samples, ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS activity_pct FROM gv$active_session_history ash JOIN dba_objects o ON o.object_id=ash.current_obj# WHERE ash.sample_time > SYSDATE - 1/24 AND ash.current_obj# > 0 GROUP BY o.owner, o.object_name, o.object_type ORDER BY samples DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:activity_machine.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === ATIVIDADE POR MAQUINA (ASH 60min) ===
SELECT * FROM (SELECT machine, COUNT(*) AS samples, ROUND(100*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS activity_pct FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 AND machine IS NOT NULL GROUP BY machine ORDER BY samples DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:topology.txt@@@
PROMPT === TOPOLOGIA DO AMBIENTE ===
SELECT i.inst_id, i.instance_name, i.host_name, i.status, TO_CHAR(i.startup_time,'YYYY-MM-DD HH24:MI') AS startup FROM gv$instance i ORDER BY i.inst_id;

PROMPT @@@FILE:rac_nodes.txt@@@
PROMPT === SESSOES POR INSTANCIA ===
SELECT inst_id, COUNT(*) AS sessoes, SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) AS ativas FROM gv$session WHERE type='USER' GROUP BY inst_id ORDER BY inst_id;
PROMPT === SERVICOS ATIVOS ===
SELECT inst_id, name AS servico FROM gv$active_services WHERE name NOT LIKE 'SYS$%' ORDER BY name, inst_id;
PROMPT === INTERCONNECT (BLOCOS PERDIDOS) ===
SELECT inst_id, name, value FROM gv$sysstat WHERE name IN ('gc blocks lost','gc blocks corrupt','gc cr block lost','gc current block lost') AND value > 0 ORDER BY inst_id, name;

PROMPT @@@FILE:ash_por_no.txt@@@
-- ATENCAO: requer DIAGNOSTIC PACK (licenca Oracle) - remova se nao licenciado
PROMPT === WAIT CLASSES POR INSTANCIA (ASH 60min) ===
SELECT inst_id, NVL(wait_class,'CPU') AS wait_class, COUNT(*) AS samples FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 GROUP BY inst_id, NVL(wait_class,'CPU') ORDER BY inst_id, samples DESC;

PROMPT @@@FILE:exadata.txt@@@
PROMPT === OFFLOAD / SMART SCAN ===
SELECT inst_id, name, value FROM gv$sysstat WHERE name IN ('cell physical IO bytes eligible for predicate offload','cell physical IO interconnect bytes returned by smart scan','cell physical IO bytes saved by storage index','cell flash cache read hits','physical read total bytes') ORDER BY inst_id, name;
PROMPT === ESPERAS DE CELULA ===
SELECT inst_id, event, total_waits, ROUND(time_waited_micro/1000/GREATEST(total_waits,1),2) AS avg_ms FROM gv$system_event WHERE event LIKE 'cell %' AND total_waits > 0 ORDER BY avg_ms DESC FETCH FIRST 15 ROWS ONLY;

PROMPT @@@FILE:goldengate.txt@@@
PROMPT === CAPTURE ===
SELECT capture_name, status, capture_type, error_number, error_message FROM dba_capture ORDER BY capture_name;
PROMPT === APPLY ===
SELECT apply_name, status, apply_captured, error_number, error_message FROM dba_apply ORDER BY apply_name;
PROMPT === LAG DE APPLY ===
SELECT apply_name, TO_CHAR(applied_message_create_time,'YYYY-MM-DD HH24:MI:SS') AS ultima_mensagem, ROUND((SYSDATE - applied_message_create_time)*1440,1) AS lag_min FROM dba_apply_progress ORDER BY lag_min DESC;

PROMPT @@@FILE:amostragem.txt@@@
PROMPT === AMOSTRAGEM DE SESSOES (SEM DIAGNOSTICS PACK) ===
SELECT CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' THEN wait_class ELSE 'CPU' END AS wait_class, CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' THEN event ELSE 'ON CPU' END AS event, COUNT(*) AS amostras FROM gv$session WHERE status = 'ACTIVE' AND type = 'USER' AND (state <> 'WAITING' OR wait_class <> 'Idle') GROUP BY CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' THEN wait_class ELSE 'CPU' END, CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' THEN event ELSE 'ON CPU' END ORDER BY amostras DESC;

PROMPT @@@FILE:corruption.txt@@@
PROMPT === BLOCOS CORROMPIDOS ===
SELECT file# AS file_id, block# AS block_id, blocks, corruption_change# AS change_num, corruption_type FROM v$database_block_corruption ORDER BY file#, block#;

PROMPT @@@FILE:datafile_status.txt@@@
PROMPT === DATAFILES FORA DE ONLINE ===
SELECT d.file# AS file_id, d.status AS file_status, h.status AS header_status, NVL(r.error,'-') AS erro, SUBSTR(d.name,1,60) AS arquivo FROM v$datafile d JOIN v$datafile_header h ON h.file# = d.file# LEFT JOIN v$recover_file r ON r.file# = d.file# WHERE d.status NOT IN ('ONLINE','SYSTEM') OR h.status <> 'ONLINE' OR r.file# IS NOT NULL ORDER BY d.file#;

PROMPT @@@FILE:archive_dest.txt@@@
PROMPT === ARCHIVE DEST STATUS ===
SELECT dest_id, dest_name, status, type, NVL(SUBSTR(error,1,60),'-') AS erro, archived_seq# AS ultimo_seq FROM v$archive_dest_status WHERE status <> 'INACTIVE' ORDER BY dest_id;

PROMPT @@@FILE:backup_age.txt@@@
PROMPT === IDADE DO BACKUP POR DATAFILE ===
SELECT d.file# AS file_id, SUBSTR(t.name,1,30) AS tablespace_name, NVL(TO_CHAR(b.ultimo,'YYYY-MM-DD HH24:MI'),'NUNCA') AS ultimo_backup, ROUND(SYSDATE - b.ultimo,1) AS dias FROM v$datafile d JOIN v$tablespace t ON t.ts# = d.ts# LEFT JOIN (SELECT file#, MAX(completion_time) AS ultimo FROM v$backup_datafile GROUP BY file#) b ON b.file# = d.file# ORDER BY dias DESC NULLS FIRST;

PROMPT @@@FILE:archive_gap.txt@@@
PROMPT === ARCHIVE GAP (STANDBY) ===
SELECT thread# AS thread_num, low_sequence# AS seq_inicial, high_sequence# AS seq_final FROM v$archive_gap ORDER BY thread#;

PROMPT @@@FILE:accounts.txt@@@
PROMPT === CONTAS EXPIRANDO ===
SELECT username, account_status, profile, TO_CHAR(expiry_date,'YYYY-MM-DD') AS expira_em, ROUND(expiry_date - SYSDATE) AS dias FROM dba_users WHERE (expiry_date IS NOT NULL AND expiry_date < SYSDATE + 60) OR account_status LIKE '%LOCKED%' OR account_status LIKE '%EXPIRED%' ORDER BY expiry_date NULLS LAST;

PROMPT @@@FILE:alert_recent.txt@@@
PROMPT === ALERT LOG RECENTE (7 dias) ===
SELECT * FROM (SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD HH24:MI:SS') AS quando, message_level AS nivel, SUBSTR(TRIM(REPLACE(REPLACE(message_text, CHR(10), ' '), CHR(13), ' ')),1,110) AS mensagem FROM v$diag_alert_ext WHERE originating_timestamp > SYSTIMESTAMP - NUMTODSINTERVAL(7,'DAY') AND (REGEXP_LIKE(message_text,'ORA-[0-9]{4,5}') OR message_level <= 2) ORDER BY originating_timestamp DESC) WHERE ROWNUM <= 40;

PROMPT @@@FILE:patches.txt@@@
PROMPT === VERSAO E PATCH ===
SELECT 'versao' AS item, CAST((SELECT version FROM v$instance) AS VARCHAR2(80)) AS valor, '-' AS quando FROM dual UNION ALL SELECT 'ultimo_patch', CAST(SUBSTR(descricao,1,60) AS VARCHAR2(80)), TO_CHAR(quando,'YYYY-MM-DD') FROM (SELECT description AS descricao, action_time AS quando FROM dba_registry_sqlpatch ORDER BY action_time DESC) WHERE ROWNUM = 1 UNION ALL SELECT 'dias_desde_patch', CAST(TO_CHAR(ROUND(SYSDATE - (SELECT CAST(MAX(action_time) AS DATE) FROM dba_registry_sqlpatch))) AS VARCHAR2(80)), '-' FROM dual;

PROMPT @@@FILE:dict_health.txt@@@
PROMPT === INTEGRIDADE DO DICIONARIO ===
SELECT 'componente_invalido' AS tipo, comp_id AS objeto, status AS detalhe FROM dba_registry WHERE status NOT IN ('VALID','OPTION OFF','REMOVED') UNION ALL SELECT 'objeto_sys_invalido', owner||'.'||object_name, object_type FROM dba_objects WHERE owner IN ('SYS','SYSTEM') AND status = 'INVALID' AND ROWNUM <= 30;

PROMPT @@@FILE:rman_fail.txt@@@
PROMPT === FALHAS DE BACKUP (RMAN) ===
SELECT operation, status, TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS inicio, ROUND(mbytes_processed/1024) AS gb, object_type FROM v$rman_status WHERE status LIKE '%FAILED%' AND start_time > SYSDATE - 7 ORDER BY start_time DESC;

PROMPT @@@FILE:bp_params.txt@@@
PROMPT === PARAMETROS X RECOMENDADO ===
SELECT name, SUBSTR(NVL(value,'(nulo)'),1,60) AS valor, isdefault FROM v$parameter WHERE name IN ('db_block_checksum','db_block_checking','db_lost_write_protect','use_large_pages','filesystemio_options','disk_asynch_io','fast_start_mttr_target','undo_retention','open_cursors','session_cached_cursors','cursor_sharing','optimizer_adaptive_statistics','statistics_level','timed_statistics','max_dump_file_size','control_file_record_keep_time','log_checkpoints_to_alert','resource_limit','remote_os_authent','audit_trail') ORDER BY name;

PROMPT @@@FILE:bp_database.txt@@@
PROMPT === CONFIGURACAO X RECOMENDADO ===
SELECT 'log_mode' AS item, CAST(log_mode AS VARCHAR2(60)) AS valor FROM v$database UNION ALL SELECT 'force_logging', CAST(force_logging AS VARCHAR2(60)) FROM v$database UNION ALL SELECT 'flashback_on', CAST(flashback_on AS VARCHAR2(60)) FROM v$database UNION ALL SELECT 'database_role', CAST(database_role AS VARCHAR2(60)) FROM v$database UNION ALL SELECT 'block_change_tracking', CAST(NVL((SELECT status FROM v$block_change_tracking),'DISABLED') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'standby_configurado', CAST(NVL2((SELECT MAX(dest_id) FROM v$archive_dest WHERE target='STANDBY' AND destination IS NOT NULL),'SIM','NAO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'rman_'||LOWER(REPLACE(name,' ','_')), CAST(SUBSTR(value,1,60) AS VARCHAR2(60)) FROM v$rman_configuration;

PROMPT @@@FILE:bp_multiplex.txt@@@
PROMPT === MULTIPLEXACAO ===
SELECT 'controlfiles' AS item, TO_CHAR(COUNT(*)) AS qtd, '-' AS detalhe FROM v$controlfile UNION ALL SELECT 'redo_grupo_'||TO_CHAR(l.group#), TO_CHAR(COUNT(f.member)), TO_CHAR(ROUND(MAX(l.bytes)/1024/1024))||' MB' FROM v$log l JOIN v$logfile f ON f.group# = l.group# GROUP BY l.group# UNION ALL SELECT 'standby_redo', TO_CHAR(COUNT(*)), '-' FROM v$standby_log;

PROMPT @@@FILE:bp_instalacao.txt@@@
PROMPT === CONFIGURACAO DE INSTALACAO ===
SELECT 'spfile_em_uso' AS item, CAST(NVL2((SELECT value FROM v$parameter WHERE name='spfile'),'SIM','NAO') AS VARCHAR2(60)) AS valor FROM dual UNION ALL SELECT 'compatible', CAST(SUBSTR((SELECT value FROM v$parameter WHERE name='compatible'),1,60) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'versao_banco', CAST(version AS VARCHAR2(60)) FROM v$instance UNION ALL SELECT 'compatible_alinhado', CAST(CASE WHEN SUBSTR(c.value,1,INSTR(c.value||'.','.',1,2)-1) = SUBSTR(i.version,1,INSTR(i.version||'.','.',1,2)-1) THEN 'SIM' ELSE 'NAO' END AS VARCHAR2(60)) FROM (SELECT value FROM v$parameter WHERE name='compatible') c, v$instance i UNION ALL SELECT 'dst_upgrade_pendente', CAST(NVL((SELECT CASE WHEN property_value='NONE' THEN 'NAO' ELSE 'SIM' END FROM database_properties WHERE property_name='DST_UPGRADE_STATE'),'NAO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'underscore_parametros_setados', CAST(TO_CHAR((SELECT COUNT(*) FROM v$spparameter WHERE name LIKE '\_%' ESCAPE '\' AND isspecified='TRUE')) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'autotask_stats', CAST(NVL((SELECT MAX(status) FROM dba_autotask_client WHERE client_name='auto optimizer stats collection'),'DESCONHECIDO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'autotask_space', CAST(NVL((SELECT MAX(status) FROM dba_autotask_client WHERE client_name='auto space advisor'),'DESCONHECIDO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'stats_estimate_percent', CAST(SUBSTR(DBMS_STATS.GET_PREFS('ESTIMATE_PERCENT'),1,60) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'stats_method_opt', CAST(SUBSTR(DBMS_STATS.GET_PREFS('METHOD_OPT'),1,60) AS VARCHAR2(60)) FROM dual;

PROMPT @@@FILE:bp_ha.txt@@@
PROMPT === RECUPERABILIDADE E HA ===
SELECT LOWER(name) AS item, CAST(SUBSTR(NVL(value,'(nulo)'),1,60) AS VARCHAR2(60)) AS valor FROM v$parameter WHERE name IN ('standby_file_management','log_archive_config','archive_lag_target','log_archive_max_processes','db_flashback_retention_target') UNION ALL SELECT 'standby_configurado', CAST(NVL2((SELECT MAX(dest_id) FROM v$archive_dest WHERE target='STANDBY' AND destination IS NOT NULL),'SIM','NAO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'flashback_on', CAST(flashback_on AS VARCHAR2(60)) FROM v$database UNION ALL SELECT 'undo_autoextend', CAST(NVL((SELECT CASE WHEN MIN(autoextensible)='YES' THEN 'SIM' ELSE 'NAO' END FROM dba_data_files WHERE tablespace_name=(SELECT UPPER(value) FROM v$parameter WHERE name='undo_tablespace')),'DESCONHECIDO') AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'default_temp_tablespace', CAST(SUBSTR(NVL((SELECT property_value FROM database_properties WHERE property_name='DEFAULT_TEMP_TABLESPACE'),'(nulo)'),1,60) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'default_permanent_tablespace', CAST(SUBSTR(NVL((SELECT property_value FROM database_properties WHERE property_name='DEFAULT_PERMANENT_TABLESPACE'),'(nulo)'),1,60) AS VARCHAR2(60)) FROM dual;

PROMPT @@@FILE:bp_seguranca.txt@@@
PROMPT === SEGURANCA E CONFORMIDADE ===
SELECT 'contas_senha_default' AS item, CAST(TO_CHAR((SELECT COUNT(*) FROM dba_users_with_defpwd d JOIN dba_users u ON u.username = d.username WHERE u.account_status='OPEN')) AS VARCHAR2(60)) AS valor FROM dual UNION ALL SELECT 'profiles_senha_sem_expiracao', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_profiles WHERE resource_name='PASSWORD_LIFE_TIME' AND limit='UNLIMITED')) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'profiles_sem_limite_de_tentativas', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_profiles WHERE resource_name='FAILED_LOGIN_ATTEMPTS' AND limit='UNLIMITED')) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'grants_perigosos_a_public', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_tab_privs WHERE grantee='PUBLIC' AND privilege='EXECUTE' AND table_name IN ('UTL_FILE','UTL_TCP','UTL_HTTP','UTL_SMTP','UTL_INADDR'))) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'usuarios_com_role_dba', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_role_privs WHERE granted_role='DBA' AND grantee NOT IN ('SYS','SYSTEM'))) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'audit_sys_operations', CAST(SUBSTR(NVL((SELECT value FROM v$parameter WHERE name='audit_sys_operations'),'DESCONHECIDO'),1,60) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'unified_auditing', CAST(NVL((SELECT value FROM v$option WHERE parameter='Unified Auditing'),'N/A') AS VARCHAR2(60)) FROM dual;

PROMPT @@@FILE:bp_estrutura.txt@@@
PROMPT === ESTRUTURA E CONTENCAO ===
SELECT 'objetos_usuario_em_system' AS item, CAST(TO_CHAR((SELECT COUNT(*) FROM dba_segments WHERE tablespace_name='SYSTEM' AND owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','SYSMAN','WMSYS','XDB','CTXSYS','MDSYS','OLAPSYS','EXFSYS','ORDSYS','ORDDATA','APPQOSSYS','AUDSYS','LBACSYS','DVSYS','OJVMSYS','GSMADMIN_INTERNAL','ORACLE_OCM','SI_INFORMTN_SCHEMA','DIP','SYSBACKUP','SYSDG','SYSKM','SYSRAC','GGSYS','MDDATA','REMOTE_SCHEDULER_AGENT'))) AS VARCHAR2(60)) AS valor FROM dual UNION ALL SELECT 'objetos_usuario_em_sysaux', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_segments WHERE tablespace_name='SYSAUX' AND owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','SYSMAN','WMSYS','XDB','CTXSYS','MDSYS','OLAPSYS','EXFSYS','ORDSYS','ORDDATA','APPQOSSYS','AUDSYS','LBACSYS','DVSYS','OJVMSYS','GSMADMIN_INTERNAL','ORACLE_OCM','SI_INFORMTN_SCHEMA','DIP','SYSBACKUP','SYSDG','SYSKM','SYSRAC','GGSYS','MDDATA','REMOTE_SCHEDULER_AGENT'))) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'tablespaces_dictionary_managed', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_tablespaces WHERE extent_management='DICTIONARY')) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'tablespaces_sem_assm', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_tablespaces WHERE contents='PERMANENT' AND segment_space_management='MANUAL' AND tablespace_name NOT IN ('SYSTEM','SYSAUX'))) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'sequences_nocache', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_sequences WHERE cache_size=0 AND sequence_owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','SYSMAN','WMSYS','XDB','CTXSYS','MDSYS','OLAPSYS','EXFSYS','ORDSYS','ORDDATA','APPQOSSYS','AUDSYS','LBACSYS','DVSYS','OJVMSYS','GSMADMIN_INTERNAL','ORACLE_OCM','DIP'))) AS VARCHAR2(60)) FROM dual UNION ALL SELECT 'sequences_cache_baixo', CAST(TO_CHAR((SELECT COUNT(*) FROM dba_sequences WHERE cache_size BETWEEN 1 AND 19 AND sequence_owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','SYSMAN','WMSYS','XDB','CTXSYS','MDSYS','OLAPSYS','EXFSYS','ORDSYS','ORDDATA','APPQOSSYS','AUDSYS','LBACSYS','DVSYS','OJVMSYS','GSMADMIN_INTERNAL','ORACLE_OCM','DIP'))) AS VARCHAR2(60)) FROM dual;

PROMPT @@@FILE:jobs.txt@@@
PROMPT === JOBS COM FALHA (7 dias) ===
SELECT owner, job_name, status, COUNT(*) AS falhas, TO_CHAR(MAX(log_date),'YYYY-MM-DD HH24:MI') AS ultima, SUBSTR(MAX(additional_info),1,60) AS erro FROM dba_scheduler_job_run_details WHERE log_date > SYSDATE-7 AND status <> 'SUCCEEDED' GROUP BY owner, job_name, status ORDER BY falhas DESC;

PROMPT @@@FILE:undo_errors.txt@@@
PROMPT === UNDO: ORA-01555 E ESPACO ===
SELECT SUM(ssolderrcnt) AS ora_01555, SUM(nospaceerrcnt) AS sem_espaco, MAX(maxquerylen) AS maior_query_seg, MAX(tuned_undoretention) AS retencao_efetiva_seg FROM v$undostat WHERE begin_time > SYSDATE-1;

PROMPT @@@FILE:osstat.txt@@@
PROMPT === RECURSOS DO HOST ===
SELECT inst_id, stat_name, value FROM gv$osstat WHERE stat_name IN ('NUM_CPUS','NUM_CPU_CORES','LOAD','PHYSICAL_MEMORY_BYTES','FREE_MEMORY_BYTES','VM_IN_BYTES','VM_OUT_BYTES') ORDER BY inst_id, stat_name;

PROMPT @@@FILE:parse.txt@@@
PROMPT === PARSE (ACUMULADO DESDE O STARTUP) ===
SELECT inst_id, name AS estatistica, value AS valor FROM gv$sysstat WHERE name IN ('parse count (total)','parse count (hard)','parse count (failures)','execute count','session cursor cache hits') ORDER BY inst_id, name;
PROMPT === SHARED POOL LIVRE ===
SELECT inst_id, ROUND(SUM(bytes)/1048576,1) AS total_mb, ROUND(SUM(CASE WHEN name='free memory' THEN bytes ELSE 0 END)/1048576,1) AS livre_mb, ROUND(100*SUM(CASE WHEN name='free memory' THEN bytes ELSE 0 END)/SUM(bytes),1) AS livre_pct FROM gv$sgastat WHERE pool = 'shared pool' GROUP BY inst_id ORDER BY inst_id;

PROMPT @@@FILE:literais.txt@@@
PROMPT === SQL SEM BIND (AGRUPADO POR ASSINATURA) ===
SELECT * FROM (SELECT force_matching_signature AS assinatura, COUNT(*) AS versoes, SUM(executions) AS execucoes, ROUND(SUM(sharable_mem)/1048576,1) AS mb_shared_pool, MAX(SUBSTR(REPLACE(REPLACE(sql_text,CHR(10),' '),CHR(13),' '),1,60)) AS exemplo FROM gv$sql WHERE force_matching_signature > 0 AND parsing_schema_name NOT IN ('SYS','SYSTEM','DBSNMP','SYSMAN') GROUP BY force_matching_signature HAVING COUNT(*) > 20 ORDER BY COUNT(*) DESC) WHERE ROWNUM <= 15;

PROMPT @@@FILE:redo_waits.txt@@@
PROMPT === ESPERAS DE REDO ===
SELECT inst_id, event, total_waits, ROUND(time_waited_micro/1000000,1) AS segundos, ROUND(time_waited_micro/DECODE(total_waits,0,1,total_waits)/1000,2) AS ms_medio FROM gv$system_event WHERE event IN ('log file sync','log file parallel write','log file switch completion','log file switch (checkpoint incomplete)','log file switch (archiving needed)','log buffer space') ORDER BY inst_id, time_waited_micro DESC;

PROMPT @@@FILE:deadlocks.txt@@@
PROMPT === DEADLOCKS (ORA-00060) ===
SELECT * FROM (SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD HH24:MI') AS quando, SUBSTR(TRIM(REPLACE(REPLACE(message_text,CHR(10),' '),CHR(13),' ')),1,110) AS mensagem FROM v$diag_alert_ext WHERE originating_timestamp > SYSTIMESTAMP - NUMTODSINTERVAL(7,'DAY') AND message_text LIKE '%ORA-00060%' ORDER BY originating_timestamp DESC) WHERE ROWNUM <= 20;

PROMPT @@@FILE:archive_rate.txt@@@
PROMPT === ARCHIVE POR HORA (24h) ===
SELECT * FROM (SELECT TO_CHAR(completion_time,'YYYY-MM-DD HH24') AS hora, COUNT(*) AS arquivos, ROUND(SUM(blocks*block_size)/1048576) AS mb FROM v$archived_log WHERE completion_time > SYSDATE-1 AND dest_id = (SELECT MIN(dest_id) FROM v$archived_log WHERE completion_time > SYSDATE-1) GROUP BY TO_CHAR(completion_time,'YYYY-MM-DD HH24') ORDER BY hora DESC) WHERE ROWNUM <= 26;

SPOOL OFF
PROMPT
PROMPT === COLETA CONCLUIDA: copie coletor_troubleshoot.txt para troubleshooting\<caso>\ ===
