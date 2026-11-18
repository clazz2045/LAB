"""lab_core.quality.inspectors.products - Inspektor struktury projektów i czystości folderów Produkt/
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Set

from lab_core.config import ConfigError
from lab_core.constants import IGNORED_DIRS, IGNORED_FILES
from lab_core.markdown import normalize_nfc
from lab_core.quality.context import QualityContext, QualityInspector

ALLOWED_PRODUKT_SUBDIRS: Set[str] = {"_podglad", "_wersje", "_zrodla"}
ALLOWED_PRODUKT_LAUNCHERS: Set[str] = {
    "LAB-Installer.command",
    "LAB-Installer.bat",
    "LAB-Update.command",
    "LAB-Update.bat",
}
FORBIDDEN_PSEUDO_RELEASE_FOLDERS: Set[str] = {
    "dzieło", "dzielo", "artefakt", "artefakty", "wydania", "wydanie", "releases", "output", "build"
}


_KEEP_VERSIONS_RE = re.compile(r"^keep_intermediate_versions:\s*(true|false)\s*$", re.IGNORECASE | re.MULTILINE)


def keeps_intermediate_versions(project_dir: Path) -> bool:
    """Czyta z Karty Projektu pole `keep_intermediate_versions` (domyślnie true = wersje pośrednie obowiązkowe)."""
    for card in project_dir.glob("PR-*.md"):
        try:
            text = card.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        m = _KEEP_VERSIONS_RE.search(text[3:end])
        if m:
            return m.group(1).lower() == "true"
    return True


class ProjectsAndProductsInspector(QualityInspector):
    """Weryfikuje strukturę projektów w oparciu o Zasadę Białej Listy (Allowlist)."""
    name: str = "projects_and_products"

    def inspect(self, ctx: QualityContext) -> None:
        unauthorized_dirs_count = 0
        invalid_prod_files = 0

        try:
            projects_room = ctx.config.get_room_by_role("active-projects")
        except ConfigError:
            try:
                projects_room = ctx.config.get_room("03")
            except ConfigError:
                return

        projects_dir = ctx.vault_root / projects_room.path
        if not projects_dir.is_dir():
            return

        for item in projects_dir.iterdir():
            if not item.is_dir() or item.name.startswith(".") or item.name in IGNORED_DIRS:
                continue

            # A. Weryfikacja Białej Listy podfolderów w Produkt/ oraz zakazu pseudo-folderów wydań
            for root, dirs, _ in os.walk(item):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                curr_p = Path(root)
                norm_curr_name = normalize_nfc(curr_p.name).lower()

                # A1. Wewnątrz Produkt/ -> dozwolone TYLKO podkatalogi z białej listy
                if norm_curr_name == "produkt":
                    for d in dirs:
                        norm_d = normalize_nfc(d).lower()
                        if norm_d not in ALLOWED_PRODUKT_SUBDIRS:
                            unauthorized_dirs_count += 1
                            unauth_p = curr_p / d
                            rel_unauth = unauth_p.relative_to(ctx.vault_root)
                            ctx.add_finding(
                                level="error",
                                category="products",
                                path=str(rel_unauth),
                                room=projects_room.id,
                                message=(
                                    f"Naruszenie Zasady Białej Listy (Allowlist Violation): "
                                    f"wykryto nieautoryzowany podkatalog '{d}' w '{curr_p.relative_to(ctx.vault_root)}'. "
                                    f"Dozwolone podkatalogi w Produkt/ to WYŁĄCZNIE: {', '.join(sorted(ALLOWED_PRODUKT_SUBDIRS))}."
                                ),
                            )

                # A2. Wykrywanie pseudo-katalogów wydań poza Produkt/
                for d in dirs:
                    norm_d = normalize_nfc(d).lower()
                    if norm_d in FORBIDDEN_PSEUDO_RELEASE_FOLDERS and norm_curr_name != "produkt":
                        unauthorized_dirs_count += 1
                        full_pseudo_path = curr_p / d
                        rel_pseudo = full_pseudo_path.relative_to(ctx.vault_root)
                        ctx.add_finding(
                            level="error",
                            category="projects",
                            path=str(rel_pseudo),
                            room=projects_room.id,
                            message=(
                                f"Naruszenie Zasady Białej Listy: wykryto nieautoryzowany katalog wydań '{d}' w projekcie '{item.name}'. "
                                "Zgodnie ze standardem LAB 2 jedynym autoryzowanym miejscem wydań jest 'Produkt/'."
                            ),
                        )

            # A3. Karta wyłącza wersje pośrednie -> Produkt/_wersje/ nie powinien zawierać plików
            if not keeps_intermediate_versions(item):
                versions_dir = next(
                    (c for c in (item / "Produkt").glob("*") if c.is_dir() and normalize_nfc(c.name).lower() == "_wersje"),
                    None,
                ) if (item / "Produkt").is_dir() else None
                if versions_dir and any(f.is_file() and f.name not in IGNORED_FILES and not f.name.startswith(".") for f in versions_dir.rglob("*")):
                    ctx.add_finding(
                        level="warning",
                        category="products",
                        path=str(versions_dir.relative_to(ctx.vault_root)),
                        room=projects_room.id,
                        message=(
                            f"Karta projektu '{item.name}' ma keep_intermediate_versions: false, "
                            "a Produkt/_wersje/ zawiera pliki. Usuń wersje pośrednie albo zmień pole w Karcie."
                        ),
                    )

            # B. Sprawdź czystość folderów Produkt/ w projekcie (zakaz skryptów i śmieci tymczasowych)
            for root, dirs, files in os.walk(item):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                current_dir = Path(root)
                try:
                    rel_to_proj = current_dir.relative_to(item)
                    is_in_produkt = len(rel_to_proj.parts) > 0 and rel_to_proj.parts[0].lower() == "produkt"
                except ValueError:
                    is_in_produkt = False

                if not is_in_produkt:
                    continue

                if any("starter" in part.lower() for part in current_dir.parts):
                    continue

                for f in files:
                    if f.startswith(".") or f in IGNORED_FILES or f in ALLOWED_PRODUKT_LAUNCHERS:
                        continue
                    file_path = current_dir / f
                    ext = file_path.suffix.lower()
                    name_lower = f.lower()

                    is_script = ext in {".py", ".sh", ".bash", ".zsh", ".command", ".bat", ".cmd", ".rb"}
                    is_temp = (
                        ext in {".tmp", ".temp", ".bak", ".swp", ".swo", ".log"}
                        or name_lower.startswith("temp_")
                        or name_lower.startswith("tmp_")
                        or name_lower.endswith("~")
                    )

                    if is_script or is_temp:
                        invalid_prod_files += 1
                        rel_f = file_path.relative_to(ctx.vault_root)
                        file_type = "skrypt" if is_script else "plik tymczasowy"
                        ctx.add_finding(
                            level="error",
                            category="products",
                            path=str(rel_f),
                            room=projects_room.id,
                            message=(
                                f"Wykryto niedozwolony {file_type} w folderze Produkt: '{rel_f}'. "
                                "Folder Produkt/ może zawierać wyłącznie czyste artefakty wydań (zakaz skryptów i śmieci tymczasowych)."
                            ),
                        )

        ctx.stats["unauthorized_release_dirs"] = unauthorized_dirs_count
        ctx.stats["invalid_produkt_files"] = invalid_prod_files
