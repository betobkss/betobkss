-- ############################################################
-- # 10 - ACOES DE SQL TUNING  (NAO E READ-ONLY)
-- # ATENCAO: cria tasks, aceita SQL Profile e coleta estatisticas,
-- # o que MUDA planos de execucao. Sob Matriz de Risco:
-- #   aviso de impacto + plano de rollback + APROVACAO explicita.
-- # Trocar &sql_id / owner / tabela conforme o caso.
-- # SQL Tuning Advisor exige TUNING PACK (licenca Oracle).
-- ############################################################
SET SERVEROUTPUT ON SIZE UNLIMITED
SET LONG 1000000 LONGCHUNKSIZE 1000 LINESIZE 200 PAGESIZE 0

-- 1) Criar a tuning task para um SQL_ID
-- [ACAO] cria objeto de advisor (reversivel via DROP no passo 5)
DECLARE
  t VARCHAR2(40);
BEGIN
  t := DBMS_SQLTUNE.CREATE_TUNING_TASK(
         sql_id    => '&sql_id',
         task_name => 'tt_&sql_id',
         description => 'Tuning do SQL &sql_id');
  DBMS_OUTPUT.PUT_LINE('Task: '||t);
END;
/

-- 2) Executar o advisor
-- [ACAO] consome recursos; nao altera planos ainda
BEGIN
  DBMS_SQLTUNE.EXECUTE_TUNING_TASK(task_name => 'tt_&sql_id');
END;
/

-- 3) Ver o relatorio (read-only) - avaliar recomendacoes ANTES de aplicar
SELECT DBMS_SQLTUNE.REPORT_TUNING_TASK('tt_&sql_id') AS relatorio FROM dual;

-- 4) [ACAO DESTRUTIVA/MUDA PLANO] Aceitar o SQL Profile recomendado
--    So execute apos aprovacao. Rollback: DBMS_SQLTUNE.DROP_SQL_PROFILE(name=>...)
-- BEGIN
--   DBMS_SQLTUNE.ACCEPT_SQL_PROFILE(
--     task_name => 'tt_&sql_id',
--     name      => 'prof_&sql_id',
--     force_match => TRUE);
-- END;
-- /

-- 5) Coletar estatisticas de uma tabela (opcional) [ACAO - MUDA PLANOS]
--    Rollback: restaurar via DBMS_STATS.RESTORE_TABLE_STATS (historico de stats).
-- BEGIN
--   DBMS_STATS.GATHER_TABLE_STATS(
--     ownname => '&owner', tabname => '&tabela',
--     method_opt => 'FOR ALL COLUMNS SIZE AUTO', cascade => TRUE);
-- END;
-- /

-- 6) Remover a tuning task (limpeza)
-- EXEC DBMS_SQLTUNE.DROP_TUNING_TASK('tt_&sql_id');

-- Alternativa oficial read-only (recomendacoes atuais) - rodar no SQL*Plus:
-- @?/rdbms/admin/sqltrpt.sql
