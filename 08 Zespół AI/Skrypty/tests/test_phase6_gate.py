"""Testy Fazy 6 PR-017: twarda bramka rejestracji subagentów i audyt zgodności sesji."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab_core import ConfigManager, find_vault_root
from lab_core.compliance import check_session_compliance, find_session_transcript, main as compliance_main
from lab_core.schema import get_master_db_connection, init_master_db, log_subagent_dispatch, mark_dispatch_invalid
from lab_core.subagents import (
    candidate_transcripts,
    distillate_sha256,
    run_gate,
    verify_cc_distillate_binding,
    verify_transcript_has_subagent_call,
)

GOOD = "Fakty: zbadano 3 źródła.\nWnioski: synteza wskazuje A.\nRekomendacja: plik w Bazie Wiedzy."
SUB_ID = "11111111-2222-3333-4444-555555555555"


def _sys_msg(sender: str, body: str, idx: int) -> dict:
    return {
        "step_index": idx, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE",
        "content": f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-12T16:31:13Z sender={sender} priority=HIGH content={body}\n</SYSTEM_MESSAGE>",
    }


def _invoke(idx: int, role: str = "Maks", type_name: str = "self", prompt: str = "x") -> dict:
    return {
        "step_index": idx, "source": "MODEL", "type": "PLANNER_RESPONSE",
        "tool_calls": [{"name": "invoke_subagent", "args": {"Role": role, "TypeName": type_name, "Prompt": prompt}}],
    }


def _call(idx: int, name: str, args: dict | None = None) -> dict:
    return {"step_index": idx, "source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": name, "args": args or {}}]}


def _write(path: Path, steps: list) -> Path:
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n", encoding="utf-8")
    return path


def _isolate_home(tc: unittest.TestCase, home: Path) -> Path:
    """HOME izolowany w tmp (nigdy realny HOME). Zwraca katalog ~/.claude/projects/proj (dozwolony root transkryptów)."""
    patcher = patch.dict(os.environ, {"HOME": str(home)})
    patcher.start()
    tc.addCleanup(patcher.stop)
    for key in ("LAB_TRANSCRIPT_PATH", "CLAUDE_CODE_SESSION_ID", "ANTIGRAVITY_CONVERSATION_ID"):
        os.environ.pop(key, None)
    proj = home / ".claude" / "projects" / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    return proj


CC_ID = "toolu_01AgentCall"


def _cc_call(tool_id: str = CC_ID, name: str = "Agent", subagent_type: str = "Maks", prompt: str = "Zbadaj temat.") -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Deleguję."},
        {"type": "tool_use", "id": tool_id, "name": name,
         "input": {"subagent_type": subagent_type, "description": "analiza", "prompt": prompt}},
    ]}}


def _cc_result(text: str = GOOD, tool_id: str = CC_ID, is_error: bool = False, as_list: bool = False) -> dict:
    content = [{"type": "text", "text": text}] if as_list else text
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error},
    ]}}


class TestHardGate(unittest.TestCase):
    def setUp(self):
        ctx_root = find_vault_root()
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy2(ctx_root / "lab.config.toml", self.tmp / "lab.config.toml")
        self.db_rel = ConfigManager(vault_root=ctx_root).get_room_by_role("sqlite-relational").path
        (self.tmp / self.db_rel).mkdir(parents=True)
        self.db = self.tmp / self.db_rel / "lab_master.db"
        self.tr_dir = _isolate_home(self, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _last(self):
        conn = get_master_db_connection(self.db)
        try:
            return conn.execute("SELECT * FROM subagent_dispatches ORDER BY id DESC").fetchone()
        finally:
            conn.close()

    def _gate(self, text=GOOD, transcript=None, sub_id=SUB_ID, sha="auto", agent="Maks", task="code", brain=None):
        sha = distillate_sha256(text) if sha == "auto" else sha
        return run_gate(text, agent, task, vault_root=self.tmp, transcript_path=transcript,
                        subagent_id=sub_id, content_sha256=sha, brain_dir=brain)

    def test_missing_transcript_rejects(self):
        empty_brain = self.tmp / "brain"
        empty_brain.mkdir()
        self.assertEqual(self._gate(brain=empty_brain), 1)
        self.assertIn("fail-closed", self._last()["summary_excerpt"])

    def test_unreadable_explicit_transcript_rejects(self):
        self.assertEqual(self._gate(transcript=self.tmp / "nie-ma.jsonl"), 1)
        self.assertEqual(self._last()["status"], "rejected")

    def test_env_flags_do_not_disable_check(self):
        import os
        os.environ["LAB_SKIP_TRANSCRIPT_CHECK"] = "1"
        os.environ["LAB_UNIT_TEST"] = "1"
        try:
            self.assertEqual(self._gate(transcript=self.tmp / "nie-ma.jsonl"), 1)
        finally:
            del os.environ["LAB_SKIP_TRANSCRIPT_CHECK"]
            del os.environ["LAB_UNIT_TEST"]

    def test_distillate_without_subagent_id_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr, sub_id=None), 1)
        self.assertIn("--subagent-id", self._last()["summary_excerpt"])

    def test_distillate_without_hash_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr, sha=None), 1)

    def test_reconstructed_text_rejects(self):
        reconstructed = GOOD.replace("3 źródła", "5 źródeł")
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(text=reconstructed, transcript=tr), 1)
        self.assertIn("zrekonstruowany", self._last()["summary_excerpt"])

    def test_wrong_hash_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr, sha="0" * 64), 1)

    def test_message_from_other_sender_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg("99999999-0000-0000-0000-000000000000", GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr), 1)

    def test_model_step_cannot_forge_message(self):
        forged = {"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
                  "content": f"sender={SUB_ID} content={GOOD}"}
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), forged])
        self.assertEqual(self._gate(transcript=tr), 1)

    def test_no_invoke_before_message_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr), 1)

    def test_agent_role_mismatch_rejects(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1, role="Klara", prompt="research"), _sys_msg(SUB_ID, GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr, agent="Kinga"), 1)
        self.assertIn("niezgodny", self._last()["summary_excerpt"])

    def test_bound_distillate_accepted_and_logged(self):
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1, role="Maks"), _sys_msg(SUB_ID, "Raport:\n" + GOOD, 2)])
        self.assertEqual(self._gate(transcript=tr), 0)
        row = self._last()
        self.assertEqual((row["status"], row["subagent_id"], row["content_sha256"]), ("accepted", SUB_ID, distillate_sha256(GOOD)))

    def test_size_rules_still_enforced(self):
        big = "Fakty: a\nWnioski: b\nRekomendacje: c\n" + "x" * 900
        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1), _sys_msg(SUB_ID, big, 2)])
        self.assertEqual(self._gate(text=big, transcript=tr), 1)
        self.assertIn("800", self._last()["summary_excerpt"])


class TestHarnessTranscript(unittest.TestCase):
    def test_conversation_id_selects_session_transcript(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            # brain/ musi leżeć w dozwolonym rootcie (~/.gemini/antigravity/brain) pod izolowanym HOME
            brain = tmp / ".gemini" / "antigravity" / "brain"
            logs = brain / "abc-123" / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            other = brain / "zzz" / ".system_generated" / "logs"
            other.mkdir(parents=True)
            (logs / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
            (other / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
            with patch.dict(os.environ, {"HOME": str(tmp)}):
                for key in ("LAB_TRANSCRIPT_PATH", "CLAUDE_CODE_SESSION_ID"):
                    os.environ.pop(key, None)
                os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "abc-123"
                self.assertEqual(candidate_transcripts(brain_dir=brain), [logs / "transcript.jsonl"])
        finally:
            shutil.rmtree(tmp)


class TestCheckpointAndBootstrap(unittest.TestCase):
    def test_checkpoint_requires_violation_note(self):
        from checkpoint import ComplianceAnnotationRequired, SessionCheckpoint
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            shutil.copy2(find_vault_root() / "lab.config.toml", tmp / "lab.config.toml")
            tr = _write(_isolate_home(self, tmp) / "t.jsonl", [_invoke(1), _call(2, "write_file")])
            cp = SessionCheckpoint(vault_root=tmp)
            with self.assertRaises(ComplianceAnnotationRequired):
                cp.run_checkpoint(dry_run=True, transcript_path=tr, handoff="Weryfikacja błędu braku adnotacji w checkpoint")
            receipt = cp.run_checkpoint(dry_run=True, transcript_path=tr, violation_note="zapis awaryjny, zatwierdzony", handoff="Weryfikacja błędu braku adnotacji w checkpoint")
            self.assertEqual(receipt.compliance["status"], "violation")
        finally:
            shutil.rmtree(tmp)

    def test_checkpoint_reports_open_agenda_threads_without_modifying_file(self):
        from checkpoint import SessionCheckpoint, render_terminal_receipt
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            shutil.copy2(find_vault_root() / "lab.config.toml", tmp / "lab.config.toml")
            cfg = ConfigManager(vault_root=tmp)
            ws_dir = tmp / cfg.get_room_by_role("daily-workbench").path
            ws_dir.mkdir(parents=True, exist_ok=True)
            agenda_path = ws_dir / "2026-11-15 – Agenda Robocza.md"
            original_content = (
                "---\ntype: agenda\n---\n# Agenda\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [x] [ZADANIE] Zamknięty wątek\n"
                "- [ ] [ZADANIE] Otwarty wątek do przypomnienia\n"
                "  - podpunkt\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli\n\n"
            )
            agenda_path.write_text(original_content, encoding="utf-8")

            tr = _write(_isolate_home(self, tmp) / "t.jsonl", [_call(1, "read_file"), _invoke(2)])
            cp = SessionCheckpoint(vault_root=tmp)
            receipt = cp.run_checkpoint(dry_run=True, transcript_path=tr, handoff="Weryfikacja raportowania otwartych wątków agendy")

            self.assertTrue(receipt.agenda_check["section_found"])
            self.assertEqual(
                receipt.agenda_check["open_tasks"],
                ["- [ ] [ZADANIE] Otwarty wątek do przypomnienia"],
            )
            rendered = render_terminal_receipt(receipt)
            self.assertIn("PRZYPOMNIENIE KONTROLNE", rendered)
            self.assertIn("Otwarty wątek do przypomnienia", rendered)
            self.assertEqual(agenda_path.read_text(encoding="utf-8"), original_content)
        finally:
            shutil.rmtree(tmp)

    def test_checkpoint_reports_all_agenda_threads_closed(self):
        from checkpoint import SessionCheckpoint, render_terminal_receipt
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            shutil.copy2(find_vault_root() / "lab.config.toml", tmp / "lab.config.toml")
            cfg = ConfigManager(vault_root=tmp)
            ws_dir = tmp / cfg.get_room_by_role("daily-workbench").path
            ws_dir.mkdir(parents=True, exist_ok=True)
            agenda_path = ws_dir / "2026-11-15 – Agenda Robocza.md"
            agenda_path.write_text(
                "---\ntype: agenda\n---\n# Agenda\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [x] [ZADANIE] Wszystko gotowe\n",
                encoding="utf-8",
            )

            tr = _write(_isolate_home(self, tmp) / "t.jsonl", [_call(1, "read_file"), _invoke(2)])
            cp = SessionCheckpoint(vault_root=tmp)
            receipt = cp.run_checkpoint(dry_run=True, transcript_path=tr, handoff="Weryfikacja raportowania otwartych wątków agendy")

            self.assertTrue(receipt.agenda_check["section_found"])
            self.assertEqual(receipt.agenda_check["open_tasks"], [])
            rendered = render_terminal_receipt(receipt)
            self.assertIn("Wszystkie pozycje w sekcji '## 🎯 Główne Wątki Robocze na Dziś' są zamknięte.", rendered)
        finally:
            shutil.rmtree(tmp)

    def test_checkpoint_warns_on_unprocessed_thought_blocks(self):
        from checkpoint import SessionCheckpoint, render_terminal_receipt
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            shutil.copy2(find_vault_root() / "lab.config.toml", tmp / "lab.config.toml")
            cfg = ConfigManager(vault_root=tmp)
            ws_dir = tmp / cfg.get_room_by_role("daily-workbench").path
            ws_dir.mkdir(parents=True, exist_ok=True)
            agenda_path = ws_dir / "2026-11-15 – Agenda Robocza.md"
            agenda_content = (
                "---\ntype: agenda\n---\n# Agenda\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [x] [ZADANIE] Zadanie zrobione\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli\n\n"
                "Oryginalna poranna myśl 1.\n"
                "---\n"
                "Oryginalna poranna myśl 2.\n"
                "---\n"
            )
            agenda_path.write_text(agenda_content, encoding="utf-8")

            tr = _write(_isolate_home(self, tmp) / "t.jsonl", [_call(1, "read_file"), _invoke(2)])
            cp = SessionCheckpoint(vault_root=tmp)
            receipt = cp.run_checkpoint(dry_run=True, transcript_path=tr, handoff="Weryfikacja raportowania otwartych wątków agendy")

            self.assertTrue(receipt.agenda_check["section_found"])
            self.assertEqual(receipt.agenda_check["open_tasks"], [])
            self.assertEqual(receipt.agenda_check["unprocessed_thought_blocks"], 2)
            self.assertTrue(any("2 nieoznaczonych bloków myśli" in w for w in receipt.warnings))

            rendered = render_terminal_receipt(receipt)
            self.assertIn("OSTRZEŻENIA KONTROLNE", rendered)
            self.assertIn("2 nieoznaczonych bloków myśli", rendered)
            # Upewnij się, że plik Agendy nie został zmodyfikowany (Reguła 1)
            self.assertEqual(agenda_path.read_text(encoding="utf-8"), agenda_content)
        finally:
            shutil.rmtree(tmp)

    def test_bootstrap_prints_coordinator_contract(self):
        from session_bootstrap import SessionBootstrap, format_text_output
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            shutil.copy2(find_vault_root() / "lab.config.toml", tmp / "lab.config.toml")
            ai_room = ConfigManager(vault_root=tmp).get_room_by_role("governance-and-engine").path
            d = tmp / ai_room / "Role" / "Koordynator - Adam"
            d.mkdir(parents=True)
            (d / "AGENT.md").write_text("# Adam kontrakt-testowy", encoding="utf-8")
            sb = SessionBootstrap(vault_root=tmp)
            self.assertIn("kontrakt-testowy", sb.read_coordinator_contract())
            self.assertIn("kontrakt-testowy", format_text_output({
                "status": "READY", "quality_status": "OK", "agenda_file": "x", "inbox_count": 0,
                "coordinator_contract": sb.read_coordinator_contract()}))
        finally:
            shutil.rmtree(tmp)


class TestDispatchInvalidation(unittest.TestCase):
    def test_mark_invalid_keeps_row_and_wal(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            db = tmp / "lab_master.db"
            init_master_db(db)
            ok_id = log_subagent_dispatch(db, "Maks", "code", "accepted", None, "tekst")
            rej_id = log_subagent_dispatch(db, "Maks", "code", "rejected", None, "powod")
            self.assertTrue(mark_dispatch_invalid(db, ok_id, "zrekonstruowany"))
            self.assertFalse(mark_dispatch_invalid(db, rej_id, "n/a"))
            conn = get_master_db_connection(db)
            try:
                a = conn.execute("SELECT status, note, summary_excerpt FROM subagent_dispatches WHERE id=?", (ok_id,)).fetchone()
                r = conn.execute("SELECT status, note FROM subagent_dispatches WHERE id=?", (rej_id,)).fetchone()
                self.assertEqual((a["status"], a["note"], a["summary_excerpt"]), ("invalid_reconstructed", "zrekonstruowany", "tekst"))
                self.assertEqual((r["status"], r["note"]), ("rejected", None))
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp)

    def test_migration_adds_columns_to_legacy_table(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            db = tmp / "legacy.db"
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE subagent_dispatches (id INTEGER PRIMARY KEY AUTOINCREMENT, dispatch_time TEXT NOT NULL, "
                      "agent_name TEXT NOT NULL, task_type TEXT NOT NULL, tokens_used INTEGER, status TEXT NOT NULL, summary_excerpt TEXT)")
            c.commit(); c.close()
            rid = log_subagent_dispatch(db, "Ada", "audit", "rejected", None, "x")
            self.assertGreater(rid, 0)
            self.assertTrue(mark_dispatch_invalid(db, rid, "n", expected_status="rejected"))
        finally:
            shutil.rmtree(tmp)


class TestSessionCompliance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_four_reads_before_invoke_is_violation(self):
        steps = [_call(i, "read_file") for i in range(4)] + [_invoke(5)]
        rep = check_session_compliance(_write(self.tmp / "t.jsonl", steps))
        self.assertEqual(rep["status"], "violation")
        self.assertEqual(rep["reads_before_first_invoke"], 4)

    def test_four_reads_without_invoke_is_violation(self):
        steps = [_call(1, "grep_search"), _call(2, "find_by_name"), _call(3, "read_file"), _call(4, "list_dir")]
        self.assertEqual(check_session_compliance(_write(self.tmp / "t.jsonl", steps))["status"], "violation")

    def test_shell_reads_count(self):
        steps = [_call(i, "run_command", {"CommandLine": "cat plik.md"}) for i in range(4)]
        self.assertEqual(check_session_compliance(_write(self.tmp / "t.jsonl", steps))["status"], "violation")

    def test_three_reads_then_invoke_ok(self):
        steps = [_call(i, "read_file") for i in range(3)] + [_invoke(4)] + [_call(5, "read_file")] * 5
        self.assertEqual(check_session_compliance(_write(self.tmp / "t.jsonl", steps))["status"], "ok")

    def test_write_in_main_session_is_violation(self):
        steps = [_invoke(1), _call(2, "write_file")]
        rep = check_session_compliance(_write(self.tmp / "t.jsonl", steps))
        self.assertEqual((rep["status"], rep["write_calls"]), ("violation", 1))
        rep2 = check_session_compliance(_write(self.tmp / "t2.jsonl", [_invoke(1), _call(2, "edit_file")]))
        self.assertEqual(rep2["status"], "violation")

    def test_missing_transcript_is_violation(self):
        self.assertEqual(check_session_compliance(self.tmp / "brak.jsonl")["status"], "violation")

    def test_unrecognized_schema_is_never_ok(self):
        rep = check_session_compliance(_write(self.tmp / "obcy.jsonl", [{"foo": 1}, {"bar": [2]}]))
        self.assertNotEqual(rep["status"], "ok")
        self.assertEqual(rep["status"], "violation")


def _cc_line(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def _cc_tool(name: str, inp: dict | None = None) -> dict:
    return {"type": "tool_use", "id": "t", "name": name, "input": inp or {}}


class TestSessionComplianceClaudeCode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _cc_file(self, lines: list) -> Path:
        return _write(self.tmp / "cc.jsonl", lines)

    def test_agent_tool_use_is_invoke(self):
        lines = [_cc_line(_cc_tool("Read", {"file_path": "a"})) for _ in range(3)]
        lines += [_cc_line(_cc_tool("Agent", {"prompt": "x"}))]
        lines += [_cc_line(_cc_tool("Read")) for _ in range(5)]
        rep = check_session_compliance(self._cc_file(lines))
        self.assertEqual(rep["status"], "ok")
        self.assertEqual((rep["invoke_count"], rep["reads_before_first_invoke"]), (1, 3))

    def test_four_reads_before_agent_is_violation(self):
        lines = [_cc_line(_cc_tool("Grep")) for _ in range(4)] + [_cc_line(_cc_tool("Agent"))]
        self.assertEqual(check_session_compliance(self._cc_file(lines))["status"], "violation")

    def test_edit_after_agent_is_write_violation(self):
        rep = check_session_compliance(self._cc_file([_cc_line(_cc_tool("Agent")), _cc_line(_cc_tool("Edit"))]))
        self.assertEqual((rep["status"], rep["write_calls"]), ("violation", 1))

    def test_bash_cat_counts_as_read(self):
        lines = [_cc_line(_cc_tool("Bash", {"command": "cat plik.md"})) for _ in range(4)]
        self.assertEqual(check_session_compliance(self._cc_file(lines))["status"], "violation")

    def test_tool_result_and_user_lines_are_ignored(self):
        user = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}
        lines = [user, _cc_line(_cc_tool("Agent")), user]
        self.assertEqual(check_session_compliance(self._cc_file(lines))["status"], "ok")

    def test_invoke_search_matches_claude_code_agent_tool(self):
        """R1: wyszukiwanie wywołań subagenta działa dla transkryptów Claude Code (tool 'Agent')."""
        with_agent = self._cc_file([_cc_line(_cc_tool("Read")), _cc_line(_cc_tool("Agent", {"prompt": "x"}))])
        self.assertTrue(verify_transcript_has_subagent_call(with_agent))
        without = self._cc_file([_cc_line(_cc_tool("Read")), _cc_line(_cc_tool("Edit"))])
        self.assertFalse(verify_transcript_has_subagent_call(without))

    def test_transcript_env_outside_claude_projects_is_rejected(self):
        """B2: LAB_TRANSCRIPT_PATH poza ~/.claude/projects (i brain/) jest odrzucany, nawet gdy czytelny."""
        home = self.tmp / "home"
        (home / ".claude" / "projects" / "proj").mkdir(parents=True)
        inside = home / ".claude" / "projects" / "proj" / "sess.jsonl"
        inside.write_text(json.dumps(_cc_line(_cc_tool("Agent"))) + "\n", encoding="utf-8")
        outside = self.tmp / "podrobiony.jsonl"
        outside.write_text(json.dumps(_cc_line(_cc_tool("Agent"))) + "\n", encoding="utf-8")
        link = home / ".claude" / "projects" / "proj" / "link.jsonl"
        os.symlink(outside, link)  # realpath poza dozwolonym katalogiem musi być odrzucony
        keys = ("HOME", "LAB_TRANSCRIPT_PATH", "CLAUDE_CODE_SESSION_ID", "ANTIGRAVITY_CONVERSATION_ID")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["HOME"] = str(home)
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)
            os.environ["LAB_TRANSCRIPT_PATH"] = str(outside)
            self.assertIsNone(find_session_transcript())
            os.environ["LAB_TRANSCRIPT_PATH"] = str(link)
            self.assertIsNone(find_session_transcript())
            self.assertEqual(compliance_main(["--transcript-path", str(outside)]), 2)
            os.environ["LAB_TRANSCRIPT_PATH"] = str(inside)
            self.assertEqual(find_session_transcript(), inside.resolve())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_claude_code_transcript_resolved_from_session_id_only(self):
        from lab_core.compliance import claude_code_transcript_from_env
        proj = self.tmp / "Mój projekt"
        proj.mkdir()
        home = self.tmp / "home"
        encoded = "".join(c if c.isascii() and c.isalnum() else "-" for c in str(proj.resolve()))
        target = home / ".claude" / "projects" / encoded / "sess-1.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text("{}\n", encoding="utf-8")
        old = os.environ.get("CLAUDE_CODE_SESSION_ID")
        try:
            os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-1"
            self.assertEqual(claude_code_transcript_from_env(project_dir=proj, home_dir=home), target)
            os.environ["CLAUDE_CODE_SESSION_ID"] = "../sess-1"
            self.assertIsNone(claude_code_transcript_from_env(project_dir=proj, home_dir=home))
            os.environ["CLAUDE_CODE_SESSION_ID"] = "brak"
            self.assertIsNone(claude_code_transcript_from_env(project_dir=proj, home_dir=home))
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old


class TestClaudeCodeBinding(unittest.TestCase):
    """Wiązanie destylatu z wywołaniem Agent (Claude Code) przez tool_use_id + dozwolone katalogi transkryptów."""

    def setUp(self):
        ctx_root = find_vault_root()
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        shutil.copy2(ctx_root / "lab.config.toml", self.tmp / "lab.config.toml")
        self.db_rel = ConfigManager(vault_root=ctx_root).get_room_by_role("sqlite-relational").path
        (self.tmp / self.db_rel).mkdir(parents=True)
        self.db = self.tmp / self.db_rel / "lab_master.db"
        self.tr_dir = _isolate_home(self, self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _last(self):
        conn = get_master_db_connection(self.db)
        try:
            return conn.execute("SELECT * FROM subagent_dispatches ORDER BY id DESC").fetchone()
        finally:
            conn.close()

    def _gate(self, steps, text=GOOD, sha="auto", agent="Maks", task="code", tool_id=CC_ID):
        tr = _write(self.tr_dir / "sess.jsonl", steps)
        sha = distillate_sha256(text) if sha == "auto" else sha
        return run_gate(text, agent, task, vault_root=self.tmp, transcript_path=tr,
                        subagent_id=tool_id, content_sha256=sha)

    def test_valid_binding_accepted_and_logged(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result()]), 0)
        row = self._last()
        self.assertEqual((row["status"], row["subagent_id"], row["content_sha256"]),
                         ("accepted", CC_ID, distillate_sha256(GOOD)))

    def test_valid_binding_with_block_list_content_accepted(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result(as_list=True)]), 0)

    def test_no_tool_result_rejected(self):
        self.assertEqual(self._gate([_cc_call()]), 1)
        self.assertEqual(self._last()["status"], "rejected")
        self.assertIn("0 (wymagane", self._last()["summary_excerpt"])

    def test_wrong_sha_rejected(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result()], sha="0" * 64), 1)

    def test_reconstructed_text_rejected(self):
        reconstructed = GOOD.replace("3 źródła", "5 źródeł")
        self.assertEqual(self._gate([_cc_call(), _cc_result(GOOD)], text=reconstructed), 1)
        self.assertIn("tool_result", self._last()["summary_excerpt"])

    def test_multiple_tool_results_rejected(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result(), _cc_result()]), 1)
        self.assertIn("2 (wymagane", self._last()["summary_excerpt"])

    def test_multiple_agent_calls_same_id_rejected(self):
        self.assertEqual(self._gate([_cc_call(), _cc_call(), _cc_result()]), 1)

    def test_result_for_other_tool_id_rejected(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result(tool_id="toolu_OTHER")]), 1)

    def test_error_result_rejected(self):
        self.assertEqual(self._gate([_cc_call(), _cc_result(is_error=True)]), 1)

    def test_result_before_call_rejected(self):
        self.assertEqual(self._gate([_cc_result(), _cc_call()]), 1)

    def test_non_agent_tool_use_rejected(self):
        self.assertEqual(self._gate([_cc_call(name="Bash"), _cc_result()]), 1)

    def test_agent_type_mismatch_rejected(self):
        self.assertEqual(self._gate([_cc_call(subagent_type="Klara"), _cc_result()], agent="Kinga"), 1)
        self.assertIn("niezgodny", self._last()["summary_excerpt"])

    def test_same_id_in_two_transcripts_is_ambiguous(self):
        tr1 = _write(self.tr_dir / "a.jsonl", [_cc_call(), _cc_result()])
        tr2 = _write(self.tr_dir / "b.jsonl", [_cc_call(), _cc_result()])
        ok, problems = verify_cc_distillate_binding([tr1, tr2], CC_ID, GOOD, ("Maks",))
        self.assertFalse(ok)
        self.assertTrue(problems)

    def test_empty_tool_use_id_rejected(self):
        self.assertEqual(self._gate([_cc_call(tool_id=""), _cc_result(tool_id="")], tool_id=""), 1)

    def test_harness_snapshot_duplicates_are_ignored(self):
        """Realne transkrypty CC: rekordy api-request-* powtarzają tool_use/tool_result - nie są niejednoznaczne."""
        snap_call = dict(_cc_call(), type="api-request-blob")
        snap_result = dict(_cc_result(), type="api-request-blob")
        self.assertEqual(self._gate([_cc_call(), snap_call, _cc_result(), snap_result]), 0)

    def test_async_launch_ack_is_not_a_distillate(self):
        """Asynchroniczne wywołanie Agent zwraca w tool_result tylko potwierdzenie startu - nie wiąże destylatu (fail-closed)."""
        ack = "Async agent launched successfully. (This tool result is internal metadata)"
        self.assertEqual(self._gate([_cc_call(), _cc_result(ack)], text=GOOD), 1)

    def test_run_auto_bind_extracts_tool_result(self):
        from lab_core.subagents import run_auto_bind
        tr = _write(self.tr_dir / "sess.jsonl", [_cc_call(), _cc_result(GOOD)])
        rc = run_auto_bind("Maks", "code", CC_ID, vault_root=self.tmp, transcript_path=tr)
        self.assertEqual(rc, 0)
        self.assertEqual(self._last()["content_sha256"], distillate_sha256(GOOD))


class TestTranscriptRootsGate(unittest.TestCase):
    """Ścieżki transkryptu wyłącznie z allowed_transcript_roots (~/.claude/projects, ~/.gemini/antigravity/brain)."""

    def setUp(self):
        ctx_root = find_vault_root()
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        shutil.copy2(ctx_root / "lab.config.toml", self.tmp / "lab.config.toml")
        self.db_rel = ConfigManager(vault_root=ctx_root).get_room_by_role("sqlite-relational").path
        (self.tmp / self.db_rel).mkdir(parents=True)
        self.db = self.tmp / self.db_rel / "lab_master.db"
        self.home = self.tmp / "home"
        self.proj = _isolate_home(self, self.home)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_candidate_transcripts_filters_explicit_and_env_paths(self):
        inside = self.proj / "sess.jsonl"
        inside.write_text("{}\n", encoding="utf-8")
        outside = self.tmp / "obcy.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        link = self.proj / "link.jsonl"
        os.symlink(outside, link)  # realpath poza dozwolonym rootem = odrzucenie
        self.assertEqual(candidate_transcripts(explicit=inside), [inside.resolve()])
        self.assertEqual(candidate_transcripts(explicit=outside), [])
        self.assertEqual(candidate_transcripts(explicit=link), [])
        os.environ["LAB_TRANSCRIPT_PATH"] = str(outside)
        try:
            self.assertEqual(candidate_transcripts(), [])
            os.environ["LAB_TRANSCRIPT_PATH"] = str(inside)
            self.assertEqual(candidate_transcripts(), [inside.resolve()])
        finally:
            os.environ.pop("LAB_TRANSCRIPT_PATH", None)

    def test_candidate_transcripts_rejects_brain_outside_roots(self):
        brain = self.tmp / "brain"
        logs = brain / "abc" / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        (logs / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "abc"
        try:
            self.assertEqual(candidate_transcripts(brain_dir=brain), [])
        finally:
            os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)

    def test_gate_rejects_antigravity_binding_outside_roots(self):
        steps = [_invoke(1, role="Maks"), _sys_msg(SUB_ID, GOOD, 2)]
        outside = _write(self.tmp / "poza.jsonl", steps)
        rc = run_gate(GOOD, "Maks", "code", vault_root=self.tmp, transcript_path=outside,
                      subagent_id=SUB_ID, content_sha256=distillate_sha256(GOOD))
        self.assertEqual(rc, 1)
        inside = _write(self.proj / "ok.jsonl", steps)
        rc_ok = run_gate(GOOD, "Maks", "code", vault_root=self.tmp, transcript_path=inside,
                         subagent_id=SUB_ID, content_sha256=distillate_sha256(GOOD))
        self.assertEqual(rc_ok, 0)

    def test_checkpoint_rejects_explicit_transcript_outside_roots(self):
        from checkpoint import ComplianceAnnotationRequired, SessionCheckpoint
        handoff = "Weryfikacja odrzucenia transkryptu spoza katalogów dozwolonych"
        clean = [_call(1, "read_file"), _invoke(2)]
        outside = _write(self.tmp / "poza.jsonl", clean)
        inside = _write(self.proj / "ok.jsonl", clean)
        (self.tmp / ConfigManager(vault_root=self.tmp).get_room_by_role("governance-and-engine").path
         / "Dzienniki Sesji").mkdir(parents=True, exist_ok=True)
        (self.tmp / ConfigManager(vault_root=self.tmp).get_room_by_role("daily-workbench").path).mkdir(parents=True, exist_ok=True)
        cp = SessionCheckpoint(vault_root=self.tmp)
        with self.assertRaises(ComplianceAnnotationRequired) as ctx:
            cp.run_checkpoint(dry_run=True, transcript_path=outside, handoff=handoff)
        self.assertIn("odrzucony", " ".join(ctx.exception.report["violations"]))
        receipt = cp.run_checkpoint(dry_run=True, transcript_path=outside, violation_note="test odrzucenia", handoff=handoff)
        self.assertEqual(receipt.compliance["status"], "violation")
        receipt_ok = cp.run_checkpoint(dry_run=True, transcript_path=inside, handoff=handoff)
        self.assertEqual(receipt_ok.compliance["status"], "ok")


class TestSubagentOptimizationsAndCompactCheckpoint(unittest.TestCase):
    def setUp(self):
        ctx_root = find_vault_root()
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy2(ctx_root / "lab.config.toml", self.tmp / "lab.config.toml")
        self.db_rel = ConfigManager(vault_root=ctx_root).get_room_by_role("sqlite-relational").path
        (self.tmp / self.db_rel).mkdir(parents=True)
        self.db = self.tmp / self.db_rel / "lab_master.db"
        self.tr_dir = _isolate_home(self, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_check_only_valid_and_invalid_without_db_or_transcript(self):
        import contextlib
        import io
        from lab_core.subagents import main as subagents_main

        dist_file = self.tmp / "ok.md"
        dist_file.write_text(GOOD, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = subagents_main(["--check-only", "--file", str(dist_file)])
        self.assertEqual(rc, 0)
        self.assertIn("CHECK-ONLY: OK", buf.getvalue())
        self.assertIn("tok", buf.getvalue())
        self.assertFalse(self.db.exists())

        bad_file = self.tmp / "bad.md"
        bad_file.write_text("brak sekcji", encoding="utf-8")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc_bad = subagents_main(["--check-only", "--file", str(bad_file)])
        self.assertEqual(rc_bad, 1)
        self.assertIn("CHECK-ONLY: REJECTED", buf2.getvalue())

    def test_auto_bind_picks_latest_message_and_logs_accepted(self):
        from lab_core.subagents import run_auto_bind

        older = "Fakty: starsza.\nWnioski: stare.\nRekomendacje: brak."
        newer = GOOD
        tr_dir = self.tr_dir
        tr_full = _write(
            tr_dir / "transcript_full.jsonl",
            [_invoke(1, role="Maks"), _sys_msg(SUB_ID, older, 2), _sys_msg(SUB_ID, newer, 3)],
        )
        tr_short = _write(tr_dir / "transcript.jsonl", [])

        rc = run_auto_bind("Maks", "code", SUB_ID, vault_root=self.tmp, transcript_path=tr_short)
        self.assertEqual(rc, 0)
        conn = get_master_db_connection(self.db)
        try:
            row = conn.execute("SELECT * FROM subagent_dispatches ORDER BY id DESC").fetchone()
            self.assertEqual(row["status"], "accepted")
            self.assertEqual(row["subagent_id"], SUB_ID)
            self.assertEqual(row["content_sha256"], distillate_sha256(newer))
        finally:
            conn.close()
        self.assertTrue(tr_full.exists())

    def test_auto_bind_missing_message_fails_closed(self):
        from lab_core.subagents import run_auto_bind

        tr = _write(self.tr_dir / "t.jsonl", [_invoke(1, role="Maks")])
        rc = run_auto_bind("Maks", "code", SUB_ID, vault_root=self.tmp, transcript_path=tr)
        self.assertEqual(rc, 1)
        conn = get_master_db_connection(self.db)
        try:
            row = conn.execute("SELECT * FROM subagent_dispatches ORDER BY id DESC").fetchone()
            self.assertEqual(row["status"], "rejected")
        finally:
            conn.close()

    def test_checkpoint_compact_json_and_full_files_flag(self):
        from checkpoint import SessionReceipt, format_receipt_json_dict

        files = [f"03 Projekty/p_{i}.md" for i in range(15)]
        receipt = SessionReceipt(
            receipt_id="REC-1",
            session_id="2026-11-15-10-00_adam_checkpoint",
            timestamp="2026-11-15T10:00:00+01:00",
            coordinator="Adam",
            session_log_path="08 Zespół AI/Dzienniki Sesji/2026/11/log.md",
            files_touched_count=15,
            files_touched=files,
            vault_status="ok",
            signature="deadbeef12345678",
        )
        compact = format_receipt_json_dict(receipt, full_files=False)
        self.assertEqual(compact["files_touched_count"], 15)
        self.assertEqual(len(compact["files_touched"]), 10)
        self.assertTrue(compact.get("files_touched_truncated"))

        full = format_receipt_json_dict(receipt, full_files=True)
        self.assertEqual(full["files_touched_count"], 15)
        self.assertEqual(len(full["files_touched"]), 15)
        self.assertNotIn("files_touched_truncated", full)


if __name__ == "__main__":
    unittest.main()
