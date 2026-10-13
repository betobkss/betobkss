-- ============================================================
-- sqlmon.sql - Relatorio SQL Monitor em TEXTO para um SQL_ID
-- ------------------------------------------------------------
-- Substitua &SQLID pelo SQL_ID alvo (ex.: g22ss9cm6vyp5).
-- Salve a saida como: troubleshooting\<caso>\sqlmon.txt
-- (o formato TEXT e o mais facil de parsear pelo framework)
-- ============================================================
SET LINESIZE 250 PAGESIZE 0 LONG 2000000 LONGCHUNKSIZE 2000000 TRIMSPOOL ON

SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(
           sql_id       => '&SQLID',
           type         => 'TEXT',
           report_level => 'ALL'
       ) AS sql_monitor_report
FROM dual;

-- Alternativa (ultimo SQL monitorado, se nao souber o SQL_ID):
-- SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(type=>'TEXT', report_level=>'ALL')
-- FROM dual;
