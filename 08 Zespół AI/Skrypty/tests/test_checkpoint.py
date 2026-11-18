"""Testy jednostkowe walidacji punktu startowego (handoff) i procedury checkpoint.py.

Weryfikuje:
  1. Odrzucanie pustego, zbyt krótkiego (< 25 znaków) lub generycznego handoffu (Fail-Closed).
  2. Akceptację konkretnego handoffu i obecność sekcji ## 4. Punkt Startowy Kolejnej Sesji (Handoff).
  3. Odczyt i walidację handoffu z istniejącego pliku dziennika sesji.
  4. Obsługę flagi CLI --handoff.

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checkpoint import (
    HandoffValidationError,
    SessionCheckpoint,
    extract_handoff_from_log_content,
    main as checkpoint_main,
    validate_handoff,
)
from core_config import ConfigManager, find_vault_root


class TestHandoffValidation(unittest.TestCase):
    """Testy jednostkowe funkcji validate_handoff."""

    def test_rejects_none_and_empty(self) -> None:
        for val in (None, "", "   ", "\n\t"):
            valid, err = validate_handoff(val)
            self.assertFalse(valid)
            self.assertIn("BŁĄD CHECKPOINTU: Wymagane podanie konkretnego punktu startowego", err or "")

    def test_rejects_too_short(self) -> None:
        short_inputs = [
            "Krótki tekst",
            "123456789012345678901234",  # 24 znaki
            "- [ ] Dalsze prace",
        ]
        for val in short_inputs:
            valid, err = validate_handoff(val)
            self.assertFalse(valid, f"Powinien odrzucić zbyt krótki handoff: '{val}'")
            self.assertIn("BŁĄD CHECKPOINTU", err or "")

    def test_rejects_generic_placeholders(self) -> None:
        generic_inputs = [
            "Przegląd Agendy Roboczej Dnia na otwarcie kolejnej sesji.",
            "- [ ] Przegląd Agendy Roboczej Dnia na otwarcie kolejnej sesji.",
            "przegląd agendy roboczej dnia na otwarcie kolejnej sesji",
            "Kontynuacja prac nad projektem.",
            "Dalsze prace zgodnie z planem.",
            "Start kolejnej sesji zgodnie z agendą.",
        ]
        for val in generic_inputs:
            valid, err = validate_handoff(val)
            self.assertFalse(valid, f"Powinien odrzucić generyczny handoff: '{val}'")
            self.assertIn("BŁĄD CHECKPOINTU", err or "")

    def test_accepts_concrete_handoff(self) -> None:
        valid_inputs = [
            "Wdrożenie pakietu 3 zabezpieczeń silnika w 08 Zespół AI/Skrypty/",
            "- [ ] Refaktoryzacja bazy sqlite i migracja tabeli telemetrii w PR-018",
            "Audyt bezpieczeństwa procedur drenażu skrzynki i walidacja tokenów",
            "Przygotowanie wydania LAB v3.1: audyt Ady i wdrożenie hooków Antigravity",
        ]
        for val in valid_inputs:
            valid, err = validate_handoff(val)
            self.assertTrue(valid, f"Powinien zaakceptować konkretny handoff: '{val}' (błąd: {err})")
            self.assertIsNone(err)


class TestCheckpointHandoffExecution(unittest.TestCase):
    """Testy integracyjne SessionCheckpoint z walidacją handoffu."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        shutil.copy2(find_vault_root() / "lab.config.toml", self.tmp / "lab.config.toml")
        cfg = ConfigManager(vault_root=self.tmp)
        (self.tmp / cfg.get_room_by_role("governance-and-engine").path / "Dzienniki Sesji").mkdir(
            parents=True, exist_ok=True
        )
        (self.tmp / cfg.get_room_by_role("daily-workbench").path).mkdir(parents=True, exist_ok=True)
        self.clean_tr = self.tmp / "clean_transcript.jsonl"
        self.clean_tr.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Witaj"}) + "\n" +
            json.dumps({"step_index": 1, "source": "MODEL", "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "research"}}]}) + "\n",
            encoding="utf-8"
        )
        self.cp = SessionCheckpoint(vault_root=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_checkpoint_fails_without_handoff(self) -> None:
        with self.assertRaises(HandoffValidationError) as ctx:
            self.cp.run_checkpoint(dry_run=True, transcript_path=self.clean_tr, handoff=None)
        self.assertIn("BŁĄD CHECKPOINTU", str(ctx.exception))

    def test_run_checkpoint_fails_with_generic_handoff(self) -> None:
        with self.assertRaises(HandoffValidationError) as ctx:
            self.cp.run_checkpoint(
                dry_run=True,
                transcript_path=self.clean_tr,
                handoff="- [ ] Przegląd Agendy Roboczej Dnia na otwarcie kolejnej sesji.",
            )
        self.assertIn("BŁĄD CHECKPOINTU", str(ctx.exception))

    def test_run_checkpoint_creates_log_with_handoff_section(self) -> None:
        concrete_handoff = "Wdrożenie pakietu 3 zabezpieczeń silnika w 08 Zespół AI/Skrypty/"
        receipt = self.cp.run_checkpoint(
            slug="test-handoff",
            dry_run=False,
            transcript_path=self.clean_tr,
            handoff=concrete_handoff,
        )
        self.assertEqual(receipt.handoff, concrete_handoff)

        log_path = self.tmp / receipt.session_log_path
        self.assertTrue(log_path.is_file())
        content = log_path.read_text(encoding="utf-8")

        self.assertIn("## 4. Punkt Startowy Kolejnej Sesji (Handoff)", content)
        self.assertIn(concrete_handoff, content)
        self.assertNotIn("## 4. Zadania Otwarte i Następne Kroki", content)

    def test_extract_handoff_from_existing_session_log(self) -> None:
        concrete = "Dokończenie integracji pre_tool_guard i testów jednostkowych."
        content_new_header = (
            "# Dziennik Sesji\n\n"
            "## 3. Rezultaty\n- test\n\n"
            f"## 4. Punkt Startowy Kolejnej Sesji (Handoff)\n{concrete}\n"
        )
        self.assertEqual(extract_handoff_from_log_content(content_new_header), concrete)

        content_old_header = (
            "# Dziennik Sesji\n\n"
            "## 3. Rezultaty\n- test\n\n"
            f"## 4. Zadania Otwarte i Następne Kroki\n{concrete}\n"
        )
        self.assertEqual(extract_handoff_from_log_content(content_old_header), concrete)

    def test_cli_requires_handoff_or_fails(self) -> None:
        test_args = [
            "checkpoint.py",
            "--vault-root",
            str(self.tmp),
            "--dry-run",
            "--transcript-path",
            str(self.clean_tr),
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = checkpoint_main()
            self.assertEqual(ret, 2)
            out = json.loads(buf.getvalue())
            self.assertEqual(out.get("status"), "error")
            self.assertIn("BŁĄD CHECKPOINTU", out.get("message", ""))

    def test_cli_succeeds_with_valid_handoff(self) -> None:
        concrete = "Weryfikacja deterministycznego punktu startowego przez CLI checkpointu"
        test_args = [
            "checkpoint.py",
            "--vault-root",
            str(self.tmp),
            "--dry-run",
            "--transcript-path",
            str(self.clean_tr),
            "--json",
            "--handoff",
            concrete,
        ]
        with patch.object(sys, "argv", test_args):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = checkpoint_main()
            self.assertEqual(ret, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out.get("handoff"), concrete)


if __name__ == "__main__":
    unittest.main()
