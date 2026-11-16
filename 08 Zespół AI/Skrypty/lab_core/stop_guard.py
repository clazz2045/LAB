#!/usr/bin/env python3
"""lab_core.stop_guard - Bramka prewencyjna Stop Hook w Antigravity (LAB).

Zabezpiecza środowisko LAB przed:
  1. Zamykaniem tury samą opowieścią bez faktycznego wywołania subagenta (Phantom Delegation).
  2. Samodzielnym pisaniem kodu/inżynierii przez Koordynatora w sesji głównej (Greedy Engineering).
  3. Brakiem obowiązkowej telemetrii tokenów w stopce odpowiedzi Koordynatora (Reguła 6).

Wywoływany synchronicznie przez harness platformy Antigravity na zakończenie pętli
wykonawczej (zdarzenie Stop).

Architektura:
  1. Fast-Path (<2 ms): Wykrywanie subagenta na podstawie nagłówka transkryptu (krok 0, SYSTEM)
     lub katalogu wiadomości wejściowych. Subagenci otrzymują natychmiastowe {"decision": "allow"}.
  2. Loop Breaker: Jeśli executionNum > 1 (harness już raz wymusił kontynuację w tej turze),
     zwracane jest allow, aby zapobiec zapętleniu (Infinite Loop Prevention).
  3. Automatyczna Telemetria: Oblicza najświeższe zużycie okna kontekstowego z transkryptu
     i zapisuje cache w /tmp/lab_session_telemetry.json oraz w katalogu sesji.
  4. Prewencja Monologu / Opowieści:
     - Wykrywa deklarację delegacji w tekście odpowiedzi przy braku wywołania invoke_subagent.
     - Wykrywa bloki implementacji kodu w sesji głównej bez udziału subagenta inżynieryjnego.
     - Weryfikuje obecność telemetrii tokenów w stopce wypowiedzi Koordynatora.
  5. Fail-Safe: Wszelkie błędy wejścia/wykonania są logowane do /tmp/lab_stop_guard_error.log,
     a skrypt bezpiecznie zwraca {"decision": "allow"}.

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import traceback
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

# Dołączenie katalogu skryptów do sys.path
_script_dir = Path(__file__).resolve().parent
_scripts_root = _script_dir.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from lab_core.telemetry import (
    DEFAULT_WINDOW_LIMIT,
    TokenMetrics,
    calculate_zone,
    format_statusline,
    format_tokens,
)
from lab_core.pre_tool_guard import is_subagent_session

LOG_FILE_PATH = Path("/tmp/lab_stop_guard_error.log")
CACHE_TELEMETRY_PATH = Path("/tmp/lab_session_telemetry.json")

# Wzorce deklaracji delegacji do subagenta w tekście wypowiedzi
PHANTOM_DELEGATION_PATTERNS = (
    re.compile(
        r"(?:zlecam|deleguj[ęe]|przekazuj[ęe]|uruchamiam|powo[łl]uj[ęe]|odpytuj[ęe]|prosz[ęe])"
        r"\s+(?:to\s+|zadanie\s+)?(?:do\s+)?(?:subagenta\s+)?(maksa|ady|klary|kingi|edyty|mai)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:subagent|agent)\s+(maks|ada|klara|kinga|edyta|maja)"
        r"\s+(?:bada|sprawdza|zajmie|przeanalizuje|przygotuje|wykona|otrzyma)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:wywo[łl]uj[ęe]|startuj[ęe])\s+(?:subagenta|agenta)",
        re.IGNORECASE,
    ),
    re.compile(
        r"dyspozycj[aa]\s+(?:do\s+)?(maksa|ady|klary|kingi|edyty|mai)",
        re.IGNORECASE,
    ),
)

# Wzorce bloków kodu implementacyjnego w sesji głównej
CODE_BLOCK_PATTERN = re.compile(r"```(?:python|py|bash|sh|zsh)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Sztywny wzorzec obecności telemetrii tokenów w wypowiedzi Koordynatora (z lab_core.telemetry)
STRICT_TELEMETRY_PATTERN = re.compile(r"🧠\s*\d+(?:\.\d+)?k?\s*/\s*250k\s*\(\d+(?:\.\d+)?%\)")
TELEMETRY_PRESENCE_PATTERNS = (
    STRICT_TELEMETRY_PATTERN,
)


def _log_error(msg: str) -> None:
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def extract_latest_token_metrics(transcript_path: Optional[str], limit: int = DEFAULT_WINDOW_LIMIT) -> Optional[TokenMetrics]:
    """Wyciąga najnowsze metryki tokenów z transkryptu sesji."""
    if not transcript_path:
        return None
    tp = Path(transcript_path).resolve()
    if not tp.is_file():
        return None

    last_entry: Optional[Dict[str, Any]] = None
    try:
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if isinstance(data, dict) and "input_tokens" in data:
                        last_entry = data
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        _log_error(f"Błąd odczytu tokenów z {tp}: {e}")
        return None

    if not last_entry:
        return None

    try:
        input_tokens = int(last_entry.get("input_tokens", 0))
        cache_read_tokens = int(last_entry.get("cache_read_tokens", 0))
        output_tokens = int(last_entry.get("output_tokens", 0))
        step_index = last_entry.get("step_index")
        if step_index is not None:
            step_index = int(step_index)
    except (ValueError, TypeError):
        return None

    total_context_tokens = input_tokens + cache_read_tokens
    percentage = round((total_context_tokens / limit) * 100, 2) if limit > 0 else 0.0
    zone, zone_icon, zone_display = calculate_zone(total_context_tokens, limit)

    return TokenMetrics(
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        output_tokens=output_tokens,
        total_context_tokens=total_context_tokens,
        limit=limit,
        percentage=percentage,
        zone=zone,
        zone_icon=zone_icon,
        zone_display=zone_display,
        step_index=step_index,
        transcript_path=str(tp),
    )


def save_telemetry_cache(metrics: TokenMetrics, artifact_dir: Optional[str] = None) -> None:
    """Zapisuje wyliczoną telemetrię do pliku cache dla narzędzi zewnętrznych."""
    data = metrics.to_dict()
    data["timestamp"] = datetime.now().isoformat()
    try:
        CACHE_TELEMETRY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    if artifact_dir:
        try:
            art_path = Path(artifact_dir).resolve()
            if art_path.is_dir():
                target = art_path / "telemetry.json"
                target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def inspect_turn_events(transcript_path: Optional[str]) -> Tuple[str, int, int]:
    """Analizuje kroki w ostatniej turze (od ostatniego USER_INPUT).

    Zwraca krotkę:
      (last_model_content, invoke_count, tool_calls_count)
    """
    if not transcript_path:
        return "", 0, 0
    tp = Path(transcript_path).resolve()
    if not tp.is_file():
        return "", 0, 0

    steps: List[Dict[str, Any]] = []
    try:
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    steps.append(json.loads(s))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        _log_error(f"Błąd analizy kroków transkryptu {tp}: {e}")
        return "", 0, 0

    if not steps:
        return "", 0, 0

    # Znajdź indeks ostatniego kroku użytkownika
    last_user_idx = -1
    for i in range(len(steps) - 1, -1, -1):
        st = steps[i]
        if st.get("source") == "USER_EXPLICIT" or st.get("type") == "USER_INPUT":
            last_user_idx = i
            break

    # Kroki należące do bieżącej tury (po ostatnim wejściu użytkownika)
    turn_steps = steps[last_user_idx + 1 :] if last_user_idx != -1 else steps

    last_model_content = ""
    invoke_count = 0
    tool_calls_count = 0

    for st in turn_steps:
        if st.get("source") == "MODEL":
            content = st.get("content")
            if content and isinstance(content, str):
                last_model_content = content

            t_calls = st.get("tool_calls")
            if isinstance(t_calls, list):
                tool_calls_count += len(t_calls)
                for tc in t_calls:
                    if isinstance(tc, dict):
                        name = tc.get("name") or tc.get("tool_name")
                        if name in ("invoke_subagent", "send_message"):
                            invoke_count += 1

    return last_model_content, invoke_count, tool_calls_count


def has_code_implementation_block(content: str) -> bool:
    """Sprawdza, czy treść zawiera blok kodu implementacyjnego (> 3 linijki kodu lub definicje)."""
    for match in CODE_BLOCK_PATTERN.finditer(content):
        code = match.group(1).strip()
        lines = [line for line in code.splitlines() if line.strip() and not line.strip().startswith("#")]
        if len(lines) >= 4:
            return True
        if any(line.strip().startswith(("def ", "class ", "import ", "from ")) for line in lines):
            return True
    return False


def has_phantom_delegation(content: str) -> bool:
    """Weryfikuje, czy tekst deklaruje zlecenie subagentowi bez jego wywołania."""
    for pattern in PHANTOM_DELEGATION_PATTERNS:
        if pattern.search(content):
            return True
    return False


def has_token_telemetry_signature(content: str) -> bool:
    """Sprawdza obecność sygnatury telemetrii tokenów w treści."""
    return any(pattern.search(content) for pattern in TELEMETRY_PRESENCE_PATTERNS)


_CHECKPOINT_TEXT_RE = re.compile(
    r"(?:^|\s)/?checkpoint\b|zako[ńn]cz(?:enie)?\s+sesj|zamknij\s+sesj|zamkni[ęe]cie\s+sesj",
    re.IGNORECASE,
)
_CHECKPOINT_TOOL_RE = re.compile(r"checkpoint|Dzienniki\s+Sesji", re.IGNORECASE)
_GATE_MAKS_CODE_RE = re.compile(
    r"subagent_gate(?:\.py)?\b(?=.*--agent\s+[\"']?(?:maks|engineer)[\"']?)(?=.*--task-type\s+[\"']?code[\"']?)",
    re.IGNORECASE | re.DOTALL,
)
_GATE_ADA_AUDIT_RE = re.compile(
    r"subagent_gate(?:\.py)?\b(?=.*--agent\s+[\"']?(?:ada|governance_qa)[\"']?)(?=.*--task-type\s+[\"']?audit[\"']?)",
    re.IGNORECASE | re.DOTALL,
)


def _nfc_lower(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip().lower()


def _load_transcript_steps(transcript_path: Optional[str]) -> List[Dict[str, Any]]:
    if not transcript_path:
        return []
    tp = Path(transcript_path).resolve()
    if not tp.is_file():
        return []
    steps: List[Dict[str, Any]] = []
    try:
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    data = json.loads(s)
                    if isinstance(data, dict):
                        steps.append(data)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        _log_error(f"Błąd odczytu transkryptu {tp}: {e}")
    return steps


def is_checkpoint_or_session_end_turn(transcript_path: Optional[str]) -> bool:
    """Wykrywa, czy bieżąca tura dotyczy procedury /checkpoint lub zamknięcia sesji."""
    steps = _load_transcript_steps(transcript_path)
    if not steps:
        return False

    last_user_idx = -1
    for i in range(len(steps) - 1, -1, -1):
        st = steps[i]
        if st.get("source") == "USER_EXPLICIT" or st.get("type") == "USER_INPUT":
            last_user_idx = i
            break

    turn_steps = steps[last_user_idx:] if last_user_idx != -1 else steps
    for st in turn_steps:
        src = st.get("source")
        if src in ("USER_EXPLICIT", "MODEL") or st.get("type") == "USER_INPUT":
            content = str(st.get("content") or "")
            if _CHECKPOINT_TEXT_RE.search(content):
                return True
        if src == "MODEL":
            for tc in st.get("tool_calls") or []:
                if isinstance(tc, dict):
                    raw_tc = json.dumps(tc, ensure_ascii=False)
                    if _CHECKPOINT_TOOL_RE.search(raw_tc):
                        return True
    return False


def _parse_iso_ts(ts_str: str) -> Optional[float]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _transcript_has_unverified_maks_code(transcript_path: Optional[str]) -> bool:
    """Fallback na transkrypt: szuka wywołań subagent_gate Maks/code bez późniejszego Ada/audit."""
    steps = _load_transcript_steps(transcript_path)
    pending = False
    for st in steps:
        if st.get("source") != "MODEL":
            continue
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            raw_tc = json.dumps(tc, ensure_ascii=False)
            if _GATE_MAKS_CODE_RE.search(raw_tc):
                pending = True
            elif _GATE_ADA_AUDIT_RE.search(raw_tc):
                pending = False
    return pending


def has_unverified_maks_code(
    vault_root: Optional[Path] = None,
    transcript_path: Optional[str] = None,
) -> bool:
    """Sprawdza, czy w bieżącej sesji/dniu jest Maks/code (ACCEPTED) bez późniejszego Ada/audit (ACCEPTED)."""
    resolved_root: Optional[Path] = vault_root
    if resolved_root is None:
        try:
            from lab_core.env import find_vault_root
            resolved_root = find_vault_root(_scripts_dir)
        except Exception:
            resolved_root = None

    if resolved_root is not None:
        db_path: Optional[Path] = None
        logs_dir: Optional[Path] = None
        maks_name = "maks"
        ada_name = "ada"
        try:
            from lab_core.config import ConfigManager
            cfg = ConfigManager(vault_root=resolved_root)
            db_path = resolved_root / cfg.get_room_by_role("sqlite-relational").path / "lab_master.db"
            logs_dir = resolved_root / cfg.get_room_by_role("governance-and-engine").path / "Dzienniki Sesji"
            maks_name = _nfc_lower(cfg.get_agent_name("engineer"))
            ada_name = _nfc_lower(cfg.get_agent_name("governance_qa"))
        except Exception:
            cand_db = resolved_root / "07 Bazy Danych" / "lab_master.db"
            if cand_db.is_file():
                db_path = cand_db

        if db_path is not None and db_path.is_file():
            threshold_ts = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
            if logs_dir is not None and logs_dir.is_dir():
                mtimes: List[float] = []
                for p in logs_dir.rglob("*.md"):
                    if not p.name.startswith("."):
                        try:
                            mtimes.append(p.stat().st_mtime)
                        except OSError:
                            pass
                if mtimes:
                    threshold_ts = max(mtimes)

            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    tbl = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_dispatches';"
                    ).fetchone()
                    if tbl:
                        rows = conn.execute(
                            "SELECT id, agent_name, task_type, dispatch_time "
                            "FROM subagent_dispatches WHERE status = 'accepted' ORDER BY id ASC;"
                        ).fetchall()
                        session_rows: List[Tuple[float, int, str, str]] = []
                        for row in rows:
                            ts_val = _parse_iso_ts(str(row["dispatch_time"] or ""))
                            if ts_val is not None and ts_val >= threshold_ts:
                                session_rows.append((
                                    ts_val,
                                    int(row["id"]),
                                    _nfc_lower(str(row["agent_name"] or "")),
                                    str(row["task_type"] or "").strip().lower(),
                                ))
                        session_rows.sort(key=lambda item: (item[0], item[1]))
                        pending = False
                        saw_maks_code = False
                        for _, _, agent, task_type in session_rows:
                            if agent == maks_name and task_type == "code":
                                pending = True
                                saw_maks_code = True
                            elif agent == ada_name and task_type == "audit":
                                pending = False
                        if saw_maks_code:
                            return pending
                finally:
                    conn.close()
            except Exception as e:
                _log_error(f"Błąd odczytu subagent_dispatches w stop_guard: {e}")

    return _transcript_has_unverified_maks_code(transcript_path)


def evaluate_stop(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Główna funkcja ewaluująca zdarzenie Stop."""
    transcript_path = payload.get("transcriptPath")
    artifact_dir = payload.get("artifactDirectoryPath")
    execution_num = int(payload.get("executionNum", 1))
    termination_reason = str(payload.get("terminationReason", "NO_TOOL_CALL"))

    # 1. Fast-Path: Subagent ma pełną swobodę zamykania swoich tur
    if is_subagent_session(transcript_path):
        return {"decision": "allow"}

    # 2. Jeśli zakończenie nastąpiło z powodu błędu, anulowania lub limitu, nie blokujemy
    if termination_reason not in ("NO_TOOL_CALL", "TERMINAL_STEP_TYPE"):
        return {"decision": "allow"}

    # 3. Obliczenie i zapisanie telemetrii tokenów
    metrics = extract_latest_token_metrics(transcript_path)
    if metrics:
        save_telemetry_cache(metrics, artifact_dir)
        statusline = format_statusline(metrics)
    else:
        statusline = "🧠 Telemetria okna niedostępna"

    # 4. Loop Breaker: Jeśli harness już raz wymusił kontynuację w tej turze, pozwalamy na stop
    if execution_num > 1:
        return {"decision": "allow"}

    # 5. Analiza zdarzeń tury Koordynatora
    content, invoke_count, tool_calls_count = inspect_turn_events(transcript_path)

    # Jeśli brak treści odpowiedzi lub wywołano subagenta, nie blokujemy
    if not content.strip():
        return {"decision": "allow"}

    # 6. Wektor A: Phantom Delegation (deklaracja delegacji bez wywołania invoke_subagent)
    if invoke_count == 0 and has_phantom_delegation(content):
        reason = (
            "LAB GOVERNANCE GUARD (Reguła 8 - Stop Hook): Wykryto deklarację delegacji do subagenta "
            "w tekście odpowiedzi bez faktycznego wywołania narzędzia `invoke_subagent` w tej turze! "
            "Zgodnie z Regułą 8 masz bezwzględny zakaz zamykania tury samą opowieścią. "
            "Wywołaj narzędzie `invoke_subagent` (z rolą i promptem), aby przekazać zadanie."
        )
        return {"decision": "continue", "reason": reason}

    # 7. Wektor B: Greedy Engineering (pisanie kodu produkcyjnego w sesji głównej)
    if invoke_count == 0 and has_code_implementation_block(content):
        reason = (
            "LAB GOVERNANCE GUARD (Reguła 8 - Stop Hook): Koordynator nie ma prawa samodzielnie pisać kodu "
            "ani realizować zadań inżynieryjnych w sesji głównej. Oddeleguj to zadanie do właściwego "
            "subagenta (Maks / Kinga / Ada) przez `invoke_subagent`."
        )
        return {"decision": "continue", "reason": reason}

    # 7b. Wektor D: Bramka sekwencyjna Maks/code -> Ada/audit (wyłącznie przy zamykaniu sesji / checkpoincie,
    # gdy w turze nie wywołano subagenta — brak false-positive podczas oczekiwania na subagenta)
    if invoke_count == 0 and is_checkpoint_or_session_end_turn(transcript_path):
        vault_root_raw = payload.get("vaultRoot")
        v_root = Path(vault_root_raw).resolve() if vault_root_raw else None
        if has_unverified_maks_code(vault_root=v_root, transcript_path=transcript_path):
            reason = (
                "LAB GOVERNANCE GUARD (Reguła 8 - Bramka sekwencyjna Stop Hook): W bieżącej sesji "
                "zarejestrowano wpis Maks/code (ACCEPTED) bez późniejszego zatwierdzonego audytu Ada/audit. "
                "Przed zakończeniem sesji (/checkpoint) zleć audyt Adzie przez `invoke_subagent` "
                "i zarejestruj destylat przez `subagent_gate.py`."
            )
            return {"decision": "continue", "reason": reason}

    # 8. Wektor C: Obowiązkowa telemetria tokenów w stopce odpowiedzi Koordynatora (Reguła 6)
    if not has_token_telemetry_signature(content):
        reason = (
            f"LAB GOVERNANCE GUARD (Reguła 6 - Stop Hook): Brak lub niepoprawny format telemetrii tokenów "
            f"w stopce wypowiedzi Koordynatora (wymagany wzorzec: `🧠 <X>k / 250k (<Y>%) <ikona>`). "
            f"Uzupełnij stopkę odpowiedzi o aktualny statusline z `lab_core.telemetry`:\n\n{statusline}"
        )
        return {"decision": "continue", "reason": reason}

    return {"decision": "allow"}


def main() -> int:
    """Punkt wejścia CLI dla hooka Stop w Antigravity."""
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"decision": "allow"}))
            return 0

        payload = json.loads(raw_input)
        if not isinstance(payload, dict):
            print(json.dumps({"decision": "allow"}))
            return 0

        result = evaluate_stop(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        _log_error(f"Krytyczny błąd stop_guard: {e}\n{traceback.format_exc()}")
        # Fail-Safe: Zawsze allow przy awarii, aby nie zablokować środowiska
        print(json.dumps({"decision": "allow"}))
        return 0


if __name__ == "__main__":
    import traceback
    sys.exit(main())
