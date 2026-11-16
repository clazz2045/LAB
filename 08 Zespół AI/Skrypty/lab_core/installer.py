"""lab_core.installer - Silnik Powoływania Nowej Instancji LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (shutil, json, pathlib, dataclasses, tomllib, sqlite3).
Odpowiada za deterministyczną budowę czystego skarbca LAB:
1. Pre-flight check (fail-safe przed uruchomieniem).
2. Struktura 8 pokoi rdzenia z plikami zarządczymi.
3. Czyste pliki zarządcze bez danych prywatnych (Warsztat, Projekty, Operacje, AGENTS.md).
4. Konfiguracja .obsidian/ (appearance.json, app.json, bookmarks.json, lab-design-tokens.css) z walidacją JSON.
5. Dziewicza baza SQLite Kingi (07 Bazy Danych/lab_master.db).
6. Nowy plik lab.config.toml (wersja 3.0.0) z walidacją tomllib i kopiami .bak.
7. Kopiowanie czystego silnika Zespołu AI do pokoju 08 Zespół AI (+ warunkowe uprawnienia Raycast na macOS).
8. Automatyczny Post-Install Smoke Check (TOML, JSON, SQLite WAL + UTF-8, session_bootstrap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

from .config import ConfigManager
from .diagnostics import (
    PreflightReport,
    check_icloud_storage,
    clear_macos_quarantine,
    run_preflight_diagnostics,
    scan_icloud_hazards,
)
from .schema import init_master_db, verify_master_db
from .templates import (
    generate_agents_contract,
    generate_operations_index,
    generate_projects_index,
    generate_room_readme,
    generate_workshop_entry,
)
from .themes import generate_design_tokens_css, get_theme_preset

LAB_VERSION = "3.0.0"

DEFAULT_AGENT_NAMES: Dict[str, str] = {
    "coordinator": "Adam",
    "auditor": "Ada",
    "researcher": "Klara",
    "engineer": "Maks",
    "designer": "Maja",
    "knowledge": "Edyta",
    "database_code": "Kinga",
}

ROLE_FOLDER_PREFIXES: Dict[str, str] = {
    "Koordynator": "coordinator",
    "Audytor": "auditor",
    "Research": "researcher",
    "Inżynier": "engineer",
    "Design": "designer",
    "Wiedza": "knowledge",
    "Bazy Danych": "database_code",
}

VAULT_GITIGNORE_CONTENT = """# LAB 3.0 - Git Ignore Standard
__pycache__/
*.py[cod]
*$py.class
*.so
.DS_Store
*.tmp
*.log
*.bak
*.db
*.db-wal
*.db-shm
*-wal
*-shm
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/wispr_sync_state.json
.obsidian/plugins/
.venv/
env/
venv/
"""


def _toml_escape(value: str) -> str:
    """Bezpiecznie escapuje łańcuch tekstowy do formatu TOML."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _backup_if_exists(path: Path) -> Optional[Path]:
    """Tworzy kopię zapasową .bak przed nadpisaniem istniejącego pliku."""
    if path.is_file():
        bak_path = path.with_name(f"{path.name}.bak")
        shutil.copy2(path, bak_path)
        return bak_path
    return None


def _is_icloud_path(path: Path) -> bool:
    """Sprawdza, czy ścieżka leży wewnątrz katalogu iCloud Drive."""
    s = str(path)
    return "Mobile Documents" in s or "com~apple~CloudDocs" in s or "iCloud~md~obsidian" in s


class InstallerError(Exception):
    """Bazowy błąd instalatora środowiska LAB."""
    pass


class PreflightCheckFailedError(InstallerError):
    """Podnoszony, gdy diagnostyka pre-flight wykaże brak wymagań krytycznych."""
    pass


@dataclass
class InstallOptions:
    """Parametry powołania nowej instancji LAB 3.0."""
    target_dir: Path
    instance_name: str = "LAB"
    user_name: str = "Użytkownik"
    call_sign: Optional[str] = None
    theme_preset: str = "dawn"
    theme_preset_light: str = "dawn"
    theme_preset_dark: str = "tokyonight"
    theme_mode: str = "system"
    coordinator_name: str = "Adam"
    governance_name: str = "Ada"
    agent_names: Dict[str, str] = field(default_factory=dict)
    force: bool = False
    skip_preflight: bool = False
    copy_engine: bool = True
    profile: str = "full"
    icloud_enabled: Optional[bool] = None
    github_enabled: bool = False
    enable_projects: bool = True
    enable_operations: bool = True
    llm_provider: str = "antigravity"
    llm_model: str = "auto"
    run_smoke_check: bool = True
    confirm_icloud_optimize_off: bool = False

    def __post_init__(self) -> None:
        self.target_dir = Path(self.target_dir).resolve()
        if not self.call_sign:
            self.call_sign = self.user_name
        if self.icloud_enabled is None:
            self.icloud_enabled = _is_icloud_path(self.target_dir)
        merged = dict(DEFAULT_AGENT_NAMES)
        merged["coordinator"] = self.coordinator_name
        merged["auditor"] = self.governance_name
        if self.agent_names:
            for k, v in self.agent_names.items():
                if v and str(v).strip():
                    merged[k] = str(v).strip()
        self.agent_names = merged
        self.coordinator_name = merged["coordinator"]
        self.governance_name = merged["auditor"]


@dataclass
class InstallResult:
    """Raport z operacji powołania nowej instancji."""
    success: bool
    target_dir: Path
    instance_name: str
    elapsed_seconds: float
    created_items: List[str] = field(default_factory=list)
    skipped_items: List[str] = field(default_factory=list)
    preflight: Optional[PreflightReport] = None
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    smoke_check_passed: bool = True
    smoke_check_details: List[str] = field(default_factory=list)


