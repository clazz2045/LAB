"""lab_core.quality.inspectors.notes_links - Inspektor frontmattera YAML oraz integralności wikilinków
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Set

from lab_core.config import ConfigError
from lab_core.constants import IGNORED_DIRS, IGNORED_FILES
from lab_core.markdown import normalize_nfc
from lab_core.quality.context import QualityContext, QualityInspector

ARCHIVE_DIR = "_archiwum"

LINK_PLACEHOLDER_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),  # Daty YYYY-MM-DD
    re.compile(r"^(YYYY|RRRR|MM|DD|HH|SS)", re.IGNORECASE),
    re.compile(r"^(przewodnik|template|szablon|notatka|tytuł|nazwa|powiązane|zagadnienie|przykład|cel)", re.IGNORECASE),
    re.compile(r"^(Nadrzędny Temat|RS-xxx|04 Inner World/|raw/Nazwa_Zrodla)", re.IGNORECASE),
    re.compile(r"^\.\.\.$"),
]


class NotesAndLinksInspector(QualityInspector):
    """Weryfikuje poprawność frontmattera YAML oraz brak martwych linków (dangling links)."""
    name: str = "notes_and_links"

    def inspect(self, ctx: QualityContext) -> None:
        vault_registry: Set[str] = set()
        md_files: List[Path] = []

        for root, dirs, files in os.walk(ctx.vault_root):
            # _archiwum nie jest skanowane jako źródło linków, ale jego zawartość
            # jest poprawnym celem wikilinków (rejestr celów).
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS or d == ARCHIVE_DIR]
            in_archive = ARCHIVE_DIR in Path(root).relative_to(ctx.vault_root).parts

            for d in dirs:
                if d.startswith(".") or (d in IGNORED_DIRS and d != ARCHIVE_DIR):
                    continue
                full_d = Path(root) / d
                rel_d = full_d.relative_to(ctx.vault_root)
                vault_registry.add(normalize_nfc(str(rel_d)).lower())
                vault_registry.add(normalize_nfc(d).lower())

            for f in files:
                if f.startswith(".") or f in IGNORED_FILES:
                    continue
                full_p = Path(root) / f
                rel_p = full_p.relative_to(ctx.vault_root)

                vault_registry.add(normalize_nfc(str(rel_p)).lower())
                vault_registry.add(normalize_nfc(f).lower())

                if f.endswith(".md"):
                    if not in_archive:
                        md_files.append(full_p)
                    title = f[:-3]
                    vault_registry.add(normalize_nfc(title).lower())

        ctx.stats["total_notes"] = len(md_files)

        fm_errors = 0
        dangling_links = 0

        # Dozwolone prefiksy dla flagi consumed: (Reguła 7) wyznaczane dynamicznie z konfiguracji
        try:
            assets_room = ctx.config.get_room_by_role("reference-and-assets")
        except ConfigError:
            assets_room = ctx.config.get_room("06")
        try:
            aiteam_room = ctx.config.get_room_by_role("governance-and-engine")
        except ConfigError:
            aiteam_room = ctx.config.get_room("08")

        assets_notes_prefix = f"{assets_room.path}/Notatki/"
        templates_prefix = f"{aiteam_room.path}/Szablony/"
        allowed_consumed_prefixes = (assets_notes_prefix, templates_prefix)

        for md_path in md_files:
            rel_path = md_path.relative_to(ctx.vault_root).as_posix()
            try:
                content = md_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            # A. Sprawdź frontmatter YAML
            if content.startswith("---"):
                lines = content.splitlines(keepends=True)
                closing_idx = -1
                for i in range(1, len(lines)):
                    if lines[i].strip() == "---":
                        closing_idx = i
                        break

                if closing_idx == -1:
                    fm_errors += 1
                    ctx.add_finding(
                        level="error",
                        category="frontmatter",
                        path=rel_path,
                        message=f"Niedomknięty frontmatter YAML w pliku '{rel_path}' (brak drugiego '---')",
                    )
                else:
                    fm_text = "".join(lines[1:closing_idx])
                    for fml in fm_text.splitlines():
                        if ":" in fml:
                            val = fml.split(":", 1)[1].strip()
                            if val.startswith("[") and not val.endswith("]"):
                                fm_errors += 1
                                ctx.add_finding(
                                    level="error",
                                    category="frontmatter",
                                    path=rel_path,
                                    message=f"Błąd składni YAML w '{rel_path}': niedomknięty nawias kwadratowy w linii '{fml.strip()}'",
                                )

                    if re.search(r"^\s*consumed\s*:", fm_text, re.MULTILINE):
                        if not rel_path.startswith(allowed_consumed_prefixes):
                            ctx.add_finding(
                                level="warning",
                                category="frontmatter",
                                path=rel_path,
                                message=(
                                    f"Naruszenie zakresu flagi 'consumed:' (Reguła 7) w '{rel_path}': "
                                    f"pole 'consumed' jest dozwolone wyłącznie w '{assets_notes_prefix}' oraz '{templates_prefix}'."
                                ),
                            )

            # B. Sprawdź martwe linki wikilink [[Target]] (z pominięciem bloków kodu i inline code)
            scan_text = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
            scan_text = re.sub(r"~~~.*?~~~", "", scan_text, flags=re.DOTALL)
            scan_text = re.sub(r"`[^`\n]+`", "", scan_text)
            wikilinks = re.findall(r"\[\[(.*?)\]\]", scan_text)
            for raw_link in wikilinks:
                clean_raw = raw_link.replace(r"\|", "|")
                target = clean_raw.split("|")[0].split("#")[0].strip().rstrip("\\/")
                if not target:
                    continue

                is_placeholder = False
                for pattern in LINK_PLACEHOLDER_PATTERNS:
                    if pattern.search(target):
                        is_placeholder = True
                        break

                if is_placeholder:
                    continue

                norm_target = normalize_nfc(target).lower()
                if norm_target in vault_registry:
                    continue
                if norm_target.endswith(".md") and norm_target[:-3] in vault_registry:
                    continue
                if f"{norm_target}.md" in vault_registry:
                    continue

                dangling_links += 1
                ctx.add_finding(
                    level="warning",
                    category="links",
                    path=rel_path,
                    message=f"Martwy link w '{rel_path}': brak celu [[{raw_link}]] w skarbcu",
                )

        ctx.stats["frontmatter_errors"] = fm_errors
        ctx.stats["dangling_links"] = dangling_links
