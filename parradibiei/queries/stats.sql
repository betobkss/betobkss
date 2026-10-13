-- ============================================================
-- stats.sql - Estatisticas STALE / antigas do otimizador
-- Salve como: troubleshooting\<caso>\stats.txt
-- ============================================================
SET LINESIZE 200 PAGESIZE 200 TRIMSPOOL ON

SELECT
    owner || '.' || table_name                          AS table_name,
    TO_CHAR(last_analyzed,'YYYY-MM-DD HH24:MI')          AS last_analyzed,
    stale_stats                                          AS stale_stats,
    num_rows                                             AS num_rows
FROM   dba_tab_statistics
WHERE  owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','DBSNMP')
AND    (stale_stats = 'YES' OR last_analyzed < SYSDATE - 7 OR last_analyzed IS NULL)
ORDER  BY last_analyzed NULLS FIRST
FETCH FIRST 30 ROWS ONLY;
