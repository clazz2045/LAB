#!/usr/bin/env python3
"""test_drain_and_quality.py - Testy jednostkowe dla Z-03 (drain_inbox.py) i Z-04 (check_quality.py)

Standard inżynieryjny:
- 100% Python Standard Library (unittest).
- Zero zewnętrznych zależności pip.
- Weryfikacja:
  1. Routing drenażu Skrzynki (brak zrzutów w Zasobach, surowe zrzuty do Skrzynki/_archiwum/).
  2. Doklejanie zrzutów myśli do Dziennika Dnia (Stream Decoupling).
  3. Wykrywanie surowych plików Wispr i raw-capture w Zasobach (Bariera czystości Zasobów).
  4. Walidacja standardu SAT notatki referencyjnej: callout metadanych, minimalna długość 400 znaków, brak zanieczyszczenia myśli człowieka.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import unittest
from pathlib import Path

# Blokada tworzenia bytecode __pycache__
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from core_config import find_vault_root
from drain_inbox import InboxDrainer
from check_quality import QualityChecker


class BaseSandboxTest(unittest.TestCase):
    """Baza testowa z izolowanym katalogiem roboczym."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vault_root = find_vault_root(_script_dir)
        cls.sandbox_dir = cls.vault_root / "08 Zespół AI" / "Skrypty" / ".tmp_test_drain_sandbox"
        if cls.sandbox_dir.exists():
            shutil.rmtree(cls.sandbox_dir, ignore_errors=True)
        cls.sandbox_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.sandbox_dir.exists():
            shutil.rmtree(cls.sandbox_dir, ignore_errors=True)

    def tearDown(self) -> None:
        if self.sandbox_dir.exists():
            for item in self.sandbox_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)


class TestInboxDrainerZ03(BaseSandboxTest):
    """Testy deterministycznego routingu w drain_inbox.py (Z-03)."""

    def setUp(self) -> None:
        self.drainer = InboxDrainer(vault_root=self.vault_root)

    def test_extract_date(self) -> None:
        """Ekstrakcja daty z nazwy pliku."""
        d1 = self.drainer._extract_date("Wispr_2026-11-04_1013_Notatka.md")
        self.assertEqual(d1, datetime.date(2026, 11, 4))

        d2 = self.drainer._extract_date("2026-10-25_raport.md")
        self.assertEqual(d2, datetime.date(2026, 10, 25))

        d3 = self.drainer._extract_date("bez_daty.md")
        self.assertIsNone(d3)

    def test_wispr_file_with_url_routes_to_inbox_archive_never_assets(self) -> None:
        """Plik o nazwie Wispr_* zawierający link URL ZAWSZE trafia do Skrzynka/_archiwum, NIGDY do Zasobów."""
        mock_file = self.sandbox_dir / "Wispr_2026-11-04_1200_httpsjackclarknet.md"
        mock_file.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\n"
            "Sprawdź ten link https://jack-clark.net/2026/11/02/import-ai-479/\n"
            "oraz popraw zadania w sprincie.",
            encoding="utf-8",
        )

        dest_dir = self.drainer.get_destination_dir(mock_file)

        # Musi trafić do 01 Skrzynka/_archiwum/2026-11/
        self.assertIn("_archiwum", str(dest_dir))
        self.assertIn("01 Skrzynka", str(dest_dir))
        self.assertNotIn("06 Zasoby", str(dest_dir))
        self.assertTrue(str(dest_dir).endswith("2026-11"))

    def test_raw_capture_metadata_routes_to_inbox_archive(self) -> None:
        """Plik bez prefiksu Wispr, ale z type: raw-capture lub source: wispr-flow trafia do archiwum Skrzynki."""
        mock_file = self.sandbox_dir / "glosowy_zrzut.md"
        mock_file.write_text(
            "---\ntype: raw-capture\ncreated: 2026-11-04\n---\nLuźne myśli z drogi do biura.",
            encoding="utf-8",
        )

        dest_dir = self.drainer.get_destination_dir(mock_file)
        self.assertIn("01 Skrzynka", str(dest_dir))
        self.assertIn("_archiwum", str(dest_dir))
        self.assertNotIn("06 Zasoby", str(dest_dir))

    def test_valid_reference_note_routes_to_assets_notes(self) -> None:
        """Prawdziwa notatka referencyjna (type: note, note_type: reference) trafia do 06 Zasoby/Notatki."""
        mock_file = self.sandbox_dir / "Artykul_Referencyjny.md"
        mock_file.write_text(
            "---\ntype: note\nnote_type: reference\ncreated: 2026-11-04\nsource_url: https://example.com/artykul\n---\n"
            "> [!info] Metadane źródłowe\n"
            "Treść artykułu badawczego.",
            encoding="utf-8",
        )

        dest_dir = self.drainer.get_destination_dir(mock_file)
        self.assertEqual(dest_dir, self.vault_root / "06 Zasoby" / "Notatki")

    def test_media_and_documents_route_to_assets_subfolders(self) -> None:
        """Pliki binarne (pdf, png, mp3) trafiają do odpowiednich podkatalogów w Zasobach."""
        pdf_file = self.sandbox_dir / "raport.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 mock")
        self.assertEqual(self.drainer.get_destination_dir(pdf_file), self.vault_root / "06 Zasoby" / "Dokumenty")

        png_file = self.sandbox_dir / "diagram.png"
        png_file.write_bytes(b"\x89PNG mock")
        self.assertEqual(self.drainer.get_destination_dir(png_file), self.vault_root / "06 Zasoby" / "Obrazy")

        mp3_file = self.sandbox_dir / "nagranie.mp3"
        mp3_file.write_bytes(b"ID3 mock")
        self.assertEqual(self.drainer.get_destination_dir(mp3_file), self.vault_root / "06 Zasoby" / "Audio")

    def test_append_to_daily_journal(self) -> None:
        """Deterministyczne doklejanie treści zrzutu do Dziennika Dnia w Warsztacie."""
        target_date = datetime.date(2026, 11, 4)
        mock_capture = self.sandbox_dir / "Wispr_2026-11-04_1430_Wazne_Refleksje.md"
        mock_capture.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\nKluczowa myśl o architekturze LAB 2.",
            encoding="utf-8",
        )

        daily_file = self.vault_root / "02 Warsztat" / "_archiwum" / "2026" / "11" / "2026-11-04.md"
        existed_before = daily_file.is_file()
        original_content = daily_file.read_text(encoding="utf-8") if existed_before else None

        try:
            self.drainer._append_to_daily_journal(mock_capture, mock_capture.read_text(encoding="utf-8"))
            self.assertTrue(daily_file.is_file())
            appended_content = daily_file.read_text(encoding="utf-8")
            self.assertIn("Wispr: 2026-11-04_1430_Wazne_Refleksje", appended_content)
            self.assertIn("Kluczowa myśl o architekturze LAB 2.", appended_content)
        finally:
            if existed_before and original_content is not None:
                daily_file.write_text(original_content, encoding="utf-8")
            elif not existed_before and daily_file.is_file():
                daily_file.unlink()


