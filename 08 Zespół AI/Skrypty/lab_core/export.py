#!/usr/bin/env python3
"""export_pdf.py - Wydawniczy eksporter notatek Markdown do PDF dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (re, html, pathlib, argparse, subprocess, tempfile).
- Rendering silnikiem headless Google Chrome / Chromium na macOS.
- Typografia wydawnicza wg wytycznych Mai (SF Pro / System Sans, callouty, tabele, metadane).
- Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional, Tuple

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

PRINT_THEMES_DIR = Path(__file__).resolve().parent / "print_themes"
BOOK_THEMES = ("book-classic", "swiss", "editorial", "manuscript")
VALID_LAYOUTS = ("note", "book")
VALID_PAGE_SIZES = ("A4", "B5", "A5")
VALID_TEMPLATES = ("note", "textbook", *BOOK_THEMES)
_LAB_PREFIX_RE = re.compile(r"^(?:RS|PR|OP|SOP|WY|AT|MOC|KB)-\d+(?:\.\d+)*\s*[:\-–—]?\s*")


def find_browser_engine() -> Optional[str]:
    """Wyszukuje dostępną przeglądarkę Chromium do renderowania PDF w trybie headless."""
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Wyodrębnia słownik metadanych frontmatteru oraz właściwą treść notatki."""
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines(keepends=True)
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break

    if closing_idx == -1:
        return {}, text

    fm_lines = lines[1:closing_idx]
    body = "".join(lines[closing_idx + 1:])

    meta = {}
    current_list_key: Optional[str] = None
    for raw_line in fm_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip().strip("\"'")
            if item:
                existing = meta.get(current_list_key, "")
                meta[current_list_key] = f"{existing}, {item}" if existing else item
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip()
            v = v.strip().strip("\"'[]")
            meta[k] = v
            current_list_key = k if not v else None
        else:
            current_list_key = None

    return meta, body


def clean_lab_title(title: str) -> str:
    """Usuwa wewnętrzne prefiksy dokumentów (np. XX-000.00) z tytułu publikacji."""
    cleaned = _LAB_PREFIX_RE.sub("", title.strip()).strip()
    return cleaned or title.strip()


def resolve_layout(metadata: dict, cli_layout: Optional[str] = None) -> str:
    """Rozstrzyga układ dokumentu (note lub book) z CLI lub YAML frontmatter."""
    if cli_layout is not None:
        return cli_layout
    fm_layout = str(metadata.get("print_layout", "")).strip().lower()
    if fm_layout in VALID_LAYOUTS:
        return fm_layout
    return "note"


def resolve_template(
    metadata: dict,
    cli_template: Optional[str] = None,
    layout: str = "note",
) -> str:
    """Rozstrzyga profil wydawniczy na podstawie flagi CLI, układu oraz YAML frontmatter."""
    if cli_template is not None:
        return cli_template
    pt = str(metadata.get("print_template", "")).strip().lower()
    if pt in VALID_TEMPLATES:
        return pt
    cssclasses_raw = str(metadata.get("cssclasses", ""))
    cssclasses = [c.strip().strip("\"'").lower() for c in cssclasses_raw.split(",") if c.strip()]
    for candidate in ("textbook", *BOOK_THEMES):
        if candidate in cssclasses:
            return candidate
    if layout == "book":
        return "book-classic"
    return "note"


def resolve_page_size(
    layout: str,
    cli_page_size: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Rozstrzyga rozmiar strony (A4, B5, A5) na podstawie CLI, frontmattera i układu."""
    if cli_page_size is not None:
        return cli_page_size
    if metadata:
        fm_size = str(metadata.get("print_page_size", "")).strip().upper()
        if fm_size in VALID_PAGE_SIZES:
            return fm_size
    return "B5" if layout == "book" else "A4"


def parse_markdown(md_text: str, metadata: Optional[dict] = None) -> str:
    """Kompatybilny wrapper parsera Markdown do HTML."""
    return markdown_to_html(md_text, metadata or {})


def build_full_html(body_html: str, title: str, template: str = "note") -> str:
    """Kompatybilny wrapper generatora pełnego HTML."""
    return generate_full_html(title, body_html, template=template)


def _apply_emphasis(escaped_text: str) -> str:
    """Konwertuje **pogrubienie** i *kursywę* na już zeskejpowanym tekście."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped_text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _render_inline(raw_text: str) -> str:
    """Konwertuje formatowanie inline Markdown (kod, linki, **bold**, *italic*) na HTML."""
    placeholders: List[str] = []

    def _store(html_snippet: str) -> str:
        idx = len(placeholders)
        placeholders.append(html_snippet)
        return f"@@LABINL{idx}@@"

    # 1. Ochrona kodu inline `...`
    text = re.sub(
        r"`([^`]+)`",
        lambda m: _store(f"<code>{html.escape(m.group(1))}</code>"),
        raw_text,
    )

    # 2. Linki Markdown [etykieta](url) z obsługą *...* / **...** w etykiecie
    def _replace_link(match: re.Match) -> str:
        label = _apply_emphasis(html.escape(match.group(1)))
        url = html.escape(match.group(2).strip(), quote=True)
        return _store(f'<a href="{url}">{label}</a>')

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _replace_link, text)

    # 3. Eskejpowanie reszty tekstu + pogrubienie i kursywa
    text = _apply_emphasis(html.escape(text))

    # 4. Przywrócenie placeholderów w odwrotnej kolejności (obsługa zagnieżdżeń np. kod w linku)
    for idx in range(len(placeholders) - 1, -1, -1):
        text = text.replace(f"@@LABINL{idx}@@", placeholders[idx])
    return text


def _slugify_heading(text: str, used_ids: set) -> str:
    """Tworzy unikalny identyfikator HTML dla nagłówka."""
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    plain = re.sub(r"[*_`]", "", plain)
    normalized = unicodedata.normalize("NFKD", plain)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "sec"
    candidate = slug
    idx = 2
    while candidate in used_ids:
        candidate = f"{slug}-{idx}"
        idx += 1
    used_ids.add(candidate)
    return candidate


