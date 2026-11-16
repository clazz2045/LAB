#!/usr/bin/env python3
"""drain_inbox.py - Narzędzie drenażu Skrzynki do Zera (drain-to-zero) dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (datetime, json, pathlib, argparse, shutil).
- Pełna integracja z core_config.ConfigManager.
- Zero zewnętrznych zależności pip.

Odpowiada za:
1. Listowanie zawartości Skrzynki (01 Skrzynka) z wyłączeniem _archiwum i plików ukrytych.
2. Archiwizację plików do '01 Skrzynka/_archiwum/YYYY-MM/'.
3. Deterministyczne odnotowanie zrzuconych elementów w kanonicznym Dzienniku Dnia
   w Warsztacie (bez zanieczyszczania kokpitu Dashboard LAB.md ani Agendy Roboczej).
4. Obsługę trybu --dry-run oraz --json dla automatyzacji agentowych.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Blokada tworzenia folderów __pycache__ w iCloud
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from lab_core.env import ensure_vault_in_sys_path, find_vault_root
ensure_vault_in_sys_path(_script_dir)

from lab_core.config import ConfigError, ConfigManager
from lab_core.markdown import parse_frontmatter
from lab_core.routing import DrainRouter, extract_date

IGNORED_FILES = {".DS_Store", "desktop.ini"}


@dataclass
class InboxItem:
    name: str
    path: str
    rel_path: str
    is_dir: bool
    size_bytes: int
    modified_iso: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DrainReport:
    status: str
    vault_root: str
    inbox_path: str
    total_items: int
    instance_name: str = "LAB"
    archived_items: List[str] = field(default_factory=list)
    logged_to_workshop: bool = False
    dry_run: bool = False
    items: List[InboxItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vault_root": self.vault_root,
            "inbox_path": self.inbox_path,
            "total_items": self.total_items,
            "archived_items_count": len(self.archived_items),
            "archived_items": self.archived_items,
            "logged_to_workshop": self.logged_to_workshop,
            "dry_run": self.dry_run,
            "items": [it.to_dict() for it in self.items],
        }


class InboxDrainer:
    """Zarządca drenażu Skrzynki (drain-to-zero) w LAB 2."""

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root()
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)

        try:
            self.inbox_room = self.config.get_room_by_role("ingestion")
        except ConfigError:
            self.inbox_room = self.config.get_room("01")

        self.inbox_dir = self.vault_root / self.inbox_room.path
        self.router = DrainRouter(vault_root=self.vault_root, config=self.config)

    def list_items(self) -> List[InboxItem]:
        """Zwraca listę elementów w Skrzynce oczekujących na przetworzenie."""
        if not self.inbox_dir.is_dir():
            return []

        items: List[InboxItem] = []
        for entry in self.inbox_dir.iterdir():
            if entry.name.startswith(".") or entry.name in IGNORED_FILES or entry.name == "_archiwum":
                continue

            stat = entry.stat()
            dt = datetime.datetime.fromtimestamp(stat.st_mtime)
            items.append(
                InboxItem(
                    name=entry.name,
                    path=str(entry),
                    rel_path=str(entry.relative_to(self.vault_root)),
                    is_dir=entry.is_dir(),
                    size_bytes=stat.st_size if entry.is_file() else 0,
                    modified_iso=dt.isoformat(),
                )
            )

        # Sortuj alfabetycznie
        items.sort(key=lambda x: x.name.lower())
        return items

    def _extract_date(self, filename: str) -> Optional[datetime.date]:
        """Wyciąga datę YYYY-MM-DD z nazwy pliku lub zwraca None."""
        return extract_date(filename)

    def get_destination_dir(self, item_path: Path, dt: Optional[datetime.date] = None) -> Path:
        """Zwraca docelowy katalog drenażu w zależności od typu pliku poprzez DrainRouter:
        - Dokumenty (.pdf, .docx, .epub, .xlsx, .pptx, .csv) -> 06 Zasoby/Dokumenty
        - Obrazy (.png, .jpg, .webp, .gif, .svg) -> 06 Zasoby/Obrazy
        - Audio (.mp3, .m4a, .wav, .aac, .flac, .ogg) -> 06 Zasoby/Audio
        - Surowe zrzuty myśli (Wispr_*, type: raw-capture, source: wispr-flow) -> 01 Skrzynka/_archiwum/YYYY-MM/
        - Notatki referencyjne SAT (type: note i note_type: reference lub source_url:, o ile NIE są raw-capture) -> 06 Zasoby/Notatki
        - Wsady surowe i pozostałe pliki -> 01 Skrzynka/_archiwum/YYYY-MM/
        """
        return self.router.resolve_destination(item_path, dt=dt)

    def get_archive_dir(self, dt: Optional[datetime.date] = None) -> Path:
        """Zwraca domyślny katalog archiwum w Skrzynce (01 Skrzynka/_archiwum/YYYY-MM/)."""
        target_date = dt or datetime.date.today()
        return self.inbox_dir / "_archiwum" / target_date.strftime("%Y-%m")

    def archive_item(
        self,
        target_name_or_rel: str,
        dry_run: bool = False,
    ) -> Path:
        """
        Drenuje wskazany plik/katalog ze Skrzynki do właściwego pokoju docelowego:
        - Dokumenty/Obrazy/Audio -> 06 Zasoby/
        - Materiały referencyjne -> 06 Zasoby/Notatki/
        - Wsady surowe i zrzuty -> 01 Skrzynka/_archiwum/YYYY-MM/
        W przypadku zrzutów myśli (Wispr / raw-capture), ich treść jest automatycznie
        doklejana do kanonicznego dziennika dnia w Warsztacie (02 Warsztat/_archiwum/YYYY/MM/YYYY-MM-DD.md).
        Zwraca docelową ścieżkę.
        """
        # Znajdź plik w Skrzynce
        candidate = self.inbox_dir / target_name_or_rel
        if not candidate.exists():
            # Sprawdź czy podano relatywną ścieżkę do skarbca
            candidate = self.vault_root / target_name_or_rel
            if not candidate.exists() or candidate.parent != self.inbox_dir:
                raise FileNotFoundError(
                    f"Nie znaleziono elementu '{target_name_or_rel}' bezpośrednio w Skrzynce ({self.inbox_dir})"
                )

        # Reguła konkatenacji dla zrzutów myśli (Wispr / raw-capture):
        if candidate.suffix == ".md" and candidate.is_file():
            try:
                fm, body = parse_frontmatter(candidate)
                fm_type = str(fm.get("type", "")).strip().lower()
                fm_source = str(fm.get("source", "")).strip().lower()
                is_thought_capture = (
                    candidate.name.startswith("Wispr_")
                    or fm_type == "raw-capture"
                    or fm_source == "wispr-flow"
                    or "source: wispr-flow" in body
                    or "type: raw-capture" in body
                )
                if is_thought_capture and not dry_run:
                    self._append_to_daily_journal(candidate, raw_text=candidate.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass

        dest_dir = self.get_destination_dir(candidate)
        dest_path = dest_dir / candidate.name

        # Obsłuż kolizję nazw
        if dest_path.exists():
            stem = candidate.stem if candidate.is_file() else candidate.name
            suffix = candidate.suffix if candidate.is_file() else ""
            ts = datetime.datetime.now().strftime("%H%M%S")
            dest_path = dest_dir / f"{stem}_{ts}{suffix}"

        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(candidate), str(dest_path))

            # Jeśli przedrenowano dokument tekstowy/binarny do Zasobów,
            # zagwarantuj istnienie karty referencyjnej z flagą consumed: false
            doc_exts = {".pdf", ".docx", ".doc", ".epub", ".xlsx", ".pptx", ".csv"}
            if dest_path.is_file() and dest_path.suffix.lower() in doc_exts:
                self._ensure_companion_reference_note(dest_path)
            elif dest_path.is_file() and dest_path.suffix.lower() == ".md" and dest_dir.name == "Notatki":
                self._ensure_source_metadata_callout(dest_path)

        return dest_path

    def _ensure_source_metadata_callout(self, note_path: Path) -> None:
        """Bramka ingestu: gwarantuje obecność calloutu '> [!info] Metadane źródłowe' w notatce referencyjnej."""
        try:
            content = note_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        if re.search(r"^>\s*\[!info\]", content, re.MULTILINE | re.IGNORECASE):
            return

        fm, body = parse_frontmatter(content)
        source_url = str(fm.get("source_url") or fm.get("source") or fm.get("url") or "").strip()
        created_val = str(fm.get("created") or datetime.date.today().isoformat()).strip()

        callout_lines = ["> [!info] Metadane źródłowe"]
        if source_url:
            callout_lines.append(f"> - **Źródło:** {source_url}")
        callout_lines.append(f"> - **Data dodania:** {created_val}")
        callout_block = "\n".join(callout_lines) + "\n\n"

        # Wstaw po pierwszym nagłówku H1 lub bezpośrednio po frontmatterze
        h1_match = re.search(r"^(#\s+[^\n]+\n+)", body, re.MULTILINE)
        if h1_match:
            pos = h1_match.end()
            new_body = body[:pos] + callout_block + body[pos:]
        else:
            new_body = "\n" + callout_block + body.lstrip("\n")

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                new_content = f"---{parts[1]}---\n{new_body.lstrip(chr(10))}"
                note_path.write_text(new_content, encoding="utf-8")
                return

        note_path.write_text(new_body, encoding="utf-8")

    def _ensure_companion_reference_note(self, doc_path: Path) -> Optional[Path]:
        """
        Zapewnia obecność notatki referencyjnej w '06 Zasoby/Notatki/' dla zdeponowanego dokumentu.
        Gwarantuje stempel 'consumed: false', włączając dokument do kolejki czytania człowieka.
        """
        try:
            assets_room = self.config.get_room_by_role("reference-and-assets")
        except ConfigError:
            assets_room = self.config.get_room("06")

        notes_dir = self.vault_root / assets_room.path / "Notatki"
        if not notes_dir.is_dir():
            notes_dir.mkdir(parents=True, exist_ok=True)

        # Sprawdź czy jakakolwiek istniejąca notatka już linkuje do tego pliku
        doc_filename = doc_path.name
        doc_stem = doc_path.stem
        for existing_file in notes_dir.glob("*.md"):
            try:
                content = existing_file.read_text(encoding="utf-8", errors="ignore")
                if doc_filename in content or f"[[{doc_stem}]]" in content:
                    return existing_file
            except Exception:
                continue

        # Utwórz nową notatkę referencyjną
        target_note = notes_dir / f"{doc_stem}.md"
        if target_note.exists():
            ts = datetime.datetime.now().strftime("%H%M%S")
            target_note = notes_dir / f"{doc_stem}_{ts}.md"

        rel_doc = str(doc_path.relative_to(self.vault_root))
        today_str = datetime.date.today().isoformat()
        user_display = getattr(getattr(self.config, "user", None), "call_sign", None) or "Właściciela Systemu"

        content = f"""---
