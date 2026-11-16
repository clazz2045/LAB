"""lab_core.radar - Deterministyczny Radar Kondycji i Zasobów Skarbca LAB 3.0 (Framework PZW)
Standard: 100% Python Standard Library (os, sys, time, datetime, pathlib, typing). Zero-Pip.

Aksjomat: Maszyna liczy deterministycznie przy ZEROWYM zużyciu tokenów modelu LLM.
Wyznacza twarde sygnały operacyjne:
1. Eject Trigger: projekty rozrośnięte do rozmiaru aplikacji (kod, bazy, wolumen) kwalifikujące się
   do wyekstrahowania do satelitarnej instancji LAB w celu odciążenia okna kontekstowego.
2. Hibernation Trigger: projekty aktywne o statusie 'w toku', ale bez zmian od >= 14 dni (zasada KISS).
3. Queue Bottlenecks: zatory w Skrzynce (drain-to-zero) oraz kolejce lektur (consumed: false).
"""

from __future__ import annotations
import argparse
import sys

import datetime
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lab_core.config import ConfigManager, find_vault_root
from lab_core.constants import IGNORED_DIRS
from lab_core.markdown import parse_frontmatter


@dataclass
class ProjectRadarItem:
    """Metryki kondycji pojedynczego projektu."""
    id: str
    name: str
    folder_path: Path
    status: str
    file_count: int = 0
    total_size_bytes: int = 0
    has_code: bool = False
    has_db: bool = False
    latest_mtime: Optional[datetime.datetime] = None
    idle_days: int = 0
    eject_candidate: bool = False
    eject_reasons: List[str] = field(default_factory=list)
    hibernate_candidate: bool = False
    hibernate_reasons: List[str] = field(default_factory=list)


@dataclass
class ReadingQueueMetrics:
    """Metryki kolejki lektur i zrzutów."""
    total_unconsumed: int = 0
    oldest_item_name: Optional[str] = None
    oldest_item_age_days: int = 0
    bottleneck_detected: bool = False


@dataclass
class RadarReport:
    """Kompletny raport kondycji skarbca LAB."""
    generated_at: str
    active_projects_count: int
    eject_candidates: List[ProjectRadarItem]
    hibernate_candidates: List[ProjectRadarItem]
    reading_queue: ReadingQueueMetrics
    inbox_unprocessed_count: int
    summary_lines: List[str] = field(default_factory=list)


