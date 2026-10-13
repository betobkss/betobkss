-- ============================================================
-- advisor.sql - SQL TUNING ADVISOR pelo SQL*Plus (Citrix)
--
-- >>> ESTE SCRIPT **ESCREVE** NO BANCO. NAO E READ-ONLY. <<<
--
-- Ele cria e executa tuning tasks (DBMS_SQLTUNE) e consome
-- **Oracle Tuning Pack**. E o unico script do framework que escreve -
-- todo o resto (coletor.sql) e SELECT puro em sessao READ ONLY.
-- Por isso ele e um arquivo SEPARADO: o coletor.sql abre a sessao com
-- SET TRANSACTION READ ONLY e o advisor falharia ali com ORA-01456.
--
-- SO RODE COM APROVACAO EXPLICITA e depois de confirmar que este banco
-- licencia Tuning Pack.
--
-- PRE-REQUISITOS
--   - Tuning Pack licenciado (o advisor em si).
--   - Diagnostics Pack licenciado (a selecao le DBA_HIST_SQLSTAT).
--   - Privilegio ADVISOR no usuario. O 'dbaread' de leitura normalmente
--     NAO tem: se vier ORA-01031, use um usuario com ADVISOR.
--
-- USO
--   1) @coletor.sql   -> gera coletor_troubleshoot.txt   (read-only)
--   2) @advisor.sql   -> gera advisor_troubleshoot.txt   (escreve)
--   3) copie OS DOIS .txt para troubleshooting\<caso>\
--   4) .\redija.bat <caso>
--
-- O QUE ELE FAZ
--   Seleciona no maximo 5 SQL entre os 10 mais regredidos dos ultimos
--   7 dias e roda o advisor so neles. Criterio (igual ao do --advisor
--   do COLETAR.py): TROCA DE PLANO, ou prejuizo >= 300s.
--   Regressao de TEMPO com pouco impacto fica de fora de proposito: ali
--   o advisor so responderia "colete estatisticas", que o acoes.sql ja
--   gera de graca e sem escrever nada.
--
-- LIMPEZA
--   Cada tuning task e removida (DROP_TUNING_TASK) ao fim do SQL.
--   EXCECAO: quando o advisor propoe um SQL Profile, a task e MANTIDA -
--   o accept_sql_profile referencia a task pelo nome e sem ela a
--   recomendacao fica inexecutavel. O script imprime o DROP para voce
--   rodar depois de decidir. Confira no fim do spool.
-- ============================================================
SET SERVEROUTPUT ON SIZE UNLIMITED FORMAT WRAPPED
SET LINESIZE 32767 PAGESIZE 0 TRIMSPOOL ON TRIMOUT ON
SET FEEDBACK OFF ECHO OFF VERIFY OFF HEADING OFF TAB OFF
SET LONG 2000000 LONGCHUNKSIZE 2000000
ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS';

SPOOL advisor_troubleshoot.txt

PROMPT @@@FILE:advisor.txt@@@

