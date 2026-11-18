#!/usr/bin/env python3
"""git.py - Deterministyczny synchronizator i eksporter LAB do Git / GitHub

Standard inżynieryjny:
- 100% Python Standard Library (argparse, datetime, os, pathlib, re, shutil, subprocess, sys, zipfile).
- Oparte o core_config.ConfigManager (brak hardkodowanych ścieżek).
- Zero zewnętrznych zależności pip.

Zapewnia jednokierunkowy eksport zsanitowanego rdzenia LAB poza iCloud Drive:
- Czyszczenie katalogu docelowego przed synchronizacją (z zachowaniem .git/).
- Eksport lekkiej konfiguracji .obsidian/ (app.json, appearance.json, hotkeys.json, snippets/).
- Tworzenie .gitkeep dla pustych pokoi 01–07 oraz 08 Zespół AI/Dzienniki Sesji/.
- Bramka QG-2: skaner PII, kluczy API, twardych ścieżek domowych i prywatnych URL-i.
- Tryb orphan commit oraz budowanie paczki .zip dla GitHub Releases.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import zipfile

sys.dont_write_bytecode = True

_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigManager

DEFAULT_LOCAL_REPO = Path.home() / "LAB-OpenSource"
DEFAULT_REMOTE_URL = "https://github.com/clazz2045/LAB.git"

# Ścisła biała lista (Allowlist / Default-Deny) eksportu
ROOT_FILES = [
    "core_config.py",
    "lab-init.py",
    "lab_init.py",
    "pytest.py",
    "lab.config.example.toml",
    "AGENTS.md",
    "LAB-Installer.command",
    "LAB-Installer.bat",
    "LAB-Update.command",
    "LAB-Update.bat",
]

GENERATED_ROOT_FILES = {".gitignore", "README.md", "LICENSE", "CONTRIBUTING.md"}
ALLOWED_ROOT_FILES: Set[str] = set(ROOT_FILES) | GENERATED_ROOT_FILES

ALLOWED_DOCS_EXTENSIONS: Set[str] = {".pdf", ".md", ".html"}

DEFAULT_ROOM_PATHS: Set[str] = {
    "01 Skrzynka",
    "02 Warsztat",
    "03 Projekty",
    "04 Operacje",
    "05 Baza Wiedzy",
    "06 Zasoby",
    "07 Bazy Danych",
}

ALLOWED_AI_SUBDIRS: Set[str] = {
    "Skrypty",
    "Role",
    "Szablony",
    "Governance",
    "Dzienniki Sesji",
}

ALLOWED_SKRYPTY_SUBDIRS: Set[str] = {"lab_core", "tests", "integrations"}
ALLOWED_INTEGRATIONS_SUBDIRS: Set[str] = {"raycast"}
OBSIDIAN_LIGHT_FILES = ["app.json", "appearance.json", "hotkeys.json"]
ALLOWED_OBSIDIAN_FILES: Set[str] = set(OBSIDIAN_LIGHT_FILES)

GITIGNORE_CONTENT = """# LAB - Git Ignore Standard
__pycache__/
*.py[cod]
*$py.class
*.so
.DS_Store
.pytest_cache/
.coverage
htmlcov/
*.tmp
*.log
*.bak
*.db
*.db-wal
*.db-shm
*-wal
*-shm
lab.config.toml
.obsidian/workspace*.json
.obsidian/plugins/
.obsidian/wispr_sync_state.json
.venv/
env/
venv/
"""

LICENSE_CONTENT = """MIT License

Copyright (c) 2026 LAB Open Source (clazz2045)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

