#!/usr/bin/env python3
"""
token_telemetry.py - Deterministyczna Telemetria Tokenów Sesji dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (Zero-Pip).
- Bezpieczeństwo i integralność danych (fail-safe).
- Integracja z core_config.ConfigManager (Aksjomat Nienaruszalności Rdzenia).

Zastosowanie:
- Monitorowanie zużycia okna kontekstowego modelu LLM w aktywnej sesji Antigravity/Gemini.
- Formatowanie telemetrii dla paska Raycasta, kokpitu Warsztatu Współpracy i potoków CI/CD.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca LAB
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Dołączenie korzenia skarbca do sys.path dla poprawnego importu core_config
_script_dir = Path(__file__).resolve().parent
from lab_core.env import EnvironmentError as LabEnvError, find_vault_root
try:
    _vault_root = find_vault_root(_script_dir)
    if str(_vault_root) not in sys.path:
        sys.path.insert(0, str(_vault_root))
except LabEnvError:
    _vault_root = None

try:
    from lab_core.config import ConfigError, ConfigManager
except ImportError:
    try:
        from core_config import ConfigError, ConfigManager
    except ImportError:
        ConfigManager = None  # type: ignore
        ConfigError = Exception  # type: ignore


# ==============================================================================
# WYJĄTKI MODUŁU TELEMETRII
# ==============================================================================

class TelemetryError(Exception):
    """Bazowy wyjątek dla błędów telemetrii tokenów."""
    pass


class TranscriptNotFoundError(TelemetryError):
    """Podnoszony, gdy nie znaleziono pliku transkryptu sesji."""
    pass


class TelemetryDataError(TelemetryError):
    """Podnoszony, gdy w transkrypcie brakuje danych o tokenach lub plik jest uszkodzony."""
    pass


class WorkshopUpdateError(TelemetryError):
    """Podnoszony w przypadku błędu aktualizacji pliku Warsztatu Współpracy."""
    pass


# ==============================================================================
# STRUKTURY DANYCH I STAŁE
# ==============================================================================

DEFAULT_WINDOW_LIMIT = 150_000
DEFAULT_ANTIGRAVITY_LIMIT = 250_000


def default_budget_for_provider(provider: str = "") -> int:
    """Zwraca domyślny budżet okna kontekstowego dla wskazanego dostawcy LLM."""
    return DEFAULT_ANTIGRAVITY_LIMIT if (provider or "").strip().lower() == "antigravity" else DEFAULT_WINDOW_LIMIT


def _detect_runtime_provider() -> str:
    """Wykrywa aktywnego dostawcę LLM na podstawie zmiennych środowiskowych procesu."""
    if os.environ.get("ANTIGRAVITY_SOURCE_METADATA"):
        return "antigravity"
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if any(os.environ.get(k) for k in ("CODEX_THREAD_ID", "CODEX_CI", "CODEX_HOME", "CODEX_SANDBOX")):
        return "codex"
    return ""


def _resolve_target_vault_root(vault_root: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """Rozwiązuje ścieżkę korzenia skarbca (argument -> moduł -> CWD)."""
    if vault_root:
        return Path(vault_root).expanduser().resolve()
    if _vault_root is not None:
        return _vault_root
    try:
        return find_vault_root(Path.cwd())
    except Exception:
        return None


def resolve_llm_provider(vault_root: Optional[Union[str, Path]] = None) -> str:
    """Ustala aktywnego dostawcę LLM (zmienne środowiskowe runtime -> [ai_team.llm].provider)."""
    runtime_prov = _detect_runtime_provider()
    if runtime_prov:
        return runtime_prov
    target_root = _resolve_target_vault_root(vault_root)
    if target_root is None or ConfigManager is None:
        return ""
    cfg_file = target_root / "lab.config.toml"
    if not cfg_file.is_file():
        return ""
    try:
        cfg = ConfigManager(config_path=cfg_file, vault_root=target_root)
        llm_cfg = cfg._raw_data.get("ai_team", {}).get("llm", {})
        if isinstance(llm_cfg, dict):
            return str(llm_cfg.get("provider", "") or "").strip().lower()
    except Exception:
        pass
    return ""


def resolve_session_token_budget(
    vault_root: Optional[Union[str, Path]] = None,
    default: int = DEFAULT_WINDOW_LIMIT,
) -> int:
    """Odczytuje [telemetry].session_token_budget z lab.config.toml (z domyślnym 150k dla Codex)."""
    if ConfigManager is None:
        return default
    target_root = _resolve_target_vault_root(vault_root)
    if target_root is None:
        return default
    cfg_file = target_root / "lab.config.toml"
    if not cfg_file.is_file():
        return default
    try:
        cfg = ConfigManager(config_path=cfg_file, vault_root=target_root)
        raw_tel = cfg._raw_data.get("telemetry", {})
        has_explicit = isinstance(raw_tel, dict) and "session_token_budget" in raw_tel
        provider = resolve_llm_provider(target_root)
        if not has_explicit:
            if provider:
                return default_budget_for_provider(provider)
            return default
        budget = int(cfg.telemetry.session_token_budget)
        return budget if budget > 0 else default
    except Exception:
        return default

ZONE_COMFORT = "Komfortowa"
ZONE_ATTENTION = "Wymaga uwagi"
ZONE_CRITICAL = "Krytyczna"

ICON_COMFORT = "🟢"
ICON_ATTENTION = "🟡"
ICON_CRITICAL = "🔴"


@dataclass(frozen=True)
class TokenMetrics:
    """Niemutowalny model metryk telemetrii tokenów."""
    input_tokens: int
    cache_read_tokens: int
    output_tokens: int
    total_context_tokens: int
    limit: int
    percentage: float
    zone: str
    zone_icon: str
    zone_display: str
    step_index: Optional[int] = None
    transcript_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Konwertuje metryki do słownika serializowalnego do JSON."""
        data = asdict(self)
        data["statusline"] = format_statusline(self)
        return data


@dataclass(frozen=True)
class UsageTotals:
    """Zagregowane sumy zużycia tokenów i wywołań planera."""
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    planner_calls: int = 0
    peak_context_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)

    def add(self, other: "UsageTotals") -> "UsageTotals":
        return UsageTotals(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            planner_calls=self.planner_calls + other.planner_calls,
            peak_context_tokens=max(self.peak_context_tokens, other.peak_context_tokens),
        )


@dataclass(frozen=True)
class SessionSummary:
    """Podsumowanie zużycia tokenów sesji głównej wraz z powiązanymi subagentami."""
    session_id: str
    session_date: str
    transcript_path: str
    subagents_count: int
    subagent_ids: Tuple[str, ...]
    main: UsageTotals
    subagents: UsageTotals
    total: UsageTotals
    limit: int = DEFAULT_WINDOW_LIMIT
    load_zone: str = ZONE_COMFORT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_date": self.session_date,
            "transcript_path": self.transcript_path,
            "subagents_count": self.subagents_count,
            "subagent_ids": list(self.subagent_ids),
            "limit": self.limit,
            "load_zone": self.load_zone,
            "main": self.main.to_dict(),
            "subagents": self.subagents.to_dict(),
            "total": self.total.to_dict(),
        }


@dataclass(frozen=True)
class DailyTelemetryRow:
    """Dzienne zestawienie zużycia tokenów."""
    date: str
    sessions_count: int
    subagents_count: int
    main: UsageTotals
    subagents: UsageTotals
    total: UsageTotals

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "sessions_count": self.sessions_count,
            "subagents_count": self.subagents_count,
            "main": self.main.to_dict(),
            "subagents": self.subagents.to_dict(),
            "total": self.total.to_dict(),
        }


