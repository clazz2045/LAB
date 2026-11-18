"""lab_core.markdown - Deterministyczny parser i generator Markdown / YAML Frontmatter
Standard inżynieryjny: 100% Python Standard Library (re, unicodedata, pathlib)
Zero zewnętrznych zależności pip (brak PyYAML).
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def normalize_nfc(text: str) -> str:
    """Normalizuje tekst Unicode do kanonicznej formy NFC (bezpieczeństwo znaków diakrytycznych na macOS)."""
    return unicodedata.normalize("NFC", text)


def parse_frontmatter(
    source: Union[str, Path]
) -> Tuple[Dict[str, Any], str]:
    """Wyodrębnia frontmatter YAML i treść notatki bez zewnętrznych bibliotek.

    Przyjmuje ścieżkę do pliku (Path / str) lub bezpośredni tekst Markdown.
    Zwraca krotkę (słownik_metadanych, treść_bez_frontmattera).
    """
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and (source.endswith(".md") or "/" in source or "\\" in source)):
        p = Path(source)
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                return {}, ""
        else:
            text = str(source)
    else:
        text = str(source)

    text = normalize_nfc(text)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break

    if closing_idx == -1:
        return {}, text

    fm_dict: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    current_list_val: List[str] = []

    for raw_line in lines[1:closing_idx]:
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Element listy YAML pod bieżącym kluczem
        if current_list_key and stripped.startswith("- "):
            item = stripped[2:].strip().strip("\"'")
            current_list_val.append(item)
            continue

        if ":" in line:
            # Zamknij poprzednią listę jeśli istniała
            if current_list_key:
                fm_dict[current_list_key] = current_list_val
                current_list_key = None
                current_list_val = []

            key, val = line.split(":", 1)
            key = key.strip()
            val_clean = val.strip()

            if not val_clean:
                # Potencjalny start listy wielowierszowej
                current_list_key = key
                current_list_val = []
            elif val_clean.startswith("[") and val_clean.endswith("]"):
                # Lista jednoliniowa [a, b, c]
                inner = val_clean[1:-1].strip()
                if not inner:
                    fm_dict[key] = []
                else:
                    items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
                    fm_dict[key] = items
            elif val_clean.lower() in {"true", "false"}:
                fm_dict[key] = (val_clean.lower() == "true")
            elif val_clean.isdigit():
                fm_dict[key] = int(val_clean)
            else:
                fm_dict[key] = val_clean.strip("\"'")

    if current_list_key:
        fm_dict[current_list_key] = current_list_val

    body = "".join(lines[closing_idx + 1:])
    return fm_dict, body


def dump_frontmatter(metadata: Dict[str, Any], body: str) -> str:
    """Serializuje słownik metadanych do frontmattera YAML i łączy z treścią Markdown."""
    if not metadata:
        return body

    yaml_lines = ["---"]
    for key, val in metadata.items():
        if isinstance(val, bool):
            yaml_lines.append(f"{key}: {'true' if val else 'false'}")
        elif isinstance(val, (int, float)):
            yaml_lines.append(f"{key}: {val}")
        elif isinstance(val, list):
            if not val:
                yaml_lines.append(f"{key}: []")
            elif all(isinstance(x, (str, int, float, bool)) for x in val) and len(val) <= 3 and all("," not in str(x) for x in val):
                items_str = ", ".join(f'"{x}"' if isinstance(x, str) else str(x) for x in val)
                yaml_lines.append(f"{key}: [{items_str}]")
            else:
                yaml_lines.append(f"{key}:")
                for item in val:
                    if isinstance(item, str) and ("\"" in item or ":" in item or " " in item):
                        yaml_lines.append(f'  - "{item}"')
                    else:
                        yaml_lines.append(f"  - {item}")
        elif isinstance(val, str):
            if "\n" in val or ":" in val or '"' in val:
                safe_val = val.replace('"', '\\"')
                yaml_lines.append(f'{key}: "{safe_val}"')
            else:
                yaml_lines.append(f"{key}: {val}")
        else:
            yaml_lines.append(f"{key}: {val}")

    yaml_lines.append("---")
    yaml_header = "\n".join(yaml_lines) + "\n"

    # Zapewnij czysty odstęp między nagłówkiem a treścią
    clean_body = body.lstrip("\r\n")
    return yaml_header + clean_body


def extract_wikilinks(content: str) -> List[str]:
    """Wyciąga kanoniczne cele linków wikilink [[Target|Alias]] lub [[Target#Nagłówek]]."""
    targets: List[str] = []
    wikilinks = re.findall(r"\[\[(.*?)\]\]", content)
    for raw_link in wikilinks:
        clean_raw = raw_link.replace(r"\|", "|")
        target = clean_raw.split("|")[0].split("#")[0].strip().rstrip("\\")
        if target:
            targets.append(normalize_nfc(target))
    return targets


def ensure_consumed_field(content: str, default: bool = False) -> str:
    """
    Wstrzykuje pole 'consumed: false' (lub True) do frontmattera YAML, jeśli nie istnieje.
    Jeśli notatka posiada już pole 'consumed:', zwraca tekst bez modyfikacji.
    """
    fm, body = parse_frontmatter(content)
    if "consumed" in fm:
        return content

    # Jeśli jest frontmatter, dopisz 'consumed: false' przed zamykającym '---'
    lines = content.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        closing_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break
        if closing_idx != -1:
            val_str = "true" if default else "false"
            indent = ""
            lines.insert(closing_idx, f"consumed: {val_str}\n")
            return "".join(lines)

    # Jeśli brak frontmattera, wygeneruj minimalny nagłówek
    fm_val = {"type": "note", "consumed": default}
    return dump_frontmatter(fm_val, content)


def normalize_reference_frontmatter(content: str, today_str: Optional[str] = None) -> str:
    """Normalizuje frontmatter notatki referencyjnej przy drenażu do 06 Zasoby/Notatki/.

    Jeśli brak klucza 'type' lub ma wartość 'capture', ustawia:
    - type: note
    - note_type: reference (jeśli brak)
    - created: YYYY-MM-DD (dzisiejsza data, jeśli brak)
    - consumed: false (jeśli brak)
    Zachowuje pozostałe metadane oraz treść notatki.
    """
    fm, body = parse_frontmatter(content)
    raw_type = str(fm.get("type", "")).strip().lower()
    if raw_type not in {"", "capture"}:
        return ensure_consumed_field(content, default=False)

    fm["type"] = "note"
    if not str(fm.get("note_type", "")).strip():
        fm["note_type"] = "reference"
    if not str(fm.get("created", "")).strip():
        fm["created"] = today_str or datetime.date.today().isoformat()
    if "consumed" not in fm:
        fm["consumed"] = False

    return dump_frontmatter(fm, body)
