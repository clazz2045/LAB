#!/usr/bin/env python3
"""lab_core.pre_tool_guard - Bramka prewencyjna Poziomu 1 (PreToolUse Hook) w Antigravity.

Zabezpiecza środowisko LAB przed naruszeniem Reguły 8 (twardy zakaz bezpośredniej edycji kodu
i bazy wiedzy oraz zakaz masowego zwiadu powłokowego w sesji głównej Koordynatora).
Wywoływany synchronicznie przez harness platformy Antigravity przed uruchomieniem narzędzi:
  - run_command
  - read_file
  - write_file
  - edit_file
  - invoke_subagent

Architektura:
  1. Fast-Path (<2 ms): Wykrywanie subagenta na podstawie nagłówka transkryptu (krok 0, SYSTEM)
     lub katalogu wiadomości wejściowych. Subagenci otrzymują natychmiastowe {"decision": "allow"}.
  2. Koordynator (Sesja Główna):
     - invoke_subagent: Wymuszenie jawnego parametru Model (ochrona budżetu i okna 250k).
       Dla 'research' dozwolone wyłącznie 'flash' / 'flash_lite'.
       Dla 'self' dozwolone jawne 'flash', 'flash_lite', 'inherit', 'pro'.
     - read_file / view_file: Ścisła biała lista ścieżek (Zero Code Reading in Main:
       bezwzględna blokada plików .py, katalogów tests/*, konfiguracji i baz danych; dozwolone wyłącznie
       Agenda Robocza, Dzienniki Sesji, notatki Warsztatu, pliki zarządcze projektów/operacji
       (progress-report.md, PLAN.md, README.md, STATUS.md) oraz pliki sesji/brain/tmp) oraz twardy licznik
       w czasie rzeczywistym ograniczający serie odczytów w sesji głównej do max 2 przed delegacją (Stop & Ask).
     - write_file / edit_file: Dopuszczone tylko pliki Agendy, Dashboardu i sesji (brain/tmp).
       Wszystkie pliki produkcyjne (kod, baza wiedzy, projekty, operacje, zasoby) są bezwzględnie blokowane.
     - run_command: Dopuszczone tylko polecenia statusowe oraz skrypty koordynacyjne:
       session_bootstrap.py, checkpoint.py, rollover_agenda.py, subagents.py,
       telemetry.py, check_quality.py, statusowe zapytania sqlite3 do lab_master.db, git status.
       Narzędzia zwiadu powłokowego (grep, find, cat, head, tail, awk, sed, rg) oraz modyfikacji
       są bezwzględnie blokowane.
  3. Fail-Safe: Wszelkie nieoczekiwane wyjątki są przechwytywane, rejestrowane w
     /tmp/lab_pre_tool_guard_error.log i zwracają awaryjne allow (ochrona przed deadlockiem).

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
import tomllib
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

# Narzędzia podlegające kontroli
READ_TOOLS = frozenset({
    "read_file",
    "view_file",
})

WRITE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "replace_file_content",
    "multi_replace_file_content",
    "create_file",
    "delete_file",
    "apply_patch",
    "patch_file",
    "write",
    "edit",
})

SHELL_TOOLS = frozenset({
    "run_command",
    "execute_command",
    "bash",
    "shell",
    "shell_command",
    "exec_command",
    "local_shell",
})

SUBAGENT_TOOLS = frozenset({
    "invoke_subagent",
})

ALLOWED_SUBAGENT_MODELS = frozenset({
    "flash",
    "flash_lite",
    "inherit",
    "pro",
})

RESEARCH_ALLOWED_MODELS = frozenset({
    "flash",
    "flash_lite",
})

RECON_COMMANDS = frozenset({
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "awk",
    "sed",
    "less",
    "more",
    "wc",
})

ALLOWED_PYTHON_SCRIPTS = frozenset({
    "session_bootstrap.py",
    "checkpoint.py",
    "rollover_agenda.py",
    "subagents.py",
    "telemetry.py",
    "check_quality.py",
})

ALLOWED_PYTHON_MODULES = frozenset({
    "lab_core.telemetry",
    "lab_core.subagents",
})

ALLOWED_GIT_SUBCOMMANDS = frozenset({
    "status",
    "branch",
    "diff",
    "log",
    "rev-parse",
})

ALLOWED_SHELL_STATUS_EXES = frozenset({
    "date",
    "pwd",
    "whoami",
    "echo",
})

DENY_REASON_WRITE = (
    "BLOKADA REGUŁY 8: Koordynator nie ma prawa edytować plików produkcyjnych, kodu ani bazy wiedzy w sesji głównej."
)

DENY_REASON_RECON = (
    "BLOKADA REGUŁY 8: Koordynator ma zakaz przeszukiwania repozytorium w sesji głównej. "
    "Oddeleguj zadanie do Klary."
)

DENY_REASON_COMMAND = (
    "BLOKADA REGUŁY 8: Polecenie niedozwolone dla Koordynatora w sesji głównej."
)

DENY_REASON_SUBAGENT_MISSING_MODEL = (
    "BLOKADA REGUŁY 8: Wymagane jest jawne podanie parametru 'Model' przy wywołaniu invoke_subagent."
)

DENY_REASON_RESEARCH_MODEL = (
    "BLOKADA REGUŁY 8: Dla subagenta 'research' dopuszczalne są wyłącznie modele 'flash' lub 'flash_lite' (zakaz 'inherit' i 'pro')."
)

COORDINATOR_READ_LIMIT = 2

DENY_REASON_READ_LIMIT = (
    "BLOKADA REGUŁY 8: Wykorzystano limit 2 odczytów w sesji głównej. "
    "Zastosuj zasadę Stop & Ask: zadaj pytanie człowiekowi na czacie lub zdeleguj zwiad do subagenta."
)

DENY_REASON_READ_CODE = (
    "BLOKADA REGUŁY 8: Koordynator nie ma uprawnień do inspekcji kodu w sesji głównej. "
    "Zdeleguj to zadanie do właściwego subagenta (Maks/Klara)."
)

ALLOWED_MANAGEMENT_FILES = frozenset({
    "progress-report.md",
    "progress_report.md",
    "plan.md",
    "readme.md",
    "status.md",
})

DENY_REASON_READ_WHITELIST = (
    "BLOKADA REGUŁY 8: Koordynator może czytać wyłącznie pliki Agendy Roboczej, Dzienników Sesji, "
    "Warsztatu oraz pliki zarządcze projektów/operacji (progress-report.md, PLAN.md, README.md, STATUS.md). "
    "Zdeleguj zwiad do właściwego subagenta (Maks/Klara/Edyta)."
)

READ_STATE_FILE = Path(tempfile.gettempdir()) / "lab_coordinator_read_state.json"

LOG_FILE_PATH = Path("/tmp/lab_pre_tool_guard_error.log")


def _log_error(msg: str) -> None:
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def find_vault_root_fast() -> Path:
    """Zwraca katalog główny skarbca LAB na podstawie położenia pliku."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "lab.config.toml").is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path.cwd().resolve()


