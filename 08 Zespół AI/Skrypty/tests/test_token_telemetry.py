#!/usr/bin/env python3
"""
test_token_telemetry.py - Zestaw testów jednostkowych dla token_telemetry.py (LAB 2.0)
Standard inżynieryjny: 100% Python Standard Library (unittest, tempfile, json, pathlib).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

# Blokada tworzenia bytecode pycache
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Dodanie ścieżki do sys.path
_scripts_dir = Path(__file__).resolve().parent.parent
_vault_root = _scripts_dir.parent.parent.resolve()
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
if str(_vault_root) not in sys.path:
    sys.path.insert(0, str(_vault_root))

from lab_core.telemetry import (
    DEFAULT_WINDOW_LIMIT,
    ICON_ATTENTION,
    ICON_COMFORT,
    ICON_CRITICAL,
    ZONE_ATTENTION,
    ZONE_COMFORT,
    ZONE_CRITICAL,
    TelemetryDataError,
    TokenMetrics,
    TranscriptNotFoundError,
    WorkshopUpdateError,
    calculate_zone,
    find_latest_transcript,
    format_statusline,
    format_terminal_summary,
    format_tokens,
    main,
    parse_telemetry,
    clean_workshop_telemetry,
    update_workshop_file,
)


class TestTokenCalculationsAndFormatting(unittest.TestCase):
    """Testy obliczeń, progów stref i formatowania tekstowego."""

    def test_format_tokens(self) -> None:
        self.assertEqual(format_tokens(500), "500")
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(1000), "1k")
        self.assertEqual(format_tokens(250000), "250k")
        self.assertEqual(format_tokens(109400), "109.4k")
        self.assertEqual(format_tokens(109500), "109.5k")
        self.assertEqual(format_tokens(200000), "200k")

    def test_calculate_zone_default_limit(self) -> None:
        # Komfortowa (< 200 000)
        zone, icon, display = calculate_zone(150_000)
        self.assertEqual(zone, ZONE_COMFORT)
        self.assertEqual(icon, ICON_COMFORT)
        self.assertEqual(display, "🟢 Komfortowa")

        zone, icon, display = calculate_zone(199_999)
        self.assertEqual(zone, ZONE_COMFORT)

        # Wymaga uwagi (200 000 - 240 000)
        zone, icon, display = calculate_zone(200_000)
        self.assertEqual(zone, ZONE_ATTENTION)
        self.assertEqual(icon, ICON_ATTENTION)
        self.assertEqual(display, "🟡 Wymaga uwagi")

        zone, icon, display = calculate_zone(220_000)
        self.assertEqual(zone, ZONE_ATTENTION)

        zone, icon, display = calculate_zone(240_000)
        self.assertEqual(zone, ZONE_ATTENTION)

        # Krytyczna (> 240 000)
        zone, icon, display = calculate_zone(240_001)
        self.assertEqual(zone, ZONE_CRITICAL)
        self.assertEqual(icon, ICON_CRITICAL)
        self.assertEqual(display, "🔴 Krytyczna")

        zone, icon, display = calculate_zone(250_000)
        self.assertEqual(zone, ZONE_CRITICAL)

    def test_calculate_zone_custom_limit(self) -> None:
        # Limit 100 000 -> 80% = 80 000, 96% = 96 000
        zone, icon, _ = calculate_zone(75_000, limit=100_000)
        self.assertEqual(zone, ZONE_COMFORT)
        self.assertEqual(icon, ICON_COMFORT)

        zone, icon, _ = calculate_zone(85_000, limit=100_000)
        self.assertEqual(zone, ZONE_ATTENTION)
        self.assertEqual(icon, ICON_ATTENTION)

        zone, icon, _ = calculate_zone(97_000, limit=100_000)
        self.assertEqual(zone, ZONE_CRITICAL)
        self.assertEqual(icon, ICON_CRITICAL)

    def test_format_statusline(self) -> None:
        metrics = TokenMetrics(
            input_tokens=9400,
            cache_read_tokens=100000,
            output_tokens=500,
            total_context_tokens=109400,
            limit=250000,
            percentage=43.8,
            zone=ZONE_COMFORT,
            zone_icon=ICON_COMFORT,
            zone_display="🟢 Komfortowa",
        )
        line = format_statusline(metrics)
        self.assertEqual(line, "🧠 109.4k / 250k (43.8%) 🟢")

    def test_format_terminal_summary(self) -> None:
        metrics = TokenMetrics(
            input_tokens=2500,
            cache_read_tokens=100000,
            output_tokens=300,
            total_context_tokens=102500,
            limit=250000,
            percentage=41.0,
            zone=ZONE_COMFORT,
            zone_icon=ICON_COMFORT,
            zone_display="🟢 Komfortowa",
            step_index=42,
            transcript_path="/path/test/transcript.jsonl",
        )
        summary = format_terminal_summary(metrics)
        self.assertIn("102,500", summary)
        self.assertIn("250,000", summary)
        self.assertIn("41.0%", summary)
        self.assertIn("🟢 Komfortowa", summary)
        self.assertIn("🧠 102.5k / 250k (41.0%) 🟢", summary)


class TestParseTelemetry(unittest.TestCase):
    """Testy parsowania logów transkryptu JSONL."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_parse_telemetry_happy_path(self) -> None:
        transcript_file = self.dir_path / "transcript.jsonl"
        lines = [
            json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "Cześć"}),
            json.dumps({
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "input_tokens": 5000,
                "cache_read_tokens": 10000,
                "output_tokens": 120,
            }),
            json.dumps({"step_index": 3, "type": "TOOL_CALL", "content": "ls"}),
            json.dumps({
                "step_index": 4,
                "type": "PLANNER_RESPONSE",
                "input_tokens": 3000,
                "cache_read_tokens": 105000,
                "output_tokens": 450,
            }),
        ]
        transcript_file.write_text("\n".join(lines), encoding="utf-8")

        metrics = parse_telemetry(transcript_file)
        self.assertEqual(metrics.step_index, 4)
        self.assertEqual(metrics.input_tokens, 3000)
        self.assertEqual(metrics.cache_read_tokens, 105000)
        self.assertEqual(metrics.output_tokens, 450)
        self.assertEqual(metrics.total_context_tokens, 108000)
        self.assertEqual(metrics.percentage, 43.2)
        self.assertEqual(metrics.zone, ZONE_COMFORT)

    def test_parse_telemetry_missing_file_raises_not_found(self) -> None:
        missing_file = self.dir_path / "non_existent.jsonl"
        with self.assertRaises(TranscriptNotFoundError):
            parse_telemetry(missing_file)

    def test_parse_telemetry_empty_file_raises_data_error(self) -> None:
        empty_file = self.dir_path / "empty.jsonl"
        empty_file.write_text("", encoding="utf-8")
        with self.assertRaises(TelemetryDataError):
            parse_telemetry(empty_file)

    def test_parse_telemetry_no_tokens_raises_data_error(self) -> None:
        no_tokens_file = self.dir_path / "no_tokens.jsonl"
        lines = [
            json.dumps({"step_index": 1, "content": "Pytanie użytkownika"}),
            json.dumps({"step_index": 2, "content": "Odpowiedź bez danych o tokenach"}),
        ]
        no_tokens_file.write_text("\n".join(lines), encoding="utf-8")
        with self.assertRaises(TelemetryDataError):
            parse_telemetry(no_tokens_file)

    def test_parse_telemetry_resilient_to_corrupted_lines(self) -> None:
        file_path = self.dir_path / "corrupted.jsonl"
        content = (
            "not a valid json line 1\n"
            + json.dumps({"step_index": 10, "input_tokens": 2000, "cache_read_tokens": 210000, "output_tokens": 50})
            + "\n{broken json\n"
        )
        file_path.write_text(content, encoding="utf-8")

        metrics = parse_telemetry(file_path)
        self.assertEqual(metrics.step_index, 10)
        self.assertEqual(metrics.total_context_tokens, 212000)
        self.assertEqual(metrics.zone, ZONE_ATTENTION)


