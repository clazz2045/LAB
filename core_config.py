"""core_config.py - Fasada wstecznej kompatybilności dla LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library.
Re-eksportuje symbole z wewnętrznego pakietu lab_core (Projekt PR-015, Faza 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Blokada tworzenia folderów __pycache__ w iCloud
sys.dont_write_bytecode = True

# Dynamiczny bootstrap sys.path dla pakietu lab_core z pokoju 08 Zespół AI
_scripts_dir = Path(__file__).resolve().parent / "08 Zespół AI" / "Skrypty"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core.config import (
    AgentConfig,
    AITeamConfig,
    CANONICAL_ROLE_KEYS,
    ConfigError,
    ConfigManager,
    ConfigNotFoundError,
    ConfigValidationError,
    ExternalStorageConfig,
    HumanThoughtsPolicyConfig,
    PresentationConfig,
    RoomConfig,
    StorageConfig,
    SystemConfig,
    UserConfig,
    WorkshopPolicyConfig,
)
from lab_core.env import find_vault_root

__all__ = [
    "CANONICAL_ROLE_KEYS",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigValidationError",
    "UserConfig",
    "SystemConfig",
    "StorageConfig",
    "ExternalStorageConfig",
    "RoomConfig",
    "AgentConfig",
    "AITeamConfig",
    "PresentationConfig",
    "HumanThoughtsPolicyConfig",
    "WorkshopPolicyConfig",
    "ConfigManager",
    "find_vault_root",
]