def generate_initial_config_toml(
    instance_name: str = "LAB",
    user_name: str = "Użytkownik",
    call_sign: str = "Użytkownik",
    theme_preset: str = "dawn",
    theme_preset_light: str = "dawn",
    theme_preset_dark: str = "tokyonight",
    theme_mode: str = "system",
    coordinator_name: str = "Adam",
    governance_name: str = "Ada",
    profile: str = "full",
    agent_names: Optional[Dict[str, str]] = None,
    enable_projects: bool = True,
    enable_operations: bool = True,
    github_enabled: bool = False,
    icloud_enabled: bool = False,
    llm_provider: str = "antigravity",
    llm_model: str = "auto",
) -> str:
    """Generuje kanoniczny plik konfiguracyjny lab.config.toml dla nowej instancji LAB 3.0."""
    names = dict(DEFAULT_AGENT_NAMES)
    names["coordinator"] = coordinator_name
    names["auditor"] = governance_name
    if agent_names:
        for k, v in agent_names.items():
            if v and str(v).strip():
                names[k] = str(v).strip()

    if profile == "project":
        rooms_parts = [
            """[rooms.r01_inbox]
id = "r01_inbox"
code = "01"
role = "ingestion"
policy = "drain-to-zero"
name = "Skrzynka"
path = "01 Skrzynka"
color = "#bf616a"

[rooms.r02_workshop]
id = "r02_workshop"
code = "02"
role = "daily-workbench"
policy = "daily-journal-wip"
name = "Warsztat"
path = "02 Warsztat"
color = "#d19a66"
entry_file = "Dashboard LAB.md\"""",
        ]
        if enable_projects:
            rooms_parts.append("""[rooms.r03_projects]
id = "r03_projects"
code = "03"
role = "active-projects"
policy = "bounded-initiatives"
name = "Projekty"
path = "03 Projekty"
color = "#61afef"
entry_file = "_Spis Projektów.md\"""")
        rooms_parts.append("""[rooms.r07_databases]
id = "r07_databases"
code = "07"
role = "sqlite-relational"
policy = "read-heavy-wal"
name = "Bazy Danych"
path = "07 Bazy Danych"
color = "#b57a86"

[rooms.r08_aiteam]
id = "r08_aiteam"
code = "08"
role = "governance-and-engine"
policy = "ai-orchestration"
name = "Zespół AI"
path = "08 Zespół AI"
color = "#c678dd\"""")
        rooms_toml = "# SŁOWNIK TOPOGRAFII PRZESTRZENNEJ (KAPSUŁA PROJEKTOWA)\n\n" + "\n\n".join(rooms_parts)
    else:
        rooms_parts = [
            """[rooms.r01_inbox]
id = "r01_inbox"
code = "01"
role = "ingestion"
policy = "drain-to-zero"
name = "Skrzynka"
path = "01 Skrzynka"
color = "#bf616a"

[rooms.r02_workshop]
id = "r02_workshop"
code = "02"
role = "daily-workbench"
policy = "daily-journal-wip"
name = "Warsztat"
path = "02 Warsztat"
color = "#d19a66"
entry_file = "Dashboard LAB.md\"""",
        ]
        if enable_projects:
            rooms_parts.append("""[rooms.r03_projects]
id = "r03_projects"
code = "03"
role = "active-projects"
policy = "bounded-initiatives"
name = "Projekty"
path = "03 Projekty"
color = "#61afef"
entry_file = "_Spis Projektów.md\"""")
        if enable_operations:
            rooms_parts.append("""[rooms.r04_operations]
id = "r04_operations"
code = "04"
role = "lifecycle-routines"
policy = "continuous-responsibilities"
name = "Operacje"
path = "04 Operacje"
color = "#2982db"
entry_file = "_Spis Operacji.md\"""")
        rooms_parts.append("""[rooms.r05_knowledge]
id = "r05_knowledge"
code = "05"
role = "atomic-synthesis-and-research"
policy = "evergreen-and-research"
name = "Baza Wiedzy"
path = "05 Baza Wiedzy"
color = "#98c379"

[rooms.r06_assets]
id = "r06_assets"
code = "06"
role = "reference-and-assets"
policy = "reference-archive"
name = "Zasoby"
path = "06 Zasoby"
color = "#b48ead"

[rooms.r07_databases]
id = "r07_databases"
code = "07"
role = "sqlite-relational"
policy = "read-heavy-wal"
name = "Bazy Danych"
path = "07 Bazy Danych"
color = "#b57a86"

[rooms.r08_aiteam]
id = "r08_aiteam"
code = "08"
role = "governance-and-engine"
policy = "ai-orchestration"
name = "Zespół AI"
path = "08 Zespół AI"
color = "#c678dd\"""")
        rooms_toml = "# SŁOWNIK TOPOGRAFII PRZESTRZENNEJ (LAB 3.0)\n\n" + "\n\n".join(rooms_parts)

    vcs_enabled = bool(github_enabled and not icloud_enabled)
    e = _toml_escape

    return f"""# lab.config.toml - Referencyjny plik konfiguracyjny LAB 3.0 (Framework PZW)
# Standard: TOML (v1.0.0), moduł wbudowany: tomllib (Python 3.11+)

[system]
id = "lab-pzw-{e(instance_name.lower())}"
instance_name = "{e(instance_name)}"
version = "{LAB_VERSION}"
language = "pl"
timezone = "Europe/Warsaw"

[user]
name = "{e(user_name)}"
formal_title = "Właściciel Systemu"
role = "system-owner"
call_sign = "{e(call_sign)}"

[storage]
root_var = "VAULT_ROOT"
relative_mode = true
root_indicator = "lab.config.toml"
icloud_sync = {"true" if icloud_enabled else "false"}

[vcs]
enabled = {"true" if vcs_enabled else "false"}
provider = "github"
remote_url = "https://github.com/clazz2045/LAB.git"
branch = "main"
icloud_guard = true

{rooms_toml}

# ==============================================================================
# KONFIGURACJA SILNIKA LLM I TOŻSAMOŚCI ZESPOŁU AI
# ==============================================================================

[ai_team.llm]
provider = "{e(llm_provider)}"
model = "{e(llm_model)}"
available_providers = ["antigravity", "claude"]

[ai_team.llm.providers.antigravity]
engine = "antigravity-ide"
default_model = "gemini-2.5-pro"
fast_model = "gemini-2.5-flash"

[ai_team.llm.providers.claude]
engine = "anthropic-claude"
default_model = "claude-sonnet-4-5"
audit_model = "claude-opus-4-1"

[ai_team.coordinator]
role_key = "coordinator"
role = "Koordynator Zespołu AI"
assigned_name = "{e(names['coordinator'])}"
contract_file = "08 Zespół AI/Role/Koordynator - {e(names['coordinator'])}/AGENT.md"
default_model = "fast"

[ai_team.governance_qa]
role_key = "auditor"
role = "Niezależny Główny Audytor i Jakość"
assigned_name = "{e(names['auditor'])}"
contract_file = "08 Zespół AI/Role/Audytor - {e(names['auditor'])}/AGENT.md"
default_model = "flagship-audit"

[[ai_team.specialists]]
role_key = "researcher"
role = "Badacz Źródeł i Faktów"
assigned_name = "{e(names['researcher'])}"
name = "{e(names['researcher'])}"
contract_file = "08 Zespół AI/Role/Research - {e(names['researcher'])}/AGENT.md"
slug = "klara"

[[ai_team.specialists]]
role_key = "engineer"
role = "Szef Sztabu Wykonawczego i Główny Inżynier"
assigned_name = "{e(names['engineer'])}"
name = "{e(names['engineer'])}"
contract_file = "08 Zespół AI/Role/Inżynier - {e(names['engineer'])}/AGENT.md"
slug = "maks"

[[ai_team.specialists]]
role_key = "designer"
role = "Architekt Systemu Designu i Ergonomii Wizualnej"
assigned_name = "{e(names['designer'])}"
name = "{e(names['designer'])}"
contract_file = "08 Zespół AI/Role/Design - {e(names['designer'])}/AGENT.md"
slug = "maja"

[[ai_team.specialists]]
role_key = "knowledge"
role = "Specjalista Syntezy i Organizacji Wiedzy"
assigned_name = "{e(names['knowledge'])}"
name = "{e(names['knowledge'])}"
contract_file = "08 Zespół AI/Role/Wiedza - {e(names['knowledge'])}/AGENT.md"
slug = "edyta"

[[ai_team.specialists]]
role_key = "database_code"
role = "Główny Architekt Struktury i Baz Danych"
assigned_name = "{e(names['database_code'])}"
name = "{e(names['database_code'])}"
contract_file = "08 Zespół AI/Role/Bazy Danych - {e(names['database_code'])}/AGENT.md"
slug = "kinga"

# ==============================================================================
# SŁOWNIK PREZENTACJI I DESIGN SYSTEMU
# ==============================================================================

[presentation]
theme_preset = "{e(theme_preset)}"
theme_preset_light = "{e(theme_preset_light)}"
theme_preset_dark = "{e(theme_preset_dark)}"
theme_mode = "{e(theme_mode)}"
font_sans = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif"
font_mono = "ui-monospace, 'SF Mono', Menlo, monospace"

# ==============================================================================
# SŁOWNIK POLITYK BEZPIECZEŃSTWA I INTEGRALNOŚCI (POLICIES)
# ==============================================================================

[policies.human_thoughts]
protect_unorganized_thoughts = true
forbid_auto_sprint_injection = true
require_explicit_confirmation = true
description = "Bezwzględny zakaz samowolnego dopisywania nieuporządkowanych myśli użytkownika do Warsztatu/Sprintu."

[policies.workshop]
allowed_task_types = ["PR", "OP"]
forbidden_task_types = ["RS"]
forbidden_sections = ["Pokoje Robocze", "Zrzut ze Skrzynki", "Filar IV: Badania i Synteza Wiedzy"]
description = "Warsztat jest stołem roboczym wyłącznie dla projektów (PR) i operacji (OP)."
"""


