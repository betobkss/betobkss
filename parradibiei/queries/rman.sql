-- ============================================================
-- rman.sql - Status dos jobs de backup RMAN (ultimos 7 dias)
-- Salve como: troubleshooting\<caso>\rman.txt
-- ============================================================
SET LINESIZE 220 PAGESIZE 200 TRIMSPOOL ON

SELECT
    input_type                                            AS input_type,
    status                                                AS status,
    TO_CHAR(start_time,'YYYY-MM-DD HH24:MI')              AS start_time,
    TO_CHAR(end_time,'YYYY-MM-DD HH24:MI')                AS end_time,
    ROUND(input_bytes/1024/1024/1024, 1)                  AS input_gb,
    ROUND(output_bytes/1024/1024/1024, 1)                 AS output_gb,
    time_taken_display                                    AS elapsed
FROM   v$rman_backup_job_details
WHERE  start_time > SYSDATE - 7
ORDER  BY start_time DESC;
