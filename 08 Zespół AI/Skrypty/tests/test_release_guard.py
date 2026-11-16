#!/usr/bin/env python3
"""test_release_guard.py - Zestaw testów jednostkowych i integracyjnych strażnika wydań LAB 2.0

Standard inżynieryjny:
- 100% Python Standard Library (unittest / pytest).
- Zero zewnętrznych zależności pip.
- Testuje wszystkie 4 filary modułu release_guard.py oraz integrację z check_quality.py:
  1. Immutable Artifact Guard (blokada nadpisywania wydań).
  2. Canonical Path Guard (blokada /tmp, /private/tmp, /var/folders i ścieżek spoza drzewa LAB).
  3. Fail-Hard Data Validator (blokada brakujących pól i cichych fallbacków).
  4. Atomic Test-and-Promote Pipeline (brama testowa, czyszczenie po błędzie, manifest SHA-256).
  5. Quality Checker Integration (wykrywanie folderów Dzieło oraz skryptów i plików tymczasowych w Produkt/).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

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

from lab_core.security import (
    CanonicalPathError,
    CriticalDataMissingError,
    ImmutableReleaseError,
    ReleaseGuardError,
    TestGateFailedError,
    UnauthorizedSprintInjectionError,
    WorkshopPolicyViolationError,
    assert_release_immutable,
    assert_sprint_task_authorized,
    assert_workshop_task_type_allowed,
    assert_workshop_content_clean,
    calculate_sha256,
    is_release_path,
    promote_release,
    format_release_filename,
    parse_release_filename,
    validate_canonical_path,
    validate_metadata_batch,
    validate_metadata_record,
    validate_no_unauthorized_sprint_injection,
    validate_script_execution,
)
from check_quality import QualityChecker, check_quality
from core_config import ConfigManager, find_vault_root


class BaseReleaseGuardTest(unittest.TestCase):
    """Baza testowa konfigurująca bezpieczny katalog roboczy wewnątrz skarbca LAB."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vault_root = find_vault_root(_script_dir)
        cls.test_sandbox = cls.vault_root / "08 Zespół AI" / "Skrypty" / ".tmp_test_guard_sandbox"
        if cls.test_sandbox.exists():
            shutil.rmtree(cls.test_sandbox)
        cls.test_sandbox.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.test_sandbox.exists():
            shutil.rmtree(cls.test_sandbox)

    def tearDown(self) -> None:
        # Czyść zawartość piaskownicy po każdym teście
        if self.test_sandbox.exists():
            for item in self.test_sandbox.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()


# ==============================================================================
# 1. TESTY: IMMUTABLE ARTIFACT GUARD
# ==============================================================================

class TestImmutableArtifactGuard(BaseReleaseGuardTest):
    """Weryfikacja reguły: istniejące wydania w Produkt/ są niezmienne."""

    def test_immutable_artifact_raises_on_existing_target(self) -> None:
        """Próba nadpisania istniejącego pliku wydania rzuca ImmutableReleaseError (FAIL)."""
        prod_dir = self.test_sandbox / "Produkt"
        prod_dir.mkdir(parents=True, exist_ok=True)
        existing_pdf = prod_dir / "Raport_v1.0.pdf"
        existing_pdf.write_text("Wydanie 1.0", encoding="utf-8")

        self.assertTrue(existing_pdf.exists())
        with self.assertRaises(ImmutableReleaseError) as ctx:
            assert_release_immutable(existing_pdf)

        self.assertIn("już istnieje", str(ctx.exception))
        self.assertIn("Raport_v1.0.pdf", str(ctx.exception))

    def test_immutable_artifact_passes_on_non_existent_target(self) -> None:
        """Brak pliku docelowego nie rzuca błędu."""
        prod_dir = self.test_sandbox / "Produkt"
        prod_dir.mkdir(parents=True, exist_ok=True)
        non_existent = prod_dir / "Raport_v1.1.pdf"

        # Nie powinno rzucić wyjątku
        assert_release_immutable(non_existent)

    def test_is_release_path_detection(self) -> None:
        """Poprawne rozpoznawanie ścieżek wydań Produkt/ i Produkt/_wersje/."""
        self.assertTrue(is_release_path(Path("03 Projekty/PR-001/Produkt/plik.pdf")))
        self.assertTrue(is_release_path(Path("03 Projekty/PR-001/Produkt/_wersje/plik_v1.0.pdf")))
        self.assertFalse(is_release_path(Path("02 Warsztat/Brudnopis.md")))


