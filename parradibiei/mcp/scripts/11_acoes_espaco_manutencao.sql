-- ############################################################
-- # 11 - ACOES DE ESPACO / MANUTENCAO  (NAO E READ-ONLY)
-- # ATENCAO: comandos DESTRUTIVOS/MUTANTES. Sob Matriz de Risco:
-- #   aviso de impacto + plano de rollback + APROVACAO explicita.
-- # Antes de resize/add: confira espaco no diskgroup (v$asm_diskgroup).
-- # Ajuste nomes, IDs e caminhos ('+DG_...') ao ambiente.
-- ############################################################

-- === DATAFILE ===
-- [ACAO] Aumentar datafile (rollback: RESIZE de volta ao tamanho anterior)
-- ALTER DATABASE DATAFILE 12 RESIZE 16384M;
-- [ACAO] Ativar autoextend com teto
-- ALTER DATABASE DATAFILE 12 AUTOEXTEND ON NEXT 1024M MAXSIZE 32767M;
-- [ACAO] Adicionar datafile a uma tablespace
-- ALTER TABLESPACE USERS ADD DATAFILE '+DG_DADOS' SIZE 10000M AUTOEXTEND ON NEXT 1000M MAXSIZE 32767M;
-- [ACAO] Mover datafile (online, 12c+)
-- ALTER DATABASE MOVE DATAFILE 15 TO '+DG_DESTINO';

-- Gerar comando de RESIZE ao minimo (HWM) - APENAS GERA (read-only), revisar antes:
SET LINESIZE 200 PAGESIZE 0 FEEDBACK OFF
SELECT 'ALTER DATABASE DATAFILE '''||a.file_name||''' RESIZE '||
       CEIL((NVL(h.hwm,1)*t.block_size)/1048576)||'M;  -- atual '||
       ROUND(a.bytes/1048576)||'M'
FROM   dba_data_files a
JOIN   dba_tablespaces t ON t.tablespace_name = a.tablespace_name
LEFT JOIN (SELECT file_id, MAX(block_id+blocks-1) hwm FROM dba_extents GROUP BY file_id) h
       ON h.file_id = a.file_id
WHERE  a.bytes/1048576 - CEIL((NVL(h.hwm,1)*t.block_size)/1048576) > 500
ORDER  BY a.bytes DESC;

-- === TEMP ===
-- [ACAO] Adicionar tempfile / redimensionar
-- ALTER TABLESPACE TEMP ADD TEMPFILE '+DG_DADOS' SIZE 30000M AUTOEXTEND OFF;
-- ALTER DATABASE TEMPFILE 3 RESIZE 43500M;
-- [ACAO DESTRUTIVA] Recriar TEMP (troca a default; so com aprovacao)
-- CREATE TEMPORARY TABLESPACE TEMP2 TEMPFILE '+DG_DADOS' SIZE 30000M AUTOEXTEND OFF;
-- ALTER DATABASE DEFAULT TEMPORARY TABLESPACE TEMP2;
-- DROP TABLESPACE TEMP INCLUDING CONTENTS AND DATAFILES;

-- === UNDO ===
-- [ACAO] Ajustar retencao (rollback: voltar valor anterior)
-- ALTER SYSTEM SET UNDO_RETENTION = 3600 SCOPE=BOTH;
-- [ACAO DESTRUTIVA] Recriar UNDO (so com aprovacao; garantir undo novo ativo antes do drop)
-- CREATE UNDO TABLESPACE UNDOTBS_NEW DATAFILE '+DG_DADOS' SIZE 10000M AUTOEXTEND ON;
-- ALTER SYSTEM SET UNDO_TABLESPACE = UNDOTBS_NEW SCOPE=BOTH;
-- DROP TABLESPACE UNDOTBS1 INCLUDING CONTENTS AND DATAFILES;

-- === SHARED POOL / SESSAO ===
-- Gera o comando de PURGE de um cursor especifico (read-only; executar o retorno e ACAO)
SELECT 'exec DBMS_SHARED_POOL.PURGE ('''||address||','||hash_value||''',''C'');'
FROM   v$sqlarea WHERE sql_id = '&sql_id';
-- [ACAO DESTRUTIVA] Encerrar sessao (confirmar com a aplicacao; sem rollback):
-- ALTER SYSTEM KILL SESSION 'sid,serial#,@inst' IMMEDIATE;