def markdown_to_html(
    md_text: str,
    metadata: dict,
    include_meta_card: bool = True,
    add_heading_ids: bool = False,
) -> str:
    """Konwertuje treść Markdown na semantyczny kod HTML z calloutami i tabelami."""
    used_heading_ids: set = set()
    # Oczyszczenie wikilinków: [[Cel|Etykieta]] -> Etykieta, [[Cel]] -> Cel
    md_text = re.sub(r"\[\[([^|\]\n]+)\|([^\]\n]+)\]\]", r"\2", md_text)
    md_text = re.sub(r"\[\[([^\]\n]+)\]\]", r"\1", md_text)

    lines = md_text.splitlines()
    html_lines: List[str] = []

    in_code_block = False
    code_block_lang = ""
    code_block_lines: List[str] = []

    in_table = False
    table_rows: List[str] = []

    in_callout = False
    callout_type = "note"
    callout_lines: List[str] = []

    in_ol = False
    ol_items: List[str] = []

    in_ul = False
    ul_items: List[str] = []

    in_bq = False
    bq_lines: List[str] = []

    def flush_table():
        nonlocal in_table, table_rows
        if not in_table:
            return
        if not table_rows:
            in_table = False
            return

        out = ['<table class="report-table">']
        # Nagłówek
        headers = [c.strip() for c in table_rows[0].strip("|").split("|")]
        out.append("<thead><tr>")
        for h in headers:
            out.append(f"<th>{_render_inline(h)}</th>")
        out.append("</tr></thead><tbody>")

        # Ciało tabeli (pomijamy wiersz separatora ---|---)
        for row in table_rows[2:] if len(table_rows) > 1 and "---" in table_rows[1] else table_rows[1:]:
            cols = [c.strip() for c in row.strip("|").split("|")]
            out.append("<tr>")
            for c in cols:
                out.append(f"<td>{_render_inline(c)}</td>")
            out.append("</tr>")

        out.append("</tbody></table>")
        html_lines.append("\n".join(out))
        table_rows = []
        in_table = False

    def flush_callout():
        nonlocal in_callout, callout_type, callout_lines
        if not in_callout:
            return
        content_html = "<br>".join(callout_lines)
        html_lines.append(
            f'<div class="callout callout-{callout_type}">'
            f'<div class="callout-header">{callout_type.upper()}</div>'
            f'{content_html}</div>'
        )
        callout_lines = []
        in_callout = False

    def flush_ol():
        nonlocal in_ol, ol_items
        if not in_ol or not ol_items:
            in_ol = False
            ol_items = []
            return
        out = ["<ol>"]
        for it in ol_items:
            out.append(f"  <li>{it}</li>")
        out.append("</ol>")
        html_lines.append("\n".join(out))
        ol_items = []
        in_ol = False

    def flush_ul():
        nonlocal in_ul, ul_items
        if not in_ul or not ul_items:
            in_ul = False
            ul_items = []
            return
        out = ["<ul>"]
        for it in ul_items:
            out.append(f"  <li>{it}</li>")
        out.append("</ul>")
        html_lines.append("\n".join(out))
        ul_items = []
        in_ul = False

    def flush_bq():
        nonlocal in_bq, bq_lines
        if not in_bq or not bq_lines:
            in_bq = False
            bq_lines = []
            return
        content = "<br>".join(bq_lines)
        html_lines.append(f"<blockquote><p>{content}</p></blockquote>")
        bq_lines = []
        in_bq = False

    def flush_all():
        flush_table()
        flush_callout()
        flush_ol()
        flush_ul()
        flush_bq()

    i = 0
    while i < len(lines):
        line = lines[i]

        # 1. Bloki kodu
        if line.startswith("```"):
            if in_code_block:
                in_code_block = False
                escaped_code = html.escape("\n".join(code_block_lines))
                if code_block_lang.lower() == "mermaid":
                    html_lines.append(f'<div class="mermaid">\n{escaped_code}\n</div>')
                else:
                    html_lines.append(f'<pre><code class="language-{code_block_lang}">{escaped_code}</code></pre>')
                code_block_lines = []
            else:
                flush_all()
                in_code_block = True
                code_block_lang = line[3:].strip()
            i += 1
            continue

        if in_code_block:
            code_block_lines.append(line)
            i += 1
            continue

        # 2. Callouty Obsidian: > [!typ]
        callout_match = re.match(r"^>\s*\[!([a-zA-Z0-9_\-]+)\](.*)", line)
        if callout_match:
            flush_all()
            in_callout = True
            callout_type = callout_match.group(1).lower()
            initial_text = callout_match.group(2).strip()
            if initial_text:
                callout_lines.append(_render_inline(initial_text))
            i += 1
            continue

        if in_callout:
            if line.startswith(">"):
                callout_lines.append(_render_inline(line[1:].strip()))
                i += 1
                continue
            else:
                flush_callout()

        # 3. Tabele Markdown: | ... |
        if line.strip().startswith("|") and line.strip().endswith("|"):
            flush_ol()
            flush_ul()
            flush_bq()
            in_table = True
            table_rows.append(line.strip())
            i += 1
            continue
        elif in_table:
            flush_table()

        # 4. Cytaty (> ...)
        if line.startswith(">"):
            flush_table()
            flush_ol()
            flush_ul()
            in_bq = True
            bq_lines.append(_render_inline(line[1:].strip()))
            i += 1
            continue
        elif in_bq:
            flush_bq()

        # 5. Listy numerowane: 1. 2. 3.
        ol_match = re.match(r"^\s*(\d+)\.\s+(.*)", line)
        if ol_match:
            flush_table()
            flush_ul()
            flush_bq()
            in_ol = True
            ol_items.append(_render_inline(ol_match.group(2).strip()))
            i += 1
            continue
        elif in_ol:
            flush_ol()

        # 6. Listy nienumerowane: - lub *
        ul_match = re.match(r"^\s*[-*]\s+(.*)", line)
        if ul_match:
            flush_table()
            flush_ol()
            flush_bq()
            in_ul = True
            ul_items.append(_render_inline(ul_match.group(1).strip()))
            i += 1
            continue
        elif in_ul:
            flush_ul()

        # 7. Nagłówki i separatory
        heading_match = re.match(r"^(#{1,4})\s+(.*)", line)
        if heading_match:
            flush_all()
            level = len(heading_match.group(1))
            raw_heading = heading_match.group(2).strip()
            rendered_heading = _render_inline(raw_heading)
            if add_heading_ids:
                hid = _slugify_heading(raw_heading, used_heading_ids)
                html_lines.append(f'<h{level} id="{hid}">{rendered_heading}</h{level}>')
            else:
                html_lines.append(f"<h{level}>{rendered_heading}</h{level}>")
        elif line.strip() in {"---", "***", "___"}:
            flush_all()
            html_lines.append("<hr>")
        elif line.strip():
            flush_all()
            html_lines.append(f"<p>{_render_inline(line.strip())}</p>")

        i += 1

    flush_all()

    # Karta metadanych jeśli frontmatter istnieje
    meta_html = ""
    if include_meta_card and metadata:
        meta_items = "".join(f"<span><strong>{k}:</strong> {html.escape(v)}</span>" for k, v in metadata.items() if k in {"type", "title", "author", "created", "status", "project"})
        if meta_items:
            meta_html = f'<div class="meta-card">{meta_items}</div>'

    body_content = "\n".join(html_lines)
    return meta_html + body_content


