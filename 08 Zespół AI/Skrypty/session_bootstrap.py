#!/usr/bin/env python3
"""session_bootstrap.py - Deterministyczny bootstrap sesji roboczej dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (argparse, datetime, json, os, pathlib, sys).
- Integracja z core_config.ConfigManager.
- Zero zewnętrznych zależności pip.

Odpowiada za procedurę otwarcia sesji w jednym, atomowym kroku:
1. Inspekcja jakościowa (QualityChecker z lab_core.quality).
2. Skanowanie pokoju '01 Skrzynka' (drain-to-zero count).
3. Automatyczne rolowanie Agendy Roboczej Dnia (AgendaRollover z rollover_agenda).
4. Przegląd portfela projektów (ProjectManager z projects).
5. Wykrywanie blokerów krytycznych (Circuit Breaker / Ticket dla Maksa).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Blokada tworzenia folderów __pycache__
sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from core_config import ConfigError, ConfigManager, find_vault_root
from check_quality import QualityChecker
from rollover_agenda import AgendaRollover
from projects import ProjectManager

IGNORED_INBOX_FILES = {".DS_Store", "desktop.ini", "Thumbs.db"}
COORDINATOR_ROLE_PREFIX = "Koordynator"


class SessionBootstrap:
    """Automatyzuje pełną procedurę otwarcia sesji LAB 3.0."""

    def __init__(self, vault_root: Optional[Union[str, Path]] = None, config_path: Optional[Union[str, Path]] = None) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root(_script_dir)
        cfg_p = Path(config_path).resolve() if config_path else None
        try:
            self.config = ConfigManager(config_path=cfg_p, vault_root=self.vault_root)
            self.init_error = None
        except Exception as ex:
            self.config = None
            self.init_error = str(ex)

    def read_coordinator_contract(self) -> str:
        """Zwraca treść kontraktu Koordynatora (Role/Koordynator*/AGENT.md) lub pusty ciąg, gdy brak."""
        if self.config is None:
            return ""
        try:
            roles_dir = self.vault_root / self.config.get_room_by_role("governance-and-engine").path / "Role"
        except Exception:
            roles_dir = self.vault_root / "08 Zespół AI" / "Role"
        if not roles_dir.is_dir():
            return ""
        import unicodedata
        for d in sorted(roles_dir.iterdir()):
            if d.is_dir() and unicodedata.normalize("NFC", d.name).startswith(COORDINATOR_ROLE_PREFIX):
                f = d / "AGENT.md"
                if f.is_file():
                    try:
                        return f.read_text(encoding="utf-8")
                    except OSError:
                        return ""
        return ""

    def read_telemetry_statusline(self) -> str:
        """Zwraca jednoliniowy statusline telemetrii tokenów z lab_core.telemetry (fail-safe)."""
        try:
            from lab_core.telemetry import find_latest_transcript, format_statusline, parse_telemetry

            transcript = find_latest_transcript()
            metrics = parse_telemetry(transcript)
            return format_statusline(metrics)
        except Exception:
            return "🧠 0 / 250k (0.0%) 🟢"

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """Uruchamia wszystkie kroki bootstrapu sesji i zwraca zwięzły raport stanu."""
        telemetry_statusline = self.read_telemetry_statusline()
        if self.init_error or self.config is None:
            return {
                "status": "BROKEN",
                "quality_status": "BROKEN",
                "agenda_file": "Brak konfiguracji",
                "inbox_count": 0,
                "inbox_items": [],
                "projects_summary": {"active_count": 0, "active_list": [], "total_count": 0},
                "telemetry_statusline": telemetry_statusline,
                "ticket": {
                    "component": "config",
                    "error": self.init_error or "Błąd konfiguracji",
                    "path": "lab.config.toml",
                    "recommended_action": "Naprawić konfigurację lab.config.toml (skierować do Maksa)",
                },
            }
        # 1. Rolowanie agendy roboczej dnia
        rollover = AgendaRollover(vault_root=self.vault_root)
        today = datetime.date.today()
        agenda_name = f"{today.isoformat()} – Agenda Robocza.md"
        try:
            ws_room = self.config.get_room_by_role("daily-workbench")
            ws_rel = Path(ws_room.path)
        except Exception:
            ws_rel = Path("02 Warsztat")

        agenda_rel = str(ws_rel / agenda_name)
        try:
            rollover.run_rollover(target_date=today, dry_run=dry_run)
        except Exception as ex:
            agenda_rel = f"Błąd rolowania: {ex}"

        # 2. Skanowanie pokoju Skrzynka
        try:
            inbox_room = self.config.get_room_by_role("ingestion")
            inbox_dir = self.vault_root / inbox_room.path
        except Exception:
            inbox_dir = self.vault_root / "01 Skrzynka"

        inbox_items: List[str] = []
        if inbox_dir.is_dir():
            for item in sorted(inbox_dir.iterdir()):
                if item.is_file() and not item.name.startswith(".") and item.name not in IGNORED_INBOX_FILES:
                    inbox_items.append(item.name)

        inbox_count = len(inbox_items)

        # 3. Przegląd portfela projektów
        try:
            pm = ProjectManager(vault_root=self.vault_root)
            all_projects = pm.scan_all_projects()
            active_projects = [p for p in all_projects if p.status == "active"]
            projects_summary = {
                "active_count": len(active_projects),
                "active_list": [f"{p.id} {p.name}" for p in active_projects],
                "total_count": len(all_projects),
            }
        except Exception as ex:
            projects_summary = {
                "active_count": 0,
                "active_list": [],
                "total_count": 0,
                "error": str(ex),
            }

        # 4. Inspekcja jakościowa
        try:
            cfg_p = getattr(self.config, "config_path", None)
            checker = QualityChecker(vault_root=self.vault_root, config_path=cfg_p)
            report = checker.check_all()
            raw_q_status = report.status.lower()
            quality_status = raw_q_status.upper()
            errors = [f for f in report.findings if f.level == "error"]
            unprocessed_thought_blocks = int(report.stats.get("unprocessed_thought_blocks", 0))
        except Exception as ex:
            raw_q_status = "broken"
            quality_status = "BROKEN"
            errors = []
            unprocessed_thought_blocks = 0

        # 5. Wyznaczenie statusu nadrzędnego i biletu (Circuit Breaker)
        ticket: Optional[Dict[str, str]] = None
        if raw_q_status == "broken":
            status = "BROKEN"
            if errors:
                first_err = errors[0]
                ticket = {
                    "component": first_err.category or "quality_gate",
                    "error": first_err.message,
                    "path": first_err.path or "",
                    "recommended_action": f"Skierować naprawę do Maksa (Inżynier) dla komponentu {first_err.category}: {first_err.path or ''}".strip(),
                }
            else:
                ticket = {
                    "component": "quality_gate",
                    "error": "Status jakości raportu: BROKEN",
                    "path": "",
                    "recommended_action": "Skierować audyt i naprawę do Maksa (Inżynier)",
                }
        elif raw_q_status == "attention" or inbox_count > 0:
            status = "ATTENTION"
        else:
            status = "READY"

        return {
            "status": status,
            "coordinator_contract": self.read_coordinator_contract(),
            "telemetry_statusline": telemetry_statusline,
            "quality_status": quality_status,
            "agenda_file": agenda_rel,
            "inbox_count": inbox_count,
            "inbox_items": inbox_items,
            "unprocessed_thought_blocks": unprocessed_thought_blocks,
            "projects_summary": projects_summary,
            "ticket": ticket,
        }


def format_text_output(data: Dict[str, Any]) -> str:
    """Formatuje raport dla wyjścia terminalowego / Raycast."""
    status = data["status"]
    status_icon = "🟢" if status == "READY" else ("🟡" if status == "ATTENTION" else "🔴")
    q_icon = "🟢" if data["quality_status"] == "OK" else ("🟡" if data["quality_status"] == "ATTENTION" else "🔴")
    inbox_icon = "🟢" if data["inbox_count"] == 0 else "🔴"

    lines = [
        "=" * 64,
        f"SESSION BOOTSTRAP - STATUS: {status_icon} {status}",
        "=" * 64,
        f"  - Telemetria:         {data.get('telemetry_statusline', '🧠 0 / 250k (0.0%) 🟢')}",
        f"  - Jakość skarbca:     {q_icon} {data['quality_status']}",
        f"  - Agenda Robocza:     {data['agenda_file']}",
        f"  - Skrzynka (ingest):  {inbox_icon} {data['inbox_count']} oczekuje na drenaż",
    ]
    unproc = int(data.get("unprocessed_thought_blocks", 0))
    if unproc > 0:
        lines.append(f"  - Myśli w Agendzie:   🟡 {unproc} bloków w '## ✍️' do streszczenia przez Edytę")

    p_summary = data.get("projects_summary", {})
    act_cnt = p_summary.get("active_count", 0)
    act_list = p_summary.get("active_list", [])
    if act_list:
        lines.append(f"  - Projekty w toku:    {act_cnt} aktywne ({', '.join(act_list)})")
    else:
        lines.append(f"  - Projekty w toku:    {act_cnt} aktywne")

    contract = data.get("coordinator_contract")
    if contract:
        lines.extend(["-" * 64, "KONTRAKT KOORDYNATORA (Role/Koordynator/AGENT.md):", "-" * 64, contract.strip()])

    if data.get("ticket"):
        t = data["ticket"]
        lines.extend([
            "-" * 64,
            "🚨 WYKRYTO BLOKER KRYTYCZNY (CIRCUIT BREAKER TRIGGERED):",
            f"  Komponent:       {t.get('component')}",
            f"  Ścieżka:         {t.get('path')}",
            f"  Błąd:            {t.get('error')}",
            f"  Zalecana akcja:  {t.get('recommended_action')}",
            "=" * 64,
        ])
    else:
        lines.append("-" * 64)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministyczny bootstrap sesji (LAB 3.0)")
    parser.add_argument("--vault-root", help="Ścieżka do korzenia skarbca LAB")
    parser.add_argument("--config", help="Ścieżka do lab.config.toml (opcjonalnie)")
    parser.add_argument("--json", action="store_true", help="Zwróć wynik jako zwarty obiekt JSON")
    parser.add_argument("--dry-run", action="store_true", help="Tryb symulacji (bez modyfikacji dysku)")
    args = parser.parse_args()

    bootstrap = SessionBootstrap(vault_root=args.vault_root, config_path=args.config)
    result = bootstrap.run(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text_output(result))

    return 1 if result["status"] == "BROKEN" else 0


if __name__ == "__main__":
    sys.exit(main())
