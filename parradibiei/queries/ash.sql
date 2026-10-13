-- ============================================================
-- ash.sql - Distribuicao de esperas (ASH, ultimos 60 min)
-- Salve como: troubleshooting\<caso>\ash.txt
-- ============================================================
SET LINESIZE 200 PAGESIZE 200 TRIMSPOOL ON

PROMPT === TOP WAIT CLASSES (ASH 60 min) ===
SELECT NVL(wait_class,'CPU') AS wait_class, COUNT(*) AS samples
FROM   gv$active_session_history
WHERE  sample_time > SYSDATE - 1/24
GROUP  BY NVL(wait_class,'CPU')
ORDER  BY samples DESC;

PROMPT
PROMPT === TOP EVENTS (ASH 60 min) ===
SELECT NVL(event,'ON CPU') AS event, COUNT(*) AS samples
FROM   gv$active_session_history
WHERE  sample_time > SYSDATE - 1/24
GROUP  BY NVL(event,'ON CPU')
ORDER  BY samples DESC FETCH FIRST 15 ROWS ONLY;