NOTE_CSS = """  @page {
    size: A4;
    margin: 18mm 16mm 18mm 16mm;
  }
  *, *:before, *:after {
    box-sizing: border-box;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
    line-height: 1.6;
    color: #1e293b;
    font-size: 13.5px;
    background: #fff;
    margin: 0;
    padding: 0;
  }
  .meta-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 20px;
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    font-size: 11.5px;
    color: #64748b;
  }
  .meta-card span strong {
    color: #334155;
  }
  h1 {
    font-size: 20pt;
    color: #0f172a;
    font-weight: 800;
    margin: 0 0 14px 0;
    letter-spacing: -0.3px;
    border: none;
    page-break-after: avoid;
    break-after: avoid;
  }
  h2 {
    font-size: 14.5pt;
    color: #0f172a;
    font-weight: 700;
    border: none;
    margin-top: 26px;
    margin-bottom: 10px;
    page-break-after: avoid;
    break-after: avoid;
  }
  h3 {
    font-size: 12pt;
    color: #1e293b;
    font-weight: 700;
    margin-top: 18px;
    margin-bottom: 6px;
    page-break-after: avoid;
    break-after: avoid;
  }
  hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 24px 0;
    height: 0;
  }
  p {
    margin: 8px 0;
    line-height: 1.6;
    color: #1e293b;
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11.5px;
    background: #f1f5f9;
    padding: 2px 5px;
    border-radius: 4px;
    border: 1px solid #e2e8f0;
    color: #0f172a;
  }
  pre {
    background: #0f172a;
    color: #f8fafc;
    padding: 12px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 11px;
    page-break-inside: avoid;
  }
  pre code {
    background: none;
    color: inherit;
    border: none;
    padding: 0;
  }
  blockquote {
    background-color: #f8fafc;
    border: none;
    border-left: 4px solid #0284c7;
    border-radius: 4px;
    padding: 10px 16px;
    margin: 16px 0;
    color: #334155;
    font-style: normal;
    page-break-inside: avoid;
    break-inside: avoid;
  }
  blockquote p {
    margin: 4px 0;
    color: #334155;
    line-height: 1.6;
  }
  ol, ul {
    padding-left: 24px;
    margin: 12px 0;
  }
  li {
    margin-bottom: 8px;
    line-height: 1.55;
    color: #1e293b;
  }
  .callout {
    padding: 10px 14px;
    border-radius: 6px;
    margin: 12px 0;
    page-break-inside: avoid;
    font-size: 12.5px;
  }
  .callout-header {
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }
  .callout-note, .callout-info {
    background: #eff6ff;
    border-left: 4px solid #2563eb;
    color: #1e3a8a;
  }
  .callout-tip {
    background: #f0fdf4;
    border-left: 4px solid #16a34a;
    color: #14532d;
  }
  .callout-warning {
    background: #fef2f2;
    border-left: 4px solid #dc2626;
    color: #7f1d1d;
  }
  .callout-important {
    background: #fffbeb;
    border-left: 4px solid #d97706;
    color: #78350f;
  }
  .report-table {
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 12px;
    page-break-inside: avoid;
  }
  .report-table th {
    background: #f8fafc;
    color: #0f172a;
    font-weight: 700;
    text-align: left;
    padding: 7px 9px;
    border: 1px solid #cbd5e1;
  }
  .report-table td {
    padding: 6px 9px;
    border: 1px solid #cbd5e1;
  }
  .report-table tr:nth-child(even) {
    background: #f8fafc;
  }
  hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 18px 0;
  }"""

TEXTBOOK_CSS = """@page {
  size: A4;
  margin: 22mm 20mm 24mm 24mm;
  @bottom-center {
    content: "— " counter(page) " —";
    font-family: "Charter", "Georgia", serif;
    font-size: 9pt;
    color: #64748b;
  }
}
@page :first {
  @bottom-center {
    content: none;
  }
}
*, *:before, *:after {
  box-sizing: border-box;
}
body {
  font-family: "Charter", "Iowan Old Style", "Palatino", "Georgia", "Times New Roman", serif;
  line-height: 1.62;
  color: #111827;
  font-size: 10.5pt;
  background: #ffffff;
  margin: 0;
  padding: 0;
}
.meta-card {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-left: 3px solid #1e3a8a;
  padding: 8px 12px;
  margin-bottom: 22px;
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 9pt;
  color: #475569;
}
.meta-card span strong {
  color: #0f172a;
}
h1, h2, h3, h4 {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  color: #0f172a;
  break-after: avoid;
  page-break-after: avoid;
}
h1 {
  font-size: 20pt;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin: 0 0 14px 0;
  padding-bottom: 6px;
  border-bottom: 2px solid #0f172a;
}
h1:not(:first-of-type) {
  break-before: page;
  page-break-before: always;
}
h2 {
  font-size: 14pt;
  font-weight: 700;
  margin: 24px 0 8px 0;
  padding-bottom: 4px;
  border-bottom: 1px solid #cbd5e1;
}
h3 {
  font-size: 11.5pt;
  font-weight: 700;
  color: #1e293b;
  margin: 18px 0 6px 0;
}
p {
  margin: 0 0 9pt 0;
  text-align: justify;
  hyphens: auto;
  -webkit-hyphens: auto;
  orphans: 3;
  widows: 3;
}
ol, ul {
  padding-left: 20pt;
  margin: 8pt 0 12pt 0;
}
li {
  margin-bottom: 5pt;
  text-align: justify;
  hyphens: auto;
  -webkit-hyphens: auto;
}
blockquote {
  font-style: italic;
  color: #334155;
  border-left: 3px solid #1e3a8a;
  padding: 2pt 0 2pt 12pt;
  margin: 12pt 0;
  break-inside: avoid;
  page-break-inside: avoid;
}
blockquote p {
  margin: 3pt 0;
}
code {
  font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 8.8pt;
  background: #f1f5f9;
  padding: 1pt 4pt;
  border-radius: 3px;
  border: 1px solid #e2e8f0;
  color: #0f172a;
}
pre {
  font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 8.5pt;
  line-height: 1.45;
  background: #f8fafc;
  color: #0f172a;
  padding: 10pt 12pt;
  border: 1px solid #cbd5e1;
  border-left: 3px solid #64748b;
  border-radius: 3px;
  overflow-x: auto;
  break-inside: avoid;
  page-break-inside: avoid;
}
pre code {
  background: none;
  border: none;
  padding: 0;
}
.callout {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-size: 9.5pt;
  line-height: 1.5;
  padding: 9pt 12pt;
  border: 1px solid #cbd5e1;
  border-radius: 3px;
  margin: 12pt 0;
  break-inside: avoid;
  page-break-inside: avoid;
}
.callout-header {
  font-weight: 700;
  font-size: 8.5pt;
  letter-spacing: 0.4px;
  margin-bottom: 4pt;
}
.callout-note, .callout-info { background: #f8fafc; border-left: 4px solid #1e3a8a; color: #1e293b; }
.callout-tip { background: #f0fdf4; border-left: 4px solid #15803d; color: #14532d; }
.callout-warning { background: #fef2f2; border-left: 4px solid #b91c1c; color: #7f1d1d; }
.callout-important { background: #fffbeb; border-left: 4px solid #b45309; color: #78350f; }
.report-table {
  width: 100%;
  border-collapse: collapse;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-size: 9pt;
  margin: 14pt 0;
  border-top: 2px solid #0f172a;
  border-bottom: 2px solid #0f172a;
  break-inside: avoid;
  page-break-inside: avoid;
}
.report-table th {
  font-weight: 700;
  text-align: left;
  color: #0f172a;
  padding: 6pt 8pt;
  border-bottom: 1px solid #475569;
}
.report-table td {
  padding: 5pt 8pt;
  border-bottom: 1px solid #e2e8f0;
  vertical-align: top;
}
.report-table tr:last-child td {
  border-bottom: none;
}
hr {
  border: none;
  text-align: center;
  margin: 18pt 0;
  height: 12pt;
}
hr::after {
  content: "§";
  color: #94a3b8;
  font-size: 11pt;
}"""

