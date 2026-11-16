"""Testy jednostkowe Poziomu 1 Ochrony (PreToolUse Hook Guard) w Antigravity.

Weryfikuje:
  1. Fast-Path subagentów (<2 ms, paszport natychmiastowy bez blokad).
  2. Białą listę Koordynatora (odczyt/zapis Agendy, Dashboardu, skryptów sesji, git status).
  3. Blokadę odczytu plików produkcyjnych (kod, projekty, baza wiedzy).
  4. Blokadę modyfikacji plików produkcyjnych przez Koordynatora.
  5. Blokadę narzędzi zwiadu powłokowego (cat, grep, find, awk) oraz chainingu (&&, |).
  6. Klauzulę Fail-Safe (odporność na błędy wejścia, brak deadlocka).
  7. Poprawność pliku konfiguracyjnego .agents/hooks.json.

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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab_core.pre_tool_guard import (
    DENY_REASON_COMMAND,
    DENY_REASON_READ,
    DENY_REASON_RECON,
    DENY_REASON_WRITE,
    evaluate_pre_tool,
    is_allowed_coordinator_command,
    is_allowed_coordinator_read,
    is_allowed_coordinator_write,
    is_subagent_session,
    main,
    normalize_target_path,
)


class TestSubagentFastPath(unittest.TestCase):
    """Testy szybkiej ścieżki (Fast-Path) dla wyizolowanych subagentów."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="lab_subagent_test_"))
        self.logs_dir = self.temp_dir / ".system_generated" / "logs"
        self.messages_dir = self.temp_dir / ".system_generated" / "messages"
        self.logs_dir.mkdir(parents=True)
        self.messages_dir.mkdir(parents=True)
        self.transcript = self.logs_dir / "transcript.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_subagent_detected_by_transcript_first_line(self) -> None:
        line0 = {
            "step_index": 0,
            "source": "SYSTEM",
            "type": "SYSTEM_MESSAGE",
            "content": "<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-12T17:11:22Z sender=parent-1234 content=Zadanie\n</SYSTEM_MESSAGE>",
        }
        self.transcript.write_text(json.dumps(line0) + "\n", encoding="utf-8")

        t0 = time.perf_counter()
        is_sub = is_subagent_session(str(self.transcript))
        duration_ms = (time.perf_counter() - t0) * 1000

        self.assertTrue(is_sub)
        self.assertLess(duration_ms, 20.0)  # Zwykle < 1 ms

    def test_subagent_detected_by_incoming_messages_dir(self) -> None:
        # Pusty transkrypt, ale obecna wiadomość przychodząca w katalogu messages
        self.transcript.write_text("", encoding="utf-8")
        msg_file = self.messages_dir / "msg_001.json"
        msg_file.write_text(json.dumps({"sender": "parent-uuid", "content": "hello"}), encoding="utf-8")

        self.assertTrue(is_subagent_session(str(self.transcript)))

    def test_subagent_always_allowed_even_for_guarded_tools(self) -> None:
        line0 = {
            "step_index": 0,
            "source": "SYSTEM",
            "type": "SYSTEM_MESSAGE",
            "content": "[Message] timestamp=2026-11-12 sender=parent-1234 content=test",
        }
        self.transcript.write_text(json.dumps(line0) + "\n", encoding="utf-8")

        # Subagent może swobodnie modyfikować kod i przeszukiwać repozytorium
        payloads = [
            {
                "toolCall": {"name": "run_command", "args": {"CommandLine": "grep -r 'todo' ."}},
                "transcriptPath": str(self.transcript),
            },
            {
                "toolCall": {"name": "read_file", "args": {"Path": "05 Baza Wiedzy/Architektura.md"}},
                "transcriptPath": str(self.transcript),
            },
            {
                "toolCall": {"name": "write_file", "args": {"Path": "08 Zespół AI/Skrypty/lab_core/test.py"}},
                "transcriptPath": str(self.transcript),
            },
        ]
        for p in payloads:
            res = evaluate_pre_tool(p)
            self.assertEqual(res.get("decision"), "allow", f"Failed for payload: {p}")


