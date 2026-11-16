#!/usr/bin/env python3
"""test_projects.py - Zestaw testów jednostkowych zarządcy projektów LAB 2.0

Standard inżynieryjny:
- 100% Python Standard Library (unittest).
- Zero zewnętrznych zależności pip.
- Testuje wszystkie filary modułu projects.py:
  1. Parsowanie frontmattera YAML i metadanych spisu (happy path & edge cases).
  2. Skanowanie i kategoryzację statusów (active, queued, paused, completed).
  3. Ignorowanie folderów technicznych (_* i .*).
  4. Deterministyczną generację i sortowanie _Spis Projektów.md.
  5. Bezpieczną archiwizację projektów do _archiwum/ (w tym kolizje i dry-run).
  6. Integralność i regułę The One Law (--check, brak karty, brak spisu, zalegające completed).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca LAB
sys.dont_write_bytecode = True

# Zaimportuj moduły z katalogu bieżącego lub korzenia skarbca
_script_dir = Path(__file__).resolve().parent.parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from projects import (
    ProjectInfo,
    ProjectManager,
    extract_existing_index_metadata,
    parse_frontmatter,
)


class TestProjectManager(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="lab_test_proj_")
        self.vault_root = Path(self.test_dir)

        # Utwórz strukturę pokoi
        (self.vault_root / "03 Projekty" / "_archiwum").mkdir(parents=True, exist_ok=True)

        # Skopiuj kanoniczny lab.config.toml
        canonical_toml = Path("lab.config.toml")
        if canonical_toml.is_file():
            shutil.copy(canonical_toml, self.vault_root / "lab.config.toml")
        else:
            raise FileNotFoundError("Brak lab.config.toml w korzeniu skarbca")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_frontmatter_happy_path(self) -> None:
        f = self.vault_root / "test.md"
        f.write_text("---\nid: PR-099\nstatus: active\n---\n# Content\n", encoding="utf-8")
        fm, content = parse_frontmatter(f)
        self.assertEqual(fm.get("id"), "PR-099")
        self.assertEqual(fm.get("status"), "active")
        self.assertIn("# Content", content)

    def test_parse_frontmatter_edge_cases(self) -> None:
        # 1. Nieistniejący plik
        non_existent = self.vault_root / "nie_ma_takiego_pliku.md"
        fm, c = parse_frontmatter(non_existent)
        self.assertEqual(fm, {})
        self.assertEqual(c, "")

        # 2. Pusty plik
        empty_f = self.vault_root / "empty.md"
        empty_f.write_text("", encoding="utf-8")
        fm, c = parse_frontmatter(empty_f)
        self.assertEqual(fm, {})
        self.assertEqual(c, "")

        # 3. Brak delimitera YAML
        no_yaml = self.vault_root / "no_yaml.md"
        no_yaml.write_text("# Zwykły nagłówek bez YAML\nTreść", encoding="utf-8")
        fm, c = parse_frontmatter(no_yaml)
        self.assertEqual(fm, {})
        self.assertIn("Zwykły nagłówek", c)

        # 4. Niezamknięty YAML (brak drugiego '---')
        unclosed = self.vault_root / "unclosed.md"
        unclosed.write_text("---\ntitle: Test\nstatus: active\n", encoding="utf-8")
        fm, c = parse_frontmatter(unclosed)
        self.assertEqual(fm, {})

    def test_extract_existing_index_metadata(self) -> None:
        idx = self.vault_root / "03 Projekty" / "_Spis Projektów.md"
        idx_content = """
| ID | Nazwa Projektu | Status | Faza / Kontekst | Ostatnia Aktualizacja | Karta Projektu |
| :--- | :--- | :---: | :--- | :---: | :--- |
| `PR-010` | Sprzęt IT | 🟢 `w toku` | Badania ofert | 2026-11-01 | [[card]] |
| `PR-020` | Audio | ⏸️ `wstrzymany` | Brak czasu | 2026-11-02 | [[card2]] |