TEMPLATES_CSS = {
    "note": NOTE_CSS,
    "textbook": TEXTBOOK_CSS,
}


def _load_book_theme_css(theme_name: str, page_size: str, running_title: str) -> str:
    """Ładuje _base_book.css oraz wskazany motyw z print_themes/ i dokleja reguły @page."""
    resolved_theme = "book-classic" if theme_name in ("note", "textbook") else theme_name
    if resolved_theme not in BOOK_THEMES:
        raise ValueError(f"Nieznany motyw książkowy: {theme_name!r}. Dozwolone: {BOOK_THEMES}")

    base_css = (PRINT_THEMES_DIR / "_base_book.css").read_text(encoding="utf-8")
    theme_css = (PRINT_THEMES_DIR / f"{resolved_theme}.css").read_text(encoding="utf-8")

    dynamic_parts = [f"@page {{\n  size: {page_size};\n}}"]
    if running_title:
        safe_title = running_title.replace("\\", "\\\\").replace('"', '\\"')
        dynamic_parts.append(
            f'@page :left {{\n  @top-left {{\n    content: "{safe_title}";\n'
            f'    font-family: var(--font-ui);\n    font-size: 8pt;\n    color: var(--color-muted);\n  }}\n}}'
        )
        dynamic_parts.append(
            f'@page :right {{\n  @top-right {{\n    content: "{safe_title}";\n'
            f'    font-family: var(--font-ui);\n    font-size: 8pt;\n    color: var(--color-muted);\n  }}\n}}'
        )

    return f"{base_css}\n{theme_css}\n" + "\n".join(dynamic_parts)


def _split_top_level_blocks(body_html: str) -> List[str]:
    """Dzieli wygenerowany HTML na listę bloków najwyższego poziomu."""
    blocks: List[str] = []
    n = len(body_html)
    pos = 0
    tag_re = re.compile(r"<(h[1-4]|p|ol|ul|blockquote|table|pre|div|hr)\b[^>]*>", re.I)

    while pos < n:
        m = tag_re.search(body_html, pos)
        if not m:
            tail = body_html[pos:].strip()
            if tail:
                blocks.append(tail)
            break
        prefix = body_html[pos:m.start()].strip()
        if prefix:
            blocks.append(prefix)

        tag = m.group(1).lower()
        if tag == "hr":
            blocks.append(m.group(0))
            pos = m.end()
            continue

        if tag == "div":
            depth = 1
            scan = m.end()
            div_re = re.compile(r"<div\b[^>]*>|</div>", re.I)
            while depth > 0:
                dm = div_re.search(body_html, scan)
                if not dm:
                    scan = n
                    break
                if dm.group(0).lower().startswith("</div"):
                    depth -= 1
                else:
                    depth += 1
                scan = dm.end()
            blocks.append(body_html[m.start():scan])
            pos = scan
            continue

        close_re = re.compile(rf"</{tag}>", re.I)
        cm = close_re.search(body_html, m.end())
        if cm:
            blocks.append(body_html[m.start():cm.end()])
            pos = cm.end()
        else:
            blocks.append(body_html[m.start():].strip())
            break
    return blocks


def wrap_keep_with_next(body_html: str) -> str:
    """Owija każdy h2/h3 wraz z pierwszym następującym elementem blokowym w .keep-with-next."""
    blocks = _split_top_level_blocks(body_html)
    out: List[str] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        is_h23 = bool(re.match(r"^<h[23]\b", cur, re.I))
        if is_h23 and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            is_heading_or_hr = bool(re.match(r"^<(?:h[1-4]|hr)\b", nxt, re.I))
            if not is_heading_or_hr:
                out.append(f'<div class="keep-with-next">\n{cur}\n{nxt}\n</div>')
                i += 2
                continue
        out.append(cur)
        i += 1
    return "\n".join(out)


def prepare_book_content(
    body_html: str,
    metadata: dict,
    fallback_title: str,
    chapter_break: bool = False,
) -> Tuple[str, str]:
    """Buduje strukturę książki (.cover-page, .toc-page, .book-body) bez szumu LAB."""
    raw_title = str(metadata.get("title") or fallback_title)
    cover_title = clean_lab_title(raw_title)
    subtitle = str(metadata.get("subtitle", "")).strip()

    all_headings = re.findall(r'<h([1-3]) id="([^"]+)">(.*?)</h\1>', body_html)
    h1_count = sum(1 for lvl, _, _ in all_headings if lvl == "1")
    first_h1_match = re.match(r'^\s*<h1 id="([^"]+)">(.*?)</h1>\s*', body_html)

    h1_moved_to_cover = False
    if first_h1_match and h1_count == 1:
        extracted_h1 = html.unescape(first_h1_match.group(2)).strip()
        body_html = body_html[first_h1_match.end():]
        h1_moved_to_cover = True
        if not metadata.get("title"):
            cover_title = clean_lab_title(extracted_h1)
        elif not subtitle and clean_lab_title(extracted_h1) != cover_title:
            subtitle = extracted_h1

    # Poziomy spisu treści zależne od tego, czy h1 był tytułem publikacji
    if h1_moved_to_cover:
        toc_source = [(lvl, hid, txt) for lvl, hid, txt in all_headings[1:] if lvl in ("2", "3")]
        level_map = {"2": "toc-h1", "3": "toc-h2"}
    else:
        toc_source = [(lvl, hid, txt) for lvl, hid, txt in all_headings if lvl in ("1", "2")]
        level_map = {"1": "toc-h1", "2": "toc-h2"}

    # Strona tytułowa (.cover-page)
    kicker = str(metadata.get("kicker", "")).strip()
    author = str(metadata.get("author", "")).strip() or "Zbigniew Jasiołek"  # QG2-IGNORE
    date_val = str(metadata.get("date") or metadata.get("created") or "").strip()
    version = str(metadata.get("version", "")).strip()

    cover_parts = ['<div class="cover-page">', '  <div class="cover-header">']
    if kicker:
        cover_parts.append(f'    <p class="cover-kicker">{html.escape(kicker)}</p>')
    cover_parts.append(f'    <h1 class="cover-title">{html.escape(cover_title)}</h1>')
    if subtitle:
        cover_parts.append(f'    <p class="cover-subtitle">{html.escape(subtitle)}</p>')
    cover_parts.append('    <hr class="cover-rule">')
    cover_parts.append('  </div>')

    if author or date_val or version:
        cover_parts.append('  <div class="cover-meta">')
        if author:
            cover_parts.append(f'    <div class="cover-author">{html.escape(author)}</div>')
        if date_val:
            cover_parts.append(f'    <span class="cover-date">{html.escape(date_val)}</span>')
        if version:
            cover_parts.append(f'    <span class="cover-version">{html.escape(version)}</span>')
        cover_parts.append('  </div>')
    cover_parts.append('</div>')
    cover_html = "\n".join(cover_parts)

    # Spis treści (.toc-page)
    toc_html = ""
    if toc_source:
        lang = str(metadata.get("lang", "pl")).strip().lower()
        toc_heading = "Table of Contents" if lang == "en" else "Spis treści"
        toc_lines = [
            '<div class="toc-page">',
            f'  <h2 class="toc-title">{html.escape(toc_heading)}</h2>',
            '  <ul class="toc-list">',
        ]
        for lvl, hid, txt in toc_source:
            toc_cls = level_map[lvl]
            toc_lines.append(
                f'    <li class="toc-item {toc_cls}"><a href="#{hid}">{txt}</a><span class="toc-dots"></span></li>'
            )
        toc_lines.extend(['  </ul>', '</div>'])
        toc_html = "\n".join(toc_lines)

    wrapped_body_html = wrap_keep_with_next(body_html)
    body_cls = "book-body chapter-breaks" if chapter_break else "book-body"
    book_body_html = f'<main class="{body_cls}">\n{wrapped_body_html}\n</main>'
    combined = "\n".join(part for part in (cover_html, toc_html, book_body_html) if part)
    return cover_title, combined