@dataclass(frozen=True)
class TelemetryReport:
    """Okresowy raport zużycia tokenów (sesje główne vs subagenci)."""
    date_from: str
    date_to: str
    days: Tuple[DailyTelemetryRow, ...]
    sessions_count: int
    subagents_count: int
    main: UsageTotals
    subagents: UsageTotals
    total: UsageTotals

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "sessions_count": self.sessions_count,
            "subagents_count": self.subagents_count,
            "main": self.main.to_dict(),
            "subagents": self.subagents.to_dict(),
            "total": self.total.to_dict(),
            "days": [d.to_dict() for d in self.days],
        }


# ==============================================================================
# LOGIKA BIZNESOWA I FORMATOWANIE
# ==============================================================================

def format_tokens(n: int) -> str:
    """
    Formatuje liczbę tokenów do zwięzłej postaci inżynieryjnej:
    - 250000 -> '250k'
    - 109400 -> '109.4k'
    - 500 -> '500'
    """
    if n >= 1000:
        k_val = n / 1000.0
        if k_val.is_integer():
            return f"{int(k_val)}k"
        return f"{k_val:.1f}k"
    return str(n)


def calculate_zone(total_context_tokens: int, limit: int = DEFAULT_WINDOW_LIMIT) -> Tuple[str, str, str]:
    """
    Określa strefę obciążenia okna kontekstowego (progi 80% i 96% limitu):
    - Dla 150 000: < 120 000 🟢 | 120 000..144 000 🟡 | > 144 000 🔴
    - Dla 250 000: < 200 000 🟢 | 200 000..240 000 🟡 | > 240 000 🔴
    """
    warn_threshold = int(limit * 0.80)
    crit_threshold = int(limit * 0.96)

    if total_context_tokens < warn_threshold:
        zone = ZONE_COMFORT
        icon = ICON_COMFORT
    elif total_context_tokens <= crit_threshold:
        zone = ZONE_ATTENTION
        icon = ICON_ATTENTION
    else:
        zone = ZONE_CRITICAL
        icon = ICON_CRITICAL

    display = f"{icon} {zone}"
    return zone, icon, display


def format_statusline(metrics: TokenMetrics) -> str:
    """Generuje 1-linijkowy format statusline dla Raycast / paska menu."""
    used_fmt = format_tokens(metrics.total_context_tokens)
    limit_fmt = format_tokens(metrics.limit)
    return f"🧠 {used_fmt} / {limit_fmt} ({metrics.percentage:.1f}%) {metrics.zone_icon}"


def format_terminal_summary(metrics: TokenMetrics) -> str:
    """Generuje czytelny raport terminalowy."""
    used_fmt = format_tokens(metrics.total_context_tokens)
    limit_fmt = format_tokens(metrics.limit)
    step_str = str(metrics.step_index) if metrics.step_index is not None else "N/A"
    path_str = metrics.transcript_path or "N/A"

    lines = [
        "=" * 64,
        "LAB 3.0 • TELEMETRIA OKNA KONTEKSTOWEGO SESJI (PZW)",
        "=" * 64,
        f"Transkrypt sesji  : {path_str}",
        f"Ostatni krok logu : {step_str}",
        f"Input Tokens      : {metrics.input_tokens:,}",
        f"Cache Read Tokens : {metrics.cache_read_tokens:,}",
        f"Output Tokens     : {metrics.output_tokens:,}",
        "-" * 64,
        f"Kontekst aktywny  : {metrics.total_context_tokens:,} ({used_fmt})",
        f"Limit okna        : {metrics.limit:,} ({limit_fmt})",
        f"Zużycie okna      : {metrics.percentage:.1f}%",
        f"Strefa obciążenia : {metrics.zone_display}",
        "=" * 64,
        f"Statusline        : {format_statusline(metrics)}",
    ]
    return "\n".join(lines)


# ==============================================================================
# DETEKCJA LOGÓW TRANSKRYPTU I PARSOWANIE
# ==============================================================================

def _find_claude_transcripts(claude_projects_dir: Optional[Path] = None) -> List[Path]:
    """Zwraca listę plików sesji Claude Code (*.jsonl) z ~/.claude/projects/."""
    base = claude_projects_dir or (Path.home() / ".claude" / "projects")
    if not base.is_dir():
        return []
    candidates: List[Path] = []
    # Preferuj projekt pasujący do bieżącego _vault_root, jeśli istnieje
    if _vault_root is not None:
        encoded = re.sub(r"[^A-Za-z0-9]", "-", str(_vault_root))
        proj_dir = base / encoded
        if proj_dir.is_dir():
            try:
                candidates.extend(p for p in proj_dir.glob("*.jsonl") if p.is_file())
            except (PermissionError, OSError):
                pass
    if not candidates:
        try:
            candidates.extend(p for p in base.glob("*/*.jsonl") if p.is_file())
        except (PermissionError, OSError):
            pass
    return candidates


