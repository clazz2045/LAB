"""lab_core.distillate - Walidator destylatu SAT zwracanego przez subagentów do Koordynatora
Standard: 100% Python Standard Library. Zero-Pip.

Limit: 600 tokenów (szacunek: ~4 znaki / token => 2400 znaków).
Sekcje SAT rozpoznawane po nagłówkach (z diakrytykami lub bez): Fakty, Wnioski, Rekomendacje.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import List, Tuple

sys.dont_write_bytecode = True

MAX_DISTILLATE_TOKENS = 600
CHARS_PER_TOKEN = 4
MAX_RAW_DUMP_LINES = 40
MAX_LINE_CHARS = 800

# Nagłówek sekcji na początku linii: opcjonalny markdown (#, *, -, >, cyfry) + słowo kluczowe + dwukropek/koniec
_SECTION_PREFIX = r"^[\s#>*\-\d.)_]*"
_SAT_SECTIONS = {
    "fakty": re.compile(_SECTION_PREFIX + r"(fakty|fakt|dowody|ustalenia)\b", re.IGNORECASE | re.MULTILINE),
    "wnioski": re.compile(_SECTION_PREFIX + r"(wnioski|wniosek|synteza|analiza)\b", re.IGNORECASE | re.MULTILINE),
    "rekomendacje": re.compile(
        _SECTION_PREFIX + r"(rekomendacje|rekomendacja|rezultat|artefakt|polecenia|polecenie|dyspozycje|dyspozycja|decyzje|decyzja)\b", re.IGNORECASE | re.MULTILINE
    ),
}

_PERSONIFICATION_PATTERNS = [
    re.compile(r"\b(jako\s+(sztuczna\s+inteligencja|model\s+ai|agent\s+ai\s+czuj[eę]|inteligentny\s+byt))\b", re.IGNORECASE),
    re.compile(r"\b(moja\s+(samoswiadomosc|samoświadomość|tozsamosc|tożsamość|swiadomosc|świadomość))\b", re.IGNORECASE),
    re.compile(r"\b(nasz\s+organizm\s+cyfrowy|nasz\s+kolektyw)\b", re.IGNORECASE),
]


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).replace("\u0142", "l").replace("\u0141", "L")


def estimate_tokens(text: str) -> int:
    """Szacunkowa liczba tokenów (heurystyka znakowa)."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def validate_sat_distillate(text: str) -> Tuple[bool, List[str]]:
    """Zwraca (ok, lista_problemów) dla destylatu SAT."""
    problems: List[str] = []
    if not text or not text.strip():
        return False, ["Pusty destylat."]

    tokens = estimate_tokens(text)
    if tokens > MAX_DISTILLATE_TOKENS:
        problems.append(f"Destylat przekracza limit: ~{tokens} tokenów (max {MAX_DISTILLATE_TOKENS}).")

    lines = text.splitlines()
    if len(lines) > MAX_RAW_DUMP_LINES:
        problems.append("Podejrzenie surowego zrzutu danych (zbyt wiele linii).")
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        problems.append(f"Podejrzenie surowego zrzutu danych (linia dłuższa niż {MAX_LINE_CHARS} znaków).")

    normalized = _strip_diacritics(text)
    for name, pattern in _SAT_SECTIONS.items():
        if not pattern.search(normalized):
            problems.append(f"Brak sekcji SAT: {name} (oczekiwany nagłówek na początku linii).")

    for p_pat in _PERSONIFICATION_PATTERNS:
        if p_pat.search(text):
            problems.append("Destylat zawiera niedozwoloną personifikację AI / nowomowę zamiast obiektywnych faktów.")
            break
    return (not problems, problems)


DISPATCH_FORBIDDEN_PAIRS = {
    # Inżynierowie kodu nie mogą brać zadań badań domenowych i syntezy wiedzy
    ("maks", "engineer", "kinga", "database_code", "db_architect", "silas"): {
        "research", "zwiad", "knowledge", "knowledge-synthesis", "synthesis", "taxonomy"
    },
    # Badacze i syntetycy wiedzy nie mogą brać zadań implementacji kodu/baz danych
    ("klara", "researcher", "edyta", "knowledge", "knowledge_processor", "pax", "penn"): {
        "code", "refactor", "feature", "database", "sql", "schema"
    },
    # Audytor nie bierze zadań implementacji kodu/baz
    ("ada", "auditor", "quality_gate"): {
        "code", "refactor", "feature", "database", "sql", "schema"
    },
}


def validate_dispatch_guard(
    agent_identifier: str, agent_assigned_name: str, task_type: str
) -> Tuple[bool, Optional[str]]:
    """Deterministyczna walidacja domenowa w kodzie (Dispatch Guard Poziom 3).
    Rzuca Fail-Fast w razie próby skierowania zadania poza domenę agenta."""
    norm_id = str(agent_identifier).lower().strip()
    norm_name = str(agent_assigned_name).lower().strip()
    norm_task = str(task_type).lower().strip()

    for roles, forbidden_tasks in DISPATCH_FORBIDDEN_PAIRS.items():
        if norm_id in roles or norm_name in roles:
            if norm_task in forbidden_tasks:
                return False, (
                    f"Dispatch Guard (Poziom 3): Niedozwolona dyspozycja! "
                    f"Agent '{agent_assigned_name}' ({agent_identifier}) nie może realizować zadania typu '{task_type}'. "
                    f"Naruszenie separacji domenowej (Reguła 8 i Konstytucja LAB)."
                )
    return True, None