# ==============================================================================
# 2. TESTY: CANONICAL PATH GUARD
# ==============================================================================

class TestCanonicalPathGuard(BaseReleaseGuardTest):
    """Weryfikacja reguły: zakaz ścieżek z /tmp oraz spoza kanonicznego drzewa LAB."""

    def test_path_in_tmp_raises_canonical_path_error(self) -> None:
        """Próba użycia ścieżki w /tmp rzuca CanonicalPathError (FAIL)."""
        with self.assertRaises(CanonicalPathError) as ctx:
            validate_canonical_path("/tmp/nielegalny_skrypt.py", vault_root=self.vault_root)
        self.assertIn("/tmp", str(ctx.exception))

    def test_path_in_private_tmp_raises_canonical_path_error(self) -> None:
        """Próba użycia ścieżki w /private/tmp rzuca CanonicalPathError (FAIL)."""
        with self.assertRaises(CanonicalPathError) as ctx:
            validate_canonical_path("/private/tmp/dane.json", vault_root=self.vault_root)
        self.assertIn("/private/tmp", str(ctx.exception))

    def test_path_in_var_folders_raises_canonical_path_error(self) -> None:
        """Próba użycia ścieżki w systemowym /var/folders rzuca CanonicalPathError (FAIL)."""
        with self.assertRaises(CanonicalPathError) as ctx:
            validate_canonical_path("/var/folders/zz/temp_file.pdf", vault_root=self.vault_root)
        self.assertIn("/var/folders", str(ctx.exception))

    def test_path_outside_vault_raises_canonical_path_error(self) -> None:
        """Próba użycia ścieżki leżącej poza drzewem LAB rzuca CanonicalPathError (FAIL)."""
        outside_path = Path.home() / "Desktop" / "outside.txt"
        with self.assertRaises(CanonicalPathError) as ctx:
            validate_canonical_path(outside_path, vault_root=self.vault_root)
        self.assertIn("poza kanonicznym drzewem LAB", str(ctx.exception))

    def test_canonical_path_valid_inside_vault(self) -> None:
        """Poprawna ścieżka wewnątrz drzewa LAB przechodzi bez błędu."""
        valid_path = self.test_sandbox / "poprawny_plik.txt"
        resolved = validate_canonical_path(valid_path, vault_root=self.vault_root)
        self.assertEqual(resolved, valid_path.resolve())

    def test_validate_script_execution_inside_vault(self) -> None:
        """Weryfikacja, czy aktualny skrypt wykonuje się z wnętrza drzewa LAB."""
        script_path = Path(__file__).resolve()
        validated = validate_script_execution(script_path=script_path, vault_root=self.vault_root)
        self.assertEqual(validated, script_path)


# ==============================================================================
# 3. TESTY: FAIL-HARD DATA VALIDATOR
# ==============================================================================

