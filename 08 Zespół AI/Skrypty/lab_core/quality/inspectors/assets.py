"""lab_core.quality.inspectors.assets - Inspektor higieny Zasobów i notatek referencyjnych SAT
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from lab_core.config import ConfigError
from lab_core.constants import IGNORED_DIRS, IGNORED_FILES
from lab_core.metadata import (
    extract_template_sections,
    extract_youtube_video_id,
    get_reference_template_path,
)
from lab_core.quality.context import QualityContext, QualityInspector

PLACEHOLDER_PATTERNS = [
    re.compile(r"\[\s\]\s*do zrobienia", re.IGNORECASE),
    re.compile(r"\[\s\]\s*pobranie pełnej", re.IGNORECASE),
    re.compile(r"\*\(do uzupełnienia", re.IGNORECASE),
    re.compile(r"\*\(wymagane min\.", re.IGNORECASE),
    re.compile(r"\*\(powiązania tematyczne do uzupełnienia", re.IGNORECASE),
    re.compile(r"brak opisu w metadanych źródłowych", re.IGNORECASE),
    re.compile(r"brak szczegółów w metadanych źródłowych", re.IGNORECASE),
    re.compile(r"^\s*-\s*`\[00:00\]`\s*start materiału\s*$", re.IGNORECASE | re.MULTILINE),
]


def _is_video_reference_note(frontmatter: str, fname: str) -> bool:
    """Deterministycznie sprawdza na podstawie frontmattera/nazwy pliku czy notatka dotyczy wideo."""
    if fname.startswith("Wideo"):
        return True
    src_match = re.search(r"^\s*source_url:\s*['\"]?([^\s'\"]+)['\"]?", frontmatter, re.MULTILINE | re.IGNORECASE)
    if src_match and extract_youtube_video_id(src_match.group(1)):
        return True
    title_match = re.search(r"^\s*title:\s*['\"]?(.+?)['\"]?\s*$", frontmatter, re.MULTILINE | re.IGNORECASE)
    if title_match and title_match.group(1).strip().startswith("Wideo"):
        return True
    tags_block = re.search(r"^\s*tags:\s*\n((?:\s*-\s+.*\n?)*)", frontmatter, re.MULTILINE | re.IGNORECASE)
    if tags_block:
        tags_lower = tags_block.group(1).lower()
        if re.search(r"^\s*-\s+(wideo|youtube)\s*$", tags_lower, re.MULTILINE):
            return True
    return False


def _classify_template_section(heading: str) -> str:
    """Mapuje nagłówek H2 z kanonicznego szablonu lub notatki na rolę sekcji."""
    h = heading.lower()
    if any(k in h for k in ("teza", "esencja", "abstrakt")):
        return "thesis"
    if any(k in h for k in ("dowod", "fakt", "przebieg", "szczegół", "szczegol", "argument")):
        return "details"
    if any(k in h for k in ("oś czasu", "os czasu", "chronolog")):
        return "timeline"
    if any(k in h for k in ("zastosowanie", "implikacj", "wnioski")):
        return "implications"
    if "powiązan" in h or "powiazan" in h or "🔗" in heading:
        return "links"
    return heading.strip()


def _parse_markdown_h2_sections(body: str) -> dict[str, str]:
    """Wyodrębnia sekcje ## ... wraz z ich treścią z ciała notatki (ignorując bloki kodu ```)."""
    sections: dict[str, str] = {}
    current_key: str | None = None
    buf: list[str] = []
    in_code_block = False

    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            if current_key is not None:
                buf.append(line)
            continue

        if not in_code_block:
            m = re.match(r"^##\s+(.+)$", line)
            if m:
                if current_key is not None and current_key not in sections:
                    sections[current_key] = "\n".join(buf).strip()
                current_key = _classify_template_section(m.group(1).strip())
                buf = []
                continue

        if current_key is not None:
            buf.append(line)

    if current_key is not None and current_key not in sections:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def _section_has_placeholder_or_empty(sec_body: str) -> bool:
    """Sprawdza czy treść sekcji jest pusta lub zawiera niewypełnione placeholdery."""
    cleaned = re.sub(r"(?m)^\s*---\s*$", "", sec_body).strip()
    if not cleaned:
        return True
    return any(pat.search(cleaned) for pat in PLACEHOLDER_PATTERNS)