class TestCheckQualityZ04(BaseSandboxTest):
    """Testy reguł czystości Zasobów i standardu SAT w check_quality.py (Z-04)."""

    def setUp(self) -> None:
        self.checker = QualityChecker(vault_root=self.vault_root)
        self.assets_notes_dir = self.vault_root / "06 Zasoby" / "Notatki"
        self.assets_docs_dir = self.vault_root / "06 Zasoby" / "Dokumenty"

    def test_check_quality_detects_wispr_file_in_assets(self) -> None:
        """Wykrycie pliku Wispr_ w Zasobach generuje błąd poziomu error (FAIL)."""
        mock_file = self.assets_notes_dir / "Wispr_2026-11-04_leak_in_assets.md"
        mock_file.write_text("surowy przechwyt", encoding="utf-8")

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "Wispr_2026-11-04_leak_in_assets.md" in (f.path or "")
            ]
            self.assertTrue(len(findings) > 0, "Powinien zostać wykryty surowy plik Wispr w Zasobach.")
            self.assertEqual(findings[0].level, "error")
            self.assertIn("Naruszenie czystości Zasobów", findings[0].message)
            self.assertGreaterEqual(report.stats.get("raw_captures_in_assets", 0), 1)
        finally:
            mock_file.unlink(missing_ok=True)

    def test_check_quality_detects_raw_capture_metadata_in_assets(self) -> None:
        """Wykrycie pliku z type: raw-capture w Zasobach generuje błąd poziomu error (FAIL)."""
        mock_file = self.assets_docs_dir / "notatka_przechwyt.md"
        mock_file.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\nTreść surowa.",
            encoding="utf-8",
        )

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "notatka_przechwyt.md" in (f.path or "")
            ]
            self.assertTrue(len(findings) > 0, "Powinien zostać wykryty plik raw-capture w Zasobach.")
            self.assertEqual(findings[0].level, "error")
            self.assertIn("Naruszenie czystości Zasobów", findings[0].message)
        finally:
            mock_file.unlink(missing_ok=True)

    def test_reference_note_missing_info_callout_warns(self) -> None:
        """Notatka referencyjna bez calloutu > [!info] generuje ostrzeżenie (warning)."""
        mock_file = self.assets_notes_dir / "_test_ref_no_callout.md"
        body = "To jest treść badawcza bez calloutu metadanych źródłowych. " * 10
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\ncreated: 2026-11-15\nsource_url: https://example.com\nconsumed: false\n---\n# Tytuł\n\n{body}",
            encoding="utf-8",
        )

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_no_callout.md" in (f.path or "")
            ]
            self.assertTrue(len(findings) > 0, "Powinien zostać wykryty brak calloutu metadanych.")
            self.assertEqual(findings[0].level, "warning")
            self.assertIn("Brak wymaganego calloutu metadanych źródłowych", findings[0].message)
        finally:
            mock_file.unlink(missing_ok=True)

    def test_reference_note_too_short_generates_error(self) -> None:
        """Brak sztucznego limitu znaków: krótka, ale kompletna notatka distilled od 2026-11-13 przechodzi bez ERROR."""
        mock_file = self.assets_notes_dir / "_test_ref_stub.md"
        mock_file.write_text(
            "---\ntype: note\nnote_type: reference\nstage: distilled\ncreated: 2026-11-13\nsource_url: https://example.com\nconsumed: false\n---\n"
            "# Tytuł\n\n> [!info] Metadane źródłowe\n> - Autor: Jan\n\n"
            "## 💡 Główna Teza i Esencja\nKrótka, konkretna teza.\n\n"
            "## 🔍 Szczegóły i Przebieg Rozumowania\n- Fakt 1.\n\n"
            "## 🚀 Implikacje i Wnioski\n- Wniosek 1.\n\n"
            "## 🔗 Powiązania w Skarbcu\n- [[Sztuczna Inteligencja]]\n",
            encoding="utf-8",
        )

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_stub.md" in (f.path or "") and f.level == "error"
            ]
            self.assertEqual(len(findings), 0, f"Krótka ale kompletna notatka nie powinna mieć ERROR (brak limitu znaków): {findings}")
        finally:
            mock_file.unlink(missing_ok=True)

    def test_reference_note_contaminated_with_human_thoughts_generates_error(self) -> None:
        """Notatka referencyjna zanieczyszczona myślami człowieka generuje błąd poziomu error."""
        mock_file = self.assets_notes_dir / "_test_ref_contaminated.md"
        body = (
            "Obszerny tekst artykułu badawczego o sztucznej inteligencji. " * 8
            + "\n\n### Notatki operacyjne użytkownika:\n- Poprawić manifest\n- Zrobić zakupy\n"
        )
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\ncreated: 2026-11-04\nsource_url: https://example.com\nconsumed: false\n---\n"
            f"# Tytuł\n\n> [!info] Metadane źródłowe\n> - Autor: Badacz\n\n{body}",
            encoding="utf-8",
        )

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_contaminated.md" in (f.path or "") and f.level == "error"
            ]
            self.assertTrue(len(findings) > 0, "Powinno zostać wykryte zanieczyszczenie notatki myślami człowieka.")
            self.assertIn("Zanieczyszczenie notatki referencyjnej", findings[0].message)
        finally:
            mock_file.unlink(missing_ok=True)

    def test_valid_sat_reference_note_passes_cleanly(self) -> None:
        """Wzorcowa notatka SAT (>400 zn., callout metadanych, czysta) przechodzi bez błędów."""
        mock_file = self.assets_notes_dir / "_test_ref_valid_sat.md"
        body = (
            "Rozwinięta synteza merytoryczna oparta na twardych dowodach empirycznych i danych liczbowych. "
            "Autor analizuje odwrócenie paradoksu produktywności badań naukowych dzięki zastosowaniu modeli frontier AI. "
            "W benchmarku FrontierMath skuteczność wzrosła z 2% do 94%, natomiast w teście Humanity's Last Exam "
            "odnotowano skok z 8% do 67.7%. Wnioski makroekonomiczne wskazują na pilną potrzebę adaptacji instytucji finansowych."
        )
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\ncreated: 2026-11-04\nsource_url: https://example.com/badanie\nconsumed: false\n---\n"
            f"# Tytuł Notatki SAT\n\n> [!info] Metadane źródłowe\n> - **Autor:** Dr Badacz\n> - **Źródło:** [example.com](https://example.com)\n\n"
            f"## Tezy Wiodące\n\n{body}\n\n## 🔗 Powiązania w Skarbcu\n- [[Sztuczna Inteligencja]]\n",
            encoding="utf-8",
        )

        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_valid_sat.md" in (f.path or "")
            ]
            self.assertEqual(len(findings), 0, f"Wzorcowa notatka SAT nie powinna mieć żadnych zastrzeżeń: {findings}")
        finally:
            mock_file.unlink(missing_ok=True)

    def test_reference_note_without_consumed_warns(self) -> None:
        """Notatka referencyjna z listopada 2026 bez flagi consumed generuje ostrzeżenie jakościowe."""
        mock_file = self.assets_notes_dir / "_test_ref_missing_consumed.md"
        body = "Merytoryczna treść notatki powyżej czterystu znaków. " * 15
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\ncreated: 2026-11-05\nsource_url: https://example.com/test\n---\n"
            f"# Test\n\n> [!info] Metadane źródłowe\n> - **Autor:** Tester\n\n{body}\n",
            encoding="utf-8",
        )
        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_missing_consumed.md" in (f.path or "")
            ]
            self.assertTrue(any("consumed" in f.message for f in findings))
        finally:
            mock_file.unlink(missing_ok=True)

    def test_distilled_sat_note_missing_layers_generates_error(self) -> None:
        """Notatka stage: distilled bez wymaganych warstw Kanonu Trójwarstwowego generuje error."""
        mock_file = self.assets_notes_dir / "_test_ref_distilled_incomplete.md"
        body = "Obszerny tekst merytoryczny o AI. " * 30
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\nstage: distilled\ncreated: 2026-11-06\nsource_url: https://example.com/test\nconsumed: false\n---\n"
            f"# Test\n\n> [!info] Metadane źródłowe\n> - **Autor:** Tester\n\n## Teza Wiodąca\n\n{body}\n",
            encoding="utf-8",
        )
        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_distilled_incomplete.md" in (f.path or "") and f.level == "error"
            ]
            self.assertTrue(len(findings) > 0, "Brak warstw powinien generować błąd krytyczny.")
            self.assertTrue(any("Kanonu Trójwarstwowego" in f.message for f in findings))
        finally:
            mock_file.unlink(missing_ok=True)

    def test_distilled_sat_note_valid_passes_cleanly(self) -> None:
        """Wzorcowa notatka stage: distilled ze wszystkimi warstwami i kotwicami przechodzi bez błędów."""
        mock_file = self.assets_notes_dir / "_test_ref_distilled_complete.md"
        l1 = "Teza wiodąca wskazująca na skok produktywności dzięki modelom AI. " * 10
        l2 = (
            "Przegląd faktów i wskaźników empirycznych:\n"
            "- Wzrost wskaźnika FrontierMath z 2% do 94% w latach 2024-2026.\n"
            "- Automatyzacja 10-krotna generowania kodu w zespołach badawczych.\n"
            "- Raport IMF wskazuje na konieczność zmian w modelach makroekonomicznych.\n"
        )
        l3 = "Wnioski dla środowiska LAB: integracja procedur i adaptacja nowych architektur agentowych. " * 5
        mock_file.write_text(
            f"---\ntype: note\nnote_type: reference\nstage: distilled\ncreated: 2026-11-06\nsource_url: https://example.com/complete\nconsumed: false\n---\n"
            f"# Kompletna Notatka SAT\n\n> [!info] Metadane źródłowe\n> - **Autor:** Jack Clark\n> - **Źródło:** [example.com](https://example.com)\n\n"
            f"## 🎯 Teza Wiodąca\n\n{l1}\n\n## 📊 Dowody i Przebieg\n\n{l2}\n\n## 💡 Zastosowanie w LAB\n\n{l3}\n\n## 🔗 Powiązania w Skarbcu\n- [[Informatyka i Technologie]]\n",
            encoding="utf-8",
        )
        try:
            report = self.checker.check_all()
            findings = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_distilled_complete.md" in (f.path or "") and f.level == "error"
            ]
            self.assertEqual(len(findings), 0, f"Kompletna notatka distilled nie powinna mieć błędów: {findings}")
        finally:
            mock_file.unlink(missing_ok=True)

    def test_strict_sat_era_raw_and_short_note_generates_errors(self) -> None:
        """Nowa notatka od 2026-11-13 ze stage: raw, brakującymi sekcjami lub placeholderami generuje ERROR (bez limitu znaków)."""
        mock_file = self.assets_notes_dir / "_test_ref_strict_2026_11_13.md"
        mock_file.write_text(
            "---\ntype: note\nnote_type: reference\nstage: raw\ncreated: 2026-11-13\nsource_url: https://example.com/yt\nconsumed: false\ntags:\n  - wideo\n  - do-opracowania\n---\n"
            "# Surowe Wideo\n\n> [!info] Metadane źródłowe\n> - **Autor:** Tester\n\n"
            "## 💡 Abstrakt i Główna Teza (Executive Summary)\n\n"
            "## 🔍 Dowody, Fakty i Szczegóły Techniczne\n*(Do uzupełnienia)*\n",
            encoding="utf-8",
        )
        try:
            report = self.checker.check_all()
            errors = [
                f.message for f in report.findings
                if f.category == "assets" and "_test_ref_strict_2026_11_13.md" in (f.path or "") and f.level == "error"
            ]
            joined = "\n".join(errors)
            self.assertIn("nie ma statusu 'stage: distilled'", joined)
            self.assertIn("Brak wymaganych sekcji z kanonicznego szablonu", joined)
            self.assertIn("Puste sekcje lub niewypełnione placeholdery", joined)
        finally:
            mock_file.unlink(missing_ok=True)

    def test_article_mentioning_youtube_is_not_classified_as_video(self) -> None:
        """Artykuł tekstowy wspominający YouTube i mający kod z TODO/## nie jest klasyfikowany jako wideo."""
        mock_file = self.assets_notes_dir / "_test_ref_article_mentions_yt.md"
        mock_file.write_text(
            "---\ntype: note\nnote_type: reference\nstage: distilled\ncreated: 2026-11-13\n"
            "updated: 2026-11-13\nsource_url: https://example.com/article\nconsumed: false\ntags:\n  - ai\n---\n"
            "# Analiza platform wideo\n\n> [!info] Metadane źródłowe\n> - **Autor:** Analityk\n\n"
            "## 💡 Główna Teza i Esencja\nArtykuł analizuje wpływ YouTube na edukację w 2026 roku.\n\n"
            "## 🔍 Szczegóły i Przebieg Rozumowania\n"
            "- Punkt 1: 75% twórców korzysta z narzędzi AI.\n"
            "- Punkt 2: Wzrost 2x.\n"
            "- Punkt 3: Cytat \"Jakość treści rośnie\".\n"
            "```python\n## Fake H2 inside code\n# TODO in code comment\n```\n\n"
            "## 🚀 Implikacje i Wnioski\n- Wnioski dla LAB.\n\n"
            "## 🔗 Powiązania w Skarbcu\n- [[Sztuczna Inteligencja]]\n",
            encoding="utf-8",
        )
        try:
            report = self.checker.check_all()
            errors = [
                f for f in report.findings
                if f.category == "assets" and "_test_ref_article_mentions_yt.md" in (f.path or "") and f.level == "error"
            ]
            self.assertEqual(len(errors), 0, f"Artykuł nie powinien mieć błędów ani wymagać sekcji wideo: {errors}")
        finally:
            mock_file.unlink(missing_ok=True)

    def test_render_terminal_report_token_hygiene(self) -> None:
        """Weryfikacja czy render_terminal_report chroni okno kontekstowe (max_findings, summary, verbose)."""
        from check_quality import render_terminal_report
        from lab_core.quality import Finding, QualityReport

        findings = [
            Finding(level="error", category="schema", message="Krytyczny błąd schematu"),
        ] + [
            Finding(level="warning", category="links", message=f"Martwy link #{i}")
            for i in range(25)
        ] + [
            Finding(level="warning", category="assets", message=f"Ostrzeżenie zasobów #{i}")
            for i in range(15)
        ]

        report = QualityReport(
            status="broken",
            vault_root=str(self.vault_root),
            instance_name="LAB",
            rooms_count=8,
            findings=findings,
            stats={},
        )

        # 1. Tryb domyślny (max_findings=10)
        default_out = render_terminal_report(report, max_findings=10)
        self.assertIn("Krytyczny błąd schematu", default_out)
        self.assertIn("pokazano 10 z 40", default_out)
        self.assertIn("... oraz 30 pozostałych ostrzeżeń", default_out)

        # 2. Tryb summary
        summary_out = render_terminal_report(report, summary_only=True)
        self.assertIn("Krytyczny błąd schematu", summary_out)
        self.assertIn("PODSUMOWANIE OSTRZEŻEŃ", summary_out)
        self.assertIn("[links]: 25 wystąpień", summary_out)
        self.assertIn("[assets]: 15 wystąpień", summary_out)

        # 3. Tryb verbose
        verbose_out = render_terminal_report(report, verbose=True)
        self.assertIn("Martwy link #24", verbose_out)
        self.assertIn("Ostrzeżenie zasobów #14", verbose_out)