class TestFailHardDataValidator(unittest.TestCase):
    """Weryfikacja reguły: zakaz brakujących pól i cichych fallbacków w metadanych."""

    def test_missing_required_title_raises_critical_error(self) -> None:
        """Brak wymaganego pola 'title' rzuca CriticalDataMissingError (FAIL)."""
        record = {"year": 2024, "page": 42}
        with self.assertRaises(CriticalDataMissingError) as ctx:
            validate_metadata_record(record, required_fields=("title", "year", "page"))
        self.assertIn("brak wymaganego pola 'title'", str(ctx.exception))

    def test_none_field_raises_critical_error(self) -> None:
        """Pole równe None rzuca CriticalDataMissingError (FAIL)."""
        record = {"title": None, "year": 2024, "page": 42}
        with self.assertRaises(CriticalDataMissingError) as ctx:
            validate_metadata_record(record)
        self.assertIn("pole 'title' ma wartość None", str(ctx.exception))

    def test_empty_string_title_raises_critical_error(self) -> None:
        """Puste pole tekstowe rzuca CriticalDataMissingError (FAIL)."""
        record = {"title": "   ", "year": 2024, "page": 42}
        with self.assertRaises(CriticalDataMissingError) as ctx:
            validate_metadata_record(record)
        self.assertIn("jest pustym ciągiem znaków", str(ctx.exception))

    def test_forbidden_fallback_phrase_raises_critical_error(self) -> None:
        """Wykrycie 'Poezje i felietony z rubryki' rzuca CriticalDataMissingError (FAIL)."""
        record = {
            "title": "Poezje i felietony z rubryki",
            "year": 2024,
            "page": 42,
        }
        with self.assertRaises(CriticalDataMissingError) as ctx:
            validate_metadata_record(record)
        self.assertIn("cichy fallback", str(ctx.exception))
        self.assertIn("Poezje i felietony z rubryki", str(ctx.exception))

    def test_additional_fallback_variants_raise_critical_error(self) -> None:
        """Wykrycie generycznych wartości 'Brak tytułu', 'Untitled' itp. rzuca błąd."""
        for bad_title in ["Brak tytułu", "bez tytułu", "Untitled", "[brak]", "N/A"]:
            with self.subTest(title=bad_title):
                rec = {"title": bad_title, "year": 2023, "page": 1}
                with self.assertRaises(CriticalDataMissingError):
                    validate_metadata_record(rec)

    def test_invalid_page_number_raises_critical_error(self) -> None:
        """Numer strony <= 0 rzuca błąd."""
        rec = {"title": "Poprawny tytuł", "year": 2024, "page": 0}
        with self.assertRaises(CriticalDataMissingError) as ctx:
            validate_metadata_record(rec)
        self.assertIn("musi być > 0", str(ctx.exception))

    def test_valid_record_passes(self) -> None:
        """Poprawny rekord przechodzi walidację bez zgłaszania wyjątku (PASS)."""
        record = {
            "title": "W 60. rocznicę",
            "year": 2024,
            "page": 42,
        }
        # Nie powinno rzucić wyjątku
        validate_metadata_record(record)

    def test_validate_metadata_batch(self) -> None:
        """Walidacja wsadowa zatrzymuje się na pierwszym błędnym rekordzie."""
        valid_records = [
            {"title": f"Wiersz {i}", "year": 2020 + i, "page": i + 1}
            for i in range(5)
        ]
        self.assertEqual(validate_metadata_batch(valid_records), 5)

        corrupt_records = valid_records + [{"title": "Poezje i felietony z rubryki", "year": 2024, "page": 10}]
        with self.assertRaises(CriticalDataMissingError):
            validate_metadata_batch(corrupt_records)


# ==============================================================================
# 4. TESTY: ATOMIC TEST-AND-PROMOTE PIPELINE
# ==============================================================================