README_CONTENT = """# LAB – Silnik Rdzenia i Governance Zespołu AI dla środowiska Obsidian

**Deterministyczny silnik, architektura agentowa i korpus ładu działający w środowisku Obsidian (Partner Zarządzania Wiedzą).**

Projekt rozwijany w standardzie **SAT (Szukaj · Analizuj · Twórz)** oraz **100% Python stdlib (Zero-Pip)**.

> **Licencja MIT · Klauzula Showcase / As-Is (No-SLA):** Repozytorium udostępniane jest na licencji **MIT** (zobacz `LICENSE`) w trybie *Showcase / As-Is*, bez gwarancji wsparcia technicznego, utrzymania ani SLA.

---

## Szybki start (macOS / Windows)

**Wariant 1 – Paczka instalacyjna (`LAB-Installer.zip` z GitHub Releases):**
Rozpakuj archiwum. Na wierzchu zobaczysz launchery: `LAB-Installer.command` / `LAB-Update.command` (macOS) oraz `LAB-Installer.bat` / `LAB-Update.bat` (Windows) – cały wsad instalacyjny jest bezpiecznie ukryty w `.lab-payload/`.

**Wariant 2 – Klonowanie repozytorium:**
```bash
git clone https://github.com/clazz2045/LAB.git
cd LAB
```

- **macOS:** uruchom `LAB-Installer.command` (lub `python3 lab-init.py`)
- **Windows:** uruchom `LAB-Installer.bat` (lub `py -3 lab-init.py`)
- **Tryb bezinteraktywny (CI):** `python3 lab-init.py --non-interactive --target-dir ~/Documents/LAB`

### Aktualizacja istniejącego środowiska LAB (1 kliknięcie)

Masz już własny skarbiec LAB i chcesz wgrać najnowszą wersję silnika? Zrobisz to w 3 prostych krokach:

1. Pobierz plik `LAB-Update.command` (macOS) lub `LAB-Update.bat` (Windows) z repozytorium lub z paczki Release (`LAB-Installer.zip`).
2. Przenieś pobrany plik do głównego katalogu swojego skarbca LAB (obok pliku `lab.config.toml`).
3. Kliknij dwukrotnie w plik. Skrypt automatycznie i bezpiecznie pobierze najnowszy silnik do `08 Zespół AI/Skrypty/` oraz zaktualizuje konfigurację agentów, gwarantując 100% nienaruszalności Twoich prywatnych notatek, projektów i baz danych (pokoje `01–07`).

### Uwaga: Gatekeeper macOS (pierwsze uruchomienie `.command`)
Jeśli macOS zablokuje plik `LAB-Installer.command` komunikatem o nieznanym deweloperze:
- **Przez Finder (GUI):** kliknij plik **Control+klik** (prawy przycisk myszy) → **Otwórz** → potwierdź **Otwórz** (lub przejdź do *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*).
- **Przez Terminal (bez atrybutu `com.apple.quarantine`):**
  ```bash
  xattr -dr com.apple.quarantine . && ./LAB-Installer.command
  ```

### Uwaga: Widok GitHub ↔ Obsidian
Obsidian w nawigatorze plików ukrywa pliki i katalogi systemowe w katalogu głównym (m.in. `.obsidian/`, `.gitignore`, `.lab-payload/`, skrypty `.py` oraz launchery `LAB-Installer.*`), pokazując poza 8 Pokojami (`01–08`) przede wszystkim plik `AGENTS.md`. Pełną strukturę techniczną zobaczysz w Finderze, Eksploratorze plików lub bezpośrednio na GitHubie.

Konfiguracja wzorcowa znajduje się w `lab.config.example.toml` (sekcje `[vcs]` oraz `[ai_team.llm]` dla **Antigravity**, **Claude** i **OpenAI Codex CLI**). W trybie **OpenAI Codex CLI** instalator wymaga katalogu lokalnego (poza iCloud Drive).

---

## Struktura Repozytorium

```text
LAB/
├── core_config.py           # Silnik parametryzacji, walidacji TOML i stałych ról pokoi
├── lab-init.py              # Zunifikowany instalator i kreator WWW (http.server)
├── LAB-Installer.command    # Launcher instalacji macOS (sprawdza Python >= 3.11)
├── LAB-Installer.bat        # Launcher instalacji Windows (py -3)
├── LAB-Update.command       # Launcher aktualizacji macOS (1 kliknięcie)
├── LAB-Update.bat           # Launcher aktualizacji Windows (1 kliknięcie)
├── lab.config.example.toml  # Wzorzec konfiguracji ([vcs], [ai_team.llm] Antigravity, Claude, OpenAI Codex CLI)
├── pytest.py                # 100% stdlib runner testów (Zero-Pip)
├── AGENTS.md                # Główny kontrakt wejściowy Zespołu AI
├── LICENSE                  # Licencja MIT
├── README.md                # Dokumentacja startowa repozytorium
├── CONTRIBUTING.md          # Zasady współpracy i tryb Showcase / As-Is
├── docs/                    # Podręcznik AI, Use Cases (PDF) oraz Karta Startowa LAB
├── .obsidian/               # Lekka konfiguracja Obsidiana (app.json, appearance.json, hotkeys.json, snippets/)
├── 01 Skrzynka/             # Bufor wejściowy (drain-to-zero, .gitkeep)
├── 02 Warsztat/             # Stół roboczy dnia, Agenda Robocza i Dashboard LAB (.gitkeep)
├── 03 Projekty/             # Aktywne projekty o zamkniętym horyzoncie (.gitkeep)
├── 04 Operacje/             # Ciągłe operacje, rutyny i utrzymanie (.gitkeep)
├── 05 Baza Wiedzy/          # Synteza wiedzy (sieć połączonych notatek, mapy tematów, .gitkeep)
├── 06 Zasoby/               # Archiwum referencyjne i zasoby multimedialne (.gitkeep)
├── 07 Bazy Danych/          # Warstwa relacyjna SQLite WAL (inicjalizowana deterministycznie, .gitkeep)
└── 08 Zespół AI/            # Silnik Zespołu AI i Governance
    ├── Dzienniki Sesji/     # Puste logi sesji (.gitkeep)
    ├── Governance/          # Jednolity dokument ustrojowy (_Księga Governance LAB.md)
    ├── Role/                # Kontrakty agentów (Adam, Ada, Maks, Kinga, Maja, Klara, Edyta)
    ├── Skrypty/             # Narzędzia CLI, lab_core/, tests/
    │   └── integrations/raycast/  # Opcjonalne skrypty Raycast dla macOS
    └── Szablony/            # Szablony dokumentów i agendy roboczej
```

---

## Testy i Kontrola Jakości

```bash
python3 -m unittest discover -s "08 Zespół AI/Skrypty/tests" -p "test_*.py"
python3 "08 Zespół AI/Skrypty/check_quality.py" --vault-root .
```

---

## Higiena sesji i telemetria tokenów

1. **Pasek telemetrii pod każdą odpowiedzią:** Koordynator raportuje aktualne zużycie okna kontekstowego (`🧠 Xk / <Limit>k (Y%)`), abyś widział, ile pamięci roboczej pozostało w bieżącej rozmowie.
2. **Budżet higieniczny sesji (`[telemetry].session_token_budget`):** Uniwersalny próg domyślny to **150k tokenów** dla **Claude Code** i **OpenAI Codex CLI** (ostrzeżenie od **80%**, czyli 120k). Przy pracy w **Antigravity** z modelami wielko-oknowymi możesz ustawić w `lab.config.toml` wartość **250_000** (`250k`, ostrzeżenie od 200k), aby chronić jakość analiz w długich wątkach (*Lost in the Middle*).
3. **Bezpieczne zamknięcie i nowa sesja:** Uruchom **`/checkpoint`** (zapisuje stan Agendy i Dziennik Sesji), a następnie otwórz nową, świeżą rozmowę:
   - **Antigravity:** kliknij *New Chat* (`+`) w panelu konwersacji.
   - **Claude Code:** wpisz `/clear` (lub uruchom nową sesję `claude` w katalogu skarbca).
   - **OpenAI Codex CLI:** uruchom nową sesję `codex` w katalogu skarbca (lub użyj `/new`).

---

## Licencja i Zakres Odpowiedzialności (`QG-3`)

1. **Licencja MIT:** Całość kodu i szablonów publikowana jest na licencji MIT.
2. **Tryb Showcase / As-Is (No-SLA):** Projekt pełni rolę wzorca referencyjnego. Autor nie świadczy indywidualnego wsparcia technicznego, konsultacji wdrożeniowych ani nie gwarantuje terminów reakcji na zgłoszenia.
3. **Wkład zewnętrzny:** Szczegóły i ograniczenia zakresu opisuje plik `CONTRIBUTING.md`.
"""

