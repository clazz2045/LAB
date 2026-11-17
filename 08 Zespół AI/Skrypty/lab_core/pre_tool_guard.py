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
     - read_file: Model Read-Only (brak licznika i brak blokady odczytu plików w sesji głównej;
       zakres merytoryczny i ochronę okna 250k reguluje AGENTS.md).
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
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
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
    return paths


def extract_event_data(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """Wyodrębnia nazwę narzędzia, argumenty oraz ścieżkę transkryptu z wejścia (Antigravity / Claude / Codex CLI)."""
    tool_name = ""
    tool_args: Dict[str, Any] = {}

    if "toolCall" in payload and isinstance(payload["toolCall"], dict):
        tc = payload["toolCall"]
        tool_name = str(tc.get("name") or "")
        tool_args = _coerce_args_dict(tc.get("args"))
    elif "tool_name" in payload:
        tool_name = str(payload.get("tool_name") or "")
        raw_args = payload.get("tool_input") if payload.get("tool_input") is not None else payload.get("tool_args")
        tool_args = _coerce_args_dict(raw_args)
    elif "name" in payload:
        tool_name = str(payload.get("name") or "")
        tool_args = _coerce_args_dict(payload.get("args") or payload.get("input"))

    transcript_path = (
        payload.get("transcriptPath")
        or payload.get("transcript_path")
        or payload.get("session_path")
    )
    return tool_name, tool_args, transcript_path


def evaluate_pre_tool(payload: Dict[str, Any], vault_root: Optional[Path] = None) -> Dict[str, Any]:
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
            return {"decision": "allow"}
        return {"decision": "deny", "reason": reason or DENY_REASON_SUBAGENT_MISSING_MODEL}

    # Narzędzia Read-Only (read_file) oraz narzędzia nieobjęte restrykcjami (np. send_message)
    if (
        tool_name_lower in READ_TOOLS
        or (tool_name_lower not in WRITE_TOOLS and tool_name_lower not in SHELL_TOOLS)
    ):
        return {"decision": "allow"}

    root = vault_root or find_vault_root_fast()

    # KROK 3: Weryfikacja dla sesji głównej (Koordynator) - Narzędzia zapisu i poleceń
    if tool_name_lower in WRITE_TOOLS:
        raw_path = str(
            tool_args.get("Path")
            or tool_args.get("path")
            or tool_args.get("file_path")
            or tool_args.get("FilePath")
            or ""
        )
        if raw_path:
            if is_allowed_coordinator_write(raw_path, root):
                return {"decision": "allow"}
            return {"decision": "deny", "reason": DENY_REASON_WRITE}
        patch_text = str(tool_args.get("patch") or tool_args.get("input") or "")
        patch_paths = _extract_patch_paths(patch_text) if patch_text else []
        if patch_paths and all(is_allowed_coordinator_write(p, root) for p in patch_paths):
            return {"decision": "allow"}
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