class TestFindLatestTranscript(unittest.TestCase):
    """Testy automatycznego wykrywania aktywnego pliku transkryptu."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_find_latest_transcript_selects_newest_mtime(self) -> None:
        # Tworzymy dwie sesje
        sess_old = self.base_dir / "old-session-uuid" / ".system_generated" / "logs"
        sess_old.mkdir(parents=True, exist_ok=True)
        old_file = sess_old / "transcript.jsonl"
        old_file.write_text('{"input_tokens": 100}\n', encoding="utf-8")

        # Ustaw mtime w przeszłości
        old_time = time.time() - 3600
        os.utime(old_file, (old_time, old_time))

        sess_new = self.base_dir / "new-session-uuid" / ".system_generated" / "logs"
        sess_new.mkdir(parents=True, exist_ok=True)
        new_file = sess_new / "transcript.jsonl"
        new_file.write_text('{"input_tokens": 200}\n', encoding="utf-8")

        new_time = time.time()
        os.utime(new_file, (new_time, new_time))

        found = find_latest_transcript(brain_dir=self.base_dir)
        self.assertEqual(found.resolve(), new_file.resolve())

    def test_find_latest_transcript_empty_raises_error(self) -> None:
        with self.assertRaises(TranscriptNotFoundError):
            find_latest_transcript(brain_dir=self.base_dir)

    def test_find_latest_transcript_missing_dir_raises_error(self) -> None:
        missing = self.base_dir / "non_existing_dir"
        with self.assertRaises(TranscriptNotFoundError):
            find_latest_transcript(brain_dir=missing)


class TestWorkshopUpdate(unittest.TestCase):
    """Testy ochrony i oczyszczania pliku Warsztatu z telemetrii."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workshop_file = Path(self.tmp_dir.name) / "Dashboard LAB.md"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_clean_workshop_telemetry_removes_existing_lines(self) -> None:
        initial_content = (
            "---\ntype: cockpit-dashboard\n---\n\n"
            "# 🛠️ Dashboard LAB (LAB)\n\n"
            "> **Pulpit operacyjny, dynamiczny radar zadań i stół warsztatowy (Adam ↔ Użytkownik)**  \n"
            "> *Środowisko ma pracować dla człowieka, a nie człowiek dla środowiska.*  \n"
            "> \n"
            "> 🧠 **Telemetria Pamięci Sesji (Sesja 5):** `109.4k / 250k tokenów (43.8%)` • Strefa: 🟢 Komfortowa • [[RS-002.02 Zarządzanie Oknem Kontekstowym i Kompaktacja|Więcej o oknie kontekstowym ↗]]\n\n"
            "---\n"
        )
        self.workshop_file.write_text(initial_content, encoding="utf-8")

        res = clean_workshop_telemetry(self.workshop_file)
        self.assertTrue(res)

        updated_text = self.workshop_file.read_text(encoding="utf-8")
        self.assertNotIn("Telemetria Pamięci Sesji", updated_text)
        self.assertNotIn("109.4k", updated_text)
        self.assertIn("# 🛠️ Dashboard LAB (LAB)", updated_text)
        self.assertIn("Środowisko ma pracować dla człowieka", updated_text)

    def test_clean_workshop_telemetry_on_clean_file_returns_false(self) -> None:
        initial_content = (
            "# Warsztat\n"
            "> *Cytat*\n\n"
            "## 🎯 Na tapecie\n"
        )
        self.workshop_file.write_text(initial_content, encoding="utf-8")

        res = clean_workshop_telemetry(self.workshop_file)
        self.assertFalse(res)
        self.assertEqual(self.workshop_file.read_text(encoding="utf-8"), initial_content)

    def test_update_workshop_file_cleans_instead_of_writing(self) -> None:
        initial_content = (
            "# Warsztat\n"
            "> 🧠 **Telemetria Pamięci Sesji (Sesja 5):** `100k / 250k tokenów (40.0%)` • Strefa: 🟢 Komfortowa • [[RS-002.02|Link]]\n\n"
            "---"
        )
        self.workshop_file.write_text(initial_content, encoding="utf-8")

        res = update_workshop_file(self.workshop_file)
        self.assertFalse(res)
        updated_text = self.workshop_file.read_text(encoding="utf-8")
        self.assertNotIn("Telemetria Pamięci Sesji", updated_text)

    def test_clean_workshop_missing_file_raises_error(self) -> None:
        missing = Path("/non/existent/path/Warsztat.md")
        with self.assertRaises(WorkshopUpdateError):
            clean_workshop_telemetry(missing)


