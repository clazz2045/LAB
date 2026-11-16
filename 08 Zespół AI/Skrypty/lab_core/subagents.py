#!/usr/bin/env python3
"""
subagent_gate.py - Twarda bramka destylatu SAT subagentów LAB 3.0 (Reguła 8 w kodzie)

Użycie (destylat z pliku; hash i id subagenta są WYMAGANE):
    python3 subagent_gate.py --file destylat.md --print-sha
    python3 subagent_gate.py --agent Klara --task-type research --file destylat.md \\
        --subagent-id <conversation_id> --sha256 <hash>

Działanie (Faza 6, fail-closed): waliduje destylat (limit 600 tokenów, sekcje Fakty/Wnioski/Rekomendacje,
brak surowych zrzutów) ORAZ wiąże go z subagentem: w transkrypcie sesji rodzica musi istnieć wiadomość systemowa
(SYSTEM_MESSAGE, sender=<subagent-id>) zawierająca identyczny tekst, a wcześniej wywołanie invoke_subagent
zgodne z rolą --agent. Brak/nieczytelny transkrypt = odrzucenie. Wynik trafia do rejestru subagent_dispatches
(lab_master.db) i kod wyjścia: 0 (accepted) / 1 (rejected).

Ograniczenie: powiązanie opiera się na tym, że harness zapisuje wiadomości subagentów jako kroki SYSTEM
w transkrypcie rodzica; nie chroni przed Koordynatorem z dostępem do zapisu transkryptu (zależność od hosta).

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

_scripts_dir = Path(__file__).resolve().parent.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core import ConfigManager, find_vault_root
from lab_core.compliance import INVOKE_TOOL, _call_args, iter_steps, session_transcript_from_harness, transcript_files
from lab_core.distillate import (
    _strip_diacritics,
    estimate_tokens,
    validate_dispatch_guard,
    validate_sat_distillate,
)
from lab_core.schema import init_master_db, log_subagent_dispatch

MASTER_DB_NAME = "lab_master.db"

_SENDER_RE = re.compile(r"\bsender=(\S+)")
_BODY_RE = re.compile(r"\bcontent=(.*?)(?:\n?</SYSTEM_MESSAGE>\s*)?$", re.DOTALL)


def normalize_distillate(text: str) -> str:
    """Normalizacja do porównań/hasha: LF, bez białych znaków na krańcach linii i tekstu."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def distillate_sha256(text: str) -> str:
    return hashlib.sha256(normalize_distillate(text).encode("utf-8")).hexdigest()


def candidate_transcripts(explicit: Optional[Path] = None, brain_dir: Optional[Path] = None) -> List[Path]:
    """Lista czytelnych transkryptów do przeszukania (jawny > LAB_TRANSCRIPT_PATH > sesja harnessu > brain/, najnowsze pierwsze).

    Powiązanie jest dowodem samo w sobie (wiadomość od konkretnego nadawcy), więc nie zgadujemy "aktywnej" sesji.
    """
    if explicit is not None:
        p = Path(explicit).expanduser().resolve()
        return [p] if p.is_file() and os.access(p, os.R_OK) else []
    env_override = os.environ.get("LAB_TRANSCRIPT_PATH")
    if env_override:
        p = Path(env_override).expanduser().resolve()
        return [p] if p.is_file() and os.access(p, os.R_OK) else []
    harness = session_transcript_from_harness(brain_dir)
    if harness is not None:
        return [harness]
    base_dir = Path(brain_dir).expanduser().resolve() if brain_dir else (Path.home() / ".gemini" / "antigravity" / "brain")
    found: List[Path] = []
    try:
        for cand in base_dir.glob("*/.system_generated/logs/transcript.jsonl"):
            try:
                if cand.is_file() and os.access(cand, os.R_OK):
                    found.append(cand)
            except OSError:
                continue
    except OSError:
        return []
    return sorted(found, key=lambda x: x.stat().st_mtime, reverse=True)


