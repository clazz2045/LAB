"""Testy jednostkowe Poziomu 1 Ochrony (PreToolUse Hook Guard) w Antigravity.

Weryfikuje:
  1. Fast-Path subagentów (<2 ms, paszport natychmiastowy bez blokad).
  2. Ścisłą białą listę odczytu dla Koordynatora (Zero Code Reading in Main) oraz licznik odczytów w czasie rzeczywistym (max 2).
  3. Białą listę zapisu Koordynatora (Agenda, Dashboard, brain/tmp) oraz twardą blokadę zapisu
     plików produkcyjnych (kod, projekty, baza wiedzy).
  4. Blokadę narzędzi zwiadu powłokowego (cat, grep, find, awk) oraz chainingu (&&, |).
  5. Klauzulę Fail-Safe (odporność na błędy wejścia, brak deadlocka).
  6. Poprawność pliku konfiguracyjnego .agents/hooks.json.

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
    COORDINATOR_READ_LIMIT,
    DENY_REASON_COMMAND,
    DENY_REASON_RECON,
    DENY_REASON_RESEARCH_MODEL,
    DENY_REASON_SUBAGENT_MISSING_MODEL,
    DENY_REASON_WRITE,
    DENY_REASON_READ_CODE,
    DENY_REASON_READ_LIMIT,
    DENY_REASON_READ_WHITELIST,
    evaluate_pre_tool,
    is_allowed_coordinator_command,
    is_allowed_coordinator_read,
    is_allowed_coordinator_subagent,
    is_allowed_coordinator_write,
    is_subagent_session,
    main,
    normalize_target_path,
    reset_coordinator_read_counter,
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
    """Testy uprawnień odczytu (read_file / view_file) oraz białej listy ścieżek dla sesji Koordynatora."""

    def setUp(self) -> None:
        self.temp_vault = Path(tempfile.mkdtemp(prefix="lab_vault_test_"))
        self.state_file = self.temp_vault / "read_state.json"
        (self.temp_vault / "02 Warsztat").mkdir(parents=True)
        (self.temp_vault / "03 Projekty").mkdir(parents=True)
        (self.temp_vault / "05 Baza Wiedzy").mkdir(parents=True)
        (self.temp_vault / "08 Zespół AI" / "Role" / "Koordynator").mkdir(parents=True)
        (self.temp_vault / "08 Zespół AI" / "Dzienniki Sesji" / "2026" / "11").mkdir(parents=True)
        (self.temp_vault / "08 Zespół AI" / "Skrypty" / "tests").mkdir(parents=True)

        (self.temp_vault / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")
        (self.temp_vault / "GEMINI.md").write_text("# GEMINI", encoding="utf-8")
        (self.temp_vault / "lab.config.toml").write_text("[vault]", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "2026-11-12 – Agenda Robocza.md").write_text("# Agenda", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "Dashboard LAB.md").write_text("# Dashboard", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "Notatka robocza.md").write_text("# Notatka", encoding="utf-8")
        (self.temp_vault / "08 Zespół AI" / "Dzienniki Sesji" / "2026" / "11" / "session.md").write_text("# Dziennik", encoding="utf-8")
        (self.temp_vault / "08 Zespół AI" / "Skrypty" / "session_bootstrap.py").write_text("# Code", encoding="utf-8")
        (self.temp_vault / "08 Zespół AI" / "Skrypty" / "tests" / "test_dummy.py").write_text("# Test", encoding="utf-8")

        # Koordynator: krok 0 to USER_EXPLICIT
        self.coord_transcript = self.temp_vault / "transcript.jsonl"
        self.coord_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "content": "Witaj Adamie"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_vault, ignore_errors=True)

    def test_allowed_whitelist_reads_for_coordinator(self) -> None:
        """Koordynator może czytać wyłącznie pliki Agendy, Dzienników Sesji, Warsztatu oraz sesji."""
        allowed_paths = [
            "02 Warsztat/2026-11-12 – Agenda Robocza.md",
            "02 Warsztat/Dashboard LAB.md",
            "02 Warsztat/Notatka robocza.md",
            "08 Zespół AI/Dzienniki Sesji/2026/11/session.md",
            "/tmp/temp_debug.txt",
            str(Path.home() / ".gemini" / "antigravity" / "brain" / "session-123" / "transcript.jsonl"),
        ]
        for path_str in allowed_paths:
            # Reset stanu licznika przed każdym sprawdzeniem ścieżki
            reset_coordinator_read_counter(state_file=self.state_file)
            for tool_name in ("read_file", "view_file"):
                payload = {
                    "toolCall": {"name": tool_name, "args": {"Path": path_str}},
                    "transcriptPath": str(self.coord_transcript),
                }
                res = evaluate_pre_tool(payload, vault_root=self.temp_vault, state_file=self.state_file)
                self.assertEqual(res.get("decision"), "allow", f"Powinien zezwolić na odczyt {path_str}")

    def test_zero_code_reading_in_main_session(self) -> None:
        """Koordynator ma bezwzględny zakaz czytania plików kodu .py i testów w sesji głównej."""
        code_paths = [
            "08 Zespół AI/Skrypty/session_bootstrap.py",
            "08 Zespół AI/Skrypty/checkpoint.py",
            "08 Zespół AI/Skrypty/tests/test_dummy.py",
            "08 Zespół AI/Skrypty/tests/anything.txt",
        ]
        for path_str in code_paths:
            reset_coordinator_read_counter(state_file=self.state_file)
            for tool_name in ("read_file", "view_file"):
                payload = {
                    "toolCall": {"name": tool_name, "args": {"Path": path_str}},
                    "transcriptPath": str(self.coord_transcript),
                }
                res = evaluate_pre_tool(payload, vault_root=self.temp_vault, state_file=self.state_file)
                self.assertEqual(res.get("decision"), "deny", f"Powinien zablokować inspekcję kodu {path_str}")
                self.assertIn("Koordynator nie ma uprawnień do inspekcji kodu w sesji głównej", res.get("reason", ""))

    def test_disallowed_non_whitelist_files(self) -> None:
        """Koordynator ma zakaz czytania bazy wiedzy, projektów, operacji, zasobów i konfiguracji w sesji głównej."""
        disallowed = [
            "AGENTS.md",
            "lab.config.toml",
            "05 Baza Wiedzy/Architektura.md",
            "03 Projekty/PR-017 LAB 3.0/Produkt/file.md",
            "07 Bazy Danych/lab_master.db",
        ]
        for path_str in disallowed:
            reset_coordinator_read_counter(state_file=self.state_file)
            payload = {
                "toolCall": {"name": "read_file", "args": {"Path": path_str}},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault, state_file=self.state_file)
            self.assertEqual(res.get("decision"), "deny", f"Powinien zablokować odczyt spoza białej listy: {path_str}")
            self.assertIn("Koordynator może czytać wyłącznie pliki Agendy Roboczej", res.get("reason", ""))


class TestCoordinatorReadCounterGuard(unittest.TestCase):
    """Testy licznika odczytów w czasie rzeczywistym (max 2 odczyty przed delegacją)."""

    def setUp(self) -> None:
        self.temp_vault = Path(tempfile.mkdtemp(prefix="lab_vault_counter_test_"))
        self.state_file = self.temp_vault / "read_state.json"
        (self.temp_vault / "02 Warsztat").mkdir(parents=True)
        (self.temp_vault / "02 Warsztat" / "2026-11-18 – Agenda Robocza.md").write_text("# Agenda", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "Dashboard LAB.md").write_text("# Dashboard", encoding="utf-8")
        (self.temp_vault / "02 Warsztat" / "Notatka robocza.md").write_text("# Notatka", encoding="utf-8")

        self.coord_transcript = self.temp_vault / "transcript.jsonl"
        self.coord_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "content": "Witaj"}) + "\n",
            encoding="utf-8",
        )
        reset_coordinator_read_counter(state_file=self.state_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_vault, ignore_errors=True)

    def test_read_limit_blocks_third_consecutive_read(self) -> None:
        """Limit 2 kolejnych odczytów: 1. i 2. dozwolone, 3. próba zablokowana."""
        p1 = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/2026-11-18 – Agenda Robocza.md"}},
            "transcriptPath": str(self.coord_transcript),
        }
        p2 = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/Dashboard LAB.md"}},
            "transcriptPath": str(self.coord_transcript),
        }
        p3 = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/Notatka robocza.md"}},
            "transcriptPath": str(self.coord_transcript),
        }

        # Odczyt 1 -> OK
        res1 = evaluate_pre_tool(p1, vault_root=self.temp_vault, state_file=self.state_file)
        self.assertEqual(res1.get("decision"), "allow")

        # Odczyt 2 -> OK
        res2 = evaluate_pre_tool(p2, vault_root=self.temp_vault, state_file=self.state_file)
        self.assertEqual(res2.get("decision"), "allow")

        # Odczyt 3 -> BLOKADA REGUŁY 8
        res3 = evaluate_pre_tool(p3, vault_root=self.temp_vault, state_file=self.state_file)
        self.assertEqual(res3.get("decision"), "deny")
        self.assertIn("Wykorzystano limit 2 odczytów w sesji głównej", res3.get("reason", ""))
        self.assertIn("Zastosuj zasadę Stop & Ask", res3.get("reason", ""))

    def test_invoke_subagent_resets_read_counter(self) -> None:
        """Wywołanie invoke_subagent resetuje licznik odczytów."""
        p_read = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/2026-11-18 – Agenda Robocza.md"}},
            "transcriptPath": str(self.coord_transcript),
        }
        p_invoke = {
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "research", "Model": "flash", "Role": "Badacz", "Prompt": "Zbadaj X"},
            },
            "transcriptPath": str(self.coord_transcript),
        }

        # 2 odczyty
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")

        # 3. odczyt zablokowany
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "deny")

        # Koordynator deleguje zadanie do subagenta
        self.assertEqual(evaluate_pre_tool(p_invoke, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")

        # Licznik zresetowany -> kolejny odczyt ponownie dozwolony
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")

    def test_switching_transcript_resets_counter_and_last_user_idx(self) -> None:
        """Przełączenie transcript_path na nową sesję resetuje licznik i last_user_step_idx."""
        # Sesja 1: długi transkrypt z wejściem użytkownika na kroku 10
        tr1 = self.temp_vault / "transcript_session1.jsonl"
        lines1 = [
            json.dumps({"step_index": i, "source": "SYSTEM", "content": f"msg {i}"})
            for i in range(10)
        ]
        lines1.append(json.dumps({"step_index": 10, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Sesja 1"}))
        tr1.write_text("\n".join(lines1) + "\n", encoding="utf-8")

        p_read_s1 = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/2026-11-18 – Agenda Robocza.md"}},
            "transcriptPath": str(tr1),
        }

        # Wykorzystaj 2 odczyty w Sesji 1
        self.assertEqual(evaluate_pre_tool(p_read_s1, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")
        self.assertEqual(evaluate_pre_tool(p_read_s1, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")
        # Trzeci zablokowany
        self.assertEqual(evaluate_pre_tool(p_read_s1, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "deny")

        # Sesja 2: nowa sesja z wejściem użytkownika na kroku 0 (indeks mniejszy niż 10 z poprzedniej sesji)
        tr2 = self.temp_vault / "transcript_session2.jsonl"
        tr2.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Sesja 2"}) + "\n",
            encoding="utf-8",
        )

        p_read_s2 = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/2026-11-18 – Agenda Robocza.md"}},
            "transcriptPath": str(tr2),
        }

        # W nowej sesji licznik musi być zresetowany do 0 i zezwolić na odczyt
        res = evaluate_pre_tool(p_read_s2, vault_root=self.temp_vault, state_file=self.state_file)
        self.assertEqual(res.get("decision"), "allow", "Nowa sesja powinna zresetować licznik pomimo niższego indeksu kroku")

    def test_new_user_message_in_transcript_resets_read_counter(self) -> None:
        """Nowy krok USER_EXPLICIT w bieżącym transkrypcie resetuje licznik odczytów."""
        tr = self.temp_vault / "transcript_turns.jsonl"
        tr.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Tura 1"}) + "\n",
            encoding="utf-8",
        )

        p_read = {
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/2026-11-18 – Agenda Robocza.md"}},
            "transcriptPath": str(tr),
        }

        # 2 odczyty w turze 1 -> OK
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "allow")
        # 3. odczyt -> zablokowany
        self.assertEqual(evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file).get("decision"), "deny")

        # Użytkownik odpowiada na czacie (krok 1: MODEL, krok 2: USER_EXPLICIT)
        with open(tr, "a", encoding="utf-8") as f:
            f.write(json.dumps({"step_index": 1, "source": "MODEL", "content": "Pytanie do człowieka"}) + "\n")
            f.write(json.dumps({"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Odpowiedź człowieka"}) + "\n")

        # Nowa tura po odpowiedzi człowieka -> licznik zresetowany, odczyt dozwolony
        res = evaluate_pre_tool(p_read, vault_root=self.temp_vault, state_file=self.state_file)
        self.assertEqual(res.get("decision"), "allow", "Nowy krok użytkownika powinien zresetować licznik odczytów")


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
            "toolCall": {"name": "read_file", "args": {"Path": "02 Warsztat/Dashboard LAB.md"}},
            "transcriptPath": "/nieistniejaca/sciezka/transcript.jsonl",
        }
        res = evaluate_pre_tool(payload)
        self.assertEqual(res.get("decision"), "allow")


class TestCoordinatorSubagentGuard(unittest.TestCase):
    """Testy weryfikacji wywołań invoke_subagent w sesji głównej Koordynatora."""

    def setUp(self) -> None:
        self.temp_vault = Path(tempfile.mkdtemp(prefix="lab_vault_subagent_test_"))
        self.coord_transcript = self.temp_vault / "transcript.jsonl"
        self.coord_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "content": "Start"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_vault, ignore_errors=True)

    def test_missing_or_empty_model_is_denied(self) -> None:
        cases = [
            {},
            {"TypeName": "self"},
            {"TypeName": "research"},
            {"TypeName": "self", "Model": ""},
            {"TypeName": "self", "Model": "   "},
            {"type_name": "research", "model": None},
        ]
        for args in cases:
            ok, reason = is_allowed_coordinator_subagent(args)
            self.assertFalse(ok, f"Expected deny for args: {args}")
            self.assertEqual(reason, DENY_REASON_SUBAGENT_MISSING_MODEL)

            payload = {
                "toolCall": {"name": "invoke_subagent", "args": args},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "deny")
            self.assertEqual(res.get("reason"), DENY_REASON_SUBAGENT_MISSING_MODEL)

    def test_research_subagent_model_enforcement(self) -> None:
        # Zabronione dla research: inherit, pro oraz inne
        denied_models = ["inherit", "pro", "INHERIT", "PRO", "ultra"]
        for m in denied_models:
            args = {"TypeName": "research", "Model": m}
            ok, reason = is_allowed_coordinator_subagent(args)
            self.assertFalse(ok, f"Expected deny for research with model={m}")
            self.assertEqual(reason, DENY_REASON_RESEARCH_MODEL)

            payload = {
                "toolCall": {"name": "invoke_subagent", "args": args},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "deny")
            self.assertEqual(res.get("reason"), DENY_REASON_RESEARCH_MODEL)

        # Dozwolone dla research: flash, flash_lite (case-insensitive)
        allowed_models = ["flash", "flash_lite", "FLASH", "Flash_Lite", "  flash  "]
        for m in allowed_models:
            args = {"TypeName": "research", "Model": m}
            ok, reason = is_allowed_coordinator_subagent(args)
            self.assertTrue(ok, f"Expected allow for research with model={m}")
            self.assertIsNone(reason)

            payload = {
                "toolCall": {"name": "invoke_subagent", "args": args},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "allow")

    def test_self_subagent_model_enforcement(self) -> None:
        # Dozwolone dla self: flash, flash_lite, inherit, pro
        allowed_models = ["flash", "flash_lite", "inherit", "pro", "FLASH", "Inherit", "PRO"]
        for m in allowed_models:
            args = {"TypeName": "self", "Model": m}
            ok, reason = is_allowed_coordinator_subagent(args)
            self.assertTrue(ok, f"Expected allow for self with model={m}")
            self.assertIsNone(reason)

            payload = {
                "toolCall": {"name": "invoke_subagent", "args": args},
                "transcriptPath": str(self.coord_transcript),
            }
            res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
            self.assertEqual(res.get("decision"), "allow")

        # Niedozwolony model dla self (np. nieznany string)
        args_invalid = {"TypeName": "self", "Model": "invalid_model"}
        ok, reason = is_allowed_coordinator_subagent(args_invalid)
        self.assertFalse(ok)
        self.assertIn("Niedozwolona wartość", reason or "")

    def test_alternative_keys_supported(self) -> None:
        payload = {
            "tool_name": "invoke_subagent",
            "tool_input": {"type_name": "research", "model": "flash"},
            "transcript_path": str(self.coord_transcript),
        }
        res = evaluate_pre_tool(payload, vault_root=self.temp_vault)
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
        for tool in ("run_command", "read_file", "write_file", "edit_file", "invoke_subagent"):
            self.assertIn(tool, matcher)

        hooks_list = rule.get("hooks", [])
        self.assertGreaterEqual(len(hooks_list), 1)
        cmd = hooks_list[0].get("command", "")
        self.assertIn("pre_tool_guard.py", cmd)
        self.assertIn("../08 Zespół AI", cmd)

    def test_codex_cli_pre_tool_use_adapter(self) -> None:
        from lab_core.pre_tool_guard import evaluate_pre_tool

        vault_root = Path(__file__).resolve().parents[3]
        # Codex CLI Bash z niedozwolonym poleceniem -> deny
        res_bad_cmd = evaluate_pre_tool({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "grep -rn secret ."},
        }, vault_root=vault_root)
        self.assertEqual(res_bad_cmd["decision"], "deny")

        # Codex CLI shell z listą argumentów dla dozwolonego skryptu -> allow
        res_ok_cmd = evaluate_pre_tool({
            "hook_event_name": "PreToolUse",
            "tool_name": "shell",
            "tool_input": {"command": ["python3", "08 Zespół AI/Skrypty/checkpoint.py"]},
        }, vault_root=vault_root)
        self.assertEqual(res_ok_cmd["decision"], "allow")

        # Codex CLI apply_patch na plik produkcyjny -> deny
        res_bad_patch = evaluate_pre_tool({
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"input": "*** Begin Patch\n*** Update File: 08 Zespół AI/Skrypty/lab_core/setup.py\n*** End Patch"},
        }, vault_root=vault_root)
        self.assertEqual(res_bad_patch["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