class TestArchiveLinkTargets(BaseSandboxTest):
    """Linki do zawartości _archiwum są poprawne, a sam _archiwum nie jest skanowany jako źródło."""

    def _run(self, files: dict):
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.notes_links import NotesAndLinksInspector

        root = self.sandbox_dir / "vault_links"
        for rel, content in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        ctx = QualityContext(vault_root=root, config=ConfigManager(self.vault_root / "lab.config.toml"))
        NotesAndLinksInspector().inspect(ctx)
        return [f for f in ctx.findings if f.category == "links"]

    def test_link_to_archived_file_is_not_dangling(self) -> None:
        links = self._run({
            "03 Projekty/_archiwum/PR-X/cel_testowy.md": "treść",
            "06 Zasoby/Notatki/a.md": "[[cel_testowy]]",
        })
        self.assertEqual(links, [])

    def test_link_to_missing_target_is_dangling(self) -> None:
        links = self._run({"06 Zasoby/Notatki/a.md": "[[nieistnieje_xyz]]"})
        self.assertEqual(len(links), 1)

    def test_dead_link_inside_archive_is_not_reported(self) -> None:
        links = self._run({"03 Projekty/_archiwum/PR-X/stara.md": "[[nieistnieje_xyz]]"})
        self.assertEqual(links, [])

    def test_links_in_code_blocks_and_templates_are_ignored(self) -> None:
        links = self._run({
            "06 Zasoby/Notatki/a.md": (
                "Przykład `[[wikilinks]]` oraz:\n"
                "```markdown\n[[Brak_W_Kodzie]]\n```\n"
                "[[Nadrzędny Temat 1]] i [[RS-xxx Nazwa]] i [[...]]\n"
            ),
        })
        self.assertEqual(links, [])