def _step_text(step: Dict[str, Any]) -> str:
    return json.dumps(step, ensure_ascii=False)


def _is_system_message(step: Dict[str, Any]) -> bool:
    return step.get("source") == "SYSTEM" and str(step.get("type", "")).upper() == "SYSTEM_MESSAGE"


def _extract_system_message_body(step: Dict[str, Any], subagent_id: str) -> Optional[str]:
    """Zwraca treść (body) wiadomości systemowej od subagent_id lub None."""
    if not _is_system_message(step):
        return None
    content = str(step.get("content", ""))
    m = _SENDER_RE.search(content)
    if not m or m.group(1) != subagent_id:
        return None
    body_m = _BODY_RE.search(content[content.find("content="):]) if "content=" in content else None
    return body_m.group(1) if body_m else content


def find_bound_message(steps: List[Dict[str, Any]], subagent_id: str, text: str) -> Optional[int]:
    """Zwraca indeks kroku wiadomości systemowej od subagent_id, której treść zawiera identyczny destylat."""
    want = normalize_distillate(text)
    if not want:
        return None
    for i, step in enumerate(steps):
        body = _extract_system_message_body(step, subagent_id)
        if body is not None and want in normalize_distillate(body):
            return i
    return None


def find_latest_subagent_message(transcripts: List[Path], subagent_id: str) -> Optional[str]:
    """Wyszukuje najnowszą wiadomość systemową od subagent_id w transkryptach rodzica."""
    for tpath in transcripts:
        for fpath in transcript_files(tpath):
            steps = list(iter_steps(fpath))
            for step in reversed(steps):
                body = _extract_system_message_body(step, subagent_id)
                if body is not None:
                    return normalize_distillate(body)
    return None


def _invoke_fields(step: Dict[str, Any]) -> List[str]:
    """Pola Role/TypeName/Prompt wszystkich wywołań invoke_subagent w kroku."""
    out: List[str] = []
    for tc in step.get("tool_calls") or []:
        if isinstance(tc, dict) and (tc.get("name") or tc.get("tool_name")) == INVOKE_TOOL:
            args = _call_args(tc)
            out.append(" ".join(str(args.get(k, "")) for k in ("Role", "TypeName", "Prompt")))
    return out


def _agent_tokens(*names: str) -> List[str]:
    tokens: List[str] = []
    for n in names:
        t = _strip_diacritics(str(n)).lower().strip()
        if not t:
            continue
        tokens.append(t[:-1] if len(t) >= 5 else t)
    return tokens


def verify_distillate_binding(
    transcripts: List[Path],
    subagent_id: str,
    text: str,
    agent_names: Tuple[str, ...],
) -> Tuple[bool, List[str]]:
    """Weryfikuje powiązanie destylatu z subagentem w transkrypcie rodzica. Zwraca (ok, problemy)."""
    if not transcripts:
        return False, ["Brak lub nieczytelny transkrypt sesji (fail-closed): nie można zweryfikować źródła destylatu."]
    problems: List[str] = []
    for tpath in transcripts:
        for fpath in transcript_files(tpath):
            steps = list(iter_steps(fpath))
            msg_idx = find_bound_message(steps, subagent_id, text)
            if msg_idx is None:
                continue
            # Identyczny tekst przyszedł od tego nadawcy. Sprawdź wcześniejsze invoke_subagent zgodne z rolą.
            first_seen = next((i for i, st in enumerate(steps) if subagent_id in _step_text(st)), msg_idx)
            invokes: List[str] = []
            for st in steps[: first_seen + 1]:
                if st.get("source") in ("USER_EXPLICIT", "SYSTEM"):
                    continue
                invokes.extend(_invoke_fields(st))
            if not invokes:
                return False, ["Brak wywołania 'invoke_subagent' poprzedzającego wiadomość subagenta w transkrypcie."]
            haystacks = [_strip_diacritics(f).lower() for f in invokes]
            tokens = _agent_tokens(*agent_names)
            if tokens and not any(tok in h for tok in tokens for h in haystacks):
                return False, [
                    f"--agent ({', '.join(agent_names)}) niezgodny z rolą/TypeName w invoke_subagent powiązanym z subagentem {subagent_id}."
                ]
            return True, []
    problems.append(
        "Brak w transkrypcie wiadomości od subagenta "
        f"{subagent_id} z identycznym tekstem destylatu (tekst zrekonstruowany, zmieniony lub od innego nadawcy)."
    )
    return False, problems


