"""lab_core.constants - Centralny rejestr stałych architektonicznych LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library. Zero zewnętrznych zależności pip.
Jedno Źródło Prawdy (Single Source of Truth) dla całego silnika.
"""

from __future__ import annotations

# Zestaw katalogów ignorowanych przez skanery, parsery i audytory
IGNORED_DIRS: set[str] = {
    ".obsidian",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".trash",
    "_archiwum",
}

# Zestaw plików systemowych ignorowanych przez narzędzia indeksujące
IGNORED_FILES: set[str] = {
    ".DS_Store",
    "desktop.ini",
    "Thumbs.db",
}

# Kanoniczne pliki zarządcze (KISS Governance) - domyślny wzorzec fallback (dokładnie 3 pliki)
# Narzędzia i inspektory jakości pobierają ścieżki dynamicznie przez ConfigManager.get_management_files()
CANONICAL_MANAGEMENT_FILES: tuple[str, ...] = (
    "02 Warsztat/Dashboard LAB.md",
    "03 Projekty/_Spis Projektów.md",
    "04 Operacje/_Spis Operacji.md",
)

# Kody 8 pokoi rdzenia systemu LAB (stałe prawo rdzenia)
CORE_ROOM_CODES: tuple[str, ...] = (
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
)

# Domyślne nazwy folderów 8 pokoi rdzenia (kanoniczny fallback/default)
# Zgodnie z Aksjomatem Nienaruszalności Rdzenia silnik i audytor bazują na ConfigManager.rooms
DEFAULT_CANONICAL_ROOMS: set[str] = {
    "01 Skrzynka",
    "02 Warsztat",
    "03 Projekty",
    "04 Operacje",
    "05 Baza Wiedzy",
    "06 Zasoby",
    "07 Bazy Danych",
    "08 Zespół AI",
}

# Zakazane sekcje na Warsztacie Współpracy (Architektura Czystego Stołu)
FORBIDDEN_WORKSHOP_SECTIONS: tuple[str, ...] = (
    "Pokoje Robocze",
    "Zrzut ze Skrzynki",
    "Filar IV: Badania i Synteza Wiedzy",
)

# Główny kontrakt AI (plik wejściowy) i podkatalog ról w pokoju governance-and-engine
AGENTS_CONTRACT_FILE: str = "AGENTS.md"
ROLES_DIRNAME: str = "Role"

# Frazy, które musi zawierać Reguła 8 (twarda polityka subagentów) w głównym kontrakcie
RULE8_REQUIRED_PHRASES: tuple[str, ...] = (
    "Twarde reguły delegacji i wywoływania subagentów",
    "Obowiązkowa delegacja do subagenta",
    "Zasada Destylatu SAT",
    "Autonomia Koordynatora",
)

# Kanoniczny zestaw identyfikatorów ról systemowych (Aksjomat Nienaruszalności Rdzenia)
CANONICAL_ROLE_KEYS: tuple[str, ...] = (
    "coordinator",
    "auditor",
    "researcher",
    "engineer",
    "designer",
    "knowledge",
    "database_code",
)

# Dozwolone tagi typów wątków w sekcji '## 🎯 Główne Wątki Robocze na Dziś' Agendy Roboczej
AGENDA_THREAD_TYPES: tuple[str, ...] = (
    "PROMPT",
    "PYTANIE",
    "ZADANIE",
    "DECYZJA",
)

# Markery oznaczające przetworzony blok myśli w sekcji '## ✍️' Agendy Roboczej (bez edycji tekstu człowieka)
THOUGHT_PROCESSED_MARKERS: tuple[str, ...] = (
    "<!-- processed",
    "<!-- przetworzone",
    "<!-- distilled",
)

