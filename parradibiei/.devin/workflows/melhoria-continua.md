---
description: /MELHORIA - rotina de melhoria continua do framework (corpus de regressao offline, higiene e radar da comunidade Oracle; bateria contra banco real so a mao, quando houver update ou bug corrigido)
---

# /MELHORIA - melhoria continua

Ritual periodico para o framework nao envelhecer. Roda sozinho o que da para
automatizar e **cobra** o que exige leitura humana.

## Regra de ouro (inegociavel)

- **Producao: so o resultado ja coletado.** Nunca reconectar num banco de
  producao para "testar de novo". A reanalise usa apenas a evidencia gravada em
  `troubleshooting/<caso>/` e **nao escreve nada** na pasta do caso.
- **A rotina e offline.** `MELHORIA.py` nao abre conexao com banco nenhum. A
  bateria contra bancos reais saiu da rotina (e do painel): e `TESTE_HOMOLOG.py`
  no terminal, sob demanda.
- **`--advisor` nunca** entra na rotina: escreve no dicionario e consome Tuning
  Pack.

## Cadencia sugerida

| Quando | Comando |
|---|---|
| A cada mudanca nos analisadores | `MELHORIA.py --corpus` |
| Semanal | `MELHORIA.py` (corpus + higiene + fontes) |
| Antes de liberar update / apos corrigir bug | `TESTE_HOMOLOG.py` a mao (passo 4) |

## Passo a passo

### 1. Corpus de regressao (offline)

// turbo
```powershell
.\.venv\Scripts\python.exe MELHORIA.py --corpus
```

Reanalisa TODOS os casos ja coletados e compara com `output/melhoria/baseline.json`.

Quem decide se e bug seu ou nao e a **impressao digital da evidencia** (nome,
tamanho e mtime dos arquivos do caso), nao o nome do caso:

- `[REGRESSAO] ... (mesma evidencia: mudou o codigo)` -> os arquivos do caso sao
  os mesmos e o diagnostico mudou. So sobra uma explicacao. Investigue ANTES de
  seguir; se a mudanca for intencional (limiar novo, analisador melhor), aceite
  com `--aceitar` e explique no commit **por que** o diagnostico mudou.
  Vale tambem para caso de homologacao **parado**: sem recoleta, ele e corpus tao
  legitimo quanto producao.
- `[info] ... recoletado (evidencia recoletada: nao e o codigo)` -> alguem rodou
  o `COLETAR.py` nesse caso (tipico da bateria ou de um teste manual em
  homologacao). Mudou o banco. Nao bloqueia.
- `erro_fatal` / `analisador_quebrou` -> prioridade maxima: um analisador esta
  estourando em evidencia real e o caso sai com uma secao a menos, calado.

Anotar o caso pelo `CASO.py` **nao** conta como recoleta: `meta.json` fica fora
da impressao digital.

Aceitar o retrato atual como nova referencia:

```powershell
.\.venv\Scripts\python.exe MELHORIA.py --corpus --aceitar
```

O baseline mora em `output/` (ignorado no git) porque carrega titulos de achados
de casos reais. Nao versione.

### 2. Higiene

// turbo
```powershell
.\.venv\Scripts\python.exe MELHORIA.py --higiene
```

Roda a suite, confere os pins de `requirements.txt` contra o que esta instalado
e aponta os buracos do `mcp/catalog.json`:

- `sem_sql` - check declarado que nao coleta nada;
- `sem_analisador` - **coleta evidencia que ninguem le**; escolha: escrever o
  analisador ou remover o check;
- `sem_referencia` - limiar sem fonte citada. Todo numero magico precisa de uma
  linha em `references` (doc oficial, MOS, orachk, artigo da comunidade).

### 3. Radar da documentacao e da comunidade

// turbo
```powershell
.\.venv\Scripts\python.exe MELHORIA.py --fontes
```

Lista o que esta ha mais de 30 dias sem revisao. Nao ha scraping (o proxy
bloqueia e leitura tecnica exige criterio): a rotina cobra, voce le.

O que procurar em cada fonte, na pratica:

1. **Oracle Docs / Reference** - `v$`/`dba_` novas por versao, colunas que
   mudaram de nome (fonte de `[warn]` em versao nova).
2. **MOS** - cada `ORA-` que aparecer na coleta vira uma busca de nota/bug.
3. **oracle-base, Tanel Poder, Jonathan Lewis, AskTOM, Optimizer Blog,
   Mike Dietrich, Franck Pachot** - tecnica nova, script melhor, limiar mais
   defensavel do que o nosso.
4. **AHF / orachk** - lista de checks oficial: compare com `mcp/catalog.json` e
   traga o que falta (respeitando read-only e licenca de pack).
5. **python-oracledb release notes** - o driver da coleta; bug corrigido e
   feature de conexao (ex.: timeouts) entram aqui.
6. **Method R** - manter a priorizacao por DB time, nao por sintoma.

Ao aplicar algo, registre - e isso que transforma leitura em melhoria:

```powershell
.\.venv\Scripts\python.exe MELHORIA.py --revisei tanel-poder --nota "novo check de ASH por machine"
```

### 4. Bateria contra banco real (interna, fora da rotina)

**Nao faz parte do `MELHORIA.py`** e nao esta no painel: e ferramenta interna de
quem mantem o framework. Rode a mao quando houver **update para liberar** ou
**bug corrigido para validar** - nao "toda semana por habito", porque cada
execucao abre conexao em N bancos reais.

```powershell
.\.venv\Scripts\python.exe TESTE_HOMOLOG.py --file servidores.txt --comparar
```

Trate o resultado pela ordem de prioridade da propria bateria: `BUG` (traceback)
antes de `FALHA`, `ARTEFATO`, `CHECK`, `PACK`.

Servidor novo? Acrescente em `servidores.txt` (repositorio vivo da bateria).

### 5. Fechar o ciclo

- Relatorio datado em `output/melhoria/RELATORIO_<data>.md`.
- Achou bug: corrija, escreva o teste de regressao, rode `--corpus` de novo.
- Mudanca intencional no diagnostico: `--aceitar` e explique no commit **por que**
  o diagnostico mudou.
- Depois, `/commit` para consolidar.

## Criterio de saida

`MELHORIA.py` retorna `1` se houver regressao no corpus ou teste vermelho. Serve
para nao seguir para o commit com o diagnostico de um caso ja entregue alterado
sem que ninguem tenha olhado.
