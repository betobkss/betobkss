-- ============================================================
-- iostat.sql - Latencia de I/O por datafile (v$filestat)
-- Salve como: troubleshooting\<caso>\iostat.txt
-- ============================================================
SET LINESIZE 220 PAGESIZE 200 TRIMSPOOL ON

SELECT
    df.tablespace_name                                    AS tablespace,
    SUBSTR(df.file_name, -40)                             AS file_name,
    fs.phyrds                                             AS phyrds,
    fs.phywrts                                            AS phywrts,
    ROUND(fs.readtim*10/GREATEST(fs.phyrds,1), 2)         AS avg_read_ms,
    ROUND(fs.writetim*10/GREATEST(fs.phywrts,1), 2)       AS avg_write_ms
FROM   gv$filestat fs
JOIN   dba_data_files df ON df.file_id = fs.file#
ORDER  BY avg_read_ms DESC
FETCH FIRST 20 ROWS ONLY;