type: note
note_type: reference
created: {today_str}
consumed: false
stage: raw
topics:
  - "[[06 Zasoby/Dokumenty]]"
tags: [dokument, zasoby, do-przeczytania]
---

# {doc_stem}

> [!info] Metadane źródłowe
> - **Plik źródłowy:** [[{rel_doc}|Otwórz dokument]]
> - **Data dodania:** {today_str}
> - **Etap opracowania:** `stage: raw` (surowy wsad archiwalny do przeczytania przez {user_display})

---

## 📄 Wprowadzenie i Opis Materiału

Dokument `{doc_filename}` wprowadzony do Zasobów ze Skrzynki. Materiał został automatycznie zarejestrowany w kolejce lektur z flagą `consumed: false` do swobodnej lektury przez {user_display}.

Dokument oczekuje na zapoznanie się, lekturę oraz ewentualne opracowanie dojrzałej syntezy SAT lub ekstrakcję kluczowych wniosków do Bazy Wiedzy. Karta gwarantuje, że żaden wsad tekstowy lub analityczny nie umknie uwadze człowieka i zostanie odłożony w czasie do asynchronicznej weryfikacji.

---

## 🔗 Powiązania i Przestrzenie Tematyczne
- [[06 Zasoby/Dokumenty]]
"""
        target_note.write_text(content, encoding="utf-8")
        return target_note

    def _append_to_daily_journal(self, candidate_path: Path, raw_text: str) -> None:
        """Deterministycznie dokleja treść zrzutu do kanonicznego Dziennika Dnia w Warsztacie."""
        target_date = self._extract_date(candidate_path.name) or datetime.date.today()
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")
        day_str = target_date.strftime("%Y-%m-%d")

        try:
            workshop_room = self.config.get_room_by_role("daily-workbench")
        except ConfigError:
            workshop_room = self.config.get_room("02")

        workshop_archive_dir = self.vault_root / workshop_room.path / "_archiwum" / year_str / month_str
        workshop_archive_dir.mkdir(parents=True, exist_ok=True)
        daily_file = workshop_archive_dir / f"{day_str}.md"

        # Wydobądź właściwą treść (bez nagłówka frontmatter)
        _, body = parse_frontmatter(raw_text)

        # Wyciągnij godzinę z nazwy pliku lub metadanych
        time_tag = ""
        time_match = re.search(r"_(\d{2})(\d{2})_", candidate_path.name)
        if time_match:
            time_tag = f"{time_match.group(1)}:{time_match.group(2)} "

        header_title = candidate_path.stem
        if header_title.startswith("Wispr_"):
            # Uprość nagłówek
            header_title = header_title.replace("Wispr_", "Wispr: ")

        entry_text = f"\n\n---\n\n## 🎙️ {time_tag}{header_title}\n\n{body}\n"

        if not daily_file.is_file():
            init_content = f"""---