def verify_transcript_has_subagent_call(transcript_path: Path) -> bool:
    """Zgodność wsteczna: czy w transkrypcie jest jakiekolwiek wywołanie invoke_subagent.

    UWAGA: to słaba kontrola (nie wiąże destylatu z subagentem); bramka używa verify_distillate_binding.
    """
    for fpath in transcript_files(Path(transcript_path)):
        for step in iter_steps(fpath):
            if _invoke_fields(step):
                return True
            if step.get("name") == INVOKE_TOOL or step.get("tool_name") == INVOKE_TOOL:
                return True
    return False


def run_gate(
    text: str,
    agent: str,
    task_type: str,
    tokens_used: Optional[int] = None,
    vault_root: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    subagent_id: Optional[str] = None,
    content_sha256: Optional[str] = None,
    enforce_binding: bool = True,
    brain_dir: Optional[Path] = None,
) -> int:
    """Waliduje destylat, loguje wynik w rejestrze i zwraca kod wyjścia (0 = accepted).

    enforce_binding=False wyłącznie dla testów jednostkowych logiki walidacji (CLI go nie udostępnia).
    Produkcyjnie (domyślnie) wymagane: subagent_id, content_sha256 i transkrypt z wiadomością subagenta.
    """
    root = Path(vault_root).resolve() if vault_root else find_vault_root()
    config = ConfigManager(vault_root=root)
    db_path = root / config.get_room_by_role("sqlite-relational").path / MASTER_DB_NAME
    if not db_path.is_file():
        init_master_db(db_path, instance_name=config.system.instance_name)

    try:
        agent_name = config.get_agent_name(agent)
    except Exception:
        agent_name = agent

    ok, problems = validate_sat_distillate(text)
    problems_list = list(problems)

    guard_ok, guard_err = validate_dispatch_guard(agent, agent_name, task_type)
    if not guard_ok and guard_err:
        ok = False
        problems_list.append(guard_err)

    actual_sha = distillate_sha256(text)
    if enforce_binding:
        if not subagent_id or not str(subagent_id).strip():
            ok = False
            problems_list.append("Brak --subagent-id (conversation_id subagenta): destylat bez powiązania ze źródłem.")
        if not content_sha256:
            ok = False
            problems_list.append("Brak --sha256 treści destylatu (użyj --print-sha).")
        elif content_sha256.strip().lower() != actual_sha:
            ok = False
            problems_list.append("Hash --sha256 nie zgadza się z treścią destylatu (tekst zmieniony po wygenerowaniu hasha).")
        if subagent_id and str(subagent_id).strip():
            transcripts = candidate_transcripts(transcript_path, brain_dir)
            b_ok, b_problems = verify_distillate_binding(
                transcripts, str(subagent_id).strip(), text, (agent, agent_name)
            )
            if not b_ok:
                ok = False
                problems_list.extend(b_problems)

    status = "accepted" if ok else "rejected"
    excerpt = text.strip() if ok else "; ".join(problems_list)
    row_id = log_subagent_dispatch(
        db_path, agent_name, task_type, status, tokens_used, excerpt,
        subagent_id=subagent_id if enforce_binding else None,
        content_sha256=actual_sha if enforce_binding else None,
    )

    print(f"[subagent_gate] {agent_name}/{task_type}: {status.upper()} (~{estimate_tokens(text)} tok, wpis #{row_id})")
    for problem in problems_list:
        print(f"  - {problem}")
    return 0 if ok else 1