DECLARE
  -- Iguais a troubleshoot/sqlhc.py. Se mudar la, mude aqui: o relatorio
  -- marca o SQL como critico com base no MESMO limiar, e divergir faz o
  -- dashboard oferecer advisor num SQL que este script vai recusar.
  c_impacto_min  CONSTANT NUMBER       := 300;   -- ADVISOR_IMPACTO_MIN_S
  c_max_sqls     CONSTANT PLS_INTEGER  := 5;     -- ADVISOR_MAX_SQLS
  c_top_n        CONSTANT PLS_INTEGER  := 10;    -- TOP_N
  c_limite_min   CONSTANT PLS_INTEGER  := 5;     -- limite por SQL

  v_task    VARCHAR2(128);
  v_rel     CLOB;
  v_manter  BOOLEAN;
  v_n       PLS_INTEGER := 0;
  v_erro    VARCHAR2(4000);

  CURSOR c_alvos IS
    SELECT sql_id, tipo, impacto_s FROM (
      SELECT sql_id, schema, plano_base, plano_rec, execs_rec,
             avg_s_base, avg_s_rec,
             ROUND((avg_s_rec - avg_s_base) * execs_rec, 1) AS impacto_s,
             CASE WHEN plano_mudou = 1 THEN 'PLANO' ELSE 'TEMPO' END AS tipo
        FROM (
          SELECT sql_id, schema, plano_base, plano_rec, execs_rec,
                 avg_s_base, avg_s_rec,
                 CASE WHEN NVL(plano_base,0) <> 0 AND NVL(plano_rec,0) <> 0
                           AND plano_base <> plano_rec THEN 1 ELSE 0 END AS plano_mudou
            FROM (
              SELECT sql_id,
                     MAX(schema) AS schema,
                     MAX(CASE WHEN periodo='BASE' THEN phv END) AS plano_base,
                     MAX(CASE WHEN periodo='REC'  THEN phv END) AS plano_rec,
                     SUM(CASE WHEN periodo='REC'  THEN execs END) AS execs_rec,
                     ROUND(SUM(CASE WHEN periodo='BASE' THEN elapsed END)/1000000
                           /NULLIF(SUM(CASE WHEN periodo='BASE' THEN execs END),0),3) AS avg_s_base,
                     ROUND(SUM(CASE WHEN periodo='REC' THEN elapsed END)/1000000
                           /NULLIF(SUM(CASE WHEN periodo='REC' THEN execs END),0),3) AS avg_s_rec
                FROM (SELECT ss.sql_id, ss.plan_hash_value AS phv,
                             ss.parsing_schema_name AS schema,
                             CASE WHEN sn.begin_interval_time > SYSDATE-1
                                  THEN 'REC' ELSE 'BASE' END AS periodo,
                             ss.executions_delta AS execs,
                             ss.elapsed_time_delta AS elapsed
                        FROM dba_hist_sqlstat ss
                        JOIN dba_hist_snapshot sn
                          ON ss.snap_id = sn.snap_id
                         AND ss.instance_number = sn.instance_number
                       WHERE sn.begin_interval_time > SYSDATE-7
                         AND ss.executions_delta > 0
                         AND ss.parsing_schema_name NOT IN
                             ('SYS','SYSTEM','DBSNMP','SYSMAN','XDB','WMSYS','CTXSYS',
                              'MDSYS','ORDSYS','OUTLN','APPQOSSYS','GSMADMIN_INTERNAL',
                              'AUDSYS','DVSYS','LBACSYS','OJVMSYS','ORACLE_OCM',
                              'DBSFWUSER','REMOTE_SCHEDULER_AGENT','ORDDATA','OLAPSYS',
                              'APEX_040000','FLOWS_FILES','SI_INFORMTN_SCHEMA','ANONYMOUS'))
               GROUP BY sql_id
              HAVING SUM(CASE WHEN periodo='REC'  THEN execs END) > 0
                 AND SUM(CASE WHEN periodo='BASE' THEN execs END) > 0
            )
        )
       WHERE avg_s_rec >= 0.05
         AND avg_s_rec - avg_s_base >= 0.05
         AND NVL(plano_rec,0) <> 0
       ORDER BY (avg_s_rec - avg_s_base) * execs_rec DESC
    ) WHERE ROWNUM <= c_top_n;

  -- O REPORT_TUNING_TASK vem como CLOB de centenas de KB. DBMS_OUTPUT nao
  -- aceita CLOB nem linha acima de 32767, entao sai quebrado por \n.
  PROCEDURE imprime_clob(p_clob IN CLOB) IS
    v_off  PLS_INTEGER := 1;
    v_len  PLS_INTEGER := DBMS_LOB.GETLENGTH(p_clob);
    v_nl   PLS_INTEGER;
    v_linha VARCHAR2(32767);
  BEGIN
    WHILE v_off <= v_len LOOP
      v_nl := DBMS_LOB.INSTR(p_clob, CHR(10), v_off);
      IF v_nl = 0 OR v_nl - v_off > 32000 THEN
        v_linha := DBMS_LOB.SUBSTR(p_clob, LEAST(32000, v_len - v_off + 1), v_off);
        v_off   := v_off + LENGTH(v_linha);
      ELSE
        IF v_nl > v_off THEN
          v_linha := DBMS_LOB.SUBSTR(p_clob, v_nl - v_off, v_off);
        ELSE
          v_linha := NULL;
        END IF;
        v_off := v_nl + 1;
      END IF;
      DBMS_OUTPUT.PUT_LINE(RTRIM(NVL(v_linha, ' '), CHR(13)));
    END LOOP;
  END imprime_clob;

