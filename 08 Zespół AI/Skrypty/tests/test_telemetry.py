#!/usr/bin/env python3
"""
test_telemetry.py - Testy jednostkowe dla Wariantu B telemetrii sesji i raportowania.
Standard: 100% Python Standard Library (unittest, tempfile, sqlite3, json, pathlib).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

_scripts_dir = Path(__file__).resolve().parent.parent
_vault_root = _scripts_dir.parent.parent.resolve()
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
if str(_vault_root) not in sys.path:
    sys.path.insert(0, str(_vault_root))

from lab_core.schema import ensure_telemetry_columns, get_master_db_connection, init_master_db
from lab_core.telemetry import (
    TelemetryDataError,
    collect_related_subagents,
    generate_telemetry_report,
    main as telemetry_main,
    save_session_telemetry,
    summarize_session_telemetry,
)


def _write_transcript(brain_dir: Path, cid: str, steps: list[dict], full_steps: list[dict] | None = None) -> Path:
    logs_dir = brain_dir / cid / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    t_path = logs_dir / "transcript.jsonl"
    t_path.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
    if full_steps is not None:
        full_path = logs_dir / "transcript_full.jsonl"
        full_path.write_text("\n".join(json.dumps(s) for s in full_steps) + "\n", encoding="utf-8")
    return t_path


class TestSessionSummaryAndCycles(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.brain = self.root / "brain"
        self.brain.mkdir()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_summarize_session_with_nested_subagents_and_cycle_guard(self) -> None:
        main_cid = "11111111-1111-1111-1111-111111111111"
        sub1_cid = "22222222-2222-2222-2222-222222222222"
        sub2_cid = "33333333-3333-3333-3333-333333333333"

        main_steps = [
            {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-11-16T10:00:00Z", "content": "Start"},
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-16T10:00:05Z",
                "input_tokens": 1000,
                "cache_read_tokens": 2000,
                "output_tokens": 100,
                "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "research"}}],
            },
            {
                "step_index": 2,
                "type": "GENERIC",
                "created_at": "2026-11-16T10:00:06Z",
                "content": f'{{"conversationId": "{sub1_cid}"}}',
            },
            {
                "step_index": 3,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-16T10:01:00Z",
                "input_tokens": 500,
                "cache_read_tokens": 3500,
                "output_tokens": 250,
            },
        ]

        # Subagent 1 zawiera <subagent_reminder> z ID rodzica oraz odniesienie zwrotne (cykl) i wywołuje sub2
        sub1_steps = [
            {
                "step_index": 0,
                "type": "SYSTEM_MESSAGE",
                "created_at": "2026-11-16T10:00:07Z",
                "content": f'<subagent_reminder>caller (name: "parent", id: "{main_cid}")</subagent_reminder>',
            },
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-16T10:00:08Z",
                "input_tokens": 400,
                "cache_read_tokens": 600,
                "output_tokens": 80,
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"TypeName": "research"}},
                    {"name": "send_message", "args": {"Recipient": main_cid, "Message": "cycle back"}},
                ],
            },
            {
                "step_index": 2,
                "type": "GENERIC",
                "created_at": "2026-11-16T10:00:09Z",
                "content": f"conversationId: {sub2_cid}",
            },
        ]

        # Subagent 2 próbuje wskazać na sub1 (cykl)
        sub2_steps = [
            {
                "step_index": 0,
                "type": "SYSTEM_MESSAGE",
                "created_at": "2026-11-16T10:00:10Z",
                "content": f'<subagent_reminder>caller (name: "parent", id: "{sub1_cid}")</subagent_reminder>',
            },
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-16T10:00:11Z",
                "input_tokens": 300,
                "cache_read_tokens": 100,
                "output_tokens": 40,
                "tool_calls": [{"name": "send_message", "args": {"Recipient": sub1_cid, "Message": "done"}}],
            },
        ]

        main_path = _write_transcript(self.brain, main_cid, main_steps)
        _write_transcript(self.brain, sub1_cid, sub1_steps)
        _write_transcript(self.brain, sub2_cid, sub2_steps)

        related = collect_related_subagents(main_path, brain_dir=self.brain)
        self.assertEqual([cid for cid, _ in related], [sub1_cid, sub2_cid])

        summary = summarize_session_telemetry(main_path, brain_dir=self.brain)
        self.assertEqual(summary.session_id, main_cid)
        self.assertEqual(summary.session_date, "2026-11-16")
        self.assertEqual(summary.subagents_count, 2)

        # Main: in=1500, cache=5500, out=350, calls=2, peak=4000
        self.assertEqual(summary.main.input_tokens, 1500)
        self.assertEqual(summary.main.cache_read_tokens, 5500)
        self.assertEqual(summary.main.output_tokens, 350)
        self.assertEqual(summary.main.planner_calls, 2)
        self.assertEqual(summary.main.peak_context_tokens, 4000)

        # Subagents: in=700, cache=700, out=120, calls=2, peak=1000
        self.assertEqual(summary.subagents.input_tokens, 700)
        self.assertEqual(summary.subagents.cache_read_tokens, 700)
        self.assertEqual(summary.subagents.output_tokens, 120)
        self.assertEqual(summary.subagents.planner_calls, 2)
        self.assertEqual(summary.subagents.peak_context_tokens, 1000)

        # Total: in=2200, cache=6200, out=470, calls=4, peak=4000
        self.assertEqual(summary.total.input_tokens, 2200)
        self.assertEqual(summary.total.cache_read_tokens, 6200)
        self.assertEqual(summary.total.output_tokens, 470)
        self.assertEqual(summary.total.planner_calls, 4)
        self.assertEqual(summary.total.peak_context_tokens, 4000)

    def test_cli_session_summary_json(self) -> None:
        cid = "aaaa1111-1111-1111-1111-111111111111"
        t_path = _write_transcript(
            self.brain,
            cid,
            [{"step_index": 1, "created_at": "2026-11-16T12:00:00Z", "input_tokens": 200, "cache_read_tokens": 800, "output_tokens": 50}],
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = telemetry_main(["--session-summary", "--transcript-path", str(t_path), "--brain-dir", str(self.brain), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["session_id"], cid)
        self.assertEqual(payload["main"]["output_tokens"], 50)
        self.assertEqual(payload["total"]["planner_calls"], 1)


class TestDatabaseUpsertAndReport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.brain = self.root / "brain"
        self.brain.mkdir()
        self.db_path = self.root / "lab_master.db"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_legacy_table_migration_and_idempotent_upsert(self) -> None:
        # Stara tabela session_telemetry bez kolumn subagentów
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """
            CREATE TABLE session_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                session_number INTEGER DEFAULT 0,
                tokens_active INTEGER NOT NULL,
                tokens_limit INTEGER NOT NULL,
                load_zone TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        cid = "bbbb2222-2222-2222-2222-222222222222"
        t_path = _write_transcript(
            self.brain,
            cid,
            [{"step_index": 1, "created_at": "2026-11-15T09:00:00Z", "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 100}],
        )
        summary1 = summarize_session_telemetry(t_path, brain_dir=self.brain)
        id1 = save_session_telemetry(self.db_path, summary1)

        # Drugi zapis dla tego samego session_id (zaktualizowany transkrypt) nie duplikuje wiersza
        t_path = _write_transcript(
            self.brain,
            cid,
            [
                {"step_index": 1, "created_at": "2026-11-15T09:00:00Z", "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 100},
                {"step_index": 2, "created_at": "2026-11-15T09:05:00Z", "input_tokens": 200, "cache_read_tokens": 1500, "output_tokens": 50},
            ],
        )
        summary2 = summarize_session_telemetry(t_path, brain_dir=self.brain)
        id2 = save_session_telemetry(self.db_path, summary2)

        self.assertEqual(id1, id2)
        c = get_master_db_connection(self.db_path)
        try:
            ensure_telemetry_columns(c)
            rows = c.execute("SELECT * FROM session_telemetry WHERE session_id = ?", (cid,)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["input_tokens"], 1200)
            self.assertEqual(rows[0]["output_tokens"], 150)
            self.assertEqual(rows[0]["planner_calls"], 2)
        finally:
            c.close()

    def test_report_merges_brain_and_historical_db_without_duplicates(self) -> None:
        init_master_db(self.db_path)

        # Sesja obecna w brain i jednocześnie zapisana w DB -> nie powinna być liczona podwójnie
        live_cid = "cccc3333-3333-3333-3333-333333333333"
        live_path = _write_transcript(
            self.brain,
            live_cid,
            [{"step_index": 1, "created_at": "2026-11-16T11:00:00Z", "input_tokens": 500, "cache_read_tokens": 500, "output_tokens": 50}],
        )
        live_summary = summarize_session_telemetry(live_path, brain_dir=self.brain)
        save_session_telemetry(self.db_path, live_summary)

        # Sesja historyczna tylko w DB (brak w brain/)
        hist_cid = "dddd4444-4444-4444-4444-444444444444"
        hist_path = _write_transcript(
            self.root / "archived_brain",
            hist_cid,
            [{"step_index": 1, "created_at": "2026-11-14T11:00:00Z", "input_tokens": 800, "cache_read_tokens": 200, "output_tokens": 90}],
        )
        hist_summary = summarize_session_telemetry(hist_path, brain_dir=self.root / "archived_brain")
        save_session_telemetry(self.db_path, hist_summary)

        report = generate_telemetry_report(
            brain_dir=self.brain,
            db_path=self.db_path,
            days=7,
            reference_date=date(2026, 11, 16),
        )
        self.assertEqual(report.sessions_count, 2)
        self.assertEqual(len(report.days), 2)
        self.assertEqual(report.main.input_tokens, 1300)
        self.assertEqual(report.main.output_tokens, 140)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = telemetry_main([
                "--report",
                "--brain-dir", str(self.brain),
                "--db-path", str(self.db_path),
                "--from", "2026-11-14",
                "--to", "2026-11-16",
                "--json",
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["sessions_count"], 2)
        self.assertEqual(data["total"]["output_tokens"], 140)

    def test_report_invalid_date_raises(self) -> None:
        with self.assertRaises(TelemetryDataError):
            generate_telemetry_report(brain_dir=self.brain, db_path=self.db_path, date_from="2026-11-20", date_to="2026-11-10")


class TestCheckpointIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp_dir.name)
        # HOME izolowany w tmp: checkpoint przyjmuje tylko transkrypty z ~/.gemini/antigravity/brain lub ~/.claude/projects.
        from unittest.mock import patch as _patch
        _home_patch = _patch.dict(os.environ, {"HOME": str(self.vault)})
        _home_patch.start()
        self.addCleanup(_home_patch.stop)
        for _key in ("LAB_TRANSCRIPT_PATH", "CLAUDE_CODE_SESSION_ID", "ANTIGRAVITY_CONVERSATION_ID"):
            os.environ.pop(_key, None)
        (self.vault / "02 Warsztat").mkdir()
        (self.vault / "07 Bazy Danych").mkdir()
        (self.vault / "08 Zespół AI" / "Dzienniki Sesji").mkdir(parents=True)
        (self.vault / "lab.config.toml").write_text(
            (_vault_root / "lab.config.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.db_path = self.vault / "07 Bazy Danych" / "lab_master.db"
        init_master_db(self.db_path, instance_name="LAB-TEST")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_checkpoint_saves_session_telemetry_and_renders_summary(self) -> None:
        from checkpoint import SessionCheckpoint, render_terminal_receipt

        brain = self.vault / ".gemini" / "antigravity" / "brain"
        cid = "eeee5555-5555-5555-5555-555555555555"
        t_path = _write_transcript(
            brain,
            cid,
            [
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-11-16T13:00:00Z",
                    "input_tokens": 1200,
                    "cache_read_tokens": 3800,
                    "output_tokens": 150,
                    "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "research"}}],
                }
            ],
        )

        cp = SessionCheckpoint(vault_root=self.vault)
        receipt = cp.run_checkpoint(
            slug="telemetry-test",
            hours=1.0,
            dry_run=False,
            transcript_path=t_path,
            handoff="Weryfikacja telemetrii i kwitu sesji w bazie",
        )

        self.assertTrue(receipt.telemetry)
        self.assertEqual(receipt.telemetry["session_id"], cid)
        self.assertEqual(receipt.telemetry["total"]["output_tokens"], 150)

        rendered = render_terminal_receipt(receipt)
        self.assertIn("TELEMETRIA TOKENÓW SESJI:", rendered)
        self.assertIn("Sesja główna", rendered)
        self.assertIn("Łącznie", rendered)

        conn = get_master_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM session_telemetry WHERE session_id = ?", (cid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["input_tokens"], 1200)
            self.assertEqual(row["output_tokens"], 150)
        finally:
            conn.close()

    def test_antigravity_missing_or_stale_transcripts_raises_not_found_without_cross_platform_leak(self) -> None:
        from lab_core.telemetry import TranscriptNotFoundError, find_latest_transcript, estimate_fallback_telemetry
        from unittest import mock

        empty_brain = self.vault / "empty_brain"
        empty_brain.mkdir(parents=True, exist_ok=True)

        fake_claude = self.vault / "fake_claude"
        fake_claude.mkdir(parents=True, exist_ok=True)
        (fake_claude / "claude_session.jsonl").write_text('{"type": "message"}\n', encoding="utf-8")

        fake_codex = self.vault / "fake_codex"
        fake_codex.mkdir(parents=True, exist_ok=True)
        (fake_codex / "rollout-2026.jsonl").write_text('{"type": "session_meta"}\n', encoding="utf-8")

        # Gdy provider to antigravity, brak transkryptów w brain musi rzucić TranscriptNotFoundError
        # i nie może zwrócić plików z Claude ani Codex
        with mock.patch("lab_core.telemetry.resolve_llm_provider", return_value="antigravity"):
            with self.assertRaises(TranscriptNotFoundError):
                find_latest_transcript(
                    brain_dir=empty_brain,
                    claude_dir=fake_claude,
                    codex_dir=fake_codex,
                    vault_root=self.vault,
                )

        # Fallback działa bezpiecznie i zwraca obiekt TokenMetrics z oszacowaniem
        fb = estimate_fallback_telemetry(vault_root=self.vault, limit=150_000)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.limit, 150_000)


if __name__ == "__main__":
    unittest.main()
