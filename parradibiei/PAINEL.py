r"""
PAINEL.py - abre o painel local (GUI) do framework de troubleshooting.

Uso:
    .\.venv\Scripts\python.exe PAINEL.py              # abre o navegador
    .\.venv\Scripts\python.exe PAINEL.py --port 9000
    .\.venv\Scripts\python.exe PAINEL.py --no-open    # so sobe o servidor

O painel NAO substitui o terminal: ele dispara os mesmos COLETAR.py /
TROUBLESHOOT.py / CASO.py. `--advisor` e o unico que escreve no banco: na tela
ele existe so como opt-in do modo Completo, desmarcado e com confirmacao.

Escuta so em 127.0.0.1. Expor na rede exige --host explicito e e ma ideia: a
tela le evidencia de producao e dispara coleta sem autenticacao nenhuma.
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from troubleshoot.painel import Painel, criar_servidor


def main() -> int:
    ap = argparse.ArgumentParser(description="Painel local do framework de troubleshooting")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="padrao 127.0.0.1 (loopback). Mudar expoe o painel na rede.")
    ap.add_argument("--no-open", dest="no_open", action="store_true",
                    help="nao abrir o navegador automaticamente")
    args = ap.parse_args()

    try:
        srv = criar_servidor(args.host, args.port, Painel())
    except OSError as e:
        print(f"[ERRO] nao foi possivel escutar em {args.host}:{args.port} -> {e}")
        print("       outra instancia do painel ja esta aberta? tente --port 8766")
        return 1

    url = f"http://{args.host}:{srv.server_port}/"
    if args.host not in ("127.0.0.1", "localhost"):
        print("[AVISO] o painel esta escutando FORA do loopback: qualquer um na "
              "rede pode disparar coleta e ler evidencia. Use so se souber o "
              "que esta fazendo.")
    print(f"Painel de Troubleshooting Oracle em {url}")
    print("Ctrl+C para encerrar.")

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando...")
    finally:
        srv.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
