"""lab_core.compliance - Audyt zgodności sesji głównej z Regułą 8 (detekcja PO FAKCIE).

Skanuje transcript.jsonl sesji głównej (Koordynatora). Obsługuje dwa schematy:
  - Antigravity: kroki z polami source / tool_calls / step_index (wywołanie: invoke_subagent),
  - Claude Code: linie z polem message.content[] i blokami tool_use (wywołanie subagenta: "Agent").
Naruszenie:
  - więcej niż MAX_READS_BEFORE_INVOKE odczytów plików przed pierwszym `invoke_subagent`,
  - jakiekolwiek wywołanie narzędzia zapisu (write_file/edit_file/...) w sesji głównej.

Ograniczenie: to wykrycie po fakcie, a nie blokada. Koordynator może pominąć uruchomienie audytu;
dlatego `checkpoint.py` uruchamia go automatycznie i wymaga adnotacji naruszenia.

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.dont_write_bytecode = True

MAX_READS_BEFORE_INVOKE = 3

READ_TOOLS = frozenset({
    "read_file", "view_file", "grep", "grep_search", "find", "find_by_name",
    "list_dir", "codebase_search", "glob", "search_files",
})
WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "replace_file_content", "multi_replace_file_content",
    "create_file", "delete_file",
})
SHELL_READ_COMMANDS = frozenset({"cat", "head", "tail", "grep", "rg", "find", "ls", "less", "sed", "awk", "wc"})
SHELL_TOOLS = frozenset({"run_command", "Bash"})
INVOKE_TOOL = "invoke_subagent"
# Schemat Claude Code (nazwy narzędzi wbudowanych)
CC_INVOKE_TOOL = "Agent"
CC_READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS"})
CC_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def transcript_files(transcript_path: Path) -> List[Path]:
    """Preferuje transcript_full.jsonl (pełne pola), a w razie braku transcript.jsonl."""
    p = Path(transcript_path)
    full = p.parent / "transcript_full.jsonl"
    out: List[Path] = []
    if full.is_file() and os.access(full, os.R_OK):
        out.append(full)
    if p.is_file() and os.access(p, os.R_OK):
        out.append(p)
    return out


def iter_steps(path: Path) -> Iterator[Dict[str, Any]]:
    """Iteruje poprawne kroki JSONL (nieczytelne linie są pomijane)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _call_args(tc: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("args", "arguments", "input", "parameters"):
        val = tc.get(key)
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return {}


def _is_claude_code_step(step: Dict[str, Any]) -> bool:
    return isinstance(step.get("message"), dict)


def _is_antigravity_step(step: Dict[str, Any]) -> bool:
    return isinstance(step.get("tool_calls"), list) or "source" in step


def step_tool_calls(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Wywołania narzędzi jednego kroku (oba schematy), znormalizowane do dict z kluczami name + input/args.

    Claude Code: bloki tool_use w wiadomości asystenta (wynik narzędzia/tool_result jest pomijany).
    Antigravity: lista tool_calls.
    """
    if _is_claude_code_step(step):
        msg = step["message"]
        content = msg.get("content")
        if msg.get("role") != "assistant" or not isinstance(content, list):
            return []
        out: List[Dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input")
                out.append({"name": str(block.get("name") or ""), "input": inp if isinstance(inp, dict) else {}})
        return out
    calls = step.get("tool_calls")
    if not isinstance(calls, list):
        return []
    return [tc for tc in calls if isinstance(tc, dict)]


def iter_tool_calls(path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Iteruje (step_index, tool_call) wyłącznie z kroków modelu (nie systemu/użytkownika)."""
    for n, step in enumerate(iter_steps(path)):
        if _is_claude_code_step(step):
            for tc in step_tool_calls(step):
                yield n, tc
            continue
        if step.get("source") in ("USER_EXPLICIT", "SYSTEM"):
            continue
        calls = step.get("tool_calls")
        if not isinstance(calls, list):
            continue
        idx = step.get("step_index", n)
        for tc in calls:
            if isinstance(tc, dict):
                yield int(idx) if isinstance(idx, int) else n, tc


def classify_call(tc: Dict[str, Any]) -> str:
    """Zwraca 'invoke' | 'read' | 'write' | 'other'."""
    name = str(tc.get("name") or tc.get("tool_name") or "")
    if name in (INVOKE_TOOL, CC_INVOKE_TOOL):
        return "invoke"
    if name in WRITE_TOOLS or name in CC_WRITE_TOOLS:
        return "write"
    if name in READ_TOOLS or name in CC_READ_TOOLS:
        return "read"
    if name in SHELL_TOOLS:
        args = _call_args(tc)
        cmd = str(args.get("CommandLine") or args.get("command") or "").strip()
        try:
            first = shlex.split(cmd)[0] if cmd else ""
        except ValueError:
            first = cmd.split()[0] if cmd.split() else ""
        if os.path.basename(first) in SHELL_READ_COMMANDS:
            return "read"
    return "other"


def check_session_compliance(transcript_path: Path) -> Dict[str, Any]:
    """Skanuje transkrypt sesji głównej. Zwraca raport z listą naruszeń."""
    tpath = Path(transcript_path)
    files = transcript_files(tpath)
    report: Dict[str, Any] = {
        "transcript": str(tpath),
        "readable": bool(files),
        "reads_before_first_invoke": 0,
        "invoke_count": 0,
        "write_calls": 0,
        "violations": [],
        "status": "unknown",
    }
    if not files:
        report["violations"].append("Brak lub nieczytelny transkrypt - audyt niemożliwy (fail-closed).")
        report["status"] = "violation"
        return report
    if not any(_is_claude_code_step(s) or _is_antigravity_step(s) for s in iter_steps(files[0])):
        report["violations"].append("Nierozpoznany schemat transkryptu - audyt niemożliwy (fail-closed).")
        report["status"] = "violation"
        return report

    reads = 0
    invoked = False
    writes: List[str] = []
    for step_idx, tc in iter_tool_calls(files[0]):
        kind = classify_call(tc)
        if kind == "invoke":
            invoked = True
            report["invoke_count"] += 1
        elif kind == "read" and not invoked:
            reads += 1
        elif kind == "write":
            writes.append(f"krok {step_idx}: {tc.get('name')}")
    report["reads_before_first_invoke"] = reads
    report["write_calls"] = len(writes)

    if reads > MAX_READS_BEFORE_INVOKE:
        report["violations"].append(
            f"{reads} odczytów plików przed pierwszym invoke_subagent (limit {MAX_READS_BEFORE_INVOKE})."
            + ("" if report["invoke_count"] else " Brak jakiegokolwiek invoke_subagent w sesji.")
        )
    if writes:
        report["violations"].append(
            f"{len(writes)} zapisów plików w sesji głównej ({'; '.join(writes[:5])})."
        )
    report["status"] = "violation" if report["violations"] else "ok"
    return report


def allowed_transcript_roots() -> Tuple[Path, ...]:
    """Katalogi, w których wolno czytać transkrypty: Claude Code (~/.claude/projects) i Antigravity (brain/)."""
    home = Path.home()
    return (home / ".claude" / "projects", home / ".gemini" / "antigravity" / "brain")


def transcript_path_rejection(path: Path) -> Optional[str]:
    """None, jeśli ścieżka (po realpath) leży w dozwolonym katalogu; inaczej powód odrzucenia (fail-closed)."""
    try:
        real = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return f"nie można rozwiązać ścieżki ({exc})"
    for root in allowed_transcript_roots():
        if real.is_relative_to(root.resolve()):
            return None
    roots = ", ".join(str(r) for r in allowed_transcript_roots())
    return f"ścieżka {real} leży poza dozwolonymi katalogami transkryptów ({roots})"


def missing_transcript_report(reason: str = "Brak ścieżki transkryptu sesji głównej - audyt niemożliwy (fail-closed).") -> Dict[str, Any]:
    """Raport Reguły 8 dla sesji bez transkryptu: jawne naruszenie, nigdy pusty wynik ani 'ok'."""
    return {
        "transcript": "",
        "readable": False,
        "reads_before_first_invoke": 0,
        "invoke_count": 0,
        "write_calls": 0,
        "violations": [reason],
        "status": "violation",
    }


def session_transcript_from_harness(brain_dir: Optional[Path] = None) -> Optional[Path]:
    """Transkrypt bieżącej sesji wg ANTIGRAVITY_CONVERSATION_ID (zmienna ustawiana przez harness)."""
    cid = os.environ.get("ANTIGRAVITY_CONVERSATION_ID", "").strip()
    if not cid or "/" in cid or ".." in cid:
        return None
    base = Path(brain_dir).expanduser().resolve() if brain_dir else (Path.home() / ".gemini" / "antigravity" / "brain")
    cand = base / cid / ".system_generated" / "logs" / "transcript.jsonl"
    if cand.is_file() and os.access(cand, os.R_OK):
        return cand
    return None


def claude_code_transcript_from_env(project_dir: Optional[Path] = None, home_dir: Optional[Path] = None) -> Optional[Path]:
    """Transkrypt Claude Code: ~/.claude/projects/<zakodowany-katalog>/<CLAUDE_CODE_SESSION_ID>.jsonl.

    Tylko wskazany plik (bez przeszukiwania dysku). Zakodowany katalog: każdy znak spoza [A-Za-z0-9] -> '-'.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return None
    proj = Path(project_dir).expanduser().resolve() if project_dir else Path.cwd().resolve()
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(proj))
    base = Path(home_dir).expanduser() if home_dir else Path.home()
    cand = base / ".claude" / "projects" / encoded / f"{sid}.jsonl"
    if cand.is_file() and os.access(cand, os.R_OK):
        return cand
    return None


def find_session_transcript() -> Optional[Path]:
    """Transkrypt sesji głównej: LAB_TRANSCRIPT_PATH, potem CLAUDE_CODE_SESSION_ID, potem sesja harnessu.

    Bez zgadywania 'najnowszego'. Jawnie wskazany LAB_TRANSCRIPT_PATH, który nie istnieje, daje None (fail-closed).
    """
    env = os.environ.get("LAB_TRANSCRIPT_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        rejection = transcript_path_rejection(p)
        if rejection is not None:
            print(f"[compliance] LAB_TRANSCRIPT_PATH odrzucony: {rejection}", file=sys.stderr)
            return None
        if p.is_file() and os.access(p, os.R_OK):
            return p
        return None
    cc = claude_code_transcript_from_env()
    if cc is not None:
        return cc
    return session_transcript_from_harness()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audyt zgodności sesji głównej (Reguła 8, po fakcie).")
    parser.add_argument("--transcript-path", default=None, help="transcript.jsonl sesji głównej (lub LAB_TRANSCRIPT_PATH)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path: Optional[Path] = None
    if args.transcript_path:
        path = Path(args.transcript_path).expanduser().resolve()
        rejection = transcript_path_rejection(path)
        if rejection is not None:
            print(f"[compliance] --transcript-path odrzucony: {rejection}", file=sys.stderr)
            return 2
    else:
        path = find_session_transcript()
    if path is None:
        print("[compliance] Brak ścieżki transkryptu (--transcript-path / LAB_TRANSCRIPT_PATH).", file=sys.stderr)
        return 2
    report = check_session_compliance(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[compliance] {report['status'].upper()} (odczyty przed invoke: {report['reads_before_first_invoke']}, zapisy: {report['write_calls']})")
        for v in report["violations"]:
            print(f"  - {v}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