BEGIN
  DBMS_OUTPUT.PUT_LINE('=== SQL TUNING ADVISOR (executado por advisor.sql) ===');

  FOR a IN c_alvos LOOP
    -- Mesmo filtro do _advisor_elegiveis: troca de plano rende SQL Profile;
    -- fora disso so vale se o prejuizo sozinho paga o custo da licenca.
    CONTINUE WHEN NOT (a.tipo = 'PLANO' OR NVL(a.impacto_s,0) >= c_impacto_min);
    EXIT WHEN v_n >= c_max_sqls;
    v_n := v_n + 1;

    v_task   := NULL;
    v_manter := FALSE;
    v_erro   := NULL;

    BEGIN
      v_task := DBMS_SQLTUNE.CREATE_TUNING_TASK(
                  sql_id     => a.sql_id,
                  scope      => 'COMPREHENSIVE',
                  time_limit => c_limite_min * 60,
                  task_name  => NULL);
      DBMS_SQLTUNE.EXECUTE_TUNING_TASK(v_task);
      v_rel := DBMS_SQLTUNE.REPORT_TUNING_TASK(v_task, 'TEXT', 'ALL', 'ALL');
    EXCEPTION
      WHEN OTHERS THEN
        v_erro := SUBSTR(SQLERRM, 1, 4000);
    END;

    DBMS_OUTPUT.PUT_LINE('');
    DBMS_OUTPUT.PUT_LINE('--- ' || a.sql_id || ' (' || a.tipo ||
                         ', impacto ' || TO_CHAR(ROUND(NVL(a.impacto_s,0))) || 's) ---');

    IF v_erro IS NOT NULL THEN
      DBMS_OUTPUT.PUT_LINE('-- ERRO: ' || v_erro);
    ELSE
      imprime_clob(v_rel);
      v_manter := DBMS_LOB.INSTR(LOWER(v_rel), 'accept_sql_profile') > 0;
      IF v_manter THEN
        DBMS_OUTPUT.PUT_LINE('-- tuning task ' || v_task || ' MANTIDA (ha SQL '
          || 'Profile a aceitar). Remova apos decidir:');
        DBMS_OUTPUT.PUT_LINE('--   EXEC DBMS_SQLTUNE.DROP_TUNING_TASK('''
          || v_task || ''');');
      END IF;
    END IF;

    IF v_task IS NOT NULL AND NOT v_manter THEN
      BEGIN
        DBMS_SQLTUNE.DROP_TUNING_TASK(v_task);
      EXCEPTION
        WHEN OTHERS THEN NULL;   -- limpeza best-effort, nao derruba o resto
      END;
    END IF;
  END LOOP;

  IF v_n = 0 THEN
    -- Registrar que rodou e nao se aplicava. Sem esta linha o relatorio nao
    -- distingue "nenhum SQL elegivel" de "voce esqueceu de rodar o advisor".
    DBMS_OUTPUT.PUT_LINE('-- Nao executado: nenhum SQL elegivel na janela.');
    DBMS_OUTPUT.PUT_LINE('-- Criterio: troca de plano OU impacto >= '
      || TO_CHAR(c_impacto_min) || 's.');
    DBMS_OUTPUT.PUT_LINE('-- Nenhuma tuning task criada; Tuning Pack NAO consumido.');
  END IF;
END;
/

SPOOL OFF

PROMPT
PROMPT ============================================================
PROMPT Pronto: advisor_troubleshoot.txt
PROMPT Copie junto com coletor_troubleshoot.txt para troubleshooting\<caso>\
PROMPT e rode: .\redija.bat <caso>
PROMPT Confira no fim do arquivo se ficou alguma tuning task MANTIDA.
PROMPT ============================================================