class LabEngineInstaller:
    """Silnik powoływania nowej, dziewiczej instancji środowiska LAB 3.0."""

    def __init__(self, options: InstallOptions) -> None:
        self.options = options
        self.created: List[str] = []
        self.skipped: List[str] = []
        self.warnings: List[str] = []

    def run(self) -> InstallResult:
        """Uruchamia pełny proces instalacji."""
        start_time = time.perf_counter()
        self.created.clear()
        self.skipped.clear()
        self.warnings.clear()

        # 1. Pre-flight diagnostics check
        preflight: Optional[PreflightReport] = None
        if not self.options.skip_preflight:
            preflight = run_preflight_diagnostics()
            if not preflight.all_critical_passed:
                err_msg = "Pre-flight check nie powiódł się. Brakuje wymagań krytycznych."
                return InstallResult(
                    success=False,
                    target_dir=self.options.target_dir,
                    instance_name=self.options.instance_name,
                    elapsed_seconds=round(time.perf_counter() - start_time, 4),
                    created_items=self.created,
                    skipped_items=self.skipped,
                    preflight=preflight,
                    error_message=err_msg,
                )

        target = self.options.target_dir

        # 1B. Bramki iCloud: blokada .git/.venv/node_modules oraz Optimize Mac Storage = OFF
        if self.options.icloud_enabled:
            if target.exists():
                hazards = scan_icloud_hazards(target)
                if hazards["forbidden_dirs"]:
                    listed = ", ".join(hazards["forbidden_dirs"][:5])
                    return InstallResult(
                        success=False,
                        target_dir=target,
                        instance_name=self.options.instance_name,
                        elapsed_seconds=round(time.perf_counter() - start_time, 4),
                        preflight=preflight,
                        error_message=(
                            f"QG-1 / iCloud Guard: Wykryto niedozwolone katalogi ({listed}) w ścieżce iCloud. "
                            "Usuń .git, .venv/venv i node_modules z katalogu iCloud przed instalacją."
                        ),
                    )
                if hazards["icloud_placeholders"]:
                    return InstallResult(
                        success=False,
                        target_dir=target,
                        instance_name=self.options.instance_name,
                        elapsed_seconds=round(time.perf_counter() - start_time, 4),
                        preflight=preflight,
                        error_message=(
                            "Bramka iCloud: Wykryto pliki placeholderowe .icloud w docelowym katalogu. "
                            "Wyłącz 'Optimize Mac Storage' (Optimize Mac Storage = OFF) i pobierz pliki lokalnie."
                        ),
                    )
            icloud_item = check_icloud_storage(target if target.exists() else None)
            if not icloud_item.passed:
                return InstallResult(
                    success=False,
                    target_dir=target,
                    instance_name=self.options.instance_name,
                    elapsed_seconds=round(time.perf_counter() - start_time, 4),
                    preflight=preflight,
                    error_message=f"Bramka iCloud: {icloud_item.details} {icloud_item.remedy or ''}".strip(),
                )
            if sys.platform == "darwin" and not self.options.confirm_icloud_optimize_off:
                self.warnings.append(
                    "iCloud (macOS): Upewnij się, że opcja 'Optimize Mac Storage' jest WYŁĄCZONA (OFF)."
                )

        target.mkdir(parents=True, exist_ok=True)

        # QG-1: Ostrzeżenie i twarda blokada .git wewnątrz iCloud
        if self.options.icloud_enabled and self.options.github_enabled:
            self.warnings.append(
                "QG-1: Wykryto iCloud Drive — aktywacja Git/GitHub wewnątrz iCloud została zablokowana."
            )

        # 2. Zapis lab.config.toml w korzeniu instancji (+ .bak i walidacja tomllib)
        config_path = target / "lab.config.toml"
        if not config_path.exists() or self.options.force:
            if config_path.exists():
                bak = _backup_if_exists(config_path)
                if bak:
                    self.created.append(bak.name)
            config_content = generate_initial_config_toml(
                instance_name=self.options.instance_name,
                user_name=self.options.user_name,
                call_sign=self.options.call_sign or self.options.user_name,
                theme_preset=self.options.theme_preset,
                theme_preset_light=self.options.theme_preset_light,
                theme_preset_dark=self.options.theme_preset_dark,
                theme_mode=self.options.theme_mode,
                coordinator_name=self.options.coordinator_name,
                governance_name=self.options.governance_name,
                profile=self.options.profile,
                agent_names=self.options.agent_names,
                enable_projects=self.options.enable_projects,
                enable_operations=self.options.enable_operations,
                github_enabled=self.options.github_enabled,
                icloud_enabled=bool(self.options.icloud_enabled),
                llm_provider=self.options.llm_provider,
                llm_model=self.options.llm_model,
            )
            tomllib.loads(config_content)
            config_path.write_text(config_content, encoding="utf-8")
            self.created.append("lab.config.toml")
        else:
            self.skipped.append("lab.config.toml (istnieje)")

        # Załaduj ConfigManager dla nowo utworzonej instancji
        config_mgr = ConfigManager(config_path=config_path, vault_root=target)

        # 2B. Opcjonalny .gitignore przy aktywnym GitHub poza iCloud
        if self.options.github_enabled and not self.options.icloud_enabled:
            gi_path = target / ".gitignore"
            if not gi_path.exists() or self.options.force:
                if gi_path.exists():
                    _backup_if_exists(gi_path)
                gi_path.write_text(VAULT_GITIGNORE_CONTENT, encoding="utf-8")
                self.created.append(".gitignore")
            else:
                self.skipped.append(".gitignore (istnieje)")

        # 3. Utworzenie pokoi rdzenia oraz podfolderów
        self._create_rooms(config_mgr)

        # 4. Utworzenie czystych plików zarządczych
        self._create_management_files(config_mgr)

        # 5. Konfiguracja Obsidiana (.obsidian/appearance.json, app.json, bookmarks.json, lab-design-tokens.css)
        self._create_obsidian_config(config_mgr)

        # 6. Inicjalizacja dziewiczej bazy Kingi (07 Bazy Danych/lab_master.db)
        self._init_database(config_mgr)

        # 7. Kopiowanie czystego silnika Zespołu AI do pokoju 08 Zespół AI
        if self.options.copy_engine:
            self._copy_engine_components(config_mgr)

        # 8. Weryfikacja obecności .agents/hooks.json przy wyborze Antigravity
        antigravity_ok = True
        antigravity_msg = ""
        if self.options.llm_provider.strip().lower() == "antigravity":
            hooks_file = target / ".agents" / "hooks.json"
            if hooks_file.is_file():
                try:
                    json.loads(hooks_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    antigravity_ok = False
                    antigravity_msg = f"Uszkodzony plik .agents/hooks.json dla Antigravity: {exc}"
            elif self.options.copy_engine:
                antigravity_ok = False
                antigravity_msg = "Brak wymaganego pliku .agents/hooks.json dla dostawcy Antigravity."
            else:
                self.warnings.append(
                    "Antigravity: brak pliku '.agents/hooks.json' (pominięto kopiowanie silnika)."
                )

        # 9. Post-Install Smoke Check
        smoke_ok = True
        smoke_details: List[str] = []
        if self.options.run_smoke_check:
            smoke_ok, smoke_details = run_post_install_smoke_check(
                target,
                check_bootstrap=self.options.copy_engine,
                llm_provider=self.options.llm_provider,
            )

        final_ok = bool(smoke_ok and antigravity_ok)
        err_msg: Optional[str] = None
        if not antigravity_ok:
            err_msg = antigravity_msg
        elif not smoke_ok:
            err_msg = "Post-Install Smoke Check nie powiódł się."

        elapsed = time.perf_counter() - start_time
        return InstallResult(
            success=final_ok,
            target_dir=target,
            instance_name=self.options.instance_name,
            elapsed_seconds=round(elapsed, 4),
            created_items=self.created,
            skipped_items=self.skipped,
            preflight=preflight,
            error_message=err_msg,
            warnings=self.warnings,
            smoke_check_passed=smoke_ok,
            smoke_check_details=smoke_details,
        )

    def _create_rooms(self, config: ConfigManager) -> None:
        """Tworzy katalogi 8 pokoi rdzenia i ich pliki README.md."""
        for room in sorted(config.rooms.values(), key=lambda r: r.code):
            room_dir = config.get_room_path(room.id, absolute=True)
            if not room_dir.exists():
                room_dir.mkdir(parents=True, exist_ok=True)
                self.created.append(f"{room.path}/")
            else:
                self.skipped.append(f"{room.path}/ (istnieje)")

            # Specjalne podfoldery _archiwum dla pokojów z cyklem życia
            if room.id in ("r02_workshop", "r03_projects", "r04_operations"):
                arch_dir = room_dir / "_archiwum"
                if not arch_dir.exists():
                    arch_dir.mkdir(parents=True, exist_ok=True)
                    self.created.append(f"{room.path}/_archiwum/")

            # Podfoldery zasobów (pokój 06)
            if room.id == "r06_assets":
                for sub in ("Notatki", "Obrazy", "Audio", "Dokumenty"):
                    sub_dir = room_dir / sub
                    if not sub_dir.exists():
                        sub_dir.mkdir(parents=True, exist_ok=True)
                        self.created.append(f"{room.path}/{sub}/")

            # Zabezpieczenie pustej Skrzynki przed pominięciem w archiwach ZIP / Git (drain-to-zero)
            if room.id == "r01_inbox":
                keep_file = room_dir / ".gitkeep"
                if not keep_file.exists():
                    keep_file.write_text("", encoding="utf-8")
                    self.created.append(f"{room.path}/.gitkeep")

    def _create_management_files(self, config: ConfigManager) -> None:
        """Generuje czyste pliki zarządcze bez danych prywatnych."""
        # 1. Dashboard LAB
        workshop_room = config.get_room_by_role("daily-workbench")
        workshop_file = config.vault_root / workshop_room.path / (workshop_room.entry_file or "Dashboard LAB.md")
        if not workshop_file.exists() or self.options.force:
            workshop_file.write_text(generate_workshop_entry(workshop_room, config), encoding="utf-8")
            self.created.append(str(workshop_file.relative_to(config.vault_root)))
        else:
            self.skipped.append(str(workshop_file.relative_to(config.vault_root)))

        # 2. _Spis Projektów (tylko gdy pokój istnieje)
        if any(r.role.lower() == "active-projects" for r in config.rooms.values()):
            projects_room = config.get_room_by_role("active-projects")
            projects_file = config.vault_root / projects_room.path / (projects_room.entry_file or "_Spis Projektów.md")
            if not projects_file.exists() or self.options.force:
                if projects_file.exists():
                    _backup_if_exists(projects_file)
                projects_file.write_text(generate_projects_index(projects_room, config), encoding="utf-8")
                self.created.append(str(projects_file.relative_to(config.vault_root)))
            else:
                self.skipped.append(str(projects_file.relative_to(config.vault_root)))

        # 3. _Spis Operacji (tylko w profilu pełnym, gdy pokój istnieje)
        if any(r.role.lower() == "lifecycle-routines" for r in config.rooms.values()):
            ops_room = config.get_room_by_role("lifecycle-routines")
            ops_file = config.vault_root / ops_room.path / (ops_room.entry_file or "_Spis Operacji.md")
            if not ops_file.exists() or self.options.force:
                ops_file.write_text(generate_operations_index(ops_room, config), encoding="utf-8")
                self.created.append(str(ops_file.relative_to(config.vault_root)))
            else:
                self.skipped.append(str(ops_file.relative_to(config.vault_root)))

        # 4. Dashboard Projektu (w profilu project)
        if self.options.profile == "project":
            dashboard_file = config.vault_root / workshop_room.path / "Dashboard Projektu.md"
            if not dashboard_file.exists() or self.options.force:
                dash_content = f"""---
type: project-dashboard
title: "Dashboard Projektu"
instance: "{config.system.instance_name}"
status: active
created: {time.strftime('%Y-%m-%d')}
---

# 📊 Dashboard Projektu ({config.system.instance_name})

> **Autonomiczne Centrum Koordynacji Projektu**  
> *Środowisko: Kapsuła Projektowa LAB (Framework PZW)*

> [!WARNING] Autonomiczna Kapsuła Projektowa ({config.system.instance_name})
> Modyfikacje skryptów i procedur w tym skarbcu są lokalne i nie wracają do głównego LAB-a.

---

## 🎯 Cel Główny i Zakres Projektu

- **Nazwa Projektu:** {config.system.instance_name}
- **Właściciel:** {config.user.name}
- **Koordynator AI:** {config.get_agent_name('coordinator')}
- **Audytor Jakości:** {config.get_agent_name('auditor')}
- **Status:** 🟢 Aktywny

---

## 📋 Etapy Realizacji

- [ ] **Etap 1: Inicjalizacja i Architektura**
- [ ] **Etap 2: Realizacja i Weryfikacja Maszynowa**
- [ ] **Etap 3: Wdrożenie i Archiwizacja**

---

## 🗄️ Relacyjna Baza Danych i Magazyn

- Główna Baza SQLite: `{config.get_room_by_role('sqlite-relational').path}/lab_master.db`
- Bufor Wejściowy: `{config.get_room_by_role('ingestion').path}/`
"""
                dashboard_file.write_text(dash_content, encoding="utf-8")
                self.created.append(str(dashboard_file.relative_to(config.vault_root)))

        # 5. AGENTS.md (Główny kontrakt AI)
        agents_file = config.vault_root / "AGENTS.md"
        if not agents_file.exists() or self.options.force:
            agents_file.write_text(generate_agents_contract(config), encoding="utf-8")
            self.created.append("AGENTS.md")
        else:
            self.skipped.append("AGENTS.md (istnieje)")

    def _create_obsidian_config(self, config: ConfigManager) -> None:
        """Tworzy konfigurację katalogu .obsidian."""
        obsidian_dir = config.vault_root / ".obsidian"
        snippets_dir = obsidian_dir / "snippets"
        snippets_dir.mkdir(parents=True, exist_ok=True)

        # 1. CSS tokens
        css_file = snippets_dir / "lab-design-tokens.css"
        if not css_file.exists() or self.options.force:
            if css_file.exists():
                _backup_if_exists(css_file)
            css_file.write_text(generate_design_tokens_css(config), encoding="utf-8")
            self.created.append(".obsidian/snippets/lab-design-tokens.css")
        else:
            self.skipped.append(".obsidian/snippets/lab-design-tokens.css (istnieje)")

        # 2. appearance.json
        app_file = obsidian_dir / "appearance.json"
        if not app_file.exists() or self.options.force:
            if app_file.exists():
                _backup_if_exists(app_file)
            app_data = {
                "baseFontSize": 16,
                "theme": self.options.theme_mode if self.options.theme_mode in ("system", "light", "dark") else "system",
                "enabledCssSnippets": ["lab-design-tokens"],
                "accentColor": "#0284c7",
                "showRibbon": True,
            }
            serialized = json.dumps(app_data, indent=2, ensure_ascii=False) + "\n"
            json.loads(serialized)
            app_file.write_text(serialized, encoding="utf-8")
            self.created.append(".obsidian/appearance.json")
        else:
            self.skipped.append(".obsidian/appearance.json (istnieje)")

        # 3. app.json (Włącza widoczność plików .html, .db itp. w drzewie plików Obsidiana)
        app_json_file = obsidian_dir / "app.json"
        if not app_json_file.exists() or self.options.force:
            if app_json_file.exists():
                _backup_if_exists(app_json_file)
            app_json_data = {
                "attachmentFolderPath": config.get_room_by_role("ingestion").path,
                "showUnsupportedFiles": True,
                "alwaysUpdateLinks": True,
                "newLinkFormat": "shortest",
                "useMarkdownLinks": False,
                "uriCallbacks": True,
                "userIgnoreFilters": [
                    "/^(?!AGENTS\\.md$)(?!0[1-8] )[^/]+$/",
                    "__pycache__",
                    ".pytest_cache",
                    ".DS_Store",
                ],
            }
            serialized = json.dumps(app_json_data, indent=2, ensure_ascii=False) + "\n"
            json.loads(serialized)
            app_json_file.write_text(serialized, encoding="utf-8")
            self.created.append(".obsidian/app.json")
        else:
            self.skipped.append(".obsidian/app.json (istnieje)")

        # 4. bookmarks.json
        bookmarks_items = [
            {
                "type": "file",
                "title": "Dashboard LAB",
                "path": f"{config.get_room_by_role('daily-workbench').path}/Dashboard LAB.md",
            },
        ]
        if self.options.profile == "project":
            bookmarks_items.append({
                "type": "file",
                "title": "Dashboard Projektu",
                "path": f"{config.get_room_by_role('daily-workbench').path}/Dashboard Projektu.md",
            })
        if any(r.role.lower() == "active-projects" for r in config.rooms.values()):
            bookmarks_items.append({
                "type": "file",
                "title": "Spis Projektów",
                "path": f"{config.get_room_by_role('active-projects').path}/_Spis Projektów.md",
            })
        if any(r.role.lower() == "lifecycle-routines" for r in config.rooms.values()):
            bookmarks_items.append({
                "type": "file",
                "title": "Spis Operacji",
                "path": f"{config.get_room_by_role('lifecycle-routines').path}/_Spis Operacji.md",
            })
        bookmarks_items.extend([
            {
                "type": "folder",
                "title": "Skrzynka Wejściowa",
                "path": config.get_room_by_role('ingestion').path,
            },
            {
                "type": "file",
                "title": "Główny Kontrakt AI (AGENTS.md)",
                "path": "AGENTS.md",
            },
        ])
        bookmarks_file = obsidian_dir / "bookmarks.json"
        if not bookmarks_file.exists() or self.options.force:
            if bookmarks_file.exists():
                _backup_if_exists(bookmarks_file)
            bookmarks_data = {"items": bookmarks_items}
            serialized = json.dumps(bookmarks_data, indent=2, ensure_ascii=False) + "\n"
            json.loads(serialized)
            bookmarks_file.write_text(serialized, encoding="utf-8")
            self.created.append(".obsidian/bookmarks.json")
        else:
            self.skipped.append(".obsidian/bookmarks.json (istnieje)")

        # 5. plugins, hotkeys i community-plugins.json (KISS stack: shellcommands, advanced-uri, sqlite-db-viewer)
        src_obsidian = Path(__file__).resolve().parent.parent.parent.parent / ".obsidian"
        src_plugins = src_obsidian / "plugins"
        src_comm = src_obsidian / "community-plugins.json"
        src_hotkeys = src_obsidian / "hotkeys.json"
        src_core_plugins = src_obsidian / "core-plugins.json"

        if src_plugins.is_dir():
            dst_plugins = obsidian_dir / "plugins"
            if not dst_plugins.exists() or self.options.force:
                shutil.copytree(src_plugins, dst_plugins, dirs_exist_ok=True)
                self.created.append(".obsidian/plugins/")
                sc_data = dst_plugins / "obsidian-shellcommands" / "data.json"
                if sc_data.is_file():
                    raw_obj = json.loads(sc_data.read_text(encoding="utf-8"))
                    replaced = _replace_str_in_json(raw_obj, str(src_obsidian.parent), str(config.vault_root))
                    serialized = json.dumps(replaced, indent=2, ensure_ascii=False) + "\n"
                    json.loads(serialized)
                    sc_data.write_text(serialized, encoding="utf-8")

        if src_comm.is_file():
            dst_comm = obsidian_dir / "community-plugins.json"
            if not dst_comm.exists() or self.options.force:
                if dst_comm.exists():
                    _backup_if_exists(dst_comm)
                shutil.copy2(src_comm, dst_comm)
                json.loads(dst_comm.read_text(encoding="utf-8"))
                self.created.append(".obsidian/community-plugins.json")

        if src_hotkeys.is_file():
            dst_hotkeys = obsidian_dir / "hotkeys.json"
            if not dst_hotkeys.exists() or self.options.force:
                if dst_hotkeys.exists():
                    _backup_if_exists(dst_hotkeys)
                shutil.copy2(src_hotkeys, dst_hotkeys)
                json.loads(dst_hotkeys.read_text(encoding="utf-8"))
                self.created.append(".obsidian/hotkeys.json")

        if src_core_plugins.is_file():
            dst_core = obsidian_dir / "core-plugins.json"
            if not dst_core.exists() or self.options.force:
                if dst_core.exists():
                    _backup_if_exists(dst_core)
                shutil.copy2(src_core_plugins, dst_core)
                json.loads(dst_core.read_text(encoding="utf-8"))
                self.created.append(".obsidian/core-plugins.json")

    def _init_database(self, config: ConfigManager) -> None:
        """Inicjalizuje dziewiczą bazę SQLite Kingi lab_master.db oraz Konfigurator Wizualny."""
        db_room = config.get_room_by_role("sqlite-relational")
        db_path = config.vault_root / db_room.path / "lab_master.db"
        if not db_path.exists() or self.options.force:
            init_master_db(db_path, instance_name=config.system.instance_name)
            self.created.append(f"{db_room.path}/lab_master.db")
        else:
            self.skipped.append(f"{db_room.path}/lab_master.db (istnieje)")

        # Inicjalizacja Konfiguratora Wizualnego (Dashboards/Konfigurator_Wizualny_LAB.html)
        dash_dir = config.vault_root / db_room.path / "Dashboards"
        dash_dir.mkdir(parents=True, exist_ok=True)
        try:
            from build_visual_configurator import build_configurator
            cfg_html = build_configurator(vault_root=config.vault_root)
            self.created.append(str(cfg_html.relative_to(config.vault_root)))
        except Exception:
            pass

    def _remap_role_path(self, rel: Path) -> Path:
        """Podmienia imię w folderze roli agenta na wybrane w opcjach instalacji.

        Dzięki temu foldery ról zgadzają się z contract_file w lab.config.toml.
        """
        if not rel.parts:
            return rel
        function, sep, _old_name = rel.parts[0].partition(" - ")
        role_key = ROLE_FOLDER_PREFIXES.get(function)
        if sep and role_key:
            new_name = self.options.agent_names.get(role_key)
            if new_name:
                return Path(f"{function} - {new_name}", *rel.parts[1:])
        return rel

    def _copy_engine_components(self, config: ConfigManager) -> None:
        """Kopiuje moduły lab_core i skrypty startowe do 08 Zespół AI."""
        current_script_dir = Path(__file__).resolve().parent
        # current_script_dir to .../08 Zespół AI/Skrypty/lab_core
        src_skrypty = current_script_dir.parent
        src_ai_root = src_skrypty.parent

        ai_room = config.get_room_by_role("governance-and-engine")
        target_ai_dir = config.vault_root / ai_room.path

        # Upewnij się, że nie kopiujemy samych do siebie
        if target_ai_dir.resolve() == src_ai_root.resolve():
            return

        # Kopiuj kluczowe moduły korzenia (core_config.py, pytest.py, lab-init.py, lab_init.py, launchery)
        src_vault_root = src_ai_root.parent
        for root_file in (
            "core_config.py",
            "pytest.py",
            "lab-init.py",
            "lab_init.py",
            "LAB-Installer.command",
            "LAB-Installer.bat",
        ):
            src_rf = src_vault_root / root_file
            dst_rf = config.vault_root / root_file
            if src_rf.is_file() and (not dst_rf.exists() or self.options.force):
                if dst_rf.exists():
                    _backup_if_exists(dst_rf)
                shutil.copy2(src_rf, dst_rf)
                if sys.platform == "darwin" and dst_rf.suffix == ".command":
                    os.chmod(dst_rf, 0o755)
                    clear_macos_quarantine(dst_rf)
                self.created.append(root_file)

        # Kopiuj .agents/hooks.json (bramka Antigravity)
        src_hooks = src_vault_root / ".agents" / "hooks.json"
        if src_hooks.is_file():
            dst_hooks = config.vault_root / ".agents" / "hooks.json"
            dst_hooks.parent.mkdir(parents=True, exist_ok=True)
            if not dst_hooks.exists() or self.options.force:
                if dst_hooks.exists():
                    _backup_if_exists(dst_hooks)
                shutil.copy2(src_hooks, dst_hooks)
                self.created.append(".agents/hooks.json")

        # Podkatalogi Zespołu AI do powołania
        for category in ("Role", "Skrypty", "Szablony", "Dzienniki Sesji", "Governance"):
            src_cat = src_ai_root / category
            dst_cat = target_ai_dir / category
            dst_cat.mkdir(parents=True, exist_ok=True)

            if category == "Dzienniki Sesji":
                keep = dst_cat / ".gitkeep"
                if not keep.exists():
                    keep.write_text("", encoding="utf-8")
                continue

            if src_cat.is_dir():
                for root, dirs, files in os.walk(src_cat):
                    dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache"}]
                    rel = Path(root).relative_to(src_cat)
                    if category == "Role":
                        rel = self._remap_role_path(rel)
                    dst_sub = dst_cat / rel
                    dst_sub.mkdir(parents=True, exist_ok=True)

                    for f in files:
                        if f in {".DS_Store"} or f.endswith(".pyc"):
                            continue
                        src_f = Path(root) / f
                        dst_f = dst_sub / f
                        if not dst_f.exists() or self.options.force:
                            if dst_f.exists():
                                _backup_if_exists(dst_f)
                            if category == "Role" and src_f.suffix.lower() == ".md":
                                raw_md = src_f.read_text(encoding="utf-8")
                                personalized = _personalize_role_markdown(
                                    raw_md,
                                    self.options.agent_names or DEFAULT_AGENT_NAMES,
                                    self.options.user_name,
                                )
                                dst_f.write_text(personalized, encoding="utf-8")
                            else:
                                shutil.copy2(src_f, dst_f)
                            if sys.platform == "darwin" and "integrations/raycast" in dst_f.as_posix() and dst_f.suffix == ".sh":
                                os.chmod(dst_f, 0o755)
                            self.created.append(str(dst_f.relative_to(config.vault_root)))


def _personalize_role_markdown(
    text: str,
    agent_names: Dict[str, str],
    user_name: str,
) -> str:
    """Podmienia domyślne imiona agentów oraz właściciela w kopiowanych kontraktach Role/*/AGENT.md."""
    out = text
    for role_key, default_name in DEFAULT_AGENT_NAMES.items():
        custom_name = (agent_names.get(role_key) or default_name).strip()
        if not custom_name or custom_name == default_name:
            continue
        out = re.sub(rf"\b{re.escape(default_name)}\b", custom_name, out)
    clean_user = (user_name or "").strip()
    if clean_user:
        for old_owner in ("Zbigniewa", "Zbigniewem", "Zbigniewowi", "Zbigniew"):  # QG2-IGNORE
            out = re.sub(rf"\b{old_owner}\b", clean_user, out)
    return out


def _replace_str_in_json(obj: Any, old: str, new: str) -> Any:
    """Rekurencyjnie podmienia tekst w strukturze JSON bez psucia escapowania."""
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [_replace_str_in_json(item, old, new) for item in obj]
    if isinstance(obj, dict):
        return {k: _replace_str_in_json(v, old, new) for k, v in obj.items()}
    return obj


def run_post_install_smoke_check(
    target_dir: Path,
    check_bootstrap: bool = True,
    llm_provider: str = "",
) -> Tuple[bool, List[str]]:
    """Automatyczny Post-Install Smoke Check (Specyfikacja Instalatora LAB 3.0).

    1. Parsowanie wygenerowanego lab.config.toml (tomllib) oraz plików .json w .obsidian/.
    2. Test zapisu/odczytu SQLite w trybie WAL w docelowej lokalizacji (spacje + UTF-8).
    3. Wywołanie session_bootstrap.py --json (jeśli istnieje) i weryfikacja braku BROKEN.
    """
    details: List[str] = []
    ok = True

    # 1. TOML + JSON
    cfg_path = target_dir / "lab.config.toml"
    try:
        tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        details.append("TOML: OK")
    except Exception as exc:
        details.append(f"TOML: FAIL ({exc})")
        ok = False

    obs_dir = target_dir / ".obsidian"
    if obs_dir.is_dir():
        for jf in obs_dir.glob("*.json"):
            try:
                json.loads(jf.read_text(encoding="utf-8"))
            except Exception as exc:
                details.append(f"JSON ({jf.name}): FAIL ({exc})")
                ok = False
        details.append("JSON (.obsidian): OK")

    # 2. SQLite WAL + UTF-8 test
    db_dir = target_dir / "07 Bazy Danych"
    if db_dir.is_dir():
        smoke_db = db_dir / "_smoke_test.db"
        try:
            conn = sqlite3.connect(str(smoke_db))
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE IF NOT EXISTS _smoke (id INTEGER PRIMARY KEY, txt TEXT);")
            conn.execute("INSERT INTO _smoke (txt) VALUES (?);", ("Zażółć gęślą jaźń 🚀",))
            row = conn.execute("SELECT txt FROM _smoke;").fetchone()
            conn.commit()
            conn.close()
            if row and row[0] == "Zażółć gęślą jaźń 🚀":
                details.append("SQLite WAL + UTF-8: OK")
            else:
                details.append("SQLite WAL + UTF-8: FAIL (odczyt niezgodny)")
                ok = False
        except Exception as exc:
            details.append(f"SQLite WAL + UTF-8: FAIL ({exc})")
            ok = False
        finally:
            for ext in ("", "-wal", "-shm"):
                p = Path(f"{smoke_db}{ext}")
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

    # 3. session_bootstrap.py --json
    bootstrap = target_dir / "08 Zespół AI" / "Skrypty" / "session_bootstrap.py"
    if check_bootstrap and bootstrap.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(bootstrap), "--json"],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
            status = data.get("status", "UNKNOWN")
            if proc.returncode == 0 and status != "BROKEN":
                details.append(f"session_bootstrap: OK ({status})")
            else:
                details.append(f"session_bootstrap: FAIL (status={status}, rc={proc.returncode})")
                ok = False
        except Exception as exc:
            details.append(f"session_bootstrap: FAIL ({exc})")
            ok = False

    # 4. Antigravity hooks.json
    if llm_provider.strip().lower() == "antigravity":
        hooks_path = target_dir / ".agents" / "hooks.json"
        if hooks_path.is_file():
            try:
                json.loads(hooks_path.read_text(encoding="utf-8"))
                details.append("Antigravity hooks.json: OK")
            except Exception as exc:
                details.append(f"Antigravity hooks.json: FAIL ({exc})")
                ok = False
        elif check_bootstrap:
            details.append("Antigravity hooks.json: FAIL (brak pliku)")
            ok = False

    return ok, details


