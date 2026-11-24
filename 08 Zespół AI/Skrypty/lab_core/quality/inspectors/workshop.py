"""lab_core.quality.inspectors.workshop - Inspektor higieny Warsztatu i ochrony myśli człowieka
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import re
from pathlib import Path
import sys
from typing import List, Optional, Set, Tuple

from lab_core.config import ConfigError
from lab_core.constants import AGENDA_THREAD_TYPES, IGNORED_FILES, THOUGHT_PROCESSED_MARKERS
from lab_core.markdown import normalize_nfc
from lab_core.quality.context import QualityContext, QualityInspector


def _line_indent(line: str) -> int:
    """Zwraca szerokość wcięcia wiodącego (tab = 4 spacje)."""
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _strip_trailing_progress_note(task_body: str) -> str:
    """Usuwa końcową adnotację postępu '*(...)*' aktualizowaną w trakcie sesji."""
    return re.sub(r"\s*\*\((?:(?!\)\.?\*).)*\)\.?\*\.?\s*$", "", task_body).strip()


def extract_agenda_section(content: str, section_kind: str) -> str:
    """Zwraca treść sekcji 'threads' (## 🎯) lub 'thoughts' (## ✍️) z Agendy Roboczej."""
    if section_kind == "threads":
        m = re.search(r"^##\s*(?:🎯|.*Główne Wątki).*$", content, flags=re.MULTILINE | re.IGNORECASE)
    elif section_kind == "thoughts":
        m = re.search(r"^##\s*(?:✍️|.*Dosłowny Zapis).*$", content, flags=re.MULTILINE | re.IGNORECASE)
    else:
        return ""
    if not m:
        return ""
    after_header = content[m.end():]
    next_h2 = re.search(r"^##\s+", after_header, flags=re.MULTILINE)
    return after_header[:next_h2.start()] if next_h2 else after_header


def extract_unfinished_agenda_tasks(content: str) -> List[str]:
    """Stanowy parser wyciągający 100% otwartych zadań (- [ ]) wraz z poddrzewem wcięć z sekcji '## 🎯'."""
    threads_body = extract_agenda_section(content, "threads")
    if not threads_body:
        return []

    tasks: List[str] = []
    current_block: List[str] = []
    pending_empty: List[str] = []
    root_indent: int = 0
    open_task_re = re.compile(r"^\s*-\s*\[\s*\]\s+")

    def flush_block() -> None:
        nonlocal current_block, pending_empty
        if current_block:
            tasks.append("\n".join(current_block))
            current_block = []
        pending_empty = []

    for line in threads_body.splitlines():
        stripped = line.strip()
        if stripped == "---":
            flush_block()
            continue

        if not stripped:
            if current_block:
                pending_empty.append("")
            continue

        indent = _line_indent(line)
        if current_block:
            if indent > root_indent:
                if pending_empty:
                    current_block.extend(pending_empty)
                    pending_empty = []
                trimmed = line.expandtabs(4)[root_indent:] if root_indent > 0 else line
                current_block.append(trimmed)
                continue
            else:
                flush_block()

        if open_task_re.match(line):
            root_indent = indent
            trimmed = line.expandtabs(4)[root_indent:] if root_indent > 0 else line
            current_block = [trimmed]
            pending_empty = []

    flush_block()
    return tasks


def detect_unprocessed_thought_blocks(content: str) -> List[str]:
    """Wykrywa nieprzetworzone bloki myśli oddzielone '---' w sekcji '## ✍️' Agendy Roboczej.

    Blok uznaje się za przetworzony, jeśli zawiera marker (np. '<!-- processed -->'),
    adnotację przeniesienia ('> ✅' / '> 📦') lub wszystkie jego linie merytoryczne są przekreślone ('~~...~~').
    """
    thoughts_body = extract_agenda_section(content, "thoughts")
    if not thoughts_body:
        return []

    raw_blocks = re.split(r"^\s*---\s*$", thoughts_body, flags=re.MULTILINE)
    unprocessed: List[str] = []
    for block in raw_blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # Pomiń puste bloki oraz pojedyncze linie metadanych źródła typu *(Źródło: ...)* / *(Wątek kroczący ...)*
        meaningful = [
            ln for ln in lines
            if not (
                (ln.startswith("*(Źródło:") and ln.endswith(")*"))
                or (ln.startswith("*(Wątek kroczący") and ln.endswith(")*"))
            )
        ]
        if not meaningful:
            continue

        block_lower = block.lower()
        has_marker = any(marker in block_lower for marker in THOUGHT_PROCESSED_MARKERS)
        has_quote_note = any(ln.startswith("> ✅") or ln.startswith("> 📦") for ln in meaningful)
        all_strikethrough = all(ln.startswith("~~") and ln.endswith("~~") for ln in meaningful)

        if not (has_marker or has_quote_note or all_strikethrough):
            unprocessed.append("\n".join(meaningful))

    return unprocessed


def mark_thought_blocks_processed(
    content: str,
    block_indices: Optional[List[int]] = None,
    note: Optional[str] = None,
) -> Tuple[str, int]:
    """Deterministycznie oznacza nieprzetworzone bloki myśli w sekcji '## ✍️' jako '<!-- processed -->'.

    Zachowuje w 100% oryginalny tekst użytkownika (Reguła 1) — jedynie dopisuje znacznik
    na końcu wybranego nieprzetworzonego bloku.
    Zwraca (zaktualizowana_treść, liczba_oznaczonych_bloków).
    """
    m = re.search(r"^##\s*(?:✍️|.*Dosłowny Zapis).*$", content, flags=re.MULTILINE | re.IGNORECASE)
    if not m:
        return content, 0

    after_header = content[m.end():]
    next_h2 = re.search(r"^##\s+", after_header, flags=re.MULTILINE)
    section_start = m.end()
    section_end = section_start + next_h2.start() if next_h2 else len(content)
    thoughts_body = content[section_start:section_end]

    parts = re.split(r"(^\s*---\s*$)", thoughts_body, flags=re.MULTILINE)
    target_set = set(block_indices) if block_indices else None
    unprocessed_idx = 0
    marked_count = 0
    new_parts: List[str] = []

    for part in parts:
        if re.match(r"^\s*---\s*$", part):
            new_parts.append(part)
            continue

        lines = [ln.strip() for ln in part.splitlines() if ln.strip()]
        meaningful = [
            ln for ln in lines
            if not (
                (ln.startswith("*(Źródło:") and ln.endswith(")*"))
                or (ln.startswith("*(Wątek kroczący") and ln.endswith(")*"))
            )
        ]
        if not meaningful:
            new_parts.append(part)
            continue

        part_lower = part.lower()
        has_marker = any(marker in part_lower for marker in THOUGHT_PROCESSED_MARKERS)
        has_quote_note = any(ln.startswith("> ✅") or ln.startswith("> 📦") for ln in meaningful)
        all_strikethrough = all(ln.startswith("~~") and ln.endswith("~~") for ln in meaningful)

        if has_marker or has_quote_note or all_strikethrough:
            new_parts.append(part)
            continue

        unprocessed_idx += 1
        if target_set is None or unprocessed_idx in target_set:
            today_str = datetime.date.today().isoformat()
            note_text = note.strip() if note and note.strip() else "przetworzono do Agendy"
            suffix_lines = [
                f"> ✅ Przetworzono ({today_str}): {note_text}",
                "<!-- processed -->",
            ]
            stripped_tail = part.rstrip()
            trailing_ws = part[len(stripped_tail):]
            if not trailing_ws:
                trailing_ws = "\n"
            updated_part = stripped_tail + "\n" + "\n".join(suffix_lines) + trailing_ws
            new_parts.append(updated_part)
            marked_count += 1
        else:
            new_parts.append(part)

    if marked_count == 0:
        return content, 0

    new_content = content[:section_start] + "".join(new_parts) + content[section_end:]
    return new_content, marked_count


class WorkshopAndThoughtsInspector(QualityInspector):
    """Weryfikuje ochronę myśli człowieka oraz architekturę Czystego Stołu Warsztatu."""
    name: str = "workshop_and_thoughts"

    def inspect(self, ctx: QualityContext) -> None:
        policy = getattr(ctx.config, "human_thoughts_policy", None)
        is_active = bool(
            policy
            and getattr(policy, "protect_unorganized_thoughts", False)
            and getattr(policy, "forbid_auto_sprint_injection", False)
        )
        ctx.stats["human_thoughts_protection_active"] = is_active

        if not is_active:
            ctx.add_finding(
                level="error",
                category="policies",
                message=(
                    "Polityka ochrony myśli ludzkiej [policies.human_thoughts] jest nieaktywna "
                    "lub wyłączona w lab.config.toml. Bezwzględny zakaz samowolnego dopisywania myśli do sprintu musi być aktywny."
                ),
            )

        try:
            workshop_room = ctx.config.get_room_by_role("daily-workbench")
        except ConfigError:
            try:
                workshop_room = ctx.config.get_room("02")
            except ConfigError:
                return

        workshop_path = ctx.vault_root / workshop_room.path
        if not workshop_path.is_dir():
            return

        # 1. Sprawdź czy w Warsztacie nie ma nieautoryzowanych plików luźnych
        ALLOWED_WORKSHOP_ENTRIES = {
            "dashboard lab.md",
            "narzędziownia.md",
            "_archiwum",
            ".ds_store",
        }
        workshop_tools = getattr(getattr(ctx.config, "code_layout", None), "workshop_tools", "02 Warsztat/Skrypty")
        workshop_tools_parts = Path(workshop_tools).parts
        if (
            len(workshop_tools_parts) >= 2
            and normalize_nfc(workshop_tools_parts[0]).lower() == normalize_nfc(workshop_room.path).lower()
        ):
            ALLOWED_WORKSHOP_ENTRIES.add(normalize_nfc(workshop_tools_parts[1]).lower())
        else:
            ALLOWED_WORKSHOP_ENTRIES.add("skrypty")
        for item in workshop_path.iterdir():
            if item.name.startswith(".") and item.name != ".DS_Store":
                continue
            norm_raw = normalize_nfc(item.name)
            norm_name = norm_raw.lower()
            is_daily_wip = bool(item.is_file() and re.match(r"^\d{4}-\d{2}-\d{2}.*\.md$", item.name, re.IGNORECASE))
            is_tmp_note = bool(
                item.is_file()
                and norm_raw.startswith("TMP - ")
                and norm_raw.lower().endswith(".md")
                and norm_raw[len("TMP - "):-3].strip()
            )
            if (
                norm_name not in ALLOWED_WORKSHOP_ENTRIES
                and item.name not in IGNORED_FILES
                and not is_daily_wip
                and not is_tmp_note
            ):
                rel_p = item.relative_to(ctx.vault_root)
                ctx.add_finding(
                    level="warning",
                    category="workshop",
                    path=str(rel_p),
                    room=workshop_room.id,
                    message=(
                        f"Naruszenie czystości Warsztatu: wykryto nieoczekiwany element '{item.name}' w '{workshop_room.path}'. "
                        f"Wyczyszczony Warsztat powinien zawierać wyłącznie plik 'Dashboard LAB.md', dzienną agendę WIP ('YYYY-MM-DD ...'), tymczasowe notatki robocze ('TMP - <tytuł>.md') oraz '_archiwum/'."
                    ),
                )

        # 2. Rozwiąż aktywny plik Warsztatu (preferencja dla dziennej Agendy Roboczej)
        active_agendas = sorted([f for f in workshop_path.glob("*– Agenda Robocza.md") if f.is_file()], key=lambda x: x.name, reverse=True)
        if active_agendas:
            main_file = active_agendas[0]
        else:
            main_file = workshop_path / (workshop_room.entry_file or "Dashboard LAB.md")

        if not main_file.is_file():
            ctx.stats["workshop_hygiene_clean"] = True
            return

        try:
            content = main_file.read_text(encoding="utf-8")
        except Exception as ex:
            ctx.add_finding(
                level="error",
                category="workshop",
                path=str(main_file.relative_to(ctx.vault_root)),
                room=workshop_room.id,
                message=f"Nie można odczytać pliku Warsztatu: {ex}",
            )
            return

        FORBIDDEN_SPRINT_MARKERS = [
            "[unconfirmed_thought]",
            "[nieautoryzowane]",
            "[auto_injection]",
            "[samowolny_wpis]",
        ]
        content_lower = content.lower()
        for marker in FORBIDDEN_SPRINT_MARKERS:
            if marker in content_lower:
                ctx.add_finding(
                    level="error",
                    category="sprint",
                    path=str(main_file.relative_to(ctx.vault_root)),
                    room=workshop_room.id,
                    message=(
                        f"Wykryto nieautoryzowane wstrzyknięcie myśli w Warsztacie Współpracy "
                        f"(znacznik '{marker}'). Wpisy w sprincie wymagają potwierdzenia człowieka."
                    ),
                )

        # 3. Weryfikacja czystości Warsztatu: brak zakazanych sekcji (Pokoje Robocze, Zrzut ze Skrzynki, Filar IV)
        forbidden_sections = [
            "Pokoje Robocze",
            "Zrzut ze Skrzynki",
            "Filar IV",
        ]
        workshop_clean = True
        for forbidden in forbidden_sections:
            if re.search(rf"^#+\s+.*{re.escape(forbidden)}", content, flags=re.MULTILINE | re.IGNORECASE):
                workshop_clean = False
                ctx.add_finding(
                    level="error",
                    category="workshop",
                    path=str(main_file.relative_to(ctx.vault_root)),
                    room=workshop_room.id,
                    message=(
                        f"Wykryto zakazaną sekcję '{forbidden}' w Warsztacie Współpracy. "
                        f"Zgodnie z wolą Właściciela Systemu i [policies.workshop] "
                        f"sekcje pokojów roboczych, zrzutów ze skrzynki oraz badań (RS) "
                        f"są bezwzględnie usunięte z Warsztatu."
                    ),
                )

        # 4. Weryfikacja typu zadań na Warsztacie (tylko PR, OP; zakaz RS w sprincie)
        sprint_m = re.search(r"## 🎯 Na tapecie \(Bieżący Sprint\)(.*?)(?=\n## |\Z)", content, flags=re.DOTALL)
        if sprint_m:
            sprint_text = sprint_m.group(1)
            for line in sprint_text.splitlines():
                l_str = line.strip()
                if l_str.startswith("- [") and ("RS-" in l_str or "[[RS-" in l_str):
                    workshop_clean = False
                    ctx.add_finding(
                        level="error",
                        category="sprint",
                        path=str(main_file.relative_to(ctx.vault_root)),
                        room=workshop_room.id,
                        message=(
                            f"Wykryto zadanie badawcze RS w bieżącym sprincie na Warsztacie: '{l_str}'. "
                            f"Zadania typu 'research' (RS) należą wyłącznie do Bazy Wiedzy (05 Baza Wiedzy). "
                            f"Na Warsztacie dopuszczalne są wyłącznie zadania: PR, OP oraz nienumerowane pozycje bieżące."
                        ),
                    )

        # 5. Wykrywanie nieprzetworzonych bloków myśli w sekcji '## ✍️' Agendy Roboczej
        unprocessed_blocks = detect_unprocessed_thought_blocks(content)
        ctx.stats["unprocessed_thought_blocks"] = len(unprocessed_blocks)
        if unprocessed_blocks:
            allowed_tags = ", ".join(f"[{t}]" for t in AGENDA_THREAD_TYPES)
            # Nieoznaczony blok myśli to OSTRZEŻENIE także w trybie strict (decyzja właściciela):
            # nie blokuje kwitu zamknięcia, status skarbca = ATTENTION.
            ctx.add_finding(
                level="warning",
                category="agenda_thoughts",
                path=str(main_file.relative_to(ctx.vault_root)),
                room=workshop_room.id,
                message=(
                    f"W sekcji '## ✍️' Agendy Roboczej wykryto {len(unprocessed_blocks)} nieprzetworzonych bloków myśli "
                    f"(rozdzielonych '---'). Wymagają streszczenia przez Edytę do '## 🎯' (tagi: {allowed_tags}) "
                    f"i oznaczenia deterministycznym helperem: "
                    f"`PYTHONPATH=\"08 Zespół AI/Skrypty\" python3 -m lab_core.quality.inspectors.workshop --agenda \"{main_file.relative_to(ctx.vault_root)}\" --mark-processed`."
                ),
            )

        # 6. Twarda bramka struktury i kompletności rolowania Agendy Roboczej
        is_agenda_file = bool(re.match(r"^\d{4}-\d{2}-\d{2}\s*–\s*Agenda Robocza\.md$", main_file.name, re.IGNORECASE))
        if is_agenda_file:
            rel_agenda = str(main_file.relative_to(ctx.vault_root))
            has_threads_h2 = bool(re.search(r"^##\s*(?:🎯|.*Główne Wątki)", content, flags=re.MULTILINE | re.IGNORECASE))
            has_thoughts_h2 = bool(re.search(r"^##\s*(?:✍️|.*Dosłowny Zapis)", content, flags=re.MULTILINE | re.IGNORECASE))
            if not (has_threads_h2 and has_thoughts_h2):
                workshop_clean = False
                ctx.add_finding(
                    level="error",
                    category="agenda_rollover",
                    path=rel_agenda,
                    room=workshop_room.id,
                    message=(
                        "Agenda Robocza nie spełnia kanonicznego szablonu: brak wymaganych nagłówków H2 "
                        "('## 🎯 Główne Wątki Robocze na Dziś' oraz '## ✍️ Dosłowny Zapis Porannych Myśli ...')."
                    ),
                )

            archive_root = workshop_path / "_archiwum"
            if archive_root.is_dir():
                archived_agendas = sorted(
                    [
                        f for f in archive_root.rglob("*– Agenda Robocza.md")
                        if f.is_file() and f.name < main_file.name
                    ],
                    key=lambda x: x.name,
                    reverse=True,
                )
                if archived_agendas:
                    prev_agenda = archived_agendas[0]
                    try:
                        prev_content = prev_agenda.read_text(encoding="utf-8")
                    except Exception:
                        prev_content = ""

                    if prev_content:
                        active_threads_norm = normalize_nfc(extract_agenda_section(content, "threads"))
                        today_tasks: List[str] = []
                        for line in active_threads_norm.splitlines():
                            m_task = re.match(r"^\s*-\s*\[[\sxX]\]\s*(.*)$", line)
                            if m_task:
                                tb = normalize_nfc(m_task.group(1).strip())
                                cb = _strip_trailing_progress_note(tb)
                                today_tasks.append(cb if cb else tb)

                        missing_tasks_count = 0
                        stop_words = {"dla", "oraz", "jest", "sie", "się", "pod", "nad", "nie", "tak", "gdy", "aby", "the", "and", "lub", "wraz", "przez", "jako"}

                        for task in extract_unfinished_agenda_tasks(prev_content):
                            first_line = task.splitlines()[0].strip() if task.splitlines() else ""
                            task_body = normalize_nfc(re.sub(r"^\s*-\s*\[[\sxX]\]\s*", "", first_line).strip())
                            core_body = _strip_trailing_progress_note(task_body)
                            if not task_body:
                                continue

                            # 1. Dosłowne dopasowanie
                            if task_body in active_threads_norm or (core_body and core_body in active_threads_norm):
                                continue

                            # 2. SequenceMatcher (ratio >= 0.50) oraz zbieżność słów kluczowych
                            target = (core_body if core_body else task_body).strip()
                            target_clean = re.sub(r"^\[(?:PROMPT|PYTANIE|ZADANIE|DECYZJA)\]\s*", "", target, flags=re.IGNORECASE).strip()
                            target_cmp = (target_clean if target_clean else target).lower()
                            target_words = {w for w in re.findall(r"\b\w{3,}\b", target_cmp) if w not in stop_words}

                            matched = False
                            for cand in today_tasks:
                                cand_clean = re.sub(r"^\[(?:PROMPT|PYTANIE|ZADANIE|DECYZJA)\]\s*", "", cand, flags=re.IGNORECASE).strip()
                                cand_cmp = (cand_clean if cand_clean else cand).lower()

                                # Ratio SequenceMatcher >= 0.50
                                if difflib.SequenceMatcher(None, target_cmp, cand_cmp).ratio() >= 0.50:
                                    matched = True
                                    break

                                # Pokrycie słów kluczowych
                                cand_words = {w for w in re.findall(r"\b\w{3,}\b", cand_cmp) if w not in stop_words}
                                if target_words and cand_words:
                                    overlap = target_words & cand_words
                                    if len(overlap) >= 2 or (len(target_words) == 1 and len(overlap) == 1) or (len(overlap) / len(target_words) >= 0.50):
                                        matched = True
                                        break

                            if not matched:
                                missing_tasks_count += 1

                        active_thoughts_norm = normalize_nfc(extract_agenda_section(content, "thoughts")).replace("~~", "")
                        missing_thoughts_count = 0
                        for block in detect_unprocessed_thought_blocks(prev_content):
                            first_thought_line = normalize_nfc(block.splitlines()[0].strip()) if block.splitlines() else ""
                            if first_thought_line and first_thought_line not in active_thoughts_norm:
                                missing_thoughts_count += 1

                        if missing_tasks_count > 0 or missing_thoughts_count > 0:
                            ctx.add_finding(
                                level="warning",
                                category="agenda_rollover",
                                path=rel_agenda,
                                room=workshop_room.id,
                                message=(
                                    f"Niekompletne rolowanie z '{prev_agenda.name}': pominięto "
                                    f"{missing_tasks_count} otwartych zadań (- [ ]) w '## 🎯' oraz "
                                    f"{missing_thoughts_count} nieprzetworzonych bloków myśli w '## ✍️'."
                                ),
                            )

        ctx.stats["workshop_hygiene_clean"] = workshop_clean


def main(argv: Optional[List[str]] = None) -> int:
    """Narzędzie CLI do listowania i deterministycznego oznaczania bloków myśli w Agendzie jako <!-- processed -->."""
    parser = argparse.ArgumentParser(
        description="Deterministyczne oznaczanie przetworzonych bloków myśli w Agendzie Roboczej (Reguła 1)."
    )
    parser.add_argument("--agenda", required=True, help="Ścieżka do pliku Agendy Roboczej (.md)")
    parser.add_argument("--list-unprocessed", action="store_true", help="Wypisz nieprzetworzone bloki myśli z indeksami")
    parser.add_argument("--mark-processed", action="store_true", help="Oznacz wybrane (lub wszystkie) bloki jako <!-- processed -->")
    parser.add_argument("--blocks", type=str, default=None, help="Lista 1-bazowych indeksów bloków (np. 1,2)")
    parser.add_argument("--note", type=str, default=None, help="Opcjonalna adnotacja '> ✅ ...' dopisywana pod markerem")
    args = parser.parse_args(argv)

    agenda_path = Path(args.agenda).expanduser().resolve()
    if not agenda_path.is_file():
        print(f"BŁĄD: Nie znaleziono pliku Agendy: {agenda_path}", file=sys.stderr)
        return 1

    content = agenda_path.read_text(encoding="utf-8")
    if args.list_unprocessed:
        blocks = detect_unprocessed_thought_blocks(content)
        if not blocks:
            print("Brak nieprzetworzonych bloków myśli w sekcji '## ✍️'.")
            return 0
        for idx, blk in enumerate(blocks, start=1):
            preview = blk.splitlines()[0][:100]
            print(f"[{idx}] {preview}")
        return 0

    if args.mark_processed:
        indices: Optional[List[int]] = None
        if args.blocks:
            indices = [int(x.strip()) for x in args.blocks.split(",") if x.strip()]
        new_content, count = mark_thought_blocks_processed(content, block_indices=indices, note=args.note)
        if count > 0:
            agenda_path.write_text(new_content, encoding="utf-8")
        print(f"Oznaczono bloków jako <!-- processed -->: {count}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