class TestCoordinatorReadGuard(unittest.TestCase):
    """Testy uprawnień odczytu (read_file) dla sesji Koordynatora."""

    def setUp(self) -> None:
        self.temp_vault = Path(tempfile.mkdtemp(prefix="lab_vault_test_"))
        (self.temp_vault / "02 Warsztat").mkdir(parents=True)
        (self.temp_vault / "03 Projekty").mkdir(parents=True)
        (self.temp_vault / "05 Baza Wiedzy").mkdir(parents=True)
        (self.temp_vault / "08 Zespół AI" / "Role" / "Koordynator").mkdir(parents=True)
        (self.temp_vault / "08 Zespół AI" / "Skrypty").mkdir(parents=True)

        (self.temp_vault / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")
        (self.temp_vault / "GEMINI.md").write_text("# GEMINI", encoding="utf-8")
        (self.temp_vault / "lab.config.toml").write_text("[vault]", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "2026-11-12 – Agenda Robocza.md").write_text("# Agenda", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "Dashboard LAB.md").write_text("# Dashboard", encoding="utf-8")
        (self.temp_vault / "08 Zespół AI" / "Role" / "Koordynator" / "AGENT.md").write_text("# Koordynator", encoding="utf-8")

        # Koordynator: krok 0 to USER_EXPLICIT
        self.coord_transcript = self.temp_vault / "transcript.jsonl"
        self.coord_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "content": "Witaj Adamie"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_vault, ignore_errors=True)

    def test_allowed_reads(self) -> None:
        allowed = [
            "02 Warsztat/2026-11-12 – Agenda Robocza.md",
            "02 Warsztat/Dashboard LAB.md",
            "AGENTS.md",
            "GEMINI.md",
            "lab.config.toml",
            "08 Zespół AI/Role/Koordynator/AGENT.md",
            "/tmp/temp_debug.txt",
            str(Path.home() / ".gemini" / "antigravity" / "brain" / "session-123" / "transcript.jsonl"),
        ]
        for path_str in allowed:
            self.assertTrue(
                is_allowed_coordinator_read(path_str, self.temp_vault),
                f"Should allow read: {path_str}",
            )
            payload = {
                "toolCall": {"name": "read_file", "args": {"Path": path_str}},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "allow", f"Evaluation failed for {path_str}")

    def test_denied_reads(self) -> None:
        denied = [
            "08 Zespół AI/Skrypty/session_bootstrap.py",
            "05 Baza Wiedzy/Architektura.md",
            "03 Projekty/PR-017 LAB 3.0/Produkt/file.md",
            "04 Operacje/Procedury/SOP.md",
            "06 Zasoby/Notatki/ref.md",
            "07 Bazy Danych/lab_master.db",
        ]
        for path_str in denied:
            self.assertFalse(
                is_allowed_coordinator_read(path_str, self.temp_vault),
                f"Should deny read: {path_str}",
            )
            payload = {
                "toolCall": {"name": "read_file", "args": {"Path": path_str}},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "deny", f"Should deny {path_str}")
            self.assertEqual(res.get("reason"), DENY_REASON_READ)


class TestCoordinatorWriteGuard(unittest.TestCase):
    """Testy uprawnień zapisu (write_file / edit_file) dla sesji Koordynatora."""

    def setUp(self) -> None:
        self.temp_vault = Path(tempfile.mkdtemp(prefix="lab_vault_write_test_"))
        (self.temp_vault / "02 Warsztat").mkdir(parents=True)
        self.coord_transcript = self.temp_vault / "transcript.jsonl"
        self.coord_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "content": "Witaj"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_vault, ignore_errors=True)

    def test_allowed_writes(self) -> None:
        allowed = [
            "02 Warsztat/2026-11-12 – Agenda Robocza.md",
            "02 Warsztat/Dashboard LAB.md",
            "/tmp/temp_output.json",
            str(Path.home() / ".gemini" / "antigravity" / "brain" / "session" / "artifact.md"),
        ]
        for path_str in allowed:
            self.assertTrue(
                is_allowed_coordinator_write(path_str, self.temp_vault),
                f"Should allow write: {path_str}",
            )
            for tool_name in ("write_file", "edit_file"):
                payload = {
                    "toolCall": {"name": tool_name, "args": {"Path": path_str, "Content": "x"}},
                    "transcriptPath": str(self.coord_transcript),
                }
                res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
                self.assertEqual(res.get("decision"), "allow", f"Failed for {tool_name} on {path_str}")

    def test_denied_writes(self) -> None:
        denied = [
            "AGENTS.md",
            "lab.config.toml",
            "08 Zespół AI/Skrypty/test.py",
            "05 Baza Wiedzy/nowa.md",
            "03 Projekty/PR-017/nowy.md",
        ]
        for path_str in denied:
            self.assertFalse(
                is_allowed_coordinator_write(path_str, self.temp_vault),
                f"Should deny write: {path_str}",
            )
            for tool_name in ("write_file", "edit_file"):
                payload = {
                    "toolCall": {"name": tool_name, "args": {"Path": path_str, "Content": "x"}},
                    "transcriptPath": str(self.coord_transcript),
                }
                res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
                self.assertEqual(res.get("decision"), "deny", f"Should deny {tool_name} on {path_str}")
                self.assertEqual(res.get("reason"), DENY_REASON_WRITE)