def is_subagent_session(transcript_path: Optional[str]) -> bool:
    """Sprawdza, czy bieżąca sesja to wyizolowany subagent (nie Koordynator).

    Wydajność: odczyt pojedynczej linii pliku (<1 ms).
    Kryteria:
      1. Krok 0 transkryptu ma source == 'SYSTEM' oraz treść zawiera '[Message]' i 'sender='.
      2. Lub katalog .system_generated/messages zawiera pliki komunikatów przychodzących.
    """
    if not transcript_path:
        return False
    try:
        tp = Path(transcript_path).resolve()
        if tp.is_file():
            with open(tp, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline().strip()
            if first_line:
                step = json.loads(first_line)
                if step.get("source") == "SYSTEM":
                    content = str(step.get("content", ""))
                    if "[Message]" in content and "sender=" in content:
                        return True

        messages_dir = tp.parent.parent / "messages"
        if messages_dir.is_dir():
            for item in messages_dir.iterdir():
                if item.is_file() and item.suffix == ".json" and item.name != "read.json":
                    return True
    except Exception:
        pass

    return False


def normalize_target_path(raw_path: str, vault_root: Path) -> Path:
    p = Path(os.path.expanduser(raw_path.strip()))
    if not p.is_absolute():
        p = (vault_root / p).resolve()
    else:
        p = p.resolve()
    return p


def is_allowed_coordinator_write(raw_path: str, vault_root: Path) -> bool:
    """Weryfikuje, czy plik może być zmodyfikowany przez Koordynatora w sesji głównej."""
    if not raw_path or not raw_path.strip():
        return False

    root = vault_root.resolve()
    p = normalize_target_path(raw_path, root)
    p_str = str(p)
    parts = set(p.parts)

    # 1. Sesja / brain / tmp / logi
    if "brain" in parts or ".system_generated" in parts:
        return True
    if (
        p_str.startswith("/tmp")
        or p_str.startswith("/private/tmp")
        or p_str.startswith(tempfile.gettempdir())
        or "tmp" in parts
    ):
        return True

    # 2. Agenda Robocza w Warsztacie
    if ("Agenda Robocza" in p.name or "Agenda_Robocza" in p.name) and (
        "02 Warsztat" in parts or "Warsztat" in parts or p.parent.name.endswith("Warsztat")
    ):
        return True

    # 3. Dashboard w Warsztacie (niezależnie od nazwy instancji)
    if (p.name in ("Dashboard LAB.md", "Dashboard_LAB.md") or (p.name.startswith("Dashboard ") and p.suffix.lower() == ".md")) and (
        "02 Warsztat" in parts or "Warsztat" in parts or p.parent.name.endswith("Warsztat")
    ):
        return True

    # Wszelki zapis do kodu, bazy wiedzy, projektów jest zakazany w sesji głównej
    return False


def is_internal_session_file(raw_path: str, vault_root: Path) -> bool:
    """Sprawdza, czy plik jest wewnętrznym plikiem sesji / harnessu (nie zużywa limitu odczytów)."""
    p = normalize_target_path(raw_path, vault_root)
    p_str = str(p)
    parts = set(p.parts)
    return (
        "brain" in parts
        or ".system_generated" in parts
        or p_str.startswith("/tmp")
        or p_str.startswith("/private/tmp")
        or p_str.startswith(tempfile.gettempdir())
        or "tmp" in parts
    )


def is_in_projects_or_operations(p: Path, vault_root: Path) -> bool:
    """Sprawdza, czy ścieżka znajduje się wewnątrz Pokoju Projektów (03) lub Operacji (04).

    Rygor bezpieczeństwa:
      1. Ścieżka musi leżeć wewnątrz vault_root.
      2. Wymagane co najmniej 2 segmenty relatywne (pokój/plik...).
      3. Pierwszy segment rel.parts[0] musi dokładnie odpowiadać katalogowi pokoju 03 lub 04.
    """
    try:
        p_res = p.resolve()
        root_res = vault_root.resolve()
        if not p_res.is_relative_to(root_res):
            return False

        rel = p_res.relative_to(root_res)
        if len(rel.parts) < 2:
            return False

        allowed_room_dirs = {"03 Projekty", "04 Operacje"}
        cfg_file = root_res / "lab.config.toml"
        if cfg_file.is_file():
            try:
                with open(cfg_file, "rb") as f:
                    cfg_data = tomllib.load(f)
                rooms = cfg_data.get("rooms", {})
                for r_key, r_val in rooms.items():
                    if isinstance(r_val, dict):
                        role = r_val.get("role") or ""
                        path_str = r_val.get("path") or r_val.get("name") or ""
                        if role in ("active-projects", "lifecycle-routines") or r_key in ("r03_projects", "r04_operations"):
                            if path_str:
                                allowed_room_dirs.add(path_str)
            except Exception:
                pass

        return rel.parts[0] in allowed_room_dirs
    except Exception:
        return False


def is_allowed_coordinator_read(raw_path: str, vault_root: Path) -> Tuple[bool, Optional[str]]:
    """Weryfikuje, czy plik może być odczytany przez Koordynatora w sesji głównej."""
    if not raw_path or not raw_path.strip():
        return False, DENY_REASON_READ_WHITELIST

    root = vault_root.resolve()
    p = normalize_target_path(raw_path, root)
    p_str = str(p)
    parts = set(p.parts)

    # 1. Zero Code Reading in Main: Twarda blokada kodu .py oraz katalogów testów
    if p.suffix == ".py" or p.name.endswith(".py") or "tests" in parts:
        return False, DENY_REASON_READ_CODE

    # 2. Blokada baz danych i plików konfiguracyjnych poza dozwolonymi
    if p.suffix in (".db", ".sqlite", ".sqlite3") or p.name in ("lab.config.toml", "config.toml"):
        return False, DENY_REASON_READ_WHITELIST

    # 3. Sesja / brain / tmp / logi harnessu
    if "brain" in parts or ".system_generated" in parts:
        return True, None
    if (
        p_str.startswith("/tmp")
        or p_str.startswith("/private/tmp")
        or p_str.startswith(tempfile.gettempdir())
        or "tmp" in parts
    ):
        return True, None

    # 4. Agenda Robocza w Warsztacie
    if ("Agenda Robocza" in p.name or "Agenda_Robocza" in p.name) and (
        "02 Warsztat" in parts or "Warsztat" in parts or p.parent.name.endswith("Warsztat")
    ):
        return True, None

    # 5. Dzienniki Sesji (w 08 Zespół AI lub pokoju zarządzania)
    if "Dzienniki Sesji" in parts or "Dzienniki_Sesji" in parts:
        return True, None

    # 6. Dashboard i notatki robocze w Warsztacie
    if "02 Warsztat" in parts or "Warsztat" in parts or p.parent.name.endswith("Warsztat"):
        return True, None

    # 7. Pliki zarządcze i statusowe w Pokoju Projektów (03) oraz Operacji (04)
    if p.name.lower() in ALLOWED_MANAGEMENT_FILES:
        if is_in_projects_or_operations(p, root):
            return True, None

    # Wszystkie inne ścieżki (Baza Wiedzy, Zasoby, Skrypty, korzeń skarbca oraz pliki robocze projektów/operacji) są blokowane dla Koordynatora
    return False, DENY_REASON_READ_WHITELIST


def get_read_state(state_file: Optional[Path] = None) -> Dict[str, Any]:
    target = state_file or READ_STATE_FILE
    if target.is_file():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_read_state(state: Dict[str, Any], state_file: Optional[Path] = None) -> None:
    target = state_file or READ_STATE_FILE
    try:
        target.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def reset_coordinator_read_counter(
    transcript_path: Optional[str] = None,
    state_file: Optional[Path] = None,
) -> None:
    state = get_read_state(state_file)
    state["consecutive_reads"] = 0
    state["last_reset_time"] = time.time()
    if transcript_path:
        state["transcript_path"] = str(transcript_path)
    save_read_state(state, state_file)


def check_and_increment_read_counter(
    transcript_path: Optional[str] = None,
    state_file: Optional[Path] = None,
) -> Tuple[bool, Optional[str]]:
    """Sprawdza i inkrementuje licznik odczytów dla Koordynatora w sesji głównej."""
    state = get_read_state(state_file)
    current_tp = str(transcript_path) if transcript_path else state.get("transcript_path", "")

    # Nowa sesja (zmiana transkryptu) -> reset
    if current_tp and state.get("transcript_path") and state.get("transcript_path") != current_tp:
        state["consecutive_reads"] = 0
        state["last_user_step_idx"] = -1
        state["transcript_path"] = current_tp

    # Sprawdzenie nowego wejścia użytkownika w transkrypcie
    if current_tp and Path(current_tp).is_file():
        try:
            latest_user_idx = -1
            with open(current_tp, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        step = json.loads(s)
                        if step.get("source") == "USER_EXPLICIT" or step.get("type") == "USER_INPUT":
                            latest_user_idx = idx
                    except Exception:
                        pass
            if latest_user_idx >= 0 and latest_user_idx > state.get("last_user_step_idx", -1):
                state["consecutive_reads"] = 0
                state["last_user_step_idx"] = latest_user_idx
        except Exception:
            pass

    count = int(state.get("consecutive_reads", 0))
    if count >= COORDINATOR_READ_LIMIT:
        return False, DENY_REASON_READ_LIMIT

    state["consecutive_reads"] = count + 1
    state["transcript_path"] = current_tp
    state["last_read_time"] = time.time()
    save_read_state(state, state_file)
    return True, None


def is_allowed_coordinator_command(cmd_str: str) -> Tuple[bool, Optional[str]]:
    """Weryfikuje polecenie powłoki dla Koordynatora w sesji głównej."""
    if not cmd_str or not cmd_str.strip():
        return True, None

    # Ochrona przed podstawianiem poleceń ($() oraz backtick)
    if "$(" in cmd_str or "`" in cmd_str:
        return False, DENY_REASON_COMMAND

    try:
        s = shlex.shlex(cmd_str, posix=True, punctuation_chars=True)
        s.whitespace_split = True
        tokens = list(s)
    except Exception:
        return False, DENY_REASON_COMMAND

    segments: List[List[str]] = []
    current: List[str] = []
    for t in tokens:
        if t in (";", "&&", "||", "|", "&"):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(t)
    if current:
        segments.append(current)

    if not segments:
        return True, None

    for seg in segments:
        if not seg:
            continue

        while seg:
            tok = seg[0].strip("'\"")
            if tok in ("sudo", "env", "time", "nohup") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
                seg = seg[1:]
            else:
                break
        if not seg:
            continue

        exe_raw = seg[0].strip("'\"")
        exe = Path(exe_raw).name.lower()

        # Blokada narzędzi zwiadu powłokowego
        if exe in RECON_COMMANDS:
            return False, DENY_REASON_RECON

        # Ochrona przed wywołaniem narzędzi zwiadu przez xargs
        if exe == "xargs":
            for arg in seg[1:]:
                if not arg.startswith("-") and Path(arg.strip("'\"")).name.lower() in RECON_COMMANDS:
                    return False, DENY_REASON_RECON

        # Obsługa poleceń Pythona
        if exe in ("python", "python3") or exe.startswith("python3."):
            has_allowed_script = False
            idx = 1
            while idx < len(seg):
                arg = seg[idx].strip("'\"")
                if arg in ("-W", "-X", "-Q"):
                    idx += 2
                    continue
                if arg == "-m":
                    if idx + 1 < len(seg):
                        mod_name = seg[idx + 1].strip("'\"")
                        if mod_name in ALLOWED_PYTHON_MODULES:
                            has_allowed_script = True
                            break
                    idx += 2
                    continue
                if arg.startswith("-"):
                    idx += 1
                    continue
                script_name = Path(arg).name
                if script_name in ALLOWED_PYTHON_SCRIPTS:
                    has_allowed_script = True
                    break
                idx += 1
            if not has_allowed_script:
                return False, DENY_REASON_COMMAND
            continue

        # Obsługa poleceń sqlite3
        if exe == "sqlite3":
            has_master_db = any("lab_master.db" in arg for arg in seg[1:])
            if has_master_db:
                continue
            return False, DENY_REASON_COMMAND

        # Obsługa poleceń git
        if exe == "git":
            subcmd = None
            for arg in seg[1:]:
                if not arg.startswith("-"):
                    subcmd = arg.strip("'\"")
                    break
            if subcmd in ALLOWED_GIT_SUBCOMMANDS:
                continue
            return False, DENY_REASON_COMMAND

        # Bezpieczne polecenia systemowe statusu
        if exe in ALLOWED_SHELL_STATUS_EXES:
            continue

        return False, DENY_REASON_COMMAND

    return True, None


def is_allowed_coordinator_subagent(tool_args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Weryfikuje parametry wywołania invoke_subagent dla Koordynatora w sesji głównej."""
    if not isinstance(tool_args, dict):
        return False, DENY_REASON_SUBAGENT_MISSING_MODEL

    raw_model = tool_args.get("Model")
    if raw_model is None:
        raw_model = tool_args.get("model")

    if raw_model is None or not str(raw_model).strip():
        return False, DENY_REASON_SUBAGENT_MISSING_MODEL

    model = str(raw_model).strip().lower()

    raw_type = tool_args.get("TypeName")
    if raw_type is None:
        raw_type = tool_args.get("type_name") or tool_args.get("typeName")
    type_name = str(raw_type).strip().lower() if raw_type else ""

    if type_name == "research":
        if model not in RESEARCH_ALLOWED_MODELS:
            return False, DENY_REASON_RESEARCH_MODEL
        return True, None

    if type_name == "self":
        if model not in ALLOWED_SUBAGENT_MODELS:
            return False, (
                f"BLOKADA REGUŁY 8: Niedozwolona wartość parametru Model='{raw_model}' dla subagenta 'self'. "
                "Dopuszczalne wartości: flash, flash_lite, inherit, pro."
            )
        return True, None

    if model not in ALLOWED_SUBAGENT_MODELS:
        return False, (
            f"BLOKADA REGUŁY 8: Niedozwolona wartość parametru Model='{raw_model}'. "
            "Dopuszczalne wartości: flash, flash_lite, inherit, pro."
        )

    return True, None


def _coerce_args_dict(args_val: Any) -> Dict[str, Any]:
    """Konwertuje argumenty narzędzia (dict lub string JSON z Codex CLI) na słownik."""
    if isinstance(args_val, dict):
        return args_val
    if isinstance(args_val, str):
        raw = args_val.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {"input": args_val}
    return {}


def _extract_patch_paths(patch_text: str) -> List[str]:
    """Wyodrębnia ścieżki plików z formatu apply_patch (Codex CLI) lub unified diff."""
    paths: List[str] = []
    for line in patch_text.splitlines():
        s = line.strip()
        for prefix in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: "):
            if s.startswith(prefix):
                p = s[len(prefix):].strip()
                if p:
                    paths.append(p)
        if s.startswith("+++ b/"):
            p = s[6:].strip()
            if p:
                paths.append(p)
        elif s.startswith("--- a/"):
            p = s[6:].strip()
            if p and p != "/dev/null":
                paths.append(p)
        elif s.startswith("Index: "):
            p = s[7:].strip()
            if p:
                paths.append(p)
        elif s.startswith("diff --git "):
            parts = s.split()
            if len(parts) >= 4:
                p = parts[-1].removeprefix("b/").strip()
                if p:
                    paths.append(p)
    return list(dict.fromkeys(paths))


def extract_event_data(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """Wyodrębnia nazwę narzędzia, argumenty oraz ścieżkę transkryptu z wejścia (Antigravity / Claude / Codex CLI)."""
    tool_name = ""
    tool_args: Dict[str, Any] = {}

    if "toolCall" in payload and isinstance(payload["toolCall"], dict):
        tc = payload["toolCall"]
        tool_name = str(tc.get("name") or "")
        raw_args = tc.get("args") if tc.get("args") is not None else tc.get("arguments")
        tool_args = _coerce_args_dict(raw_args)
    elif "tool_name" in payload:
        tool_name = str(payload.get("tool_name") or "")
        raw_args = (
            payload.get("tool_input")
            if payload.get("tool_input") is not None
            else payload.get("tool_args")
            if payload.get("tool_args") is not None
            else payload.get("arguments")
            if payload.get("arguments") is not None
            else payload.get("args")
        )
        tool_args = _coerce_args_dict(raw_args)
    elif "function" in payload and isinstance(payload["function"], dict):
        fn = payload["function"]
        tool_name = str(fn.get("name") or "")
        raw_args = fn.get("arguments") if fn.get("arguments") is not None else fn.get("args")
        tool_args = _coerce_args_dict(raw_args)
    elif "name" in payload:
        tool_name = str(payload.get("name") or "")
        raw_args = (
            payload.get("arguments")
            if payload.get("arguments") is not None
            else payload.get("args")
            if payload.get("args") is not None
            else payload.get("tool_input")
            if payload.get("tool_input") is not None
            else payload.get("tool_args")
            if payload.get("tool_args") is not None
            else payload.get("input")
        )
        tool_args = _coerce_args_dict(raw_args)

    transcript_path = (
        payload.get("transcriptPath")
        or payload.get("transcript_path")
        or payload.get("session_path")
    )
    return tool_name, tool_args, transcript_path


def evaluate_pre_tool(
    payload: Dict[str, Any],
    vault_root: Optional[Path] = None,
    state_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Główny silnik ewaluacji PreToolUse."""
    tool_name, tool_args, transcript_path = extract_event_data(payload)
    tool_name_lower = tool_name.lower().strip()

    # KROK 1: Szybka ścieżka dla subagentów (Fast-Path Allow <2 ms)
    if is_subagent_session(transcript_path):
        return {"decision": "allow"}

    # KROK 2: Weryfikacja invoke_subagent dla Koordynatora (Sesja Główna)
    if tool_name_lower in SUBAGENT_TOOLS:
        allowed, reason = is_allowed_coordinator_subagent(tool_args)
        if allowed:
            reset_coordinator_read_counter(transcript_path, state_file)
            return {"decision": "allow"}
        return {"decision": "deny", "reason": reason or DENY_REASON_SUBAGENT_MISSING_MODEL}

    root = vault_root or find_vault_root_fast()

    # KROK 3: Weryfikacja odczytu (read_file / view_file) dla Koordynatora (Sesja Główna)
    if tool_name_lower in READ_TOOLS:
        raw_path = str(
            tool_args.get("Path")
            or tool_args.get("path")
            or tool_args.get("file_path")
            or tool_args.get("FilePath")
            or ""
        )
        allowed, reason = is_allowed_coordinator_read(raw_path, root)
        if not allowed:
            return {"decision": "deny", "reason": reason or DENY_REASON_READ_WHITELIST}

        # Ścieżka dozwolona - sprawdzamy licznik kolejnych odczytów (nie wliczamy plików wewnętrznych /tmp oraz sesji/brain)
        if not is_internal_session_file(raw_path, root):
            allowed_cnt, reason_cnt = check_and_increment_read_counter(transcript_path, state_file)
            if not allowed_cnt:
                return {"decision": "deny", "reason": reason_cnt or DENY_REASON_READ_LIMIT}

        return {"decision": "allow"}

    # Narzędzia nieobjęte restrykcjami (np. send_message)
    if tool_name_lower not in WRITE_TOOLS and tool_name_lower not in SHELL_TOOLS:
        return {"decision": "allow"}

    # KROK 4: Weryfikacja dla sesji głównej (Koordynator) - Narzędzia zapisu i poleceń
    if tool_name_lower in WRITE_TOOLS:
        raw_path = str(
            tool_args.get("Path")
            or tool_args.get("path")
            or tool_args.get("file_path")
            or tool_args.get("FilePath")
            or ""
        )
        patch_text = str(tool_args.get("patch") or tool_args.get("input") or tool_args.get("diff") or "")
        patch_paths = _extract_patch_paths(patch_text) if patch_text else []
        all_targets = ([raw_path] if raw_path else []) + patch_paths

        if all_targets:
            if all(is_allowed_coordinator_write(p, root) for p in all_targets):
                return {"decision": "allow"}
            return {"decision": "deny", "reason": DENY_REASON_WRITE}
        return {"decision": "deny", "reason": DENY_REASON_WRITE}

    if tool_name_lower in SHELL_TOOLS:
        raw_cmd = (
            tool_args.get("CommandLine")
            or tool_args.get("command")
            or tool_args.get("command_line")
            or tool_args.get("cmd")
            or ""
        )
        if isinstance(raw_cmd, list):
            cmd_str = " ".join(str(x) for x in raw_cmd)
        else:
            cmd_str = str(raw_cmd)
        allowed, reason = is_allowed_coordinator_command(cmd_str)
        if allowed:
            return {"decision": "allow"}
        return {"decision": "deny", "reason": reason or DENY_REASON_COMMAND}

    return {"decision": "allow"}


def main() -> None:
    """Punkt wejścia hooka ze ścisłą klauzulą Fail-Safe."""
    try:
        raw_input = sys.stdin.read().strip()
        if not raw_input:
            print(json.dumps({"decision": "allow"}))
            return
        payload = json.loads(raw_input)
    except Exception as exc:
        _log_error(f"Fail-Safe aktywowany przy odczycie wejścia: {exc}\n{traceback.format_exc()}")
        print(
            json.dumps({
                "decision": "allow",
                "reason": "PreToolGuard fail-safe fallback: internal error tolerated to prevent workspace lockout",
            })
        )
        return

    try:
        result = evaluate_pre_tool(payload)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        _log_error(f"Fail-Safe aktywowany podczas ewaluacji: {exc}\n{traceback.format_exc()}")
        print(
            json.dumps({
                "decision": "allow",
                "reason": "PreToolGuard fail-safe fallback: internal error tolerated to prevent workspace lockout",
            })
        )


if __name__ == "__main__":
    main()
