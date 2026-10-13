"""Analisadores de troubleshooting (um por tipo de evidencia)."""
from .addm import AddmAnalyzer
from .alertlog import AlertLogAnalyzer
from .amostragem import AmostragemAnalyzer
from .ash import AshAnalyzer
from .base import Analyzer, num, parse_fixed_width_tables, read_text
from .baseline import BaselineAnalyzer
from .bugs import BugsAnalyzer
from .changes import ChangesAnalyzer
from .conformidade import ConformidadeAnalyzer
from .contencao import ContencaoAnalyzer
from .dataguard import DataGuardAnalyzer
from .exadata import ExadataAnalyzer
from .forecast import ForecastAnalyzer
from .generic import GenericAnalyzer
from .goldengate import GoldenGateAnalyzer
from .integridade import IntegridadeAnalyzer
from .iostat import IoStatAnalyzer
from .locks import LocksAnalyzer
from .operacao import OperacaoAnalyzer
from .rac import RacAnalyzer
from .regression import RegressionAnalyzer
from .rman import RmanAnalyzer
from .saude import SaudeAnalyzer
from .sessions import SessionsAnalyzer
from .space import SpaceAnalyzer
from .sqlmon import SqlMonitorAnalyzer
from .stats import StatsAnalyzer
from .topologia import TopologiaAnalyzer
from .workload import WorkloadAnalyzer

# Registro de analisadores especializados (ordem = prioridade em empate)
ANALYZERS = [
    TopologiaAnalyzer(),   # topology.txt/rac_nodes.txt: decide o contexto do resto
    ExadataAnalyzer(),
    GoldenGateAnalyzer(),
    SqlMonitorAnalyzer(),
    AddmAnalyzer(),
    LocksAnalyzer(),
    ForecastAnalyzer(),   # antes do Space: forecast.txt tambem tem header TABLESPACE
    SaudeAnalyzer(),      # antes do Space: fra/undo/df_noautoext citam tablespace
    SpaceAnalyzer(),
    ChangesAnalyzer(),
    AlertLogAnalyzer(),
    AmostragemAnalyzer(),  # antes do Ash: amostragem.txt tem perfil de esperas
    ContencaoAnalyzer(),   # parse/redo/deadlock: antes do Ash (redo_waits tem
                           # coluna 'event' e o Ash reclamaria o arquivo)
    AshAnalyzer(),
    IoStatAnalyzer(),
    DataGuardAnalyzer(),
    IntegridadeAnalyzer(),  # antes do Rman: backup_age.txt tambem casa com "backup"
    OperacaoAnalyzer(),
    ConformidadeAnalyzer(),  # bp_*.txt: best practices, antes do generico
    BugsAnalyzer(),          # patches/dicionario/backup falhando
    RmanAnalyzer(),
    RacAnalyzer(),
    SessionsAnalyzer(),
    StatsAnalyzer(),
    RegressionAnalyzer(),   # antes do Baseline: regressions.txt e formato proprio
    BaselineAnalyzer(),
    WorkloadAnalyzer(),
]

# Fallback explicito para arquivos nao classificados
GENERIC = GenericAnalyzer()

__all__ = [
    "ANALYZERS",
    "GENERIC",
    "AddmAnalyzer",
    "AlertLogAnalyzer",
    "AmostragemAnalyzer",
    "Analyzer",
    "AshAnalyzer",
    "BaselineAnalyzer",
    "BugsAnalyzer",
    "ChangesAnalyzer",
    "ConformidadeAnalyzer",
    "ContencaoAnalyzer",
    "DataGuardAnalyzer",
    "ExadataAnalyzer",
    "ForecastAnalyzer",
    "GenericAnalyzer",
    "GoldenGateAnalyzer",
    "IntegridadeAnalyzer",
    "IoStatAnalyzer",
    "LocksAnalyzer",
    "OperacaoAnalyzer",
    "RacAnalyzer",
    "RegressionAnalyzer",
    "RmanAnalyzer",
    "SaudeAnalyzer",
    "SessionsAnalyzer",
    "SpaceAnalyzer",
    "SqlMonitorAnalyzer",
    "StatsAnalyzer",
    "TopologiaAnalyzer",
    "WorkloadAnalyzer",
    "num",
    "parse_fixed_width_tables",
    "read_text",
]
