"""
Repositorio de conexoes Oracle a partir de exports do SQL Developer.

Le todos os *.json em stringConexao/ (formato {"connections":[{"info":{...},
"name":...}]}) e expoe busca/uso das conexoes. Tambem permite adicionar/atualizar
conexoes mantendo o formato compativel com o SQL Developer (reimportavel).

Senhas exportadas pelo SQL Developer sao cifradas com chave propria e NAO sao
usadas: a senha e sempre solicitada/!env no momento da conexao.
"""
from __future__ import annotations

import difflib
import glob
import json
import os

REPO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "stringConexao")
DEFAULT_FILE = "Parra.json"


class Conn(dict):
    """Conexao normalizada: name, host, port, service, user, folder, file."""

    @property
    def dsn(self) -> str:
        return f"{self['host']}:{self['port']}/{self['service']}"

    def __str__(self) -> str:
        grp = f"  [{self['folder']}]" if self.get("folder") else ""
        return f"{self['name']} -> {self.dsn}  (user={self['user']}){grp}"


def _normalize(raw: dict, source_file: str) -> Conn | None:
    info = raw.get("info", {})
    host = info.get("hostname") or ""
    service = info.get("serviceName") or ""
    if not host and not service:
        return None
    return Conn(
        name=raw.get("name", service or host),
        host=host,
        port=info.get("port", "1521"),
        service=service,
        user=info.get("user", ""),
        folder=info.get("NAV_FOLDER", ""),
        file=os.path.basename(source_file),
    )


def load_connections(repo_dir: str = REPO_DIR) -> list[Conn]:
    """Carrega e mescla as conexoes de todos os *.json do repositorio."""
    conns: list[Conn] = []
    for path in sorted(glob.glob(os.path.join(repo_dir, "*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for raw in data.get("connections", []):
            c = _normalize(raw, path)
            if c:
                conns.append(c)
    conns.sort(key=lambda c: c["name"].lower())
    return conns


def find(query: str, repo_dir: str = REPO_DIR) -> list[Conn]:
    """Busca conexoes por nome/servico/host (exata primeiro, depois substring)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    conns = load_connections(repo_dir)
    exact = [c for c in conns if c["name"].lower() == q or c["service"].lower() == q]
    if exact:
        return exact
    return [c for c in conns
            if q in c["name"].lower() or q in c["service"].lower()
            or q in c["host"].lower() or q in c["folder"].lower()]


def parecidas(query: str, nomes: list[str], limite: int = 5) -> list[str]:
    """Nomes de conexao proximos do que foi digitado.

    "Nenhuma conexao encontrada" nao ajuda quem errou uma letra num repositorio
    de 115 entradas. Combina prefixo (DBSRV6 -> DBSRV600) com similaridade, que
    e o que pega troca de digito e maiuscula/minuscula.

    Mora aqui, e nao no COLETAR.py, porque o painel precisa da MESMA sugestao:
    duas implementacoes divergiriam e o DBA veria uma lista no terminal e outra
    na tela para o mesmo erro de digitacao.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    prefixo = [n for n in nomes if n.lower().startswith(q[:4])]
    perto = difflib.get_close_matches(q, nomes, n=limite, cutoff=0.6)
    out = []
    for n in prefixo + perto:
        if n not in out:
            out.append(n)
    return out[:limite]


def _sqldev_entry(name, host, port, service, user, folder) -> dict:
    return {
        "info": {
            "role": "", "SavePassword": "false", "OracleConnectionType": "BASIC",
            "PROXY_TYPE": "USER NAME", "RaptorConnectionType": "Oracle",
            "serviceName": service, "NAV_FOLDER": folder,
            "customUrl": f"jdbc:oracle:thin:@//{host}:{port}/{service}",
            "oraDriverType": "thin", "NoPasswordConnection": "TRUE",
            "hostname": host, "driver": "oracle.jdbc.OracleDriver",
            "subtype": "oraJDBC", "port": str(port), "OS_AUTHENTICATION": "false",
            "IS_PROXY": "false", "KERBEROS_AUTHENTICATION": "false",
            "user": user, "PROXY_USER_NAME": "",
        },
        "name": name, "type": "jdbc",
    }


def add_or_update(name, host, service, user, port="1529", folder="",
                  repo_dir: str = REPO_DIR, file: str = DEFAULT_FILE) -> str:
    """Adiciona ou atualiza uma conexao no arquivo do repositorio (formato SQL
    Developer). Retorna o caminho do arquivo salvo."""
    os.makedirs(repo_dir, exist_ok=True)
    path = os.path.join(repo_dir, file)
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            # NUNCA cair para uma lista vazia aqui: a gravacao logo abaixo
            # substituiria o arquivo inteiro pela conexao nova, apagando o
            # inventario do DBA (dezenas de bancos). Um arquivo exportado do
            # SQL Developer em outra codificacao ja basta para chegar neste
            # ponto sem que nada esteja de fato perdido - e ler falhando nao
            # autoriza escrever por cima.
            raise ValueError(
                f"{path} existe mas nao pode ser lido ({e}). "
                "Corrija ou renomeie o arquivo antes de gravar - nao vou "
                "sobrescrever as conexoes ja cadastradas.") from e
    else:
        data = {"connections": []}
    data.setdefault("connections", [])
    entry = _sqldev_entry(name, host, port, service or name, user, folder)
    for i, c in enumerate(data["connections"]):
        if c.get("name", "").lower() == name.lower():
            data["connections"][i] = entry
            break
    else:
        data["connections"].append(entry)
    # Escreve ao lado e troca: `open(path,"w")` trunca ANTES de escrever, e uma
    # interrupcao no meio deixaria o arquivo de conexoes pela metade.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path