def run_check_only(text: str) -> int:
    """Lokalna walidacja destylatu SAT (format i limit tokenów) bez transkryptu i bez zapisu do DB."""
    ok, problems = validate_sat_distillate(text)
    tokens = estimate_tokens(text)
    status = "OK" if ok else "REJECTED"
    print(f"[subagent_gate] CHECK-ONLY: {status} (~{tokens} tok)")
    for problem in problems:
        print(f"  - {problem}")
    return 0 if ok else 1


def run_auto_bind(
    agent: str,
    task_type: str,
    subagent_id: str,
    tokens_used: Optional[int] = None,
    vault_root: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    brain_dir: Optional[Path] = None,
) -> int:
    """Wyszukuje najnowszą wiadomość subagenta w transkrypcie, wylicza SHA-256 i rejestruje destylat."""
    sid = str(subagent_id).strip()
    transcripts = candidate_transcripts(transcript_path, brain_dir)
    extracted = find_latest_subagent_message(transcripts, sid)
    if extracted is None:
        return run_gate(
            "",
            agent,
            task_type,
            tokens_used=tokens_used,
            vault_root=vault_root,
            transcript_path=transcript_path,
            subagent_id=sid,
            content_sha256=None,
            brain_dir=brain_dir,
        )
    sha = distillate_sha256(extracted)
    return run_gate(
        extracted,
        agent,
        task_type,
        tokens_used=tokens_used,
        vault_root=vault_root,
        transcript_path=transcript_path,
        subagent_id=sid,
        content_sha256=sha,
        brain_dir=brain_dir,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bramka destylatu SAT subagentów (Reguła 8, fail-closed).")
    parser.add_argument("--agent", help="Identyfikator roli (role_key) lub imię subagenta (np. researcher, Klara)")
    parser.add_argument("--task-type", help="Typ zadania (np. research, code, audit)")
    parser.add_argument("--file", help="Plik z destylatem (domyślnie stdin)")
    parser.add_argument("--tokens", type=int, default=None, help="Zużycie tokenów subagenta w jego piaskownicy")
    parser.add_argument("--transcript-path", default=None, help="transcript.jsonl sesji rodzica (domyślnie LAB_TRANSCRIPT_PATH lub przeszukanie brain/)")
    parser.add_argument("--subagent-id", default=None, help="conversation_id subagenta (nadawca wiadomości z destylatem)")
    parser.add_argument("--sha256", default=None, help="SHA-256 znormalizowanego destylatu (z --print-sha)")
    parser.add_argument("--print-sha", action="store_true", help="Wypisz SHA-256 destylatu i zakończ")
    parser.add_argument("--check-only", action="store_true", help="Lokalna walidacja formatu SAT i limitu tokenów bez transkryptu i zapisu do DB")
    parser.add_argument("--auto-bind", action="store_true", help="Automatycznie pobierz najnowszą wiadomość od --subagent-id z transkryptu, wylicz SHA-256 i zarejestruj")
    args = parser.parse_args(argv)

    if args.check_only:
        text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        return run_check_only(text)

    if args.print_sha:
        text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        print(distillate_sha256(text))
        return 0

    if not args.agent or not args.task_type:
        parser.error("--agent i --task-type są wymagane")
    t_path = Path(args.transcript_path) if args.transcript_path else None

    if args.auto_bind:
        if not args.subagent_id or not str(args.subagent_id).strip():
            parser.error("--auto-bind wymaga --subagent-id")
        return run_auto_bind(
            args.agent,
            args.task_type,
            args.subagent_id,
            tokens_used=args.tokens,
            transcript_path=t_path,
        )

    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    return run_gate(
        text, args.agent, args.task_type, args.tokens,
        transcript_path=t_path, subagent_id=args.subagent_id, content_sha256=args.sha256,
    )


if __name__ == "__main__":
    sys.exit(main())
