"""lab_core.quality.inspectors.subagents - Inspektor polityki subagentów i integralności ról Zespołu AI
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.dont_write_bytecode = True

from lab_core.compliance import (
    CC_WRITE_TOOLS,
    WRITE_TOOLS,
    _call_args,
    _is_claude_code_step,
    find_session_transcript,
    iter_steps,
    step_tool_calls,
    transcript_files,
    transcript_path_rejection,
)
from lab_core.config import ConfigError, ConfigValidationError
from lab_core.constants import (
    AGENTS_CONTRACT_FILE,
    IGNORED_DIRS,
    IGNORED_FILES,
    ROLES_DIRNAME,
    RULE8_REQUIRED_PHRASES,
)
from lab_core.markdown import parse_frontmatter
from lab_core.pre_tool_guard import _extract_patch_paths
from lab_core.quality.context import QualityContext, QualityInspector

MIN_SOUL_BYTES = 50
MASTER_DB_NAME = "lab_master.db"
SESSION_LOGS_DIRNAME = "Dzienniki Sesji"
CODE_OR_UI_EXTENSIONS: Set[str] = {".py", ".sql", ".swift", ".html", ".css"}
AUDIT_GATE_EXTENSIONS: Set[str] = {".py", ".css", ".sh", ".command", ".bat"}
QUALITY_GATE_PASS_RE = re.compile(r"quality\s+gate\s*:\s*pass\b", re.IGNORECASE)
QUALITY_GATE_FAIL_RE = re.compile(r"quality\s+gate\s*:\s*fail\b", re.IGNORECASE)
ALL_WRITE_TOOLS: Set[str] = {
    *(t.lower() for t in WRITE_TOOLS),
    *(t.lower() for t in CC_WRITE_TOOLS),
    "apply_patch",
    "patch_file",
    "write",
    "edit",
}
WRITE_PATH_ARG_KEYS: Tuple[str, ...] = (
    "Path",
    "path",
    "file_path",
    "FilePath",
    "TargetFile",
    "target_file",
    "notebook_path",
)
PATCH_TOOL_NAMES: Set[str] = {"apply_patch", "patch_file"}
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
        # Spójność modelu Read-Only: brak przestarzałego zakazu pojedynczego odczytu
        if "nie ma prawa czytać plików" in text.lower():
            ctx.add_finding(
                level="warning",
                category="subagents",
                path=AGENTS_CONTRACT_FILE,
                message=f"{AGENTS_CONTRACT_FILE} zawiera przestarzały zakaz odczytu pojedynczych plików niezgodny z modelem Read-Only.",
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

    def _set_neutral_session_audit_stats(self, ctx: QualityContext, active: bool = False) -> None:
        """Ustawia neutralne statystyki audytu sesyjnego (np. brak aktywnej sesji AI)."""
        ctx.stats["session_audit_active"] = active
        ctx.stats["session_modified_code_config"] = 0
        ctx.stats["pending_maks_code_audit"] = False
        ctx.stats["pending_audit_gate"] = False
        ctx.stats["session_modified_code_ui"] = 0
        ctx.stats["session_modified_knowledge"] = 0

    @staticmethod
    def _unwrap_str(val: Any) -> str:
        """Normalizuje wartość argumentu ścieżki (w tym podwójnie zacytowany JSON ze skróconego transkryptu)."""
        if not isinstance(val, str):
            return ""
        s = val.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                unwrapped = json.loads(s)
                if isinstance(unwrapped, str):
                    return unwrapped.strip()
            except (json.JSONDecodeError, ValueError):
                return s[1:-1].strip()
        return s

    @classmethod
    def _resolve_vault_rel_path(cls, raw_path: Any, vault_root_res: Path) -> Optional[str]:
        """Rozwiązuje ścieżkę narzędzia do względnej ścieżki NFC w skarbcu lub None poza skarbcem."""
        cleaned = cls._unwrap_str(raw_path)
        if not cleaned:
            return None
        p = Path(os.path.expanduser(cleaned))
        target = (vault_root_res / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            rel = target.relative_to(vault_root_res)
        except ValueError:
            return None
        rel_posix = rel.as_posix()
        return None if rel_posix in ("", ".") else _nfc(rel_posix)

    @staticmethod
    def _is_subagent_transcript(transcript_path: Path) -> bool:
        """Sprawdza po kroku 0 transkryptu, czy należy on do wyizolowanego subagenta (nie Koordynatora)."""
        files = transcript_files(transcript_path)
        if not files:
            return False
        for step in iter_steps(files[0]):
            if step.get("source") == "SYSTEM":
                content = str(step.get("content", ""))
                if "[Message]" in content and "sender=" in content:
                    return True
            return False
        return False

    def _collect_transcript_modified_paths(
        self,
        transcript_path: Path,
        vault_root: Path,
        min_timestamp: Optional[float] = None,
    ) -> Set[str]:
        """Zbiera z transkryptu sesji znormalizowane ścieżki plików zmodyfikowanych narzędziami zapisu AI."""
        files = transcript_files(transcript_path)
        if not files:
            return set()

        vault_root_res = vault_root.resolve()
        # Antigravity zapisuje created_at z dokładnością do pełnej sekundy (truncation),
        # więc porównujemy z podłogą timestampu checkpointu.
        min_second = float(int(min_timestamp)) if min_timestamp is not None else None
        modified: Set[str] = set()

        for step in iter_steps(files[0]):
            if not _is_claude_code_step(step) and step.get("source") in ("USER_EXPLICIT", "SYSTEM"):
                continue
            if min_second is not None:
                ts_raw = step.get("created_at") or step.get("timestamp")
                if ts_raw is not None:
                    ts_val = self._parse_iso_timestamp(str(ts_raw))
                    if ts_val is not None and ts_val < min_second:
                        continue
            for tc in step_tool_calls(step):
                tool_name = str(tc.get("name") or tc.get("tool_name") or "").strip().lower()
                if tool_name not in ALL_WRITE_TOOLS:
                    continue
                args = _call_args(tc)
                raw_candidates: List[Any] = [args.get(k) for k in WRITE_PATH_ARG_KEYS if args.get(k)]
                if tool_name in PATCH_TOOL_NAMES or any(k in args for k in ("patch", "input", "diff")):
                    patch_text = str(args.get("patch") or args.get("input") or args.get("diff") or "")
                    if patch_text:
                        raw_candidates.extend(_extract_patch_paths(patch_text))
                for raw_p in raw_candidates:
                    rel = self._resolve_vault_rel_path(raw_p, vault_root_res)
                    if rel:
                        modified.add(rel)

        return modified

    def _collect_engineering_subagent_paths(
        self,
        ctx: QualityContext,
        session_dispatches: List[sqlite3.Row],
        engineering_agents: Set[str],
        min_timestamp: Optional[float],
    ) -> Set[str]:
        """Zbiera ścieżki modyfikacji z transkryptów subagentów technicznych powiązanych przez subagent_id."""
        paths: Set[str] = set()
        seen_ids: Set[str] = set()
        brain_root = Path.home() / ".gemini" / "antigravity" / "brain"

        for row in session_dispatches:
            agent = _nfc(str(row["agent_name"] or "").strip().lower())
            task_type = str(row["task_type"] or "").strip().lower()
            if agent not in engineering_agents and task_type != "engineering":
                continue
            raw_sid = row["subagent_id"] if "subagent_id" in row.keys() else None
            sid = str(raw_sid or "").strip()
            if not sid or sid in seen_ids or "/" in sid or "\\" in sid or ".." in sid:
                continue
            seen_ids.add(sid)
            cand = brain_root / sid / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript_path_rejection(cand) is None and cand.is_file() and os.access(cand, os.R_OK):
                paths.update(self._collect_transcript_modified_paths(cand, ctx.vault_root, min_timestamp=min_timestamp))

        return paths

    def _engineering_agent_names(self, ctx: QualityContext) -> Set[str]:
        names: Set[str] = {"maks", "kinga", "maja"}
        for rk in ("engineer", "database_code", "designer"):
            try:
                names.add(_nfc(ctx.config.get_agent_name(rk).strip().lower()))
            except Exception:
                pass
        return names

    def _check_session_delegation_discipline(self, ctx: QualityContext) -> None:
        """Weryfikuje dyscyplinę delegacji do subagentów (Reguła 8) na podstawie transkryptu sesji AI.

        Stan neutralny: brak aktywnego transkryptu sesji AI (np. ręczne uruchomienie check_quality.py
        przez człowieka poza sesją agenta) lub wykonanie wewnątrz sesji subagenta nie zgłasza błędu.
        """
        try:
            ai_room = ctx.config.get_room_by_role("governance-and-engine")
            db_room = ctx.config.get_room_by_role("sqlite-relational")
        except ConfigError:
            self._set_neutral_session_audit_stats(ctx, active=False)
            return

        transcript_path = find_session_transcript()
        if transcript_path is None or self._is_subagent_transcript(transcript_path):
            self._set_neutral_session_audit_stats(ctx, active=False)
            return

        db_path = ctx.vault_root / db_room.path / MASTER_DB_NAME
        if not db_path.is_file():
            self._set_neutral_session_audit_stats(ctx, active=False)
            return

        logs_dir = ctx.vault_root / ai_room.path / SESSION_LOGS_DIRNAME
        session_logs: List[Path] = []
        if logs_dir.is_dir():
            for root_str, dirs, files in os.walk(logs_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for fname in files:
                    if fname.endswith(".md") and not fname.startswith(".") and fname not in IGNORED_FILES:
                        session_logs.append(Path(root_str) / fname)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        assets_registry_map: Dict[str, Tuple[str, str]] = {}
        try:
            row_tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_dispatches';"
            ).fetchone()
            if not row_tbl:
                self._set_neutral_session_audit_stats(ctx, active=False)
                return
            dispatch_cols = {r[1] for r in conn.execute("PRAGMA table_info(subagent_dispatches)").fetchall()}
            sub_col = ", subagent_id" if "subagent_id" in dispatch_cols else ""
            dispatches = conn.execute(
                f"SELECT id, agent_name, task_type, status, dispatch_time, summary_excerpt, note{sub_col} "
                "FROM subagent_dispatches WHERE status = 'accepted' ORDER BY id ASC;"
            ).fetchall()
            assets_tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='assets_registry';"
            ).fetchone()
            if assets_tbl:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(assets_registry)").fetchall()}
                if {"path", "stage", "content_sha256"}.issubset(cols):
                    for arow in conn.execute("SELECT path, stage, content_sha256 FROM assets_registry").fetchall():
                        if arow["path"]:
                            assets_registry_map[_nfc(str(arow["path"]))] = (
                                str(arow["stage"] or "").strip().lower(),
                                str(arow["content_sha256"] or "").strip().lower(),
                            )
        except sqlite3.Error:
            self._set_neutral_session_audit_stats(ctx, active=False)
            return
        finally:
            conn.close()

        last_checkpoint_mtime: Optional[float] = max(p.stat().st_mtime for p in session_logs) if session_logs else None
        last_checkpoint_iso = (
            datetime.fromtimestamp(last_checkpoint_mtime, tz=timezone.utc).isoformat()
            if last_checkpoint_mtime is not None
            else ""
        )

        domain_b_allowed = set(DOMAIN_B_AGENTS)
        for rk in ("researcher", "knowledge"):
            try:
                domain_b_allowed.add(_nfc(ctx.config.get_agent_name(rk).strip().lower()))
            except Exception:
                pass

        session_dispatches = []
        has_prior_knowledge_dispatch = False
        for row in dispatches:
            if last_checkpoint_mtime is None:
                session_dispatches.append((0.0, int(row["id"]), row))
                continue
            ts_str = str(row["dispatch_time"] or "")
            ts_val = self._parse_iso_timestamp(ts_str)
            is_after = (ts_val is not None and ts_val >= last_checkpoint_mtime) or (ts_str >= last_checkpoint_iso)
            if is_after:
                session_dispatches.append((ts_val if ts_val is not None else 0.0, int(row["id"]), row))
            else:
                agent_lower = _nfc(str(row["agent_name"] or "").strip().lower())
                if agent_lower in domain_b_allowed:
                    has_prior_knowledge_dispatch = True

        session_dispatches.sort(key=lambda item: (item[0], item[1]))
        ctx.stats["session_audit_active"] = True

        main_ai_paths = self._collect_transcript_modified_paths(
            transcript_path,
            ctx.vault_root,
            min_timestamp=last_checkpoint_mtime,
        )
        session_dispatch_rows = [item[2] for item in session_dispatches]
        eng_subagent_paths = self._collect_engineering_subagent_paths(
            ctx,
            session_dispatch_rows,
            self._engineering_agent_names(ctx),
            min_timestamp=last_checkpoint_mtime,
        )

        modified_code_config = self._filter_modified_code_or_config(ctx, main_ai_paths | eng_subagent_paths)
        self._check_maks_code_audit_sequence(
            ctx,
            session_dispatch_rows,
            modified_code_config,
            ai_room.id,
            db_path,
        )

        modified_code_ui = self._filter_modified_code_or_ui(ctx, main_ai_paths)
        modified_knowledge = self._filter_modified_knowledge_or_research(
            ctx,
            main_ai_paths,
            last_checkpoint_mtime=last_checkpoint_mtime,
            session_logs=session_logs,
            has_prior_knowledge_dispatch=has_prior_knowledge_dispatch,
            assets_registry_map=assets_registry_map,
        )

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
        modified_code_config: List[str],
        room_id: str,
        db_path: Path,
    ) -> None:
        """Weryfikuje deterministyczną bramkę audytu Ady (Wariant A) w bieżącej sesji.

        Wymaga późniejszego (wyższe id) wpisu Ada/audit z 'Quality Gate: PASS',
        gdy w bieżącej sesji istnieje wpis Maks/Kinga/Maja (lub task_type='engineering')
        ORAZ wykryto zmodyfikowane przez AI pliki kodu/konfiguracji.
        """
        ctx.stats["session_modified_code_config"] = len(modified_code_config)
        if not modified_code_config:
            ctx.stats["pending_maks_code_audit"] = False
            ctx.stats["pending_audit_gate"] = False
            return

        engineering_agents = self._engineering_agent_names(ctx)
        try:
            ada_name = _nfc(ctx.config.get_agent_name("governance_qa").strip().lower())
        except Exception:
            ada_name = "ada"

        last_eng_id = 0
        last_ada_pass_id = 0
        last_ada_fail_id = 0

        for row in session_dispatches:
            row_id = int(row["id"])
            agent = _nfc(str(row["agent_name"] or "").strip().lower())
            task_type = str(row["task_type"] or "").strip().lower()
            excerpt = str(row["summary_excerpt"] or "")
            note = str(row["note"] or "")
            audit_text = f"{note}\n{excerpt}"

            if agent in engineering_agents or task_type == "engineering":
                if row_id > last_eng_id:
                    last_eng_id = row_id
            elif agent == ada_name and task_type == "audit":
                if QUALITY_GATE_PASS_RE.search(audit_text):
                    if row_id > last_ada_pass_id:
                        last_ada_pass_id = row_id
                elif QUALITY_GATE_FAIL_RE.search(audit_text):
                    if row_id > last_ada_fail_id:
                        last_ada_fail_id = row_id

        pending_audit = last_eng_id > 0 and last_ada_pass_id <= last_eng_id
        ctx.stats["pending_maks_code_audit"] = pending_audit
        ctx.stats["pending_audit_gate"] = pending_audit

        if pending_audit:
            files_preview = ", ".join(modified_code_config[:5])
            if last_ada_fail_id > last_eng_id:
                msg = (
                    f"Bramka audytu (Wariant A): Ostatni audyt Ady zwrócił 'Quality Gate: FAIL' "
                    f"bez późniejszego 'Quality Gate: PASS' dla zmodyfikowanych plików ({files_preview})."
                )
            else:
                msg = (
                    f"Bramka audytu (Wariant A): Wykryto zmodyfikowane pliki kodu/konfiguracji ({files_preview}) "
                    f"oraz wpis inżynierski w lab_master.db, ale brak późniejszego zaakceptowanego audytu "
                    f"Ada/audit z 'Quality Gate: PASS'."
                )
            ctx.add_finding(
                level="error",
                category="audit_gate",
                path=str(db_path.relative_to(ctx.vault_root)),
                room=room_id,
                message=msg,
            )

    @staticmethod
    def _has_ignored_dir(dir_parts: Tuple[str, ...], ignored_dirs: Set[str]) -> bool:
        return any(d in ignored_dirs or d.startswith(".") for d in dir_parts)

    def _room_rel(self, ctx: QualityContext, role: str) -> Optional[str]:
        try:
            return _nfc(Path(ctx.config.get_room_by_role(role).path).as_posix())
        except ConfigError:
            return None

    def _filter_modified_code_or_config(self, ctx: QualityContext, rel_paths: Set[str]) -> List[str]:
        """Filtruje ścieżki z transkryptu należące do kodu lub konfiguracji (bramka audytu Ady)."""
        modified: List[str] = []
        ai_rel = self._room_rel(ctx, "governance-and-engine")
        proj_rel = self._room_rel(ctx, "active-projects")
        non_py_exts = AUDIT_GATE_EXTENSIONS - {".py"}

        for rel_str in rel_paths:
            p = Path(rel_str)
            name = p.name
            if name.startswith(".") or name in IGNORED_FILES:
                continue
            is_cfg = name.startswith("lab.config") and name.endswith(".toml")
            ext = p.suffix.lower()
            parts = p.parts

            if len(parts) == 1:
                if is_cfg or ext in non_py_exts:
                    modified.append(rel_str)
                continue

            if ai_rel and rel_str.startswith(ai_rel + "/"):
                sub = p.relative_to(ai_rel).parts
                if self._has_ignored_dir(sub[:-1], IGNORED_DIRS | {SESSION_LOGS_DIRNAME}):
                    continue
                in_skrypty = len(sub) >= 2 and sub[0] == "Skrypty"
                if is_cfg or (ext == ".py" and in_skrypty) or ext in non_py_exts:
                    modified.append(rel_str)
                continue

            if proj_rel and rel_str.startswith(proj_rel + "/"):
                sub = p.relative_to(proj_rel).parts
                if self._has_ignored_dir(sub[:-1], IGNORED_DIRS - {"_archiwum"}):
                    continue
                if "Produkt" in sub[:-1] and (is_cfg or ext in non_py_exts):
                    modified.append(rel_str)

        return sorted(set(modified))

    def _filter_modified_code_or_ui(self, ctx: QualityContext, rel_paths: Set[str]) -> List[str]:
        """Domena A (code_or_ui): *.py, *.sql, *.swift, *.html, *.css w Skrypty/, Bazy Danych/ lub Projekty/.../Produkt/."""
        modified: List[str] = []
        ai_rel = self._room_rel(ctx, "governance-and-engine")
        skrypty_rel = f"{ai_rel}/Skrypty" if ai_rel else None
        db_rel = self._room_rel(ctx, "sqlite-relational")
        proj_rel = self._room_rel(ctx, "active-projects")

        for rel_str in rel_paths:
            p = Path(rel_str)
            name = p.name
            if name.startswith(".") or name in IGNORED_FILES or p.suffix.lower() not in CODE_OR_UI_EXTENSIONS:
                continue

            if skrypty_rel and rel_str.startswith(skrypty_rel + "/"):
                sub = p.relative_to(skrypty_rel).parts
                if not self._has_ignored_dir(sub[:-1], IGNORED_DIRS):
                    modified.append(rel_str)
                continue

            if db_rel and rel_str.startswith(db_rel + "/"):
                sub = p.relative_to(db_rel).parts
                if not self._has_ignored_dir(sub[:-1], IGNORED_DIRS):
                    modified.append(rel_str)
                continue

            if proj_rel and rel_str.startswith(proj_rel + "/"):
                sub = p.relative_to(proj_rel).parts
                if not self._has_ignored_dir(sub[:-1], IGNORED_DIRS - {"_archiwum"}) and "Produkt" in sub[:-1]:
                    modified.append(rel_str)

        return sorted(set(modified))

    def _filter_modified_knowledge_or_research(
        self,
        ctx: QualityContext,
        rel_paths: Set[str],
        last_checkpoint_mtime: Optional[float] = None,
        session_logs: Optional[List[Path]] = None,
        has_prior_knowledge_dispatch: bool = False,
        assets_registry_map: Optional[Dict[str, Tuple[str, str]]] = None,
    ) -> List[str]:
        """Domena B (knowledge_or_research): *.md w Badania/Inkubator/Refleksje oraz 06 Zasoby/Notatki/ ze stage: distilled."""
        modified: List[str] = []
        know_rel = self._room_rel(ctx, "atomic-synthesis-and-research")
        assets_rel = self._room_rel(ctx, "reference-and-assets")
        notes_rel = f"{assets_rel}/Notatki" if assets_rel else None
        logged_paths_cache: Optional[Set[str]] = None

        for rel_str in rel_paths:
            p = Path(rel_str)
            name = p.name
            if not name.endswith(".md") or name.startswith(".") or name in IGNORED_FILES or name.lower() == "readme.md":
                continue

            if know_rel and rel_str.startswith(know_rel + "/"):
                sub = p.relative_to(know_rel).parts
                if len(sub) >= 2 and sub[0] in ("Badania", "Inkubator", "Refleksje"):
                    if not self._has_ignored_dir(sub[1:-1], IGNORED_DIRS):
                        modified.append(rel_str)
                continue

            if notes_rel and rel_str.startswith(notes_rel + "/"):
                sub = p.relative_to(notes_rel).parts
                if self._has_ignored_dir(sub[:-1], IGNORED_DIRS):
                    continue
                fpath = ctx.vault_root / rel_str
                try:
                    if not fpath.is_file():
                        continue
                    stat_res = fpath.stat()
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
                    if self._is_previously_validated_reference_note(
                        rel_str,
                        content,
                        fm,
                        stat_res,
                        last_checkpoint_mtime=last_checkpoint_mtime,
                        session_logs=session_logs,
                        has_prior_knowledge_dispatch=has_prior_knowledge_dispatch,
                        assets_registry_map=assets_registry_map,
                        logged_paths_cache=logged_paths_cache,
                    ):
                        continue
                    modified.append(rel_str)

        return sorted(set(modified))

    @staticmethod
    def _collect_logged_paths(session_logs: Optional[List[Path]]) -> Set[str]:
        """Zbiera znormalizowane (NFC) ścieżki plików z Dzienników Sesji."""
        paths: Set[str] = set()
        if not session_logs:
            return paths
        for log_p in session_logs:
            try:
                txt = log_p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in re.findall(r"`([^`\n]+\.md)`", txt):
                paths.add(_nfc(m.strip()))
        return paths

    def _is_previously_validated_reference_note(
        self,
        rel_path: str,
        content: str,
        fm: Optional[Dict[str, object]],
        stat_res: os.stat_result,
        last_checkpoint_mtime: Optional[float],
        session_logs: Optional[List[Path]],
        has_prior_knowledge_dispatch: bool,
        assets_registry_map: Optional[Dict[str, Tuple[str, str]]],
        logged_paths_cache: Optional[Set[str]],
    ) -> bool:
        """Sprawdza deterministycznie czy notatka z 06 Zasoby/Notatki/ była już zwalidowanym destylatem przed sesją."""
        rel_nfc = _nfc(rel_path)

        # 1. Ścieżka rejestrowa (assets_registry): identyczny hash treści oraz stage == 'distilled'
        if assets_registry_map and rel_nfc in assets_registry_map:
            reg_stage, reg_sha = assets_registry_map[rel_nfc]
            if reg_stage == "distilled" and reg_sha:
                norm_content = content.replace("\r\n", "\n").replace("\r", "\n")
                actual_sha = hashlib.sha256(norm_content.encode("utf-8")).hexdigest()
                if actual_sha == reg_sha:
                    return True

        # 2. Ścieżka historyczna (stan przed zasiedleniem assets_registry):
        #    plik występował już w Dziennikach Sesji, istnieje wcześniejszy dispatch Klary/Edyty,
        #    plik powstał przed ostatnim checkpointem (st_birthtime) oraz daty YAML nie są po checkpoincie.
        if last_checkpoint_mtime is None or not has_prior_knowledge_dispatch or not session_logs:
            return False

        birthtime = getattr(stat_res, "st_birthtime", None)
        if birthtime is not None and birthtime > last_checkpoint_mtime:
            return False

        cp_date = datetime.fromtimestamp(last_checkpoint_mtime).date()
        has_yaml_date = False
        if fm:
            for key in ("updated", "created", "date"):
                raw_val = fm.get(key)
                if raw_val is None:
                    continue
                try:
                    parsed = date.fromisoformat(str(raw_val).strip()[:10])
                    has_yaml_date = True
                    if parsed > cp_date:
                        return False
                except ValueError:
                    continue
        if not has_yaml_date:
            return False

        if logged_paths_cache is None:
            logged_paths_cache = self._collect_logged_paths(session_logs)
        return rel_nfc in logged_paths_cache

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