def generate_full_html(
    title: str,
    content_html: str,
    template: str = "note",
    layout: str = "note",
    page_size: Optional[str] = None,
    book_meta: Optional[dict] = None,
) -> str:
    """Tworzy kompletny dokument HTML ze zintegrowanymi arkuszami Maja Print CSS."""
    if layout not in VALID_LAYOUTS:
        raise ValueError(f"Nieznany układ dokumentu: {layout!r}. Dozwolone: {VALID_LAYOUTS}")
    if template not in VALID_TEMPLATES:
        raise ValueError(f"Nieznany szablon wydawniczy: {template!r}. Dozwolone: {VALID_TEMPLATES}")

    if layout == "book":
        effective_size = page_size or "B5"
        running_title = clean_lab_title(title)
        css = _load_book_theme_css(template, effective_size, running_title)
        lang_attr = html.escape(str((book_meta or {}).get("lang", "pl")).strip().lower() or "pl")
        doc_title = running_title
    else:
        lang_attr = "pl"
        doc_title = title
        if template in BOOK_THEMES:
            effective_size = page_size or "A4"
            css = _load_book_theme_css(template, effective_size, "")
        else:
            css = TEMPLATES_CSS[template]
            if page_size and page_size != "A4":
                css = f"{css}\n@page {{ size: {page_size}; }}"

    mermaid_block = ""
    if 'class="mermaid"' in content_html or "class='mermaid'" in content_html:
        mermaid_block = '<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>\n<script>mermaid.initialize({startOnLoad:true});</script>\n'

    return f"""<!DOCTYPE html>
<html lang="{lang_attr}">
<head>
<meta charset="utf-8">
<title>{html.escape(doc_title)}</title>
{mermaid_block}<style>
{css}
</style>
</head>
<body>
{content_html}
</body>
</html>
"""


