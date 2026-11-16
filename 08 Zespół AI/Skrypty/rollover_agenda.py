#!/usr/bin/env python3
"""rollover_agenda.py - Deterministyczne rolowanie i archiwizacja Agendy Roboczej w Warsztacie

Standard inżynieryjny:
- 100% Python Standard Library (Zero-Pip).
- Pełna integracja z core_config.ConfigManager.
- Gwarancja aksjomatu: w 02 Warsztat znajduje się DOKŁADNIE JEDNA aktywna agenda.
- Automatyczna migracja niezrealizowanych zadań (- [ ]) do nowej agendy.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigError, ConfigManager, find_vault_root
from lab_core.constants import AGENDA_THREAD_TYPES
from lab_core.markdown import normalize_nfc
from lab_core.quality.inspectors.workshop import (
    detect_unprocessed_thought_blocks,
    extract_agenda_section,
    extract_unfinished_agenda_tasks,
)


_THREAD_TYPE_RE = re.compile(
    r"^\s*-\s*\[[\sxX]\]\s*\[(" + "|".join(AGENDA_THREAD_TYPES) + r")\]",
    re.IGNORECASE,
)


def parse_thread_type(line: str) -> Optional[str]:
    """Zwraca typ wątku ([PROMPT], [PYTANIE], [ZADANIE], [DECYZJA]) z linii checkboxa lub None."""
    m = _THREAD_TYPE_RE.match(line)
    return m.group(1).upper() if m else None


class AgendaRollover:
    """Zarządza cyklem życia Agendy Roboczej w pokoju Warsztatu."""

    def __init__(self, vault_root: Optional[Path] = None, config_path: Optional[Path] = None) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root(_script_dir)
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            self.ws_dir = self.vault_root / ws_room.path
        except ConfigError:
            self.ws_dir = self.vault_root / "02 Warsztat"

    def scan_agendas(self) -> List[Tuple[datetime.date, Path]]:
        """Zwraca posortowaną listę plików agend w Warsztacie wraz z ich datami."""
        if not self.ws_dir.is_dir():
            return []

        results = []
        pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*–\s*Agenda Robocza\.md$", re.IGNORECASE)
        for item in self.ws_dir.iterdir():
            if not item.is_file():
                continue
            m = pattern.match(item.name)
            if m:
                try:
                    dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
                    results.append((dt, item))
                except ValueError:
                    continue

        results.sort(key=lambda x: x[0])
        return results

    def extract_unfinished_tasks(self, content: str) -> List[str]:
        """Wyciąga 100% otwartych zadań (- [ ]) wraz z poddrzewem wcięć z sekcji '## 🎯'."""
        return extract_unfinished_agenda_tasks(content)

    def _get_template_path(self) -> Path:
        try:
            gov_room = self.config.get_room_by_role("governance-and-engine")
            gov_dir = self.vault_root / gov_room.path
        except ConfigError:
            gov_dir = self.vault_root / "08 Zespół AI"
        return gov_dir / "Szablony" / "Szablon Agendy Roboczej.md"

    def render_template(self, target_date: datetime.date) -> str:
        """Renderuje kanoniczny szablon Agendy Roboczej na podstawie wzorca z 08 Zespół AI/Szablony/."""
        user_name = getattr(self.config, "user_name", None) or (self.config.user.name if hasattr(self.config, "user") else "Owner")
        coord_name = self.config.get_agent_name("coordinator")
        instance_name = self.config.instance_name
        date_str = target_date.isoformat()

        tpl_path = self._get_template_path()
        if tpl_path.is_file():
            raw = tpl_path.read_text(encoding="utf-8")
        else:
            raw = (
                "---\n"
                "type: workbench-agenda\n"
                'title: "{date} – Agenda Robocza Dnia"\n'
                "date: {date}\n"
                'instance: "{instance_name}"\n'
                "status: in_progress\n"
                "tags:\n"
                "  - agenda-dnia\n"
                "  - warsztat\n"
                "  - wip\n"
                "---\n\n"
                "# 🛠️ Agenda Robocza Dnia ({date})\n\n"
                "> **Żywy stół warsztatowy ({user_name} ↔ {coord_name})**  \n"
                "> *Swobodny zarys tematów, uzupełnień i zadań na dziś. W miarę realizacji wspólnie odznaczamy statusy i linkujemy wytworzone materiały.*\n\n"
                "---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli {user_name} (Skrzynka & Brudnopis)\n"
            )

        return (
            raw.replace("{date}", date_str)
            .replace("{instance_name}", instance_name)
            .replace("{user_name}", user_name)
            .replace("{coord_name}", coord_name)
        )

    def archive_old_agenda(self, date_val: datetime.date, file_path: Path, dry_run: bool = False) -> Path:
        """Oznacza agendę jako zarchiwizowaną i przenosi do _archiwum/YYYY/MM/."""
        content = file_path.read_text(encoding="utf-8")
        # Aktualizacja frontmattera
        if "status: in_progress" in content:
            content = content.replace("status: in_progress", f"status: archived\narchived_date: {datetime.date.today().isoformat()}")
        elif "status:" in content and "archived" not in content:
            content = re.sub(r"status:\s*\w+", f"status: archived\narchived_date: {datetime.date.today().isoformat()}", content)

        year_str = date_val.strftime("%Y")
        month_str = date_val.strftime("%m")
        archive_dir = self.ws_dir / "_archiwum" / year_str / month_str
        target_path = archive_dir / file_path.name

        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            shutil.move(file_path, target_path)

        return target_path

    def run_rollover(self, target_date: Optional[datetime.date] = None, dry_run: bool = False) -> dict:
        """Przeprowadza pełne rolowanie: archiwizuje stare agendy i scala otwarte zadania oraz nieprzetworzone myśli."""
        today = target_date or datetime.date.today()
        agendas = self.scan_agendas()

        archived = []
        carried_over_tasks = []
        carried_over_thoughts: List[Tuple[datetime.date, str, str]] = []

        for dt, p in agendas:
            if dt < today:
                content = p.read_text(encoding="utf-8")
                tasks = self.extract_unfinished_tasks(content)
                carried_over_tasks.extend(tasks)
                for block in detect_unprocessed_thought_blocks(content):
                    carried_over_thoughts.append((dt, p.name, block))
                arch_path = self.archive_old_agenda(dt, p, dry_run=dry_run)
                archived.append((str(p.name), str(arch_path)))

        today_agenda_name = f"{today.isoformat()} – Agenda Robocza.md"
        today_agenda_path = self.ws_dir / today_agenda_name

        if not today_agenda_path.is_file() and not dry_run:
            self.ws_dir.mkdir(parents=True, exist_ok=True)
            today_agenda_path.write_text(self.render_template(today), encoding="utf-8")

        merged_count = 0
        merged_thoughts_count = 0
        if today_agenda_path.is_file() and not dry_run:
            today_content = today_agenda_path.read_text(encoding="utf-8")
            updated = False

            if carried_over_tasks:
                active_threads_norm = normalize_nfc(extract_agenda_section(today_content, "threads"))
                missing_tasks = []
                for t in carried_over_tasks:
                    first_line = t.splitlines()[0].strip() if t.splitlines() else ""
                    task_body = normalize_nfc(re.sub(r"^\s*-\s*\[[\sxX]\]\s*", "", first_line).strip())
                    if task_body and task_body not in active_threads_norm:
                        missing_tasks.append(t)

                if missing_tasks:
                    insertion = "\n\n" + "\n".join(missing_tasks)
                    threads_hdr = re.search(r"^##\s*(?:🎯|.*Główne Wątki).*$", today_content, flags=re.MULTILINE | re.IGNORECASE)
                    if threads_hdr:
                        idx = threads_hdr.end()
                        rest = today_content[idx:].lstrip("\n")
                        today_content = today_content[:idx] + insertion + "\n\n" + rest
                    else:
                        today_content = today_content.rstrip() + insertion + "\n"
                    merged_count = len(missing_tasks)
                    updated = True

            if carried_over_thoughts:
                active_thoughts_norm = normalize_nfc(extract_agenda_section(today_content, "thoughts")).replace("~~", "")
                missing_thoughts: List[str] = []
                for src_dt, src_name, block in carried_over_thoughts:
                    first_thought = normalize_nfc(block.splitlines()[0].strip()) if block.splitlines() else ""
                    if first_thought and first_thought not in active_thoughts_norm:
                        note_hdr = f"*(Wątek kroczący z {src_dt.isoformat()} — źródło: `{src_name}`, zapis oryginalny 1:1)*"
                        missing_thoughts.append(f"---\n\n{note_hdr}\n\n{block}")

                if missing_thoughts:
                    thoughts_insertion = "\n\n" + "\n\n".join(missing_thoughts) + "\n"
                    today_content = today_content.rstrip() + thoughts_insertion
                    merged_thoughts_count = len(missing_thoughts)
                    updated = True

            if updated:
                today_agenda_path.write_text(today_content, encoding="utf-8")

        carried_over_by_type = {}
        for t in carried_over_tasks:
            ttype = parse_thread_type(t.splitlines()[0]) if t.splitlines() else None
            key = ttype or "UNTAGGED"
            carried_over_by_type[key] = carried_over_by_type.get(key, 0) + 1

        return {
            "today": today.isoformat(),
            "archived_count": len(archived),
            "archived_files": archived,
            "tasks_carried_over": len(carried_over_tasks),
            "tasks_merged_into_today": merged_count,
            "thoughts_carried_over": len(carried_over_thoughts),
            "thoughts_merged_into_today": merged_thoughts_count,
            "carried_over_by_type": carried_over_by_type,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rolowanie i archiwizacja agendy roboczej")
    parser.add_argument("--vault-root", help="Katalog główny skarbca")
    parser.add_argument("--dry-run", action="store_true", help="Tryb symulacji bez zapisu")
    parser.add_argument("--date", help="Data docelowa w formacie YYYY-MM-DD")
    args = parser.parse_args()

    target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    rollover = AgendaRollover(vault_root=Path(args.vault_root) if args.vault_root else None)
    res = rollover.run_rollover(target_date=target_date, dry_run=args.dry_run)

    print(f"✅ Rolowanie agendy zakończone:")
    print(f"  - Dzisiejsza data: {res['today']}")
    print(f"  - Zarchiwizowane stare agendy: {res['archived_count']}")
    print(f"  - Przeniesione otwarte zadania: {res['tasks_carried_over']} (nowo dopisane: {res['tasks_merged_into_today']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