# Ścisła biała lista kanonicznych plików jądra silnika (Default-Deny dla 08 Zespół AI/Skrypty/)
# Każdy plik poza tą listą (poza plikami tymczasowymi/systemowymi) powinien trafić do 02 Warsztat/Skrypty/
# lub właściwego katalogu projektu w 03 Projekty/{project_id}/.
CANONICAL_ENGINE_ALLOWLIST: frozenset[str] = frozenset({
    # Punkty wejścia i dozwolone pliki katalogu głównego Skrypty/
    "__init__.py",
    ".gitignore",
    "README",
    "README.md",
    "check_quality.py",
    "checkpoint.py",
    "core_config.py",
    "drain_inbox.py",
    "projects.py",
    "rollover_agenda.py",
    "session_bootstrap.py",
    "stop_guard.py",
    # Integracje systemowe
    "integrations/raycast/lab-mail-to-lab-raycast.sh",
    "integrations/raycast/lab-open-scratchpad-raycast.sh",
    "integrations/raycast/lab-quick-capture-raycast.sh",
    "integrations/raycast/lab-set-theme-raycast.sh",
    "integrations/raycast/lab-start-session-raycast.sh",
    "integrations/raycast/lab-token-telemetry-raycast.sh",
    # Rdzeń lab_core
    "lab_core/__init__.py",
    "lab_core/capsule.py",
    "lab_core/compliance.py",
    "lab_core/config.py",
    "lab_core/constants.py",
    "lab_core/dashboards.py",
    "lab_core/diagnostics.py",
    "lab_core/distillate.py",
    "lab_core/env.py",
    "lab_core/export.py",
    "lab_core/git.py",
    "lab_core/installer.py",
    "lab_core/mail.py",
    "lab_core/markdown.py",
    "lab_core/metadata.py",
    "lab_core/pre_tool_guard.py",
    "lab_core/prompt.py",
    "lab_core/radar.py",
    "lab_core/routing.py",
    "lab_core/schema.py",
    "lab_core/scratchpad.py",
    "lab_core/security.py",
    "lab_core/setup.py",
    "lab_core/skills.py",
    "lab_core/stop_guard.py",
    "lab_core/subagents.py",
    "lab_core/telemetry.py",
    "lab_core/templates.py",
    "lab_core/themes.py",
    # Motywy druku PDF
    "lab_core/print_themes/_base_book.css",
    "lab_core/print_themes/book-classic.css",
    "lab_core/print_themes/editorial.css",
    "lab_core/print_themes/manuscript.css",
    "lab_core/print_themes/swiss.css",
    # Podsystem jakości
    "lab_core/quality/__init__.py",
    "lab_core/quality/context.py",
    "lab_core/quality/inspectors/__init__.py",
    "lab_core/quality/inspectors/assets.py",
    "lab_core/quality/inspectors/engine_purity.py",
    "lab_core/quality/inspectors/governance_style.py",
    "lab_core/quality/inspectors/inbox.py",
    "lab_core/quality/inspectors/management.py",
    "lab_core/quality/inspectors/notes_links.py",
    "lab_core/quality/inspectors/portability.py",
    "lab_core/quality/inspectors/products.py",
    "lab_core/quality/inspectors/readmes.py",
    "lab_core/quality/inspectors/rooms.py",
    "lab_core/quality/inspectors/skills.py",
    "lab_core/quality/inspectors/subagents.py",
    "lab_core/quality/inspectors/workshop.py",
    # Zestaw testów jednostkowych (100% stdlib unittest)
    "tests/__init__.py",
    "tests/test_agenda_workflow.py",
    "tests/test_checkpoint.py",
    "tests/test_diagnostics.py",
    "tests/test_drain_and_quality.py",
    "tests/test_engine_purity.py",
    "tests/test_git_sync.py",
    "tests/test_installer.py",
    "tests/test_lab_core.py",
    "tests/test_mail_to_lab.py",
    "tests/test_metadata.py",
    "tests/test_pdf_export_style.py",
    "tests/test_phase6_gate.py",
    "tests/test_portability.py",
    "tests/test_pre_tool_guard.py",
    "tests/test_projects.py",
    "tests/test_release_guard.py",
    "tests/test_schema.py",
    "tests/test_session_bootstrap.py",
    "tests/test_session_prompt.py",
    "tests/test_setup.py",
    "tests/test_skills_registry.py",
    "tests/test_stop_guard.py",
    "tests/test_subagents_policy.py",
    "tests/test_telemetry.py",
    "tests/test_token_telemetry.py",
    "tests/test_vault_radar.py",
})