def build_preview_html(
    md_path: Path,
    metadata: dict,
    body_md: str,
    initial_layout: str = "book",
    initial_template: str = "book-classic",
    initial_page_size: str = "B5",
    server_mode: bool = False,
    initial_chapter_break: bool = False,
) -> str:
    """Buduje samowystarczalny plik HTML z paskiem podglądu i osadzonymi 5 arkuszami CSS."""
    raw_body_html = markdown_to_html(body_md, metadata, include_meta_card=False, add_heading_ids=True)
    clean_title, book_content = prepare_book_content(
        raw_body_html,
        metadata,
        md_path.stem,
        chapter_break=initial_chapter_break,
    )
    running_title = clean_lab_title(clean_title)
    lang_attr = html.escape(str(metadata.get("lang", "pl")).strip().lower() or "pl")

    if initial_template not in ("note", *BOOK_THEMES):
        initial_template = "book-classic" if initial_layout == "book" else "note"

    theme_styles = [f'<style data-theme="note">\n{NOTE_CSS}\n</style>']
    for theme in BOOK_THEMES:
        theme_css = _load_book_theme_css(theme, initial_page_size, running_title)
        theme_styles.append(f'<style data-theme="{theme}">\n{theme_css}\n</style>')
    styles_block = "\n".join(theme_styles)

    mermaid_block = ""
    if 'class="mermaid"' in book_content or "class='mermaid'" in book_content:
        mermaid_block = '<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>\n<script>mermaid.initialize({startOnLoad:true});</script>\n'

    default_dir = Path.home() / "Documents"
    default_pdf_path = default_dir / f"{md_path.stem}.pdf"
    md_path_js = str(md_path).replace("\\", "\\\\").replace('"', '\\"')
    pdf_path_js = str(default_pdf_path).replace("\\", "\\\\").replace('"', '\\"')
    default_dir_js = str(default_dir).replace("\\", "\\\\").replace('"', '\\"')
    initial_author = str(metadata.get("author", "")).strip() or "Zbigniew Jasiołek"  # QG2-IGNORE

    if server_mode:
        actions_html = (
            f'<label>Autor: <input id="pv-author" type="text" value="{html.escape(initial_author)}" placeholder="Autor"></label>\n'
            f'  <span class="pv-dir-wrap">Folder: <span id="pv-dir-label" title="{html.escape(str(default_dir))}">~/Documents</span></span>\n'
            '  <button id="pv-choose-dir" type="button">Zmień folder…</button>\n'
            '  <button id="pv-export" type="button" class="primary">Akceptuję — Eksportuj PDF</button>\n'
            '  <button id="pv-cancel" type="button">Anuluj</button>\n'
            '  <span id="pv-status" class="pv-status" aria-live="polite"></span>'
        )
    else:
        actions_html = (
            f'<label>Autor: <input id="pv-author" type="text" value="{html.escape(initial_author)}" placeholder="Autor"></label>\n'
            '  <button id="pv-print" type="button" class="primary">Drukuj / Zapisz PDF</button>\n'
            '  <button id="pv-copy" type="button">Kopiuj komendę CLI</button>\n'
            '  <input id="pv-cmd" type="text" readonly>'
        )

    return f"""<!DOCTYPE html>
<html lang="{lang_attr}">
<head>
<meta charset="utf-8">
<title>{html.escape(running_title)} — Podgląd</title>
{mermaid_block}{styles_block}
<style id="preview-page-style">
@page {{
  size: {initial_page_size};
}}
</style>
<style id="preview-shell-css">
.preview-toolbar {{
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9999;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  background: #0f172a;
  color: #f8fafc;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-size: 12px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
}}
.preview-toolbar label {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #cbd5e1;
}}
.preview-toolbar select,
.preview-toolbar button,
.preview-toolbar input#pv-author {{
  font-family: inherit;
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #334155;
  background: #1e293b;
  color: #f8fafc;
}}
.preview-toolbar select,
.preview-toolbar button {{
  cursor: pointer;
}}
.preview-toolbar input#pv-author {{
  width: 145px;
}}
.preview-toolbar .pv-dir-wrap {{
  font-size: 11.5px;
  color: #94a3b8;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.preview-toolbar #pv-dir-label {{
  color: #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}}
.preview-toolbar button.primary {{
  background: #16a34a;
  border-color: #16a34a;
  font-weight: 600;
}}
.preview-toolbar button:disabled {{
  opacity: 0.6;
  cursor: wait;
}}
.preview-toolbar .pv-status {{
  margin-left: auto;
  font-size: 12px;
  color: #e2e8f0;
}}
.preview-toolbar input#pv-cmd {{
  flex: 1 1 260px;
  min-width: 180px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #334155;
  background: #020617;
  color: #94a3b8;
}}
[data-layout="note"] .cover-page,
[data-layout="note"] .toc-page {{
  display: none !important;
}}
@media screen {{
  html {{
    background: #e2e8f0;
  }}
  body {{
    background: transparent !important;
    padding: 64px 20px 48px 20px !important;
    margin: 0 !important;
  }}
  .preview-sheet {{
    background: #ffffff;
    margin: 0 auto;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.14);
    border-radius: 2px;
    min-height: 250mm;
  }}
  .preview-sheet[data-page-size="B5"] {{
    max-width: 176mm;
    padding: 20mm 18mm;
  }}
  .preview-sheet[data-page-size="A4"] {{
    max-width: 210mm;
    padding: 20mm 18mm;
  }}
  .preview-sheet[data-page-size="A5"] {{
    max-width: 148mm;
    padding: 16mm 14mm;
  }}
}}
@media print {{
  .preview-toolbar {{
    display: none !important;
  }}
  html, body {{
    background: #ffffff !important;
    padding: 0 !important;
    margin: 0 !important;
  }}
  .preview-sheet {{
    max-width: none !important;
    width: auto !important;
    padding: 0 !important;
    margin: 0 !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    min-height: 0 !important;
  }}
}}
</style>
</head>
<body>
<div class="preview-toolbar">
  <label>Układ:
    <select id="pv-layout">
      <option value="book">book</option>
      <option value="note">note</option>
    </select>
  </label>
  <label>Motyw:
    <select id="pv-theme">
      <option value="book-classic">book-classic</option>
      <option value="swiss">swiss</option>
      <option value="editorial">editorial</option>
      <option value="manuscript">manuscript</option>
      <option value="note">note</option>
    </select>
  </label>
  <label>Format:
    <select id="pv-size">
      <option value="B5">B5</option>
      <option value="A4">A4</option>
      <option value="A5">A5</option>
    </select>
  </label>
  <label><input type="checkbox" id="pv-chapter-break"> Każdy rozdział od nowej strony</label>
  {actions_html}
</div>
<div id="preview-sheet" class="preview-sheet" data-layout="{html.escape(initial_layout)}" data-page-size="{html.escape(initial_page_size)}">
{book_content}
</div>
<script>
(function() {{
  const MD_PATH = "{md_path_js}";
  const PDF_PATH = "{pdf_path_js}";
  const DEFAULT_DIR = "{default_dir_js}";
  const SERVER_MODE = {"true" if server_mode else "false"};
  let selectedDir = DEFAULT_DIR;
  const layoutEl = document.getElementById("pv-layout");
  const themeEl = document.getElementById("pv-theme");
  const sizeEl = document.getElementById("pv-size");
  const chapterBreakEl = document.getElementById("pv-chapter-break");
  const authorEl = document.getElementById("pv-author");
  const dirLabelEl = document.getElementById("pv-dir-label");
  const chooseDirBtn = document.getElementById("pv-choose-dir");
  const sheetEl = document.getElementById("preview-sheet");
  const bookBodyEl = document.querySelector(".book-body");
  const pageStyleEl = document.getElementById("preview-page-style");
  const cmdEl = document.getElementById("pv-cmd");
  const copyBtn = document.getElementById("pv-copy");
  const printBtn = document.getElementById("pv-print");
  const exportBtn = document.getElementById("pv-export");
  const cancelBtn = document.getElementById("pv-cancel");
  const statusEl = document.getElementById("pv-status");
  const coverAuthorEl = document.querySelector(".cover-author");
  const themeStyles = document.querySelectorAll("style[data-theme]");

  layoutEl.value = "{html.escape(initial_layout)}";
  themeEl.value = "{html.escape(initial_template)}";
  sizeEl.value = "{html.escape(initial_page_size)}";
  if (chapterBreakEl) {{
    chapterBreakEl.checked = {"true" if initial_chapter_break else "false"};
  }}

  function buildCliCommand() {{
    const authorArg = (authorEl && authorEl.value.trim()) ? ' --author "' + authorEl.value.trim() + '"' : '';
    const cbArg = (chapterBreakEl && chapterBreakEl.checked) ? ' --chapter-break' : '';
    return 'python3 "08 Zespół AI/Skrypty/lab_core/export.py" "' + MD_PATH + '" "' + PDF_PATH +
      '" --layout ' + layoutEl.value + ' --template ' + themeEl.value + ' --page-size ' + sizeEl.value + authorArg + cbArg;
  }}

  function applyState() {{
    const activeTheme = themeEl.value;
    themeStyles.forEach(function(st) {{
      st.disabled = (st.getAttribute("data-theme") !== activeTheme);
    }});
    sheetEl.setAttribute("data-layout", layoutEl.value);
    sheetEl.setAttribute("data-page-size", sizeEl.value);
    pageStyleEl.textContent = "@page {{ size: " + sizeEl.value + "; }}";
    if (bookBodyEl && chapterBreakEl) {{
      bookBodyEl.classList.toggle("chapter-breaks", chapterBreakEl.checked);
    }}
    if (coverAuthorEl && authorEl) {{
      coverAuthorEl.textContent = authorEl.value;
    }}
    if (cmdEl) {{
      cmdEl.value = buildCliCommand();
    }}
  }}

  layoutEl.addEventListener("change", applyState);
  themeEl.addEventListener("change", applyState);
  sizeEl.addEventListener("change", applyState);
  if (chapterBreakEl) {{
    chapterBreakEl.addEventListener("change", applyState);
  }}
  if (authorEl) {{
    authorEl.addEventListener("input", applyState);
  }}
  if (SERVER_MODE && chooseDirBtn) {{
    chooseDirBtn.addEventListener("click", function() {{
      chooseDirBtn.disabled = true;
      fetch("/choose-dir", {{ method: "POST" }})
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
          if (data && data.ok && data.dir) {{
            selectedDir = data.dir;
            if (dirLabelEl) {{
              dirLabelEl.textContent = data.dir;
              dirLabelEl.title = data.dir;
            }}
          }}
        }})
        .finally(function() {{
          chooseDirBtn.disabled = false;
        }});
    }});
  }}
  if (printBtn) {{
    printBtn.addEventListener("click", function() {{ window.print(); }});
  }}
  if (copyBtn) {{
    copyBtn.addEventListener("click", function() {{
      const cmd = buildCliCommand();
      if (cmdEl) cmdEl.value = cmd;
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(cmd).then(function() {{
          copyBtn.textContent = "Skopiowano!";
          setTimeout(function() {{ copyBtn.textContent = "Kopiuj komendę CLI"; }}, 1500);
        }});
      }} else if (cmdEl) {{
        cmdEl.select();
        document.execCommand("copy");
      }}
    }});
  }}
  if (SERVER_MODE && exportBtn) {{
    exportBtn.addEventListener("click", function() {{
      exportBtn.disabled = true;
      if (cancelBtn) cancelBtn.disabled = true;
      if (statusEl) statusEl.textContent = "Generowanie PDF...";
      fetch("/export", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          layout: layoutEl.value,
          template: themeEl.value,
          page_size: sizeEl.value,
          author: authorEl ? authorEl.value.trim() : undefined,
          output_dir: selectedDir,
          chapter_break: chapterBreakEl ? chapterBreakEl.checked : false
        }})
      }})
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data && data.ok) {{
          const fname = String(data.path || "").split("/").pop();
          if (statusEl) statusEl.textContent = "✓ Zapisano: " + fname + " (" + selectedDir + ")";
        }} else {{
          if (statusEl) statusEl.textContent = "Błąd: " + ((data && data.error) || "nieznany");
          exportBtn.disabled = false;
          if (cancelBtn) cancelBtn.disabled = false;
        }}
      }})
      .catch(function(err) {{
        if (statusEl) statusEl.textContent = "Błąd połączenia: " + err;
        exportBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = false;
      }});
    }});
  }}
  if (SERVER_MODE && cancelBtn) {{
    cancelBtn.addEventListener("click", function() {{
      exportBtn.disabled = true;
      cancelBtn.disabled = true;
      fetch("/cancel", {{ method: "POST" }}).finally(function() {{
        if (statusEl) statusEl.textContent = "Anulowano — możesz zamknąć tę kartę.";
      }});
    }});
  }}

  applyState();
}})();
</script>
</body>
</html>
"""


