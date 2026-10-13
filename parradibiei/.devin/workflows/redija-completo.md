---
description: redija -completo — consolida o troubleshooting do passo a passo em relatório padronizado completo (dashboard HTML + PPTX + template DBA Master)
---

# redija -completo

Aciona ao final de um troubleshooting (quando o usuário digitar `redija -completo`
ou `/redija-completo`). Pega TODAS as evidências coletadas no nosso passo a passo
(saídas de query, relatórios, prints) e gera o **framework completo** de
troubleshooting: dashboard, PPTX e o relatório no **Template Padrão DBA Master**.

Projeto base: `<RAIZ_DO_PROJETO>`.

## Passos

1. **Definir o caso.** Use o incidente/sintoma como nome (ex.: `INC000987654`).
   Se o usuário não informar, pergunte o nome do caso.

// turbo
2. **Criar a pasta do caso:**
   ```
   .\.venv\Scripts\python.exe TROUBLESHOOT.py --new <caso>
   ```

3. **Salvar as evidências do passo a passo** em `troubleshooting\<caso>\`, uma por
   arquivo, com nomes que o roteador reconhece (o conteúdo é o output COMPLETO que
   o usuário retornou em cada etapa da coleta):
   - Waits/ASH        → `ash.txt`
   - Locks/bloqueios   → `locks.txt`
   - I/O / filestat    → `iostat.txt`
   - Espaço/ASM        → `space.txt`
   - SQL Monitor (TEXT)→ `sqlmon.txt`
   - ADDM              → `addm.txt`
   - Alert log/ORA-    → `alert.log`
   - Data Guard        → `dataguard.txt`
   - RMAN/backups      → `rman.txt`
   - Sessões           → `sessions.txt`
   - Estatísticas      → `stats.txt`
   - Prints            → `*.png`
   Qualquer outra saída pode ir como `.txt` (o analisador genérico a inclui).
   As queries prontas ficam em `queries\` do projeto.

4. **Preencher `troubleshooting\<caso>\meta.json`** com os campos do Template Padrão
   a partir do que foi discutido no passo a passo (deixe vazio o que não souber):
   ```json
   {
     "title": "<INC> - <sintoma>",
     "subtitle": "<hostname> / <PDB> / <data>",
     "hostname": "<hostname>",
     "evento": "<sintoma principal>",
     "hipotese_inicial": "<causa provável>",
     "hipotese_atual": "<hipótese corrente>",
     "timeline": "<cronologia>",
     "impacto": "<impacto no negócio>",
     "riscos": "<ações destrutivas e mitigações>",
     "status": "Investigando",
     "criado_em": "<data/hora>"
   }
   ```

// turbo
5. **Gerar o relatório completo:**
   ```
   .\.venv\Scripts\python.exe TROUBLESHOOT.py <caso> --completo --open
   ```

6. **Apresentar o resultado** ao usuário: cada caso gera uma **subpasta própria**
   em `output\troubleshooting\<caso>\`. Exibir inline o conteúdo de
   `output\troubleshooting\<caso>\<caso>_relatorio.md` (Template Padrão) e citar:
   - Dashboard: `output\troubleshooting\<caso>\<caso>.html`
   - Apresentação: `output\troubleshooting\<caso>\<caso>.pptx`
   - Índice de casos: `output\troubleshooting\index.html`

## Observações

- O framework aplica o semáforo (verde/laranja/vermelho) e as recomendações por
  tipo de análise, seguindo Millsap (Response Time = Service + Wait) e USE/RED.
- Cobre: SQL Monitor, ADDM, Locks, Espaço/ASM, Alert Log, ASH/waits, I/O,
  Data Guard/HA, RMAN/backup, RAC/Global Cache, Sessões e Estatísticas — além de
  um analisador genérico para qualquer outra evidência tabular.
- Só de leitura: NÃO executa ações destrutivas. Ações críticas continuam sob a
  Matriz de Risco do prompt DBA Master (aviso + rollback + aprovação).
