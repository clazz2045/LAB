#!/usr/bin/env python3
"""test_session_bootstrap.py - Testy jednostkowe procedury session_bootstrap.py (LAB 3.0)

Standard:
- 100% Python Standard Library (unittest, tempfile, json, pathlib, subprocess, sys).
- Testowanie stanów: READY, ATTENTION, BROKEN (Circuit Breaker).
- Testowanie wyjścia JSON oraz CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Blokada tworzenia bytecode __pycache__
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from session_bootstrap import SessionBootstrap, format_text_output


class TestSessionBootstrap(unittest.TestCase):
    """Zestaw testów jednostkowych mechanizmu bootstrapu sesji."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault_root = Path(self.tmp.name).resolve()

        # Utwórz strukturę 8 pokoi
        self.rooms = [
            "01 Skrzynka",
            "02 Warsztat",
            "03 Projekty",
            "04 Operacje",
            "05 Baza Wiedzy",
            "06 Zasoby",
            "07 Bazy Danych",
            "08 Zespół AI",
        ]
        for r in self.rooms:
            (self.vault_root / r).mkdir(parents=True, exist_ok=True)

        # Skopiuj pełny, kanoniczny lab.config.toml do skarbca testowego
        real_config_path = Path(_script_dir).parent.parent / "lab.config.toml"
        real_config = real_config_path.read_text(encoding="utf-8")
        (self.vault_root / "lab.config.toml").write_text(real_config, encoding="utf-8")

        # Pliki zarządcze
        (self.vault_root / "02 Warsztat" / "Dashboard LAB.md").write_text("---\ntype: cockpit-dashboard\n---\n# Dashboard\n", encoding="utf-8")
        (self.vault_root / "03 Projekty" / "_Spis Projektów.md").write_text("# Spis Projektów\n", encoding="utf-8")
        (self.vault_root / "04 Operacje" / "_Spis Operacji.md").write_text("# Spis Operacji\n", encoding="utf-8")

        real_agents = (Path(_script_dir).parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        (self.vault_root / "AGENTS.md").write_text(real_agents, encoding="utf-8")

        real_ai_room = Path(_script_dir).parent
        real_roles = real_ai_room / "Role"
        if real_roles.is_dir():
            shutil.copytree(real_roles, self.vault_root / "08 Zespół AI" / "Role")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bootstrap_ready_state(self) -> None:
        """Stan READY: czysta skrzynka, obecne pliki zarządcze i brak błędów jakościowych."""
        bootstrap = SessionBootstrap(vault_root=self.vault_root)
        res = bootstrap.run()

        self.assertEqual(res["inbox_count"], 0)
        self.assertEqual(res["inbox_items"], [])
        self.assertIn("Agenda Robocza.md", res["agenda_file"])
        self.assertIn("telemetry_statusline", res)
        self.assertRegex(res["telemetry_statusline"], r"🧠\s*\d+(?:\.\d+)?k?\s*/\s*250k\s*\(\d+(?:\.\d+)?%\)")
        self.assertIn(res["status"], {"READY", "ATTENTION"})
        if res["quality_status"] == "OK":
            self.assertEqual(res["status"], "READY")
        self.assertIsNone(res["ticket"])

    def test_bootstrap_attention_on_inbox_items(self) -> None:
        """Stan ATTENTION: elementy w skrzynce wymagające drenażu (drain-to-zero)."""
        (self.vault_root / "01 Skrzynka" / "Notatka1.md").write_text("Test", encoding="utf-8")
        (self.vault_root / "01 Skrzynka" / "Artykul.pdf").write_text("PDF", encoding="utf-8")

        bootstrap = SessionBootstrap(vault_root=self.vault_root)
        res = bootstrap.run()

        self.assertEqual(res["status"], "ATTENTION")
        self.assertEqual(res["inbox_count"], 2)
        self.assertIn("Notatka1.md", res["inbox_items"])
        self.assertIn("Artykul.pdf", res["inbox_items"])
        self.assertIsNone(res["ticket"])

    def test_bootstrap_broken_circuit_breaker(self) -> None:
        """Stan BROKEN: brak kanonicznego pliku zarządczego wyzwala bilet dla Maksa (Circuit Breaker)."""
        # Usuń wymagany plik zarządczy
        (self.vault_root / "04 Operacje" / "_Spis Operacji.md").unlink()

        bootstrap = SessionBootstrap(vault_root=self.vault_root)
        res = bootstrap.run()

        self.assertEqual(res["status"], "BROKEN")
        self.assertEqual(res["quality_status"], "BROKEN")
        self.assertIsNotNone(res["ticket"])
        t = res["ticket"]
        self.assertIn("component", t)
        self.assertIn("error", t)
        self.assertIn("recommended_action", t)
        self.assertIn("Maks", t["recommended_action"])

    def test_bootstrap_json_output_cli(self) -> None:
        """Weryfikacja flagi --json zwracającej prawidłowy format JSON."""
        script_path = _script_dir / "session_bootstrap.py"
        proc = subprocess.run(
            [sys.executable, str(script_path), "--vault-root", str(self.vault_root), "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("status", data)
        self.assertIn("quality_status", data)
        self.assertIn("agenda_file", data)
        self.assertIn("inbox_count", data)
        self.assertIn("inbox_items", data)
        self.assertIn("projects_summary", data)
        self.assertIn("telemetry_statusline", data)
        self.assertIn("ticket", data)

    def test_bootstrap_broken_exit_code_cli(self) -> None:
        """Stan BROKEN zwraca kod wyjścia 1 z CLI."""
        (self.vault_root / "03 Projekty" / "_Spis Projektów.md").unlink()
        script_path = _script_dir / "session_bootstrap.py"
        proc = subprocess.run(
            [sys.executable, str(script_path), "--vault-root", str(self.vault_root), "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "BROKEN")
        self.assertIsNotNone(data["ticket"])


if __name__ == "__main__":
    unittest.main()