class TestCLIIntegration(unittest.TestCase):
    """Testy integracyjne CLI dla różnych trybów wyjścia."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.tmp_dir.name)
        self.transcript_file = self.dir_path / "transcript.jsonl"
        self.workshop_file = self.dir_path / "Dashboard LAB.md"

        self.transcript_file.write_text(
            json.dumps({
                "step_index": 50,
                "input_tokens": 4000,
                "cache_read_tokens": 120000,
                "output_tokens": 800,
            }) + "\n",
            encoding="utf-8",
        )
        self.workshop_file.write_text(
            "> 🧠 **Telemetria Pamięci Sesji (Sesja 5):** `100k / 250k tokenów (40.0%)` • Strefa: 🟢 Komfortowa • [[RS-002.02|Link]]\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_cli_statusline(self) -> None:
        stdout_buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_buf
        try:
            exit_code = main(["--transcript-path", str(self.transcript_file), "--statusline"])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        output = stdout_buf.getvalue().strip()
        self.assertEqual(output, "🧠 124k / 250k (49.6%) 🟢")

    def test_cli_json(self) -> None:
        stdout_buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_buf
        try:
            exit_code = main(["--transcript-path", str(self.transcript_file), "--json"])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout_buf.getvalue().strip())
        self.assertEqual(payload["input_tokens"], 4000)
        self.assertEqual(payload["cache_read_tokens"], 120000)
        self.assertEqual(payload["total_context_tokens"], 124000)
        self.assertEqual(payload["percentage"], 49.6)
        self.assertEqual(payload["zone"], ZONE_COMFORT)

    def test_cli_clean_and_update_workshop_cleans_file(self) -> None:
        stdout_buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_buf
        try:
            exit_code = main([
                "--transcript-path", str(self.transcript_file),
                "--workshop-path", str(self.workshop_file),
                "--clean-workshop",
            ])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        workshop_text = self.workshop_file.read_text(encoding="utf-8")
        self.assertNotIn("Telemetria Pamięci Sesji", workshop_text)

    def test_cli_deprecated_update_workshop_flag_also_cleans_and_warns(self) -> None:
        stdout_buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_buf
        try:
            exit_code = main([
                "--transcript-path", str(self.transcript_file),
                "--workshop-path", str(self.workshop_file),
                "--update-workshop",
            ])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        workshop_text = self.workshop_file.read_text(encoding="utf-8")
        self.assertNotIn("Telemetria Pamięci Sesji", workshop_text)
        self.assertIn("wycofana", stdout_buf.getvalue())

    def test_cli_error_returns_code_1(self) -> None:
        stderr_buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr_buf
        try:
            exit_code = main(["--transcript-path", "/non/existent/path.jsonl"])
        finally:
            sys.stderr = old_stderr

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
