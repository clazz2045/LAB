"""lab_core - Kanoniczny pakiet inżynieryjny silnika LAB 3.0 (Framework PZW)
Standard: 100% Python Standard Library (Zero-Pip).
Zasady: SOLID, DRY, KISS, YAGNI, Clean Code & Pythonic OOP.
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from .constants import (
    CANONICAL_MANAGEMENT_FILES,
    CORE_ROOM_CODES,
    DEFAULT_CANONICAL_ROOMS,
    FORBIDDEN_WORKSHOP_SECTIONS,
    IGNORED_DIRS,
    IGNORED_FILES,
)
from .env import (
    CanonicalPathError,
    EnvironmentError,
    ensure_vault_in_sys_path,
    find_vault_root,
    validate_canonical_path,
)
from .config import (
    AgentConfig,
    AITeamConfig,
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
from .markdown import (
    dump_frontmatter,
    ensure_consumed_field,
    extract_wikilinks,
    normalize_nfc,
    parse_frontmatter,
)
from .routing import (
    AudioRoute,
    DocumentsRoute,
    DrainContext,
    DrainRoute,
    DrainRouter,
    FallbackArchiveRoute,
    ImagesRoute,
    RawThoughtCaptureRoute,
    ReferenceNoteRoute,
    extract_date,
)
from .themes import (
    THEME_PRESETS,
    generate_design_tokens_css,
    get_theme_preset,
    list_theme_presets,
)
from .templates import (
    generate_agents_contract,
    generate_operations_index,
    generate_projects_index,
    generate_room_readme,
    generate_workshop_entry,
)
from .diagnostics import (
    DiagnosticItem,
    PreflightReport,
    check_git,
    check_obsidian,
    check_python,
    check_raycast,
    check_wispr_flow,
    run_preflight_diagnostics,
)
from .schema import (
    EXPECTED_TABLES,
    SCHEMA_SQL,
    get_master_db_connection,
    init_master_db,
    verify_master_db,
)
from .installer import (
    InstallOptions,
    InstallResult,
    InstallerError,
    LabEngineInstaller,
    PreflightCheckFailedError,
    generate_initial_config_toml,
    install_lab_vault,
)
from .distillate import (
    estimate_tokens,
    validate_dispatch_guard,
    validate_sat_distillate,
)
from .radar import (
    ProjectRadarItem,
    RadarReport,
    ReadingQueueMetrics,
    VaultRadar,
)
from .pre_tool_guard import (
    evaluate_pre_tool,
    is_allowed_coordinator_command,
    is_allowed_coordinator_read,
    is_allowed_coordinator_write,
    is_subagent_session,
    reset_coordinator_read_counter,
)
from .stop_guard import (
    evaluate_stop,
)

__all__ = [
    # constants
    "IGNORED_DIRS",
    "IGNORED_FILES",
    "CANONICAL_MANAGEMENT_FILES",
    "CORE_ROOM_CODES",
    "DEFAULT_CANONICAL_ROOMS",
    "FORBIDDEN_WORKSHOP_SECTIONS",
    # env
    "EnvironmentError",
    "CanonicalPathError",
    "find_vault_root",
    "ensure_vault_in_sys_path",
    "validate_canonical_path",
    # config
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
    # markdown
    "normalize_nfc",
    "parse_frontmatter",
    "dump_frontmatter",
    "extract_wikilinks",
    # routing
    "DrainContext",
    "DrainRoute",
    "DrainRouter",
    "DocumentsRoute",
    "ImagesRoute",
    "AudioRoute",
    "RawThoughtCaptureRoute",
    "ReferenceNoteRoute",
    "FallbackArchiveRoute",
    "extract_date",
    # themes
    "THEME_PRESETS",
    "get_theme_preset",
    "list_theme_presets",
    "generate_design_tokens_css",
    # templates
    "generate_room_readme",
    "generate_projects_index",
    "generate_operations_index",
    "generate_workshop_entry",
    "generate_agents_contract",
    # diagnostics
    "DiagnosticItem",
    "PreflightReport",
    "check_python",
    "check_obsidian",
    "check_git",
    "check_raycast",
    "check_wispr_flow",
    "run_preflight_diagnostics",
    # schema
    "SCHEMA_SQL",
    "EXPECTED_TABLES",
    "get_master_db_connection",
    "init_master_db",
    "verify_master_db",
    # installer
    "InstallerError",
    "PreflightCheckFailedError",
    "InstallOptions",
    "InstallResult",
    "LabEngineInstaller",
    "generate_initial_config_toml",
    "install_lab_vault",
    # radar
    "VaultRadar",
    "RadarReport",
    "ProjectRadarItem",
    "ReadingQueueMetrics",
    # pre_tool_guard
    "evaluate_pre_tool",
    "is_subagent_session",
    "is_allowed_coordinator_read",
    "is_allowed_coordinator_write",
    "is_allowed_coordinator_command",
    "reset_coordinator_read_counter",
    # stop_guard
    "evaluate_stop",
]