| ID | Nazwa Projektu | Status | Data Ukończenia | Kapsuła Archiwum |
| :--- | :--- | :---: | :---: | :--- |
| `PR-001` | Przebudowa | 🏁 `zakończony` | 2026-11-02 | [[arch]] |
"""
        idx.write_text(idx_content, encoding="utf-8")
        meta = extract_existing_index_metadata(idx)
        self.assertIn("PR-010", meta)
        self.assertEqual(meta["PR-010"]["context"], "Badania ofert")
        self.assertIn("PR-020", meta)
        self.assertEqual(meta["PR-020"]["status_raw"], "⏸️ `wstrzymany`")
        self.assertIn("PR-001", meta)
        self.assertEqual(meta["PR-001"]["status_raw"], "🏁 `zakończony`")

    def test_scan_and_categorize_projects(self) -> None:
        p_act = self.vault_root / "03 Projekty" / "PR-010 Sprzęt IT"
        p_act.mkdir(parents=True)
        (p_act / "PR-010 Sprzęt IT.md").write_text("---\nstatus: active\n---\n", encoding="utf-8")

        p_que = self.vault_root / "03 Projekty" / "PR-013 Głos"
        p_que.mkdir(parents=True)
        (p_que / "PR-013 Głos.md").write_text("---\nstatus: queued\n---\n", encoding="utf-8")

        p_pau = self.vault_root / "03 Projekty" / "PR-002 Muzyka"
        p_pau.mkdir(parents=True)
        (p_pau / "PR-002 Muzyka.md").write_text("---\nstatus: paused\n---\n", encoding="utf-8")

        mgr = ProjectManager(self.vault_root)
        projects = mgr.scan_all_projects()
        self.assertEqual(len(projects), 3)

        status_by_id = {p.id: p.status for p in projects}
        self.assertEqual(status_by_id["PR-010"], "active")
        self.assertEqual(status_by_id["PR-013"], "queued")
        self.assertEqual(status_by_id["PR-002"], "paused")

    def test_scan_ignores_system_and_hidden_dirs(self) -> None:
        # Utwórz poprawny projekt oraz foldery techniczne z prefiksami _ i .
        p_valid = self.vault_root / "03 Projekty" / "PR-030 Projekt Prawilny"
        p_valid.mkdir(parents=True)
        (p_valid / "PR-030 Projekt Prawilny.md").write_text("---\nstatus: active\n---\n", encoding="utf-8")

        p_hidden = self.vault_root / "03 Projekty" / ".git_internal"
        p_hidden.mkdir(parents=True)

        p_sys = self.vault_root / "03 Projekty" / "_szablony_pomocnicze"
        p_sys.mkdir(parents=True)

        mgr = ProjectManager(self.vault_root)
        projects = mgr.scan_all_projects()
        # Tylko 1 poprawny projekt powinien zostać zaindeksowany
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].id, "PR-030")

    def test_archive_completed_projects(self) -> None:
        p_done = self.vault_root / "03 Projekty" / "PR-001 Przebudowa"
        p_done.mkdir(parents=True)
        (p_done / "PR-001 Przebudowa.md").write_text(
            "---\nstatus: completed\nupdated: 2026-11-02\n---\n", encoding="utf-8"
        )

        mgr = ProjectManager(self.vault_root)
        ok, issues = mgr.check_integrity()
        self.assertFalse(ok)
        self.assertTrue(any("BŁĄD ARCHIWIZACJI" in iss for iss in issues))

        # Wykonaj archiwizację
        moved = mgr.archive_completed_projects(dry_run=False)
        self.assertEqual(len(moved), 1)
        self.assertFalse(p_done.exists())
        archived_folder = self.vault_root / "03 Projekty" / "_archiwum" / "PR-001 Przebudowa"
        self.assertTrue(archived_folder.is_dir())

        # Zaktualizuj spis i ponowny check powinien przejść
        mgr.update_index_file()
        ok2, issues2 = mgr.check_integrity()
        self.assertTrue(ok2)

    def test_archive_dry_run_does_not_move_directory(self) -> None:
        p_done = self.vault_root / "03 Projekty" / "PR-050 Test Dry Run"
        p_done.mkdir(parents=True)
        (p_done / "PR-050 Test Dry Run.md").write_text(
            "---\nstatus: completed\nupdated: 2026-11-04\n---\n", encoding="utf-8"
        )

        mgr = ProjectManager(self.vault_root)
        moved = mgr.archive_completed_projects(dry_run=True)
        self.assertEqual(len(moved), 1)
        # Folder źródłowy nadal fizycznie istnieje
        self.assertTrue(p_done.exists())
        # Folder docelowy w archiwum NIE został utworzony
        dest = self.vault_root / "03 Projekty" / "_archiwum" / "PR-050 Test Dry Run"
        self.assertFalse(dest.exists())

    def test_archive_completed_projects_destination_exists_raises_error(self) -> None:
        p_done = self.vault_root / "03 Projekty" / "PR-060 Kolizja"
        p_done.mkdir(parents=True)
        (p_done / "PR-060 Kolizja.md").write_text(
            "---\nstatus: completed\nupdated: 2026-11-04\n---\n", encoding="utf-8"
        )

        # Sztucznie stwórz już istniejący folder w archiwum
        colliding_dest = self.vault_root / "03 Projekty" / "_archiwum" / "PR-060 Kolizja"
        colliding_dest.mkdir(parents=True)

        mgr = ProjectManager(self.vault_root)
        with self.assertRaises(FileExistsError) as ctx:
            mgr.archive_completed_projects(dry_run=False)
        self.assertIn("już istnieje", str(ctx.exception))

    def test_check_integrity_detects_missing_card(self) -> None:
        p_broken = self.vault_root / "03 Projekty" / "PR-070 Projekt Bez Karty"
        p_broken.mkdir(parents=True)
        # Brak pliku karty md

        mgr = ProjectManager(self.vault_root)
        ok, issues = mgr.check_integrity()
        self.assertFalse(ok)
        self.assertTrue(any("[BRAK KARTY]" in iss for iss in issues))

    def test_check_integrity_detects_missing_index_file(self) -> None:
        p_ok = self.vault_root / "03 Projekty" / "PR-080 Projekt Zdrowy"
        p_ok.mkdir(parents=True)
        (p_ok / "PR-080 Projekt Zdrowy.md").write_text("---\nstatus: active\n---\n", encoding="utf-8")

        mgr = ProjectManager(self.vault_root)
        # Upewnij się, że _Spis Projektów.md nie istnieje
        idx_file = self.vault_root / "03 Projekty" / "_Spis Projektów.md"
        if idx_file.exists():
            idx_file.unlink()

        ok, issues = mgr.check_integrity()
        self.assertFalse(ok)
        self.assertTrue(any("[BRAK SPISU]" in iss for iss in issues))

    def test_generate_and_sort_index(self) -> None:
        p_act = self.vault_root / "03 Projekty" / "PR-004 Operator"
        p_act.mkdir(parents=True)
        (p_act / "PR-004 Operator.md").write_text("---\nstatus: active\n---\n", encoding="utf-8")

        p_que = self.vault_root / "03 Projekty" / "PR-005 Ulotka"
        p_que.mkdir(parents=True)
        (p_que / "PR-005 Ulotka.md").write_text("---\nstatus: queued\n---\n", encoding="utf-8")

        mgr = ProjectManager(self.vault_root)
        mgr.update_index_file()

        index_file = self.vault_root / "03 Projekty" / "_Spis Projektów.md"
        self.assertTrue(index_file.is_file())
        content = index_file.read_text(encoding="utf-8")

        self.assertIn("## 🚀 Projekty w Toku (Aktywne)", content)
        self.assertIn("## 🟡 Projekty Oczekujące", content)
        self.assertIn("## ⏸️ Projekty Wstrzymane", content)
        self.assertIn("## 📦 Archiwum Ukończonych Przedsięwzięć (`_archiwum/`)", content)
        self.assertIn("PR-004", content)
        self.assertIn("PR-005", content)

    def test_bundle_project_documentation(self) -> None:
        # Utwórz projekt w archiwum
        p_arch = self.vault_root / "03 Projekty" / "_archiwum" / "PR-015 Test Modularyzacji"
        p_arch.mkdir(parents=True)
        card_file = p_arch / "PR-015 Test Modularyzacji.md"
        card_file.write_text(
            "---\nid: PR-015\nstatus: completed\n---\n# Karta PR-015\nOpis projektu.\n",
            encoding="utf-8",
        )

        # Utwórz pliki w Dziennikach Sesji powiązane z PR-015
        session_logs = self.vault_root / "08 Zespół AI" / "Dzienniki Sesji"
        session_logs.mkdir(parents=True)
        cert_file = session_logs / "2026-11-05_Certyfikat_Audytorski_PR015.md"
        cert_file.write_text("# Certyfikat Audytorski PR-015\nOcena 10/10\n", encoding="utf-8")
        unrelated_file = session_logs / "2026-11-05_Certyfikat_Audytorski_PR099.md"
        unrelated_file.write_text("# Inny projekt\n", encoding="utf-8")

        mgr = ProjectManager(self.vault_root)
        projects = mgr.scan_all_projects()
        target_p = next(p for p in projects if p.id == "PR-015")

        copied = mgr.bundle_project_documentation(target_p)
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].name, "2026-11-05_Certyfikat_Audytorski_PR015.md")

        # Sprawdź czy plik fizycznie znalazł się w Notatki/
        notes_dir = p_arch / "Notatki"
        self.assertTrue((notes_dir / "2026-11-05_Certyfikat_Audytorski_PR015.md").is_file())
        self.assertFalse((notes_dir / "2026-11-05_Certyfikat_Audytorski_PR099.md").exists())

        # Sprawdź czy sekcja dokumentacji została dopisana do karty projektu
        updated_card = card_file.read_text(encoding="utf-8")
        self.assertIn("## Dokumentacja Wykonawcza i Archiwum Raportów Kapsuły", updated_card)
        self.assertIn("2026-11-05_Certyfikat_Audytorski_PR015", updated_card)

    def test_bundle_all_archived_projects(self) -> None:
        p_arch = self.vault_root / "03 Projekty" / "_archiwum" / "PR-001 Przebudowa"
        p_arch.mkdir(parents=True)
        (p_arch / "PR-001 Przebudowa.md").write_text(
            "---\nid: PR-001\nstatus: completed\n---\n# Karta PR-001\n",
            encoding="utf-8",
        )

        session_logs = self.vault_root / "08 Zespół AI" / "Dzienniki Sesji"
        session_logs.mkdir(parents=True)
        (session_logs / "2026-11-02_Raport_Audytora_PR-001.md").write_text(
            "# Raport Audytora PR-001\n", encoding="utf-8"
        )

        mgr = ProjectManager(self.vault_root)
        results = mgr.bundle_all_archived_projects()
        self.assertIn("PR-001", results)
        self.assertEqual(len(results["PR-001"]), 1)
        self.assertTrue((p_arch / "Notatki" / "2026-11-02_Raport_Audytora_PR-001.md").is_file())


class TestProjectPortfolioAutoSync(unittest.TestCase):
    """Testy automatycznej synchronizacji _Spis Projektów.md i nadrzędności frontmattera."""

    def _prepare_vault(self, tmp_dir: str) -> Path:
        v_root = Path(tmp_dir)
        (v_root / "03 Projekty" / "_archiwum").mkdir(parents=True, exist_ok=True)
        canonical_toml = Path("lab.config.toml")
        if not canonical_toml.is_file():
            raise FileNotFoundError("Brak lab.config.toml w korzeniu skarbca")
        shutil.copy(canonical_toml, v_root / "lab.config.toml")
        return v_root

    def test_create_project_auto_updates_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab_proj_create_") as tmp_dir:
            v_root = self._prepare_vault(tmp_dir)
            mgr = ProjectManager(v_root)

            proj = mgr.create_project("Nowy Silnik Testowy")
            self.assertEqual(proj.id, "PR-001")
            self.assertTrue((v_root / "03 Projekty" / "PR-001 Nowy Silnik Testowy" / "Produkt").is_dir())
            self.assertTrue((v_root / "03 Projekty" / "PR-001 Nowy Silnik Testowy" / "progress-report.md").is_file())
            self.assertTrue((v_root / "03 Projekty" / "PR-001 Nowy Silnik Testowy" / "PR-001 Nowy Silnik Testowy.md").is_file())

            idx_file = v_root / "03 Projekty" / "_Spis Projektów.md"
            self.assertTrue(idx_file.is_file())
            idx_text = idx_file.read_text(encoding="utf-8")
            self.assertIn("PR-001", idx_text)
            self.assertIn("Nowy Silnik Testowy", idx_text)

            ok, issues = mgr.check_integrity()
            self.assertTrue(ok, f"Nieoczekiwane błędy check_integrity: {issues}")

    def test_pause_project_updates_index_and_frontmatter_overrides_stale_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab_proj_pause_") as tmp_dir:
            v_root = self._prepare_vault(tmp_dir)
            mgr = ProjectManager(v_root)
            proj = mgr.create_project("Projekt Do Zawieszenia")

            # Ręczna weryfikacja nadrzędności frontmattera nad starym spisem:
            # W spisie widnieje jako 'w toku', zmieniamy bezpośrednio kartę na 'paused'
            card = proj.card_path
            assert card is not None
            card.write_text(card.read_text(encoding="utf-8").replace("status: active", "status: paused"), encoding="utf-8")
            scanned = mgr.scan_all_projects()
            self.assertEqual(scanned[0].status, "paused")

            # Teraz test pełnej ścieżki API set_project_status -> automatyczna aktualizacja spisu
            mgr.set_project_status("PR-001", "active")
            mgr.set_project_status("PR-001", "paused")

            fm, _ = parse_frontmatter(card)
            self.assertEqual(fm.get("status"), "paused")
            idx_text = (v_root / "03 Projekty" / "_Spis Projektów.md").read_text(encoding="utf-8")
            self.assertIn("⏸️ `wstrzymany`", idx_text)

            ok, issues = mgr.check_integrity()
            self.assertTrue(ok, f"Nieoczekiwane błędy check_integrity: {issues}")

    def test_archive_project_auto_updates_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab_proj_arch_") as tmp_dir:
            v_root = self._prepare_vault(tmp_dir)
            mgr = ProjectManager(v_root)
            mgr.create_project("Projekt Do Archiwum")
            mgr.set_project_status("PR-001", "completed")

            moved = mgr.archive_completed_projects(dry_run=False)
            self.assertEqual(len(moved), 1)
            self.assertTrue((v_root / "03 Projekty" / "_archiwum" / "PR-001 Projekt Do Archiwum").is_dir())

            # Spis został zaktualizowany automatycznie bez ręcznego update_index_file()
            idx_text = (v_root / "03 Projekty" / "_Spis Projektów.md").read_text(encoding="utf-8")
            self.assertIn("🏁 `zakończony`", idx_text)

            ok, issues = mgr.check_integrity()
            self.assertTrue(ok, f"Nieoczekiwane błędy check_integrity: {issues}")

    def test_check_integrity_detects_index_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab_proj_drift_") as tmp_dir:
            v_root = self._prepare_vault(tmp_dir)
            mgr = ProjectManager(v_root)
            proj = mgr.create_project("Projekt Rozjazd")

            # Zmieniamy status bezpośrednio w karcie bez aktualizacji _Spis Projektów.md
            card = proj.card_path
            assert card is not None
            card.write_text(card.read_text(encoding="utf-8").replace("status: active", "status: paused"), encoding="utf-8")

            ok, issues = mgr.check_integrity()
            self.assertFalse(ok)
            self.assertTrue(any("[ROZJAZD SPISU]" in iss for iss in issues))


if __name__ == "__main__":
    unittest.main()
