#!/usr/bin/env python3
"""
scratchpad.py - Deterministyczna obsługa Brudnopisu Dnia w LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (pathlib, datetime, subprocess, urllib.parse, argparse, sys)
- Zero hardkodowanych ścieżek: wszystkie ścieżki i nazwy pokoi pobierane dynamicznie
  przez core_config.ConfigManager z pliku lab.config.toml lub zmiennej VAULT_ROOT.
- Działa zarówno z poziomu terminala, jak i skrótów Raycast (Shift+D).
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse

# 1. Dynamiczne odnalezienie korzenia skarbca i załadowanie modułu core_config
_script_dir = Path(__file__).resolve().parent
_vault_root: Path | None = None

# Sprawdź zmienną środowiskową VAULT_ROOT lub LAB_ROOT
env_root = os.environ.get("LAB_ROOT") or os.environ.get("VAULT_ROOT")
if env_root and Path(env_root).is_dir():
    _vault_root = Path(env_root).resolve()
else:
    for candidate in [_script_dir] + list(_script_dir.parents):
        if (candidate / "lab.config.toml").is_file():
            _vault_root = candidate
            break

if not _vault_root:
    # Ostateczny fallback w oparciu o strukturę standardową (dwa poziomy wyżej)
    from lab_core.env import find_vault_root
    _vault_root = find_vault_root(_script_dir)

if str(_vault_root) not in sys.path:
    sys.path.insert(0, str(_vault_root))

try:
    from core_config import ConfigManager
    config = ConfigManager.load(_vault_root)
except Exception:
    from core_config import ConfigManager
    config = ConfigManager.load()


def get_daily_scratchpad_path() -> tuple[Path, str]:
    """Zwraca bezwzględną ścieżkę do dzisiejszego wpisu wejściowego w Skrzynce oraz nazwę skarbca."""
    # Pobierz ścieżkę do pokoju 01 (ingestion) dynamicznie z konfiguracji
    inbox_dir = config.get_room_path("r01_inbox", absolute=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    
    target_file = inbox_dir / f"{day}.md"
    
    if not target_file.is_file():
        template = f"""---
type: raw-capture
date: {day}
source: quick-capture
---

# Wsad Dnia: {day}

"""
        target_file.write_text(template, encoding="utf-8")
        
    vault_name = config.vault_root.name
    return target_file, vault_name


def open_scratchpad() -> None:
    """Otwiera dzisiejszy brudnopis bezpośrednio w aplikacji Obsidian przez URI scheme."""
    target_file, vault_name = get_daily_scratchpad_path()
    
    # Wylicz względną ścieżkę względem skarbca
    rel_path = target_file.relative_to(config.vault_root)
    # Usuń rozszerzenie .md dla Obsidian URI
    file_arg = str(rel_path)[:-3] if str(rel_path).endswith(".md") else str(rel_path)
    
    encoded_vault = urllib.parse.quote(vault_name)
    encoded_file = urllib.parse.quote(file_arg)
    
    uri = f"obsidian://open?vault={encoded_vault}&file={encoded_file}"
    subprocess.run(["open", uri], check=False)


def capture_thought(text: str) -> None:
    """Dopisuje pojedynczą myśl ze znacznikiem czasu do dzisiejszego brudnopisu."""
    target_file, _ = get_daily_scratchpad_path()
    time_str = datetime.now().strftime("%H:%M")
    
    with open(target_file, "a", encoding="utf-8") as f:
        f.write(f"- [{time_str}] {text}\n")
        
    print(f"Zapisano w brudnopisie: [{time_str}] {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Zarządca Brudnopisu Dnia w LAB 3.0")
    parser.add_argument("--open", action="store_true", help="Utwórz i otwórz brudnopis dnia w Obsidianie")
    parser.add_argument("--capture", type=str, help="Dopisz szybką myśl ze stemplem czasu")
    
    args = parser.parse_args()
    
    if args.capture:
        capture_thought(args.capture)
    elif args.open or len(sys.argv) == 1:
        open_scratchpad()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
