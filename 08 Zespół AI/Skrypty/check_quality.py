#!/usr/bin/env python3
"""
check_quality.py - Deterministyczny sprawdzacz jakości i integralności LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (Zero-Pip).
Zasady: SOLID, Clean Code, Pythonic OOP (Protokół QualityInspector).

Zdekomponowany w ramach Inicjatywy Refaktoringu Silnika (Modularne Inspektory Jakości).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Wymuszenie braku generowania folderów __pycache__ w iCloud
sys.dont_write_bytecode = True

# Centralny bootstrap importu przez lab_core
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core import (
    IGNORED_DIRS,
    IGNORED_FILES,
    ConfigError,
    ConfigManager,
    find_vault_root,
)
from lab_core.quality import (
    AssetsAndReferencesInspector,
    EnginePurityInspector,
    Finding,
    GovernanceStyleInspector,
    InboxInspector,
    ManagementFilesInspector,
    NotesAndLinksInspector,
    PortabilityInspector,
    ProjectsAndProductsInspector,
    QualityContext,
    QualityInspector,
    QualityReport,
    ReadmesInspector,
    RoomsInspector,
    SubagentsPolicyInspector,
    WorkshopAndThoughtsInspector,
)


class QualityChecker:
    """Główny koordynator jakości skarbca LAB 3.0 (Framework PZW).

    Działa w oparciu o wzorzec Agregatora / Kompozycji (Composition over Inheritance).
    Deleguje poszczególne domeny audytu do wyspecjalizowanych inspektorów implementujących
    protokół QualityInspector.
    """

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root()
        self.config = ConfigManager(config_path=config_path, vault_root=self.vault_root)
        self.findings: List[Finding] = []
        self.stats: Dict[str, Any] = {}

        # Rejestr polimorficznych inspektorów jakości
        self.inspectors: List[QualityInspector] = [
            RoomsInspector(),
            ReadmesInspector(),
            ManagementFilesInspector(),
            InboxInspector(),
            NotesAndLinksInspector(),
            ProjectsAndProductsInspector(),
            WorkshopAndThoughtsInspector(),
            AssetsAndReferencesInspector(),
            SubagentsPolicyInspector(),
            GovernanceStyleInspector(),
            PortabilityInspector(),
            EnginePurityInspector(),
        ]

    def check_all(self, strict_checkpoint: bool = False, audit_legacy: bool = False) -> QualityReport:
        """Wykonuje pełną, modularną analizę jakościową skarbca LAB 2."""
        self.findings = []
        self.stats = {
            "total_rooms": len(self.config.rooms),
            "verified_rooms": 0,
            "foreign_readmes": 0,
            "management_files_present": 0,
            "inbox_items": 0,
            "total_notes": 0,
            "frontmatter_errors": 0,
            "dangling_links": 0,
            "unauthorized_release_dirs": 0,
            "invalid_produkt_files": 0,
            "human_thoughts_protection_active": False,
            "raw_captures_in_assets": 0,
            "reference_notes": 0,
            "reference_notes_valid": 0,
            "reference_notes_errors": 0,
            "legacy_notes_count": 0,
        }

        ctx = QualityContext(
            vault_root=self.vault_root,
            config=self.config,
            findings=self.findings,
            stats=self.stats,
            strict_checkpoint=strict_checkpoint,
            audit_legacy=audit_legacy,
        )

        for inspector in self.inspectors:
            inspector.inspect(ctx)

        # Ustal ostateczny status
        has_errors = any(f.level == "error" for f in self.findings)
        has_warnings = any(f.level == "warning" for f in self.findings)

        if has_errors:
            status = "broken"
        elif has_warnings:
            status = "attention"
        else:
            status = "ok"

        return QualityReport(
            status=status,
            vault_root=str(self.vault_root),
            instance_name=self.config.system.instance_name,
            rooms_count=len(self.config.rooms),
            findings=self.findings,
            stats=self.stats,
            strict_checkpoint=strict_checkpoint,
            audit_legacy=audit_legacy,
        )

    # ==========================================================================
    # METODY FASADOWE WSTECZNEJ KOMPATYBILNOŚCI
    # ==========================================================================

    def _check_rooms(self) -> None:
        RoomsInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_no_foreign_readmes(self) -> None:
        ReadmesInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_management_files(self) -> None:
        ManagementFilesInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_inbox_state(self) -> None:
        InboxInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_notes_and_links(self) -> None:
        NotesAndLinksInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_projects_and_products(self) -> None:
        ProjectsAndProductsInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_human_thoughts_protection(self) -> None:
        WorkshopAndThoughtsInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))

    def _check_reference_notes_and_assets_hygiene(self) -> None:
        AssetsAndReferencesInspector().inspect(QualityContext(self.vault_root, self.config, self.findings, self.stats))


def check_quality(
    vault_root: Optional[Union[str, Path]] = None,
    config_path: Optional[Union[str, Path]] = None,
    strict_checkpoint: bool = False,
    audit_legacy: bool = False,
) -> QualityReport:
    """Wygodna funkcja modułowa do wywołania sprawdzacza jakości."""
    checker = QualityChecker(vault_root=vault_root, config_path=config_path)
    return checker.check_all(strict_checkpoint=strict_checkpoint, audit_legacy=audit_legacy)


def render_terminal_report(
    report: QualityReport,
    max_findings: int = 10,
    verbose: bool = False,
    summary_only: bool = False,
) -> str:
    """Renderuje czytelny dla człowieka raport tekstowy terminala z ochroną okna kontekstowego (higiena tokenów)."""
    status_symbols = {
        "ok": "🟢 OK",
        "attention": "🟡 ATTENTION",
        "broken": "🔴 BROKEN",
    }
    symbol = status_symbols.get(report.status, report.status.upper())

    lines = [
        "=" * 64,
        f"KISS QUALITY CHECKER - {report.instance_name} (STATUS: {symbol})",
        "=" * 64,
        f"Skarbiec:       {report.vault_root}",
        f"Instancja:      {report.instance_name}",
        f"Pokoje rdzenia: {report.rooms_count} pokoi zdefiniowanych",
        "-" * 64,
        "METRYKI JAKOŚCI:",
        f"  - Fizycznie obecne pokoje:     {report.stats.get('verified_rooms', 0)}/{report.rooms_count}",
        f"  - Obce pliki README.md:        {report.stats.get('foreign_readmes', 0)}",
        f"  - Pliki zarządcze (KISS):      {report.stats.get('management_files_present', 0)}/3 obecne",
        f"  - Elementy w Skrzynce:         {report.stats.get('inbox_items', 0)}",
        f"  - Przeanalizowane notatki:     {report.stats.get('total_notes', 0)}",
        f"  - Błędy frontmattera YAML:     {report.stats.get('frontmatter_errors', 0)}",
        f"  - Martwe linki (dangling):     {report.stats.get('dangling_links', 0)}",
        f"  - Nieautoryzowane foldery wydań: {report.stats.get('unauthorized_release_dirs', 0)}",
        f"  - Nielegalne pliki w Produkt/:   {report.stats.get('invalid_produkt_files', 0)}",
        f"  - Ochrona myśli użytkownika:    {'🟢 AKTYWNA' if report.stats.get('human_thoughts_protection_active') else '🔴 BŁĄD'}",
        f"  - Warsztat (zadania PR/OP):      {'🟢 CZYSTY' if report.stats.get('workshop_hygiene_clean') else '🔴 BŁĄD'}",
        f"  - Czystość Zasobów (SAT):      {report.stats.get('reference_notes_valid', 0)}/{report.stats.get('reference_notes', 0)} notatek ref. zgodnych (surowe zrzuty: {report.stats.get('raw_captures_in_assets', 0)}, nieopracowane stage:raw: {report.stats.get('raw_stage_notes', 0)}, dług historyczny legacy: {report.stats.get('legacy_notes_count', 0)})",
        f"  - Przenośność konfiguracji:    {'🟢 CZYSTA' if report.stats.get('portability_violations', 0) == 0 else '🔴 BŁĄD'}",
        f"  - Czystość jądra (Allowlist):  {'🟢 CZYSTA' if report.stats.get('engine_purity_violations', 0) == 0 else '🔴 BŁĄD'}",
        "-" * 64,
    ]

    if not report.findings:
        lines.append("Wszystkie reguły jakości spełnione wzorowo. Zero zastrzeżeń.")
    else:
        errors = [f for f in report.findings if f.level == "error"]
        warnings = [f for f in report.findings if f.level != "error"]

        lines.append(f"WYKRYTE ZDARZENIA I ANOMALIE (łącznie: {len(report.findings)} | błędy: {len(errors)} | ostrzeżenia: {len(warnings)}):")

        # Błędy krytyczne wyświetlamy ZAWSZE w całości
        if errors:
            lines.append("🚨 BŁĘDY KRYTYCZNE (WYMAGAJĄ NAPRAWY):")
            for f in errors:
                loc = f" ({f.path})" if f.path else ""
                lines.append(f"  [ERROR] [{f.category}]{loc}: {f.message}")

        # Ostrzeżenia prezentujemy zależnie od trybu (ochrona kontekstu / Unix silence)
        if warnings:
            if summary_only:
                # Agregacja ostrzeżeń według kategorii
                categories: Dict[str, int] = {}
                for f in warnings:
                    categories[f.category] = categories.get(f.category, 0) + 1
                lines.append("⚠️ PODSUMOWANIE OSTRZEŻEŃ (wg kategorii):")
                for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                    lines.append(f"  • [{cat}]: {count} wystąpień")
                lines.append("  (Użyj --verbose, aby wyświetlić pełną listę, lub wskaż --log-file)")
            elif verbose:
                lines.append("⚠️ LISTA WSZYSTKICH OSTRZEŻEŃ:")
                for f in warnings:
                    loc = f" ({f.path})" if f.path else ""
                    lines.append(f"  [WARN]  [{f.category}]{loc}: {f.message}")
            else:
                lines.append(f"⚠️ NAJWAŻNIEJSZE OSTRZEŻENIA (pokazano {min(len(warnings), max_findings)} z {len(warnings)}):")
                for f in warnings[:max_findings]:
                    loc = f" ({f.path})" if f.path else ""
                    lines.append(f"  [WARN]  [{f.category}]{loc}: {f.message}")
                if len(warnings) > max_findings:
                    diff = len(warnings) - max_findings
                    lines.append(f"  ... oraz {diff} pozostałych ostrzeżeń (użyj --verbose, aby zobaczyć wszystkie, lub --summary dla skrótu)")

    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprawdzacz jakości środowiska LAB 3.0")
    parser.add_argument("--vault-root", help="Katalog główny skarbca (domyślnie: autodetekcja)")
    parser.add_argument("--config", help="Ścieżka do lab.config.toml (opcjonalnie)")
    parser.add_argument("--json", action="store_true", help="Zwróć wynik jako obiekt JSON")
    parser.add_argument("--strict", action="store_true", help="Zwróć exit code 1 również przy 'attention'")
    parser.add_argument("--summary", action="store_true", help="Wyświetl tylko podsumowanie statystyczne bez listy ostrzeżeń")
    parser.add_argument("--verbose", action="store_true", help="Wyświetl wszystkie wykryte ostrzeżenia bez limitu")
    parser.add_argument("--max-findings", type=int, default=10, help="Maksymalna liczba wyświetlanych ostrzeżeń (domyślnie: 10)")
    parser.add_argument("--log-file", help="Zapisz pełny raport tekstowy do wskazanego pliku")
    parser.add_argument("--audit-legacy", action="store_true", help="Uwzględnij w ostrzeżeniach dług historyczny sprzed 2026-11-13")
    args = parser.parse_args()

    try:
        report = check_quality(vault_root=args.vault_root, config_path=args.config, audit_legacy=args.audit_legacy)
    except ConfigError as ex:
        if args.json:
            print(json.dumps({"status": "broken", "error": str(ex)}, indent=2))
        else:
            print(f"[ERROR] Błąd konfiguracji LAB 3: {ex}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        out_text = render_terminal_report(
            report,
            max_findings=args.max_findings,
            verbose=args.verbose,
            summary_only=args.summary,
        )
        print(out_text)

        if args.log_file:
            log_path = Path(args.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # W pliku logu zawsze zapisujemy pełny raport verbose
            full_log = render_terminal_report(report, verbose=True)
            log_path.write_text(full_log, encoding="utf-8")

    if report.status == "broken":
        return 1
    elif report.status == "attention" and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