def _find_codex_transcripts(codex_sessions_dir: Optional[Path] = None) -> List[Path]:
    """Zwraca listę plików sesji OpenAI Codex CLI (rollout-*.jsonl) z $CODEX_HOME/sessions lub ~/.codex/sessions/."""
    bases: List[Path] = []
    if codex_sessions_dir is not None:
        bases.append(codex_sessions_dir)
    else:
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            bases.append(Path(codex_home).expanduser() / "sessions")
        bases.append(Path.home() / ".codex" / "sessions")

    seen: Set[Path] = set()
    found: List[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("rollout-*.jsonl"):
                if p.is_file():
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(p)
        except (PermissionError, OSError):
            continue
    return found


def _codex_rollout_matches_vault(rollout_path: Path, vault_root: Path) -> bool:
    """Sprawdza czy session_meta w pliku rollout-*.jsonl pochodzi z docelowego skarbca."""
    try:
        target = str(vault_root.resolve())
        with open(rollout_path, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= 5:
                    break
                line_s = line.strip()
                if not line_s:
                    continue
                data = json.loads(line_s)
                if isinstance(data, dict) and data.get("type") == "session_meta":
                    payload = data.get("payload")
                    if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                        cwd_norm = str(Path(payload["cwd"]).expanduser().resolve())
                        return cwd_norm == target or cwd_norm.startswith(target + os.sep)
    except Exception:
        return False
    return False


def find_latest_transcript(
    brain_dir: Optional[Union[str, Path]] = None,
    claude_dir: Optional[Union[str, Path]] = None,
    codex_dir: Optional[Union[str, Path]] = None,
    vault_root: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Deterministycznie wyszukuje aktywny plik logu transkryptu sesji:
    Wybiera plik z najnowszym czasem modyfikacji (mtime) w strukturze:
    - Antigravity: ~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl
    - Claude Code: ~/.claude/projects/*/*.jsonl
    - Codex CLI: ~/.codex/sessions/**/rollout-*.jsonl
    """
    if brain_dir:
        base_dir = Path(brain_dir).expanduser().resolve()
        if not base_dir.is_dir():
            raise TranscriptNotFoundError(f"Katalog bazy sesji brain nie istnieje: {base_dir}")
    else:
        base_dir = Path.home() / ".gemini" / "antigravity" / "brain"

    # 0. Sprawdzenie aktywnej konwersacji ze środowiska wykonawczego Antigravity
    env_meta = os.environ.get("ANTIGRAVITY_SOURCE_METADATA")
    if env_meta and base_dir.is_dir():
        try:
            meta = json.loads(env_meta)
            cid = meta.get("tool", {}).get("conversationId")
            if cid:
                cand = base_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
                if cand.is_file():
                    return cand
        except Exception:
            pass

    if not brain_dir:
        provider = resolve_llm_provider(vault_root)
        target_root = _resolve_target_vault_root(vault_root)
        x_dir = Path(codex_dir).expanduser().resolve() if codex_dir else None
        codex_candidates = _find_codex_transcripts(x_dir)
        c_dir = Path(claude_dir).expanduser().resolve() if claude_dir else None
        claude_candidates = _find_claude_transcripts(c_dir)

        if provider == "codex" and codex_candidates:
            codex_sorted = sorted(codex_candidates, key=lambda p: p.stat().st_mtime, reverse=True)
            if target_root is not None:
                for p in codex_sorted[:20]:
                    if _codex_rollout_matches_vault(p, target_root):
                        return p
            return codex_sorted[0]

        if provider == "claude" and claude_candidates:
            return max(claude_candidates, key=lambda p: p.stat().st_mtime)
    else:
        codex_candidates = []
        claude_candidates = []

    candidates: List[Path] = []
    if base_dir.is_dir():
        pattern = "*/.system_generated/logs/transcript.jsonl"
        try:
            candidates.extend(p for p in base_dir.glob(pattern) if p.is_file())
        except (PermissionError, OSError):
            pass

    if not brain_dir:
        candidates.extend(claude_candidates)
        candidates.extend(codex_candidates)

    if not candidates:
        raise TranscriptNotFoundError(
            f"Nie odnaleziono żadnego pliku transkryptu sesji (Antigravity: {base_dir}, Claude Code: ~/.claude/projects, Codex CLI: ~/.codex/sessions)."
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_codex_message_chars(data: Dict[str, Any]) -> int:
    """Zlicza długość tekstów wiadomości w pliku rollout-*.jsonl (do szacowania przed pojawieniem się token_count)."""
    total_chars = 0
    dtype = data.get("type")
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return 0
    if dtype == "event_msg" and payload.get("type") in ("user_message", "agent_message"):
        for k in ("message", "text", "content"):
            val = payload.get(k)
            if isinstance(val, str):
                total_chars += len(val)
    elif dtype == "response_item" and payload.get("type") == "message":
        content = payload.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text")
                    if isinstance(txt, str):
                        total_chars += len(txt)
    return total_chars


def _estimate_codex_rollout_tokens(path: Path) -> int:
    """Szacuje tokeny z treści wiadomości w rollout-*.jsonl (0 gdy brak wiadomości)."""
    total_chars = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    total_chars += _extract_codex_message_chars(data)
    except OSError:
        return 0
    if total_chars <= 0:
        return 0
    return max(1, total_chars // 4)


def estimate_fallback_telemetry(
    vault_root: Optional[Union[str, Path]] = None,
    limit: Optional[int] = None,
) -> TokenMetrics:
    """Dynamiczny fallback telemetrii w razie braku pliku transkryptu (np. Codex CLI / sandbox).

    Szacuje aktualne obciążenie kontekstu startowego z aktywnych artefaktów sesji
    w skarbcu (AGENTS.md, kontrakt Koordynatora, lab.config.toml, dzisiejsza Agenda i Dzienniki).
    """
    target_root = _resolve_target_vault_root(vault_root)
    effective_limit = int(limit) if limit is not None else resolve_session_token_budget(vault_root=target_root)

    total_chars = 0
    if target_root is not None and target_root.is_dir():
        candidates: List[Path] = [
            target_root / "AGENTS.md",
            target_root / "lab.config.toml",
        ]
        today_str = date.today().isoformat()
        year_str, month_str, _ = today_str.split("-")

        if ConfigManager is not None and (target_root / "lab.config.toml").is_file():
            try:
                cfg = ConfigManager(config_path=target_root / "lab.config.toml", vault_root=target_root)
                try:
                    candidates.append(cfg.get_agent_contract_file("coordinator", absolute=True))
                except Exception:
                    pass
                try:
                    ws_room = cfg.get_room_by_role("daily-workbench")
                    candidates.append(target_root / ws_room.path / f"{today_str} – Agenda Robocza.md")
                except Exception:
                    pass
                try:
                    ai_room = cfg.get_room_by_role("governance-and-engine")
                    logs_dir = target_root / ai_room.path / "Dzienniki Sesji" / year_str / month_str
                    if logs_dir.is_dir():
                        candidates.extend(sorted(logs_dir.glob(f"{today_str}*.md")))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            candidates.append(target_root / "02 Warsztat" / f"{today_str} – Agenda Robocza.md")

        seen: Set[Path] = set()
        for p in candidates:
            try:
                rp = p.resolve()
                if rp in seen or not rp.is_file():
                    continue
                seen.add(rp)
                total_chars += len(rp.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue

    estimated_tokens = max(0, total_chars // 4)
    percentage = round((estimated_tokens / effective_limit) * 100, 2) if effective_limit > 0 else 0.0
    zone, zone_icon, zone_display = calculate_zone(estimated_tokens, effective_limit)
    return TokenMetrics(
        input_tokens=estimated_tokens,
        cache_read_tokens=0,
        output_tokens=0,
        total_context_tokens=estimated_tokens,
        limit=effective_limit,
        percentage=percentage,
        zone=zone,
        zone_icon=zone_icon,
        zone_display=zone_display,
        step_index=None,
        transcript_path="fallback://estimated",
    )


def parse_telemetry(
    transcript_path: Union[str, Path],
    limit: Optional[int] = None,
) -> TokenMetrics:
    """
    Odczytuje plik transcript.jsonl i wyciąga najnowsze metryki tokenów:
    - input_tokens, cache_read_tokens, output_tokens.
    - total_context_tokens = input_tokens + cache_read_tokens.
    - procent zużycia okna i strefę.
    """
    effective_limit = int(limit) if limit is not None else resolve_session_token_budget()
    path = Path(transcript_path).expanduser().resolve()
    if not path.is_file():
        raise TranscriptNotFoundError(f"Plik transkryptu nie istnieje: {path}")

    last_token_entry: Optional[Dict[str, Any]] = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if not isinstance(data, dict):
                        continue
                    extracted = _extract_usage_from_entry(data)
                    if extracted is not None:
                        in_tok, cache_tok, out_tok, step_idx = extracted
                        last_token_entry = {
                            "input_tokens": in_tok,
                            "cache_read_tokens": cache_tok,
                            "output_tokens": out_tok,
                            "step_index": step_idx,
                        }
                except json.JSONDecodeError:
                    # Pomijamy niepełne lub uszkodzone linie logu
                    continue
    except OSError as e:
        raise TelemetryDataError(f"Błąd wejścia/wyjścia podczas odczytu {path}: {e}") from e

    if last_token_entry is None:
        if path.name.startswith("rollout-"):
            est_tokens = _estimate_codex_rollout_tokens(path)
            percentage = round((est_tokens / effective_limit) * 100, 2) if effective_limit > 0 else 0.0
            zone, zone_icon, zone_display = calculate_zone(est_tokens, effective_limit)
            return TokenMetrics(
                input_tokens=est_tokens,
                cache_read_tokens=0,
                output_tokens=0,
                total_context_tokens=est_tokens,
                limit=effective_limit,
                percentage=percentage,
                zone=zone,
                zone_icon=zone_icon,
                zone_display=zone_display,
                step_index=None,
                transcript_path=str(path),
            )
        raise TelemetryDataError(
            f"W pliku transkryptu '{path}' nie znaleziono żadnego wpisu z danymi 'input_tokens'."
        )

    try:
        input_tokens = int(last_token_entry.get("input_tokens", 0))
        cache_read_tokens = int(last_token_entry.get("cache_read_tokens", 0))
        output_tokens = int(last_token_entry.get("output_tokens", 0))
        step_index = last_token_entry.get("step_index")
        if step_index is not None:
            step_index = int(step_index)
    except (ValueError, TypeError) as e:
        raise TelemetryDataError(f"Niepoprawny format wartości liczbowych tokenów: {e}") from e

    total_context_tokens = input_tokens + cache_read_tokens
    percentage = round((total_context_tokens / effective_limit) * 100, 2) if effective_limit > 0 else 0.0

    zone, zone_icon, zone_display = calculate_zone(total_context_tokens, effective_limit)

    return TokenMetrics(
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        output_tokens=output_tokens,
        total_context_tokens=total_context_tokens,
        limit=effective_limit,
        percentage=percentage,
        zone=zone,
        zone_icon=zone_icon,
        zone_display=zone_display,
        step_index=step_index,
        transcript_path=str(path),
    )


# ==============================================================================
# AGREGACJA SESJI, SUBAGENTÓW I RAPORTOWANIE OKRESOWE (WARIANT B)
# ==============================================================================

_CONV_ID_PATTERNS = (
    re.compile(r'["\']?conversationId["\']?\s*[:=]\s*["\']?([0-9a-fA-F-]{8,}|[A-Za-z0-9._-]{4,})["\']?'),
    re.compile(r'["\']?conversation_id["\']?\s*[:=]\s*["\']?([0-9a-fA-F-]{8,}|[A-Za-z0-9._-]{4,})["\']?'),
)
_SENDER_RE = re.compile(r'\bsender=([0-9a-fA-F-]{8,}|[A-Za-z0-9._-]{4,})\b')
_PARENT_ID_RE = re.compile(r'\bname:\s*"parent",\s*id:\s*"([^"]+)"')
_IGNORED_IDS = frozenset({"parent", "user", "system", "MODEL", "SYSTEM", "USER_EXPLICIT", "none", "null"})


def _extract_usage_from_entry(data: Dict[str, Any]) -> Optional[Tuple[int, int, int, Optional[int]]]:
    """Wyciąga (input_tokens, cache_read_tokens, output_tokens, step_index) z kroku JSONL."""
    if "input_tokens" in data:
        try:
            in_tok = int(data.get("input_tokens", 0) or 0)
            cache_tok = int(data.get("cache_read_tokens", 0) or 0)
            out_tok = int(data.get("output_tokens", 0) or 0)
            step_idx = data.get("step_index")
            return in_tok, cache_tok, out_tok, int(step_idx) if step_idx is not None else None
        except (ValueError, TypeError):
            return None

    # Codex CLI rollout-*.jsonl: event_msg -> payload(type=token_count) -> info.last_token_usage / total_token_usage
    if data.get("type") == "event_msg" and isinstance(data.get("payload"), dict):
        p = data["payload"]
        if p.get("type") == "token_count" and isinstance(p.get("info"), dict):
            info = p["info"]
            u = info.get("last_token_usage") or info.get("total_token_usage")
            if isinstance(u, dict) and "input_tokens" in u:
                try:
                    in_tok = int(u.get("input_tokens", 0) or 0)
                    cache_details = u.get("input_tokens_details")
                    cached_detail = int(cache_details.get("cached_tokens", 0) or 0) if isinstance(cache_details, dict) else 0
                    cache_tok = (
                        int(u.get("cached_input_tokens", 0) or 0)
                        + int(u.get("cache_read_input_tokens", 0) or 0)
                        + cached_detail
                    )
                    out_tok = int(u.get("output_tokens", 0) or 0)
                    return in_tok, cache_tok, out_tok, None
                except (ValueError, TypeError):
                    return None

    msg_usage = (data.get("message") or {}).get("usage") if isinstance(data.get("message"), dict) else None
    usage = msg_usage if isinstance(msg_usage, dict) else (data.get("usage") if isinstance(data.get("usage"), dict) else None)
    if usage and "input_tokens" in usage:
        try:
            in_tok = int(usage.get("input_tokens", 0) or 0)
            cache_details = usage.get("input_tokens_details")
            cached_detail = int(cache_details.get("cached_tokens", 0) or 0) if isinstance(cache_details, dict) else 0
            cache_tok = (
                int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
                + int(usage.get("cached_input_tokens", 0) or 0)
                + cached_detail
            )
            out_tok = int(usage.get("output_tokens", 0) or 0)
            step_idx = data.get("step_index")
            return in_tok, cache_tok, out_tok, int(step_idx) if step_idx is not None else None
        except (ValueError, TypeError):
            return None
    return None


def _preferred_transcript_file(transcript_path: Path) -> Path:
    """Preferuje transcript_full.jsonl (pełne pola bez obcięcia), jeśli jest dostępny."""
    full_path = transcript_path.parent / "transcript_full.jsonl"
    if full_path.is_file() and os.access(full_path, os.R_OK):
        return full_path
    return transcript_path


def _infer_session_id(transcript_path: Path) -> str:
    """Wyznacza session_id (conversationId) ze ścieżki transkryptu."""
    resolved = transcript_path.resolve()
    parts = resolved.parts
    if len(parts) >= 4 and parts[-2] == "logs" and parts[-3] == ".system_generated":
        return parts[-4]
    return resolved.stem


def _infer_brain_dir(transcript_path: Path, brain_dir: Optional[Union[str, Path]] = None) -> Path:
    """Wyznacza katalog brain zawierający sesje."""
    if brain_dir:
        return Path(brain_dir).expanduser().resolve()
    resolved = transcript_path.resolve()
    parts = resolved.parts
    if len(parts) >= 5 and parts[-2] == "logs" and parts[-3] == ".system_generated":
        return resolved.parents[3]
    return Path.home() / ".gemini" / "antigravity" / "brain"


def _parse_iso_date(raw_ts: str) -> Optional[str]:
    """Konwertuje znacznik czasu ISO-8601 na datę lokalną YYYY-MM-DD."""
    text = raw_ts.strip()
    if not text:
        return None
    try:
        iso_norm = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(iso_norm)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.date().isoformat()
    except ValueError:
        if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text[:10]):
            return text[:10]
    return None


def _extract_session_date(transcript_path: Path) -> str:
    """Ustala datę sesji na podstawie created_at/timestamp w transkrypcie lub mtime pliku."""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    for key in ("created_at", "timestamp"):
                        val = data.get(key)
                        if isinstance(val, str):
                            parsed = _parse_iso_date(val)
                            if parsed:
                                return parsed
    except OSError:
        pass
    try:
        mtime = transcript_path.stat().st_mtime
        return datetime.fromtimestamp(mtime).date().isoformat()
    except OSError:
        return date.today().isoformat()


def _is_subagent_transcript(transcript_path: Path) -> bool:
    """Sprawdza, czy transkrypt należy do subagenta (<subagent_reminder>)."""
    target = _preferred_transcript_file(transcript_path)
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= 8:
                    break
                if "<subagent_reminder>" in line or "You are running as a subagent" in line:
                    return True
    except OSError:
        pass
    return False


def _parse_tool_calls_field(raw_calls: Any) -> List[Dict[str, Any]]:
    """Normalizuje pole tool_calls (lista słowników lub zserializowany JSON/repr)."""
    if isinstance(raw_calls, list):
        return [tc for tc in raw_calls if isinstance(tc, dict)]
    if isinstance(raw_calls, str):
        text = raw_calls.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [tc for tc in parsed if isinstance(tc, dict)]
            except json.JSONDecodeError:
                pass
    return []


def _extract_ids_from_text(text: str) -> List[str]:
    """Wyciąga potencjalne conversationId z tekstu odpowiedzi narzędzia."""
    found: List[str] = []
    for pat in _CONV_ID_PATTERNS:
        for match in pat.findall(text):
            if match and match not in _IGNORED_IDS and match not in found:
                found.append(match)
    return found


def extract_subagent_ids(transcript_path: Union[str, Path]) -> List[str]:
    """Wyciąga unikalne conversationId subagentów wywołanych z danego transkryptu."""
    path = Path(transcript_path).expanduser().resolve()
    if not path.is_file():
        return []
    scan_path = _preferred_transcript_file(path)
    self_id = _infer_session_id(path)
    parent_ids: Set[str] = set()
    subagent_ids: List[str] = []
    seen_invoke = False

    def _add_candidate(cid: str) -> None:
        clean = cid.strip()
        if not clean or clean == self_id or clean in parent_ids or clean in _IGNORED_IDS:
            return
        if clean not in subagent_ids:
            subagent_ids.append(clean)

    try:
        with open(scan_path, "r", encoding="utf-8", errors="replace") as f:
            for step_num, line in enumerate(f):
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue

                step_type = str(data.get("type") or "")
                content_str = str(data.get("content") or "")

                # Rozpoznaj ID rodzica z <subagent_reminder>, aby nigdy nie traktować go jak subagenta
                if "<subagent_reminder>" in content_str:
                    for pm in _PARENT_ID_RE.findall(content_str):
                        parent_ids.add(pm)
                    for sm in _SENDER_RE.findall(content_str):
                        parent_ids.add(sm)
                    continue

                if step_num == 0 and step_type == "SYSTEM_MESSAGE":
                    for sm in _SENDER_RE.findall(content_str):
                        parent_ids.add(sm)
                    continue

                # 1. Wywołania narzędzi (invoke_subagent / send_message)
                for tc in _parse_tool_calls_field(data.get("tool_calls")):
                    name = str(tc.get("name") or tc.get("tool_name") or "")
                    if name == "invoke_subagent":
                        seen_invoke = True
                        for key in ("conversationId", "conversation_id"):
                            val = tc.get(key)
                            if isinstance(val, str):
                                _add_candidate(val)
                        for resp_key in ("response", "result", "output"):
                            resp = tc.get(resp_key)
                            if resp is not None:
                                for cid in _extract_ids_from_text(json.dumps(resp) if isinstance(resp, dict) else str(resp)):
                                    _add_candidate(cid)
                    elif name == "send_message":
                        args = tc.get("args") or tc.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        if isinstance(args, dict):
                            recipient = args.get("Recipient") or args.get("recipient")
                            if isinstance(recipient, str):
                                _add_candidate(recipient)

                # Fallback na obcięte tool_calls jako tekst w transcript.jsonl
                raw_tc = data.get("tool_calls")
                if isinstance(raw_tc, str) and "invoke_subagent" in raw_tc:
                    seen_invoke = True
                    for cid in _extract_ids_from_text(raw_tc):
                        _add_candidate(cid)

                # 2. Odpowiedzi narzędzi (GENERIC) po invoke_subagent lub zawierające conversationId
                if step_type == "GENERIC" and (seen_invoke or "conversationId" in content_str or "conversation_id" in content_str):
                    for cid in _extract_ids_from_text(content_str):
                        _add_candidate(cid)

                # 3. Komunikaty SYSTEM_MESSAGE od powracających subagentów
                if step_type == "SYSTEM_MESSAGE" and seen_invoke:
                    for sm in _SENDER_RE.findall(content_str):
                        _add_candidate(sm)
                    for cid in _extract_ids_from_text(content_str):
                        _add_candidate(cid)
    except OSError:
        return []

    return subagent_ids


def collect_related_subagents(
    transcript_path: Union[str, Path],
    brain_dir: Optional[Union[str, Path]] = None,
) -> List[Tuple[str, Path]]:
    """Zwraca listę (conversationId, path) powiązanych subagentów (BFS z ochroną przed cyklami)."""
    root_path = Path(transcript_path).expanduser().resolve()
    root_id = _infer_session_id(root_path)
    b_dir = _infer_brain_dir(root_path, brain_dir=brain_dir)

    visited: Set[str] = {root_id}
    queue: deque[Path] = deque([root_path])
    found: List[Tuple[str, Path]] = []

    while queue:
        curr_path = queue.popleft()
        for cid in extract_subagent_ids(curr_path):
            if cid in visited:
                continue
            visited.add(cid)
            cand = b_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                found.append((cid, cand))
                queue.append(cand)

    return found


def aggregate_transcript_usage(transcript_path: Union[str, Path]) -> Tuple[UsageTotals, int]:
    """Sumuje wszystkie kroki tokenów w jednym pliku transcript.jsonl.

    Zwraca (UsageTotals, last_context_tokens).
    """
    path = Path(transcript_path).expanduser().resolve()
    if not path.is_file():
        raise TranscriptNotFoundError(f"Plik transkryptu nie istnieje: {path}")

    in_sum = 0
    cache_sum = 0
    out_sum = 0
    calls = 0
    peak_ctx = 0
    last_ctx = 0

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                extracted = _extract_usage_from_entry(data)
                if extracted is None:
                    continue
                in_tok, cache_tok, out_tok, _ = extracted
                in_sum += in_tok
                cache_sum += cache_tok
                out_sum += out_tok
                calls += 1
                ctx = in_tok + cache_tok
                last_ctx = ctx
                if ctx > peak_ctx:
                    peak_ctx = ctx
    except OSError as e:
        raise TelemetryDataError(f"Błąd wejścia/wyjścia podczas odczytu {path}: {e}") from e

    return (
        UsageTotals(
            input_tokens=in_sum,
            cache_read_tokens=cache_sum,
            output_tokens=out_sum,
            planner_calls=calls,
            peak_context_tokens=peak_ctx,
        ),
        last_ctx,
    )


def summarize_session_telemetry(
    transcript_path: Union[str, Path],
    brain_dir: Optional[Union[str, Path]] = None,
    limit: Optional[int] = None,
) -> SessionSummary:
    """Oblicza podsumowanie zużycia tokenów dla sesji głównej, jej subagentów i łącznie."""
    effective_limit = int(limit) if limit is not None else resolve_session_token_budget()
    path = Path(transcript_path).expanduser().resolve()
    main_totals, last_ctx = aggregate_transcript_usage(path)
    related = collect_related_subagents(path, brain_dir=brain_dir)

    sub_totals = UsageTotals()
    sub_ids: List[str] = []
    for cid, sub_path in related:
        sub_ids.append(cid)
        try:
            u, _ = aggregate_transcript_usage(sub_path)
            sub_totals = sub_totals.add(u)
        except TelemetryError:
            continue

    total_totals = main_totals.add(sub_totals)
    zone, _, _ = calculate_zone(last_ctx, limit=effective_limit)

    return SessionSummary(
        session_id=_infer_session_id(path),
        session_date=_extract_session_date(path),
        transcript_path=str(path),
        subagents_count=len(sub_ids),
        subagent_ids=tuple(sub_ids),
        main=main_totals,
        subagents=sub_totals,
        total=total_totals,
        limit=effective_limit,
        load_zone=zone,
    )


def resolve_master_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """Rozwiązuje ścieżkę do lab_master.db przez ConfigManager."""
    if db_path:
        return Path(db_path).expanduser().resolve()
    if ConfigManager is not None:
        try:
            cm = ConfigManager()
            db_room = cm.get_room_by_role("sqlite-relational")
            return cm.get_room_path(db_room.id, absolute=True) / "lab_master.db"
        except Exception:
            pass
    if _vault_root is not None:
        return _vault_root / "07 Bazy Danych" / "lab_master.db"
    return None


def save_session_telemetry(
    db_path: Union[str, Path],
    summary: SessionSummary,
    session_id_override: Optional[str] = None,
    session_number: int = 0,
) -> int:
    """Zapisuje podsumowanie sesji do session_telemetry (idempotentny UPSERT po session_id)."""
    from lab_core.schema import ensure_telemetry_columns, get_master_db_connection, init_master_db

    target_db = Path(db_path).expanduser().resolve()
    if not target_db.is_file():
        init_master_db(target_db)

    sid = session_id_override or summary.session_id
    now_iso = datetime.now(timezone.utc).isoformat()
    tokens_active = summary.main.peak_context_tokens

    conn = get_master_db_connection(target_db)
    try:
        with conn:
            ensure_telemetry_columns(conn)
            existing = conn.execute(
                "SELECT id FROM session_telemetry WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (sid,),
            ).fetchone()

            values = (
                sid,
                summary.session_date,
                int(session_number),
                int(tokens_active),
                int(summary.limit),
                summary.load_zone,
                now_iso,
                int(summary.main.input_tokens),
                int(summary.main.cache_read_tokens),
                int(summary.main.output_tokens),
                int(summary.main.planner_calls),
                int(summary.subagents_count),
                int(summary.subagents.input_tokens),
                int(summary.subagents.cache_read_tokens),
                int(summary.subagents.output_tokens),
                int(summary.subagents.planner_calls),
                int(summary.total.peak_context_tokens),
            )

            if existing is not None:
                row_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE session_telemetry SET
                        session_id = ?,
                        session_date = ?,
                        session_number = ?,
                        tokens_active = ?,
                        tokens_limit = ?,
                        load_zone = ?,
                        timestamp = ?,
                        input_tokens = ?,
                        cache_read_tokens = ?,
                        output_tokens = ?,
                        planner_calls = ?,
                        subagents_count = ?,
                        subagents_input_tokens = ?,
                        subagents_cache_read_tokens = ?,
                        subagents_output_tokens = ?,
                        subagents_planner_calls = ?,
                        peak_context_tokens = ?
                    WHERE id = ?
                    """,
                    (*values, row_id),
                )
                return row_id

            cur = conn.execute(
                """
                INSERT INTO session_telemetry (
                    session_id, session_date, session_number, tokens_active, tokens_limit,
                    load_zone, timestamp, input_tokens, cache_read_tokens, output_tokens,
                    planner_calls, subagents_count, subagents_input_tokens,
                    subagents_cache_read_tokens, subagents_output_tokens,
                    subagents_planner_calls, peak_context_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def _parse_report_range(
    days: int = 7,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    reference_date: Optional[date] = None,
) -> Tuple[str, str]:
    """Oblicza zakres dat [date_from, date_to] w formacie YYYY-MM-DD."""
    end_dt = reference_date or date.today()
    if date_to:
        try:
            end_dt = date.fromisoformat(date_to)
        except ValueError as e:
            raise TelemetryDataError(f"Niepoprawny format daty --to '{date_to}' (wymagane YYYY-MM-DD).") from e

    if date_from:
        try:
            start_dt = date.fromisoformat(date_from)
        except ValueError as e:
            raise TelemetryDataError(f"Niepoprawny format daty --from '{date_from}' (wymagane YYYY-MM-DD).") from e
    else:
        span = max(int(days), 1)
        start_dt = end_dt - timedelta(days=span - 1)

    if start_dt > end_dt:
        raise TelemetryDataError(f"Zakres dat jest niepoprawny: {start_dt} > {end_dt}.")
    return start_dt.isoformat(), end_dt.isoformat()


def generate_telemetry_report(
    brain_dir: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None,
    days: int = 7,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    reference_date: Optional[date] = None,
) -> TelemetryReport:
    """Generuje zestawienie dzienne oraz sumę za okres (sesje główne vs subagenci)."""
    start_str, end_str = _parse_report_range(
        days=days, date_from=date_from, date_to=date_to, reference_date=reference_date
    )

    b_dir = Path(brain_dir).expanduser().resolve() if brain_dir else (Path.home() / ".gemini" / "antigravity" / "brain")
    daily_buckets: Dict[str, Dict[str, Any]] = {}

    def _bucket(d_str: str) -> Dict[str, Any]:
        if d_str not in daily_buckets:
            daily_buckets[d_str] = {
                "sessions_count": 0,
                "subagents_count": 0,
                "main": UsageTotals(),
                "subagents": UsageTotals(),
            }
        return daily_buckets[d_str]

    seen_cids: Set[str] = set()

    if b_dir.is_dir():
        transcripts = sorted(b_dir.glob("*/.system_generated/logs/transcript.jsonl"))
        main_candidates: List[Path] = []
        sub_candidates: List[Path] = []
        for tr in transcripts:
            if not tr.is_file():
                continue
            if _is_subagent_transcript(tr):
                sub_candidates.append(tr)
            else:
                main_candidates.append(tr)

        for tr in main_candidates:
            cid = _infer_session_id(tr)
            if cid in seen_cids:
                continue
            try:
                summary = summarize_session_telemetry(tr, brain_dir=b_dir)
            except TelemetryError:
                continue
            seen_cids.add(cid)
            for sub_id in summary.subagent_ids:
                seen_cids.add(sub_id)
            if start_str <= summary.session_date <= end_str:
                b = _bucket(summary.session_date)
                b["sessions_count"] += 1
                b["subagents_count"] += summary.subagents_count
                b["main"] = b["main"].add(summary.main)
                b["subagents"] = b["subagents"].add(summary.subagents)

        # Osierocone transkrypty subagentów (których rodzic nie istnieje już w brain/)
        for tr in sub_candidates:
            cid = _infer_session_id(tr)
            if cid in seen_cids:
                continue
            seen_cids.add(cid)
            s_date = _extract_session_date(tr)
            if not (start_str <= s_date <= end_str):
                continue
            try:
                u, _ = aggregate_transcript_usage(tr)
            except TelemetryError:
                continue
            b = _bucket(s_date)
            b["subagents_count"] += 1
            b["subagents"] = b["subagents"].add(u)

    # Uzupełnienie historycznymi rekordami z session_telemetry (jeśli brak danego session_id w brain/)
    resolved_db = resolve_master_db_path(db_path)
    if resolved_db is not None and resolved_db.is_file():
        try:
            from lab_core.schema import ensure_telemetry_columns, get_master_db_connection
            conn = get_master_db_connection(resolved_db)
            try:
                ensure_telemetry_columns(conn)
                rows = conn.execute(
                    """
                    SELECT session_id, session_date, tokens_active,
                           COALESCE(input_tokens, 0) AS input_tokens,
                           COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                           COALESCE(output_tokens, 0) AS output_tokens,
                           COALESCE(planner_calls, 0) AS planner_calls,
                           COALESCE(subagents_count, 0) AS subagents_count,
                           COALESCE(subagents_input_tokens, 0) AS subagents_input_tokens,
                           COALESCE(subagents_cache_read_tokens, 0) AS subagents_cache_read_tokens,
                           COALESCE(subagents_output_tokens, 0) AS subagents_output_tokens,
                           COALESCE(subagents_planner_calls, 0) AS subagents_planner_calls,
                           COALESCE(peak_context_tokens, 0) AS peak_context_tokens
                    FROM session_telemetry
                    WHERE session_date >= ? AND session_date <= ?
                    """,
                    (start_str, end_str),
                ).fetchall()
                for row in rows:
                    sid = str(row["session_id"])
                    if sid in seen_cids:
                        continue
                    seen_cids.add(sid)
                    s_date = str(row["session_date"])
                    main_u = UsageTotals(
                        input_tokens=int(row["input_tokens"]),
                        cache_read_tokens=int(row["cache_read_tokens"]),
                        output_tokens=int(row["output_tokens"]),
                        planner_calls=int(row["planner_calls"]),
                        peak_context_tokens=int(row["tokens_active"] or row["peak_context_tokens"]),
                    )
                    sub_u = UsageTotals(
                        input_tokens=int(row["subagents_input_tokens"]),
                        cache_read_tokens=int(row["subagents_cache_read_tokens"]),
                        output_tokens=int(row["subagents_output_tokens"]),
                        planner_calls=int(row["subagents_planner_calls"]),
                        peak_context_tokens=int(row["peak_context_tokens"]),
                    )
                    b = _bucket(s_date)
                    b["sessions_count"] += 1
                    b["subagents_count"] += int(row["subagents_count"])
                    b["main"] = b["main"].add(main_u)
                    b["subagents"] = b["subagents"].add(sub_u)
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    day_rows: List[DailyTelemetryRow] = []
    tot_sessions = 0
    tot_subagents = 0
    tot_main = UsageTotals()
    tot_sub = UsageTotals()

    for d_str in sorted(daily_buckets.keys()):
        b = daily_buckets[d_str]
        m_u: UsageTotals = b["main"]
        s_u: UsageTotals = b["subagents"]
        t_u = m_u.add(s_u)
        day_rows.append(
            DailyTelemetryRow(
                date=d_str,
                sessions_count=b["sessions_count"],
                subagents_count=b["subagents_count"],
                main=m_u,
                subagents=s_u,
                total=t_u,
            )
        )
        tot_sessions += b["sessions_count"]
        tot_subagents += b["subagents_count"]
        tot_main = tot_main.add(m_u)
        tot_sub = tot_sub.add(s_u)

    return TelemetryReport(
        date_from=start_str,
        date_to=end_str,
        days=tuple(day_rows),
        sessions_count=tot_sessions,
        subagents_count=tot_subagents,
        main=tot_main,
        subagents=tot_sub,
        total=tot_main.add(tot_sub),
    )


def format_session_summary(summary: SessionSummary) -> str:
    """Formatuje zwięzłe podsumowanie zużycia sesji do terminala."""
    lines = [
        "=" * 64,
        "LAB 3.0 • PODSUMOWANIE ZUŻYCIA SESJI (GŁÓWNA + SUBAGENCI)",
        "=" * 64,
        f"Sesja ID       : {summary.session_id} ({summary.session_date})",
        f"Subagenci      : {summary.subagents_count}",
        "-" * 64,
        f"Sesja główna   : in={summary.main.input_tokens:,} | cache={summary.main.cache_read_tokens:,} | out={summary.main.output_tokens:,} | calls={summary.main.planner_calls} | peak={format_tokens(summary.main.peak_context_tokens)}",
        f"Subagenci ({summary.subagents_count:<2}) : in={summary.subagents.input_tokens:,} | cache={summary.subagents.cache_read_tokens:,} | out={summary.subagents.output_tokens:,} | calls={summary.subagents.planner_calls} | peak={format_tokens(summary.subagents.peak_context_tokens)}",
        f"Łącznie        : in={summary.total.input_tokens:,} | cache={summary.total.cache_read_tokens:,} | out={summary.total.output_tokens:,} | calls={summary.total.planner_calls} | peak={format_tokens(summary.total.peak_context_tokens)}",
        "=" * 64,
    ]
    return "\n".join(lines)


def format_telemetry_report(report: TelemetryReport) -> str:
    """Formatuje okresowy raport dzienny zużycia tokenów."""
    lines = [
        "=" * 72,
        f"LAB 3.0 • RAPORT TELEMETRII ({report.date_from} .. {report.date_to})",
        "=" * 72,
    ]
    if not report.days:
        lines.append("Brak zarejestrowanych sesji w podanym okresie.")
    else:
        for d in report.days:
            lines.append(
                f"{d.date} | sesje={d.sessions_count} sub={d.subagents_count} | "
                f"main(in={format_tokens(d.main.input_tokens)}, out={format_tokens(d.main.output_tokens)}, calls={d.main.planner_calls}) | "
                f"sub(in={format_tokens(d.subagents.input_tokens)}, out={format_tokens(d.subagents.output_tokens)}, calls={d.subagents.planner_calls}) | "
                f"sum_out={format_tokens(d.total.output_tokens)}"
            )
    lines.extend([
        "-" * 72,
        f"SUMA OKRESU    : sesje={report.sessions_count}, subagenci={report.subagents_count}",
        f"  Sesja główna : in={report.main.input_tokens:,} | cache={report.main.cache_read_tokens:,} | out={report.main.output_tokens:,} | calls={report.main.planner_calls} | peak={format_tokens(report.main.peak_context_tokens)}",
        f"  Subagenci    : in={report.subagents.input_tokens:,} | cache={report.subagents.cache_read_tokens:,} | out={report.subagents.output_tokens:,} | calls={report.subagents.planner_calls} | peak={format_tokens(report.subagents.peak_context_tokens)}",
        f"  Łącznie      : in={report.total.input_tokens:,} | cache={report.total.cache_read_tokens:,} | out={report.total.output_tokens:,} | calls={report.total.planner_calls} | peak={format_tokens(report.total.peak_context_tokens)}",
        "=" * 72,
    ])
    return "\n".join(lines)


# ==============================================================================
# HIGIENA WARSZTATU WSPÓŁPRACY (ZASADA CZYSTEGO STOŁU)
# ==============================================================================

def resolve_workshop_path(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Rozwiązuje ścieżkę do pliku Warsztatu Współpracy:
    Priorytet:
    1. custom_path (jeśli podano).
    2. core_config.ConfigManager (Aksjomat Nienaruszalności Rdzenia).
    3. Domyślna ścieżka skarbca '02 Warsztat/Dashboard LAB.md'.
    """
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        return p

    if ConfigManager is not None:
        try:
            cm = ConfigManager()
            workshop_room = cm.get_room("02")
            filename = workshop_room.entry_file or "Dashboard LAB.md"
            return cm.get_room_path(workshop_room.id, absolute=True) / filename
        except Exception:
            pass

    return _vault_root / "02 Warsztat" / "Dashboard LAB.md"


def clean_workshop_telemetry(workshop_path: Union[str, Path]) -> bool:
    """
    Deterministycznie usuwa wiersze telemetrii pamięci sesji z pliku Warsztatu:
    Zapewnia regułę 'Czysty Warsztat' – dynamiczna telemetria po każdym prompcie
    należy wyłącznie do stopki dialogu czatu i paska menu Raycasta,
    a nie do plików Warsztatu.
    Zwraca True, jeśli znaleziono i usunięto wiersze telemetrii, False w przeciwnym razie.
    """
    w_path = Path(workshop_path).expanduser().resolve()
    if not w_path.is_file():
        raise WorkshopUpdateError(f"Plik Warsztatu nie istnieje: {w_path}")

    try:
        content = w_path.read_text(encoding="utf-8")
    except OSError as e:
        raise WorkshopUpdateError(f"Nie udało się odczytać pliku {w_path}: {e}") from e

    if "Telemetria Pamięci Sesji" not in content:
        return False

    lines = content.splitlines()
    out = []
    removed = False
    for line in lines:
        if "🧠" in line and "Telemetria Pamięci Sesji" in line:
            removed = True
            continue
        out.append(line)

    if not removed:
        return False

    cleaned_text = "\n".join(out)
    cleaned_text = re.sub(r'(\n>[ \t]*)+\n---', '\n\n---', cleaned_text)
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)

    if content.endswith("\n") and not cleaned_text.endswith("\n"):
        cleaned_text += "\n"

    try:
        w_path.write_text(cleaned_text, encoding="utf-8")
    except OSError as e:
        raise WorkshopUpdateError(f"Nie udało się zapisać oczyszczonego Warsztatu {w_path}: {e}") from e

    return True


def update_workshop_file(
    workshop_path: Union[str, Path],
    metrics: Optional[TokenMetrics] = None,
    session_label: Optional[str] = None
) -> bool:
    """
    [WYCOFANE / OCHRONA CZYSTEGO WARSZTATU]
    Zgodnie ze standardem LAB telemetria NIE jest zapisywana w pliku Warsztatu.
    Wywołanie tej funkcji automatycznie oczyszcza plik Warsztatu z ewentualnych wierszy telemetrii.
    """
    clean_workshop_telemetry(workshop_path)
    return False


# ==============================================================================
# INTERFEJS WIERSZA POLECEŃ (CLI)
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentów CLI."""
    parser = argparse.ArgumentParser(
        description="Deterministyczna telemetria okna kontekstowego modelu sesji LAB 3.0 (Framework PZW)."
    )
    parser.add_argument(
        "--transcript-path",
        type=str,
        default=None,
        help="Ścieżka do konkretnego pliku transcript.jsonl. Domyślnie wyszukuje najnowszy w ~/.gemini/antigravity/brain.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"Limit wielkości okna kontekstowego w tokenach (domyślnie: [telemetry].session_token_budget lub {DEFAULT_WINDOW_LIMIT}).",
    )
    parser.add_argument(
        "--brain-dir",
        type=str,
        default=None,
        help="Niestandardowy katalog sesji brain (przydatny m.in. w testach jednostkowych).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Zwraca dane telemetrii w ustrukturyzowanym formacie JSON.",
    )
    parser.add_argument(
        "--statusline",
        action="store_true",
        dest="statusline_mode",
        help="Zwraca zwarty 1-linijkowy format dla paska menu / Raycasta (np. '🧠 109.4k / 150k (72.9%%) 🟢').",
    )
    parser.add_argument(
        "--clean-workshop",
        action="store_true",
        dest="clean_workshop",
        help="Usuwa wszelkie wiersze telemetrii z Warsztatu Współpracy (zasada Czystego Stołu).",
    )
    parser.add_argument(
        "--update-workshop",
        action="store_true",
        dest="update_workshop",
        help="[WYCOFANE] Wcześniejsza flaga - obecnie automatycznie oczyszcza Warsztat z telemetrii.",
    )
    parser.add_argument(
        "--workshop-path",
        type=str,
        default=None,
        help="Niestandardowa ścieżka do pliku Warsztatu Współpracy.",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Opcjonalny identyfikator/nazwa sesji (przestarzały).",
    )
    parser.add_argument(
        "--session-summary",
        action="store_true",
        dest="session_summary",
        help="Zwraca sumaryczne zużycie tokenów bieżącej sesji (główna + powiązani subagenci + łącznie).",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        dest="report_mode",
        help="Generuje dzienne zestawienie zużycia tokenów (sesje główne vs subagenci).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Liczba dni wstecz dla --report (domyślnie: 7).",
    )
    parser.add_argument(
        "--from",
        type=str,
        default=None,
        dest="date_from",
        help="Data początkowa YYYY-MM-DD dla --report.",
    )
    parser.add_argument(
        "--to",
        type=str,
        default=None,
        dest="date_to",
        help="Data końcowa YYYY-MM-DD dla --report.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        dest="db_path",
        help="Ścieżka do bazy lab_master.db (opcjonalnie).",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        dest="save_db",
        help="Zapisuje podsumowanie sesji (--session-summary) do tabeli session_telemetry.",
    )
    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Główny punkt wejścia CLI."""
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    try:
        if parsed_args.report_mode:
            report = generate_telemetry_report(
                brain_dir=parsed_args.brain_dir,
                db_path=parsed_args.db_path,
                days=parsed_args.days,
                date_from=parsed_args.date_from,
                date_to=parsed_args.date_to,
            )
            if parsed_args.json_mode:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(format_telemetry_report(report))
            return 0

        effective_limit = parsed_args.limit if parsed_args.limit is not None else resolve_session_token_budget()

        # 1. Lokalizacja pliku transkryptu + bezpieczny fallback przy braku transkryptu w CLI (np. Codex CLI)
        explicit_source = bool(parsed_args.transcript_path or parsed_args.brain_dir)
        transcript_file: Optional[Path] = None
        metrics: Optional[TokenMetrics] = None

        if parsed_args.transcript_path:
            transcript_file = Path(parsed_args.transcript_path).expanduser().resolve()
        else:
            try:
                transcript_file = find_latest_transcript(brain_dir=parsed_args.brain_dir)
            except TranscriptNotFoundError:
                if explicit_source or parsed_args.session_summary:
                    raise
                metrics = estimate_fallback_telemetry(limit=effective_limit)

        if parsed_args.session_summary:
            assert transcript_file is not None
            summary = summarize_session_telemetry(
                transcript_file,
                brain_dir=parsed_args.brain_dir,
                limit=effective_limit,
            )
            saved_row_id: Optional[int] = None
            if parsed_args.save_db:
                target_db = resolve_master_db_path(parsed_args.db_path)
                if target_db is not None:
                    saved_row_id = save_session_telemetry(target_db, summary)
            if parsed_args.json_mode:
                payload = summary.to_dict()
                if saved_row_id is not None:
                    payload["saved_row_id"] = saved_row_id
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(format_session_summary(summary))
            return 0

        # 2. Parsowanie telemetrii (z fallbackiem, jeśli brak metryk w domyślnym trybie autodetekcji)
        if metrics is None:
            assert transcript_file is not None
            try:
                metrics = parse_telemetry(transcript_file, limit=effective_limit)
            except (TranscriptNotFoundError, TelemetryDataError):
                if explicit_source:
                    raise
                metrics = estimate_fallback_telemetry(limit=effective_limit)

        # 3. Oczyszczanie pliku warsztatu (jeśli zażądano lub użyto wycofanej flagi)
        workshop_cleaned = False
        if parsed_args.clean_workshop or parsed_args.update_workshop:
            target_workshop = resolve_workshop_path(parsed_args.workshop_path)
            workshop_cleaned = clean_workshop_telemetry(target_workshop)

        # 4. Formatowanie wyjścia
        if parsed_args.json_mode:
            payload = metrics.to_dict()
            payload["workshop_cleaned"] = workshop_cleaned
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif parsed_args.statusline_mode:
            print(format_statusline(metrics))
        else:
            print(format_terminal_summary(metrics))
            if parsed_args.update_workshop:
                print("\n⚠️ Opcja --update-workshop została wycofana: telemetria żyje w dialogu i Raycaście, a nie w pliku Warsztatu.")
            if workshop_cleaned:
                print(f"🧹 Oczyszczono Warsztat z wierszy telemetrii: {resolve_workshop_path(parsed_args.workshop_path)}")

        return 0

    except TelemetryError as e:
        if parsed_args.json_mode:
            print(json.dumps({"error": str(e), "success": False}, ensure_ascii=False))
        else:
            print(f"❌ Błąd telemetrii LAB: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        if parsed_args.json_mode:
            print(json.dumps({"error": f"Nieoczekiwany błąd: {e}", "success": False}, ensure_ascii=False))
        else:
            print(f"❌ Nieoczekiwany błąd wykonania: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
