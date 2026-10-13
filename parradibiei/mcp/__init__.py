"""Repositorio MCP: catalogo hibrido de checks READ-ONLY de diagnostico/tuning Oracle."""

__all__ = [
    "CATALOG_PATH",
    "generate_coletor_sql",
    "get_check",
    "list_checks",
    "list_scripts",
    "load_catalog",
    "online_items",
    "recommendations_by_file",
]


def __getattr__(name):
    # Import preguicoso: evita carregar loader no import do pacote
    # (impede o RuntimeWarning em `python -m mcp.loader`).
    if name in __all__:
        from . import loader
        return getattr(loader, name)
    raise AttributeError(f"module 'mcp' has no attribute {name!r}")