CONTRIBUTING_CONTENT = """# Zasady Współpracy – LAB (`CONTRIBUTING`)

> **Klauzula Showcase / As-Is (No-SLA · Bramka QG-3):** Repozytorium `clazz2045/LAB` jest publikowane na licencji **MIT** jako projekt pokazowy i wzorzec referencyjny (*As-Is*). Autor nie gwarantuje obsługi zgłoszeń (*Issues*), przeglądów *Pull Requestów*, poprawek na żądanie ani wsparcia technicznego (*No-SLA*).

---

## 1. Ścisły Zakres (*Strict Scope*)

- **Język projektu:** Dokumentacja, komunikaty i komentarze są prowadzone w **100% w języku polskim**.
- **Standard technologiczny (Zero-Pip):** Silnik `08 Zespół AI/Skrypty/` oraz instalator `lab-init.py` muszą działać wyłącznie w oparciu o **bibliotekę standardową Pythona (`stdlib`)**. Zmiany wprowadzające zewnętrzne zależności (`pip`) będą odrzucane.
- **Architektura 8 Pokoi Rdzenia:** Struktura katalogów `01–08` oraz zasady z `AGENTS.md` i `_Księgi Governance LAB.md` są niezmienne dla rdzenia systemu.
- **Brak prywatnych danych:** Zgłoszenia i propozycje zmian nie mogą zawierać danych osobowych (PII), kluczy API, prywatnych adresów URL ani twardych ścieżek lokalnych (bramka `QG-2`).

---

## 2. Jak Zgłaszać Błędy i Propozycje Zmian

Ze względu na tryb *Showcase / As-Is* przed otwarciem zgłoszenia lub *Pull Requesta*:
1. **Sprawdź środowisko lokalne:** Upewnij się, że korzystasz z Pythona `>= 3.11` i aktualnej wersji repozytorium `LAB`.
2. **Uruchom deterministyczne bramki jakości:**
   ```bash
   python3 -m unittest discover -s "08 Zespół AI/Skrypty/tests" -p "test_*.py"
   python3 "08 Zespół AI/Skrypty/check_quality.py" --vault-root .
   ```
3. **Opisz zmianę zwięźle i technicznie (po polsku):** Podaj dokładny opis problemu, kroki reprodukcji oraz wynik powyższych testów.

---

## 3. Licencja

Wysyłając wkład do repozytorium `clazz2045/LAB`, akceptujesz, że Twoje zmiany zostaną udostępnione na warunkach licencji **MIT** (zobacz `LICENSE`), bez jakichkolwiek zobowiązań gwarancyjnych (*As-Is*).
"""