type: scratchpad
date: {day_str}
title: "Dziennik Dnia: {day_str}"
tags:
  - brudnopis
  - warsztat
  - dziennik-dnia
---

# 🛠️ Dziennik Dnia: {day_str}
"""
            daily_file.write_text(init_content + entry_text, encoding="utf-8")
        else:
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(entry_text)

    def log_to_workshop(
        self,
        archived_names: List[str],
        dry_run: bool = False,
    ) -> bool:
        """
        Zdeprecjonowane: Zgodnie z wolą Właściciela Systemu i polityką [policies.workshop],
        stół roboczy Warsztatu nie gromadzi historii zrzutów ze skrzynki.
        Wszystkie zrzuty trafiają do 01 Skrzynka/_archiwum/ oraz Dziennika Dnia
        (02 Warsztat/_archiwum/YYYY/MM/YYYY-MM-DD.md). Ta metoda zwraca False
        i nie modyfikuje pliku Warsztatu.
        """
        return False

    def drain(
        self,
        specific_file: Optional[str] = None,
        archive_all: bool = False,
        log_workshop: bool = False,
        dry_run: bool = False,
    ) -> DrainReport:
        """Wykonuje operację inspekcji lub drenażu Skrzynki."""
        items = self.list_items()
        archived: List[str] = []

        if specific_file:
            # Archiwizuj konkretny plik
            dest = self.archive_item(specific_file, dry_run=dry_run)
            archived.append(Path(specific_file).name)
        elif archive_all:
            # Archiwizuj wszystkie pliki
            for it in items:
                dest = self.archive_item(it.name, dry_run=dry_run)
                archived.append(it.name)

        logged = False
        if log_workshop and archived:
            logged = self.log_to_workshop(archived, dry_run=dry_run)

        # Pobierz zaktualizowaną listę po drenażu
        remaining_items = self.list_items() if (archived and not dry_run) else items

        if archived and not dry_run:
            try:
                from lab_core.dashboards import DashboardBuilder
                DashboardBuilder(vault_root=self.vault_root).write_dashboard()
            except Exception:
                pass

        status = "ok" if len(remaining_items) == 0 else "pending"

        return DrainReport(
            status=status,
            vault_root=str(self.vault_root),
            inbox_path=str(self.inbox_dir.relative_to(self.vault_root)),
            total_items=len(remaining_items),
            instance_name=self.config.instance_name,
            archived_items=archived,
            logged_to_workshop=logged,
            dry_run=dry_run,
            items=remaining_items,
        )


def render_terminal_report(report: DrainReport) -> str:
    """Renderuje czytelne podsumowanie stanu Skrzynki."""
    lines = [
        "=" * 64,
        f"SKRZYNKA (DRAIN-TO-ZERO) - {report.instance_name} (STATUS: {report.status.upper()})",
        "=" * 64,
        f"Katalog Skrzynki:  {report.inbox_path}",
        f"Elementy w toku:   {report.total_items} nieprzetworzonych",
        f"Zarchiwizowane:    {len(report.archived_items)} elementów",
        f"Zapis w Warsztacie:{'TAK' if report.logged_to_workshop else 'NIE'}",
        f"Tryb Dry-Run:      {'TAK' if report.dry_run else 'NIE'}",
        "-" * 64,
    ]

    if report.archived_items:
        lines.append("ZARCHIWIZOWANE ELEMENTY:")
        for name in report.archived_items:
            lines.append(f"  ✓ {name}")
        lines.append("-" * 64)

    if not report.items:
        lines.append("🟢 Skrzynka jest idealnie pusta. Polityka drain-to-zero spełniona.")
    else:
        lines.append(f"ELEMENTY OCZEKUJĄCE NA PRZETWORZENIE ({len(report.items)}):")
        for it in report.items:
            kind = "[KATALOG]" if it.is_dir else f"[{it.size_bytes} B]"
            lines.append(f"  - {it.name:36} {kind:>12} (mod: {it.modified_iso[:16]})")

    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drenaż Skrzynki do Zera (drain-to-zero) w LAB 3.0")
    parser.add_argument("--vault-root", help="Katalog główny skarbca (domyślnie: autodetekcja)")
    parser.add_argument("--config", help="Ścieżka do lab.config.toml (opcjonalnie)")
    parser.add_argument("--archive", help="Nazwa konkretnego pliku do zarchiwizowania z 01 Skrzynka")
    parser.add_argument("--all", action="store_true", help="Zarchiwizuj wszystkie elementy ze Skrzynki do _archiwum/YYYY-MM/")
    parser.add_argument("--log-to-workshop", action="store_true", help="Odnotuj operację w Agendzie Roboczej Dnia")
    parser.add_argument("--dry-run", action="store_true", help="Wykonaj bez fizycznego przenoszenia plików")
    parser.add_argument("--json", action="store_true", help="Zwróć wynik jako obiekt JSON")
    args = parser.parse_args()

    try:
        drainer = InboxDrainer(vault_root=args.vault_root, config_path=args.config)
    except ConfigError as ex:
        if args.json:
            print(json.dumps({"status": "error", "message": str(ex)}, indent=2))
        else:
            print(f"[ERROR] Błąd konfiguracji LAB 3: {ex}", file=sys.stderr)
        return 1

    try:
        report = drainer.drain(
            specific_file=args.archive,
            archive_all=args.all,
            log_workshop=args.log_to_workshop,
            dry_run=args.dry_run,
        )
    except Exception as ex:
        if args.json:
            print(json.dumps({"status": "error", "message": str(ex)}, indent=2))
        else:
            print(f"[ERROR] Błąd drenażu Skrzynki: {ex}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_terminal_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
