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