class TestCoordinatorCommandGuard(unittest.TestCase):
    """Testy poleceń powłoki (run_command) dla sesji Koordynatora."""

    def test_allowed_commands(self) -> None:
        allowed = [
            'python3 "08 Zespół AI/Skrypty/session_bootstrap.py" --json',
            'python3 "08 Zespół AI/Skrypty/checkpoint.py"',
            'python3 "08 Zespół AI/Skrypty/check_quality.py" --summary',
            'python3 "08 Zespół AI/Skrypty/rollover_agenda.py"',
            'python3 "08 Zespół AI/Skrypty/lab_core/subagents.py" --file x.md',
            'PYTHONPATH="08 Zespół AI/Skrypty" python3 -m lab_core.subagents --file x.md',
            'python3 "08 Zespół AI/Skrypty/lab_core/telemetry.py"',
            'PYTHONPATH="08 Zespół AI/Skrypty" python3 -W ignore::RuntimeWarning:runpy -m lab_core.telemetry --statusline',
            'sqlite3 "07 Bazy Danych/lab_master.db" "SELECT count(*) FROM subagent_dispatches;"',
            "git status",
            "git branch",
            "git diff",
            "date",
            "pwd",
        ]
        for cmd in allowed:
            ok, reason = is_allowed_coordinator_command(cmd)
            self.assertTrue(ok, f"Command should be allowed: {cmd} (reason: {reason})")

    def test_denied_recon_commands(self) -> None:
        recon = [
            "cat AGENTS.md",
            'grep "TODO" "02 Warsztat/Agenda.md"',
            'rg "pattern" .',
            'find "05 Baza Wiedzy" -name "*.md"',
            "awk '{print $1}' file.txt",
            "head -n 20 file.txt",
            "tail -f log.txt",
            # Chaining z narzędziem zwiadu
            'python3 "08 Zespół AI/Skrypty/session_bootstrap.py" && grep "error" log.txt',
            "git status | cat",
            "xargs cat file.txt",
        ]
        for cmd in recon:
            ok, reason = is_allowed_coordinator_command(cmd)
            self.assertFalse(ok, f"Recon command should be denied: {cmd}")
            self.assertEqual(reason, DENY_REASON_RECON, f"Wrong reason for {cmd}: {reason}")

    def test_denied_modifying_or_unauthorized_commands(self) -> None:
        denied = [
            'python3 -m unittest discover "08 Zespół AI/Skrypty/tests"',
            'python3 -c "import os; os.system(\'ls\')"',
            'rm -rf "03 Projekty"',
            "mv file1 file2",
            'git commit -m "hack"',
            'sqlite3 "other.db" "DROP TABLE users;"',
            # Subshell injection
            'python3 "08 Zespół AI/Skrypty/session_bootstrap.py" $(cat secret)',
            'python3 "08 Zespół AI/Skrypty/session_bootstrap.py" `cat secret`',
        ]
        for cmd in denied:
            ok, reason = is_allowed_coordinator_command(cmd)
            self.assertFalse(ok, f"Command should be denied: {cmd}")
            self.assertEqual(reason, DENY_REASON_COMMAND, f"Wrong reason for {cmd}: {reason}")


class TestFailSafeFallback(unittest.TestCase):
    """Testy odporności na awarie (Fail-Safe) - brak deadlocka skarbca."""

    def test_corrupted_json_input(self) -> None:
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO("To nie jest poprawny JSON {{{")
            sys.stdout = io.StringIO()
            main()
            out = sys.stdout.getvalue().strip()
            data = json.loads(out)
            self.assertEqual(data.get("decision"), "allow")
            self.assertIn("fail-safe fallback", data.get("reason", ""))
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_empty_stdin(self) -> None:
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO("")
            sys.stdout = io.StringIO()
            main()
            out = sys.stdout.getvalue().strip()
            data = json.loads(out)
            self.assertEqual(data.get("decision"), "allow")
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_missing_transcript_handled_safely(self) -> None:
        payload = {
            "toolCall": {"name": "read_file", "args": {"Path": "AGENTS.md"}},
            "transcriptPath": "/nieistniejaca/sciezka/transcript.jsonl",
        }
        res = evaluate_pre_tool(payload)
        self.assertEqual(res.get("decision"), "allow")


class TestHooksJsonConfig(unittest.TestCase):
    """Weryfikacja konfiguracji .agents/hooks.json w korzeniu skarbca."""

    def test_hooks_json_exists_and_valid(self) -> None:
        vault_root = Path(__file__).resolve().parent.parent.parent.parent
        hooks_path = vault_root / ".agents" / "hooks.json"
        self.assertTrue(hooks_path.is_file(), f"Missing hooks.json at {hooks_path}")

        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertIn("lab-governance-guard", data)
        guard = data["lab-governance-guard"]
        self.assertTrue(guard.get("enabled"))
        self.assertIn("PreToolUse", guard)

        ptu = guard["PreToolUse"]
        self.assertIsInstance(ptu, list)
        self.assertGreaterEqual(len(ptu), 1)

        rule = ptu[0]
        matcher = rule.get("matcher", "")
        for tool in ("run_command", "read_file", "write_file", "edit_file"):
            self.assertIn(tool, matcher)

        hooks_list = rule.get("hooks", [])
        self.assertGreaterEqual(len(hooks_list), 1)
        cmd = hooks_list[0].get("command", "")
        self.assertIn("pre_tool_guard.py", cmd)
        self.assertIn("../08 Zespół AI", cmd)


if __name__ == "__main__":
    unittest.main()
