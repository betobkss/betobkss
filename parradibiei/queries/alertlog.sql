-- ============================================================
-- alertlog.sql - Erros recentes do Alert Log (via V$DIAG_ALERT_EXT)
-- ------------------------------------------------------------
-- Alternativa a copiar o arquivo alert.log. Traz as mensagens das
-- ultimas 24h que contem 'ORA-'. Salve a saida como:
--    troubleshooting\<caso>\alert.log   (ou alert.txt)
-- ============================================================
SET LINESIZE 300 PAGESIZE 0 TRIMSPOOL ON

SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD"T"HH24:MI:SS')
       || '  ' || message_text AS alert_line
FROM   v$diag_alert_ext
WHERE  originating_timestamp > SYSTIMESTAMP - INTERVAL '24' HOUR
AND    message_text LIKE '%ORA-%'
ORDER  BY originating_timestamp;

-- Dica: para uma janela maior troque INTERVAL '24' HOUR por '7' DAY.