# Wzorce skanera QG-2 (PII, klucze API, twarde ścieżki domowe, prywatne URL)
QG2_RULES: List[Tuple[str, re.Pattern[str]]] = [
    ("API_KEY_OPENAI", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("API_KEY_GITHUB", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("API_KEY_GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("API_KEY_AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("API_KEY_GOOGLE", re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b")),
    ("API_KEY_SLACK", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    ("HARDCODED_HOME_MAC", re.compile(r"/Users/[A-Za-z0-9._-]+/")),
    ("HARDCODED_HOME_LINUX", re.compile(r"/home/[A-Za-z0-9._-]+/")),
    ("HARDCODED_HOME_WIN", re.compile(r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._-]+\\\\")),
    ("PRIVATE_URL_GDRIVE", re.compile(r"https?://drive\.google\.com/drive/folders/[A-Za-z0-9_\-]{18,}")),
    ("PRIVATE_URL_SLACK_HOOK", re.compile(r"https?://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")),
    ("RELIC_AI_TEAM", re.compile(r"06 AI Team")),  # QG2-IGNORE
    ("RELIC_SOP_001", re.compile(r"\bSOP-001\b(?!`?(?:\s+do\s+|[–-]))")),  # QG2-IGNORE
    ("RELIC_WY_003", re.compile(r"\bWY-003\b")),  # QG2-IGNORE
    ("RELIC_LEGACY_THEMES", re.compile(r"_legacy_themes")),  # QG2-IGNORE
]

PII_OWNER_PATTERN = re.compile(r"\bZbigniew(?:a|em|owi|e)?\b")  # QG2-IGNORE


def sanitize_text_for_export(text: str) -> str:
    """Deterministycznie anonimizuje imię właściciela w eksportowanych plikach .md / .sh."""
    replacements = (
        ("Zbigniewem", "Właścicielem Systemu"),  # QG2-IGNORE
        ("Zbigniewowi", "Właścicielowi Systemu"),  # QG2-IGNORE
        ("Zbigniewa", "Właściciela Systemu"),  # QG2-IGNORE
        ("Zbigniew", "Właściciel Systemu"),  # QG2-IGNORE
    )
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out

TEXT_EXTENSIONS = {
    ".py", ".md", ".html", ".toml", ".json", ".css", ".sh", ".bat", ".command", ".txt", ".yml", ".yaml",
}


@dataclass(frozen=True)
class QG2Finding:
    """Pojedyncze naruszenie wykryte przez bramkę QG-2."""
    rule: str
    file: str
    line: int
    snippet: str


def is_allowed_export_relpath(
    rel_path: Path,
    empty_room_paths: Optional[Set[str]] = None,
    ai_room_path: str = "08 Zespół AI",
) -> bool:
    """Weryfikuje ścieżkę względną względem ścisłej białej listy eksportu (Default-Deny)."""
    parts = rel_path.parts
    if not parts:
        return False

    # Wyklucz artefakty systemowe i bytecode na każdym poziomie
    if any(p in {".DS_Store", "__pycache__", ".pytest_cache"} or p.endswith(".pyc") for p in parts):
        return False

    top = parts[0]

    # 1. Pliki w korzeniu
    if len(parts) == 1:
        return top in ALLOWED_ROOT_FILES

    # 2. Lekka konfiguracja .obsidian/
    if top == ".obsidian":
        if len(parts) == 2:
            return parts[1] in ALLOWED_OBSIDIAN_FILES
        if parts[1] == "snippets":
            return len(parts) == 3 and rel_path.suffix.lower() == ".css"
        return False

    # 2b. Publiczna dokumentacja dystrybucyjna w docs/ (ścisła biała lista rozszerzeń)
    if top == "docs":
        return len(parts) == 2 and rel_path.suffix.lower() in ALLOWED_DOCS_EXTENSIONS

    # 2c. Hooki governance Antigravity (.agents/hooks.json)
    if top == ".agents":
        return len(parts) == 2 and parts[1] == "hooks.json"

    # 2d. Hooki governance Codex CLI (.codex/config.toml)
    if top == ".codex":
        return len(parts) == 2 and parts[1] == "config.toml"

    # 3. Puste pokoje 01–07 (wyłącznie .gitkeep)
    rooms = empty_room_paths if empty_room_paths is not None else DEFAULT_ROOM_PATHS
    if top in rooms:
        return len(parts) == 2 and parts[1] == ".gitkeep"

    # 4. Pokój 08 Zespół AI
    if top == ai_room_path and len(parts) >= 2:
        sub = parts[1]
        if sub not in ALLOWED_AI_SUBDIRS:
            return False
        if sub == "Dzienniki Sesji":
            return len(parts) == 3 and parts[2] == ".gitkeep"
        if sub in {"Governance", "Szablony"}:
            return len(parts) == 3 and rel_path.suffix.lower() == ".md"
        if sub == "Role":
            return len(parts) == 4 and parts[3] == "AGENT.md"
        if sub == "Skrypty":
            if len(parts) == 3:
                return rel_path.suffix.lower() == ".py"
            sk_sub = parts[2]
            if sk_sub not in ALLOWED_SKRYPTY_SUBDIRS:
                return False
            if sk_sub == "lab_core":
                if "print_themes" in parts:
                    return rel_path.suffix.lower() == ".css"
                return rel_path.suffix.lower() == ".py"
            if sk_sub == "tests":
                return rel_path.suffix.lower() == ".py"
            if sk_sub == "integrations":
                return (
                    len(parts) == 5
                    and parts[3] in ALLOWED_INTEGRATIONS_SUBDIRS
                    and rel_path.suffix.lower() in {".sh", ".md"}
                )
    return False


def scan_qg2(
    root_dir: Path,
    empty_room_paths: Optional[Set[str]] = None,
    ai_room_path: str = "08 Zespół AI",
) -> List[QG2Finding]:
    """Skanuje wyeksportowany katalog: biała lista plików + sekrety/ścieżki/URL (QG-2)."""
    findings: List[QG2Finding] = []
    if not root_dir.is_dir():
        return findings

    for current, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in files:
            fpath = Path(current) / fname
            rel_path = fpath.relative_to(root_dir)

            if not is_allowed_export_relpath(rel_path, empty_room_paths=empty_room_paths, ai_room_path=ai_room_path):
                findings.append(
                    QG2Finding(
                        rule="ALLOWLIST_VIOLATION",
                        file=str(rel_path),
                        line=0,
                        snippet="Plik spoza ścisłej białej listy eksportu",
                    )
                )
                continue

            if fpath.suffix.lower() not in TEXT_EXTENSIONS and fname not in {".gitignore"}:
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel_str = str(rel_path)
            is_test_file = "tests" in rel_path.parts
            for line_no, line in enumerate(content.splitlines(), start=1):
                if "QG2-IGNORE" in line:
                    continue
                for rule_name, pattern in QG2_RULES:
                    if pattern.search(line):
                        findings.append(
                            QG2Finding(
                                rule=rule_name,
                                file=rel_str,
                                line=line_no,
                                snippet=line.strip()[:120],
                            )
                        )
                if not is_test_file and PII_OWNER_PATTERN.search(line):
                    findings.append(
                        QG2Finding(
                            rule="PII_OWNER_NAME",
                            file=rel_str,
                            line=line_no,
                            snippet=line.strip()[:120],
                        )
                    )
    return findings


class GitSyncManager:
    """Menedżer synchronizacji i czystego eksportu LAB do repozytorium Git/GitHub."""

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        target_dir: Optional[Union[str, Path]] = None,
        remote_url: str = DEFAULT_REMOTE_URL,
    ) -> None:
        if vault_root:
            v_root = Path(vault_root).resolve()
            cfg_file = v_root / "lab.config.toml"
            if cfg_file.is_file():
                self.config_mgr = ConfigManager(config_path=cfg_file, vault_root=v_root)
            else:
                self.config_mgr = ConfigManager(vault_root=v_root)
        else:
            self.config_mgr = ConfigManager()

        self.vault_root = self.config_mgr.vault_root.resolve()
        self.target_dir = Path(target_dir).resolve() if target_dir else DEFAULT_LOCAL_REPO
        self.remote_url = remote_url

    def clean_target_dir(self, dry_run: bool = False) -> int:
        """Czyści katalog docelowy przed synchronizacją z zachowaniem .git/."""
        if not self.target_dir.is_dir():
            return 0
        removed = 0
        for item in self.target_dir.iterdir():
            if item.name == ".git":
                continue
            removed += 1
            if not dry_run:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        return removed

    def sync_files(self, dry_run: bool = False, clean_first: bool = True) -> List[Tuple[Path, Path]]:
        """Kopiuje zsanitowany rdzeń kodu, lekkie .obsidian/ i puste pokoje (.gitkeep)."""
        synced: List[Tuple[Path, Path]] = []

        if clean_first:
            self.clean_target_dir(dry_run=dry_run)

        if not dry_run:
            self.target_dir.mkdir(parents=True, exist_ok=True)

        # 1. Pliki korzenia (bez prywatnego lab.config.toml; .md podlegają sanitacji)
        for f_name in ROOT_FILES:
            src = self.vault_root / f_name
            dst = self.target_dir / f_name
            if src.is_file():
                synced.append((src, dst))
                if not dry_run:
                    if src.suffix.lower() in {".md", ".sh"}:
                        txt = sanitize_text_for_export(src.read_text(encoding="utf-8"))
                        dst.write_text(txt, encoding="utf-8")
                    else:
                        shutil.copy2(src, dst)

        # 2. Lekki eksport .obsidian/ przez białą listę
        obs_src = self.vault_root / ".obsidian"
        obs_dst = self.target_dir / ".obsidian"
        empty_rooms = {r.path for r in self.config_mgr.rooms.values() if r.code != "08"}
        ai_room = self.config_mgr.get_room_by_role("governance-and-engine")

        if obs_src.is_dir():
            for src_f in obs_src.rglob("*"):
                if not src_f.is_file():
                    continue
                rel = Path(".obsidian") / src_f.relative_to(obs_src)
                if not is_allowed_export_relpath(rel, empty_room_paths=empty_rooms, ai_room_path=ai_room.path):
                    continue
                dst_f = self.target_dir / rel
                synced.append((src_f, dst_f))
                if not dry_run:
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    if src_f.suffix.lower() in {".md", ".sh"}:
                        txt = sanitize_text_for_export(src_f.read_text(encoding="utf-8"))
                        dst_f.write_text(txt, encoding="utf-8")
                        if src_f.suffix.lower() == ".sh":
                            shutil.copymode(src_f, dst_f)
                    else:
                        shutil.copy2(src_f, dst_f)

        # 2b. Hooki governance Antigravity (.agents/hooks.json)
        hooks_src = self.vault_root / ".agents" / "hooks.json"
        if hooks_src.is_file():
            hooks_dst = self.target_dir / ".agents" / "hooks.json"
            synced.append((hooks_src, hooks_dst))
            if not dry_run:
                hooks_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(hooks_src, hooks_dst)

        # 2c. Hooki governance Codex CLI (.codex/config.toml - bez sekretów/kluczy API)
        codex_src = self.vault_root / ".codex" / "config.toml"
        if codex_src.is_file():
            codex_dst = self.target_dir / ".codex" / "config.toml"
            synced.append((codex_src, codex_dst))
            if not dry_run:
                codex_dst.parent.mkdir(parents=True, exist_ok=True)
                txt = sanitize_text_for_export(codex_src.read_text(encoding="utf-8"))
                codex_dst.write_text(txt, encoding="utf-8")

        # 3. Puste pokoje 01–07 z .gitkeep
        for room_path in sorted(empty_rooms):
            r_dir = self.target_dir / room_path
            keep = r_dir / ".gitkeep"
            synced.append((Path(f"{room_path}/.gitkeep"), keep))
            if not dry_run:
                r_dir.mkdir(parents=True, exist_ok=True)
                keep.write_text("", encoding="utf-8")

        # 4. Pokój 08 Zespół AI (wyłącznie pliki spełniające białą listę) + pusty Dzienniki Sesji/.gitkeep
        ai_src_base = self.vault_root / ai_room.path
        ai_dst_base = self.target_dir / ai_room.path

        sessions_keep = ai_dst_base / "Dzienniki Sesji" / ".gitkeep"
        synced.append((Path(f"{ai_room.path}/Dzienniki Sesji/.gitkeep"), sessions_keep))
        if not dry_run:
            sessions_keep.parent.mkdir(parents=True, exist_ok=True)
            sessions_keep.write_text("", encoding="utf-8")

        if ai_src_base.is_dir():
            for src_f in ai_src_base.rglob("*"):
                if not src_f.is_file():
                    continue
                rel = Path(ai_room.path) / src_f.relative_to(ai_src_base)
                if rel.parts[1] == "Dzienniki Sesji":
                    continue
                if not is_allowed_export_relpath(rel, empty_room_paths=empty_rooms, ai_room_path=ai_room.path):
                    continue
                dst_f = self.target_dir / rel
                synced.append((src_f, dst_f))
                if not dry_run:
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    if src_f.suffix.lower() in {".md", ".sh"}:
                        txt = sanitize_text_for_export(src_f.read_text(encoding="utf-8"))
                        dst_f.write_text(txt, encoding="utf-8")
                        if src_f.suffix.lower() == ".sh":
                            shutil.copymode(src_f, dst_f)
                    else:
                        shutil.copy2(src_f, dst_f)

        # 5. .gitignore, README.md, LICENSE i CONTRIBUTING.md
        gitignore_path = self.target_dir / ".gitignore"
        readme_path = self.target_dir / "README.md"
        license_path = self.target_dir / "LICENSE"
        contributing_path = self.target_dir / "CONTRIBUTING.md"
        if not dry_run:
            gitignore_path.write_text(GITIGNORE_CONTENT, encoding="utf-8")
            readme_path.write_text(README_CONTENT, encoding="utf-8")
            license_path.write_text(LICENSE_CONTENT, encoding="utf-8")
            contributing_path.write_text(CONTRIBUTING_CONTENT, encoding="utf-8")
        synced.append((Path(".gitignore"), gitignore_path))
        synced.append((Path("README.md"), readme_path))
        synced.append((Path("LICENSE"), license_path))
        synced.append((Path("CONTRIBUTING.md"), contributing_path))

        # 6. Publiczna dokumentacja dystrybucyjna w docs/ (z konfiguracji [vcs].public_docs)
        vcs_cfg = self.config_mgr._raw_data.get("vcs", {})
        public_docs = vcs_cfg.get("public_docs", []) if isinstance(vcs_cfg, dict) else []
        for raw_rel in public_docs:
            if not isinstance(raw_rel, str) or not raw_rel.strip():
                continue
            src_f = (self.vault_root / raw_rel.strip()).resolve()
            try:
                src_f.relative_to(self.vault_root)
            except ValueError:
                continue
            if not src_f.is_file() or src_f.suffix.lower() not in ALLOWED_DOCS_EXTENSIONS:
                continue
            dst_f = self.target_dir / "docs" / src_f.name
            synced.append((src_f, dst_f))
            if not dry_run:
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                if src_f.suffix.lower() in {".md", ".html"}:
                    txt = sanitize_text_for_export(src_f.read_text(encoding="utf-8"))
                    dst_f.write_text(txt, encoding="utf-8")
                else:
                    shutil.copy2(src_f, dst_f)

        return synced

    def run_qg2_scan(self) -> List[QG2Finding]:
        """Uruchamia skaner QG-2 na katalogu docelowym."""
        empty_rooms = {r.path for r in self.config_mgr.rooms.values() if r.code != "08"}
        ai_room = self.config_mgr.get_room_by_role("governance-and-engine")
        return scan_qg2(self.target_dir, empty_room_paths=empty_rooms, ai_room_path=ai_room.path)

    def build_release_zip(self, output_zip: Optional[Union[str, Path]] = None) -> Path:
        """Buduje paczkę LAB-Installer.zip: launchery na wierzchu, cały wsad w .lab-payload/."""
        out_path = Path(output_zip).resolve() if output_zip else self.target_dir.parent / "LAB-Installer.zip"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        top_launchers = {
            "LAB-Installer.command",
            "LAB-Installer.bat",
            "LAB-Update.command",
            "LAB-Update.bat",
        }
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for current, dirs, files in os.walk(self.target_dir):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache"}]
                for fname in files:
                    fpath = Path(current) / fname
                    rel = fpath.relative_to(self.target_dir)
                    if len(rel.parts) == 1 and rel.name in top_launchers:
                        arcname = rel.name
                    else:
                        arcname = f".lab-payload/{rel.as_posix()}"
                    zf.write(fpath, arcname=arcname)
        return out_path

    def init_and_push(
        self,
        commit_message: Optional[str] = None,
        dry_run: bool = False,
        orphan: bool = False,
    ) -> Tuple[bool, str]:
        """Weryfikuje bramkę QG-2, inicjalizuje Git (opcjonalnie orphan commit) i wypycha zmiany."""
        if dry_run:
            return True, "[DRY-RUN] Pominięto komendy git commit i git push."

        findings = self.run_qg2_scan()
        if findings:
            sample = "; ".join(f"{f.rule}@{f.file}:{f.line}" for f in findings[:5])
            return False, f"Bramka QG-2 zablokowała push ({len(findings)} naruszeń): {sample}"

        cwd = str(self.target_dir)
        git_dir = self.target_dir / ".git"
        if not git_dir.is_dir():
            res = subprocess.run(["git", "init", "-b", "main"], cwd=cwd, capture_output=True, text=True)
            if res.returncode != 0:
                return False, f"Błąd 'git init': {res.stderr}"

        remotes = subprocess.run(["git", "remote"], cwd=cwd, capture_output=True, text=True)
        if "origin" not in remotes.stdout:
            res = subprocess.run(["git", "remote", "add", "origin", self.remote_url], cwd=cwd, capture_output=True, text=True)
            if res.returncode != 0:
                return False, f"Błąd 'git remote add': {res.stderr}"
        else:
            subprocess.run(["git", "remote", "set-url", "origin", self.remote_url], cwd=cwd, capture_output=True, text=True)

        name_check = subprocess.run(["git", "config", "user.name"], cwd=cwd, capture_output=True, text=True)
        if not name_check.stdout.strip():
            subprocess.run(["git", "config", "user.name", "LAB Release Bot"], cwd=cwd, capture_output=True, text=True)
        email_check = subprocess.run(["git", "config", "user.email"], cwd=cwd, capture_output=True, text=True)
        if not email_check.stdout.strip():
            subprocess.run(["git", "config", "user.email", "clazz2045@users.noreply.github.com"], cwd=cwd, capture_output=True, text=True)

        if not commit_message:
            today_str = datetime.date.today().isoformat()
            commit_message = f"LAB Core export ({today_str})"

        if orphan:
            subprocess.run(["git", "checkout", "--orphan", "_lab_orphan_tmp"], cwd=cwd, capture_output=True, text=True)
            subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
            commit_res = subprocess.run(["git", "commit", "-m", commit_message], cwd=cwd, capture_output=True, text=True)
            if commit_res.returncode != 0:
                return False, f"Błąd 'git commit' (orphan): {commit_res.stderr}"
            subprocess.run(["git", "branch", "-M", "main"], cwd=cwd, capture_output=True, text=True)
            push_res = subprocess.run(["git", "push", "--force", "-u", "origin", "main"], cwd=cwd, capture_output=True, text=True)
            if push_res.returncode != 0:
                return False, f"Błąd 'git push --force': {push_res.stderr}"
            return True, f"Pomyślnie wypchnięto orphan commit do {self.remote_url} ({commit_message})"

        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
        if not status_res.stdout.strip():
            push_res = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=cwd, capture_output=True, text=True)
            return True, f"Brak nowych zmian. Git push: {push_res.stdout.strip() or 'Wszystko aktualne.'}"

        commit_res = subprocess.run(["git", "commit", "-m", commit_message], cwd=cwd, capture_output=True, text=True)
        if commit_res.returncode != 0:
            return False, f"Błąd 'git commit': {commit_res.stderr}"

        push_res = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=cwd, capture_output=True, text=True)
        if push_res.returncode != 0:
            return False, f"Błąd 'git push': {push_res.stderr}"

        return True, f"Pomyślnie wypchnięto do {self.remote_url} (commit: {commit_message})"

    def get_status(self) -> Dict[str, Any]:
        """Zwraca status lokalnego repozytorium."""
        exists = self.target_dir.is_dir()
        git_initialized = (self.target_dir / ".git").is_dir() if exists else False
        git_status_output = ""
        if git_initialized:
            res = subprocess.run(["git", "status", "-s"], cwd=str(self.target_dir), capture_output=True, text=True)
            git_status_output = res.stdout.strip()

        return {
            "target_dir": str(self.target_dir),
            "exists": exists,
            "git_initialized": git_initialized,
            "remote_url": self.remote_url,
            "uncommitted_changes": git_status_output,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronizator i eksporter LAB do zewnętrznego repozytorium Git/GitHub."
    )
    parser.add_argument("--push", action="store_true", help="Synchronizuj, zweryfikuj QG-2, zatwierdź i wypchnij.")
    parser.add_argument("--orphan", action="store_true", help="Użyj trybu orphan commit (czysty start historii).")
    parser.add_argument("--sync-only", action="store_true", help="Tylko zsynchronizuj pliki (bez commit i push).")
    parser.add_argument("--scan-only", action="store_true", help="Tylko uruchom skaner QG-2 na katalogu docelowym.")
    parser.add_argument("--build-zip", type=str, nargs="?", const="", default=None, help="Zbuduj paczkę .zip dla GitHub Releases.")
    parser.add_argument("--status", action="store_true", help="Wyświetl status repozytorium.")
    parser.add_argument("--message", "-m", type=str, default=None, help="Treść komunikatu commita.")
    parser.add_argument("--target-dir", type=str, default=None, help="Ścieżka do docelowego katalogu repozytorium.")
    parser.add_argument("--remote-url", type=str, default=DEFAULT_REMOTE_URL, help="URL repozytorium GitHub.")
    parser.add_argument("--dry-run", action="store_true", help="Tryb symulacji (bez fizycznych zmian).")
    parser.add_argument("--vault-root", type=str, default=None, help="Ścieżka do korzenia skarbca LAB.")

    args = parser.parse_args()
    mgr = GitSyncManager(args.vault_root, args.target_dir, args.remote_url)

    if args.status:
        st = mgr.get_status()
        print(json_status := f"target={st['target_dir']} exists={st['exists']} git={st['git_initialized']}")
        return 0

    if args.scan_only:
        findings = mgr.run_qg2_scan()
        print(f"QG-2 scan: {len(findings)} naruszeń.")
        for f in findings:
            print(f"  [{f.rule}] {f.file}:{f.line} -> {f.snippet}")
        return 1 if findings else 0

    synced = mgr.sync_files(dry_run=args.dry_run)
    print(f"Zsynchronizowano {len(synced)} pozycji do {mgr.target_dir}")

    if args.build_zip is not None and not args.dry_run:
        zip_out = mgr.build_release_zip(args.build_zip or None)
        print(f"Zbudowano paczkę Release ZIP: {zip_out}")

    if args.push:
        ok, msg = mgr.init_and_push(commit_message=args.message, dry_run=args.dry_run, orphan=args.orphan)
        print(msg)
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
