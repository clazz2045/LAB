"""test_diagnostics.py - Zestaw testów jednostkowych dla modułu lab_core.diagnostics
Standard inżynieryjny: 100% Python Standard Library (unittest, unittest.mock).
Testy weryfikują ścieżki pozytywne i negatywne diagnostyki środowiskowej.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.dont_write_bytecode = True

# Bootstrap sys.path
_scripts_dir = Path(__file__).resolve().parent.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core.diagnostics import (
    DiagnosticItem,
    PreflightReport,
    check_git,
    check_icloud_storage,
    check_obsidian,
    check_python,
    check_raycast,
    check_wispr_flow,
    clear_macos_quarantine,
    run_preflight_diagnostics,
    scan_icloud_hazards,
    scan_icloud_placeholders,
)


class TestDiagnosticsPython(unittest.TestCase):
    """Testy weryfikacji interpretera Pythona."""

    def test_check_python_current_environment_passes(self):
        item = check_python(min_version=(3, 11))
        self.assertEqual(item.name, "Python Interpreter")
        self.assertTrue(item.is_critical)
        self.assertTrue(item.passed)
        self.assertIsNotNone(item.version)
        self.assertIsNone(item.remedy)

    def test_check_python_fails_for_older_version(self):
        with patch("sys.version_info", (3, 10, 8, "final", 0)):
            item = check_python(min_version=(3, 11))
            self.assertFalse(item.passed)
            self.assertTrue(item.is_critical)
            self.assertIn("Wymagany Python >= 3.11", item.details)
            self.assertIsNotNone(item.remedy)


class TestDiagnosticsObsidian(unittest.TestCase):
    """Testy weryfikacji aplikacji Obsidian."""

    def test_check_obsidian_finds_application_or_reports_path(self):
        item = check_obsidian()
        self.assertEqual(item.name, "Obsidian App")
        self.assertTrue(item.is_critical)
        # Na maszynie deweloperskiej Obsidian istnieje w /Applications/Obsidian.app
        if item.passed:
            self.assertIsNotNone(item.path)
            self.assertIsNone(item.remedy)

    def test_check_obsidian_negative_path_missing(self):
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=False):
            item = check_obsidian()
            self.assertFalse(item.passed)
            self.assertIsNone(item.path)
            self.assertIsNotNone(item.remedy)
            self.assertIn("obsidian.md", item.remedy)


class TestDiagnosticsGit(unittest.TestCase):
    """Testy weryfikacji narzędzia Git."""

    def test_check_git_positive_on_system(self):
        item = check_git()
        self.assertEqual(item.name, "Git VCS")
        self.assertFalse(item.is_critical)
        self.assertTrue(item.passed)
        self.assertIsNotNone(item.version)
        self.assertIn("git", item.version.lower())

    def test_check_git_missing_binary(self):
        with patch("shutil.which", return_value=None):
            item = check_git()
            self.assertFalse(item.passed)
            self.assertIn("nie zostało odnalezione", item.details)
            self.assertIsNotNone(item.remedy)

    def test_check_git_command_failure(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 127
        mock_proc.stderr = "git: command not found"
        with patch("shutil.which", return_value="/usr/bin/git"), \
             patch("subprocess.run", return_value=mock_proc):
            item = check_git()
            self.assertFalse(item.passed)
            self.assertIn("błąd wykonania", item.details)


class TestDiagnosticsOptionalTools(unittest.TestCase):
    """Testy weryfikacji narzędzi opcjonalnych (Raycast, Wispr Flow)."""

    def test_check_raycast_is_optional(self):
        item = check_raycast()
        self.assertEqual(item.name, "Raycast")
        self.assertFalse(item.is_critical)
        self.assertEqual(item.category, "optional")

    def test_check_wispr_flow_is_optional(self):
        item = check_wispr_flow()
        self.assertEqual(item.name, "Wispr Flow")
        self.assertFalse(item.is_critical)
        self.assertEqual(item.category, "optional")


class TestPreflightReport(unittest.TestCase):
    """Testy całościowego raportu PreflightReport."""

    def test_report_all_critical_passed(self):
        items = [
            DiagnosticItem("Py", "critical", True),
            DiagnosticItem("Obsidian", "critical", True),
            DiagnosticItem("Git", "critical", True),
            DiagnosticItem("Raycast", "optional", False),  # Opcjonalny nie blokuje
        ]
        rep = PreflightReport(os_info="Darwin", python_version="3.11", items=items)
        self.assertTrue(rep.all_critical_passed)
        self.assertEqual(rep.passed_count, 3)
        self.assertEqual(rep.total_count, 4)

    def test_report_fails_when_critical_item_fails(self):
        items = [
            DiagnosticItem("Py", "critical", True),
            DiagnosticItem("Obsidian", "critical", False),
            DiagnosticItem("Git", "critical", True),
            DiagnosticItem("Raycast", "optional", True),
        ]
        rep = PreflightReport(os_info="Darwin", python_version="3.11", items=items)
        self.assertFalse(rep.all_critical_passed)

    def test_format_cli_output(self):
        rep = run_preflight_diagnostics()
        cli_text = rep.format_cli()
        self.assertIn("LAB 3.0 - PRE-FLIGHT SYSTEM DIAGNOSTICS", cli_text)
        self.assertIn("Python Interpreter", cli_text)
        self.assertIn("Obsidian App", cli_text)
        self.assertIn("Git VCS", cli_text)
        self.assertTrue("Status: GOTOWY" in cli_text or "Status: BLOKADA" in cli_text)


class TestDiagnosticsIcloudAndGatekeeper(unittest.TestCase):
    """Testy skanera iCloud (.icloud, .venv, node_modules) oraz Gatekeepera."""

    def test_scan_icloud_hazards_and_placeholders(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            (root / ".venv").mkdir()
            (root / "node_modules").mkdir()
            (root / "test.md.icloud").write_text("x", encoding="utf-8")

            hazards = scan_icloud_hazards(root)
            self.assertIn(".git", hazards["forbidden_dirs"])
            self.assertIn(".venv", hazards["forbidden_dirs"])
            self.assertIn("node_modules", hazards["forbidden_dirs"])
            self.assertEqual(len(hazards["icloud_placeholders"]), 1)
            self.assertEqual(len(scan_icloud_placeholders(root)), 1)

            with patch("platform.system", return_value="Darwin"):
                item = check_icloud_storage(root)
                self.assertFalse(item.passed)
                self.assertIn(".icloud", item.details)

    def test_clear_macos_quarantine_nonexistent_returns_false(self):
        self.assertFalse(clear_macos_quarantine(Path("/nonexistent/file.command")))


if __name__ == "__main__":
    unittest.main()
