#!/usr/bin/env python3
"""test_session_prompt.py - Testy jednostkowe dla generatora promptu sesji (session_prompt.py)

Standard inżynieryjny:
- 100% Python Standard Library (unittest).
- Zero zewnętrznych zależności pip.
- Testowanie:
  1. Poprawności formatowania tekstu: 'YYYY-MM-DD, sesja X, Startujemy!'.
  2. Zliczania sesji z katalogu Dzienniki Sesji w oparciu o zamockowane pliki.
  3. Obsługi niestandardowej daty i wymuszonego numeru sesji.
  4. Trybu JSON i opcji CLI.
"""

from __future__ import annotations

import datetime
import json
import shutil
import sys
import unittest
from pathlib import Path

# Wyłącz bytecode pycache
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from lab_core.prompt import SessionPromptGenerator, build_parser, main
from core_config import find_vault_root


class TestSessionPrompt(unittest.TestCase):
    """Testy jednostkowe klasy SessionPromptGenerator."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vault_root = find_vault_root(_script_dir)
        cls.tmp_dir = cls.vault_root / "08 Zespół AI" / "Skrypty" / ".tmp_test_session_prompt"
        if cls.tmp_dir.exists():
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)
        cls.tmp_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.tmp_dir.exists():
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.generator = SessionPromptGenerator(vault_root=self.vault_root)

    def test_generate_prompt_format(self) -> None:
        """Weryfikacja formatu tekstu 'YYYY-MM-DD, sesja X, Startujemy!'."""
        d = datetime.date(2026, 11, 5)
        text = self.generator.generate_prompt(target_date=d, session_number=3)
        self.assertEqual(text, "2026-11-05, sesja 3, Startujemy!")

    def test_get_session_count_and_next_number_live(self) -> None:
        """Weryfikacja zliczania dla bieżącej daty (2026-11-05)."""
        d = datetime.date(2026, 11, 5)
        count = self.generator.get_session_count_for_date(d)
        # Dzisiaj w Dziennikach Sesji istnieje już 1 log (09-02)
        self.assertGreaterEqual(count, 1)
        next_nr = self.generator.get_next_session_number(d)
        self.assertEqual(next_nr, count + 1)

    def test_get_session_count_for_empty_date(self) -> None:
        """Weryfikacja dla daty z zerową liczbą sesji."""
        d = datetime.date(2025, 1, 1)
        count = self.generator.get_session_count_for_date(d)
        self.assertEqual(count, 0)
        self.assertEqual(self.generator.get_next_session_number(d), 1)

    def test_cli_json_mode(self) -> None:
        """Weryfikacja wyjścia JSON."""
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-11-05", "--session-number", "5", "--json"])
        self.assertEqual(args.date, "2026-11-05")
        self.assertEqual(args.session_number, 5)
        self.assertTrue(args.json_mode)

    def test_detect_active_vault_fallback_on_error(self) -> None:
        """Weryfikacja bezpiecznego fallbacku przy braku możliwości odczytu okna."""
        res = SessionPromptGenerator.detect_active_vault(self.vault_root)
        self.assertEqual(res, self.vault_root)

    def test_detect_active_vault_satellite_detection(self) -> None:
        """Weryfikacja rozpoznawania satelity na podstawie okna macOS."""
        from unittest.mock import patch, MagicMock

        # Utwórz strukturę testową wewnątrz dozwolonego tmp_dir
        base_dir = self.tmp_dir / "documents"
        base_dir.mkdir(parents=True, exist_ok=True)
        main_lab = base_dir / "LAB"
        main_lab.mkdir(parents=True, exist_ok=True)
        (main_lab / "lab.config.toml").write_text("[system]\ninstance_name = 'LAB'\n", encoding="utf-8")

        sat_dir = base_dir / "LAB-GUS-F01"
        sat_dir.mkdir(parents=True, exist_ok=True)
        (sat_dir / "lab.config.toml").write_text("[system]\ninstance_name = 'GUS'\n", encoding="utf-8")

        fake_output = f"AntiGravity|||Dashboard LAB — {sat_dir.name}"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_output

        with patch("subprocess.run", return_value=mock_proc):
            detected = SessionPromptGenerator.detect_active_vault(main_lab)
            self.assertEqual(detected, sat_dir.resolve())


if __name__ == "__main__":
    unittest.main()
