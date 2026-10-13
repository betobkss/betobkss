-- ============================================================
-- sessions.sql - Top sessoes ativas por tempo de espera
-- Salve como: troubleshooting\<caso>\sessions.txt
-- ============================================================
SET LINESIZE 240 PAGESIZE 200 TRIMSPOOL ON

SELECT
    s.inst_id                          AS inst,
    s.sid || ',' || s.serial#          AS sid,
    s.username                         AS username,
    s.status                           AS status,
    s.event                            AS event,
    s.seconds_in_wait                  AS secs,
    s.sql_id                           AS sql_id
FROM   gv$session s
WHERE  s.type = 'USER'
AND    s.status = 'ACTIVE'
AND    s.username IS NOT NULL
ORDER  BY s.seconds_in_wait DESC
FETCH FIRST 20 ROWS ONLY;