FORBIDDEN_CONTAMINATION_PHRASES = [
    "notatki operacyjne użytkownika",
    "notatki operacyjne uzytkownika",
    "source: wispr-flow",
    "z przechwytu",
    "brudnopis użytkownika",
    "brudnopis uzytkownika",
]


class AssetsAndReferencesInspector(QualityInspector):
    """Weryfikuje higienę pokoju Zasoby oraz integralność notatek referencyjnych SAT."""
    name: str = "assets_and_references"

    def inspect(self, ctx: QualityContext) -> None:
        try:
            assets_room = ctx.config.get_room_by_role("reference-and-assets")
        except ConfigError:
            try:
                assets_room = ctx.config.get_room("06")
            except ConfigError:
                return

        assets_dir = ctx.vault_root / assets_room.path
        if not assets_dir.is_dir():
            return

        # 1. Bariera czystości Zasobów (brak surowych plików przechwytu Wispr_ / type: raw-capture)
        raw_captures_found = 0
        for root, dirs, files in os.walk(assets_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for fname in files:
                if fname in IGNORED_FILES or fname.startswith("."):
                    continue

                fpath = Path(root) / fname
                rel_p = fpath.relative_to(ctx.vault_root)

                if fname.startswith("Wispr_"):
                    raw_captures_found += 1
                    ctx.add_finding(
                        level="error",
                        category="assets",
                        room=assets_room.id,
                        path=str(rel_p),
                        message=(
                            f"Naruszenie czystości Zasobów: wykryto surowy plik przechwytu w Zasobach ({rel_p}). "
                            "Surowe przechwyty podlegają drenażowi do Skrzynki/_archiwum/ i Dziennika Dnia."
                        ),
                    )
                    continue

                if fpath.suffix.lower() == ".md" and fpath.is_file():
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                        if "type: raw-capture" in content or "source: wispr-flow" in content:
                            raw_captures_found += 1
                            ctx.add_finding(
                                level="error",
                                category="assets",
                                room=assets_room.id,
                                path=str(rel_p),
                                message=(
                                    f"Naruszenie czystości Zasobów: wykryto surowy plik przechwytu w Zasobach ({rel_p}) "
                                    "(wykryto znacznik 'type: raw-capture' lub 'source: wispr-flow')."
                                ),
                            )
                    except Exception:
                        pass

        ctx.stats["raw_captures_in_assets"] = raw_captures_found

        # 2. Bramka Jakościowa Notatki Referencyjnej SAT w 06 Zasoby/Notatki/
        notes_dir = assets_dir / "Notatki"
        if not notes_dir.is_dir():
            return

        ref_total = 0
        ref_valid = 0
        ref_errors = 0
        raw_stage_notes = 0
        legacy_notes_count = 0

        for root, dirs, files in os.walk(notes_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for fname in files:
                if not fname.endswith(".md") or fname in IGNORED_FILES or fname.startswith("."):
                    continue

                fpath = Path(root) / fname
                rel_p = fpath.relative_to(ctx.vault_root)

                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if "note_type: reference" not in content and 'note_type: "reference"' not in content:
                    continue

                if "Poezja Długosza" in str(rel_p) or "cytaty" in content or "source_url:" not in content:
                    continue

                ref_total += 1
                note_has_issues = False

                stage_match = re.search(r"^\s*stage:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?", content, re.MULTILINE | re.IGNORECASE)
                stage = stage_match.group(1).lower() if stage_match else None
                created_match = re.search(r"^\s*created:\s*['\"]?(\d{4}-\d{2}-\d{2})['\"]?", content, re.MULTILINE)
                created_date = created_match.group(1) if created_match else ""
                is_strict_sat_era = created_date >= "2026-11-13"

                # A. Weryfikacja calloutu metadanych źródłowych (> [!info])
                # Cezura SAT: od 2026-11-13 obowiązkowe zawsze; starsze (legacy) zliczane jako telemetria,
                # a ostrzeżenia emitowane wyłącznie w trybie --audit-legacy.
                has_callout = bool(re.search(r"^>\s*\[!info\]", content, re.MULTILINE | re.IGNORECASE))
                if not has_callout:
                    if is_strict_sat_era:
                        ctx.add_finding(
                            level="warning",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Brak wymaganego calloutu metadanych źródłowych (> [!info] Metadane źródłowe) "
                                f"w notatce referencyjnej '{rel_p}'"
                            ),
                        )
                        note_has_issues = True
                    else:
                        legacy_notes_count += 1
                        if ctx.audit_legacy:
                            ctx.add_finding(
                                level="warning",
                                category="assets",
                                room=assets_room.id,
                                path=str(rel_p),
                                message=(
                                    f"Brak wymaganego calloutu metadanych źródłowych (> [!info] Metadane źródłowe) "
                                    f"w notatce referencyjnej '{rel_p}'"
                                ),
                            )
                            note_has_issues = True

                # B. Weryfikacja cyklu życia (stage) oraz zgodności z kanonicznym szablonem SAT (bez limitów znaków)

                frontmatter = ""
                body = content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = parts[1]
                        body = parts[2]

                if is_strict_sat_era:
                    # Walidacja nowych notatek od 2026-11-13 w oparciu o kanoniczny szablon (SSOT)
                    if stage != "distilled":
                        if stage == "raw":
                            raw_stage_notes += 1
                        ctx.add_finding(
                            level="error",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Notatka referencyjna '{rel_p}' nie ma statusu 'stage: distilled' "
                                f"(aktualnie: '{stage or 'brak'}'). Nowe notatki od 2026-11-13 muszą być w pełni opracowane."
                            ),
                        )
                        note_has_issues = True

                    is_video = _is_video_reference_note(frontmatter, fname)
                    tpl_path = get_reference_template_path(is_youtube=is_video, vault_root=ctx.vault_root)
                    tpl_headings = extract_template_sections(tpl_path)
                    note_sections = _parse_markdown_h2_sections(body)

                    missing_sections: list[str] = []
                    empty_or_placeholder: list[str] = []
                    for heading in tpl_headings:
                        key = _classify_template_section(heading)
                        if key not in note_sections:
                            missing_sections.append(heading)
                        elif _section_has_placeholder_or_empty(note_sections[key]):
                            empty_or_placeholder.append(heading)

                    if missing_sections:
                        ctx.add_finding(
                            level="error",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Brak wymaganych sekcji z kanonicznego szablonu ({tpl_path.name}) w '{rel_p}': "
                                f"{', '.join(missing_sections)}."
                            ),
                        )
                        note_has_issues = True

                    if empty_or_placeholder:
                        ctx.add_finding(
                            level="error",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Puste sekcje lub niewypełnione placeholdery w '{rel_p}': "
                                f"{', '.join(empty_or_placeholder)}."
                            ),
                        )
                        note_has_issues = True
                elif stage == "distilled":
                    # B2. Weryfikacja Kanonu Trójwarstwowego (nagłówki H2) dla notatek sprzed 2026-11-13
                    has_l1 = bool(re.search(r"^##\s+.*(teza|esencja|abstrakt)", body, re.MULTILINE | re.IGNORECASE))
                    has_l2 = bool(re.search(r"^##\s+.*(dowod|fakt|przebieg|szczeg[oó]ł|argument)", body, re.MULTILINE | re.IGNORECASE))
                    has_l3 = bool(re.search(r"^##\s+.*(zastosowanie|implikacj|wnioski)", body, re.MULTILINE | re.IGNORECASE))

                    if not (has_l1 and has_l2 and has_l3):
                        missing = []
                        if not has_l1:
                            missing.append("Warstwa I (Teza/Esencja)")
                        if not has_l2:
                            missing.append("Warstwa II (Dowody/Przebieg)")
                        if not has_l3:
                            missing.append("Warstwa III (Zastosowanie w LAB)")
                        ctx.add_finding(
                            level="error",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Naruszenie Kanonu Trójwarstwowego w '{rel_p}' (stage: distilled). "
                                f"Brakujące sekcje: {', '.join(missing)}."
                            ),
                        )
                        note_has_issues = True

                # Wspólna weryfikacja jakości dla wszystkich notatek stage: distilled (także od 2026-11-13)
                if stage == "distilled":
                    # B3. Weryfikacja kotwicy empirycznej (min 3 punkty + liczba/procent LUB cytat LUB timestamp)
                    bullets_count = len(re.findall(r"^\s*[-*]\s+", body, re.MULTILINE))
                    has_numbers = bool(re.search(r"\b\d+([.,]\d+)?\s*(%|krotnie|mld|mln|k\b|x\b|\$|zł)", body, re.IGNORECASE)) or bool(re.search(r"\b\d{2,}\b", body))
                    has_quote = bool(re.search(r'["„»][^"„»\n]{4,}["”«]', body))
                    has_timestamp = bool(re.search(r'\[\d{1,2}:\d{2}(:\d{2})?\]', body))
                    has_anchor = has_numbers or has_quote or has_timestamp

                    if bullets_count < 3 or not has_anchor:
                        ctx.add_finding(
                            level="warning",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Niska gęstość faktograficzna w '{rel_p}' (stage: distilled). "
                                "Dojrzała synteza wymaga min. 3 punktów oraz twardej kotwicy (liczba/procent, cytat w cudzysłowie lub timestamp [mm:ss])."
                            ),
                        )

                    # B4. Weryfikacja sekcji powiązań
                    has_links_sec = bool(re.search(r"^##\s+.*🔗", body, re.MULTILINE))
                    if not has_links_sec or "[[" not in body:
                        ctx.add_finding(
                            level="warning",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Brak sekcji powiązań '## 🔗' lub brak wikilinków w notatce '{rel_p}' (stage: distilled)."
                            ),
                        )
                elif not is_strict_sat_era:
                    # Dla starszych notatek (sprzed 2026-11-13) w fazie surowej (stage: raw lub brak pola stage)
                    content_lower = content.lower()
                    if stage == "raw":
                        raw_stage_notes += 1
                        has_do_opracowania = "do-opracowania" in content_lower
                        if not has_do_opracowania:
                            ctx.add_finding(
                                level="warning",
                                category="assets",
                                room=assets_room.id,
                                path=str(rel_p),
                                message=(
                                    f"Notatka referencyjna '{rel_p}' (stage: raw) nie posiada wymaganego tagu 'do-opracowania'."
                                ),
                            )
                            note_has_issues = True

                        is_recent = bool(re.search(r"created:\s*['\"]?2026-11-", content))
                        if is_recent:
                            ctx.add_finding(
                                level="warning",
                                category="assets",
                                room=assets_room.id,
                                path=str(rel_p),
                                message=(
                                    f"Notatka referencyjna '{rel_p}' pozostaje w stanie surowym (stage: raw). "
                                    "Wymaga pełnej destylacji SAT (stage: distilled) i Kanonu Trójwarstwowego."
                                ),
                            )
                            note_has_issues = True

                # C. Brak zanieczyszczenia brudnopisem użytkownika
                content_lower = content.lower()
                for phrase in FORBIDDEN_CONTAMINATION_PHRASES:
                    if phrase in content_lower:
                        ctx.add_finding(
                            level="error",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Zanieczyszczenie notatki referencyjnej '{rel_p}' myślami człowieka "
                                f"(wykryto frazę: '{phrase}')"
                            ),
                        )
                        note_has_issues = True

                # D. Weryfikacja jawnej flagi konsumpcji (consumed: true/false)
                if "consumed:" not in content:
                    is_new_note = bool(re.search(r"created:\s*['\"]?2026-11-", content))
                    if is_new_note:
                        ctx.add_finding(
                            level="warning",
                            category="assets",
                            room=assets_room.id,
                            path=str(rel_p),
                            message=(
                                f"Brak flagi 'consumed: false' (lub 'consumed: true') w metadanych YAML notatki '{rel_p}'. "
                                "Wszystkie notatki w Zasobach muszą posiadać jawny status konsumpcji."
                            ),
                        )
                        note_has_issues = True

                if note_has_issues:
                    ref_errors += 1
                else:
                    ref_valid += 1

        ctx.stats["reference_notes"] = ref_total
        ctx.stats["reference_notes_valid"] = ref_valid
        ctx.stats["reference_notes_errors"] = ref_errors
        ctx.stats["raw_stage_notes"] = raw_stage_notes
        ctx.stats["legacy_notes_count"] = legacy_notes_count
