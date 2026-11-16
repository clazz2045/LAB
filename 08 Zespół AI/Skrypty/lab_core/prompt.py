#!/usr/bin/env python3
"""session_prompt.py - Generator promptu otwarcia nowej sesji LAB dla Raycast / CLI

Standard inżynieryjny:
- 100% Python Standard Library (datetime, pathlib, argparse, json, subprocess, sys).
- Zero zewnętrznych zależności pip.
- Dynamiczne pobieranie ścieżki do pokoju 08 Zespół AI i Dzienników Sesji przez ConfigManager.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Zaimportuj core_config / lab_core z katalogu skarbca
_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigManager, ConfigError, find_vault_root


class SessionPromptGenerator:
    """Odpowiada za deterministyczne zliczanie sesji z Dzienników Sesji i budowę promptu startowego."""

    @staticmethod
    def detect_active_vault(default_vault: Path) -> Path:
        """Wykrywa aktywny skarbiec LAB na podstawie tytułu aktywnego okna macOS (AntiGravity / Obsidian)."""
        if sys.platform != "darwin":
            return default_vault

        try:
            cmd = [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '  set frontApp to name of first application process whose frontmost is true\n'
                "  tell process frontApp\n"
                "    try\n"
                "      set winTitle to name of front window\n"
                "    on error\n"
                '      set winTitle to ""\n'
                "    end try\n"
                "  end tell\n"
                '  return frontApp & "|||" & winTitle\n'
                "end tell",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0 and "|||" in res.stdout:
                _, win_title = res.stdout.strip().split("|||", 1)
                win_title = win_title.strip()
                if win_title:
                    parent_dir = default_vault.parent
                    if parent_dir.is_dir():
                        candidates = [
                            c for c in parent_dir.iterdir()
                            if c.is_dir() and not c.name.startswith(".") and (c / "lab.config.toml").is_file()
                        ]
                        # Sortuj po długości nazwy malejąco (np. LAB-GUS-F01 przed LAB)
                        candidates.sort(key=lambda c: len(c.name), reverse=True)
                        for candidate in candidates:
                            if candidate.name in win_title:
                                return candidate.resolve()
        except Exception:
            pass

        return default_vault

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> None:
        default_root = find_vault_root(_script_dir)
        if vault_root:
            self.vault_root = Path(vault_root).resolve()
        else:
            self.vault_root = self.detect_active_vault(default_root)
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)

        try:
            ai_team_room = self.config.get_room_by_role("governance-and-engine")
        except ConfigError:
            ai_team_room = self.config.get_room("08")

        self.ai_team_dir = self.vault_root / ai_team_room.path
        self.session_logs_dir = self.ai_team_dir / "Dzienniki Sesji"

    def get_session_count_for_date(self, target_date: datetime.date) -> int:
        """Zlicza liczbę zakończonych checkpointów/sesji dla wskazanej daty w Dziennikach Sesji."""
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")
        day_prefix = target_date.strftime("%Y-%m-%d")

        target_month_dir = self.session_logs_dir / year_str / month_str
        if not target_month_dir.is_dir():
            return 0

        count = 0
        for entry in target_month_dir.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.name.startswith(day_prefix) and entry.suffix == ".md":
                # Ignoruj certyfikaty audytorskie lub inne notatki niebędące logami sesji
                # Standardowy format sesji to np.: YYYY-MM-DD-HH-MM_adam_*.md (lub historycznie *_larry_*.md)
                # lub po prostu plik z datą dzienną.
                count += 1
        return count

    def get_next_session_number(self, target_date: Optional[datetime.date] = None) -> int:
        """Zwraca kolejny numer sesji dla danego dnia (liczba dotychczasowych + 1)."""
        dt = target_date or datetime.date.today()
        completed = self.get_session_count_for_date(dt)
        return completed + 1

    def generate_prompt(
        self,
        target_date: Optional[datetime.date] = None,
        session_number: Optional[int] = None,
    ) -> str:
        """Generuje kanoniczny ciąg otwarcia sesji: 'YYYY-MM-DD, sesja X, Startujemy!'."""
        dt = target_date or datetime.date.today()
        nr = session_number if session_number is not None else self.get_next_session_number(dt)
        date_formatted = dt.strftime("%Y-%m-%d")
        return f"{date_formatted}, sesja {nr}, Startujemy!"

    def copy_to_clipboard(self, text: str) -> bool:
        """Kopiuje tekst do schowka systemowego macOS (pbcopy)."""
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"))
            return proc.returncode == 0
        except Exception:
            return False

    def paste_to_active_app(self, text: str) -> bool:
        """Kopiuje do schowka i wysyła skrót Cmd+V przez osascript."""
        if not self.copy_to_clipboard(text):
            return False
        try:
            script = 'tell application "System Events" to keystroke "v" using command down'
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return res.returncode == 0
        except Exception:
            return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generator promptu startowego nowej sesji LAB (Raycast & CLI)."
    )
    parser.add_argument(
        "--vault-root",
        type=str,
        default=None,
        help="Ścieżka do korzenia skarbca LAB.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Niestandardowa data w formacie YYYY-MM-DD (domyślnie dzisiejsza).",
    )
    parser.add_argument(
        "--session-number",
        type=int,
        default=None,
        help="Wymuszenie konkretnego numeru sesji (pomija automatyczne zliczanie).",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Kopiuje wygenerowany prompt do schowka macOS.",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Kopiuje do schowka i wkleja bezpośrednio do aktywnego okna (Cmd+V).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Zwraca wynik w formacie JSON.",
    )
    return parser


def main(args: Optional[List[str]] = None) -> int:
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    target_date: Optional[datetime.date] = None
    if parsed_args.date:
        try:
            target_date = datetime.datetime.strptime(parsed_args.date, "%Y-%m-%d").date()
        except ValueError:
            sys.stderr.write(f"Błąd: nieprawidłowy format daty '{parsed_args.date}'. Oczekiwano YYYY-MM-DD.\n")
            return 1

    generator = SessionPromptGenerator(vault_root=parsed_args.vault_root)
    dt = target_date or datetime.date.today()
    nr = parsed_args.session_number or generator.get_next_session_number(dt)
    prompt = generator.generate_prompt(target_date=dt, session_number=nr)

    # Ciche odświeżenie Warsztatu (rolowanie agendy i regeneracja dashboardu)
    try:
        from rollover_agenda import AgendaRollover
        AgendaRollover(vault_root=generator.vault_root).run_rollover(target_date=dt)
    except Exception:
        pass

    try:
        from build_lab_dashboard import DashboardBuilder
        DashboardBuilder(vault_root=generator.vault_root).write_dashboard()
    except Exception:
        pass

    if parsed_args.paste:
        success = generator.paste_to_active_app(prompt)
        if not success:
            # Fallback na czyste wypisanie
            print(prompt)
        return 0

    if parsed_args.copy:
        generator.copy_to_clipboard(prompt)

    if parsed_args.json_mode:
        data = {
            "date": dt.strftime("%Y-%m-%d"),
            "session_number": nr,
            "prompt": prompt,
            "copied": parsed_args.copy,
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(prompt)

    return 0


if __name__ == "__main__":
    sys.exit(main())
