"""
Diagnostico da instalacao ("doctor").

Responde a pergunta "esta maquina esta pronta para rodar o framework?" sem
tocar em nenhum banco: valida Python, dependencias, integridade do catalogo
MCP, da base de conhecimento, repositorio de conexoes, higiene de senha e
permissao de escrita.

Uso:
  python -m troubleshoot.doctor          (ou .\verificar.bat)

Codigo de saida: 0 = pronto para usar; 1 = existe item [ERRO] bloqueante.
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OK, WARN, ERR = "OK", "AVISO", "ERRO"


def _py_version():
    v = sys.version_info
    txt = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 11):
        return ERR, txt + " (o framework exige 3.11+)"
    return OK, txt


def _portatil() -> bool:
    """Estamos rodando dentro do pacote gerado pelo `EMPACOTAR.py`?

    Nele o Python vive em `<pacote>\\python\\` e as dependencias em
    `<pacote>\\libs\\` - de proposito nao existe `.venv`. Sem esta deteccao o
    doctor dava um aviso e mandava rodar `install.bat` justamente na versao que
    foi feita para NAO instalar nada; quem recebeu o pacote achava que estava
    com a instalacao pela metade.
    """
    return (os.path.isdir(os.path.join(ROOT, "libs"))
            and os.path.abspath(sys.prefix).startswith(os.path.abspath(ROOT)))


def _in_venv():
    if _portatil():
        return OK, "pacote portatil (Python proprio, sem instalacao)"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return OK, f"venv ativo ({os.path.basename(sys.prefix)})"
    return WARN, "rodando FORA do .venv (use .\\install.bat para criar)"


def _dep(mod: str, obrigatorio: bool, para: str):
    try:
        m = __import__(mod)
        ver = getattr(m, "__version__", "?")
        return OK, f"{mod} {ver}"
    except ImportError:
        nivel = ERR if obrigatorio else WARN
        return nivel, f"{mod} ausente - necessario para {para}"


def _catalogo():
    try:
        from mcp import online_items
        from troubleshoot.collector import validate_select
    except Exception as ex:
        return ERR, f"catalogo/coletor nao importam: {ex}"
    items = online_items()
    if not items:
        return ERR, "catalogo MCP vazio (mcp/catalog.json)"
    ruins = [it["id"] for it in items if not validate_select(it["sql"])]
    if ruins:
        return ERR, f"checks NAO read-only no catalogo: {', '.join(ruins)}"
    return OK, f"{len(items)} checks read-only validados"


def _kb():
    """A base de conhecimento e servida ao DBA no painel durante o incidente.

    Playbook quebrado e pior que playbook ausente: manda executar um passo
    perigoso sem o aviso de risco. Por isso o doctor cobra o mesmo contrato que
    o loader cobra (risco declarado, pre-requisito em passo que muda algo), em
    vez de so contar arquivos.
    """
    try:
        from troubleshoot import kb
        playbooks = kb.carregar()
        erros = kb.validar_tudo()
    except Exception as ex:
        return ERR, f"base de conhecimento nao carrega: {ex}"
    if not playbooks:
        return WARN, "nenhum playbook em kb/ (o painel abre sem a KB)"
    if erros:
        return ERR, (f"{len(erros)} problema(s) de contrato em kb/: "
                     f"{erros[0]}" + (" ..." if len(erros) > 1 else ""))
    passos = sum(len(p.get("passos") or []) for p in playbooks)
    return OK, f"{len(playbooks)} playbooks / {passos} passos validados"


def _conexoes():
    try:
        from troubleshoot import conn_repo
        conns = conn_repo.load_connections()
    except Exception as ex:
        return WARN, f"repositorio de conexoes ilegivel: {ex}"
    if not conns:
        return WARN, ("nenhuma conexao em stringConexao/ - use "
                      "COLETAR.py --add ou --host/--service")
    return OK, f"{len(conns)} conexao(oes) em stringConexao/"


def _senha_persistida_no_registro() -> bool:
    """ORA_PASSWORD gravada em HKCU\\Environment (sobrevive a reboot)?

    Le o REGISTRO, e nao `os.environ`: a variavel tambem aparece no ambiente
    quando alguem a define so para a sessao atual (`$env:ORA_PASSWORD=...`), o
    que e temporario e some ao fechar a janela. O que precisa de alarme e a
    senha em texto puro no perfil do usuario, deixada la pelo antigo
    `guardar_senha.ps1`.
    """
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            valor, _ = winreg.QueryValueEx(k, "ORA_PASSWORD")
        return bool(valor)
    except FileNotFoundError:
        return False
    except Exception:
        # Registro ilegivel nao e evidencia de nada; nao inventar achado.
        return False


def _senhas_no_repositorio() -> list[tuple[str, int]]:
    """Conexoes com senha guardada em cada `stringConexao/*.json`.

    O framework nunca escreve esse campo - quem escreve e o SQL Developer, ao
    exportar conexoes salvas com *Save Password*. Por isso a checagem precisa
    ser recorrente e nao pontual: reexportar o repositorio reintroduz tudo em
    silencio, e o arquivo e justamente o que a documentacao manda levar junto
    ao copiar o projeto para outra maquina.
    """
    from troubleshoot import conn_repo
    achados = []
    for caminho in sorted(glob.glob(os.path.join(conn_repo.REPO_DIR, "*.json"))):
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, ValueError):
            continue  # ilegivel ja e reportado pelo check de conexoes
        n = sum(1 for c in dados.get("connections", [])
                if (c.get("info") or {}).get("password"))
        if n:
            achados.append((os.path.basename(caminho), n))
    return achados


def _senha():
    """O framework pede a senha a cada execucao - aqui so se checa HIGIENE.

    Este check ja foi o oposto: reportava "ORA_PASSWORD definida" como OK e
    mandava rodar `guardar_senha.bat`, que gravava a senha em texto puro no
    registro. Quem seguiu a recomendacao continua exposto ate limpar.
    """
    if _senha_persistida_no_registro():
        return ERR, ("senha PERSISTIDA em texto puro no registro "
                     "(HKCU\\Environment\\ORA_PASSWORD) - legivel por qualquer "
                     "processo seu. Rode .\\limpar_senha.bat e troque a senha")
    guardadas = _senhas_no_repositorio()
    if guardadas:
        onde = ", ".join(f"{a} ({n})" for a, n in guardadas)
        return ERR, (f"senha guardada no repositorio de conexoes: {onde}. "
                     "Veio de um export do SQL Developer com Save Password; a "
                     "cifra e do proprio SQL Developer e o algoritmo e publico. "
                     "Reexporte com Save Password desmarcado")
    if os.environ.get("ORA_PASSWORD"):
        return WARN, ("ORA_PASSWORD definida nesta sessao - o framework nao a "
                      "usa mais, mas ela e herdada por processos filhos")
    return OK, "nenhuma senha guardada (e digitada a cada execucao)"


def _escrita():
    problemas = []
    for sub in ("troubleshooting", "output"):
        d = os.path.join(ROOT, sub)
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".doctor_write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except Exception as ex:
            problemas.append(f"{sub}/ ({ex})")
    if problemas:
        return ERR, "sem permissao de escrita em " + ", ".join(problemas)
    return OK, "troubleshooting/ e output/ com permissao de escrita"


def run_checks() -> list[tuple[str, str, str]]:
    """Executa todos os checks. Retorna lista de (nivel, titulo, detalhe)."""
    resultados = []
    for titulo, fn in (
        ("Python", _py_version),
        ("Ambiente virtual", _in_venv),
        ("Driver Oracle", lambda: _dep("oracledb", True, "a coleta online")),
        ("Geracao de PPTX", lambda: _dep("pptx", False, "o PPTX (use --no-pptx sem ele)")),
        ("Catalogo MCP", _catalogo),
        ("Base de conhecimento", _kb),
        ("Repositorio de conexoes", _conexoes),
        ("Credenciais", _senha),
        ("Permissao de escrita", _escrita),
    ):
        try:
            nivel, detalhe = fn()
        except Exception as ex:                      # nunca deixa o doctor quebrar
            nivel, detalhe = ERR, f"falha inesperada: {ex}"
        resultados.append((nivel, titulo, detalhe))
    return resultados


def main() -> int:
    print("=" * 70)
    print("  VERIFICAR - diagnostico da instalacao (nao acessa banco)")
    print("=" * 70)
    resultados = run_checks()
    largura = max((len(t) for _, t, _ in resultados), default=0)
    for nivel, titulo, detalhe in resultados:
        print(f"  [{nivel:5}] {titulo.ljust(largura)}  {detalhe}")

    erros = [t for n, t, _ in resultados if n == ERR]
    avisos = [t for n, t, _ in resultados if n == WARN]
    print("-" * 70)
    if erros:
        print(f"  {len(erros)} item(ns) bloqueante(s): {', '.join(erros)}")
        print("  Rode:  .\\install.bat        (recria o .venv e instala tudo)")
        return 1
    if avisos:
        print(f"  Pronto para usar, com {len(avisos)} aviso(s): {', '.join(avisos)}")
    else:
        print("  Tudo pronto.")
    print("  Teste:  .\\healthcheck.bat <servidor> --rapido")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