class VaultRadar:
    """Kalkulator i radar przeciążenia/niedociążenia zasobów w LAB."""

    def __init__(
        self,
        vault_root: Optional[Path] = None,
        config: Optional[ConfigManager] = None,
        idle_days_threshold: int = 14,
        eject_file_threshold: int = 10,
        eject_size_threshold_bytes: int = 500_000,
        reading_queue_threshold: int = 12,
    ) -> None:
        self.vault_root = (vault_root or find_vault_root()).resolve()
        self.config = config or ConfigManager(vault_root=self.vault_root)
        self.idle_days_threshold = idle_days_threshold
        self.eject_file_threshold = eject_file_threshold
        self.eject_size_threshold_bytes = eject_size_threshold_bytes
        self.reading_queue_threshold = reading_queue_threshold

    def scan(self, now: Optional[datetime.datetime] = None) -> RadarReport:
        """Skanuje system plików i bazy danych w ułamku sekundy, generując raport."""
        now = now or datetime.datetime.now()
        projects_dir = self.vault_root / self.config.get_room("03").path
        databases_dir = self.vault_root / self.config.get_room("07").path

        # 1. Zidentyfikuj zarejestrowane bazy danych w 07 Bazy Danych
        db_files: Set[str] = set()
        if databases_dir.is_dir():
            for f in databases_dir.iterdir():
                if f.is_file() and f.suffix in {".db", ".sqlite", ".sqlite3"}:
                    db_files.add(f.stem.lower())

        # 2. Skanuj projekty w 03 Projekty/ (z pominięciem _archiwum)
        project_items: List[ProjectRadarItem] = []
        if projects_dir.is_dir():
            for p_folder in sorted(projects_dir.iterdir()):
                if not p_folder.is_dir() or p_folder.name.startswith(".") or p_folder.name == "_archiwum":
                    continue
                item = self._analyze_project(p_folder, db_files, now)
                if item:
                    project_items.append(item)

        # 3. Przefiltruj kandydatów
        eject_candidates = [p for p in project_items if p.eject_candidate]
        hibernate_candidates = [p for p in project_items if p.hibernate_candidate]

        # 4. Skanuj kolejkę lektur (06 Zasoby/Notatki)
        reading_queue = self._analyze_reading_queue(now)

        # 5. Skanuj Skrzynkę (01 Skrzynka)
        inbox_count = self._analyze_inbox()

        # 6. Sformułuj zwięzłe linie podsumowania dla koordynatora i człowieka
        summary_lines = self._build_summary_lines(
            eject_candidates, hibernate_candidates, reading_queue, inbox_count
        )

        return RadarReport(
            generated_at=now.isoformat(timespec="seconds"),
            active_projects_count=len(project_items),
            eject_candidates=eject_candidates,
            hibernate_candidates=hibernate_candidates,
            reading_queue=reading_queue,
            inbox_unprocessed_count=inbox_count,
            summary_lines=summary_lines,
        )

    def _analyze_project(
        self, folder: Path, db_files: Set[str], now: datetime.datetime
    ) -> Optional[ProjectRadarItem]:
        """Szczegółowa analiza pojedynczego projektu."""
        folder_name = folder.name
        # Wyciągnij ID np. PR-xxx
        proj_id = "PR-???"
        if folder_name.startswith("PR-"):
            parts = folder_name.split()
            proj_id = parts[0]
            clean_name = " ".join(parts[1:])
        else:
            clean_name = folder_name

        # Odczytaj kartę projektu i jej status
        card_file = folder / f"{folder_name}.md"
        if not card_file.is_file():
            # Szukaj pliku zaczynającego się od PR-
            for f in folder.glob("PR-*.md"):
                if f.name != "progress-report.md":
                    card_file = f
                    break

        status = "active"
        if card_file.is_file():
            fm, _ = parse_frontmatter(card_file)
            raw_status = str(fm.get("status", "active")).lower()
            if "paused" in raw_status or "wstrzyman" in raw_status:
                status = "paused"
            elif "completed" in raw_status or "zakonczon" in raw_status:
                status = "completed"

        # Jeśli projekt jest wstrzymany lub zakończony, pomijamy w radarze aktywności
        if status in {"paused", "completed"}:
            return None

        # Zlicz pliki, rozmiar, kody i najnowszy mtime
        file_count = 0
        total_size = 0
        has_code = False
        has_db = False
        latest_mtime: Optional[datetime.datetime] = None

        # Sprawdź powiązanie z bazą SQLite po nazwie lub aliasie
        name_lower = clean_name.lower()
        id_lower = proj_id.lower()
        for db_name in db_files:
            if db_name in name_lower or db_name in id_lower or (db_name == "photos" and "fotograf" in name_lower):
                has_db = True
                break

        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                file_count += 1
                f_path = Path(root) / f
                try:
                    stat = f_path.stat()
                    total_size += stat.st_size
                    f_mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                    if latest_mtime is None or f_mtime > latest_mtime:
                        latest_mtime = f_mtime
                except OSError:
                    pass

                suffix = f_path.suffix.lower()
                if suffix in {".py", ".sh", ".js", ".ts", ".html"}:
                    has_code = True
                if suffix in {".db", ".sqlite", ".sqlite3"}:
                    has_db = True

        idle_days = (now - latest_mtime).days if latest_mtime else 999

        # Kryteria odłączenia (Eject Trigger)
        eject_reasons = []
        if has_code and has_db:
            eject_reasons.append("zawiera dedykowany kod wykonywalny oraz relacyjną bazę danych SQLite")
        elif has_code and file_count >= self.eject_file_threshold:
            eject_reasons.append(f"zawiera skrypty kodu i przekroczył próg złożoności ({file_count} plików)")
        elif file_count >= (self.eject_file_threshold * 2):
            eject_reasons.append(f"duży wolumen plików roboczych ({file_count} plików)")
        elif total_size >= self.eject_size_threshold_bytes and has_code:
            eject_reasons.append(f"duży rozmiar zasobów ({total_size // 1024} KB) ze skryptami inżynieryjnymi")

        is_eject = len(eject_reasons) > 0

        # Kryteria uśpienia (Hibernate Trigger)
        hibernate_reasons = []
        if idle_days >= self.idle_days_threshold:
            hibernate_reasons.append(f"brak jakiejkolwiek modyfikacji od {idle_days} dni (próg: {self.idle_days_threshold} dni)")

        is_hibernate = len(hibernate_reasons) > 0 and not is_eject

        return ProjectRadarItem(
            id=proj_id,
            name=clean_name,
            folder_path=folder,
            status=status,
            file_count=file_count,
            total_size_bytes=total_size,
            has_code=has_code,
            has_db=has_db,
            latest_mtime=latest_mtime,
            idle_days=idle_days,
            eject_candidate=is_eject,
            eject_reasons=eject_reasons,
            hibernate_candidate=is_hibernate,
            hibernate_reasons=hibernate_reasons,
        )

    def _analyze_reading_queue(self, now: datetime.datetime) -> ReadingQueueMetrics:
        """Bada stan nieprzeczytanych materiałów z flagą consumed: false w Zasobach."""
        notes_dir = self.vault_root / self.config.get_room("06").path / "Notatki"
        unconsumed_count = 0
        oldest_name: Optional[str] = None
        oldest_age = 0

        if notes_dir.is_dir():
            for f in notes_dir.iterdir():
                if not f.is_file() or not f.name.endswith(".md"):
                    continue
                fm, _ = parse_frontmatter(f)
                consumed_val = fm.get("consumed")
                if consumed_val is False or str(consumed_val).lower() == "false":
                    unconsumed_count += 1
                    try:
                        stat = f.stat()
                        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                        age = (now - mtime).days
                        if age > oldest_age:
                            oldest_age = age
                            oldest_name = f.stem
                    except OSError:
                        pass

        bottleneck = unconsumed_count >= self.reading_queue_threshold
        return ReadingQueueMetrics(
            total_unconsumed=unconsumed_count,
            oldest_item_name=oldest_name,
            oldest_item_age_days=oldest_age,
            bottleneck_detected=bottleneck,
        )

    def _analyze_inbox(self) -> int:
        """Zlicza nieprzetworzone elementy w Skrzynce."""
        inbox_dir = self.vault_root / self.config.get_room("01").path
        count = 0
        if inbox_dir.is_dir():
            for item in inbox_dir.iterdir():
                if item.name.startswith(".") or item.name == "_archiwum":
                    continue
                count += 1
        return count

    def _build_summary_lines(
        self,
        eject_candidates: List[ProjectRadarItem],
        hibernate_candidates: List[ProjectRadarItem],
        queue: ReadingQueueMetrics,
        inbox_count: int,
    ) -> List[str]:
        """Tworzy zwięzłe, proaktywne linie podsumowania dla koordynatora i człowieka."""
        lines = []

        if eject_candidates:
            names = [f"[{p.id}] {p.name}" for p in eject_candidates]
            reasons = "; ".join(f"{p.id}: {p.eject_reasons[0]}" for p in eject_candidates[:2])
            lines.append(
                f"🚀 **Gotowość do odłączenia (Eject):** {', '.join(names)} ({reasons}). "
                f"Zalecane wyekstrahowanie do osobnej instancji satelitarnej dla ochrony okna kontekstowego."
            )

        if hibernate_candidates:
            names = [f"[{p.id}] {p.name} ({p.idle_days} dni)" for p in hibernate_candidates]
            lines.append(
                f"⏸️ **Projekty uśpione (KISS):** {', '.join(names)}. "
                f"Zalecane przeniesienie do archiwum zgodnie z zasadą czystego stołu."
            )

        if queue.bottleneck_detected:
            lines.append(
                f"📚 **Kolejka lektur (`consumed: false`):** {queue.total_unconsumed} nieprzeczytanych notatek "
                f"(najstarsza: '{queue.oldest_item_name}' sprzed {queue.oldest_item_age_days} dni). "
                f"Wstrzymaj nowe zrzuty do czasu zredukowania kolejki."
            )

        if inbox_count > 0:
            lines.append(
                f"📥 **Skrzynka:** {inbox_count} nieprzetworzonych elementów (oczekuje na drenaż do zera)."
            )

        if not lines:
            lines.append("🟢 **Stan idealny:** Portfel projektów zbalansowany, brak zatorów poznawczych.")

        return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministyczny Radar Kondycji i Zasobów Skarbca LAB 3.0 (Zero LLM Tokens)."
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="Ścieżka do korzenia skarbca LAB (domyślnie autodetekcja).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Wyświetla wyłącznie zwięzłe 1-3 linijki podsumowania (format dla sesji koordynatora).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Zwraca wynik w formacie JSON.",
    )
    parser.add_argument(
        "--idle-days",
        type=int,
        default=14,
        help="Próg dni bezczynności dla uśpienia projektu (domyślnie 14).",
    )
    parser.add_argument(
        "--eject-files",
        type=int,
        default=10,
        help="Próg liczby plików dla kwalifikacji projektu do odłączenia (domyślnie 10).",
    )

    args = parser.parse_args()

    vault_root = args.vault_root or find_vault_root(Path(__file__).resolve().parent)
    radar = VaultRadar(
        vault_root=vault_root,
        idle_days_threshold=args.idle_days,
        eject_file_threshold=args.eject_files,
    )
    report = radar.scan()

    if args.json:
        data = {
            "generated_at": report.generated_at,
            "active_projects_count": report.active_projects_count,
            "inbox_unprocessed_count": report.inbox_unprocessed_count,
            "reading_queue": {
                "total_unconsumed": report.reading_queue.total_unconsumed,
                "oldest_item": report.reading_queue.oldest_item_name,
                "oldest_age_days": report.reading_queue.oldest_item_age_days,
                "bottleneck": report.reading_queue.bottleneck_detected,
            },
            "eject_candidates": [
                {
                    "id": p.id,
                    "name": p.name,
                    "file_count": p.file_count,
                    "total_size_kb": p.total_size_bytes // 1024,
                    "has_code": p.has_code,
                    "has_db": p.has_db,
                    "idle_days": p.idle_days,
                    "reasons": p.eject_reasons,
                }
                for p in report.eject_candidates
            ],
            "hibernate_candidates": [
                {
                    "id": p.id,
                    "name": p.name,
                    "idle_days": p.idle_days,
                    "reasons": p.hibernate_reasons,
                }
                for p in report.hibernate_candidates
            ],
            "summary_lines": report.summary_lines,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if args.summary:
        for line in report.summary_lines:
            print(line)
        return 0

    # Domyślny pełny widok konsolowy
    print("=" * 64)
    print("LAB 3.0 • RADAR KONDYCJI I BALANSU ZASOBÓW (THE ONE LAW)")
    print("=" * 64)
    print(f"Skarbiec:            {vault_root}")
    print(f"Data analizy:        {report.generated_at}")
    print(f"Aktywne projekty:    {report.active_projects_count}")
    print(f"Skrzynka wejściowa:  {report.inbox_unprocessed_count} (drain-to-zero: {'🟢 OK' if report.inbox_unprocessed_count == 0 else '🟡 PENDING'})")
    q = report.reading_queue
    print(f"Kolejka lektur:      {q.total_unconsumed} nieprzeczytanych ({'🟡 ZATOR' if q.bottleneck_detected else '🟢 W NORMIE'})")
    if q.oldest_item_name:
        print(f"  Najstarsza notatka: '{q.oldest_item_name}' ({q.oldest_item_age_days} dni temu)")
    print("-" * 64)

    print("🚀 KANDYDACI DO ODŁĄCZENIA DO INSTANCJI SATELITARNEJ (EJECT):")
    if report.eject_candidates:
        for p in report.eject_candidates:
            features = []
            if p.has_code:
                features.append("kod Python/JS")
            if p.has_db:
                features.append("baza SQLite")
            features_str = f" [{', '.join(features)}]" if features else ""
            print(f"  • [{p.id}] {p.name}{features_str}")
            print(f"      Plików: {p.file_count} | Waga: {p.total_size_bytes // 1024} KB | Ostatnia aktywność: {p.idle_days} dni temu")
            for r in p.eject_reasons:
                print(f"      ↳ Powód: {r}")
    else:
        print("  🟢 Brak projektów wymagających natychmiastowego odłączenia.")

    print("-" * 64)
    print("⏸️ KANDYDACI DO UŚPIENIA W ARCHIWUM (ZASADA KISS):")
    if report.hibernate_candidates:
        for p in report.hibernate_candidates:
            print(f"  • [{p.id}] {p.name}")
            print(f"      Bezczynny od: {p.idle_days} dni (próg: {args.idle_days} dni)")
            for r in p.hibernate_reasons:
                print(f"      ↳ Powód: {r}")
    else:
        print("  🟢 Wszystkie aktywne projekty wykazują regularną aktywność.")

    print("=" * 64)
    try:
        from core_config import ConfigManager
        coord_name = ConfigManager().get_agent_name("coordinator")
        coord_header = f"REKOMENDACJE PROAKTYWNE ({coord_name.upper()}):"
    except Exception:
        coord_header = "REKOMENDACJE PROAKTYWNE KOORDYNATORA:"
    print(coord_header)
    for line in report.summary_lines:
        print(f"  {line}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