class TestAtomicTestAndPromotePipeline(BaseReleaseGuardTest):
    """Weryfikacja bramy testowej, atomowości i generowania manifestu SHA-256."""

    def test_promote_release_success_with_sha256_manifest(self) -> None:
        """Poprawna promocja z sukcesem bramy testowej generuje manifest SHA-256 (PASS)."""
        staged_file = self.test_sandbox / "draft_anthology.pdf"
        dummy_content = b"PDF-1.4 DUMMY HIGH-QUALITY ARTIFACT FOR LAB 2.0"
        staged_file.write_bytes(dummy_content)

        target_dir = self.test_sandbox / "Produkt"
        version_tag = "v1.3"
        metadata = {
            "project_id": "PR-003",
            "author": "Maks",
            "audit_status": "certified_by_ada",
        }

        # Brama testowa kończąca się kodem 0 (sukces)
        test_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]

        promoted_path = promote_release(
            staged_file=staged_file,
            target_dir=target_dir,
            version_tag=version_tag,
            test_command=test_cmd,
            metadata=metadata,
            vault_root=self.vault_root,
        )

        # 1. Sprawdź czy plik roboczy zniknął (przeniesiony)
        self.assertFalse(staged_file.exists())

        # 2. Sprawdź istnienie promowanego pliku docelowego
        self.assertTrue(promoted_path.exists())
        self.assertEqual(promoted_path.name, "draft_anthology_v1.3.pdf")
        self.assertEqual(promoted_path.read_bytes(), dummy_content)

        # 3. Sprawdź manifest
        manifest_file = target_dir / "draft_anthology_v1.3.pdf.manifest.json"
        self.assertTrue(manifest_file.exists())

        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest["semantic_version"], "v1.3")
        self.assertEqual(manifest["file_name"], "draft_anthology_v1.3.pdf")
        self.assertEqual(manifest["sha256"], calculate_sha256(promoted_path))
        self.assertEqual(manifest["size_bytes"], len(dummy_content))
        self.assertEqual(manifest["test_gate"]["status"], "passed")
        self.assertEqual(manifest["audit_metadata"]["project_id"], "PR-003")

    def test_promote_release_fails_and_deletes_staged_file_on_bad_test(self) -> None:
        """Oblewająca brama testowa kasuje staged_file i rzuca TestGateFailedError (FAIL)."""
        staged_file = self.test_sandbox / "untested_draft.pdf"
        staged_file.write_bytes(b"DEFECTIVE_CONTENT")

        target_dir = self.test_sandbox / "Produkt"
        test_cmd = [sys.executable, "-c", "import sys; print('TEST FAILURE MSG', file=sys.stderr); sys.exit(2)"]

        with self.assertRaises(TestGateFailedError) as ctx:
            promote_release(
                staged_file=staged_file,
                target_dir=target_dir,
                version_tag="v1.0",
                test_command=test_cmd,
                vault_root=self.vault_root,
            )

        # 1. Wyjątek zawiera informację o błędzie
        self.assertIn("kod wyjścia: 2", str(ctx.exception))
        self.assertIn("TEST FAILURE MSG", str(ctx.exception))

        # 2. Plik roboczy MUSI być bezwzględnie usunięty
        self.assertFalse(staged_file.exists())

        # 3. Żadne pliki w katalogu docelowym nie mogły powstać
        final_file = target_dir / "untested_draft_v1.0.pdf"
        self.assertFalse(final_file.exists())
        self.assertFalse((target_dir / "untested_draft_v1.0.pdf.manifest.json").exists())

    def test_promote_release_blocks_when_target_already_exists(self) -> None:
        """Próba promocji do istniejącego pliku wydania rzuca ImmutableReleaseError (FAIL)."""
        target_dir = self.test_sandbox / "Produkt"
        target_dir.mkdir(parents=True, exist_ok=True)
        existing_target = target_dir / "report_v1.0.pdf"
        existing_target.write_bytes(b"EXISTING_RELEASE")

        staged_file = self.test_sandbox / "report.pdf"
        staged_file.write_bytes(b"NEW_CONTENT")

        # Test command, który nie powinien być nawet uruchomiony
        test_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]

        with self.assertRaises(ImmutableReleaseError) as ctx:
            promote_release(
                staged_file=staged_file,
                target_dir=target_dir,
                version_tag="v1.0",
                test_command=test_cmd,
                vault_root=self.vault_root,
            )

        self.assertIn("już istnieje", str(ctx.exception))
        # Plik docelowy zachował oryginalną zawartość
        self.assertEqual(existing_target.read_bytes(), b"EXISTING_RELEASE")

    def test_promote_release_blocks_target_dir_not_in_produkt_allowlist(self) -> None:
        """Zasada Białej Listy: próba promocji do katalogu innego niż Produkt/ (np. 'artefakt') rzuca CanonicalPathError."""
        illegal_target_dir = self.test_sandbox / "artefakt"
        illegal_target_dir.mkdir(parents=True, exist_ok=True)
        staged_file = self.test_sandbox / "sample.pdf"
        staged_file.write_bytes(b"DATA")

        test_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]

        with self.assertRaises(CanonicalPathError) as ctx:
            promote_release(
                staged_file=staged_file,
                target_dir=illegal_target_dir,
                version_tag="v1.0",
                test_command=test_cmd,
                vault_root=self.vault_root,
            )
        self.assertIn("Allowlist Violation", str(ctx.exception))


