-- ============================================================
-- space.sql - Uso de Tablespace e ASM Diskgroups
-- ------------------------------------------------------------
-- Salve a saida como: troubleshooting\<caso>\space.txt
-- SET LINESIZE 200 PAGESIZE 200 TRIMSPOOL ON
-- ============================================================
SET LINESIZE 200 PAGESIZE 200 TRIMSPOOL ON

PROMPT === TABLESPACES ===
SELECT
    df.tablespace_name                                   AS tablespace,
    ROUND(df.total_mb/1024, 1)                           AS total_gb,
    ROUND((df.total_mb - NVL(fs.free_mb,0))/1024, 1)     AS used_gb,
    ROUND(NVL(fs.free_mb,0)/1024, 1)                     AS free_gb,
    ROUND((df.total_mb - NVL(fs.free_mb,0))/df.total_mb*100, 1) AS pct_used
FROM (
    SELECT tablespace_name, SUM(bytes)/1024/1024 total_mb
    FROM dba_data_files GROUP BY tablespace_name
) df
LEFT JOIN (
    SELECT tablespace_name, SUM(bytes)/1024/1024 free_mb
    FROM dba_free_space GROUP BY tablespace_name
) fs ON fs.tablespace_name = df.tablespace_name
ORDER BY pct_used DESC;

PROMPT
PROMPT === ASM DISKGROUPS ===
SELECT
    name                                       AS diskgroup,
    ROUND(total_mb/1024, 1)                    AS total_gb,
    ROUND((total_mb - free_mb)/1024, 1)        AS used_gb,
    ROUND(free_mb/1024, 1)                     AS free_gb,
    ROUND((total_mb - free_mb)/total_mb*100,1) AS pct_used
FROM v$asm_diskgroup
ORDER BY pct_used DESC;