class TestIntermediateVersionsFlag(BaseSandboxTest):
    """Pole keep_intermediate_versions w Karcie Projektu steruje kontrolą Produkt/_wersje/."""

    def _run(self, card_fm: str, with_version_file: bool):
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.products import ProjectsAndProductsInspector

        root = self.sandbox_dir / "vault_versions"
        proj = root / "03 Projekty" / "PR-900 Test"
        (proj / "Produkt" / "_wersje").mkdir(parents=True, exist_ok=True)
        (proj / "PR-900 Test.md").write_text(f"---\n{card_fm}---\n# Test\n", encoding="utf-8")
        if with_version_file:
            (proj / "Produkt" / "_wersje" / "PR-900_v0.9_x.pdf").write_bytes(b"x")
        ctx = QualityContext(vault_root=root, config=ConfigManager(self.vault_root / "lab.config.toml"))
        ProjectsAndProductsInspector().inspect(ctx)
        return [f for f in ctx.findings if "keep_intermediate_versions" in f.message]

    def test_flag_false_with_versions_warns(self) -> None:
        self.assertEqual(len(self._run("type: project\nkeep_intermediate_versions: false\n", True)), 1)

    def test_flag_false_without_versions_is_clean(self) -> None:
        self.assertEqual(self._run("type: project\nkeep_intermediate_versions: false\n", False), [])

    def test_flag_true_or_default_allows_versions(self) -> None:
        self.assertEqual(self._run("type: project\nkeep_intermediate_versions: true\n", True), [])
        self.assertEqual(self._run("type: project\n", True), [])


