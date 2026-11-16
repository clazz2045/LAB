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
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

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

DEFAULT_WINDOW_LIMIT = 250_000

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
    Określa strefę obciążenia okna kontekstowego:
    Domyślne progi dla limitu 250 000:
      < 200 000: 🟢 Komfortowa
      200 000 - 240 000: 🟡 Wymaga uwagi
      > 240 000: 🔴 Krytyczna

    Dla limitów niestandardowych zachowuje proporcje 80% i 96%.
    """
    if limit == DEFAULT_WINDOW_LIMIT:
        warn_threshold = 200_000
        crit_threshold = 240_000
    else:
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


def find_latest_transcript(
    brain_dir: Optional[Union[str, Path]] = None,
    claude_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Deterministycznie wyszukuje aktywny plik logu transkryptu sesji:
    Wybiera plik z najnowszym czasem modyfikacji (mtime) w strukturze:
    - Antigravity: ~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl
    - Claude Code: ~/.claude/projects/*/*.jsonl
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

    candidates: List[Path] = []
    if base_dir.is_dir():
        pattern = "*/.system_generated/logs/transcript.jsonl"
        try:
            candidates.extend(p for p in base_dir.glob(pattern) if p.is_file())
        except (PermissionError, OSError):
            pass

    if not brain_dir:
        c_dir = Path(claude_dir).expanduser().resolve() if claude_dir else None
        candidates.extend(_find_claude_transcripts(c_dir))

    if not candidates:
        raise TranscriptNotFoundError(
            f"Nie odnaleziono żadnego pliku transkryptu sesji (Antigravity: {base_dir}, Claude Code: ~/.claude/projects)."
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_telemetry(
    transcript_path: Union[str, Path],
    limit: int = DEFAULT_WINDOW_LIMIT
) -> TokenMetrics:
    """
    Odczytuje plik transcript.jsonl i wyciąga najnowsze metryki tokenów:
    - input_tokens, cache_read_tokens, output_tokens.
    - total_context_tokens = input_tokens + cache_read_tokens.
    - procent zużycia okna i strefę.
    """
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
                    if "input_tokens" in data:
                        last_token_entry = data
                    else:
                        msg_usage = (data.get("message") or {}).get("usage") if isinstance(data.get("message"), dict) else None
                        usage = msg_usage if isinstance(msg_usage, dict) else (data.get("usage") if isinstance(data.get("usage"), dict) else None)
                        if usage and "input_tokens" in usage:
                            last_token_entry = {
                                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                                "cache_read_tokens": (
                                    int(usage.get("cache_read_input_tokens", 0) or 0)
                                    + int(usage.get("cache_creation_input_tokens", 0) or 0)
                                ),
                                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                                "step_index": data.get("step_index"),
                            }
                except json.JSONDecodeError:
                    # Pomijamy niepełne lub uszkodzone linie logu
                    continue
    except OSError as e:
        raise TelemetryDataError(f"Błąd wejścia/wyjścia podczas odczytu {path}: {e}") from e

    if last_token_entry is None:
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
        transcript_path=str(path),
    )


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
        default=DEFAULT_WINDOW_LIMIT,
        help=f"Limit wielkości okna kontekstowego w tokenach (domyślnie: {DEFAULT_WINDOW_LIMIT}).",
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
        help="Zwraca zwarty 1-linijkowy format dla paska menu / Raycasta (np. '🧠 109.4k / 250k (43.8%%) 🟢').",
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
    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Główny punkt wejścia CLI."""
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    try:
        # 1. Lokalizacja pliku transkryptu
        if parsed_args.transcript_path:
            transcript_file = Path(parsed_args.transcript_path).expanduser().resolve()
        else:
            transcript_file = find_latest_transcript(brain_dir=parsed_args.brain_dir)

        # 2. Parsowanie telemetrii
        metrics = parse_telemetry(transcript_file, limit=parsed_args.limit)

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