# ==============================================================================
# 5. TESTY: INTEGRACJA Z CHECK_QUALITY.PY
# ==============================================================================

class TestCheckQualityIntegration(unittest.TestCase):
    """Weryfikacja reguł jakościowych w check_quality.py: zakaz folderu Dzieło i czystość Produkt/.
    Wszystkie testy działają w 100% odizolowanym katalogu tymczasowym (tempfile),
    dzięki czemu nigdy nie modyfikują i nie zanieczyszczają aktywnego skarbca iCloud użytkownika.
    """

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.mock_vault = Path(self.tmp_dir.name)

        # Skopiuj konfigurację lab.config.toml do mock skarbca
        canonical_toml = Path("lab.config.toml")
        if canonical_toml.is_file():
            shutil.copy(canonical_toml, self.mock_vault / "lab.config.toml")

        # Utwórz strukturę 8 pokoi w izolowanej piaskownicy
        for room_name in [
            "01 Skrzynka",
            "02 Warsztat",
            "03 Projekty",
            "04 Operacje",
            "05 Baza Wiedzy",
            "06 Zasoby",
            "07 Bazy Danych",
            "08 Zespół AI",
        ]:
            (self.mock_vault / room_name).mkdir(parents=True, exist_ok=True)

        # Utwórz kanoniczne pliki zarządcze
        (self.mock_vault / "02 Warsztat" / "Dashboard LAB.md").write_text("# Dashboard\n", encoding="utf-8")
        (self.mock_vault / "03 Projekty" / "_Spis Projektów.md").write_text("# Projekty\n", encoding="utf-8")
        (self.mock_vault / "04 Operacje" / "_Spis Operacji.md").write_text("# Operacje\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_check_quality_detects_unauthorized_artefakt_and_pseudo_release_folder(self) -> None:
        """Zasada Białej Listy: wykrycie folderu 'Artefakt' lub 'Dzieło' w projekcie generuje błąd (FAIL)."""
        projects_dir = self.mock_vault / "03 Projekty"
        mock_proj = projects_dir / "_test_mock_project_pseudo"
        mock_artefakt = mock_proj / "Artefakt"
        mock_artefakt.mkdir(parents=True, exist_ok=True)

        checker = QualityChecker(vault_root=self.mock_vault)
        report = checker.check_all()

        findings = [
            f for f in report.findings
            if f.category == "projects" and "Naruszenie Zasady Białej Listy" in f.message
        ]
        self.assertTrue(len(findings) > 0, "Powinien zostać wykryty nieautoryzowany folder wydań 'Artefakt'.")
        self.assertEqual(findings[0].level, "error")
        self.assertGreaterEqual(report.stats.get("unauthorized_release_dirs", 0), 1)

    def test_check_quality_detects_unauthorized_subdir_inside_produkt(self) -> None:
        """Zasada Białej Listy: wewnątrz Produkt/ dozwolone są TYLKO _podglad, _wersje, _zrodla."""
        projects_dir = self.mock_vault / "03 Projekty"
        mock_proj = projects_dir / "_test_mock_project_allowlist"
        mock_prod = mock_proj / "Produkt"
        mock_illegal_sub = mock_prod / "artefakty"  # Nie znajduje się na białej liście
        mock_illegal_sub.mkdir(parents=True, exist_ok=True)

        checker = QualityChecker(vault_root=self.mock_vault)
        report = checker.check_all()

        allowlist_findings = [
            f for f in report.findings
            if f.category == "products" and "Naruszenie Zasady Białej Listy" in f.message
        ]
        self.assertTrue(len(allowlist_findings) > 0, "Folder 'artefakty' w Produkt/ powinien naruszyć Białą Listę.")
        self.assertEqual(allowlist_findings[0].level, "error")
        self.assertGreaterEqual(report.stats.get("unauthorized_release_dirs", 0), 1)

    def test_check_quality_detects_forbidden_script_in_produkt(self) -> None:
        """Wykrycie skryptu w folderze Produkt/ generuje błąd w check_quality (FAIL)."""
        projects_dir = self.mock_vault / "03 Projekty"
        mock_proj = projects_dir / "_test_mock_project_script"
        mock_prod = mock_proj / "Produkt"
        mock_prod.mkdir(parents=True, exist_ok=True)
        illegal_script = mock_prod / "generate_report.py"
        illegal_script.write_text("print('illegal script in Produkt')", encoding="utf-8")

        checker = QualityChecker(vault_root=self.mock_vault)
        report = checker.check_all()

        prod_findings = [
            f for f in report.findings
            if f.category == "products" and "generate_report.py" in f.path
        ]
        self.assertTrue(len(prod_findings) > 0, "Powinien zostać wykryty nielegalny skrypt w folderze Produkt.")
        self.assertEqual(prod_findings[0].level, "error")
        self.assertGreaterEqual(report.stats.get("invalid_produkt_files", 0), 1)

    def test_check_quality_detects_forbidden_temp_file_in_produkt(self) -> None:
        """Wykrycie pliku tymczasowego (*.tmp) w folderze Produkt/ generuje błąd w check_quality (FAIL)."""
        projects_dir = self.mock_vault / "03 Projekty"
        mock_proj = projects_dir / "_test_mock_project_temp"
        mock_prod = mock_proj / "Produkt"
        mock_prod.mkdir(parents=True, exist_ok=True)
        temp_artifact = mock_prod / "partial_render.tmp"
        temp_artifact.write_bytes(b"TEMPORARY BYTES")

        checker = QualityChecker(vault_root=self.mock_vault)
        report = checker.check_all()

        prod_findings = [
            f for f in report.findings
            if f.category == "products" and "partial_render.tmp" in f.path
        ]
        self.assertTrue(len(prod_findings) > 0, "Powinien zostać wykryty plik tymczasowy w folderze Produkt.")
        self.assertEqual(prod_findings[0].level, "error")
        self.assertGreaterEqual(report.stats.get("invalid_produkt_files", 0), 1)


# ==============================================================================
# 6. TESTY: STANDARD NAZEWNICTWA PRODUKTÓW (LAB 3.0)
# ==============================================================================

class TestReleaseFilenameStandard(unittest.TestCase):
    """Weryfikacja formatowania i parsowania nazw wydań [ID]_[Wersja]_[Opis].[ext]."""

    def test_format_release_filename(self) -> None:
        formatted = format_release_filename("PR-003", "v1.4", "Leszek_Dlugosz_Poezje_z_Sieci_Wykadrowane", ".pdf")
        self.assertEqual(formatted, "PR-003_v1.4_Leszek_Dlugosz_Poezje_z_Sieci_Wykadrowane.pdf")

    def test_format_release_filename_adds_v_prefix(self) -> None:
        formatted = format_release_filename("PR-005", "2.0", "Ulotka_A4", "png")
        self.assertEqual(formatted, "PR-005_v2.0_Ulotka_A4.png")

    def test_parse_release_filename_valid(self) -> None:
        parsed = parse_release_filename("PR-003_v1.4_Leszek_Dlugosz_Poezje_z_Sieci_Wykadrowane.pdf")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["project_id"], "PR-003")
        self.assertEqual(parsed["version"], "v1.4")
        self.assertEqual(parsed["description"], "Leszek_Dlugosz_Poezje_z_Sieci_Wykadrowane")
        self.assertEqual(parsed["extension"], ".pdf")

    def test_parse_release_filename_invalid(self) -> None:
        # Stary format z wersją na końcu nie pasuje do nowego kanonicznego wzorca
        parsed = parse_release_filename("PR-003_Leszek_Dlugosz_v1.4.pdf")
        self.assertIsNone(parsed)


