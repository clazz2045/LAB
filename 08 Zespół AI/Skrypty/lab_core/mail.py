#!/usr/bin/env python3
"""mail_to_lab.py - Doraźny eksporter wiadomości z Apple Mail (Poczta) do LAB 3.0 (Skrzynka)

Standard inżynieryjny:
- 100% Python Standard Library (argparse, datetime, json, pathlib, re, subprocess, sys, urllib.parse).
- Zero zewnętrznych zależności pip.
- Oparte o core_config.ConfigManager (brak hardkodowanych ścieżek).
- Zgodne z The One Law oraz wytycznymi LAB 3.0.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
from typing import Any, Dict, Optional, Tuple

# Blokada tworzenia folderów __pycache__
sys.dont_write_bytecode = True

# Zaimportuj core_config z katalogu bieżącego lub nadrzędnych
_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigManager, ConfigError


JXA_FETCH_EMAIL_SCRIPT = """
(() => {
    try {
        const Mail = Application('Mail');
        if (!Mail.running()) {
            return JSON.stringify({status: 'MAIL_NOT_RUNNING'});
        }
        let sel = [];
        try {
            if (Mail.messageViewers.length > 0) {
                sel = Mail.messageViewers[0].selectedMessages();
            }
        } catch(e) {}
        if (!sel || sel.length === 0) {
            try {
                sel = Mail.selection();
            } catch(e) {}
        }
        if (!sel || sel.length === 0) {
            return JSON.stringify({status: 'NO_SELECTION'});
        }
        const m = sel[0];
        let dateStr = '';
        try {
            dateStr = m.dateReceived().toISOString();
        } catch(e) {}
        const attNames = [];
        try {
            const atts = m.mailAttachments();
            for (let i = 0; i < atts.length; i++) {
                attNames.push(atts[i].name());
            }
        } catch(e) {}
        return JSON.stringify({
            status: 'OK',
            subject: m.subject() || '',
            sender: m.sender() || '',
            messageId: m.messageId() || '',
            date: dateStr,
            attachments: attNames,
            content: m.content() || ''
        });
    } catch(err) {
        return JSON.stringify({status: 'ERROR', error: err.toString()});
    }
})();
"""


def fetch_selected_mail_jxa() -> Dict[str, Any]:
    """Pobiera dane zaznaczonego e-maila z Apple Mail za pomocą JXA."""
    res = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", JXA_FETCH_EMAIL_SCRIPT],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return {"status": "ERROR", "error": res.stderr.strip() or "Błąd wykonania JXA"}

    out = res.stdout.strip()
    if not out:
        return {"status": "ERROR", "error": "Pusta odpowiedź z AppleScript"}

    try:
        return json.loads(out)
    except Exception as exc:
        return {"status": "ERROR", "error": f"Błąd parsowania JSON: {exc}"}


def sanitize_filename(title: str, max_length: int = 60) -> str:
    """Czyści tekst na potrzeby bezpiecznej nazwy pliku w systemie macOS/Obsidian."""
    clean = re.sub(r'[\\/*?:"<>|#^\[\]]', " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        clean = "Bez_Tematu"
    clean = clean.replace(" ", "_")
    if len(clean) > max_length:
        clean = clean[:max_length].rstrip("_")
    return clean


def build_mail_deeplink(message_id: str) -> str:
    """Tworzy klikalny odsyłacz scheme message:// otwierający dany mail w Apple Mail."""
    clean_id = message_id.strip("<> \t\r\n")
    if not clean_id:
        return ""
    encoded = urllib.parse.quote(f"<{clean_id}>", safe="")
    return f"message://{encoded}"


