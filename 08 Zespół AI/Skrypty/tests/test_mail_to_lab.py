#!/usr/bin/env python3
"""test_mail_to_lab.py - Testy jednostkowe modułu mail_to_lab.py (The One Law)
Standard: 100% Python Standard Library (unittest). Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import datetime
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

# Blokada tworzenia folderów __pycache__
sys.dont_write_bytecode = True

# Zaimportuj moduły
_script_dir = Path(__file__).resolve().parent.parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from lab_core.mail import (
    build_mail_deeplink,
    export_selected_mail_to_inbox,
    format_mail_markdown,
    sanitize_filename,
)


class TestMailToLab(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.mock_vault = Path(self.tmp_dir.name)

        # Skopiuj lab.config.toml do mock skarbca
        canonical_toml = Path("lab.config.toml")
        if canonical_toml.is_file():
            shutil.copy(canonical_toml, self.mock_vault / "lab.config.toml")
        (self.mock_vault / "01 Skrzynka").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename("Faktura za prąd: E.ON / Stoen?"), "Faktura_za_prąd_E.ON_Stoen")
        self.assertEqual(sanitize_filename("   "), "Bez_Tematu")
        long_title = "A" * 100
        self.assertEqual(len(sanitize_filename(long_title, max_length=40)), 40)

    def test_build_mail_deeplink(self) -> None:
        link = build_mail_deeplink("<abc123xyz@mail.domain.com>")
        self.assertTrue(link.startswith("message://"))
        self.assertIn("abc123xyz", link)

        empty_link = build_mail_deeplink("")
        self.assertEqual(empty_link, "")

    def test_format_mail_markdown(self) -> None:
        data = {
            "subject": "Nowe rozliczenie za gaz",
            "sender": "BOK PGNiG <bok@pgnig.pl>",
            "date": "2026-11-05T12:30:00.000Z",
            "messageId": "<pgnig-98765@mailer.pl>",
            "attachments": ["faktura.pdf", "regulamin.pdf"],
            "content": "Dzień dobry, przesyłamy fakturę.",
        }
        md_text, title = format_mail_markdown(data)
        self.assertEqual(title, "Nowe rozliczenie za gaz")
        self.assertIn("type: email-capture", md_text)
        self.assertIn("consumed: false", md_text)
        self.assertIn("BOK PGNiG", md_text)
        self.assertIn("faktura.pdf", md_text)
        self.assertIn("message://", md_text)
        self.assertIn("Dzień dobry, przesyłamy fakturę.", md_text)

    def test_export_mail_not_running(self) -> None:
        mock = {"status": "MAIL_NOT_RUNNING"}
        ok, msg = export_selected_mail_to_inbox(vault_root=self.mock_vault, mock_data=mock)
        self.assertFalse(ok)
        self.assertIn("nie jest uruchomiona", msg)

    def test_export_no_selection(self) -> None:
        mock = {"status": "NO_SELECTION"}
        ok, msg = export_selected_mail_to_inbox(vault_root=self.mock_vault, mock_data=mock)
        self.assertFalse(ok)
        self.assertIn("Zaznacz wiadomość", msg)

    def test_export_success_creates_file_in_inbox(self) -> None:
        mock = {
            "status": "OK",
            "subject": "Oferta Ubezpieczenia TUZ 2027",
            "sender": "Agent TUZ <agent@tuz.pl>",
            "date": "2026-11-05T10:00:00Z",
            "messageId": "<tuz-12345@tuz.pl>",
            "attachments": ["polisa.pdf"],
            "content": "Szanowny Panie, przesyłam polisę do akceptacji.",
        }
        ok, msg = export_selected_mail_to_inbox(vault_root=self.mock_vault, mock_data=mock)
        self.assertTrue(ok)
        self.assertIn("Zapisano e-mail w Skrzynce", msg)

        inbox_dir = self.mock_vault / "01 Skrzynka"
        created_files = list(inbox_dir.glob("*_Mail_Oferta_Ubezpieczenia_TUZ_2027*.md"))
        self.assertEqual(len(created_files), 1)

        content = created_files[0].read_text(encoding="utf-8")
        self.assertIn("Oferta Ubezpieczenia TUZ 2027", content)
        self.assertIn("consumed: false", content)

    def test_export_collision_handles_unique_timestamp(self) -> None:
        mock = {
            "status": "OK",
            "subject": "Test Kolizji",
            "sender": "Test <test@test.pl>",
            "date": "2026-11-05T10:00:00Z",
            "messageId": "<msg1@test.pl>",
            "attachments": [],
            "content": "Pierwsza wiadomość.",
        }
        ok1, _ = export_selected_mail_to_inbox(vault_root=self.mock_vault, mock_data=mock)
        self.assertTrue(ok1)

        # Kolejny zapis z tym samym tematem nie nadpisuje pliku, lecz tworzy wariant ze stemplem czasu
        ok2, _ = export_selected_mail_to_inbox(vault_root=self.mock_vault, mock_data=mock)
        self.assertTrue(ok2)

        inbox_dir = self.mock_vault / "01 Skrzynka"
        created = list(inbox_dir.glob("*_Mail_Test_Kolizji*.md"))
        self.assertGreaterEqual(len(created), 2)

    def test_export_clipboard_only_does_not_create_file(self) -> None:
        mock = {
            "status": "OK",
            "subject": "Tylko Do Schowka",
            "sender": "Test <test@test.pl>",
            "date": "2026-11-05T10:00:00Z",
            "messageId": "<msg-clip@test.pl>",
            "attachments": [],
            "content": "Treść do schowka.",
        }
        ok, msg = export_selected_mail_to_inbox(vault_root=self.mock_vault, clipboard_only=True, mock_data=mock)
        self.assertTrue(ok)
        self.assertIn("Skopiowano do schowka", msg)

        inbox_dir = self.mock_vault / "01 Skrzynka"
        created = list(inbox_dir.glob("*_Mail_Tylko_Do_Schowka*.md"))
        self.assertEqual(len(created), 0)


if __name__ == "__main__":
    unittest.main()

    def test_sanitize_mail_content_unicode_separators(self) -> None:
        from lab_core.mail import sanitize_mail_content
        raw = "Linia 1\u2028Linia 2\u2029Akapit 2\r\nDOS Linia\rMac Linia\u00a0ze spacją\n\n\n\nDużo enterów"
        cleaned = sanitize_mail_content(raw)
        self.assertNotIn("\u2028", cleaned)
        self.assertNotIn("\u2029", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\u00a0", cleaned)
        self.assertNotIn("\n\n\n", cleaned)
        self.assertIn("Linia 1\nLinia 2", cleaned)
        self.assertIn("Akapit 2\nDOS Linia", cleaned)
        self.assertIn("Mac Linia ze spacją", cleaned)
