#!/usr/bin/env python3
"""lab_core.dashboards - Deterministyczny generator pulpitu 'Dashboard LAB.md'

Standard inżynieryjny:
- 100% Python Standard Library (pathlib, re, datetime, argparse, json).
- Pełna integracja z core_config.ConfigManager.
- Zero zewnętrznych zależności pip.

Odpowiada za:
1. Agregację metryk ilościowych (PR, OP, Notatki, consumed: false, Skrzynka).
2. Wygenerowanie mobilnego i czytelnego Dashboardu LAB w:
   '02 Warsztat/Dashboard LAB.md'.
3. Przykrycie wewnętrznej architektury silnika (AI / Zasoby / Bazy danych)
   przejrzystym widokiem operacyjnym dla człowieka.
4. Obsługę rozwijanych sekcji (<details>) pod Obsidian Desktop i Mobile.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Blokada tworzenia plików bytecode __pycache__
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigError, ConfigManager, find_vault_root

IGNORED_FILES = {".DS_Store", "desktop.ini"}
DOCUMENT_EXTS = {".pdf", ".docx", ".doc", ".epub", ".xlsx", ".pptx", ".csv"}


@dataclass
class ProjectItem:
    pr_id: str
    name: str
    status: str
    phase: str
    updated: str
    card_link: str


@dataclass
class OperationItem:
    op_id: str
    name: str
    frequency: str
    status: str
    link: str


@dataclass
class TaskItem:
    td_id: str
    name: str
    status: str
    owner: str
    context: str


@dataclass
class NoteItem:
    title: str
    rel_path: str
    created: str
    consumed: bool
    mtime: float


class DashboardBuilder:
    """Buduje deterministyczny Kokpit LAB."""

    def __init__(self, vault_root: Optional[Path] = None, config_path: Optional[Path] = None) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root(_script_dir)
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)

    def scan_inbox(self) -> List[Path]:
        """Zwraca elementy oczekujące w Skrzynce."""
        try:
            inbox_room = self.config.get_room_by_role("ingestion")
            inbox_dir = self.vault_root / inbox_room.path
        except ConfigError:
            inbox_dir = self.vault_root / "01 Skrzynka"

        if not inbox_dir.is_dir():
            return []

        items = []
        for p in inbox_dir.iterdir():
            if p.name.startswith(".") or p.name in IGNORED_FILES or p.name == "_archiwum":
                continue
            items.append(p)
        items.sort(key=lambda x: x.name.lower())
        return items

    def scan_projects(self) -> Tuple[List[ProjectItem], List[ProjectItem], List[ProjectItem]]:
        """Parsuje '03 Projekty/_Spis Projektów.md' i zwraca (aktywne, oczekujące, wstrzymane)."""
        active: List[ProjectItem] = []
        pending: List[ProjectItem] = []
        paused: List[ProjectItem] = []

        try:
            proj_room = self.config.get_room_by_role("active-projects")
            proj_dir = self.vault_root / proj_room.path
        except ConfigError:
            proj_dir = self.vault_root / "03 Projekty"

        index_file = proj_dir / "_Spis Projektów.md"
        if not index_file.is_file():
            return active, pending, paused

        content = index_file.read_text(encoding="utf-8", errors="ignore")
        current_section = ""

        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("## 🚀 Projekty w Toku"):
                current_section = "active"
                continue
            elif line_str.startswith("## 🟡 Projekty Oczekujące"):
                current_section = "pending"
                continue
            elif line_str.startswith("## ⏸️ Projekty Wstrzymane"):
                current_section = "paused"
                continue
            elif line_str.startswith("## 📦 Archiwum"):
                current_section = "archive"
                continue

            if re.match(r"^\|\s*`[A-Z]+-\d+`", line_str):
                # Bezpieczne dzielenie tabeli bez niszczenia linków [[plik|etykieta]]
                raw_parts = [p.strip() for p in line_str.strip().strip("|").split("|")]
                # Jeśli ostatni element został rozcięty przez pipe w linku, sklejamy go
                parts = []
                buf = ""
                for rp in raw_parts:
                    if buf:
                        buf += "|" + rp
                        if "]]" in rp:
                            parts.append(buf)
                            buf = ""
                    elif "[[" in rp and "]]" not in rp:
                        buf = rp
                    else:
                        parts.append(rp)
                if len(parts) >= 6:
                    item = ProjectItem(
                        pr_id=parts[0].replace("`", "").strip(),
                        name=parts[1],
                        status=parts[2],
                        phase=parts[3],
                        updated=parts[4],
                        card_link=parts[5],
                    )
                    if current_section == "active":
                        active.append(item)
                    elif current_section == "pending":
                        pending.append(item)
                    elif current_section == "paused":
                        paused.append(item)

        return active, pending, paused

    def scan_operations(self) -> List[OperationItem]:
        """Parsuje '04 Operacje/_Spis Operacji.md' i zwraca aktywne procedury."""
        active_ops: List[OperationItem] = []
        try:
            ops_room = self.config.get_room_by_role("lifecycle-routines")
            ops_dir = self.vault_root / ops_room.path
        except ConfigError:
            ops_dir = self.vault_root / "04 Operacje"

        index_file = ops_dir / "_Spis Operacji.md"
        if not index_file.is_file():
            return active_ops

        content = index_file.read_text(encoding="utf-8", errors="ignore")
        current_section = ""

        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("## 🏛️ Aktywne Domeny"):
                current_section = "active"
                continue
            elif line_str.startswith("## 📦 Archiwum"):
                current_section = "archive"
                continue

            if current_section == "active" and line_str.startswith("| `OP-"):
                safe_line = re.sub(r"(\[\[[^\]|]+)\|([^\]]+\]\])", r"\1\\|\2", line_str)
                parts = [p.strip() for p in re.split(r"(?<!\\)\|", safe_line)[1:-1]]
                if len(parts) >= 5:
                    item = OperationItem(
                        op_id=parts[0].replace("`", "").strip(),
                        name=parts[1],
                        frequency=parts[2],
                        status=parts[3],
                        link=parts[4] if len(parts) > 4 else "",
                    )
                    active_ops.append(item)

        return active_ops

    def scan_workshop(self) -> Tuple[List[str], List[TaskItem]]:
        """
        Parsuje bieżący sprint i checklisty zadań bieżących.
        Źródło prawdy dla sprintu i zadań: najnowsza 'Agenda Robocza.md' w 02 Warsztat.
        """
        sprint_items: List[str] = []
        td_tasks: List[TaskItem] = []

        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            ws_dir = self.vault_root / ws_room.path
        except ConfigError:
            ws_dir = self.vault_root / "02 Warsztat"

        # 1. Sprawdź najnowszą Agendę Roboczą Dnia pod kątem bieżącego sprintu
        if ws_dir.is_dir():
            agendas = [f for f in ws_dir.iterdir() if f.is_file() and "Agenda Robocza" in f.name]
            if agendas:
                latest_agenda = max(agendas, key=lambda f: f.name)
                try:
                    ag_content = latest_agenda.read_text(encoding="utf-8", errors="ignore")
                    in_sprint_section = False
                    for line in ag_content.splitlines():
                        line_str = line.strip()
                        if line_str.startswith("## 🎯 Główne Wątki") or line_str.startswith("## 🎯 Na tapecie"):
                            in_sprint_section = True
                            continue
                        elif in_sprint_section and line_str.startswith("## "):
                            in_sprint_section = False

                        # Pobieraj tylko główne punkty sprintu (bez wcięć podrzędnych)
                        if in_sprint_section and line.startswith("- ["):
                            sprint_items.append(line.rstrip())
                except Exception:
                    pass

        return sprint_items, td_tasks

    def scan_notes(self) -> Tuple[int, List[NoteItem], List[NoteItem]]:
        """
        Skanuje pokój '06 Zasoby/Notatki' i zwraca:
        (łączna_liczba, lista_do_przeczytania_unconsumed, lista_ostatnio_dodanych_recent).
        """
        try:
            assets_room = self.config.get_room_by_role("reference-and-assets")
            notes_dir = self.vault_root / assets_room.path / "Notatki"
        except ConfigError:
            notes_dir = self.vault_root / "06 Zasoby" / "Notatki"

        if not notes_dir.is_dir():
            return 0, [], []

        unconsumed: List[NoteItem] = []
        recent: List[NoteItem] = []
        total_count = 0

        for p in notes_dir.glob("*.md"):
            if p.name.startswith(".") or p.name in IGNORED_FILES or p.name.startswith("_"):
                continue

            total_count += 1
            stat = p.stat()
            mtime = stat.st_mtime

            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # Sprawdź pole consumed
            is_unconsumed = bool(re.search(r"^\s*consumed:\s*false\b", txt, re.MULTILINE))
            created_match = re.search(r"^\s*created:\s*['\"]?([0-9\-]+)['\"]?", txt, re.MULTILINE)
            created_date = created_match.group(1) if created_match else datetime.date.fromtimestamp(mtime).isoformat()

            rel_p = str(p.relative_to(self.vault_root))
            item = NoteItem(
                title=p.stem,
                rel_path=rel_p,
                created=created_date,
                consumed=not is_unconsumed,
                mtime=mtime,
            )

            if is_unconsumed:
                unconsumed.append(item)
            recent.append(item)

        unconsumed.sort(key=lambda x: x.mtime, reverse=True)
        recent.sort(key=lambda x: x.mtime, reverse=True)

        return total_count, unconsumed, recent[:8]

    def _get_dashboard_entry(self) -> Tuple[str, str]:
        """Zwraca (nazwa_pliku, stem) aktywnego kokpitu z konfiguracji ConfigManager."""
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            entry_name = ws_room.entry_file or f"Dashboard {self.config.instance_name}.md"
        except ConfigError:
            entry_name = f"Dashboard {self.config.instance_name}.md"
        return entry_name, Path(entry_name).stem

    def build_dashboard_content(self) -> str:
        """Kompiluje pełny kod Markdown Dashboardu."""
        _, entry_stem = self._get_dashboard_entry()
        now = datetime.datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M")
        today_date = now.strftime("%Y-%m-%d")
        user_name = getattr(getattr(self.config, "user", None), "name", None) or "Użytkownik"
        try:
            coord_name = self.config.get_agent_name("coordinator")
        except Exception:
            coord_name = "Koordynator"

        # Dynamiczne wykrywanie aktualnej Agendy Dnia
        agenda_link = "[[02 Warsztat|Stół Warsztatowy]]"
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            ws_dir = self.vault_root / ws_room.path
        except Exception:
            ws_dir = self.vault_root / "02 Warsztat"
        if ws_dir.is_dir():
            agendas = [f for f in ws_dir.iterdir() if f.is_file() and "Agenda Robocza" in f.name]
            if agendas:
                latest_agenda = max(agendas, key=lambda f: f.name)
                agenda_link = f"[[{latest_agenda.stem}|Agenda Robocza Dnia]]"

        # 1. Pobierz dane ze skanerów
        inbox_items = self.scan_inbox()
        pr_active, pr_pending, pr_paused = self.scan_projects()
        active_ops = self.scan_operations()
        sprint_items, td_tasks = self.scan_workshop()
        total_notes, unconsumed_notes, recent_notes = self.scan_notes()

        td_done_count = sum(1 for t in td_tasks if "zrobione" in t.status or "✅" in t.status)
        inbox_badge = "🟢 0 (czysta)" if not inbox_items else f"🔴 {len(inbox_items)} do drenażu"

        # 2. Formatowanie tabeli KPI (używamy \\| aby nie psuć kolumn tabeli Markdown)
        kpi_table = f"""| 🚀 Aktywne Projekty | 🔄 Operacje | ⚡ Zadania Doraźne | 📥 Skrzynka | 📚 Notatki Łącznie | 🔴 Do Przeczytania |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **[[_Spis Projektów\\|{len(pr_active)}]]** | **[[_Spis Operacji\\|{len(active_ops)}]]** | **{td_done_count}/{len(td_tasks)}** | **{inbox_badge}** | **{total_notes}** | **[[{entry_stem}#🔴 Kolejka Lektur Człowieka\\|{len(unconsumed_notes)}]]** |"""

        # 3. Formatowanie Skrzynki
        if not inbox_items:
            inbox_sec = """> [!tip] Skrzynka jest czysta (`drain-to-zero`)
> W pokoju `01 Skrzynka` nie zalegają żadne nieprzetworzone pliki. Wszystkie wsady zostały przedrenowane do właściwych pokoi, a dokumenty otrzymały notatki ze stemplem `consumed: false`."""
        else:
            inbox_list = "\n".join(f"- 📄 `{it.name}` ({it.stat().st_size // 1024} KB)" for it in inbox_items)
            inbox_sec = f"""> [!warning] Elementy oczekujące na drenaż ({len(inbox_items)})
{inbox_list}
> 
> *Uruchom drenaż z poziomu agenta lub poleceniem:* `python3 "08 Zespół AI/Skrypty/drain_inbox.py" --archive-all`"""

        # 4. Formatowanie Sprintu
        sprint_lines = []
        for it in sprint_items:
            sprint_lines.append(it)
        sprint_sec = "\n".join(sprint_lines) if sprint_lines else "*(Brak zadań w bieżącym sprincie)*"

        # 5. Formatowanie Kolejki Lektur (consumed: false)
        if not unconsumed_notes:
            unconsumed_sec = "> 🟢 **Kolejka lektur jest pusta!** Wszystkie materiały w Zasobach zostały przeczytane."
        else:
            rows = []
            for n in unconsumed_notes:
                rows.append(f"> | [[{n.title}]] | `{n.created}` | 🔴 Do przeczytania |")
            unconsumed_sec = f"""> | Materiał / Notatka Referencyjna | Data Dodania | Status Lektury |
> | :--- | :---: | :---: |
""" + "\n".join(rows)

        # 6. Formatowanie Ostatnio Dodanych Notatek
        recent_rows = []
        for n in recent_notes:
            status_txt = "🔴 do przeczytania" if not n.consumed else "✅ przeczytana"
            recent_rows.append(f"> | [[{n.title}]] | `{n.created}` | {status_txt} |")
        recent_sec = f"""> | Tytuł Notatki | Data Utworzenia | Status |
> | :--- | :---: | :---: |
""" + "\n".join(recent_rows)

        # 7. Formatowanie Aktywnych Projektów (tabela rozwijana)
        if pr_active:
            pr_rows = [f"> | `{p.pr_id}` | **{p.name}** | {p.status} | {p.phase} | `{p.updated}` | {p.card_link} |" for p in pr_active]
            pr_active_table = """> | ID | Nazwa Projektu | Status | Faza / Kontekst | Data | Karta |
> | :--- | :--- | :---: | :--- | :---: | :--- |
""" + "\n".join(pr_rows)
        else:
            pr_active_table = "> *(Brak aktywnych projektów w toku)*"

        # Projekty oczekujące i wstrzymane
        pr_other = pr_pending + pr_paused
        if pr_other:
            pr_other_rows = [f"> | `{p.pr_id}` | **{p.name}** | {p.status} | {p.phase} | `{p.updated}` | {p.card_link} |" for p in pr_other]
            pr_other_table = """> | ID | Nazwa Projektu | Status | Faza / Kontekst | Data | Karta |
> | :--- | :--- | :---: | :--- | :---: | :--- |
""" + "\n".join(pr_other_rows)
        else:
            pr_other_table = "> *(Brak projektów oczekujących lub wstrzymanych)*"

        # 8. Formatowanie Operacji
        if active_ops:
            op_rows = [f"> | `{o.op_id}` | **{o.name}** | {o.frequency} | {o.status} | {o.link} |" for o in active_ops]
            op_table = """> | ID | Operacja / Rutyna | Częstotliwość | Status | Karta Operacji |
> | :--- | :--- | :---: | :--- | :---: | :--- |
""" + "\n".join(op_rows)
        else:
            op_table = "> *(Brak aktywnych procedur lokalnych – bieżące rutyny obsługi nieruchomości zostały przeniesione do DEVONthink.)*"

        # 9. Formatowanie Zespołu AI (dynamicznie przez ConfigManager)
        ai_lines = []
        try:
            for rk in self.config.get_all_roles():
                agent_cfg = self.config.get_agent_config(rk)
                ai_lines.append(f"> - **{agent_cfg.role}:** {agent_cfg.assigned_name}")
        except Exception:
            ai_lines.append(f"> - **Koordynator:** {coord_name}")
        ai_lines.append("> - **Bramka Bezpieczeństwa:** `subagent_gate.py` (destylaty SAT 300–600 tokenów, ochrona okna kontekstowego)")
        ai_team_block = "\n".join(ai_lines)

        # Złożenie całości
                # Dynamiczne linki nawigacyjne dla Topografii 8 Pokoi LAB (Aksjomat Nienaruszalności Rdzenia)
        # Zakaz hardkodowania konkretnych projektów PR, badań RS czy baz dziedzinowych w kodzie silnika!
        def _get_room_shortcuts(code: str, room_name: str, room_path_str: str) -> str:
            r_dir = self.vault_root / room_path_str
            if code == "01":
                return f"[[{room_path_str}|Otwórz Skrzynkę]]"
            elif code == "02":
                tool_f = ws_dir / "Narzędziownia.md"
                if tool_f.is_file():
                    return f"{agenda_link} • [[Narzędziownia|Narzędziownia (Aktywa)]]"
                return agenda_link
            elif code == "03":
                entry = getattr(self.config.rooms.get("03"), "entry_file", "_Spis Projektów.md")
                return f"[[{entry}|Portfel Projektów (PR)]]"
            elif code == "04":
                entry = getattr(self.config.rooms.get("04"), "entry_file", "_Spis Operacji.md")
                return f"[[{entry}|Rutyny i Procedury (OP)]]"
            elif code == "05":
                shortcuts = []
                if r_dir.is_dir():
                    for sub in sorted(r_dir.iterdir()):
                        if sub.is_dir() and not sub.name.startswith(".") and not sub.name.startswith("_"):
                            base_f = sub / f"_{sub.name}.base"
                            if base_f.is_file():
                                shortcuts.append(f"[[{base_f.name}|{sub.name}]]")
                            else:
                                shortcuts.append(f"[[{sub.relative_to(self.vault_root)}|{sub.name}]]")
                return " • ".join(shortcuts) if shortcuts else f"[[{room_path_str}|{room_name}]]"
            elif code == "06":
                shortcuts = []
                if r_dir.is_dir():
                    for sub in sorted(r_dir.iterdir()):
                        if sub.is_dir() and not sub.name.startswith(".") and not sub.name.startswith("_"):
                            shortcuts.append(f"[[{sub.relative_to(self.vault_root)}|{sub.name}]]")
                return " • ".join(shortcuts) if shortcuts else f"[[{room_path_str}|{room_name}]]"
            elif code == "07":
                shortcuts = []
                if r_dir.is_dir():
                    for f in sorted(r_dir.iterdir()):
                        if f.is_file() and f.suffix == ".db":
                            shortcuts.append(f"`{f.name}`")
                return " • ".join(shortcuts) if shortcuts else f"[[{room_path_str}|{room_name}]]"
            elif code == "08":
                return f"[[AGENTS.md|Kontrakt Zespołu AI]] • [[_Księga Governance {self.config.instance_name}|Konstytucja {self.config.instance_name}]] • [[{room_path_str}/Dzienniki Sesji|Dzienniki Sesji]]"
            return f"[[{room_path_str}|{room_name}]]"

        rooms_data = [
            ("01", "Skrzynka", "ingestion", "drain-to-zero"),
            ("02", "Warsztat", "daily-workbench", "daily-journal-wip"),
            ("03", "Projekty", "active-projects", "bounded-initiatives"),
            ("04", "Operacje", "lifecycle-routines", "continuous-responsibilities"),
            ("05", "Baza Wiedzy", "atomic-synthesis-and-research", "evergreen-and-research"),
            ("06", "Zasoby", "reference-and-assets", "reference-archive"),
            ("07", "Bazy Danych", "sqlite-relational", "read-heavy-wal"),
            ("08", "Zespół AI", "governance-and-engine", "ai-orchestration"),
        ]
        room_table_rows = []
        for r_code, def_name, r_role, r_policy in rooms_data:
            r_obj = self.config.rooms.get(r_code)
            r_name = getattr(r_obj, "name", def_name) if r_obj else def_name
            r_path = getattr(r_obj, "path", f"{r_code} {def_name}") if r_obj else f"{r_code} {def_name}"
            sc = _get_room_shortcuts(r_code, r_name, r_path)
            room_table_rows.append(f"| `{r_code}` | **{r_name}** | `{r_role}` | `{r_policy}` | {sc} |")
        dynamic_rooms_table = "\n".join(room_table_rows)

        content = f"""---
type: cockpit-dashboard
title: "{entry_stem}"
instance: "{self.config.instance_name}"
generated: "{now_str}"
tags:
  - dashboard
  - kokpit
  - sat
  - pzw
  - warsztat
---

# 🎛️ {entry_stem} (Kokpit Systemu)

> **Zintegrowany panel operacyjny i centrum dowodzenia Właściciela Systemu ({user_name} ↔ {coord_name})**  
> *„Środowisko ma pracować dla człowieka, a nie człowiek dla środowiska.”*  
> Ostatnia synchronizacja: **`{now_str}`** • Instancja: **{self.config.instance_name}** • Koordynator: **{coord_name}**

---

## 📊 Wskaźniki Ilościowe Systemu (KISS Metrics)

{kpi_table}

---

## 📥 Stan Skrzynki i Ingestii (`01 Skrzynka`)
{inbox_sec}

---

## 🎯 Na Tapecie (Bieżący Sprint)
{sprint_sec}

---

> [!todo]+ 🔴 Kolejka Lektur Człowieka (`consumed: false`) ({len(unconsumed_notes)})
> *Etyka konsumpcji wiedzy: materiały oczekujące na Twoją osobistą lekturę.*
>
{unconsumed_sec}

---

> [!abstract]- 🆕 Ostatnio Dodane Materiały Referencyjne w Zasobach
{recent_sec}

---

> [!example]+ 🚀 Projekty w Toku ({len(pr_active)}) – [[_Spis Projektów\\|Otwórz pełny Spis Portfela ↗]]
{pr_active_table}

---

> [!note]- 🟡 Projekty Oczekujące i Wstrzymane ({len(pr_other)})
{pr_other_table}

---

> [!quote]- 🔄 Rejestr Operacji Cyklicznych (OP) ({len(active_ops)}) – [[_Spis Operacji\\|Otwórz pełny Spis Operacji ↗]]
{op_table}

---

## 🏛️ Topografia i Pokoje Rdzenia (Przykrycie Silnika)

> Poniższe karty abstrahują techniczną maszynerię silnika. Jako człowiek nie musisz zagłębiać się w strukturę podfolderów – wystarczą szybkie skróty nawigacyjne:

| Kod | Pokój | Rola PZW | Polityka | Szybki Skrót / Punkt Wejścia |
| :---: | :--- | :--- | :--- | :--- |
{dynamic_rooms_table}


---

### 🧠 Architektura Zespołu AI & Silnik Maszynowy (Pokój 08)
{ai_team_block}
"""
        return content

    def write_dashboard(self, dry_run: bool = False) -> Path:
        """Zapisuje wygenerowany dashboard do '02 Warsztat/Dashboard LAB.md'."""
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            ws_dir = self.vault_root / ws_room.path
        except ConfigError:
            ws_dir = self.vault_root / "02 Warsztat"

        entry_name, _ = self._get_dashboard_entry()
        target_file = ws_dir / entry_name
        content = self.build_dashboard_content()

        if not dry_run:
            target_file.write_text(content, encoding="utf-8")

        return target_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Generator Kokpitu Dashboard LAB")
    parser.add_argument("--vault-root", help="Katalog główny skarbca")
    parser.add_argument("--config", help="Ścieżka do lab.config.toml")
    parser.add_argument("--dry-run", action="store_true", help="Nie zapisuj pliku na dysku")
    args = parser.parse_args()

    builder = DashboardBuilder(vault_root=args.vault_root, config_path=args.config)
    target = builder.write_dashboard(dry_run=args.dry_run)
    print(f"✅ Wygenerowano z sukcesem: '{target}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
