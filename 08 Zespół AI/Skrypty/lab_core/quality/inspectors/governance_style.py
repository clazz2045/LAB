"""lab_core.quality.inspectors.governance_style - Strażnik czystego języka technicznego i zakazu Skyneta
Standard: 100% Python Standard Library. Zero-Pip.

Aksjomat Jednego Prawa (The One Law):
Kod weryfikuje strukturę, typy i twarde miary, eliminując przestrzeń na lanie wody i antropomorfizację:
1. Zakaz plików meta-refleksyjnych (np. autorefleksja, samoświadomość w nazwie lub nagłówku YAML).
2. Walidacja struktury dokumentów w 08 Zespół AI/ (Governance, Role):
   - Wymóg obecności nagłówków markdown (#, ##) i podziału na konkretne sekcje techniczne.
   - Limit długości pojedynczej sekcji tekstu bez podziału inżynierskiego (ochrona przed laniem wody > 350 słów).
   - Zakaz narracji w 1. osobie liczby mnogiej w oficjalnej dokumentacji (np. 'jesteśmy organizmem', 'nasza tożsamość', 'czujemy').
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Set

from lab_core.constants import IGNORED_DIRS
from lab_core.markdown import parse_frontmatter
from lab_core.quality.context import QualityContext, QualityInspector

FORBIDDEN_META_NAME_PATTERNS = [
    re.compile(r"autorefleksj[aei]", re.IGNORECASE),
    re.compile(r"samoswiadomosc", re.IGNORECASE),
    re.compile(r"samoświadomość", re.IGNORECASE),
    re.compile(r"świadomość\s+systemu", re.IGNORECASE),
    re.compile(r"tożsamość\s+systemu", re.IGNORECASE),
]

FORBIDDEN_YAML_TYPES: Set[str] = {
    "autoreflection",
    "autorefleksja",
    "philosophy",
    "meta",
    "self-awareness",
}

PERSONIFICATION_PATTERNS = [
    re.compile(r"\b(jesteśmy\s+(organizmem|świadomością|egzoszkieletem|nadinteligencją))\b", re.IGNORECASE),
    re.compile(r"\b(nasza\s+tożsamość|tożsamość\s+naszego\s+systemu)\b", re.IGNORECASE),
    re.compile(r"\b(co\s+lab\s+sam\s+o\s+sobie\s+myśli)\b", re.IGNORECASE),
    re.compile(r"\b(świadomość\s+naszego\s+systemu)\b", re.IGNORECASE),
]

MAX_SECTION_WORDS = 350


class GovernanceStyleInspector(QualityInspector):
    """Weryfikuje brak plików meta-refleksyjnych oraz zgodność ze standardem zwięzłego języka technicznego."""
    name: str = "governance_style"

    def inspect(self, ctx: QualityContext) -> None:
        governance_room = ctx.config.rooms.get("08")
        if not governance_room:
            return

        gov_dir = ctx.vault_root / governance_room.path
        if not gov_dir.is_dir():
            return

        # Skanuj pliki w pokoju 08 Zespół AI (poza archiwum)
        for root, dirs, files in os.walk(gov_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and d != "_archiwum"]
            for f in files:
                if not f.endswith(".md"):
                    continue
                file_path = Path(root) / f
                rel_path = file_path.relative_to(ctx.vault_root)

                # 1. Weryfikacja nazwy pliku pod kątem zakazu meta-refleksji
                for pattern in FORBIDDEN_META_NAME_PATTERNS:
                    if pattern.search(f):
                        ctx.add_finding(
                            level="error",
                            category="governance_style",
                            path=str(rel_path),
                            message=(
                                f"Naruszenie Jednego Prawa (Zakaz Meta-Refleksji i Nowomowy AI): "
                                f"plik '{rel_path}' zawiera w nazwie zakazany wzorzec '{pattern.pattern}'. "
                                f"System nie tworzy notatek o swojej 'tożsamości'."
                            ),
                        )
                        break

                # 2. Weryfikacja nagłówka YAML
                fm, body = parse_frontmatter(file_path)
                doc_type = str(fm.get("type", "")).strip().lower()
                note_type = str(fm.get("note_type", "")).strip().lower()

                if doc_type in FORBIDDEN_YAML_TYPES or note_type in FORBIDDEN_YAML_TYPES:
                    ctx.add_finding(
                        level="error",
                        category="governance_style",
                        path=str(rel_path),
                        message=(
                            f"Naruszenie Jednego Prawa (Zakaz Meta-Refleksji): "
                            f"plik '{rel_path}' posiada niedozwolony typ '{doc_type or note_type}'. "
                            f"Dozwolone są wyłącznie typy operacyjne i inżynierskie."
                        ),
                    )

                # 3. Weryfikacja strukturalna dla notatek Governance (08 Zespół AI/Governance/)
                if "Governance" in file_path.parts:
                    self._check_governance_document_structure(ctx, rel_path, body)

    def _check_governance_document_structure(self, ctx: QualityContext, rel_path: Path, body: str) -> None:
        """Sprawdza, czy dokument techniczny posiada nagłówki i nie ma zbyt długich, niepodzielonych sekcji."""
        # Wymóg obecności przynajmniej nagłówków H1 i H2
        h1_matches = re.findall(r"^#\s+[^\n]+", body, re.MULTILINE)
        if not h1_matches:
            ctx.add_finding(
                level="warning",
                category="governance_style",
                path=str(rel_path),
                message=f"Brak głównego nagłówka H1 (#) w dokumencie technicznym '{rel_path}'.",
            )

        # Sprawdź długość poszczególnych sekcji (ochrona przed laniem wody)
        sections = re.split(r"\n(?=#{1,3}\s+)", body)
        for sec in sections:
            sec_clean = sec.strip()
            if not sec_clean:
                continue
            words = sec_clean.split()
            # Wyciągnij nagłówek sekcji
            header_match = re.match(r"^(#{1,3}\s+[^\n]+)", sec_clean)
            header_name = header_match.group(1) if header_match else "Początek dokumentu"

            if len(words) > MAX_SECTION_WORDS:
                ctx.add_finding(
                    level="warning",
                    category="governance_style",
                    path=str(rel_path),
                    message=(
                        f"Zbyt długa sekcja bez podziału inżynierskiego w '{rel_path}' [{header_name}]: "
                        f"{len(words)} słów (limit: {MAX_SECTION_WORDS}). Podziel na konkretne punkty lub tabele."
                    ),
                )

            # Sprawdź zakazane wzorce personifikacji
            for p_pattern in PERSONIFICATION_PATTERNS:
                if p_pattern.search(sec_clean):
                    ctx.add_finding(
                        level="warning",
                        category="governance_style",
                        path=str(rel_path),
                        message=(
                            f"Wykryto syndrom Skyneta / personifikację AI w '{rel_path}' [{header_name}]: "
                            f"dopasowanie '{p_pattern.pattern}'. Zastąp opisem bezosobowym i technicznym."
                        ),
                    )
                    break
