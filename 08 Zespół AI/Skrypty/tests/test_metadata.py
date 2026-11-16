#!/usr/bin/env python3
"""test_metadata.py - Testy jednostkowe modułu metadata i narzędzia ingest_url w LAB 2.0
Standard: 100% Python Standard Library (unittest). Zero-Pip.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from lab_core.metadata import (
    SimpleHTMLMetaParser,
    _decode_json_string_fragment,
    _fetch_youtube_transcript_from_html,
    _fetch_youtube_transcript_innertube,
    _parse_timedtext_xml,
    extract_template_sections,
    extract_youtube_video_id,
    fetch_youtube_metadata,
    generate_reference_note_content,
    get_reference_template_path,
    main as metadata_main,
    sanitize_filename,
)


class TestMetadataExtraction(unittest.TestCase):
    def test_extract_youtube_video_id_standard(self):
        url = "https://www.youtube.com/watch?v=JOJ5zihcd6Q"
        self.assertEqual(extract_youtube_video_id(url), "JOJ5zihcd6Q")

    def test_extract_youtube_video_id_short_url(self):
        url = "https://youtu.be/JOJ5zihcd6Q?is=ObFk2YurPia3GxY"
        self.assertEqual(extract_youtube_video_id(url), "JOJ5zihcd6Q")

    def test_extract_youtube_video_id_shorts(self):
        url = "https://www.youtube.com/shorts/JOJ5zihcd6Q"
        self.assertEqual(extract_youtube_video_id(url), "JOJ5zihcd6Q")

    def test_extract_youtube_video_id_non_youtube(self):
        url = "https://arstechnica.com/science/2026/11/what-happens-to-mathematics/"
        self.assertIsNone(extract_youtube_video_id(url))

    def test_simple_html_meta_parser(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Przykładowy Tytuł Strony</title>
            <meta name="author" content="Jan Kowalski">
            <meta property="og:title" content="Tytuł OpenGraph">
            <meta property="og:description" content="Opis z OpenGraph dla testu.">
            <meta property="article:published_time" content="2026-11-10T12:00:00Z">
        </head>
        <body><p>Treść strony</p></body>
        </html>
        """
        parser = SimpleHTMLMetaParser()
        parser.feed(sample_html)
        self.assertEqual(parser.get_title(), "Przykładowy Tytuł Strony")
        self.assertEqual(parser.meta_tags.get("author"), "Jan Kowalski")
        self.assertEqual(parser.meta_tags.get("og:title"), "Tytuł OpenGraph")
        self.assertEqual(parser.meta_tags.get("og:description"), "Opis z OpenGraph dla testu.")
        self.assertEqual(parser.meta_tags.get("article:published_time"), "2026-11-10T12:00:00Z")

    def test_sanitize_filename(self):
        raw = 'Wideo: Czy to działa? / Test * [2026] <Super> | "Ekstra"'
        clean = sanitize_filename(raw)
        self.assertNotIn(":", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn("?", clean)
        self.assertNotIn("*", clean)
        self.assertNotIn("<", clean)
        self.assertNotIn(">", clean)
        self.assertNotIn("|", clean)
        self.assertNotIn('"', clean)

    def test_generate_reference_note_content_stage_raw(self):
        meta = {
            "title": "Quantum Entanglement Explained",
            "author": "Lukas's Lab",
            "url": "https://www.youtube.com/watch?v=JOJ5zihcd6Q",
            "is_youtube": True,
            "duration_formatted": "20m 41s",
            "duration_seconds": 1241,
            "description": "Have you ever wondered how we program a quantum computer?",
        }
        content = generate_reference_note_content(meta, stage="raw", created_date="2026-11-11")
        self.assertIn("type: note", content)
        self.assertIn("note_type: reference", content)
        self.assertIn("stage: raw", content)
        self.assertIn("consumed: false", content)
        self.assertIn("do-opracowania", content)
        self.assertIn("Lukas's Lab", content)
        self.assertIn("Quantum Entanglement Explained", content)
        self.assertIn("~20m 41s (1241 s)", content)
        self.assertIn("> [!info] Metadane źródłowe (Zweryfikowane maszynowo)", content)

    def test_main_dry_run_autodetect_vault(self):
        fake_meta = {
            "title": "Test CLI Autodetect",
            "author": "LAB Test",
            "url": "https://example.com/article",
            "is_youtube": False,
            "description": "Opis testowy",
        }
        stdout_buf = io.StringIO()
        with patch("sys.argv", ["lab_core.metadata", "https://example.com/article", "--dry-run", "--json"]), \
             patch("lab_core.metadata.fetch_resource_metadata", return_value=fake_meta), \
             redirect_stdout(stdout_buf):
            rc = metadata_main()

        self.assertEqual(rc, 0)
        self.assertIn("Test CLI Autodetect", stdout_buf.getvalue())

    def test_decode_json_string_fragment_utf8_no_mojibake(self):
        raw = r"\ud83d\udfe6 Kurs programowania dla nie-programist\u00f3w — żółć ✅"
        decoded = _decode_json_string_fragment(raw)
        self.assertIn("🟦", decoded)
        self.assertIn("nie-programistów", decoded)
        self.assertIn("żółć", decoded)
        self.assertNotIn("Ã³", decoded)
        self.assertNotIn("ðŸ", decoded)

    def test_parse_timedtext_xml_and_transcript_section(self):
        xml_sample = (
            '<transcript>'
            '<text start="0.0" dur="4.0">Pierwsze zdanie z napisów.</text>'
            '<text start="35.0" dur="4.0">Drugie zdanie po trzydziestu sekundach.</text>'
            '</transcript>'
        )
        parsed = _parse_timedtext_xml(xml_sample)
        self.assertIn("[00:00] Pierwsze zdanie z napisów.", parsed)
        self.assertIn("[00:35] Drugie zdanie po trzydziestu sekundach.", parsed)

        meta = {
            "title": "Test Video SAT",
            "author": "LAB",
            "url": "https://www.youtube.com/watch?v=abc123def45",
            "is_youtube": True,
            "transcript": parsed,
            "transcript_source": "youtube-timedtext (pl)",
        }
        content = generate_reference_note_content(meta, stage="raw", created_date="2026-11-13")
        self.assertIn("## 💡 Abstrakt i Główna Teza (Executive Summary)", content)
        self.assertIn("## 🔍 Dowody, Fakty i Szczegóły Techniczne", content)
        self.assertIn("## ⏱️ Chronologiczna Oś Czasu Materiału ([mm:ss])", content)
        self.assertIn("## 💡 Implikacje i Zastosowanie w Systemie LAB", content)
        self.assertIn("## 🔗 Powiązania i Przestrzenie Tematyczne", content)
        self.assertIn("## 🎙️ Transkrypcja Źródłowa (*źródło: youtube-timedtext (pl)*)", content)
        self.assertIn("[00:00] Pierwsze zdanie z napisów.", content)

        article_meta = {
            "title": "Test Artykuł SAT",
            "author": "LAB",
            "url": "https://example.com/art",
            "is_youtube": False,
            "description": "Krótka teza artykułu.",
        }
        article_content = generate_reference_note_content(article_meta, stage="distilled", created_date="2026-11-13")
        self.assertIn("## 💡 Główna Teza i Esencja", article_content)
        self.assertIn("## 🔍 Szczegóły i Przebieg Rozumowania", article_content)
        self.assertIn("## 🚀 Implikacje i Wnioski", article_content)
        self.assertIn("## 🔗 Powiązania w Skarbcu", article_content)

    def test_fetch_youtube_transcript_from_html_mocked(self):
        html_stub = (
            '<html><script>var ytInitialPlayerResponse = {"captions":{"playerCaptionsTracklistRenderer":'
            '{"captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?v=abc\\u0026lang=pl",'
            '"languageCode":"pl"}]}}};</script></html>'
        )
        xml_bytes = b'<timedtext format="3"><body><p t="5000" d="2000">Testowy napis z YouTube.</p></body></timedtext>'

        class _FakeResp:
            def __init__(self, data: bytes) -> None:
                self._data = data
            def read(self) -> bytes:
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=_FakeResp(xml_bytes)):
            transcript, src = _fetch_youtube_transcript_from_html(html_stub)

        self.assertIn("[00:05] Testowy napis z YouTube.", transcript)
        self.assertEqual(src, "youtube-timedtext (pl)")

    def test_template_helpers_and_yaml_escaping(self):
        video_tpl = get_reference_template_path(is_youtube=True)
        article_tpl = get_reference_template_path(is_youtube=False)
        self.assertTrue(video_tpl.exists())
        self.assertTrue(article_tpl.exists())

        video_secs = extract_template_sections(video_tpl)
        article_secs = extract_template_sections(article_tpl)
        self.assertEqual(len(video_secs), 5)
        self.assertEqual(len(article_secs), 4)

        meta = {
            "title": 'Test "Quoted" Title',
            "author": 'Jan "Tester" Kowalski',
            "url": "https://example.com/art",
            "is_youtube": False,
        }
        rendered = generate_reference_note_content(meta, stage="distilled", created_date="2026-11-13")
        self.assertIn('title: "Test \\"Quoted\\" Title"', rendered)
        self.assertIn('author: "Jan \\"Tester\\" Kowalski"', rendered)
        self.assertIn("updated: 2026-11-13", rendered)
        self.assertNotIn("06 Zasoby/Notatki/_Indeksy", rendered)

        rendered_body = generate_reference_note_content(
            meta, stage="distilled", body_text="## Custom Body\nTreść właściwa.", created_date="2026-11-13"
        )
        self.assertIn("updated: 2026-11-13", rendered_body)
        self.assertIn("## Custom Body", rendered_body)

    def test_fetch_youtube_transcript_innertube_mocked(self):
        html_stub = '<html><script>ytcfg.set({"INNERTUBE_API_KEY":"fake_key_123"});</script></html>'
        player_json = (
            b'{"captions":{"playerCaptionsTracklistRenderer":{"captionTracks":['
            b'{"baseUrl":"https://www.youtube.com/api/timedtext?v=abc&lang=pl","languageCode":"pl","kind":"asr"}'
            b']}}}'
        )
        xml_bytes = b'<timedtext format="3"><body><p t="12000" d="2000">Wypowied\xc5\xba testowa InnerTube.</p></body></timedtext>'

        class _FakeResp:
            def __init__(self, data: bytes) -> None:
                self._data = data
            def read(self) -> bytes:
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        responses = [_FakeResp(player_json), _FakeResp(xml_bytes)]
        with patch("urllib.request.urlopen", side_effect=responses):
            transcript, src = _fetch_youtube_transcript_innertube("abc123def45", html_stub)

        self.assertIn("[00:12] Wypowiedź testowa InnerTube.", transcript)
        self.assertEqual(src, "youtube-innertube (pl)")

    def test_fetch_youtube_metadata_explicit_transcript_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("network down")), \
             patch("lab_core.metadata._fetch_youtube_transcript_ytdlp", return_value=("", "")):
            meta = fetch_youtube_metadata("https://www.youtube.com/watch?v=8gxemC90Dco")

        self.assertEqual(meta["transcript"], "")
        self.assertIn("youtube-page error: network down", meta["transcript_error"])


if __name__ == "__main__":
    unittest.main()
