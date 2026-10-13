---
description: parra / dba - inicia o troubleshooting guiado no modo DBA Master (prompt vivo Parra), read-only primeiro, terminando em redija -completo
---

# /parra - Troubleshooting guiado (DBA Master)

Inicia ou retoma o fluxo guiado do **DBA Master (Parra)**.

## Passos

1. **Carregar o prompt vivo** (contexto completo):
   leia `dba_master_prompt.txt` na RAIZ DO PROJETO com a ferramenta
   de leitura de arquivo e assuma a persona DBA Master.

2. **Enquadrar o caso.** Se ainda nao informado, pergunte:
   - o **sintoma/evento** (ex.: lentidao, ORA-xxxxx, tablespace cheia);
   - o **banco** (nome no `Parra.json`) e o **incidente** (ex.: INC000987654).

3. **Conduzir o passo a passo** (sempre read-only primeiro):
   - **coletar** as evidencias:
     - Direto: `.\.venv\Scripts\python.exe COLETAR.py <caso> --conn <banco> --completo`
     - Citrix: rodar `@coletor.sql` e trazer `coletor_troubleshoot.txt` para `troubleshooting\<caso>\`
   - Levante **uma hipotese** e teste com os dados. Se nao confirmar, use **proximo**.
   - Acoes destrutivas (kill/resize/drop/gather/profile) **somente** sob Matriz de
     Risco: aviso de impacto + rollback + aprovacao explicita.

4. **Consolidar**: ao concluir, execute **redija -completo** (workflow
   /redija-completo) para gerar dashboard + PPTX + relatorio no Template Padrao
   (subpasta `output\troubleshooting\<caso>\`).

## Observacoes
- Padrao **somente leitura**; respeite a licenca Oracle (Diagnostics/Tuning Pack)
  com `--pack auto|T|D|N`.
- Priorize por DB time / wait class (Method R) e saturacao de recursos (USE).