class TestCompanionNoteOnDrain(BaseSandboxTest):
    """Weryfikuje automatyczne tworzenie notatki z 'consumed: false' przy drenażu dokumentów."""

    def test_drain_pdf_creates_companion_reference_note(self) -> None:
        test_root = self.sandbox_dir / "vault_companion"
        inbox = test_root / "01 Skrzynka"
        inbox.mkdir(parents=True, exist_ok=True)
        pdf_file = inbox / "Raport_Kwartalny.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 mock")

        # Uruchom drenaż
        drainer = InboxDrainer(vault_root=test_root, config_path=self.vault_root / "lab.config.toml")
        dest = drainer.archive_item("Raport_Kwartalny.pdf")

        # Sprawdź czy plik PDF trafił do 06 Zasoby/Dokumenty/
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.parent.name, "Dokumenty")

        # Sprawdź czy powstała notatka w 06 Zasoby/Notatki/
        note_path = test_root / "06 Zasoby" / "Notatki" / "Raport_Kwartalny.md"
        self.assertTrue(note_path.is_file())

        content = note_path.read_text(encoding="utf-8")
        self.assertIn("consumed: false", content)
        self.assertIn("note_type: reference", content)
        self.assertIn("[[06 Zasoby/Dokumenty/Raport_Kwartalny.pdf|Otwórz dokument]]", content)
        self.assertNotIn("Status czytania", content)
        self.assertNotIn("Stan lektury", content)


