#!/usr/bin/env python3
"""projects.py - Deterministyczny zarządca portfela projektów dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (argparse, dataclasses, datetime, json, os, pathlib, re, shutil, sys).
- Oparte o core_config.ConfigManager (brak hardkodowanych ścieżek).
- Zero zewnętrznych zależności pip.

Zgodnie z "The One Law":
"Kod dla wszystkiego, w czym maszyna mogłaby wskazać błąd. Instrukcje tekstowe tylko dla tego,
w czym maszyna błędu wskazać nie potrafi."

Odpowiada za:
1. Skanowanie pokoju '03 Projekty/' oraz '03 Projekty/_archiwum/'.
2. Wykrywanie statusów projektów (z frontmattera YAML karty projektu oraz _Spis Projektów.md).
3. Segregację projektów na 4 kategorie:
   - 🟢 W toku (active)
   - 🟡 Oczekujące (queued)
   - ⏸️ Wstrzymane (paused)
   - 🏁 Zakończone w archiwum (completed)
4. Automatyczną i bezpieczną archiwizację zakończonych projektów z '03 Projekty/' do '_archiwum/'.
5. Deterministyczne sortowanie i regenerację '_Spis Projektów.md' (zgodnie z transkluzją w Warsztacie).
6. Tryb walidacji (--check) do procedury otwarcia sesji (session_bootstrap).
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca LAB
sys.dont_write_bytecode = True

# Zaimportuj core_config z katalogu bieżącego lub korzenia skarbca
_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigManager, ConfigError, find_vault_root

# Dozwolone statusy i ich mapowanie kanoniczne
STATUS_MAP = {
    # W toku
    "active": "active",
    "w toku": "active",
    "w_toku": "active",
    "in_progress": "active",
    "in-progress": "active",
    "started": "active",
    # Oczekujący
    "queued": "queued",
    "oczekujący": "queued",
    "oczekujacy": "queued",
    "pending": "queued",
    "backlog": "queued",
    # Wstrzymany
    "paused": "paused",
    "wstrzymany": "paused",
    "on_hold": "paused",
    "on-hold": "paused",
    "blocked": "paused",
    "parked": "paused",
    "zaparkowany": "paused",
    # Zakończony
    "completed": "completed",
    "zakończony": "completed",
    "zakonczony": "completed",
    "done": "completed",
    "closed": "completed",
    # Anulowany
    "cancelled": "cancelled",
    "anulowany": "cancelled",
    "canceled": "cancelled",
}

STATUS_DISPLAY = {
    "active": "🟢 `w toku`",
    "queued": "🟡 `oczekujący`",
    "paused": "⏸️ `wstrzymany`",
    "completed": "🏁 `zakończony`",
    "cancelled": "❌ `anulowany`",
}


@dataclass
class ProjectInfo:
    id: str
    name: str
    folder_name: str
    is_archived: bool
    folder_path: Path
    card_path: Optional[Path] = None
    status: str = "active"  # canonical status: active, queued, paused, completed, cancelled
    phase_context: str = ""
    updated: str = ""
    completed_date: Optional[str] = None
    tags: List[str] = field(default_factory=list)


def parse_frontmatter(file_path: Path) -> Tuple[Dict[str, str], str]:
    """Wyodrębnia frontmatter YAML i treść notatki bez bibliotek zewnętrznych."""
    if not file_path.is_file():
        return {}, ""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return {}, ""

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

    fm_dict: Dict[str, str] = {}
    for line in lines[1:closing_idx]:
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            fm_dict[key] = val

    content = "".join(lines[closing_idx + 1:])
    return fm_dict, content


def update_frontmatter_fields(file_path: Path, updates: Dict[str, str]) -> None:
    """Aktualizuje lub dodaje pola w YAML frontmatter pliku Markdown bez naruszania treści."""
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    if lines and lines[0].strip() == "---":
        closing_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break
        if closing_idx != -1:
            fm_lines = lines[1:closing_idx]
            remaining = dict(updates)
            new_fm_lines: List[str] = []
            for line in fm_lines:
                if ":" in line and not line.lstrip().startswith("-"):
                    key = line.split(":", 1)[0].strip()
                    if key in remaining:
                        val = remaining.pop(key)
                        new_fm_lines.append(f"{key}: {val}\n")
                        continue
                new_fm_lines.append(line)
            for key, val in remaining.items():
                new_fm_lines.append(f"{key}: {val}\n")
            updated_text = "".join([lines[0], *new_fm_lines, *lines[closing_idx:]])
            file_path.write_text(updated_text, encoding="utf-8")
            return

    fm_block = ["---\n", *[f"{k}: {v}\n" for k, v in updates.items()], "---\n\n"]
    file_path.write_text("".join(fm_block) + text, encoding="utf-8")


def extract_existing_index_metadata(index_path: Path) -> Dict[str, Dict[str, str]]:
    """Pobiera kontekst (Faza / Kontekst, Data, Status) z istniejącego _Spis Projektów.md."""
    meta: Dict[str, Dict[str, str]] = {}
    if not index_path.is_file():
        return meta

    try:
        content = index_path.read_text(encoding="utf-8")
    except Exception:
        return meta

    for line in content.splitlines():
        line_clean = line.strip()
        if not line_clean.startswith("|") or line_clean.startswith("| :") or line_clean.startswith("| ID"):
            continue
        # Zastąp ucieczkę \| tymczasowym separatorem
        line_unescaped = line_clean.replace(r"\|", "§PIPE§")
        cols = [c.replace("§PIPE§", "|").strip() for c in line_unescaped.split("|")[1:-1]]
        if not cols:
            continue

        proj_id_match = re.search(r"PR-\d+", cols[0])
        pid = proj_id_match.group(0) if proj_id_match else None

        if len(cols) == 5:
            # Tabela archiwum: ID | Nazwa | Status | Data | Link
            name = cols[1].strip()
            status_raw = cols[2].strip()
            context_raw = ""
            updated_raw = cols[3].strip()
        elif len(cols) >= 6:
            # Tabela aktywna: ID | Nazwa | Status | Faza | Data | Link
            name = cols[1].strip()
            status_raw = cols[2].strip()
            context_raw = cols[3].strip()
            updated_raw = cols[4].strip()
        else:
            continue

        item_data = {
            "name": name,
            "status_raw": status_raw,
            "context": context_raw,
            "updated": updated_raw,
        }
        if pid:
            meta[pid] = item_data
        if name:
            meta[name] = item_data

    return meta


class ProjectManager:
    """Zarządca projektów LAB 3.0 oparty na 100% Python Standard Library."""

    def __init__(self, vault_root: Optional[Union[str, Path]] = None) -> None:
        if vault_root:
            v_root = Path(vault_root).resolve()
            cfg_file = v_root / "lab.config.toml"
            if cfg_file.is_file():
                self.config_mgr = ConfigManager(config_path=cfg_file, vault_root=v_root)
            else:
                self.config_mgr = ConfigManager(vault_root=v_root)
        else:
            self.config_mgr = ConfigManager()
        self.vault_root = self.config_mgr.vault_root.resolve()
        # Pobierz ścieżkę do pokoju 03 Projekty z konfiguracji
        try:
            self.projects_dir = (self.vault_root / self.config_mgr.get_room_path("03")).resolve()
        except Exception:
            self.projects_dir = (self.vault_root / "03 Projekty").resolve()

        self.archive_dir = (self.projects_dir / "_archiwum").resolve()
        self.index_path = (self.projects_dir / "_Spis Projektów.md").resolve()

        # Ścieżka do Dzienników Sesji Zespołu AI dla agregacji certyfikatów i meldunków
        try:
            ai_team_room = self.config_mgr.get_room_by_role("governance-and-engine")
            self.ai_team_dir = (self.vault_root / ai_team_room.path).resolve()
        except Exception:
            self.ai_team_dir = (self.vault_root / "08 Zespół AI").resolve()
        self.session_logs_dir = (self.ai_team_dir / "Dzienniki Sesji").resolve()

    def scan_all_projects(self) -> List[ProjectInfo]:
        """Skanuje aktywne projekty oraz archiwum."""
        projects: List[ProjectInfo] = []
        existing_meta = extract_existing_index_metadata(self.index_path)

        # 1. Projekty w głównym folderze 03 Projekty/
        if self.projects_dir.is_dir():
            for item in sorted(self.projects_dir.iterdir()):
                if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
                    p_info = self._parse_project_dir(item.resolve(), is_archived=False, meta_lookup=existing_meta)
                    if p_info:
                        projects.append(p_info)

        # 2. Projekty w _archiwum/
        if self.archive_dir.is_dir():
            for item in sorted(self.archive_dir.iterdir()):
                if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
                    p_info = self._parse_project_dir(item.resolve(), is_archived=True, meta_lookup=existing_meta)
                    if p_info:
                        projects.append(p_info)

        return projects

    def _parse_project_dir(
        self, folder_path: Path, is_archived: bool, meta_lookup: Dict[str, Dict[str, str]]
    ) -> Optional[ProjectInfo]:
        folder_name = folder_path.name
        # Wyciągnij ID np. PR-001
        id_match = re.search(r"PR-(\d+)", folder_name)
        proj_id = f"PR-{id_match.group(1)}" if id_match else "—"

        # Nazwa projektu
        if proj_id != "—":
            clean_name = re.sub(r"^\d{4}-\d{2}-\d{2}_|^\d{4}-\d{2}_", "", folder_name)
            clean_name = re.sub(r"^PR-\d+\s*", "", clean_name).strip()
            if not clean_name:
                clean_name = folder_name
        else:
            clean_name = re.sub(r"^\d{4}-\d{2}-\d{2}_|^\d{4}-\d{2}_", "", folder_name).strip()

        # Znajdź plik karty projektu
        card_path = None
        # Najpierw spróbuj PR-xxx*.md
        for f in folder_path.glob("PR-*.md"):
            card_path = f.resolve()
            break
        # Jeśli brak, szukaj jakiegokolwiek md o nazwie folderu lub pierwszej notatki
        if not card_path:
            for f in folder_path.glob("*.md"):
                if f.name != "progress-report.md" and not f.name.startswith("2026-"):
                    card_path = f.resolve()
                    break

        fm: Dict[str, str] = {}
        if card_path and card_path.is_file():
            fm, _ = parse_frontmatter(card_path)

        # Wyznacz status (frontmatter karty projektu jest źródłem prawdy nadrzędnym wobec _Spis Projektów.md)
        fm_status = fm.get("status", "").strip().lower()
        raw_status = fm_status

        # Fallback do _Spis Projektów.md wyłącznie gdy karta nie definiuje statusu
        if not raw_status:
            if proj_id in meta_lookup and meta_lookup[proj_id].get("status_raw"):
                raw_status = meta_lookup[proj_id]["status_raw"].lower()
            elif clean_name in meta_lookup and meta_lookup[clean_name].get("status_raw"):
                raw_status = meta_lookup[clean_name]["status_raw"].lower()

        canonical_status = "active"
        if is_archived:
            if "paused" in raw_status or "wstrzyman" in raw_status or "paused" in fm_status or "wstrzyman" in fm_status:
                canonical_status = "paused"
            else:
                canonical_status = "completed"
        else:
            for k, mapped in STATUS_MAP.items():
                if k in raw_status:
                    canonical_status = mapped
                    break

        # Wyznacz kontekst / fazę
        context = ""
        # 1. Z progress-report.md jeśli istnieje
        prog_file = folder_path / "progress-report.md"
        if prog_file.is_file():
            try:
                prog_text = prog_file.read_text(encoding="utf-8")
                ph_match = re.search(r"(?:Faza|Etap|Status):\s*([^\n]+)", prog_text, re.IGNORECASE)
                if ph_match:
                    context = ph_match.group(1).strip()
            except Exception:
                pass

        # 2. Fallback do metadanych ze spisu
        if not context and proj_id in meta_lookup:
            context = meta_lookup[proj_id].get("context", "")
        if not context and clean_name in meta_lookup:
            context = meta_lookup[clean_name].get("context", "")

        # Wyznacz datę aktualizacji
        updated_date = fm.get("updated") or fm.get("created")
        if not updated_date and proj_id in meta_lookup:
            updated_date = meta_lookup[proj_id].get("updated", "")
        if not updated_date:
            target_f = card_path if card_path else folder_path
            mtime = target_f.stat().st_mtime
            updated_date = datetime.date.fromtimestamp(mtime).isoformat()

        completed_date = None
        if canonical_status == "completed":
            completed_date = (
                fm.get("completed_date")
                or fm.get("completed")
                or fm.get("updated")
                or updated_date
            )
            date_prefix = re.match(r"^(\d{4}-\d{2}-\d{2}|\d{4}-\d{2})_", folder_name)
            if date_prefix and not fm.get("completed_date") and not fm.get("completed"):
                completed_date = date_prefix.group(1)

        return ProjectInfo(
            id=proj_id,
            name=clean_name,
            folder_name=folder_name,
            is_archived=is_archived,
            folder_path=folder_path,
            card_path=card_path,
            status=canonical_status,
            phase_context=context,
            updated=updated_date,
            completed_date=completed_date,
        )

    def archive_completed_projects(self, dry_run: bool = False) -> List[Tuple[Path, Path]]:
        """Przenosi zakończone projekty z '03 Projekty/' do '_archiwum/PR-xxx ...' bez prefiksu daty."""
        moved: List[Tuple[Path, Path]] = []
        projects = self.scan_all_projects()

        self.archive_dir.mkdir(parents=True, exist_ok=True)

        for p in projects:
            if not p.is_archived and p.status == "completed":
                # Nazwa folderu w archiwum zachowuje czystą nazwę projektu (PR-xxx ...) bez daty
                new_folder_name = p.folder_name
                dest_path = (self.archive_dir / new_folder_name).resolve()

                moved.append((p.folder_path, dest_path))
                if not dry_run:
                    if dest_path.exists():
                        raise FileExistsError(f"Folder docelowy w archiwum już istnieje: {dest_path}")
                    shutil.move(str(p.folder_path), str(dest_path))
                    p.is_archived = True
                    p.folder_path = dest_path
                    if p.card_path:
                        p.card_path = dest_path / p.card_path.name
                    # Automatycznie skompletuj dokumentację i certyfikaty w Notatki/
                    self.bundle_project_documentation(p, dest_path)

        if moved and not dry_run:
            self.update_index_file()

        return moved

    def bundle_project_documentation(self, project: ProjectInfo, dest_dir: Optional[Path] = None) -> List[Path]:
        """Kompletuje raporty techniczne, meldunki inżynierskie i certyfikaty audytorskie w Notatki/ projektu."""
        target_folder = dest_dir or project.folder_path
        notes_dir = target_folder / "Notatki"

        copied: List[Path] = []
        if not project.id or project.id == "—":
            return copied

        num_match = re.search(r"PR-(\d+)", project.id, re.IGNORECASE)
        if not num_match:
            return copied
        num_str = num_match.group(1)
        int_num = int(num_str)

        patterns = [
            re.compile(rf"\bPR-?0*{int_num}\b", re.IGNORECASE),
            re.compile(rf"pr0*{int_num}", re.IGNORECASE),
        ]

        if self.session_logs_dir.is_dir():
            for root, _, files in os.walk(self.session_logs_dir):
                for f in files:
                    if not f.endswith(".md") or f.startswith("."):
                        continue
                    lower_f = f.lower()
                    is_candidate = any(k in lower_f for k in ("certyfikat", "audyt", "meldunek", "raport", "opinia", "decyzja"))
                    src_file = Path(root) / f

                    matches_project = any(p.search(f) for p in patterns)
                    if not matches_project and is_candidate:
                        try:
                            content = src_file.read_text(encoding="utf-8", errors="ignore")
                            if any(p.search(content) for p in patterns):
                                matches_project = True
                        except Exception:
                            pass

                    if matches_project:
                        dst_file = notes_dir / f
                        if not dst_file.exists():
                            notes_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, dst_file)
                            copied.append(dst_file)

        self._ensure_card_documentation_links(project, target_folder, notes_dir)
        return copied

    def _ensure_card_documentation_links(self, project: ProjectInfo, target_folder: Path, notes_dir: Path) -> None:
        """Gwarantuje, że karta projektu linkuje do zebranej dokumentacji w podfolderze Notatki/."""
        card_p = None
        if project.card_path and project.card_path.is_file():
            card_p = project.card_path
        else:
            for cand in target_folder.glob("PR-*.md"):
                card_p = cand
                break

        if not card_p or not card_p.is_file():
            return

        try:
            text = card_p.read_text(encoding="utf-8")
        except Exception:
            return

        if "Dokumentacja Wykonawcza" in text or "Archiwum Raportów Kapsuły" in text:
            return

        if not notes_dir.is_dir():
            return

        note_files = [
            f for f in sorted(notes_dir.iterdir())
            if f.is_file() and f.suffix == ".md" and not f.name.startswith(".")
        ]
        if not note_files:
            return

        links_section = [
            "\n---\n\n",
            "## Dokumentacja Wykonawcza i Archiwum Raportów Kapsuły\n\n",
            "Meldunki inżynierskie, certyfikaty audytorskie i dokumenty źródłowe powiązane z projektem:\n\n"
        ]
        for nf in note_files:
            rel_f = nf.resolve().relative_to(self.vault_root)
            links_section.append(f"- 📄 [[{rel_f}|{nf.stem}]]\n")

        prod_dir = target_folder / "Produkt"
        if prod_dir.is_dir():
            prod_files = [pf for pf in prod_dir.iterdir() if pf.is_file() and not pf.name.startswith(".")]
            if prod_files:
                links_section.append("\n### Produkty i Artefakty Finalne\n\n")
                for pf in prod_files:
                    rel_pf = pf.resolve().relative_to(self.vault_root)
                    links_section.append(f"- 📦 [[{rel_pf}|{pf.name}]]\n")

        updated_text = text.rstrip() + "\n" + "".join(links_section)
        card_p.write_text(updated_text, encoding="utf-8")

    def bundle_all_archived_projects(self) -> Dict[str, List[Path]]:
        """Kompletuje dokumentację dla wszystkich projektów w archiwum."""
        projects = self.scan_all_projects()
        results: Dict[str, List[Path]] = {}
        for p in projects:
            if p.is_archived and p.id != "—":
                added = self.bundle_project_documentation(p)
                if added:
                    results[p.id] = added
        return results

    def generate_index_markdown(self) -> str:
        """Generuje deterministyczną treść _Spis Projektów.md z podziałem na statusy."""
        projects = self.scan_all_projects()

        active_list: List[ProjectInfo] = []
        queued_list: List[ProjectInfo] = []
        paused_list: List[ProjectInfo] = []
        completed_list: List[ProjectInfo] = []

        for p in projects:
            if p.is_archived or p.status == "completed":
                completed_list.append(p)
            elif p.status == "active":
                active_list.append(p)
            elif p.status == "queued":
                queued_list.append(p)
            elif p.status == "paused":
                paused_list.append(p)
            else:
                queued_list.append(p)

        def sort_key(p: ProjectInfo) -> Tuple[int, str]:
            num_match = re.search(r"PR-(\d+)", p.id)
            num = int(num_match.group(1)) if num_match else 9999
            return (num, p.name)

        active_list.sort(key=sort_key)
        queued_list.sort(key=sort_key)
        paused_list.sort(key=sort_key)

        def archive_sort_key(p: ProjectInfo) -> Tuple[int, str]:
            num_match = re.search(r"PR-(\d+)", p.id)
            num = int(num_match.group(1)) if num_match else 0
            return (num, p.name)

        completed_list.sort(key=archive_sort_key, reverse=True)

        today_str = datetime.date.today().isoformat()

        lines: List[str] = [
            "---",
            "type: projects-portfolio-index",
            'title: "Spis Projektów"',
            f'instance: "{self.config_mgr.instance_name}"',
            "created: 2026-11-02",
            f"updated: {today_str}",
            "tags:",
            "  - projekty",
            "  - portfolio",
            "  - index",
            "  - sat",
            "  - pzw",
            "---",
            "",
            f"# 📁 Spis Projektów ({self.config_mgr.instance_name})",
            "",
            "> **Rejestr portfela aktywnych i zakończonych przedsięwzięć (PR-xxx)**  ",
            "> *Każdy projekt mieszka we własnym podfolderze `PR-xxx <Nazwa>` i zawiera raport postępu `progress-report.md` oraz kapsułę `Produkt/`.*",
            "",
            "---",
            "",
            "## 🚀 Projekty w Toku (Aktywne)",
            "",
            "> 💡 **Słownik statusów (dozwolone wartości):**  ",
            "> `🟢 w toku` • `⏸️ wstrzymany` • `🟡 oczekujący` • `🏁 zakończony` • `❌ anulowany`  ",
            "> *(Wskazówka: Możesz swobodnie zmieniać status oraz treść w kolumnie Faza – system automatycznie respektuje Twoje wpisy).* ",
            "",
            "| ID | Nazwa Projektu | Status | Faza / Kontekst | Ostatnia Aktualizacja | Karta Projektu |",
            "| :--- | :--- | :---: | :--- | :---: | :--- |",
        ]

        if active_list:
            for p in active_list:
                lines.append(self._format_table_row(p))
        else:
            lines.append("| — | *(Brak aktywnych projektów w toku)* | — | — | — | — |")

        lines.extend([
            "",
            "---",
            "",
            "## 🟡 Projekty Oczekujące",
            "",
            "| ID | Nazwa Projektu | Status | Faza / Kontekst | Ostatnia Aktualizacja | Karta Projektu |",
            "| :--- | :--- | :---: | :--- | :---: | :--- |",
        ])

        if queued_list:
            for p in queued_list:
                lines.append(self._format_table_row(p))
        else:
            lines.append("| — | *(Brak projektów oczekujących)* | — | — | — | — |")

        lines.extend([
            "",
            "---",
            "",
            "## ⏸️ Projekty Wstrzymane",
            "",
            "| ID | Nazwa Projektu | Status | Faza / Kontekst | Ostatnia Aktualizacja | Karta Projektu |",
            "| :--- | :--- | :---: | :--- | :---: | :--- |",
        ])

        if paused_list:
            for p in paused_list:
                lines.append(self._format_table_row(p))
        else:
            lines.append("| — | *(Brak projektów wstrzymanych)* | — | — | — | — |")

        lines.extend([
            "",
            "---",
            "",
            "## 📦 Archiwum Ukończonych Przedsięwzięć (`_archiwum/`)",
            "",
            "| ID | Nazwa Projektu | Status | Data Ukończenia | Kapsuła Archiwum |",
            "| :--- | :--- | :---: | :---: | :--- |",
        ])

        if completed_list:
            for p in completed_list:
                lines.append(self._format_archive_row(p))
        else:
            lines.append("| — | *(Brak projektów w archiwum)* | — | — | — |")

        lines.append("")
        return "\n".join(lines)

    def _format_table_row(self, p: ProjectInfo) -> str:
        id_str = f"`{p.id}`" if p.id != "—" else "—"
        status_str = STATUS_DISPLAY.get(p.status, p.status)
        context_str = p.phase_context or "W toku"
        updated_str = p.updated or "—"

        if p.card_path:
            rel_card = p.card_path.resolve().relative_to(self.vault_root)
            link_str = f"[[{rel_card}|Karta Projektu]]"
        else:
            rel_folder = p.folder_path.resolve().relative_to(self.vault_root)
            link_str = f"[[{rel_folder}|Folder Projektu]]"

        return f"| {id_str} | {p.name} | {status_str} | {context_str} | {updated_str} | {link_str} |"

    def _format_archive_row(self, p: ProjectInfo) -> str:
        id_str = f"`{p.id}`" if p.id != "—" else "—"
        status_str = STATUS_DISPLAY.get(p.status, STATUS_DISPLAY.get("completed"))
        date_str = p.completed_date or p.updated or "—"

        rel_folder = p.folder_path.resolve().relative_to(self.vault_root)
        produkt_dir = p.folder_path / "Produkt"
        target_link = None
        if produkt_dir.is_dir():
            pdfs = list(produkt_dir.glob("*.pdf"))
            zips = list(produkt_dir.glob("*.zip"))
            if pdfs:
                target_link = f"[[{pdfs[0].resolve().relative_to(self.vault_root)}|Produkt PDF]]"
            elif zips:
                target_link = f"[[{zips[0].resolve().relative_to(self.vault_root)}|Paczka Produktu]]"

        if not target_link:
            if p.card_path:
                target_link = f"[[{p.card_path.resolve().relative_to(self.vault_root)}|Karta Projektu]]"
            else:
                target_link = f"[[{rel_folder}/|Archiwum]]"

        return f"| {id_str} | {p.name} | {status_str} | {date_str} | {target_link} |"

    def update_index_file(self) -> Path:
        """Zapisuje wygenerowany spis projektów do _Spis Projektów.md."""
        md_content = self.generate_index_markdown()
        self.index_path.write_text(md_content, encoding="utf-8")
        return self.index_path

    def _next_project_id(self) -> str:
        """Wyznacza kolejny numer projektu PR-xxx na podstawie aktywnych i zarchiwizowanych projektów."""
        max_num = 0
        for p in self.scan_all_projects():
            m = re.search(r"PR-(\d+)", p.id)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"PR-{max_num + 1:03d}"

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Normalizuje status wejściowy do kanonicznego statusu portfela."""
        key = status.strip().lower()
        if key in STATUS_MAP:
            return STATUS_MAP[key]
        for k, mapped in STATUS_MAP.items():
            if k in key:
                return mapped
        raise ValueError(f"Nieznany status projektu: '{status}'. Dozwolone: active, queued, paused, completed, cancelled.")

    @staticmethod
    def _normalize_project_id(project_id: str) -> str:
        """Normalizuje identyfikator projektu do formatu PR-xxx."""
        raw = project_id.strip().upper()
        if raw.isdigit():
            return f"PR-{int(raw):03d}"
        m = re.fullmatch(r"PR-?(\d+)", raw)
        if m:
            return f"PR-{int(m.group(1)):03d}"
        return raw

    def create_project(
        self,
        title: str,
        status: str = "active",
        phase_context: str = "Etap 1: Inicjalizacja",
        dry_run: bool = False,
    ) -> ProjectInfo:
        """Zakłada kapsułę kolejnego projektu PR-xxx (karta, progress-report.md, Produkt/) i aktualizuje spis."""
        clean_title = re.sub(r'[\\/:*?"<>|]+', "-", title.strip()).strip()
        if not clean_title:
            raise ValueError("Tytuł projektu nie może być pusty.")

        canonical_status = self._normalize_status(status)
        proj_id = self._next_project_id()
        folder_name = f"{proj_id} {clean_title}"
        folder_path = (self.projects_dir / folder_name).resolve()
        if folder_path.exists():
            raise FileExistsError(f"Folder projektu już istnieje: {folder_path}")

        today_str = datetime.date.today().isoformat()
        card_path = folder_path / f"{folder_name}.md"
        progress_path = folder_path / "progress-report.md"
        produkt_dir = folder_path / "Produkt"

        if not dry_run:
            produkt_dir.mkdir(parents=True, exist_ok=True)
            rel_folder = folder_path.relative_to(self.vault_root)
            card_content = (
                "---\n"
                "type: project\n"
                f"id: {proj_id}\n"
                f"status: {canonical_status}\n"
                f"created: {today_str}\n"
                f"updated: {today_str}\n"
                "tags:\n"
                "  - projekt\n"
                "---\n\n"
                f"# Projekt: {proj_id} {clean_title}\n\n"
                "> [!info] Metadane Projektu\n"
                f"> - **Identyfikator:** `{proj_id}`\n"
                f"> - **Nazwa:** {clean_title}\n"
                f"> - **Status:** {canonical_status}\n"
                f"> - **Folder roboczy:** `{rel_folder}/`\n"
                f"> - **Kapsuła produktu:** `{rel_folder}/Produkt/`\n"
                "> - **Raport postępu:** [[progress-report.md]]\n"
            )
            card_path.write_text(card_content, encoding="utf-8")

            progress_content = (
                "---\n"
                "type: progress-report\n"
                f"id: {proj_id}\n"
                f"faza: {phase_context}\n"
                f"status: {canonical_status}\n"
                f"created: {today_str}\n"
                f"updated: {today_str}\n"
                "---\n\n"
                f"# {clean_title}: progress\n\n"
                "## Scoreboard\n\n"
                "| Etap | Co dostarcza | Status | Odpowiedzialny |\n"
                "| :--- | :--- | :---: | :--- |\n"
                f"| **Etap 1: Inicjalizacja** | Kapsuła `{proj_id}` i karta projektu | **RUNNING** | Zespół |\n"
            )
            progress_path.write_text(progress_content, encoding="utf-8")
            self.update_index_file()

        return ProjectInfo(
            id=proj_id,
            name=clean_title,
            folder_name=folder_name,
            is_archived=False,
            folder_path=folder_path,
            card_path=card_path,
            status=canonical_status,
            phase_context=phase_context,
            updated=today_str,
        )

    def set_project_status(
        self,
        project_id: str,
        status: str,
        dry_run: bool = False,
    ) -> ProjectInfo:
        """Aktualizuje status w YAML frontmatter karty projektu i automatycznie synchronizuje _Spis Projektów.md."""
        norm_id = self._normalize_project_id(project_id)
        canonical_status = self._normalize_status(status)
        projects = self.scan_all_projects()
        target = next((p for p in projects if p.id.upper() == norm_id), None)
        if not target:
            raise FileNotFoundError(f"Nie znaleziono projektu o ID: {norm_id}")
        if not target.card_path or not target.card_path.is_file():
            raise FileNotFoundError(f"Projekt {norm_id} nie posiada pliku karty projektu.")

        today_str = datetime.date.today().isoformat()
        if not dry_run:
            updates: Dict[str, str] = {
                "status": canonical_status,
                "updated": today_str,
            }
            if canonical_status == "completed":
                updates["completed_date"] = today_str
            update_frontmatter_fields(target.card_path, updates)
            self.update_index_file()

        target.status = canonical_status
        target.updated = today_str
        return target

    @staticmethod
    def _strip_index_updated_date(text: str) -> str:
        """Normalizuje datę 'updated:' w nagłówku YAML, aby porównywać wyłącznie zawartość portfela."""
        return re.sub(r"(?m)^updated:\s*\d{4}-\d{2}-\d{2}\s*$", "updated: <DATE>", text.strip())

    def check_integrity(self) -> Tuple[bool, List[str]]:
        """Sprawdza reguły jakościowe portfela projektów:
        1. Brak zakończonych projektów (completed) w aktywnym folderze '03 Projekty/'.
        2. Każdy projekt posiada istniejącą kartę projektu.
        3. Sprawdza czy _Spis Projektów.md istnieje oraz czy jest spójny z generate_index_markdown().
        """
        issues: List[str] = []
        projects = self.scan_all_projects()

        for p in projects:
            if not p.is_archived and p.status == "completed":
                issues.append(
                    f"[BŁĄD ARCHIWIZACJI] Projekt '{p.id} {p.name}' ma status 'completed', "
                    f"ale znajduje się w aktywnym pokoju '{p.folder_path.name}'. Wymagana archiwizacja do _archiwum/."
                )
            if not p.card_path and not p.is_archived:
                issues.append(
                    f"[BRAK KARTY] Projekt '{p.id} {p.name}' w '{p.folder_path.name}' "
                    f"nie posiada pliku karty projektu PR-xxx*.md."
                )

        if not self.index_path.is_file():
            issues.append(f"[BRAK SPISU] Plik spisu '{self.index_path.name}' nie istnieje.")
        else:
            current_text = self.index_path.read_text(encoding="utf-8")
            expected_text = self.generate_index_markdown()
            if self._strip_index_updated_date(current_text) != self._strip_index_updated_date(expected_text):
                issues.append(
                    f"[ROZJAZD SPISU] Plik '{self.index_path.name}' jest nieaktualny względem stanu projektów. "
                    "Uruchom `projects.py --sort-index`, aby zsynchronizować spis."
                )

        return len(issues) == 0, issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zarządca portfela projektów LAB 3.0 (The One Law: projekty, statusy, archiwum)."
    )
    parser.add_argument("--scan", action="store_true", help="Skanuj i wyświetl stan projektów.")
    parser.add_argument("--create", type=str, metavar="TITLE", help="Utwórz nową kapsułę projektu PR-xxx i zaktualizuj spis.")
    parser.add_argument(
        "--set-status",
        nargs=2,
        metavar=("PR_ID", "STATUS"),
        help="Ustaw status projektu (active|queued|paused|completed|cancelled) i zaktualizuj spis.",
    )
    parser.add_argument("--pause", type=str, metavar="PR_ID", help="Wstrzymaj projekt (skrót dla --set-status PR_ID paused).")
    parser.add_argument("--sort-index", action="store_true", help="Posortuj i zaktualizuj _Spis Projektów.md.")
    parser.add_argument("--archive", action="store_true", help="Archiwizuj zakończone projekty (move do _archiwum/).")
    parser.add_argument("--bundle-docs", action="store_true", help="Skompletuj dokumentację i certyfikaty audytorskie w Notatki/ projektów.")
    parser.add_argument("--check", action="store_true", help="Zweryfikuj poprawność projektów (exit code 0/1).")
    parser.add_argument("--all", action="store_true", help="Wykonaj pełną procedurę: archiwizacja + kompletowanie dokumentacji + posortowanie spisu.")
    parser.add_argument("--dry-run", action="store_true", help="Tryb symulacji (bez fizycznych zmian).")
    parser.add_argument("--vault-root", type=str, default=None, help="Ścieżka do korzenia skarbca LAB.")

    args = parser.parse_args()
    mgr = ProjectManager(args.vault_root)

    run_all = args.all
    run_create = bool(args.create)
    run_status = bool(args.set_status or args.pause)
    run_archive = args.archive or run_all
    run_bundle = args.bundle_docs or run_all
    run_sort = args.sort_index or run_all
    run_check = args.check
    run_scan = args.scan or (
        not run_create
        and not run_status
        and not run_archive
        and not run_sort
        and not run_check
        and not run_bundle
    )

    print("================================================================")
    print("PROJECT MANAGER - LAB (THE ONE LAW & CLEAN WORKBENCH)")
    print("================================================================")
    print(f"Skarbiec:   {mgr.vault_root}")
    print(f"Projekty:   {mgr.projects_dir.name}")
    print("----------------------------------------------------------------")

    if run_create:
        try:
            proj = mgr.create_project(args.create, dry_run=args.dry_run)
            prefix = "[DRY-RUN] " if args.dry_run else ""
            print(f"🆕 {prefix}UTWORZONO KAPSUŁĘ PROJEKTU: [{proj.id}] {proj.name}")
            print(f"  -> {proj.folder_path.relative_to(mgr.vault_root)}/")
        except (ValueError, FileExistsError) as exc:
            print(f"❌ BŁĄD TWORZENIA PROJEKTU: {exc}")
            return 1
        print("----------------------------------------------------------------")

    if run_status:
        target_id, target_status = (
            (args.set_status[0], args.set_status[1])
            if args.set_status
            else (args.pause, "paused")
        )
        try:
            proj = mgr.set_project_status(target_id, target_status, dry_run=args.dry_run)
            prefix = "[DRY-RUN] " if args.dry_run else ""
            print(f"🔄 {prefix}ZMIENIONO STATUS PROJEKTU: [{proj.id}] {proj.name} -> {proj.status}")
        except (ValueError, FileNotFoundError) as exc:
            print(f"❌ BŁĄD ZMIANY STATUSU: {exc}")
            return 1
        print("----------------------------------------------------------------")

    if run_archive:
        moved = mgr.archive_completed_projects(dry_run=args.dry_run)
        if moved:
            print(f"📦 ZARCHIWIZOWANO ZAKOŃCZONE PROJEKTY ({len(moved)}):")
            for src, dst in moved:
                prefix = "[DRY-RUN] " if args.dry_run else ""
                print(f"  {prefix}{src.name} -> _archiwum/{dst.name}")
        else:
            print("📦 Brak zakończonych projektów wymagających archiwizacji.")
        print("----------------------------------------------------------------")

    if run_bundle:
        if args.dry_run:
            print("[DRY-RUN] Przeskanowano by Dzienniki Sesji i skompletowano dokumentację w Notatki/.")
        else:
            bundled = mgr.bundle_all_archived_projects()
            if bundled:
                print(f"📄 SKOMPLETOWANO DOKUMENTACJĘ W ARCHIWUM ({len(bundled)} projektów):")
                for pid, flist in bundled.items():
                    print(f"  • [{pid}]: dodano {len(flist)} dokumentów do Notatki/")
            else:
                print("📄 Dokumentacja wszystkich projektów archiwalnych jest w pełni zsynchronizowana.")
        print("----------------------------------------------------------------")

    if run_sort:
        if args.dry_run:
            print("[DRY-RUN] Wygenerowano by posortowany _Spis Projektów.md.")
        else:
            out_file = mgr.update_index_file()
            print("✅ ZAKTUALIZOWANO I POSORTOWANO SPIS PROJEKTÓW:")
            print(f"  -> {out_file.relative_to(mgr.vault_root)}")
        print("----------------------------------------------------------------")

    if run_check:
        ok, issues = mgr.check_integrity()
        if ok:
            print("🟢 STATUS CHECK: OK (wszystkie reguły spełnione)")
        else:
            print(f"🔴 STATUS CHECK: WYKRYTO NIEPRAWIDŁOWOŚCI ({len(issues)}):")
            for iss in issues:
                print(f"  {iss}")
            return 1
        print("----------------------------------------------------------------")

    if run_scan:
        projects = mgr.scan_all_projects()
        active = [p for p in projects if not p.is_archived and p.status == "active"]
        queued = [p for p in projects if not p.is_archived and p.status == "queued"]
        paused = [p for p in projects if not p.is_archived and p.status == "paused"]
        completed = [p for p in projects if p.is_archived or p.status == "completed"]

        print(f"PODSUMOWANIE PORTFELA PROJEKTÓW ({len(projects)}):")
        print(f"  - 🟢 W toku (aktywne):        {len(active)}")
        for p in active:
            print(f"      • [{p.id}] {p.name} ({p.phase_context or 'w toku'})")
        print(f"  - 🟡 Oczekujące:              {len(queued)}")
        for p in queued:
            print(f"      • [{p.id}] {p.name}")
        print(f"  - ⏸️ Wstrzymane:              {len(paused)}")
        for p in paused:
            print(f"      • [{p.id}] {p.name}")
        print(f"  - 🏁 W archiwum (_archiwum/): {len(completed)}")
        for p in completed:
            print(f"      • [{p.id}] {p.name} (data: {p.completed_date or p.updated})")
        print("================================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