def sanitize_mail_content(text: str) -> str:
    """Uniwersalna normalizacja typograficzna treści wiadomości e-mail.
    
    Eliminuje separatory Unicode (Line Separator \u2028, Paragraph Separator \u2029),
    twarde spacje (\u00a0), powroty karetek DOS/Mac (\r\n, \r) oraz porządkuje
    nadmiarowe puste linie, zapewniając idealne renderowanie Markdown w Obsidianie.
    """
    if not text:
        return ""
    # 1. Konwersja separatorów wierszy i akapitów Unicode
    text = text.replace("\u2028", "\n").replace("\u2029", "\n\n")
    # 2. Standaryzacja znaków nowej linii DOS/Mac
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 3. Zastąpienie twardych spacji zwykłą spacją
    text = text.replace("\u00a0", " ")
    # 4. Redukcja nadmiarowych pustych linii do estetycznego odstępu akapitowego
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_mail_markdown(data: Dict[str, Any]) -> Tuple[str, str]:
    """Formatuje dane wiadomości na estetyczny Markdown z metadanymi i nagłówkiem YAML.
    Zwraca krotkę: (markdown_text, safe_title).
    """
    subject = (data.get("subject") or "").strip() or "Wiadomość bez tematu"
    sender = (data.get("sender") or "").strip() or "Nieznany nadawca"
    raw_date = data.get("date") or ""
    message_id = data.get("messageId") or ""
    attachments = data.get("attachments") or []
    raw_content = data.get("content") or ""
    content = sanitize_mail_content(raw_content)

    # Sformatuj datę
    dt = None
    if raw_date:
        try:
            # ISO format np. 2026-11-05T14:15:00.000Z
            clean_iso = raw_date.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_iso)
        except Exception:
            dt = None
    if not dt:
        dt = datetime.datetime.now()

    date_str = dt.strftime("%Y-%m-%d %H:%M")
    day_str = dt.strftime("%Y-%m-%d")

    deeplink = build_mail_deeplink(message_id)

    att_list_str = "Brak"
    if attachments:
        att_list_str = ", ".join(f"`{a}`" for a in attachments)

    deeplink_line = f"- **Odsyłacz:** [📬 Otwórz wiadomość w Poczcie]({deeplink})" if deeplink else "- **Odsyłacz:** *(Brak identyfikatora Message-ID)*"

    safe_subject = subject.replace('"', '\\"')
    safe_sender = sender.replace('"', '\\"')

    md_lines = [
        "---",
        "type: email-capture",
        f'title: "{safe_subject}"',
        f'sender: "{safe_sender}"',
        f'date: {date_str}',
        f'message_id: "{message_id}"',
        f'mail_link: "{deeplink}"',
        "tags:",
        "  - email",
        "  - poczta",
        "  - skrzynka",
        "consumed: false",
        "---",
        "",
        f"# ✉️ {subject}",
        "",
        "> [!info] Metadane wiadomości",
        f"> - **Od:** {sender}",
        f"> - **Data:** {date_str}",
        f"> - **Załączniki:** {att_list_str}",
        f"> {deeplink_line}",
        "",
        "---",
        "",
        "## Treść wiadomości",
        "",
        content or "*(Wiadomość nie zawierała treści tekstowej)*",
        "",
    ]

    return "\n".join(md_lines), subject


def copy_to_clipboard(text: str) -> bool:
    """Kopiuje podany tekst do systemowego schowka macOS (pbcopy)."""
    try:
        proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return proc.returncode == 0
    except Exception:
        return False


def export_selected_mail_to_inbox(
    vault_root: Optional[Path] = None,
    clipboard_only: bool = False,
    mock_data: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Główna procedura eksportu e-maila do Skrzynki LAB."""
    # 1. Pobierz dane e-maila
    data = mock_data if mock_data is not None else fetch_selected_mail_jxa()

    status = data.get("status")
    if status == "MAIL_NOT_RUNNING":
        return False, "⚠️ Aplikacja Poczta (Apple Mail) nie jest uruchomiona."
    if status == "NO_SELECTION":
        return False, "⚠️ Zaznacz wiadomość w aplikacji Poczta (brak zaznaczenia)."
    if status != "OK":
        err_msg = data.get("error", "Nieznany błąd")
        return False, f"❌ Błąd odczytu e-maila: {err_msg}"

    # 2. Sformatuj Markdown
    md_content, subject = format_mail_markdown(data)

    # 3. Kopiuj do schowka
    copy_to_clipboard(md_content)

    if clipboard_only:
        return True, f"📋 Skopiowano do schowka: {subject}"

    # 4. Wyznacz ścieżkę do Skrzynki (01 Skrzynka)
    try:
        cfg = ConfigManager(vault_root=vault_root)
        inbox_room = cfg.get_room_by_role("ingestion")
        inbox_dir = (cfg.vault_root / inbox_room.path).resolve()
    except Exception:
        fallback_root = vault_root or Path.cwd()
        inbox_dir = fallback_root / "01 Skrzynka"

    inbox_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.date.today().isoformat()
    clean_subj = sanitize_filename(subject, max_length=50)
    target_filename = f"{today_str}_Mail_{clean_subj}.md"
    target_path = inbox_dir / target_filename

    # Unikaj kolizji nazw
    if target_path.exists():
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        target_filename = f"{today_str}_Mail_{clean_subj}_{timestamp}.md"
        target_path = inbox_dir / target_filename

    target_path.write_text(md_content, encoding="utf-8")
    return True, f"✅ Zapisano e-mail w Skrzynce: {subject}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Eksport e-maila z Apple Mail do Skrzynki LAB (PZW).")
    parser.add_argument("--clipboard-only", action="store_true", help="Tylko skopiuj do schowka, nie twórz pliku.")
    parser.add_argument("--vault-root", type=str, help="Ścieżka do skarbca LAB (opcjonalna).")
    args = parser.parse_args()

    v_root = Path(args.vault_root).resolve() if args.vault_root else None
    ok, message = export_selected_mail_to_inbox(vault_root=v_root, clipboard_only=args.clipboard_only)

    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
