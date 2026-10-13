-- ============================================================
-- dataguard.sql - Data Guard lag e managed standby
-- Salve como: troubleshooting\<caso>\dataguard.txt
-- (rodar no STANDBY para apply lag; PRIMARY para transport)
-- ============================================================
SET LINESIZE 200 PAGESIZE 200 TRIMSPOOL ON

PROMPT === DATAGUARD STATS (lag) ===
SELECT name, value, time_computed
FROM   gv$dataguard_stats
WHERE  name IN ('transport lag','apply lag');

PROMPT
PROMPT === MANAGED STANDBY ===
SELECT inst_id, process, status, thread#, sequence#, block#
FROM   gv$managed_standby
WHERE  process LIKE 'MRP%' OR process LIKE 'RFS%' OR status <> 'IDLE'
ORDER  BY inst_id, process;