# ==============================================================================
# 7. TESTY: HUMAN THOUGHTS PROTECTION GUARD (SPRINT INJECTION GUARD)
# ==============================================================================

class TestHumanThoughtsProtectionGuard(BaseReleaseGuardTest):
    """Weryfikacja ochrony myśli użytkownika przed samowolnym wpisywaniem do sprintu."""

    def test_assert_sprint_task_authorized_passes_when_confirmed(self) -> None:
        """Potwierdzone przez użytkownika zadanie przechodzi pomyślnie (PASS)."""
        # Nie powinno rzucić wyjątku
        assert_sprint_task_authorized(
            task_title="Dedykowany przełącznik motywów set_theme.py",
            is_user_confirmed=True,
        )

    def test_assert_sprint_task_authorized_raises_when_not_confirmed(self) -> None:
        """Próba wpisania zadania bez potwierdzenia człowieka rzuca UnauthorizedSprintInjectionError (FAIL)."""
        with self.assertRaises(UnauthorizedSprintInjectionError) as ctx:
            assert_sprint_task_authorized(
                task_title="Samowolna interpretacja notatki użytkownika o skrótach w Raycast",
                is_user_confirmed=False,
            )
        self.assertIn("Próba nieautoryzowanego wpisania zadania do Sprintu/Warsztatu", str(ctx.exception))
        self.assertIn("human_thoughts", str(ctx.exception))

    def test_assert_sprint_task_authorized_raises_on_empty_title(self) -> None:
        """Pusty tytuł zadania rzuca UnauthorizedSprintInjectionError."""
        with self.assertRaises(UnauthorizedSprintInjectionError) as ctx:
            assert_sprint_task_authorized(task_title="   ", is_user_confirmed=True)
        self.assertIn("nie może być pusty", str(ctx.exception))

    def test_validate_no_unauthorized_sprint_injection_batch_passes(self) -> None:
        """Walidacja wsadowa autoryzowanych zadań (dict i tuple) kończy się sukcesem."""
        batch = [
            {"title": "Zadanie A", "is_user_confirmed": True},
            {"task_title": "Zadanie B", "confirmed": True},
            ("Zadanie C", True),
        ]
        count = validate_no_unauthorized_sprint_injection(batch)
        self.assertEqual(count, 3)

    def test_validate_no_unauthorized_sprint_injection_batch_fails_on_unconfirmed(self) -> None:
        """Walidacja wsadowa zatrzymuje się i rzuca błąd przy niepotwierdzonym zadaniu."""
        batch = [
            {"title": "Zadanie A", "is_user_confirmed": True},
            {"title": "Zadanie B - samowolna idea", "is_user_confirmed": False},
            {"title": "Zadanie C", "is_user_confirmed": True},
        ]
        with self.assertRaises(UnauthorizedSprintInjectionError) as ctx:
            validate_no_unauthorized_sprint_injection(batch)
        self.assertIn("Zadanie B - samowolna idea", str(ctx.exception))

    def test_config_manager_loads_human_thoughts_policy(self) -> None:
        """Weryfikacja obecności i poprawności wczytania [policies.human_thoughts] z konfiguracji."""
        config = ConfigManager(vault_root=self.vault_root)
        self.assertTrue(hasattr(config, "human_thoughts_policy"))
        policy = config.human_thoughts_policy
        self.assertTrue(policy.protect_unorganized_thoughts)
        self.assertTrue(policy.forbid_auto_sprint_injection)
        self.assertTrue(policy.require_explicit_confirmation)
        self.assertTrue(len(policy.description) > 0)

    def test_check_quality_human_thoughts_protection_active(self) -> None:
        """Weryfikacja czy check_quality raportuje aktywną ochronę myśli użytkownika."""
        checker = QualityChecker(vault_root=self.vault_root)
        report = checker.check_all()
        self.assertTrue(report.stats.get("human_thoughts_protection_active"))