class TestHybridLegacyAndIngestGate(BaseSandboxTest):
    """Testy cezury legacy w AssetsAndReferencesInspector oraz bramki ingestu w drain_inbox."""

    def test_legacy_callout_suppressed_by_default_and_shown_with_audit_legacy(self) -> None:
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.assets import AssetsAndReferencesInspector

        root = self.sandbox_dir / "vault_legacy"
        notes = root / "06 Zasoby" / "Notatki"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / "stara.md").write_text(
            "---\ntype: note\nnote_type: reference\ncreated: 2026-09-20\nsource_url: https://example.com\n---\n# Stara\n",
            encoding="utf-8",
        )
        cfg = ConfigManager(self.vault_root / "lab.config.toml")

        ctx_default = QualityContext(vault_root=root, config=cfg, audit_legacy=False)
        AssetsAndReferencesInspector().inspect(ctx_default)
        self.assertEqual(ctx_default.stats["legacy_notes_count"], 1)
        self.assertEqual([f for f in ctx_default.findings if "Brak wymaganego calloutu" in f.message], [])

        ctx_audit = QualityContext(vault_root=root, config=cfg, audit_legacy=True)
        AssetsAndReferencesInspector().inspect(ctx_audit)
        self.assertEqual(ctx_audit.stats["legacy_notes_count"], 1)
        self.assertEqual(len([f for f in ctx_audit.findings if "Brak wymaganego calloutu" in f.message]), 1)

    def test_drain_inbox_injects_metadata_callout_for_reference_note(self) -> None:
        test_root = self.sandbox_dir / "vault_ingest_callout"
        (test_root / "01 Skrzynka").mkdir(parents=True, exist_ok=True)
        (test_root / "06 Zasoby" / "Notatki").mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.vault_root / "lab.config.toml", test_root / "lab.config.toml")

        clip = test_root / "01 Skrzynka" / "Artykul.md"
        clip.write_text(
            "---\ntype: note\nnote_type: reference\ncreated: 2026-11-15\nsource_url: https://example.com/art\n---\n# Artykuł\n\nTreść.\n",
            encoding="utf-8",
        )

        drainer = InboxDrainer(vault_root=test_root)
        dest = drainer.archive_item("Artykul.md")
        self.assertTrue(dest.is_file())
        text = dest.read_text(encoding="utf-8")
        self.assertIn("> [!info] Metadane źródłowe", text)
        self.assertIn("https://example.com/art", text)

    def test_drain_inbox_converts_capture_and_missing_type_to_reference_note(self) -> None:
        """Drenaż notatki z type: \"capture\" lub bez type do 06 Zasoby/Notatki/ konwertuje YAML do note/reference."""
        from lab_core.markdown import parse_frontmatter

        test_root = self.sandbox_dir / "vault_capture_convert"
        (test_root / "01 Skrzynka").mkdir(parents=True, exist_ok=True)
        (test_root / "06 Zasoby" / "Notatki").mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.vault_root / "lab.config.toml", test_root / "lab.config.toml")

        capture_file = test_root / "01 Skrzynka" / "Capture_Clip.md"
        capture_file.write_text(
            '---\ntype: "capture"\nsource_url: https://example.com/clip\nconsumed: false\ntags: [ai, sat]\n---\n'
            "# Przechwycony Artykuł\n\nWażna treść z sieci.\n",
            encoding="utf-8",
        )

        no_type_file = test_root / "01 Skrzynka" / "No_Type_Clip.md"
        no_type_file.write_text(
            "---\nsource_url: https://example.com/notype\nauthor: Jan Kowalski\n---\n"
            "# Notatka bez typu\n\nTreść bez pola type.\n",
            encoding="utf-8",
        )

        drainer = InboxDrainer(vault_root=test_root)
        today_str = datetime.date.today().isoformat()

        dest_capture = drainer.archive_item("Capture_Clip.md")
        self.assertEqual(dest_capture.parent, test_root / "06 Zasoby" / "Notatki")
        fm_cap, body_cap = parse_frontmatter(dest_capture)
        self.assertEqual(fm_cap.get("type"), "note")
        self.assertEqual(fm_cap.get("note_type"), "reference")
        self.assertEqual(fm_cap.get("created"), today_str)
        self.assertIs(fm_cap.get("consumed"), False)
        self.assertEqual(fm_cap.get("source_url"), "https://example.com/clip")
        self.assertEqual(fm_cap.get("tags"), ["ai", "sat"])
        self.assertIn("Ważna treść z sieci.", body_cap)
        self.assertIn("> [!info] Metadane źródłowe", body_cap)

        dest_notype = drainer.archive_item("No_Type_Clip.md")
        self.assertEqual(dest_notype.parent, test_root / "06 Zasoby" / "Notatki")
        fm_no, body_no = parse_frontmatter(dest_notype)
        self.assertEqual(fm_no.get("type"), "note")
        self.assertEqual(fm_no.get("note_type"), "reference")
        self.assertEqual(fm_no.get("created"), today_str)
        self.assertIs(fm_no.get("consumed"), False)
        self.assertEqual(fm_no.get("author"), "Jan Kowalski")
        self.assertIn("Treść bez pola type.", body_no)


class TestEngineScriptsHygiene(BaseSandboxTest):
    """Testy higieny katalogu głównego 08 Zespół AI/Skrypty/ (allowlist punktów wejścia)."""

    def _run(self, script_names: list[str]):
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.management import ManagementFilesInspector

        root = self.sandbox_dir / "vault_scripts_hygiene"
        scripts_dir = root / "08 Zespół AI" / "Skrypty"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name in script_names:
            (scripts_dir / name).write_text("# test\n", encoding="utf-8")

        ctx = QualityContext(vault_root=root, config=ConfigManager(self.vault_root / "lab.config.toml"))
        ManagementFilesInspector().inspect(ctx)
        return [f for f in ctx.findings if "Nieautoryzowany skrypt" in f.message]

    def test_allowed_entrypoints_pass_cleanly(self) -> None:
        findings = self._run(["check_quality.py", "checkpoint.py", "drain_inbox.py", "__init__.py"])
        self.assertEqual(findings, [])

    def test_unauthorized_oneoff_script_generates_error(self) -> None:
        findings = self._run(["check_quality.py", "fix_legacy_links.py"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].level, "error")
        self.assertIn("fix_legacy_links.py", findings[0].path or "")


class TestConsumedScopeRule7(BaseSandboxTest):
    """Testy zakresu flagi consumed: (Reguła 7) w NotesAndLinksInspector."""

    def _run(self, files: dict[str, str]):
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.notes_links import NotesAndLinksInspector

        root = self.sandbox_dir / "vault_consumed_scope"
        for rel, content in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        ctx = QualityContext(vault_root=root, config=ConfigManager(self.vault_root / "lab.config.toml"))
        NotesAndLinksInspector().inspect(ctx)
        return [f for f in ctx.findings if f.category == "frontmatter" and "consumed:" in f.message]

    def test_consumed_allowed_in_notes_and_templates_and_archive(self) -> None:
        findings = self._run({
            "06 Zasoby/Notatki/ref.md": "---\nconsumed: false\n---\n# Ref\n",
            "08 Zespół AI/Szablony/tpl.md": "---\nconsumed: false\n---\n# Tpl\n",
            "03 Projekty/_archiwum/PR-1/old.md": "---\nconsumed: true\n---\n# Old\n",
        })
        self.assertEqual(findings, [])

    def test_consumed_outside_allowed_scope_warns(self) -> None:
        findings = self._run({
            "05 Baza Wiedzy/evergreen.md": "---\nconsumed: false\n---\n# Note\n",
            "03 Projekty/PR-1/doc.md": "---\nconsumed: true\n---\n# Doc\n",
        })
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.level == "warning" for f in findings))