def create_preview_server(
    md_path: Path,
    metadata: dict,
    body_md: str,
    initial_layout: str = "book",
    initial_template: str = "book-classic",
    initial_page_size: str = "B5",
    author: Optional[str] = None,
    version: Optional[str] = None,
    downloads_dir: Optional[Path] = None,
    initial_chapter_break: bool = False,
) -> Tuple[ThreadingHTTPServer, threading.Event, dict]:
    """Tworzy jednorazowy mikro-serwer HTTP podglądu z endpointami /, /export, /cancel."""
    preview_html = build_preview_html(
        md_path=md_path,
        metadata=metadata,
        body_md=body_md,
        initial_layout=initial_layout,
        initial_template=initial_template,
        initial_page_size=initial_page_size,
        server_mode=True,
        initial_chapter_break=initial_chapter_break,
    )
    html_bytes = preview_html.encode("utf-8")
    done_event = threading.Event()
    safe_stem = re.sub(r'[\\/:*?"<>|]+', "_", md_path.stem).strip() or "document"
    target_dir = downloads_dir if downloads_dir is not None else (Path.home() / "Documents")
    state: dict = {
        "last_activity": time.monotonic(),
        "exported_path": None,
        "cancelled": False,
    }

    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            state["last_activity"] = time.monotonic()
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def do_POST(self) -> None:
            state["last_activity"] = time.monotonic()
            if self.path == "/cancel":
                state["cancelled"] = True
                self._send_json(200, {"ok": True, "cancelled": True})
                done_event.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            if self.path == "/choose-dir":
                try:
                    proc = subprocess.run(
                        [
                            "osascript",
                            "-e",
                            'POSIX path of (choose folder with prompt "Wybierz folder zapisu PDF")',
                        ],
                        capture_output=True,
                        text=True,
                    )
                    chosen = proc.stdout.strip()
                    if proc.returncode == 0 and chosen:
                        clean_dir = chosen.rstrip("/") or "/"
                        self._send_json(200, {"ok": True, "dir": clean_dir})
                    else:
                        self._send_json(200, {"ok": False, "cancelled": True})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if self.path != "/export":
                self.send_error(404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw_body.decode("utf-8") or "{}")
                req_layout = payload.get("layout", initial_layout)
                req_template = payload.get("template", initial_template)
                req_page_size = payload.get("page_size", initial_page_size)
                req_author = payload.get("author")
                effective_author = req_author if req_author is not None else author
                req_chapter_break = bool(payload.get("chapter_break", initial_chapter_break))

                req_dir = payload.get("output_dir")
                if req_dir:
                    chosen_dir = Path(str(req_dir)).expanduser().resolve()
                    if not chosen_dir.is_dir():
                        self._send_json(400, {"ok": False, "error": f"Wybrany katalog nie istnieje: {chosen_dir}"})
                        return
                    export_dir = chosen_dir
                else:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    export_dir = target_dir

                out_pdf = export_dir / f"{safe_stem}.pdf"
                exported = export_markdown_to_pdf(
                    md_file_path=md_path,
                    output_pdf_path=out_pdf,
                    open_after=True,
                    template=req_template,
                    layout=req_layout,
                    page_size=req_page_size,
                    author=effective_author,
                    version=version,
                    chapter_break=req_chapter_break,
                )
                state["exported_path"] = exported
                self._send_json(200, {"ok": True, "path": str(exported)})
                done_event.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
    return server, done_event, state


def run_preview_server(
    md_path: Path,
    metadata: dict,
    body_md: str,
    initial_layout: str = "book",
    initial_template: str = "book-classic",
    initial_page_size: str = "B5",
    author: Optional[str] = None,
    version: Optional[str] = None,
    no_open: bool = False,
    idle_timeout: float = 600.0,
    downloads_dir: Optional[Path] = None,
    initial_chapter_break: bool = False,
) -> Path:
    """Uruchamia jednorazowy serwer podglądu, otwiera przeglądarkę i czeka na akceptację lub anulowanie."""
    server, done_event, state = create_preview_server(
        md_path=md_path,
        metadata=metadata,
        body_md=body_md,
        initial_layout=initial_layout,
        initial_template=initial_template,
        initial_page_size=initial_page_size,
        author=author,
        version=version,
        downloads_dir=downloads_dir,
        initial_chapter_break=initial_chapter_break,
    )
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if not no_open:
        webbrowser.open(url)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        while not done_event.wait(timeout=0.5):
            if time.monotonic() - state["last_activity"] >= idle_timeout:
                break
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    return state["exported_path"] or (downloads_dir or (Path.home() / "Documents")) / f"{md_path.stem}.pdf"


