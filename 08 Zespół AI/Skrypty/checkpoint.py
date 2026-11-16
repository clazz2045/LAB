#!/usr/bin/env python3
"""checkpoint.py - Narzędzie zamykania sesji roboczej dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (datetime, hashlib, json, pathlib, argparse, re).
- Oparte o core_config.ConfigManager i check_quality.QualityChecker.
- Zero zewnętrznych zależności pip.

Odpowiada za:
1. Odczytanie plików zmodyfikowanych w oknie sesji (na podstawie mtime).
2. Wygenerowanie ustrukturyzowanego szablonu Dziennika Sesji w:
   '08 Zespół AI/Dzienniki Sesji/YYYY/MM/YYYY-MM-DD-HH-MM_<coord>_<slug>.md'.
3. Wystawienie podpisanego cyfrowo kwitu zamknięcia sesji (receipt) potwierdzającego stan.
4. Obsługę flagi --assert-logged (weryfikacja czy dzisiejszy wpis został utworzony).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Zaimportuj core_config i check_quality z katalogu bieżącego lub katalogów nadrzędnych
_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigManager, ConfigError, find_vault_root

try:
    from check_quality import check_quality, QualityReport
except ImportError:
    check_quality = None

from lab_core.quality.inspectors.workshop import (
    extract_agenda_section,
    extract_unfinished_agenda_tasks,
)

# Ignorowane katalogi i pliki przy skanowaniu zmian
IGNORED_DIRS = {".obsidian", ".git", "__pycache__", ".pytest_cache", ".trash", "_archiwum"}
IGNORED_FILES = {".DS_Store", "desktop.ini"}
_AGENDA_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*–\s*Agenda Robocza\.md$", re.IGNORECASE)


@dataclass
class SessionReceipt:
    receipt_id: str
    session_id: str
    timestamp: str
    coordinator: str
    session_log_path: str
    files_touched_count: int
    files_touched: List[str]
    vault_status: str
    signature: str
    instance_name: str = "LAB"
    compliance: Dict[str, Any] = field(default_factory=dict)
    agenda_check: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_signature(session_id: str, timestamp: str, count: int, status: str) -> str:
    """Oblicza sumę kontrolną SHA-256 dla kwitu zamknięcia sesji."""
    payload = f"{session_id}|{timestamp}|{count}|{status}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class ComplianceAnnotationRequired(Exception):
    """Naruszenie Reguły 8 w sesji wymaga jawnej adnotacji (--violation-note)."""

    def __init__(self, report: Dict[str, Any]) -> None:
        super().__init__("; ".join(report.get("violations", [])))
        self.report = report


class SessionCheckpoint:
    """Zarządca procesu checkpointu i zamykania sesji w LAB 2."""

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root()
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)

    def get_session_logs_dir(self, dt: Optional[datetime.datetime] = None) -> Path:
        """Zwraca ścieżkę do katalogu dzienników sesji w formacie YYYY/MM."""
        now = dt or datetime.datetime.now()
        year_str = now.strftime("%Y")
        month_str = now.strftime("%m")

        try:
            ai_team_room = self.config.get_room_by_role("governance-and-engine")
            base_dir = self.vault_root / ai_team_room.path / "Dzienniki Sesji"
        except ConfigError:
            base_dir = self.vault_root / "08 Zespół AI" / "Dzienniki Sesji"

        return base_dir / year_str / month_str

    def assert_logged(self, dt: Optional[datetime.date] = None) -> Tuple[bool, Optional[Path]]:
        """
        Weryfikuje, czy dla wskazanego dnia (domyślnie: dzisiaj) istnieje wpis dziennika sesji.
        Zwraca (True, path) jeśli znaleziono, lub (False, None) jeśli brak.
        """
        target_date = dt or datetime.date.today()
        date_prefix = target_date.isoformat()  # "YYYY-MM-DD"
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")

        logs_month_dir = self.get_session_logs_dir(
            datetime.datetime(target_date.year, target_date.month, target_date.day)
        )

        candidates: List[Path] = []
        if logs_month_dir.is_dir():
            for f in logs_month_dir.iterdir():
                if f.is_file() and f.name.startswith(date_prefix) and f.suffix == ".md":
                    candidates.append(f)

        # Sprawdź również alternatywne ścieżki w obrębie 08 Zespół AI/Dzienniki Sesji
        if not candidates:
            try:
                ai_team = self.vault_root / self.config.get_room_by_role("governance-and-engine").path
            except ConfigError:
                ai_team = self.vault_root / "08 Zespół AI"

            session_root = ai_team / "Dzienniki Sesji"
            if session_root.is_dir():
                for root, _, files in os.walk(session_root):
                    for f in files:
                        if f.startswith(date_prefix) and f.endswith(".md"):
                            candidates.append(Path(root) / f)

        if candidates:
            # Sortuj po mtime malejąco (najnowszy)
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return True, candidates[0]

        return False, None

    def get_modified_files(self, hours: float = 8.0) -> List[Path]:
        """Zwraca listę plików zmodyfikowanych w zadanym oknie czasowym (w godzinach)."""
        cutoff = time.time() - (hours * 3600.0)
        modified: List[Path] = []

        for root, dirs, files in os.walk(self.vault_root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

            for f in files:
                if f in IGNORED_FILES or f.startswith("."):
                    continue
                file_p = Path(root) / f
                try:
                    mtime = file_p.stat().st_mtime
                    if mtime >= cutoff:
                        modified.append(file_p)
                except OSError:
                    continue

        # Sortuj po mtime malejąco
        modified.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return modified

    def check_active_agenda(self) -> Dict[str, Any]:
        """Odczytuje aktywną Agendę Roboczą Dnia i zwraca otwarte pozycje (- [ ]) z sekcji '## 🎯'."""
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            ws_dir = self.vault_root / ws_room.path
        except ConfigError:
            return {"agenda_file": None, "section_found": False, "open_tasks": []}

        if not ws_dir.is_dir():
            return {"agenda_file": None, "section_found": False, "open_tasks": []}

        agendas: List[Tuple[datetime.date, Path]] = []
        for item in ws_dir.iterdir():
            if not item.is_file():
                continue
            m = _AGENDA_FILE_RE.match(item.name)
            if not m:
                continue
            try:
                dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
                agendas.append((dt, item))
            except ValueError:
                continue

        if not agendas:
            return {"agenda_file": None, "section_found": False, "open_tasks": []}

        agendas.sort(key=lambda x: x[0], reverse=True)
        agenda_path = agendas[0][1]
        rel_agenda = str(agenda_path.relative_to(self.vault_root))

        try:
            content = agenda_path.read_text(encoding="utf-8")
        except OSError:
            return {"agenda_file": rel_agenda, "section_found": False, "open_tasks": []}

        section_body = extract_agenda_section(content, "threads")
        if not section_body:
            return {"agenda_file": rel_agenda, "section_found": False, "open_tasks": []}

        raw_tasks = extract_unfinished_agenda_tasks(content)
        open_tasks = [t.splitlines()[0].strip() for t in raw_tasks if t.strip()]
        return {
            "agenda_file": rel_agenda,
            "section_found": True,
            "open_tasks": open_tasks,
        }

    def check_project_progress_sync(
        self,
        modified_rel_files: List[str],
        agenda_rel_path: Optional[str] = None,
    ) -> List[str]:
        """Zwraca nieblokujące ostrzeżenia, gdy zmodyfikowano pliki projektu PR-*,
        a progress-report.md ma starszy mtime niż główny plik projektu lub dzisiejsza Agenda."""
        warnings: List[str] = []
        try:
            proj_room = self.config.get_room_by_role("active-projects")
            proj_prefix = proj_room.path.strip("/")
        except ConfigError:
            proj_prefix = "03 Projekty"

        projects_root = self.vault_root / proj_prefix
        if not projects_root.is_dir():
            return warnings

        agenda_mtime: Optional[float] = None
        if agenda_rel_path:
            agenda_abs = self.vault_root / agenda_rel_path
            if agenda_abs.is_file():
                try:
                    agenda_mtime = agenda_abs.stat().st_mtime
                except OSError:
                    agenda_mtime = None

        touched_projects: set[str] = set()
        for rel in modified_rel_files:
            rel_norm = rel.replace("\\", "/")
            prefix_slash = f"{proj_prefix}/"
            if not rel_norm.startswith(prefix_slash):
                continue
            rest = rel_norm[len(prefix_slash):]
            parts = rest.split("/")
            if len(parts) >= 2 and parts[0].startswith("PR-"):
                touched_projects.add(parts[0])

        for proj_name in sorted(touched_projects):
            proj_dir = projects_root / proj_name
            if not proj_dir.is_dir():
                continue
            progress_file = proj_dir / "progress-report.md"
            main_file = proj_dir / f"{proj_name}.md"
            if not main_file.is_file():
                candidates = sorted(proj_dir.glob("PR-*.md"))
                main_file = candidates[0] if candidates else main_file

            if not progress_file.is_file():
                warnings.append(
                    f"[WARNING] Projekt '{proj_name}': zmodyfikowano pliki projektu w sesji, ale brak pliku 'progress-report.md'."
                )
                continue

            try:
                progress_mtime = progress_file.stat().st_mtime
            except OSError:
                continue

            reasons: List[str] = []
            if main_file.is_file():
                try:
                    if progress_mtime < main_file.stat().st_mtime:
                        reasons.append(f"starszy mtime niż '{main_file.name}'")
                except OSError:
                    pass
            if agenda_mtime is not None and progress_mtime < agenda_mtime:
                reasons.append(f"starszy mtime niż '{agenda_rel_path}'")

            if reasons:
                warnings.append(
                    f"[WARNING] Projekt '{proj_name}': 'progress-report.md' może być nieaktualny ({', '.join(reasons)})."
                )

        return warnings

    def create_session_log(
        self,
        slug: str = "checkpoint",
        hours: float = 8.0,
        now: Optional[datetime.datetime] = None,
        vault_status: str = "ok",
        dry_run: bool = False,
        compliance: Optional[Dict[str, Any]] = None,
        violation_note: Optional[str] = None,
    ) -> Tuple[Path, str, List[str]]:
        """
        Tworzy plik dziennika sesji w '08 Zespół AI/Dzienniki Sesji/YYYY/MM/'.
        Zwraca: (sciezka_pliku, session_id, lista_zmodyfikowanych_plikow_relatywnych).
        """
        current_time = now or datetime.datetime.now()
        date_str = current_time.strftime("%Y-%m-%d")
        time_str = current_time.strftime("%H:%M")
        ts_filename = current_time.strftime("%Y-%m-%d-%H-%M")

        # Koordynator (dynamicznie przez role_key 'coordinator')
        try:
            coord_name = self.config.get_agent_name("coordinator")
        except Exception:
            coord_name = "Koordynator"
        coord_slug = coord_name.lower()

        # Oczyszczenie sluga
        clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", slug.strip().lower()).strip("-")
        if not clean_slug:
            clean_slug = "checkpoint"

        session_id = f"{ts_filename}_{coord_slug}_{clean_slug}"
        log_dir = self.get_session_logs_dir(current_time)

        if not dry_run:
            log_dir.mkdir(parents=True, exist_ok=True)

        target_file = log_dir / f"{session_id}.md"

        # Znajdź zmodyfikowane pliki
        modified_paths = self.get_modified_files(hours=hours)
        rel_modified = [str(p.relative_to(self.vault_root)) for p in modified_paths]

        # Wygeneruj listę wypunktowaną
        if rel_modified:
            files_bullets = "\n".join(f"- `{f}`" for f in rel_modified)
        else:
            files_bullets = "- *(Brak wykrytych modyfikacji w oknie sesji)*"

        status_symbols = {
            "ok": "🟢 OK (Pełna zgodność)",
            "attention": "🟡 ATTENTION (Wymaga uwagi)",
            "broken": "🔴 BROKEN (Błędy krytyczne)",
        }
        status_display = status_symbols.get(vault_status, vault_status.upper())

        slug_title = clean_slug.replace("-", " ").capitalize()

        if not compliance:
            compliance_section = "- Audyt zgodności sesji (Reguła 8): *niewykonany (brak ścieżki transkryptu)*."
        elif compliance.get("status") == "ok":
            compliance_section = (
                f"- Audyt zgodności sesji (Reguła 8): OK (odczyty przed invoke_subagent: "
                f"{compliance.get('reads_before_first_invoke', 0)}, zapisy: {compliance.get('write_calls', 0)})."
            )
        else:
            viol = "\n".join(f"  - {v}" for v in compliance.get("violations", []))
            compliance_section = (
                "- **NARUSZENIE Reguły 8 (wykryte po fakcie):**\n"
                f"{viol}\n- Adnotacja Koordynatora: {violation_note or '(brak)'}"
            )

        content = f"""---
