-- ============================================================
-- locks.sql - Sessoes bloqueadas / bloqueadoras (blocking sessions)
-- ------------------------------------------------------------
-- Rode no seu cliente (Citrix) e salve a saida como texto:
--    troubleshooting\<caso>\locks.txt
-- Dica SQL*Plus: SET LINESIZE 200 PAGESIZE 100 TRIMSPOOL ON
-- ============================================================
SET LINESIZE 220 PAGESIZE 200 TRIMSPOOL ON

SELECT
    bs.inst_id                         AS blocking_inst,
    bs.sid || ',' || bs.serial#        AS blocking_session,
    ws.inst_id                         AS waiting_inst,
    ws.sid || ',' || ws.serial#        AS waiting_session,
    ws.event                           AS event,
    o.owner || '.' || o.object_name    AS object,
    ws.seconds_in_wait                 AS secs,
    bs.username                        AS blocking_user,
    ws.username                        AS waiting_user,
    bs.sql_id                          AS blocking_sql_id
FROM   gv$session ws
JOIN   gv$session bs
       ON  bs.inst_id = ws.blocking_instance
       AND bs.sid     = ws.blocking_session
LEFT   JOIN dba_objects o
       ON  o.object_id = ws.row_wait_obj#
WHERE  ws.blocking_session IS NOT NULL
ORDER  BY ws.seconds_in_wait DESC;