class TestGovernanceAndWorkflowHelpers(BaseSandboxTest):
    """Testy dla ManagementFilesInspector (.agents/hooks.json, pre_tool_guard.py),
    checkpoint.check_project_progress_sync oraz mark_thought_blocks_processed."""

    def test_management_inspector_checks_hooks_and_pre_tool_guard(self) -> None:
        from lab_core.config import ConfigManager
        from lab_core.quality.context import QualityContext
        from lab_core.quality.inspectors.management import ManagementFilesInspector

        root = self.sandbox_dir / "vault_hooks_guard"
        (root / "08 Zespół AI" / "Skrypty").mkdir(parents=True, exist_ok=True)
        ctx = QualityContext(vault_root=root, config=ConfigManager(self.vault_root / "lab.config.toml"))
        ManagementFilesInspector().inspect(ctx)
        msgs = [f.message for f in ctx.findings]
        self.assertTrue(any(".agents/hooks.json" in m for m in msgs))
        self.assertTrue(any("pre_tool_guard.py" in m for m in msgs))

    def test_mark_thought_blocks_processed_preserves_user_text(self) -> None:
        from lab_core.quality.inspectors.workshop import (
            detect_unprocessed_thought_blocks,
            mark_thought_blocks_processed,
        )

        agenda = (
            "## 🎯 Główne Wątki Robocze na Dziś\n- [ ] Test\n\n"
            "## ✍️ Dosłowny Zapis Porannych Myśli\n"
            "Oryginalna myśl użytkownika.\n"
            "---\n"
            "Druga myśl.\n"
            "---\n"
        )
        self.assertEqual(len(detect_unprocessed_thought_blocks(agenda)), 2)
        updated, count = mark_thought_blocks_processed(agenda, block_indices=[1], note="przetworzono do PR-018")
        self.assertEqual(count, 1)
        self.assertIn("Oryginalna myśl użytkownika.", updated)
        self.assertIn("> ✅ Przetworzono (", updated)
        self.assertIn("<!-- processed -->", updated)
        self.assertEqual(len(detect_unprocessed_thought_blocks(updated)), 1)

    def test_checkpoint_warns_when_progress_report_is_stale(self) -> None:
        import os
        import time
        from checkpoint import SessionCheckpoint

        root = self.sandbox_dir / "vault_cp_warn"
        proj_dir = root / "03 Projekty" / "PR-999 Test"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (root / "02 Warsztat").mkdir(parents=True, exist_ok=True)
        (root / "08 Zespół AI" / "Skrypty").mkdir(parents=True, exist_ok=True)

        cfg_src = (self.vault_root / "lab.config.toml").read_text(encoding="utf-8")
        (root / "lab.config.toml").write_text(cfg_src, encoding="utf-8")

        prog = proj_dir / "progress-report.md"
        main_p = proj_dir / "PR-999 Test.md"
        prog.write_text("# Progress\n", encoding="utf-8")
        main_p.write_text("# Main\n", encoding="utf-8")
        now = time.time()
        os.utime(prog, (now - 3600, now - 3600))
        os.utime(main_p, (now, now))

        cp = SessionCheckpoint(vault_root=root, config_path=root / "lab.config.toml")
        warnings = cp.check_project_progress_sync(["03 Projekty/PR-999 Test/PR-999 Test.md"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("PR-999 Test", warnings[0])


class TestWisprUrlIngestVariantB(BaseSandboxTest):
    """Testy automatycznego ingestu URL ze zrzutów Wispr Flow (Wariant B)."""

    def setUp(self) -> None:
        super().setUp()
        self.config_path = self.vault_root / "lab.config.toml"
        self.inbox_dir = self.sandbox_dir / "01 Skrzynka"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.assets_notes_dir = self.sandbox_dir / "06 Zasoby" / "Notatki"
        self.assets_notes_dir.mkdir(parents=True, exist_ok=True)
        self.workshop_archive_dir = self.sandbox_dir / "02 Warsztat" / "_archiwum" / "2026" / "11"
        self.workshop_archive_dir.mkdir(parents=True, exist_ok=True)

        self.drainer = InboxDrainer(
            vault_root=self.sandbox_dir,
            config_path=self.config_path,
        )

    def test_wispr_flow_note_with_youtube_url_auto_ingests_to_assets(self) -> None:
        """Zrzut Wispr z linkiem YouTube tworzy kartę referencyjną SAT w Zasobach i linkuje z Dziennikiem."""
        from unittest.mock import patch
        from check_quality import QualityChecker

        test_file = self.inbox_dir / "Wispr_2026-11-18_1015_Wyklad_Kwantowy.md"
        test_file.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\n"
            "Cześć, obejrzyj ten wykład o teorii kwantowej: https://www.youtube.com/watch?v=Shor_Qubit_999\n"
            "Koniecznie przeanalizuj omówienie algorytmu Shora w 10. minucie.",
            encoding="utf-8",
        )

        mock_meta = {
            "title": "Quantum Computation and Shor Algorithm",
            "author": "Oxford Physics",
            "url": "https://www.youtube.com/watch?v=Shor_Qubit_999",
            "is_youtube": True,
            "duration_formatted": "45m 20s",
            "duration_seconds": 2720,
            "description": "Comprehensive university lecture on quantum complexity and Shor algorithm.",
            "transcript": "[00:00] Welcome to quantum computing lecture. [10:00] Here is Shor algorithm analysis.",
        }

        with patch("drain_inbox.fetch_resource_metadata", return_value=mock_meta):
            dest_archive = self.drainer.archive_item(test_file.name)

        # 1. Surowy plik zrzutu przeniesiony do archiwum Skrzynki
        self.assertFalse(test_file.exists())
        self.assertTrue(dest_archive.exists())
        self.assertIn("01 Skrzynka", str(dest_archive))
        self.assertIn("_archiwum", str(dest_archive))
        self.assertTrue(str(dest_archive).startswith(str(self.sandbox_dir)))

        # 2. Utworzona karta referencyjna w Zasobach
        expected_note = self.assets_notes_dir / "Wideo – Quantum Computation and Shor Algorithm (Oxford Physics).md"
        self.assertTrue(expected_note.exists(), f"Karta referencyjna {expected_note} powinna zostać utworzona.")
        self.assertTrue(str(expected_note).startswith(str(self.sandbox_dir)))

        note_content = expected_note.read_text(encoding="utf-8")
        self.assertIn("type: note", note_content)
        self.assertIn("note_type: reference", note_content)
        self.assertIn("stage: distilled", note_content)
        self.assertIn("consumed: false", note_content)
        self.assertIn("source: https://www.youtube.com/watch?v=Shor_Qubit_999", note_content)
        self.assertIn("## 🎙️ Notatka z dyktafonu / Refleksja Właściciela", note_content)
        self.assertIn("Koniecznie przeanalizuj omówienie algorytmu Shora w 10. minucie.", note_content)
        self.assertIn("[[2026-11-18|Dziennik Dnia: 2026-11-18]]", note_content)

        # 3. Wpis w Dzienniku Dnia w Warsztacie
        daily_journal = self.workshop_archive_dir / "2026-11-18.md"
        self.assertTrue(daily_journal.exists())
        self.assertTrue(str(daily_journal).startswith(str(self.sandbox_dir)))
        journal_content = daily_journal.read_text(encoding="utf-8")
        self.assertIn("Wispr: 2026-11-18_1015_Wyklad_Kwantowy", journal_content)
        self.assertIn("📄 Karta w Zasobach: [[Wideo – Quantum Computation and Shor Algorithm (Oxford Physics)]]", journal_content)

        # 4. Sprawdzenie zgodności jakościowej nowo utworzonej karty
        checker = QualityChecker(vault_root=self.sandbox_dir, config_path=self.config_path)
        checker._check_reference_notes_and_assets_hygiene()
        errors = [
            f.message for f in checker.findings
            if expected_note.name in (f.path or "") and f.level == "error"
        ]
        self.assertEqual(len(errors), 0, f"Utworzona karta nie powinna generować błędów jakości: {errors}")

    def test_wispr_flow_note_offline_fallback(self) -> None:
        """W trybie offline (błąd sieci) powstaje karta fallback ze stage: distilled i consumed: false."""
        from unittest.mock import patch
        from check_quality import QualityChecker

        test_file = self.inbox_dir / "Wispr_2026-11-18_1100_Offline_Article.md"
        test_file.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\n"
            "Artykuł offline: https://example.org/docs/deep-dive\n"
            "Do przeczytania w pociągu bez internetu.",
            encoding="utf-8",
        )

        with patch("drain_inbox.fetch_resource_metadata", side_effect=Exception("Network Timeout 5s")):
            dest_archive = self.drainer.archive_item(test_file.name)

        self.assertFalse(test_file.exists())
        self.assertTrue(dest_archive.exists())
        self.assertTrue(str(dest_archive).startswith(str(self.sandbox_dir)))

        # Karta referencyjna fallback
        matching_notes = list(self.assets_notes_dir.glob("*example.org*.md"))
        self.assertTrue(len(matching_notes) > 0, "Powinna powstać karta fallback w Zasobach.")
        note_p = matching_notes[0]
        self.assertTrue(str(note_p).startswith(str(self.sandbox_dir)))

        content = note_p.read_text(encoding="utf-8")
        self.assertIn("stage: distilled", content)
        self.assertIn("consumed: false", content)
        self.assertIn("## 🎙️ Notatka z dyktafonu / Refleksja Właściciela", content)
        self.assertIn("Do przeczytania w pociągu bez internetu.", content)
        self.assertIn("[[2026-11-18|Dziennik Dnia: 2026-11-18]]", content)

        # 4. Sprawdzenie zgodności jakościowej utworzonej karty fallback przez QualityChecker
        checker = QualityChecker(vault_root=self.sandbox_dir, config_path=self.config_path)
        checker._check_reference_notes_and_assets_hygiene()
        errors = [
            f.message for f in checker.findings
            if note_p.name in (f.path or "") and f.level == "error"
        ]
        self.assertEqual(len(errors), 0, f"Utworzona karta fallback nie powinna generować błędów jakości: {errors}")

    def test_wispr_flow_note_no_duplicates_when_card_exists(self) -> None:
        """Jeśli karta dla danego URL już istnieje w Zasobach, drenaż nie tworzy duplikatu."""
        existing_card = self.assets_notes_dir / "Wideo – Istniejacy Material (Autor).md"
        existing_card.write_text(
            "---\ntype: note\nnote_type: reference\nstage: distilled\ncreated: 2026-11-18\n"
            "source_url: https://www.youtube.com/watch?v=EXISTING123\nconsumed: false\n---\n"
            "# Wideo – Istniejacy Material\n\n> [!info] Metadane źródłowe\n\n## 💡 Esencja\nTreść\n\n## 🔗 Powiązania\n- [[06 Zasoby/Notatki]]\n",
            encoding="utf-8",
        )

        test_file = self.inbox_dir / "Wispr_2026-11-18_1200_Duplicate.md"
        test_file.write_text(
            "---\ntype: raw-capture\nsource: wispr-flow\n---\n"
            "Kolejny zrzut z tym samym filmem https://www.youtube.com/watch?v=EXISTING123",
            encoding="utf-8",
        )

        initial_notes_count = len(list(self.assets_notes_dir.glob("*.md")))
        dest_archive = self.drainer.archive_item(test_file.name)

        final_notes_count = len(list(self.assets_notes_dir.glob("*.md")))
        self.assertEqual(initial_notes_count, final_notes_count, "Liczba notatek w Zasobach nie powinna wzrosnąć (brak duplikatu).")


if __name__ == "__main__":
    unittest.main()
