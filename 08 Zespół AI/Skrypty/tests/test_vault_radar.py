"""test_vault_radar.py - Testy jednostkowe dla Radar Kondycji i Balansu Zasobów (lab_core.radar)
Standard: 100% Python Standard Library (unittest). Zero-Pip.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from lab_core import ConfigManager, VaultRadar, find_vault_root


class TestVaultRadar(unittest.TestCase):
    """Weryfikuje logikę detekcji kandydatów do odłączenia, uśpienia i zatorów lektur."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_radar_"))
        self.real_vault = find_vault_root(_script_dir)
        # Przygotuj strukturę 8 pokoi
        (self.tmp_dir / "01 Skrzynka").mkdir(parents=True)
        (self.tmp_dir / "03 Projekty").mkdir(parents=True)
        (self.tmp_dir / "06 Zasoby" / "Notatki").mkdir(parents=True)
        (self.tmp_dir / "07 Bazy Danych").mkdir(parents=True)
        (self.tmp_dir / "08 Zespół AI").mkdir(parents=True)

        # Skopiuj poprawną konfigurację bazową
        shutil.copy(self.real_vault / "lab.config.toml", self.tmp_dir / "lab.config.toml")
        self.config = ConfigManager(config_path=self.tmp_dir / "lab.config.toml", vault_root=self.tmp_dir)
        self.now = datetime.datetime(2026, 11, 8, 12, 0, 0)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_eject_candidate_detection(self) -> None:
        """Projekt z kodem, bazą lub dużą liczbą plików powinien zostać oznaczony jako eject_candidate."""
        proj_dir = self.tmp_dir / "03 Projekty" / "PR-100 Aplikacja Testowa"
        proj_dir.mkdir(parents=True)
        (proj_dir / "PR-100 Aplikacja Testowa.md").write_text("---\nstatus: active\n---\n# PR-100", encoding="utf-8")
        (proj_dir / "script.py").write_text("print('hello')", encoding="utf-8")
        (proj_dir / "database.db").write_text("sqlite dummy data", encoding="utf-8")

        radar = VaultRadar(vault_root=self.tmp_dir, config=self.config)
        report = radar.scan(now=self.now)

        self.assertEqual(len(report.eject_candidates), 1)
        self.assertEqual(report.eject_candidates[0].id, "PR-100")
        self.assertTrue(report.eject_candidates[0].has_code)
        self.assertTrue(report.eject_candidates[0].has_db)

    def test_hibernate_candidate_detection(self) -> None:
        """Projekt aktywny bez modyfikacji od >= 14 dni powinien zostać oznaczony do uśpienia."""
        proj_dir = self.tmp_dir / "03 Projekty" / "PR-200 Projekt Uśpiony"
        proj_dir.mkdir(parents=True)
        card = proj_dir / "PR-200 Projekt Uśpiony.md"
        card.write_text("---\nstatus: active\n---\n# PR-200", encoding="utf-8")

        # Ustaw mtime na 25 dni temu
        past_time = (self.now - datetime.timedelta(days=25)).timestamp()
        os.utime(card, (past_time, past_time))
        os.utime(proj_dir, (past_time, past_time))

        radar = VaultRadar(vault_root=self.tmp_dir, config=self.config, idle_days_threshold=14)
        report = radar.scan(now=self.now)

        self.assertEqual(len(report.hibernate_candidates), 1)
        self.assertEqual(report.hibernate_candidates[0].id, "PR-200")
        self.assertGreaterEqual(report.hibernate_candidates[0].idle_days, 24)

    def test_reading_queue_bottleneck_detection(self) -> None:
        """Kolejka lektur z >= threshold nieprzeczytanymi powinna zgłosić zator."""
        notes_dir = self.tmp_dir / "06 Zasoby" / "Notatki"
        for i in range(15):
            (notes_dir / f"artykul_{i}.md").write_text(
                f"---\nconsumed: false\n---\n# Artykuł {i}", encoding="utf-8"
            )

        radar = VaultRadar(vault_root=self.tmp_dir, config=self.config, reading_queue_threshold=10)
        report = radar.scan(now=self.now)

        self.assertEqual(report.reading_queue.total_unconsumed, 15)
        self.assertTrue(report.reading_queue.bottleneck_detected)
        self.assertTrue(any("Kolejka lektur" in line for line in report.summary_lines))

    def test_live_vault_radar_execution(self) -> None:
        """Weryfikacja uruchomienia na rzeczywistym skarbcu LAB (musi wykonać się w <100ms)."""
        real_vault = find_vault_root(_script_dir)
        radar = VaultRadar(vault_root=real_vault)
        report = radar.scan()
        self.assertGreaterEqual(report.active_projects_count, 1)
        self.assertIsInstance(report.summary_lines, list)


if __name__ == "__main__":
    unittest.main()