type: session-log
session_id: "{session_id}"
date: {date_str}
time: "{time_str}"
coordinator: "{coord_name}"
status: completed
vault_status: "{vault_status}"
files_touched_count: {len(rel_modified)}
tags:
  - dziennik-sesji
  - lab2
  - checkpoint
---

# Dziennik Sesji: {slug_title}

> **Data sesji:** {date_str} {time_str}  
> **Koordynator:** {coord_name}  
> **Stan skarbca:** {status_display}  
> **Pliki w oknie sesji:** {len(rel_modified)} zmodyfikowanych  

---

## 1. Cel i Zakres Sesji
Zamknięcie okna roboczego sesji, synchronizacja stanu i weryfikacja integralności skarbca LAB 3.0.

---

## 2. Pliki Zmodyfikowane w Sesji ({len(rel_modified)})
{files_bullets}

---

## 3. Kluczowe Decyzje i Rezultaty
- Przeprowadzono inspekcję jakościową skarbca (KISS Quality Gate).
- Zarejestrowano kwit zamknięcia sesji roboczej.

---

## 3a. Zgodność Sesji z Regułą 8
{compliance_section}

---

## 4. Zadania Otwarte i Następne Kroki
- [ ] Przegląd Agendy Roboczej Dnia na otwarcie kolejnej sesji.
"""

        if not dry_run:
            target_file.write_text(content, encoding="utf-8")

        return target_file, session_id, rel_modified

    def run_checkpoint(
        self,
        slug: str = "checkpoint",
        hours: float = 8.0,
        dry_run: bool = False,
        transcript_path: Optional[Union[str, Path]] = None,
        violation_note: Optional[str] = None,
    ) -> SessionReceipt:
        """Przeprowadza pełen proces checkpointu: weryfikacja jakości, audyt zgodności, log sesji i kwit.

        Naruszenie Reguły 8 wykryte w transkrypcie wymaga adnotacji (violation_note); bez niej
        rzuca ComplianceAnnotationRequired i nie zapisuje dziennika.
        """
        compliance: Dict[str, Any] = {}
        t_path = Path(transcript_path).expanduser().resolve() if transcript_path else None
        if t_path is None:
            from lab_core.compliance import find_session_transcript
            t_path = find_session_transcript()
        if t_path is not None:
            from lab_core.compliance import check_session_compliance
            compliance = check_session_compliance(t_path)
            if compliance.get("status") != "ok" and not (violation_note and violation_note.strip()):
                raise ComplianceAnnotationRequired(compliance)

        # 1. Sprawdź jakość skarbca
        vault_status = "ok"
        if check_quality is not None:
            try:
                report = check_quality(vault_root=self.vault_root, strict_checkpoint=True)
                vault_status = report.status
            except Exception:
                vault_status = "attention"

        # 2. Utwórz wpis dziennika sesji
        now = datetime.datetime.now()
        log_file, session_id, modified_files = self.create_session_log(
            slug=slug,
            hours=hours,
            now=now,
            vault_status=vault_status,
            dry_run=dry_run,
            compliance=compliance,
            violation_note=violation_note,
        )

        if compliance and not dry_run:
            try:
                from lab_core.schema import init_master_db, record_session_compliance
                db_path = self.vault_root / self.config.get_room_by_role("sqlite-relational").path / "lab_master.db"
                if db_path.is_file():
                    init_master_db(db_path, instance_name=self.config.instance_name)
                    record_session_compliance(
                        db_path, compliance.get("transcript", ""), compliance.get("status", "unknown"),
                        int(compliance.get("reads_before_first_invoke", 0)), int(compliance.get("write_calls", 0)),
                        "; ".join(compliance.get("violations", [])), violation_note,
                    )
            except Exception:
                pass

        telemetry_dict: Dict[str, Any] = {}
        if t_path is not None:
            try:
                from lab_core.telemetry import save_session_telemetry, summarize_session_telemetry
                summary = summarize_session_telemetry(t_path)
                telemetry_dict = summary.to_dict()
                if not dry_run:
                    db_path = self.vault_root / self.config.get_room_by_role("sqlite-relational").path / "lab_master.db"
                    if db_path.is_file():
                        save_session_telemetry(db_path, summary)
            except Exception:
                pass

        # 3. Wystaw kwit zamknięcia (receipt)
        timestamp_iso = now.astimezone().isoformat()
        receipt_id = f"REC-{now.strftime('%Y%m%d-%H%M%S')}"

        try:
            coord_name = self.config.get_agent_name("coordinator")
        except Exception:
            coord_name = "Koordynator"

        rel_log_path = str(log_file.relative_to(self.vault_root)) if not dry_run else f"08 Zespół AI/Dzienniki Sesji/{now.strftime('%Y/%m')}/{session_id}.md"

        sig = calculate_signature(session_id, timestamp_iso, len(modified_files), vault_status)
        agenda_check = self.check_active_agenda()
        proj_warnings = self.check_project_progress_sync(
            modified_files, agenda_rel_path=agenda_check.get("agenda_file")
        )

        receipt = SessionReceipt(
            receipt_id=receipt_id,
            session_id=session_id,
            timestamp=timestamp_iso,
            coordinator=coord_name,
            session_log_path=rel_log_path,
            files_touched_count=len(modified_files),
            files_touched=modified_files,
            vault_status=vault_status,
            signature=sig,
            instance_name=self.config.instance_name,
            compliance={k: compliance[k] for k in ("status", "reads_before_first_invoke", "write_calls", "violations") if k in compliance},
            agenda_check=agenda_check,
            warnings=proj_warnings,
            telemetry=telemetry_dict,
        )

        return receipt


def format_receipt_json_dict(
    receipt: SessionReceipt,
    full_files: bool = False,
    max_files: int = 10,
) -> Dict[str, Any]:
    """Zwraca słownik JSON kwitu; domyślnie skraca files_touched do max_files pierwszych wpisów."""
    payload = receipt.to_dict()
    if not full_files and len(payload.get("files_touched", [])) > max_files:
        payload["files_touched"] = payload["files_touched"][:max_files]
        payload["files_touched_truncated"] = True
    return payload


def render_agenda_reminder(agenda_check: Dict[str, Any]) -> str:
    """Renderuje przypomnienie kontrolne o otwartych wątkach w Agendzie Roboczej Dnia."""
    agenda_file = agenda_check.get("agenda_file")
    if not agenda_file:
        return "KONTROLA AGENDY ROBOCZEJ: brak aktywnej Agendy w pokoju Warsztat."

    if not agenda_check.get("section_found"):
        return f"KONTROLA AGENDY ROBOCZEJ ({agenda_file}): brak sekcji '## 🎯 Główne Wątki Robocze na Dziś'."

    open_tasks = agenda_check.get("open_tasks") or []
    if not open_tasks:
        return (
            f"KONTROLA AGENDY ROBOCZEJ ({agenda_file}):\n"
            "  Wszystkie pozycje w sekcji '## 🎯 Główne Wątki Robocze na Dziś' są zamknięte."
        )

    lines = [
        f"PRZYPOMNIENIE KONTROLNE – OTWARTE POZYCJE W AGENDZIE ({agenda_file}):",
        f"  Sekcja '## 🎯 Główne Wątki Robocze na Dziś' ({len(open_tasks)} otwarte):",
    ]
    lines.extend(f"  {task}" for task in open_tasks)
    return "\n".join(lines)


def render_terminal_receipt(receipt: SessionReceipt) -> str:
    """Renderuje czytelny kwit zamknięcia sesji w terminalu."""
    lines = [
        "=" * 64,
        f"KWIT ZAMKNIĘCIA SESJI ({receipt.instance_name} SESSION RECEIPT)",
        "=" * 64,
        f"ID Kwitu:          {receipt.receipt_id}",
        f"Identyfikator:     {receipt.session_id}",
        f"Data i czas:       {receipt.timestamp}",
        f"Koordynator:       {receipt.coordinator}",
        f"Plik Dziennika:    {receipt.session_log_path}",
        f"Zmodyfikowane:     {receipt.files_touched_count} plików w oknie sesji",
        f"Status Skarbca:    {receipt.vault_status.upper()}",
        f"Podpis cyfrowy:    {receipt.signature}",
        f"Zgodność Reguły 8: {receipt.compliance.get('status', 'niewykonany (brak transkryptu)').upper()}",
        "-" * 64,
        "SESJA ZAMKNIĘTA I ZAPISANA W DZIENNIKU",
        "=" * 64,
    ]
    if receipt.agenda_check:
        lines.append(render_agenda_reminder(receipt.agenda_check))
        lines.append("=" * 64)
    if receipt.warnings:
        lines.append("OSTRZEŻENIA PROJEKTOWE (PROGRESS-REPORT):")
        lines.extend(f"  {w}" for w in receipt.warnings)
        lines.append("=" * 64)
    if receipt.telemetry:
        t_main = receipt.telemetry.get("main") or {}
        t_sub = receipt.telemetry.get("subagents") or {}
        t_tot = receipt.telemetry.get("total") or {}
        sub_cnt = int(receipt.telemetry.get("subagents_count", 0) or 0)
        lines.append("TELEMETRIA TOKENÓW SESJI:")
        lines.append(
            f"  Sesja główna : in={t_main.get('input_tokens', 0):,} | cache={t_main.get('cache_read_tokens', 0):,} | "
            f"out={t_main.get('output_tokens', 0):,} | calls={t_main.get('planner_calls', 0)} | peak={t_main.get('peak_context_tokens', 0):,}"
        )
        lines.append(
            f"  Subagenci ({sub_cnt:<2}): in={t_sub.get('input_tokens', 0):,} | cache={t_sub.get('cache_read_tokens', 0):,} | "
            f"out={t_sub.get('output_tokens', 0):,} | calls={t_sub.get('planner_calls', 0)} | peak={t_sub.get('peak_context_tokens', 0):,}"
        )
        lines.append(
            f"  Łącznie      : in={t_tot.get('input_tokens', 0):,} | cache={t_tot.get('cache_read_tokens', 0):,} | "
            f"out={t_tot.get('output_tokens', 0):,} | calls={t_tot.get('planner_calls', 0)} | peak={t_tot.get('peak_context_tokens', 0):,}"
        )
        lines.append("=" * 64)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Narzędzie zamykania sesji roboczej LAB 3.0")
    parser.add_argument("--vault-root", help="Katalog główny skarbca (domyślnie: autodetekcja)")
    parser.add_argument("--config", help="Ścieżka do lab.config.toml (opcjonalnie)")
    parser.add_argument("--slug", default="checkpoint", help="Nazwa skrócona sesji (domyślnie: checkpoint)")
    parser.add_argument("--hours", type=float, default=8.0, help="Okno czasowe sesji w godzinach (domyślnie: 8.0)")
    parser.add_argument("--assert-logged", action="store_true", help="Potwierdź istnienie dzisiejszego wpisu sesji")
    parser.add_argument("--dry-run", action="store_true", help="Wykonaj bez zapisywania pliku na dysku")
    parser.add_argument("--json", action="store_true", help="Zwróć wynik jako obiekt JSON")
    parser.add_argument("--full-files", action="store_true", help="W trybie --json wypisz pełną listę files_touched (domyślnie max 10)")
    parser.add_argument("--transcript-path", default=None, help="transcript.jsonl sesji głównej (lub LAB_TRANSCRIPT_PATH) do audytu zgodności")
    parser.add_argument("--violation-note", default=None, help="Adnotacja wymagana, gdy audyt wykryje naruszenie Reguły 8")
    args = parser.parse_args()

    try:
        cp = SessionCheckpoint(vault_root=args.vault_root, config_path=args.config)
    except ConfigError as ex:
        if args.json:
            print(json.dumps({"status": "error", "message": str(ex)}, indent=2))
        else:
            print(f"[ERROR] Błąd konfiguracji LAB 3: {ex}", file=sys.stderr)
        return 1

    # Tryb --assert-logged
    if args.assert_logged:
        logged, log_path = cp.assert_logged()
        today_str = datetime.date.today().isoformat()
        if logged and log_path:
            rel_path = log_path.relative_to(cp.vault_root)
            if args.json:
                print(json.dumps({"logged": True, "date": today_str, "file": str(rel_path)}, indent=2))
            else:
                print(f"[OK] Sesja z dnia {today_str} jest zarejestrowana: '{rel_path}'")
            return 0
        else:
            if args.json:
                print(json.dumps({"logged": False, "date": today_str, "file": None}, indent=2))
            else:
                print(f"[FAIL] Brak zarejestrowanej sesji z dnia {today_str} w '08 Zespół AI/Dzienniki Sesji/'", file=sys.stderr)
            return 1

    # Standardowy tryb checkpointu
    try:
        receipt = cp.run_checkpoint(
            slug=args.slug, hours=args.hours, dry_run=args.dry_run,
            transcript_path=args.transcript_path, violation_note=args.violation_note,
        )
    except ComplianceAnnotationRequired as ex:
        print("[CHECKPOINT ZABLOKOWANY] Wykryto naruszenie Reguły 8 w sesji głównej:", file=sys.stderr)
        for v in ex.report.get("violations", []):
            print(f"  - {v}", file=sys.stderr)
        print("Powtórz z --violation-note \"<adnotacja>\" (naruszenie zostanie zapisane w dzienniku i rejestrze).", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(format_receipt_json_dict(receipt, full_files=args.full_files), indent=2, ensure_ascii=False))
    else:
        print(render_terminal_receipt(receipt))

    if not args.dry_run:
        try:
            from lab_core.dashboards import DashboardBuilder
            DashboardBuilder(vault_root=cp.vault_root).write_dashboard()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
