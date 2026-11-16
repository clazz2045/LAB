"""lab_core.quality.inspectors.subagents - Inspektor polityki subagentów i integralności ról Zespołu AI
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional, Set

sys.dont_write_bytecode = True

from lab_core.config import ConfigError, ConfigValidationError
from lab_core.constants import (
    AGENTS_CONTRACT_FILE,
    IGNORED_DIRS,
    IGNORED_FILES,
    ROLES_DIRNAME,
    RULE8_REQUIRED_PHRASES,
)
from lab_core.markdown import parse_frontmatter
from lab_core.quality.context import QualityContext, QualityInspector

MIN_SOUL_BYTES = 50
MASTER_DB_NAME = "lab_master.db"
SESSION_LOGS_DIRNAME = "Dzienniki Sesji"
CODE_OR_UI_EXTENSIONS: Set[str] = {".py", ".sql", ".swift", ".html", ".css"}
DOMAIN_A_AGENTS: Set[str] = {
    "maks",
    "kinga",
    "maja",
    "engineer",
    "db_architect",
    "database_code",
    "designer",
    "silas",
    "felix",
}
DOMAIN_B_AGENTS: Set[str] = {
    "klara",
    "edyta",
    "researcher",
    "knowledge_processor",
    "knowledge",
    "pax",
    "penn",
}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def find_role_dir(roles_dir: Path, agent_name: str) -> Optional[Path]:
    """Znajduje folder roli '<Funkcja> - <Imię>' (porównanie NFC, bez globbingu)."""
    suffix = _nfc(f" - {agent_name}")
    for child in sorted(roles_dir.iterdir()):
        if child.is_dir() and _nfc(child.name).endswith(suffix):
            return child
    return None


class SubagentsPolicyInspector(QualityInspector):
    """Weryfikuje kompletność ról Zespołu AI, integralność kontraktów AGENT.md oraz Regułę 8."""
    name: str = "subagents"

    def inspect(self, ctx: QualityContext) -> None:
        try:
            ai_room = ctx.config.get_room_by_role("governance-and-engine")
        except ConfigValidationError:
            ctx.add_finding(
                level="error",
                category="subagents",
                message="Brak pokoju o roli rdzenia 'governance-and-engine' w lab.config.toml",
            )
            return

        self._check_rule8(ctx)

        roles_dir = ctx.vault_root / ai_room.path / ROLES_DIRNAME
        rel_roles = str(roles_dir.relative_to(ctx.vault_root))
        if not roles_dir.is_dir():
            ctx.add_finding(
                level="error",
                category="subagents",
                path=rel_roles,
                room=ai_room.id,
                message=f"Brak katalogu ról Zespołu AI pod ścieżką '{rel_roles}'",
            )
            return

        team = ctx.config.ai_team
        expected: List[str] = [
            team.coordinator.assigned_name,
            team.governance_qa.assigned_name,
            *[spec.assigned_name for spec in team.specialists],
        ]
        ctx.stats["expected_subagent_roles"] = len(expected)

        verified = 0
        for agent_name in expected:
            role_path = find_role_dir(roles_dir, agent_name)
            if role_path is None:
                ctx.add_finding(
                    level="error",
                    category="subagents",
                    path=rel_roles,
                    room=ai_room.id,
                    message=f"Brak wymaganej roli Zespołu AI: '{agent_name}' (oczekiwany folder '<Funkcja> - {agent_name}')",
                )
                continue
            ok_agent = self._check_agent_contract(ctx, role_path, agent_name, ai_room.id)
            if ok_agent:
                verified += 1

        ctx.stats["verified_subagent_roles"] = verified
        ctx.stats["subagent_policy_active"] = verified == len(expected)
        self._check_session_delegation_discipline(ctx)

    # --- pomocnicze -------------------------------------------------------

    def _check_rule8(self, ctx: QualityContext) -> None:
        contract = ctx.vault_root / AGENTS_CONTRACT_FILE
        if not contract.is_file():
            ctx.add_finding(
                level="error",
                category="subagents",
                path=AGENTS_CONTRACT_FILE,
                message=f"Brak głównego kontraktu {AGENTS_CONTRACT_FILE} (Reguła 8 nie może zostać zweryfikowana)",
            )
            return
        try:
            text = contract.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            ctx.add_finding(
                level="error",
                category="subagents",
                path=AGENTS_CONTRACT_FILE,
                message=f"Nie można odczytać {AGENTS_CONTRACT_FILE}: {exc}",
            )
            return
        text = _nfc(text)
        missing = [p for p in RULE8_REQUIRED_PHRASES if _nfc(p) not in text]
        if missing:
            ctx.add_finding(
                level="error",
                category="subagents",
                path=AGENTS_CONTRACT_FILE,
                message=f"{AGENTS_CONTRACT_FILE} nie zawiera pełnej Reguły 8 (brak fraz: {'; '.join(missing)})",
            )

    def _check_agent_contract(self, ctx: QualityContext, role_path: Path, agent_name: str, room_id: str) -> bool:
        agent_file = role_path / "AGENT.md"
        rel = str(agent_file.relative_to(ctx.vault_root))
        if not agent_file.is_file():
            ctx.add_finding("error", "subagents", f"Brak pliku kontraktu AGENT.md dla roli '{agent_name}'", rel, room_id)
            return False
        try:
            raw_text = agent_file.read_text(encoding="utf-8")
            frontmatter, _ = parse_frontmatter(raw_text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            ctx.add_finding("error", "subagents", f"Błąd odczytu AGENT.md w roli '{agent_name}': {exc}", rel, room_id)
            return False
        if not frontmatter or frontmatter.get("type") != "agent":
            ctx.add_finding(
                "error", "subagents",
                f"Plik AGENT.md w roli '{agent_name}' musi zawierać frontmatter YAML z type: agent",
                rel, room_id,
            )
            return False
        missing = [k for k in ("role", "routing_description") if not str(frontmatter.get(k, "")).strip()]
        if missing:
            ctx.add_finding(
                "error", "subagents",
                f"Plik AGENT.md w roli '{agent_name}' nie ma wymaganych pól: {', '.join(missing)}",
                rel, room_id,
            )
            return False

        # Reguła czystości kontraktu roli (KISS & Anti-Bloat)
        text_norm = _nfc(raw_text)
        if not re.search(r"(?im)^#{1,6}\s+.*(allowlist|biała\s+lista)", text_norm):
            ctx.add_finding(
                "warning", "subagents",
                f"Plik AGENT.md w roli '{agent_name}' nie zawiera sekcji Białej Listy (wymagany nagłówek 'Allowlist' lub 'Biała Lista')",
                rel, room_id,
            )

        lines = text_norm.splitlines()
        # Wyłączenie wierszy specyficznych tabel narzędziowych z limitu 50 linii
        non_table_lines = [l for l in lines if not l.strip().startswith("|")]
        effective_line_count = len(non_table_lines)
        if effective_line_count > 50:
            ctx.add_finding(
                "warning", "subagents",
                f"Plik AGENT.md w roli '{agent_name}' przekracza limit 50 linii ({effective_line_count} linii poza tabelami; zalecana redukcja do mikro-kontraktu KISS)",
                rel, room_id,
            )

        return True

    def _check_session_delegation_discipline(self, ctx: QualityContext) -> None:
        """Weryfikuje dyscyplinę delegacji do subagentów (Reguła 8) w bieżącej sesji.

        Kontrola uruchamia się, gdy w skarbcu istnieją zarówno baza 07 Bazy Danych/lab_master.db
        (z tabelą subagent_dispatches), jak i co najmniej jeden dziennik sesji w Dzienniki Sesji/.
        """
        try:
            ai_room = ctx.config.get_room_by_role("governance-and-engine")
            db_room = ctx.config.get_room_by_role("sqlite-relational")
        except ConfigError:
            return

        db_path = ctx.vault_root / db_room.path / MASTER_DB_NAME
        logs_dir = ctx.vault_root / ai_room.path / SESSION_LOGS_DIRNAME
        if not db_path.is_file() or not logs_dir.is_dir():
            return

        session_logs: List[Path] = []
        for root_str, dirs, files in os.walk(logs_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for fname in files:
                if fname.endswith(".md") and not fname.startswith(".") and fname not in IGNORED_FILES:
                    session_logs.append(Path(root_str) / fname)

        if not session_logs:
            return

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row_tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_dispatches';"
            ).fetchone()
            if not row_tbl:
                return
            dispatches = conn.execute(
                "SELECT id, agent_name, task_type, status, dispatch_time FROM subagent_dispatches WHERE status = 'accepted' ORDER BY id ASC;"
            ).fetchall()
        except sqlite3.Error:
            return
        finally:
            conn.close()

        last_checkpoint_mtime = max(p.stat().st_mtime for p in session_logs)
        threshold_mtime = last_checkpoint_mtime + 2.0
        last_checkpoint_iso = datetime.fromtimestamp(last_checkpoint_mtime, tz=timezone.utc).isoformat()

        session_dispatches = []
        for row in dispatches:
            ts_str = str(row["dispatch_time"] or "")
            ts_val = self._parse_iso_timestamp(ts_str)
            is_after = (ts_val is not None and ts_val >= last_checkpoint_mtime) or (ts_str >= last_checkpoint_iso)
            if is_after:
                session_dispatches.append((ts_val if ts_val is not None else 0.0, int(row["id"]), row))

        session_dispatches.sort(key=lambda item: (item[0], item[1]))
        self._check_maks_code_audit_sequence(ctx, [item[2] for item in session_dispatches], ai_room.id, db_path)

        modified_code_ui = self._find_modified_code_or_ui(ctx, threshold_mtime)
        modified_knowledge = self._find_modified_knowledge_or_research(ctx, threshold_mtime)

        ctx.stats["session_modified_code_ui"] = len(modified_code_ui)
        ctx.stats["session_modified_knowledge"] = len(modified_knowledge)

        if not modified_code_ui and not modified_knowledge:
            return

        accepted_agents: Set[str] = set()
        for _, _, row in session_dispatches:
            if row["agent_name"]:
                accepted_agents.add(_nfc(str(row["agent_name"]).strip().lower()))

        domain_a_allowed = set(DOMAIN_A_AGENTS)
        for rk in ("engineer", "database_code", "designer"):
            try:
                domain_a_allowed.add(_nfc(ctx.config.get_agent_name(rk).strip().lower()))
            except Exception:
                pass

        domain_b_allowed = set(DOMAIN_B_AGENTS)
        for rk in ("researcher", "knowledge"):
            try:
                domain_b_allowed.add(_nfc(ctx.config.get_agent_name(rk).strip().lower()))
            except Exception:
                pass

        if modified_code_ui and not (accepted_agents & domain_a_allowed):
            files_str = ", ".join(sorted(modified_code_ui))
            ctx.add_finding(
                level="error",
                category="subagents",
                path=sorted(modified_code_ui)[0],
                room=ai_room.id,
                message=(
                    f"Naruszenie Reguły 8 (Brak delegacji do subagenta): W bieżącej sesji zmodyfikowano kod/interfejs "
                    f"({files_str}), ale w lab_master.db brak zatwierdzonego destylatu subagenta technicznego "
                    f"(Maks/Kinga/Maja) przez subagent_gate.py."
                ),
            )

        if modified_knowledge and not (accepted_agents & domain_b_allowed):
            files_str = ", ".join(sorted(modified_knowledge))
            ctx.add_finding(
                level="error",
                category="subagents",
                path=sorted(modified_knowledge)[0],
                room=ai_room.id,
                message=(
                    f"Naruszenie Reguły 8 (Brak delegacji do subagenta): W bieżącej sesji utworzono/zmodyfikowano destylat "
                    f"wiedzy lub badanie ({files_str}), ale w lab_master.db brak zatwierdzonego destylatu subagenta wiedzy "
                    f"(Klara/Edyta) przez subagent_gate.py."
                ),
            )

    def _check_maks_code_audit_sequence(
        self,
        ctx: QualityContext,
        session_dispatches: List[sqlite3.Row],
        room_id: str,
        db_path: Path,
    ) -> None:
        """Weryfikuje bramkę sekwencyjną Maks/code -> Ada/audit (ACCEPTED) w bieżącej sesji."""
        try:
            maks_name = _nfc(ctx.config.get_agent_name("engineer").strip().lower())
        except Exception:
            maks_name = "maks"
        try:
            ada_name = _nfc(ctx.config.get_agent_name("governance_qa").strip().lower())
        except Exception:
            ada_name = "ada"

        pending_maks_code = False
        for row in session_dispatches:
            agent = _nfc(str(row["agent_name"] or "").strip().lower())
            task_type = str(row["task_type"] or "").strip().lower()
            if agent == maks_name and task_type == "code":
                pending_maks_code = True
            elif agent == ada_name and task_type == "audit":
                pending_maks_code = False

        ctx.stats["pending_maks_code_audit"] = pending_maks_code
        if pending_maks_code:
            ctx.add_finding(
                level="error",
                category="subagents",
                path=str(db_path.relative_to(ctx.vault_root)),
                room=room_id,
                message=(
                    "Naruszenie Reguły 8 (Brak audytu po implementacji): W bieżącej sesji zarejestrowano wpis "
                    "Maks/code w lab_master.db, ale brak późniejszego zatwierdzonego audytu Ada/audit "
                    "(status ACCEPTED) przez subagent_gate.py."
                ),
            )

    def _find_modified_code_or_ui(self, ctx: QualityContext, threshold_mtime: float) -> List[str]:
        """Domena A (code_or_ui): *.py, *.sql, *.swift, *.html, *.css w Skrypty/, Bazy Danych/ lub Projekty/.../Produkt/."""
        modified: List[str] = []
        scan_roots: List[Path] = []

        try:
            ai_room = ctx.config.get_room_by_role("governance-and-engine")
            scan_roots.append(ctx.vault_root / ai_room.path / "Skrypty")
        except ConfigError:
            pass

        try:
            db_room = ctx.config.get_room_by_role("sqlite-relational")
            scan_roots.append(ctx.vault_root / db_room.path)
        except ConfigError:
            pass

        for base_dir in scan_roots:
            if not base_dir.is_dir():
                continue
            for root_str, dirs, files in os.walk(base_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for fname in files:
                    if fname.startswith(".") or fname in IGNORED_FILES:
                        continue
                    fpath = Path(root_str) / fname
                    if fpath.suffix.lower() in CODE_OR_UI_EXTENSIONS:
                        try:
                            if fpath.stat().st_mtime > threshold_mtime:
                                modified.append(str(fpath.relative_to(ctx.vault_root)))
                        except OSError:
                            pass

        try:
            proj_room = ctx.config.get_room_by_role("active-projects")
            proj_dir = ctx.vault_root / proj_room.path
        except ConfigError:
            proj_dir = None

        if proj_dir and proj_dir.is_dir():
            for root_str, dirs, files in os.walk(proj_dir):
                dirs[:] = [
                    d for d in dirs
                    if d not in (IGNORED_DIRS - {"_archiwum"}) and not d.startswith(".")
                ]
                root_path = Path(root_str)
                try:
                    rel_parts = root_path.relative_to(proj_dir).parts
                except ValueError:
                    continue
                if "Produkt" not in rel_parts:
                    continue
                for fname in files:
                    if fname.startswith(".") or fname in IGNORED_FILES:
                        continue
                    fpath = root_path / fname
                    if fpath.suffix.lower() in CODE_OR_UI_EXTENSIONS:
                        try:
                            if fpath.stat().st_mtime > threshold_mtime:
                                modified.append(str(fpath.relative_to(ctx.vault_root)))
                        except OSError:
                            pass

        return sorted(set(modified))

    def _find_modified_knowledge_or_research(self, ctx: QualityContext, threshold_mtime: float) -> List[str]:
        """Domena B (knowledge_or_research): *.md w Badania/, Zettelkasten/ oraz 06 Zasoby/Notatki/ ze stage: distilled."""
        modified: List[str] = []

        try:
            know_room = ctx.config.get_room_by_role("atomic-synthesis-and-research")
            know_dir = ctx.vault_root / know_room.path
        except ConfigError:
            know_dir = None

        if know_dir and know_dir.is_dir():
            for subdir_name in ("Badania", "Inkubator", "Refleksje"):
                target_dir = know_dir / subdir_name
                if not target_dir.is_dir():
                    continue
                for root_str, dirs, files in os.walk(target_dir):
                    dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                    for fname in files:
                        if not fname.endswith(".md") or fname.startswith(".") or fname in IGNORED_FILES or fname.lower() == "readme.md":
                            continue
                        fpath = Path(root_str) / fname
                        try:
                            if fpath.stat().st_mtime > threshold_mtime:
                                modified.append(str(fpath.relative_to(ctx.vault_root)))
                        except OSError:
                            pass

        try:
            assets_room = ctx.config.get_room_by_role("reference-and-assets")
            notes_dir = ctx.vault_root / assets_room.path / "Notatki"
        except ConfigError:
            notes_dir = None

        if notes_dir and notes_dir.is_dir():
            for root_str, dirs, files in os.walk(notes_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for fname in files:
                    if not fname.endswith(".md") or fname.startswith(".") or fname in IGNORED_FILES or fname.lower() == "readme.md":
                        continue
                    fpath = Path(root_str) / fname
                    try:
                        if fpath.stat().st_mtime <= threshold_mtime:
                            continue
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    fm, _ = parse_frontmatter(content)
                    stage_val = str(fm.get("stage", "")).strip().lower() if fm else ""
                    if not stage_val:
                        m = re.search(r"^\s*stage:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?", content, re.MULTILINE | re.IGNORECASE)
                        stage_val = m.group(1).lower() if m else ""
                    consumed_raw = fm.get("consumed") if fm else None
                    is_consumed = consumed_raw is True or str(consumed_raw).strip().lower() == "true"
                    if consumed_raw is None:
                        mc = re.search(r"^\s*consumed:\s*['\"]?(true|false)['\"]?", content, re.MULTILINE | re.IGNORECASE)
                        is_consumed = bool(mc and mc.group(1).lower() == "true")
                    if stage_val == "distilled" and not is_consumed:
                        modified.append(str(fpath.relative_to(ctx.vault_root)))

        return sorted(set(modified))

    @staticmethod
    def _parse_iso_timestamp(ts_str: str) -> Optional[float]:
        if not ts_str:
            return None
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
