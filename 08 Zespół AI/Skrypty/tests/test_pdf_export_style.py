#!/usr/bin/env python3
"""test_pdf_export_style.py - Testy jednostkowe stylów drukarskich i parsera PDF dla LAB 2.0.

Standard inżynieryjny:
- 100% Python Standard Library (unittest, pathlib, re, importlib).
- Zero zewnętrznych zależności pip.
- Weryfikacja: pionowy outline list numerowanych/punktowanych, brak duplikacji h1,
  agregacja cytatów w blockquote, obecność reguł @media print w tokenach CSS.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request

SCRIPT_DIR = Path(__file__).resolve().parent.parent
VAULT_ROOT = SCRIPT_DIR.parents[1]

if str(VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(VAULT_ROOT))

from core_config import ConfigManager
import lab_core


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPdfExportStyle(unittest.TestCase):
    def setUp(self):
        self.config = ConfigManager(vault_root=VAULT_ROOT)
        self.export_pdf = _load_module("export_pdf", SCRIPT_DIR / "lab_core" / "export.py")
        self.lab_export_pdf = self.export_pdf

    def test_themes_print_rules(self):
        """Weryfikuje obecność kanonicznych reguł @media print w generate_design_tokens_css."""
        css = lab_core.generate_design_tokens_css(self.config)
        self.assertIn("@media print", css)
        self.assertIn("border-bottom: none", css)
        self.assertIn("blockquote {", css)
        self.assertIn("border-left: 4px solid", css)
        self.assertIn("hr {", css)
        self.assertIn("border-top: 1px solid #e2e8f0", css)
        self.assertIn("ol, ul {", css)

    def test_lab_export_pdf_lists_and_quotes(self):
        """Weryfikuje poprawne formatowanie list, blockquotes i hr w lab-export-pdf.py."""
        md_sample = (
            "# Tytuł Dokumentu\n\n"
            "> **Cytat Linia 1**\n"
            "> *Cytat Linia 2*\n\n"
            "---\n\n"
            "## Sekcja Testowa\n\n"
            "1. **Pierwszy punkt :** Treść pierwsza.\n"
            "2. **Drugi punkt :** Treść druga.\n\n"
            "- Punkt A\n"
            "- Punkt B\n"
        )
        body_html = self.lab_export_pdf.parse_markdown(md_sample)
        
        # 1. Sprawdź czy cytaty są w jednym bloku blockquote
        self.assertEqual(body_html.count("<blockquote>"), 1)
        self.assertIn("Cytat Linia 1", body_html)
        self.assertIn("Cytat Linia 2", body_html)
        
        # 2. Sprawdź hr
        self.assertIn("<hr>", body_html)
        self.assertNotIn("<p>---</p>", body_html)

        # 3. Sprawdź listę numerowaną
        self.assertIn("<ol>", body_html)
        self.assertIn("<li><strong>Pierwszy punkt :</strong> Treść pierwsza.</li>", body_html)
        self.assertIn("<li><strong>Drugi punkt :</strong> Treść druga.</li>", body_html)

        # 4. Sprawdź listę nienumerowaną
        self.assertIn("<ul>", body_html)
        self.assertIn("<li>Punkt A</li>", body_html)
        self.assertIn("<li>Punkt B</li>", body_html)

        # 5. Sprawdź brak duplikacji h1 w build_full_html
        full_html = self.lab_export_pdf.build_full_html(body_html, "Tytuł Dokumentu")
        self.assertEqual(full_html.count("<h1>"), 1)

    def test_lab_export_pdf_mermaid(self):
        """Weryfikuje poprawne wyodrębnianie kontenera Mermaid do renderowania grafu."""
        md_sample = (
            "# Architektura\n\n"
            "```mermaid\n"
            "flowchart TD\n"
            "    A --> B\n"
            "```\n"
        )
        body_html = self.lab_export_pdf.parse_markdown(md_sample)
        self.assertIn('<div class="mermaid">', body_html)
        self.assertIn("flowchart TD", body_html)
        self.assertNotIn('<pre class="code-block"', body_html)

        full_html = self.lab_export_pdf.build_full_html(body_html, "Architektura")
        self.assertIn("mermaid.min.js", full_html)
        self.assertIn("mermaid.initialize", full_html)

    def test_export_pdf_parser_lists(self):
        """Weryfikuje parser list i blockquote w export_pdf.py."""
        md_sample = (
            "> Źródło\n"
            "> Dodatkowy opis\n"
            "1. Raz\n"
            "2. Dwa\n"
            "- Alpha\n"
            "- Beta\n"
        )
        html_output = self.export_pdf.markdown_to_html(md_sample, {})
        self.assertIn("<ol>", html_output)
        self.assertIn("<li>Raz</li>", html_output)
        self.assertIn("<li>Dwa</li>", html_output)
        self.assertIn("<ul>", html_output)
        self.assertIn("<li>Alpha</li>", html_output)
        self.assertIn("<li>Beta</li>", html_output)
        self.assertEqual(html_output.count("<blockquote>"), 1)

    def test_generate_full_html_default_note_template(self):
        """Weryfikuje zachowanie domyślnego profilu note bez regresji."""
        full_html = self.export_pdf.generate_full_html("Test Note", "<p>Treść</p>")
        self.assertIn(self.export_pdf.NOTE_CSS, full_html)
        self.assertNotIn(self.export_pdf.TEXTBOOK_CSS, full_html)
        self.assertIn("margin: 18mm 16mm 18mm 16mm;", full_html)

    def test_generate_full_html_textbook_template(self):
        """Weryfikuje wstrzyknięcie TEXTBOOK_CSS dla profilu textbook."""
        full_html = self.export_pdf.generate_full_html("Podręcznik", "<p>Treść</p>", template="textbook")
        self.assertIn(self.export_pdf.TEXTBOOK_CSS, full_html)
        self.assertNotIn(self.export_pdf.NOTE_CSS, full_html)
        self.assertIn("@bottom-center", full_html)
        self.assertIn('content: "§";', full_html)

    def test_frontmatter_autodetect_print_template_and_cssclasses(self):
        """Weryfikuje autodetekcję profilu textbook z YAML frontmatter."""
        cases = [
            "---\ntitle: Test A\nprint_template: textbook\n---\n\n# A\n",
            "---\ntitle: Test B\ncssclasses: [textbook]\n---\n\n# B\n",
            "---\ntitle: Test C\ncssclasses:\n  - other\n  - textbook\n---\n\n# C\n",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, content in enumerate(cases):
                md_path = Path(tmpdir) / f"doc_{idx}.md"
                out_pdf = Path(tmpdir) / f"doc_{idx}.pdf"
                md_path.write_text(content, encoding="utf-8")
                out_html = self.export_pdf.export_markdown_to_pdf(md_path, out_pdf, html_only=True)
                rendered = out_html.read_text(encoding="utf-8")
                self.assertIn(self.export_pdf.TEXTBOOK_CSS, rendered)
                self.assertNotIn(self.export_pdf.NOTE_CSS, rendered)

    def test_cli_template_override(self):
        """Weryfikuje nadpisanie detekcji frontmatteru przez flagę CLI --template."""
        export_script = SCRIPT_DIR / "lab_core" / "export.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Frontmatter ma print_template: textbook, ale CLI wymusza --template note
            md_tb = Path(tmpdir) / "tb.md"
            md_tb.write_text("---\ntitle: TB\nprint_template: textbook\n---\n\n# TB\n", encoding="utf-8")
            out_note_pdf = Path(tmpdir) / "out_note.pdf"
            res1 = subprocess.run(
                [sys.executable, str(export_script), str(md_tb), str(out_note_pdf), "--html-only", "--template", "note"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res1.returncode, 0)
            rendered_note = out_note_pdf.with_suffix(".html").read_text(encoding="utf-8")
            self.assertIn(self.export_pdf.NOTE_CSS, rendered_note)
            self.assertNotIn(self.export_pdf.TEXTBOOK_CSS, rendered_note)

            # 2. Brak frontmatteru, ale CLI wymusza --template textbook
            md_plain = Path(tmpdir) / "plain.md"
            md_plain.write_text("# Plain\n\nTekst.\n", encoding="utf-8")
            out_tb_pdf = Path(tmpdir) / "out_tb.pdf"
            res2 = subprocess.run(
                [sys.executable, str(export_script), str(md_plain), str(out_tb_pdf), "--html-only", "--template", "textbook"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res2.returncode, 0)
            rendered_tb = out_tb_pdf.with_suffix(".html").read_text(encoding="utf-8")
            self.assertIn(self.export_pdf.TEXTBOOK_CSS, rendered_tb)
            self.assertNotIn(self.export_pdf.NOTE_CSS, rendered_tb)

    def test_clean_lab_title_removes_prefixes(self):
        """Weryfikuje usuwanie prefiksów LAB z tytułu okładki i żywej paginy."""
        self.assertEqual(
            self.export_pdf.clean_lab_title("RS-002.11 Podręcznik efektywnego AI"),
            "Podręcznik efektywnego AI",
        )
        self.assertEqual(self.export_pdf.clean_lab_title("PR-016 Nowy Projekt"), "Nowy Projekt")
        self.assertEqual(self.export_pdf.clean_lab_title("Zwykły Tytuł"), "Zwykły Tytuł")

    def test_book_layout_cover_toc_no_metacard_and_themes(self):
        """Weryfikuje układ book: .cover-page, .toc-page, brak .meta-card i wszystkie 4 motywy."""
        md_content = (
            "---\n"
            "type: research-note\n"
            'title: "RS-002.11 Podręcznik Testowy"\n'
            "date: 2026-11-14\n"
            "kicker: Poradnik Praktyczny\n"
            "lang: pl\n"
            "---\n\n"
            "# Pełny Tytuł Okładkowy\n\n"
            "## Wstęp\n\nTekst wstępu.\n\n"
            "## Rozdział 1: Podstawy\n\nTekst.\n\n"
            "### Sekcja 1.1\n\nSzczegóły.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "book.md"
            md_path.write_text(md_content, encoding="utf-8")
            for theme in ("book-classic", "swiss", "editorial", "manuscript"):
                out_pdf = Path(tmpdir) / f"book_{theme}.pdf"
                out_html = self.export_pdf.export_markdown_to_pdf(
                    md_path,
                    out_pdf,
                    html_only=True,
                    layout="book",
                    template=theme,
                    author="Zbigniew",
                    version="1.2",
                )
                rendered = out_html.read_text(encoding="utf-8")
                self.assertIn('class="cover-page"', rendered)
                self.assertIn('class="toc-page"', rendered)
                self.assertIn("Spis treści", rendered)
                self.assertNotIn('class="meta-card"', rendered)
                self.assertIn("Podręcznik Testowy", rendered)
                self.assertNotIn("RS-002.11 Podręcznik Testowy", rendered)
                self.assertIn("Poradnik Praktyczny", rendered)
                self.assertIn("Zbigniew", rendered)
                self.assertIn("1.2", rendered)
                # Pierwszy h1 przeniesiony z .book-body na okładkę, w TOC są h2/h3 z kotwicami
                self.assertIn('<li class="toc-item toc-h1"><a href="#wstep">Wstęp</a>', rendered)
                self.assertIn('<h2 id="wstep">Wstęp</h2>', rendered)
                self.assertIn('<li class="toc-item toc-h2"><a href="#sekcja-1-1">Sekcja 1.1</a>', rendered)
                self.assertIn("size: B5;", rendered)
                self.assertIn('content: "Podręcznik Testowy";', rendered)

    def test_book_layout_english_toc_and_page_size_override(self):
        """Weryfikuje Table of Contents dla lang: en oraz nadpisanie --page-size."""
        md_content = (
            "---\n"
            "title: Commercial AI Handbook\n"
            "print_layout: book\n"
            "lang: en\n"
            "---\n\n"
            "# Commercial AI Handbook\n\n"
            "## Introduction\n\nHello.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "book_en.md"
            md_path.write_text(md_content, encoding="utf-8")
            out_pdf = Path(tmpdir) / "book_en.pdf"
            out_html = self.export_pdf.export_markdown_to_pdf(
                md_path,
                out_pdf,
                html_only=True,
                page_size="A5",
            )
            rendered = out_html.read_text(encoding="utf-8")
            self.assertIn('<html lang="en">', rendered)
            self.assertIn("Table of Contents", rendered)
            self.assertIn("size: A5;", rendered)
            self.assertNotIn('class="meta-card"', rendered)

    def test_build_preview_html_and_cli_preview_no_open(self):
        """Weryfikuje build_preview_html oraz wywołanie CLI --preview --no-open."""
        export_script = SCRIPT_DIR / "lab_core" / "export.py"
        md_content = (
            "---\n"
            "title: Podręcznik Preview\n"
            "lang: pl\n"
            "---\n\n"
            "# Podręcznik Preview\n\n"
            "## Rozdział 1\n\nTreść testowa.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "pv.md"
            md_path.write_text(md_content, encoding="utf-8")

            preview_html = self.export_pdf.build_preview_html(
                md_path=md_path,
                metadata={"title": "Podręcznik Preview", "lang": "pl"},
                body_md="# Podręcznik Preview\n\n## Rozdział 1\n\nTreść testowa.\n",
            )
            self.assertIn('class="preview-toolbar"', preview_html)
            self.assertIn(".preview-toolbar {\n    display: none !important;\n  }", preview_html)
            for theme in ("note", "book-classic", "swiss", "editorial", "manuscript"):
                self.assertIn(f'<style data-theme="{theme}">', preview_html)
            self.assertIn("window.print()", preview_html)
            self.assertIn("Kopiuj komendę CLI", preview_html)

            out_html = Path(tmpdir) / "custom_preview.html"
            res = subprocess.run(
                [
                    sys.executable,
                    str(export_script),
                    str(md_path),
                    str(out_html),
                    "--preview",
                    "--no-open",
                    "--layout",
                    "book",
                    "--template",
                    "editorial",
                    "--page-size",
                    "A4",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertTrue(out_html.exists())
            cli_html = out_html.read_text(encoding="utf-8")
            self.assertIn('data-layout="book"', cli_html)
            self.assertIn('data-page-size="A4"', cli_html)
            self.assertIn('themeEl.value = "editorial"', cli_html)

    def test_inline_links_with_italic_headings_and_html_lang_pl(self):
        """Weryfikuje konwersję linków [tekst](url) z kursywą, kursywy w nagłówkach oraz <html lang='pl'>."""
        md_sample = (
            "### 1. Pewne siebie zmyślanie (*halucynacje* → zob. Słownik)\n\n"
            "Badanie OpenAI ([*Why Language Models Hallucinate*](https://arxiv.org/abs/2509.04664)) pokazuje ten mechanizm.\n\n"
            "Źródło: [`https://arxiv.org/abs/2509.04664`](https://arxiv.org/abs/2509.04664)\n"
        )
        body_html = self.export_pdf.markdown_to_html(md_sample, {}, include_meta_card=False, add_heading_ids=True)
        self.assertIn("1. Pewne siebie zmyślanie (<em>halucynacje</em> → zob. Słownik)</h3>", body_html)
        self.assertIn(
            '<a href="https://arxiv.org/abs/2509.04664"><em>Why Language Models Hallucinate</em></a>',
            body_html,
        )
        self.assertIn(
            '<a href="https://arxiv.org/abs/2509.04664"><code>https://arxiv.org/abs/2509.04664</code></a>',
            body_html,
        )
        self.assertNotIn("[*Why Language Models Hallucinate*]", body_html)
        self.assertNotIn("*halucynacje*", body_html)

        full_html = self.export_pdf.generate_full_html("Test PL", body_html, template="book-classic", layout="book")
        self.assertIn('<html lang="pl">', full_html)
        self.assertIn("overflow-wrap: break-word;", full_html)

    def test_preview_server_get_export_and_cancel(self):
        """Weryfikuje jednorazowy serwer podglądu: GET /, POST /export (Downloads) i POST /cancel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            md_path = tmp_path / "Raport Testowy.md"
            md_path.write_text("---\ntitle: Raport\n---\n\n# Raport\n\nTreść.\n", encoding="utf-8")
            documents_dir = tmp_path / "Documents"

            # 1. GET / + POST /export
            server, done_event, state = self.export_pdf.create_preview_server(
                md_path=md_path,
                metadata={"title": "Raport"},
                body_md="# Raport\n\nTreść.\n",
                downloads_dir=documents_dir,
            )
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
                    page_html = resp.read().decode("utf-8")
                    self.assertIn("Akceptuję — Eksportuj PDF", page_html)
                    self.assertIn("Anuluj", page_html)
                    self.assertIn("~/Documents", page_html)

                expected_pdf = documents_dir / "Raport Testowy.pdf"
                with mock.patch.object(
                    self.export_pdf,
                    "export_markdown_to_pdf",
                    return_value=expected_pdf,
                ) as mock_export:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/export",
                        data=json.dumps(
                            {"layout": "book", "template": "swiss", "page_size": "A4"}
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        self.assertEqual(resp.status, 200)
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(data["ok"])
                        self.assertEqual(data["path"], str(expected_pdf))

                    mock_export.assert_called_once()
                    kwargs = mock_export.call_args.kwargs
                    self.assertEqual(kwargs["output_pdf_path"], expected_pdf)
                    self.assertTrue(kwargs["open_after"])
                    self.assertEqual(kwargs["layout"], "book")
                    self.assertEqual(kwargs["template"], "swiss")
                    self.assertEqual(kwargs["page_size"], "A4")
                    self.assertTrue(done_event.wait(timeout=2))
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=2)

            # 2. POST /cancel
            server2, done_event2, state2 = self.export_pdf.create_preview_server(
                md_path=md_path,
                metadata={"title": "Raport"},
                body_md="# Raport\n\nTreść.\n",
                downloads_dir=documents_dir,
            )
            port2 = server2.server_address[1]
            t2 = threading.Thread(target=server2.serve_forever, daemon=True)
            t2.start()
            try:
                req_cancel = urllib.request.Request(
                    f"http://127.0.0.1:{port2}/cancel",
                    data=b"{}",
                    method="POST",
                )
                with urllib.request.urlopen(req_cancel, timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
                    cancel_data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(cancel_data["ok"])
                    self.assertTrue(cancel_data["cancelled"])
                self.assertTrue(state2["cancelled"])
                self.assertTrue(done_event2.wait(timeout=2))
            finally:
                server2.shutdown()
                server2.server_close()
                t2.join(timeout=2)

    def test_default_output_directory_is_documents(self):
        """Weryfikuje, że domyślnym katalogiem wyjściowym bez output_pdf_path jest ~/Documents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            md_path = fake_home / "Notatka.md"
            md_path.write_text("# Notatka\n\nTreść.\n", encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=fake_home):
                out_html = self.export_pdf.export_markdown_to_pdf(md_path, html_only=True)
                self.assertEqual(out_html.resolve(), (fake_home / "Documents" / "Notatka.html").resolve())
                self.assertTrue(out_html.exists())

    def test_obsidian_shellcommands_data_json_valid(self):
        """Weryfikuje poprawność JSON w data.json i obecność obu komend eksportu PDF."""
        data_path = VAULT_ROOT / ".obsidian" / "plugins" / "obsidian-shellcommands" / "data.json"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        cmds = {cmd["id"]: cmd for cmd in data.get("shell_commands", [])}
        self.assertIn("lab-export-pdf", cmds)
        self.assertIn("lab_core/export.py", cmds["lab-export-pdf"]["platform_specific_commands"]["default"])
        self.assertIn("--open", cmds["lab-export-pdf"]["platform_specific_commands"]["default"])
        self.assertIn("lab-export-pdf-template", cmds)
        entry = cmds["lab-export-pdf-template"]
        self.assertEqual(entry["alias"], "Eksportuj do PDF z użyciem szablonu")
        self.assertIn("lab_core/export.py", entry["platform_specific_commands"]["default"])
        self.assertIn("--preview", entry["platform_specific_commands"]["default"])

    def test_default_author_zbigniew_jasiolek(self):
        """Weryfikuje domyślnego autora 'Zbigniew Jasiołek' w układzie book i podglądzie."""
        _, book_html = self.export_pdf.prepare_book_content("<p>Treść</p>", {"title": "Test"}, "Test")
        self.assertIn('<div class="cover-author">Zbigniew Jasiołek</div>', book_html)

        preview_html = self.export_pdf.build_preview_html(
            md_path=Path("/tmp/test.md"),
            metadata={"title": "Test"},
            body_md="Treść",
            server_mode=True,
        )
        self.assertIn('value="Zbigniew Jasiołek"', preview_html)
        self.assertIn("Zmień folder…", preview_html)

    def test_preview_server_choose_dir_and_custom_output_dir_author(self):
        """Weryfikuje POST /choose-dir (sukces i anulowanie) oraz POST /export z custom output_dir i author."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            md_path = tmp_path / "Podrecznik.md"
            md_path.write_text("# Podręcznik\n\nTreść.\n", encoding="utf-8")
            custom_dir = tmp_path / "CustomOut"
            custom_dir.mkdir()

            server, done_event, _ = self.export_pdf.create_preview_server(
                md_path=md_path,
                metadata={"title": "Podręcznik"},
                body_md="# Podręcznik\n\nTreść.\n",
                downloads_dir=tmp_path / "Documents",
            )
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                # 1. POST /choose-dir — sukces
                fake_ok = mock.Mock(returncode=0, stdout=f"{custom_dir}/\n")
                with mock.patch.object(self.export_pdf.subprocess, "run", return_value=fake_ok):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/choose-dir", data=b"{}", method="POST")
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(data["ok"])
                        self.assertEqual(data["dir"], str(custom_dir))

                # 2. POST /choose-dir — anulowanie przez użytkownika
                fake_cancel = mock.Mock(returncode=1, stdout="")
                with mock.patch.object(self.export_pdf.subprocess, "run", return_value=fake_cancel):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/choose-dir", data=b"{}", method="POST")
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertFalse(data["ok"])
                        self.assertTrue(data["cancelled"])

                # 3. POST /export z custom output_dir i author
                expected_pdf = custom_dir.resolve() / "Podrecznik.pdf"
                with mock.patch.object(self.export_pdf, "export_markdown_to_pdf", return_value=expected_pdf) as mock_exp:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/export",
                        data=json.dumps({
                            "layout": "book",
                            "template": "book-classic",
                            "page_size": "B5",
                            "author": "Jan Kowalski",
                            "output_dir": str(custom_dir),
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(data["ok"])
                        self.assertEqual(data["path"], str(expected_pdf))

                    kwargs = mock_exp.call_args.kwargs
                    self.assertEqual(kwargs["author"], "Jan Kowalski")
                    self.assertEqual(kwargs["output_pdf_path"], expected_pdf)
                    self.assertTrue(done_event.wait(timeout=2))
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=2)

    def test_keep_with_next_and_chapter_break_toggle(self):
        """Weryfikuje owijanie h2/h3 w .keep-with-next, chapter_break=True/False oraz checkbox w podglądzie."""
        md_body = "## Rozdział 1\n\nAkapit pierwszy.\n\n### Podrozdział 1.1\n\n- Punkt A\n- Punkt B\n"
        body_html = self.export_pdf.markdown_to_html(md_body, {}, include_meta_card=False, add_heading_ids=True)

        _, book_soft = self.export_pdf.prepare_book_content(body_html, {"title": "Książka"}, "Książka", chapter_break=False)
        self.assertIn('<main class="book-body">', book_soft)
        self.assertNotIn("chapter-breaks", book_soft)
        self.assertEqual(book_soft.count('<div class="keep-with-next">'), 2)
        self.assertIn('<div class="keep-with-next">\n<h2 id="rozdzia-1">Rozdział 1</h2>\n<p>Akapit pierwszy.</p>\n</div>', book_soft)

        _, book_hard = self.export_pdf.prepare_book_content(body_html, {"title": "Książka"}, "Książka", chapter_break=True)
        self.assertIn('<main class="book-body chapter-breaks">', book_hard)
        self.assertEqual(book_hard.count('<div class="keep-with-next">'), 2)

        preview_html = self.export_pdf.build_preview_html(
            md_path=Path("/tmp/test_cb.md"),
            metadata={"title": "Książka"},
            body_md=md_body,
            server_mode=True,
        )
        self.assertIn('id="pv-chapter-break"', preview_html)
        self.assertIn("Każdy rozdział od nowej strony", preview_html)
        self.assertIn(".keep-with-next {", preview_html)
        self.assertIn("orphans: 4;", preview_html)

    def test_preview_server_post_export_passes_chapter_break(self):
        """Weryfikuje przekazanie chapter_break=True przez POST /export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            md_path = tmp_path / "Rozdzialy.md"
            md_path.write_text("# Tytuł\n\n## R1\n\nTekst.\n", encoding="utf-8")
            docs_dir = tmp_path / "Documents"
            server, done_event, _ = self.export_pdf.create_preview_server(
                md_path=md_path,
                metadata={"title": "Tytuł"},
                body_md="# Tytuł\n\n## R1\n\nTekst.\n",
                downloads_dir=docs_dir,
            )
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                expected_pdf = docs_dir / "Rozdzialy.pdf"
                with mock.patch.object(self.export_pdf, "export_markdown_to_pdf", return_value=expected_pdf) as mock_exp:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/export",
                        data=json.dumps({
                            "layout": "book",
                            "template": "book-classic",
                            "page_size": "B5",
                            "chapter_break": True,
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(data["ok"])
                    self.assertTrue(mock_exp.call_args.kwargs["chapter_break"])
                    self.assertTrue(done_event.wait(timeout=2))
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