# ==============================================================================
# 8. TESTY: WORKSHOP & SPRINT INTEGRITY GUARD (PR, OP, TD vs RS & CLEAN WORKBENCH)
# ==============================================================================

class TestWorkshopIntegrityGuard(BaseReleaseGuardTest):
    """Weryfikacja nienaruszalności stołu Warsztatu i separacji badań RS."""

    def test_config_manager_loads_workshop_policy(self) -> None:
        """Weryfikacja wczytania [policies.workshop] przez ConfigManager."""
        config = ConfigManager(vault_root=self.vault_root)
        self.assertTrue(hasattr(config, "workshop_policy"))
        policy = config.workshop_policy
        self.assertEqual(policy.allowed_task_types, ["PR", "OP"])
        self.assertEqual(policy.forbidden_task_types, ["RS"])
        self.assertIn("Pokoje Robocze", policy.forbidden_sections)
        self.assertIn("Zrzut ze Skrzynki", policy.forbidden_sections)

    def test_assert_workshop_task_type_allowed_passes_for_pr_op(self) -> None:
        """Zadania PR i OP przechodzą pomyślnie (PASS)."""
        valid_tasks = [
            "PR-014 Budowa Instalatora i Wzorca Instancji LAB",
            "OP-001 Kopia Zapasowa i Archiwizacja",
            "Zadanie operacyjne bez prefiksu badawczego",
        ]
        for task in valid_tasks:
            assert_workshop_task_type_allowed(task)

    def test_assert_workshop_task_type_allowed_raises_on_rs(self) -> None:
        """Zadania badawcze (RS) rzucają WorkshopPolicyViolationError (FAIL)."""
        forbidden_tasks = [
            "RS-002 Architektura Instancji Agentowych",
            "[[RS-002.01 Hierarchiczny Koordynator AI vs Płaska Struktura]]",
            "Badanie: SOTA modele krawędziowe",
        ]
        for task in forbidden_tasks:
            with self.assertRaises(WorkshopPolicyViolationError) as ctx:
                assert_workshop_task_type_allowed(task)
            self.assertIn("Baza Wiedzy", str(ctx.exception))

    def test_assert_workshop_content_clean_passes(self) -> None:
        """Czysty plik Warsztatu (tylko PR, OP, TD, bez pokojów i zrzutów) przechodzi pomyślnie."""
        clean_content = """---
type: cockpit-dashboard
---
# 🛠️ Dashboard LAB (LAB)

## 🎯 Na tapecie (Bieżący Sprint)
- [x] PR-001 Zakończenie przebudowy

## ⚡ Filar I: Zadania Doraźne na Warsztacie (`TD-xxxx`)
| ID | Zadanie doraźne To-Do | Status |
| :--- | :--- | :---: |

## 📁 Filar II: Aktywne Projekty (`PR-xxx`)
![[_Spis Projektów#🚀 Projekty w Toku (Aktywne)]]

## 🔄 Filar III: Stałe Operacje (`OP-xxx`)
![[_Spis Operacji#🏛️ Aktywne Domeny Życiowe i Procedury]]
"""
        assert_workshop_content_clean(clean_content)

    def test_assert_workshop_content_clean_raises_on_forbidden_sections(self) -> None:
        """Obecność sekcji Pokojów, Zrzutu lub Filaru IV rzuca błąd."""
        for section in ["## 🧭 Pokoje Robocze LAB", "## 📥 Zrzut ze Skrzynki", "## 🧠 Filar IV: Badania i Synteza Wiedzy"]:
            bad_content = f"# Warsztat\n\n{section}\nTreść sekcji"
            with self.assertRaises(WorkshopPolicyViolationError) as ctx:
                assert_workshop_content_clean(bad_content)
            self.assertIn("Naruszenie struktury Warsztatu", str(ctx.exception))

    def test_assert_workshop_content_clean_raises_on_rs_in_sprint(self) -> None:
        """Obecność zadania RS w sprincie rzuca WorkshopPolicyViolationError."""
        bad_sprint = """# Warsztat

## 🎯 Na tapecie (Bieżący Sprint)
- [ ] [[RS-002.01 Badanie modeli]]

## ⚡ Filar I: Zadania Doraźne na Warsztacie (`TD-xxxx`)
"""
        with self.assertRaises(WorkshopPolicyViolationError) as ctx:
            assert_workshop_content_clean(bad_sprint)
        self.assertIn("Wykryto zadanie badawcze RS", str(ctx.exception))

    def test_check_quality_workshop_hygiene_clean_metric(self) -> None:
        """Weryfikacja czy check_quality raportuje czystość Warsztatu w skarbcu."""
        checker = QualityChecker(vault_root=self.vault_root)
        report = checker.check_all()
        self.assertTrue(report.stats.get("workshop_hygiene_clean"))
        workshop_errors = [f for f in report.findings if f.category in {"workshop", "sprint"}]
        self.assertEqual(len(workshop_errors), 0, f"Znaleziono błędy Warsztatu: {workshop_errors}")


if __name__ == "__main__":
    unittest.main()
