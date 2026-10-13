# Framework de Troubleshooting Oracle - versao portatil

Pasta autocontida: **nao instala nada**, nao pede admin e nao mexe no Python da
maquina. Copie a pasta inteira e rode.

## Como usar

1. `INICIAR.bat` -> abre o painel em <http://127.0.0.1:8765/>.
2. `TERMINAL.bat` -> prompt com o Python do pacote, para os comandos completos
   (`COLETAR.py`, `TROUBLESHOOT.py`, `CASO.py`, `--advisor`...).

## O que tem dentro

- `python\` - Python embeddable (nao registra nada no Windows).
- `libs\` - `oracledb` (modo thin, sem Oracle Client) e `python-pptx`.
- o framework e o repositorio de conexoes.

## Cuidados

- **Senha**: e pedida a cada execucao - no terminal sem eco, ou numa caixa
  mascarada no painel. Nao existe onde guarda-la, e isso e de proposito: nunca
  escreva a senha num arquivo dentro da pasta (o pacote e feito para circular).
- **O pacote nao leva evidencia**: `troubleshooting\` e `output\` nascem vazios.
  O que voce coletar fica na copia que estiver usando - trate como dado de
  producao.
- **Rede**: e preciso alcancar o listener Oracle (1529/1521) da maquina onde a
  pasta estiver.
