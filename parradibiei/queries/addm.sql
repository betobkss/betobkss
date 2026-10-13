-- ============================================================
-- addm.sql - Relatorio ADDM em TEXTO
-- ------------------------------------------------------------
-- Gera o ADDM do ultimo periodo entre os 2 snapshots mais recentes.
-- Salve a saida como: troubleshooting\<caso>\addm.txt
-- ============================================================
SET LINESIZE 250 PAGESIZE 0 LONG 2000000 LONGCHUNKSIZE 2000000 SERVEROUTPUT ON

DECLARE
    l_task   VARCHAR2(100) := 'ADDM_TS_' || TO_CHAR(SYSDATE,'YYYYMMDD_HH24MISS');
    l_beg    NUMBER;
    l_end    NUMBER;
BEGIN
    SELECT MIN(snap_id), MAX(snap_id) INTO l_beg, l_end
    FROM (
        SELECT snap_id FROM dba_hist_snapshot
        ORDER BY snap_id DESC FETCH FIRST 2 ROWS ONLY
    );

    DBMS_ADDM.ANALYZE_DB(l_task, l_beg, l_end);
    DBMS_OUTPUT.PUT_LINE(DBMS_ADDM.GET_REPORT(l_task));
END;
/

-- Se preferir o relatorio classico:
-- @?/rdbms/admin/addmrpt.sql