def install_lab_vault(
    target_dir: Path | str,
    instance_name: str = "LAB",
    user_name: str = "Użytkownik",
    call_sign: Optional[str] = None,
    theme_preset: str = "dawn",
    theme_preset_light: str = "dawn",
    theme_preset_dark: str = "tokyonight",
    theme_mode: str = "system",
    coordinator_name: str = "Adam",
    governance_name: str = "Ada",
    agent_names: Optional[Dict[str, str]] = None,
    force: bool = False,
    skip_preflight: bool = False,
    copy_engine: bool = True,
    icloud_enabled: Optional[bool] = None,
    github_enabled: bool = False,
    enable_projects: bool = True,
    enable_operations: bool = True,
    llm_provider: str = "antigravity",
    llm_model: str = "auto",
    run_smoke_check: bool = True,
) -> InstallResult:
    """Wygodna funkcja powołania nowej instancji LAB 3.0."""
    options = InstallOptions(
        target_dir=Path(target_dir),
        instance_name=instance_name,
        user_name=user_name,
        call_sign=call_sign,
        theme_preset=theme_preset,
        theme_preset_light=theme_preset_light,
        theme_preset_dark=theme_preset_dark,
        theme_mode=theme_mode,
        coordinator_name=coordinator_name,
        governance_name=governance_name,
        agent_names=agent_names or {},
        force=force,
        skip_preflight=skip_preflight,
        copy_engine=copy_engine,
        icloud_enabled=icloud_enabled,
        github_enabled=github_enabled,
        enable_projects=enable_projects,
        enable_operations=enable_operations,
        llm_provider=llm_provider,
        llm_model=llm_model,
        run_smoke_check=run_smoke_check,
    )
    installer = LabEngineInstaller(options)
    return installer.run()


def install_project_capsule(
    target_dir: Path | str,
    project_name: str = "Projekt",
    instance_name: Optional[str] = None,
    user_name: str = "Użytkownik",
    call_sign: Optional[str] = None,
    theme_preset: str = "tokyonight",
    coordinator_name: str = "Adam",
    governance_name: str = "Ada",
    force: bool = False,
    skip_preflight: bool = False,
    copy_engine: bool = True,
) -> InstallResult:
    """Wygodna funkcja powołania nowej Kapsuły Projektowej LAB (profil: project)."""
    inst_name = instance_name or f"LAB-{project_name}"
    options = InstallOptions(
        target_dir=Path(target_dir),
        instance_name=inst_name,
        user_name=user_name,
        call_sign=call_sign or user_name,
        theme_preset=theme_preset,
        coordinator_name=coordinator_name,
        governance_name=governance_name,
        force=force,
        skip_preflight=skip_preflight,
        copy_engine=copy_engine,
        profile="project",
    )
    installer = LabEngineInstaller(options)
    return installer.run()