def export_markdown_to_pdf(
    md_file_path: Union[str, Path],
    output_pdf_path: Optional[Union[str, Path]] = None,
    open_after: bool = False,
    html_only: bool = False,
    template: Optional[str] = None,
    layout: Optional[str] = None,
    page_size: Optional[str] = None,
    author: Optional[str] = None,
    version: Optional[str] = None,
    preview: bool = False,
    no_open: bool = False,
    preview_static: bool = False,
    chapter_break: bool = False,
) -> Path:
    """Konwertuje notatkę Markdown na dokument PDF z użyciem headless Chromium."""
    md_path = Path(md_file_path).resolve()
    if not md_path.is_file():
        raise FileNotFoundError(f"Plik wejściowy Markdown nie istnieje: {md_path}")

    raw_text = md_path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(raw_text)
    if author is not None:
        metadata["author"] = author
    if version is not None:
        metadata["version"] = version

    resolved_layout = resolve_layout(metadata, layout)
    resolved_template = resolve_template(metadata, template, layout=resolved_layout)
    resolved_page_size = resolve_page_size(resolved_layout, page_size, metadata)

    is_explicit_html = bool(output_pdf_path and str(output_pdf_path).lower().endswith(".html"))
    if preview_static or (preview and is_explicit_html):
        out_html = (
            Path(output_pdf_path).resolve().with_suffix(".html")
            if output_pdf_path
            else Path("/tmp") / f"{md_path.stem}_preview.html"
        )
        preview_html = build_preview_html(
            md_path=md_path,
            metadata=metadata,
            body_md=body,
            initial_layout=resolved_layout,
            initial_template=resolved_template,
            initial_page_size=resolved_page_size,
            initial_chapter_break=chapter_break,
        )
        out_html.write_text(preview_html, encoding="utf-8")
        if not no_open:
            webbrowser.open(out_html.as_uri())
        return out_html

    if preview:
        return run_preview_server(
            md_path=md_path,
            metadata=metadata,
            body_md=body,
            initial_layout=resolved_layout,
            initial_template=resolved_template,
            initial_page_size=resolved_page_size,
            author=author,
            version=version,
            no_open=no_open,
            initial_chapter_break=chapter_break,
        )

    if resolved_layout == "book":
        raw_body_html = markdown_to_html(body, metadata, include_meta_card=False, add_heading_ids=True)
        clean_title, content_html = prepare_book_content(
            raw_body_html,
            metadata,
            md_path.stem,
            chapter_break=chapter_break,
        )
        full_html = generate_full_html(
            clean_title,
            content_html,
            template=resolved_template,
            layout="book",
            page_size=resolved_page_size,
            book_meta=metadata,
        )
    else:
        title = metadata.get("title") or md_path.stem
        body_html = markdown_to_html(body, metadata)
        full_html = generate_full_html(
            title,
            body_html,
            template=resolved_template,
            layout="note",
            page_size=resolved_page_size,
            book_meta=metadata,
        )

    if output_pdf_path:
        out_pdf = Path(output_pdf_path).resolve()
    else:
        out_pdf = Path.home() / "Documents" / f"{md_path.stem}.pdf"
        out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Jeśli użytkownik zażądał tylko pliku HTML
    if html_only:
        out_html = out_pdf.with_suffix(".html")
        out_html.write_text(full_html, encoding="utf-8")
        return out_html

    browser_bin = find_browser_engine()
    if not browser_bin:
        raise RuntimeError(
            "Nie odnaleziono wspieranego silnika przeglądarki (Google Chrome, Chromium, Brave, Edge). "
            "Uruchom z flagą --html-only, aby wygenerować kod HTML."
        )

    # Zapisz tymczasowy plik HTML
    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as tf:
        tf.write(full_html)
        temp_html_path = Path(tf.name)

    try:
        cmd = [
            browser_bin,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={str(out_pdf)}",
            str(temp_html_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        if not out_pdf.exists():
            raise RuntimeError(f"Silnik przeglądarki nie wygenerował pliku PDF: {res.stderr}")

        if open_after and sys.platform == "darwin":
            subprocess.run(["open", str(out_pdf)])

        return out_pdf
    finally:
        if temp_html_path.exists():
            temp_html_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Eksporter notatek Markdown do wydawniczego PDF (Maja Standard)")
    parser.add_argument("input_md", help="Ścieżka do wejściowego pliku notatki Markdown (.md)")
    parser.add_argument("output_pdf", nargs="?", help="Docelowa ścieżka pliku .pdf (opcjonalnie)")
    parser.add_argument("--open", action="store_true", help="Otwórz wygenerowany PDF w systemowym programie Preview")
    parser.add_argument("--html-only", action="store_true", help="Wygeneruj wyłącznie semantyczny plik HTML bez PDF")
    parser.add_argument(
        "--layout",
        choices=list(VALID_LAYOUTS),
        default=None,
        help="Układ dokumentu: note (domyślny) lub book (okładka + spis treści + żywa pagina)",
    )
    parser.add_argument(
        "--template",
        choices=list(VALID_TEMPLATES),
        default=None,
        help="Profil wydawniczy CSS (domyślnie autodetekcja z frontmatteru lub 'note'/'book-classic')",
    )
    parser.add_argument(
        "--page-size",
        choices=list(VALID_PAGE_SIZES),
        default=None,
        help="Format strony: A4 (domyślny dla note), B5 (domyślny dla book), A5",
    )
    parser.add_argument("--author", default=None, help="Autor publikacji (nadpisuje frontmatter)")
    parser.add_argument("--version", default=None, help="Wersja publikacji (nadpisuje frontmatter)")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Uruchom jednorazowy serwer podglądu w przeglądarce z przyciskiem eksportu PDF",
    )
    parser.add_argument(
        "--preview-static",
        action="store_true",
        help="Wygeneruj wyłącznie statyczny plik HTML podglądu bez uruchamiania serwera",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Nie otwieraj automatycznie przeglądarki w trybie --preview",
    )
    parser.add_argument(
        "--chapter-break",
        action="store_true",
        help="Rozpoczynaj każdy główny rozdział od nowej strony w układzie book",
    )
    args = parser.parse_args()

    try:
        out = export_markdown_to_pdf(
            md_file_path=args.input_md,
            output_pdf_path=args.output_pdf,
            open_after=args.open,
            html_only=args.html_only,
            template=args.template,
            layout=args.layout,
            page_size=args.page_size,
            author=args.author,
            version=args.version,
            preview=args.preview,
            no_open=args.no_open,
            preview_static=args.preview_static,
            chapter_break=args.chapter_break,
        )
        print(f"✓ Sukces: Wygenerowano plik: {out}")
        return 0
    except Exception as ex:
        print(f"[ERROR] Błąd generowania: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
