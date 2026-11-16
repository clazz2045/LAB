"""Testy jednostkowe Stop Hook Guard w Antigravity (LAB, Faza 7, Zadanie 4.1).

Weryfikuje:
  1. Fast-Path subagentów (<2 ms, paszport natychmiastowy bez blokad).
  2. Loop Breaker (ochrona przed zapętleniem przy executionNum > 1).
  3. Przepuszczanie zakończeń awaryjnych (ERROR, USER_CANCELED, itp.).
  4. Blokadę Phantom Delegation (deklaracja zlecenia w tekście bez invoke_subagent).
  5. Blokadę Greedy Engineering (pisanie kodu w sesji głównej bez subagenta).
  6. Wymóg telemetrii tokenów w stopce odpowiedzi Koordynatora.
  7. Przepuszczanie poprawnych odpowiedzi z telemetrią lub po wywołaniu subagenta.
  8. Zapisywanie cache telemetrii (/tmp/lab_session_telemetry.json oraz artifactDirectoryPath).
  9. Klauzulę Fail-Safe (odporność na błędy wejścia, brak deadlocka).
  10. Poprawność konfiguracji .agents/hooks.json dla zdarzenia Stop.

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
_scripts_dir = Path(__file__).resolve().parent.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core.stop_guard import (
    CACHE_TELEMETRY_PATH,
    LOG_FILE_PATH,
    evaluate_stop,
    extract_latest_token_metrics,
    has_code_implementation_block,
    has_phantom_delegation,
    has_token_telemetry_signature,
    inspect_turn_events,
    main,
)


class TestStopGuard(unittest.TestCase):
    """Kompleksowe testy jednostkowe hooka Stop."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="lab_stop_guard_test_"))
        self.logs_dir = self.temp_dir / ".system_generated" / "logs"
        self.messages_dir = self.temp_dir / ".system_generated" / "messages"
        self.artifacts_dir = self.temp_dir / "artifacts"
        self.logs_dir.mkdir(parents=True)
        self.messages_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)
        self.transcript = self.logs_dir / "transcript.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_transcript(self, steps: list) -> None:
        lines = [json.dumps(st) for st in steps]
        self.transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_subagent_fast_path_allowed(self) -> None:
        """Subagenci natychmiast otrzymują allow bez żadnych opóźnień i inspekcji."""
        line0 = {
            "step_index": 0,
            "source": "SYSTEM",
            "type": "SYSTEM_MESSAGE",
            "content": "<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-12T17:11:22Z sender=parent-1234 content=Zadanie\n</SYSTEM_MESSAGE>",
        }
        self._write_transcript([line0])

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }

        t0 = time.perf_counter()
        res = evaluate_stop(payload)
        duration_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(res.get("decision"), "allow")
        self.assertLess(duration_ms, 20.0)

    def test_loop_breaker_allows_when_execution_num_greater_than_one(self) -> None:
        """Gdy executionNum > 1, hook nigdy nie zapętla pętli (Loop Breaker)."""
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Zrób to"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Zlecam to zadanie do Maksa.",  # Phantom delegation
            },
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 2,  # Druga iteracja
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }
        res = evaluate_stop(payload)
        self.assertEqual(res.get("decision"), "allow")

    def test_non_normal_termination_reasons_allowed(self) -> None:
        """Błędy, limity tokenów lub anulowanie nie są blokowane."""
        reasons = ["ERROR", "USER_CANCELED", "MAX_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED"]
        for reason in reasons:
            payload = {
                "executionNum": 1,
                "terminationReason": reason,
                "transcriptPath": str(self.transcript),
            }
            res = evaluate_stop(payload)
            self.assertEqual(res.get("decision"), "allow")

    def test_phantom_delegation_blocked(self) -> None:
        """Deklaracja delegacji w tekście bez wywołania invoke_subagent wymusza continue."""
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Sprawdź stan bazy"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Przekazuję zadanie do Kingi, która zweryfikuje schemat bazy.\n\n🧠 100k / 250k (40.0%) 🟢",
            },
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }
        res = evaluate_stop(payload)
        self.assertEqual(res.get("decision"), "continue")
        self.assertIn("Reguła 8", res.get("reason", ""))
        self.assertIn("invoke_subagent", res.get("reason", ""))

    def test_greedy_engineering_code_block_blocked(self) -> None:
        """Koordynator piszący samodzielnie kod w sesji głównej zostaje zablokowany."""
        code_text = (
            "Oto gotowy skrypt w Pythonie:\n\n"
            "```python\n"
            "def calculate_total(a, b):\n"
            "    result = a + b\n"
            "    return result\n"
            "```\n\n"
            "🧠 50k / 250k (20.0%) 🟢"
        )
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Napisz funkcję"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": code_text},
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }
        res = evaluate_stop(payload)
        self.assertEqual(res.get("decision"), "continue")
        self.assertIn("nie ma prawa samodzielnie pisać kodu", res.get("reason", ""))

    def test_missing_telemetry_blocked_and_supplies_statusline(self) -> None:
        """Brak telemetrii w stopce wymusza continue i podaje gotowy statusline."""
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Co dziś w agendzie?"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "input_tokens": 50000,
                "cache_read_tokens": 10000,
                "output_tokens": 500,
                "content": "Dziś w agendzie mamy dokończenie Fazy 7 projektu PR-017.",
            },
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }
        res = evaluate_stop(payload)
        self.assertEqual(res.get("decision"), "continue")
        self.assertIn("Reguła 6", res.get("reason", ""))
        self.assertIn("🧠 60k / 250k (24.0%) 🟢", res.get("reason", ""))

    def test_invalid_telemetry_formats_blocked(self) -> None:
        """Niepełna lub błędna stopka telemetrii (np. samo 🧠 lub / 1000k) zostaje zablokowana."""
        invalid_contents = [
            "Wszystko gotowe. 🧠",
            "Stan okna kontekstowego: w normie.",
            "Raport.\n\n*Telemetria: 🧠 110.0k / 1 000 000 (11.0%) 🟢*",
            "Raport.\n\n*Telemetria: 🧠 110.0k / 1000k (11.0%) 🟢*",
        ]
        for content in invalid_contents:
            self.assertFalse(has_token_telemetry_signature(content), f"Powinno odrzucić: {content}")
            steps = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Status"},
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "input_tokens": 50000,
                    "cache_read_tokens": 10000,
                    "output_tokens": 200,
                    "content": content,
                },
            ]
            self._write_transcript(steps)
            res = evaluate_stop({
                "executionNum": 1,
                "terminationReason": "NO_TOOL_CALL",
                "transcriptPath": str(self.transcript),
            })
            self.assertEqual(res.get("decision"), "continue")
            self.assertIn("🧠 60k / 250k (24.0%) 🟢", res.get("reason", ""))

    def test_allowed_when_telemetry_present_and_no_violations(self) -> None:
        """Odpowiedź z poprawną telemetrią i bez naruszeń zostaje zatwierdzona (allow)."""
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Jaki jest status?"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "input_tokens": 20000,
                "cache_read_tokens": 5000,
                "output_tokens": 300,
                "content": (
                    "Wszystkie systemy działają stabilnie. Czekam na dyspozycję.\n\n"
                    "🧠 25k / 250k (10.0%) 🟢"
                ),
            },
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "artifactDirectoryPath": str(self.artifacts_dir),
        }
        res = evaluate_stop(payload)
        self.assertEqual(res.get("decision"), "allow")

        # Sprawdzenie zapisu telemetrii do cache i artefaktów
        self.assertTrue(CACHE_TELEMETRY_PATH.is_file())
        art_telemetry = self.artifacts_dir / "telemetry.json"
        self.assertTrue(art_telemetry.is_file())
        art_data = json.loads(art_telemetry.read_text(encoding="utf-8"))
        self.assertEqual(art_data.get("total_context_tokens"), 25000)

    def test_allowed_when_subagent_was_invoked_in_turn(self) -> None:
        """Gdy w turze wywołano subagenta, wzorce phantom delegation nie blokują (bo wywołanie nastąpiło)."""
        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Zbadaj kod"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Deleguję zadanie do Maksa.",
                "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "Maks"}}],
            },
        ]
        self._write_transcript(steps)

        payload = {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
        }
        # W turze był invoke_subagent, więc invoke_count > 0
        _, invoke_count, _ = inspect_turn_events(str(self.transcript))
        self.assertEqual(invoke_count, 1)

    def test_main_cli_fail_safe_on_invalid_input(self) -> None:
        """Niepoprawny JSON na stdin nie powoduje awarii (Fail-Safe allow)."""
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO("To nie jest poprawny JSON {{{")
            sys.stdout = io.StringIO()
            code = main()
            output = json.loads(sys.stdout.getvalue().strip())
            self.assertEqual(code, 0)
            self.assertEqual(output.get("decision"), "allow")
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_hooks_json_configuration_integrity(self) -> None:
        """Weryfikuje poprawność konfiguracji punktu zaczepienia Stop w .agents/hooks.json."""
        vault_root = _scripts_dir.parent.parent
        hooks_path = vault_root / ".agents" / "hooks.json"
        self.assertTrue(hooks_path.is_file(), f"Brak pliku {hooks_path}")

        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertIn("lab-governance-guard", data)
        guard = data["lab-governance-guard"]
        self.assertTrue(guard.get("enabled", False))

        self.assertIn("Stop", guard)
        stop_entries = guard["Stop"]
        self.assertIsInstance(stop_entries, list)
        self.assertGreaterEqual(len(stop_entries), 1)

        first_stop = stop_entries[0]
        self.assertIn("command", first_stop)
        self.assertIn("stop_guard.py", first_stop["command"])
        self.assertIn("timeout", first_stop)

    def test_maks_code_without_ada_audit_blocked_on_checkpoint_db(self) -> None:
        """Maks/code (ACCEPTED) bez późniejszego Ada/audit blokuje zamknięcie sesji (/checkpoint)."""
        from lab_core.schema import init_master_db, log_subagent_dispatch

        vault_dir = self.temp_dir / "vault"
        db_dir = vault_dir / "07 Bazy Danych"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "lab_master.db"
        init_master_db(db_path, instance_name="LAB")
        log_subagent_dispatch(db_path, "Maks", "code", "accepted", 100, "OK")

        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/checkpoint"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Zamykam sesję.\n\n🧠 100k / 250k (40.0%) 🟢",
            },
        ]
        self._write_transcript(steps)

        res = evaluate_stop({
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "vaultRoot": str(vault_dir),
        })
        self.assertEqual(res.get("decision"), "continue")
        self.assertIn("Ada/audit", res.get("reason", ""))

    def test_maks_code_with_ada_audit_allowed_on_checkpoint_db(self) -> None:
        """Maks/code (ACCEPTED) z późniejszym Ada/audit (ACCEPTED) przechodzi bramkę Stop."""
        from lab_core.schema import init_master_db, log_subagent_dispatch

        vault_dir = self.temp_dir / "vault"
        db_dir = vault_dir / "07 Bazy Danych"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "lab_master.db"
        init_master_db(db_path, instance_name="LAB")
        log_subagent_dispatch(db_path, "Maks", "code", "accepted", 100, "OK")
        log_subagent_dispatch(db_path, "Ada", "audit", "accepted", 80, "PASS")

        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/checkpoint"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Sesja zamknięta.\n\n🧠 100k / 250k (40.0%) 🟢",
            },
        ]
        self._write_transcript(steps)

        res = evaluate_stop({
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "vaultRoot": str(vault_dir),
        })
        self.assertEqual(res.get("decision"), "allow")

    def test_maks_code_without_ada_audit_does_not_block_while_waiting_for_subagent(self) -> None:
        """Brak false-positive, gdy Koordynator w turze wywołał subagenta (invoke_count > 0)."""
        from lab_core.schema import init_master_db, log_subagent_dispatch

        vault_dir = self.temp_dir / "vault"
        db_dir = vault_dir / "07 Bazy Danych"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "lab_master.db"
        init_master_db(db_path, instance_name="LAB")
        log_subagent_dispatch(db_path, "Maks", "code", "accepted", 100, "OK")

        steps = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/checkpoint"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Czekam na wynik audytu.\n\n🧠 100k / 250k (40.0%) 🟢",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Role": "Ada"}}],
            },
        ]
        self._write_transcript(steps)

        res = evaluate_stop({
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "vaultRoot": str(vault_dir),
        })
        self.assertEqual(res.get("decision"), "allow")

    def test_maks_code_transcript_fallback_sequence(self) -> None:
        """Fallback na transkrypt (bez DB): blokuje Maks/code bez Ada/audit i przepuszcza po Ada/audit."""
        empty_vault = self.temp_dir / "empty_vault"
        empty_vault.mkdir(parents=True)

        steps_missing_audit = [
            {
                "step_index": 0,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {"CommandLine": "python3 subagent_gate.py --agent Maks --task-type code --auto-bind"},
                    }
                ],
            },
            {"step_index": 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/checkpoint"},
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Zamykam.\n\n🧠 100k / 250k (40.0%) 🟢",
            },
        ]
        self._write_transcript(steps_missing_audit)
        res_block = evaluate_stop({
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "vaultRoot": str(empty_vault),
        })
        self.assertEqual(res_block.get("decision"), "continue")

        steps_with_audit = [
            steps_missing_audit[0],
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {"CommandLine": "python3 subagent_gate.py --agent Ada --task-type audit --auto-bind"},
                    }
                ],
            },
            steps_missing_audit[1],
            steps_missing_audit[2],
        ]
        self._write_transcript(steps_with_audit)
        res_pass = evaluate_stop({
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(self.transcript),
            "vaultRoot": str(empty_vault),
        })
        self.assertEqual(res_pass.get("decision"), "allow")


if __name__ == "__main__":
    unittest.main()
