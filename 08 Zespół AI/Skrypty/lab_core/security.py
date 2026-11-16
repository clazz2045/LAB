#!/usr/bin/env python3
"""release_guard.py - Twardy strażnik wydań i egzekucji kodu dla LAB 3.0 (Framework PZW)

Standard inżynieryjny:
- 100% Python Standard Library (os, sys, hashlib, json, datetime, pathlib, subprocess, typing).
- Zero zewnętrznych zależności pip.
- Zgodny z „Jednym Prawem” (kod zamiast tekstu) oraz wymogami audytowymi Ady.

Główne filary strażnika:
1. Immutable Artifact Guard:
   - Zakaz nadpisywania plików w 'Produkt/' oraz 'Produkt/_wersje/'.
   - Jeśli docelowy plik wydania już istnieje, rzuca ImmutableReleaseError.
2. Canonical Path Guard:
   - Blokada ścieżek z /tmp, /private/tmp, /var/folders oraz spoza kanonicznego drzewa LAB.
   - Weryfikacja ścieżki skryptu oraz ścieżek wejścia/wyjścia; rzuca CanonicalPathError.
3. Fail-Hard Data Validator:
   - Zakaz cichych fallbacków i brakujących pól w rekordach metadanych.
   - Weryfikuje wymagane klucze oraz obecność podejrzanych tekstów zastępczych; rzuca CriticalDataMissingError.
4. Atomic Test-and-Promote Pipeline:
   - Funkcja promote_release(...): uruchamia test bramkowy. Jeśli kod wyjścia != 0,
     kasuje plik roboczy (staged_file) i rzuca TestGateFailedError.
   - Jeśli test przeszedł (exit code 0), atomowo przenosi plik do katalogu docelowego
     pod niezmienną nazwą wersji i generuje obok *.manifest.json z sumą SHA-256.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca LAB
sys.dont_write_bytecode = True

# Zaimportuj core_config z katalogu bieżącego lub katalogów nadrzędnych
_script_dir = Path(__file__).parent.resolve()
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "core_config.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

try:
    from core_config import ConfigError, ConfigManager, find_vault_root
except ImportError:
    # Bezpieczny fallback awaryjny dla deterministycznego szukania korzenia skarbca
    class ConfigError(Exception):  # type: ignore[no-redef]
        pass

    def find_vault_root(start_path: Optional[Union[str, Path]] = None) -> Path:  # type: ignore[misc]
        curr = Path(start_path or Path.cwd()).resolve()
        for p in [curr, *curr.parents]:
            if (p / "lab.config.toml").is_file():
                return p
        return curr


# ==============================================================================
# WYJĄTKI ARCHITEKTONICZNE (FAIL-HARD ERROR HIERARCHY)
# ==============================================================================

class ReleaseGuardError(Exception):
    """Bazowy wyjątek dla wszystkich błędów strażnika wydań LAB."""
    pass


class ImmutableReleaseError(ReleaseGuardError):
    """Podnoszony przy próbie nadpisania istniejącego pliku wydania (Immutable Artifact Guard)."""
    pass


class CanonicalPathError(ReleaseGuardError):
    """Podnoszony, gdy ścieżka leży w /tmp, /private/tmp lub poza kanonicznym drzewem LAB."""
    pass


class CriticalDataMissingError(ReleaseGuardError):
    """Podnoszony, gdy rekord metadanych nie posiada wymaganych pól lub zawiera cichy fallback."""
    pass


class TestGateFailedError(ReleaseGuardError):
    """Podnoszony, gdy brama testowa w pipeline test-and-promote zakończy się niezerowym kodem wyjścia."""
    pass


class UnauthorizedSprintInjectionError(ReleaseGuardError):
    """Podnoszony przy próbie dodania nieautoryzowanego zadania lub interpretacji myśli do sprintu/warsztatu bez zgody człowieka."""
    pass


class WorkshopPolicyViolationError(ReleaseGuardError):
    """Podnoszony w przypadku naruszenia polityki czystości Warsztatu lub typu zadań w sprincie (np. zadania RS, Pokoje, Zrzut)."""
    pass


# ==============================================================================
# STAŁE I WZORCE
# ==============================================================================

# Znane ścieżki i prefiksy katalogów tymczasowych
FORBIDDEN_TMP_PREFIXES: Tuple[str, ...] = (
    "/tmp",
    "/private/tmp",
    "/var/tmp",
    "/private/var/tmp",
    "/var/folders",
    "/private/var/folders",
)

# Domyślne podejrzane ciągi fallbacków (znane z incydentów braku danych)
DEFAULT_FORBIDDEN_FALLBACKS: Tuple[str, ...] = (
    "Poezje i felietony z rubryki",
    "Brak tytułu",
    "Bez tytułu",
    "Untitled",
    "[brak]",
    "<brak>",
    "N/A",
    "null",
    "undefined",
)


# ==============================================================================
# 1. CANONICAL PATH GUARD
# ==============================================================================

def validate_canonical_path(
    path: Union[str, Path],
    vault_root: Optional[Union[str, Path]] = None,
    allow_nonexistent: bool = True,
) -> Path:
    """Weryfikuje czy podana ścieżka jest poprawna i leży wewnątrz kanonicznego drzewa LAB.

    Reguły:
    1. Ścieżka NIE MOŻE leżeć w /tmp, /private/tmp, /var/tmp ani /var/folders.
    2. Ścieżka MUSI leżeć wewnątrz kanonicznego korzenia skarbca LAB (vault_root).
    3. W przypadku naruszenia rzuca CanonicalPathError.
    """
    if not path:
        raise CanonicalPathError("Ścieżka nie może być pusta.")

    p = Path(path)
    # Rozwiąż ścieżkę bezwzględną
    try:
        resolved = p.resolve()
    except Exception as ex:
        raise CanonicalPathError(f"Nie udało się zresolwować ścieżki '{path}': {ex}") from ex

    # 1. Sprawdź zakazane prefiksy tymczasowe
    resolved_str = str(resolved)
    for forbidden in FORBIDDEN_TMP_PREFIXES:
        if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
            raise CanonicalPathError(
                f"Ścieżka '{path}' narusza Canonical Path Guard: "
                f"leży w zakazanym katalogu tymczasowym ('{resolved_str}'). "
                f"Zabronione jest operowanie na plikach tymczasowych systemu."
            )

    # 2. Ustal kanoniczny korzeń LAB
    if vault_root is not None:
        canonical_root = Path(vault_root).resolve()
    else:
        try:
            canonical_root = find_vault_root(resolved.parent if resolved.is_file() else resolved)
        except Exception:
            canonical_root = Path.cwd().resolve()

    canonical_root_str = str(canonical_root)
    # Korzeń skarbca sam również nie może być w /tmp
    for forbidden in FORBIDDEN_TMP_PREFIXES:
        if canonical_root_str == forbidden or canonical_root_str.startswith(forbidden + "/"):
            raise CanonicalPathError(
                f"Korzeń skarbca LAB '{canonical_root_str}' leży w zakazanym katalogu /tmp."
            )

    # 3. Sprawdź czy ścieżka leży wewnątrz drzewa LAB
    try:
        resolved.relative_to(canonical_root)
    except ValueError:
        raise CanonicalPathError(
            f"Ścieżka '{path}' ('{resolved_str}') narusza Canonical Path Guard: "
            f"leży poza kanonicznym drzewem LAB ('{canonical_root_str}')."
        )

    return resolved


def validate_script_execution(
    script_path: Optional[Union[str, Path]] = None,
    vault_root: Optional[Union[str, Path]] = None,
) -> Path:
    """Weryfikuje, czy aktualnie wykonywany skrypt nie został uruchomiony ze ścieżki /tmp

    ani spoza kanonicznego drzewa LAB. Rzuca CanonicalPathError w przypadku naruszenia.
    """
    target_script = script_path if script_path is not None else sys.argv[0]
    return validate_canonical_path(target_script, vault_root=vault_root, allow_nonexistent=False)


# ==============================================================================
# 2. IMMUTABLE ARTIFACT GUARD
# ==============================================================================

def is_release_path(path: Union[str, Path]) -> bool:
    """Sprawdza, czy ścieżka wskazuje na przestrzeń wydań (Produkt/ lub Produkt/_wersje/)."""
    p = Path(path)
    parts = [part.lower() for part in p.parts]
    return "produkt" in parts or "_wersje" in parts


def assert_release_immutable(
    target_path: Union[str, Path],
    strict_release_only: bool = False,
) -> None:
    """Weryfikuje, czy plik docelowy nie istnieje już na dysku.

    Zgodnie z zasadą Immutable Artifacts:
    - Istniejące wydanie (np. *_v1.3.pdf) w folderze Produkt/ lub Produkt/_wersje/
      jest niezmienne i NIE MOŻE być nadpisane.
    - Wszelkie zmiany wymagają podbicia wersji semantycznej (np. v1.4).
    - Jeśli plik istnieje, rzuca ImmutableReleaseError.
    """
    p = Path(target_path)
    if not p.exists():
        return

    # Jeśli strict_release_only=True, sprawdzaj tylko jeśli ścieżka jest w Produkt/
    if strict_release_only and not is_release_path(p):
        return

    raise ImmutableReleaseError(
        f"Blokada nadpisywania wydań (Immutable Artifact Guard): "
        f"plik docelowy '{p.name}' już istnieje pod ścieżką '{p}'. "
        f"Wydania są niemutowalne i nie wolno ich nadpisywać. Podbij wersję semantyczną!"
    )


# ==============================================================================
# 3. FAIL-HARD DATA VALIDATOR
# ==============================================================================

def validate_metadata_record(
    record: Dict[str, Any],
    required_fields: Sequence[str] = ("title", "year", "page"),
    forbidden_fallbacks: Sequence[str] = DEFAULT_FORBIDDEN_FALLBACKS,
    record_index: Optional[int] = None,
) -> None:
    """Waliduje rekord metadanych pod kątem kompletności i zakazu cichych fallbacków.

    Reguły:
    1. Każde wymagane pole z 'required_fields' musi istnieć i nie może być None ani puste.
    2. Wartości tekstowe pól nie mogą zawierać żadnego zakazanego wzorca fallbacku
       (np. 'Poezje i felietony z rubryki').
    3. W przypadku jakiegokolwiek naruszenia rzuca natychmiast CriticalDataMissingError.
    """
    rec_prefix = f"Rekord [{record_index}]" if record_index is not None else "Rekord metadanych"

    if not isinstance(record, dict):
        raise CriticalDataMissingError(
            f"{rec_prefix} musi być słownikiem (dict), otrzymano typ: {type(record).__name__}."
        )

    for field in required_fields:
        # A. Sprawdź obecność klucza
        if field not in record:
            raise CriticalDataMissingError(
                f"{rec_prefix} narusza integralność: brak wymaganego pola '{field}'. "
                f"Dostępne pola: {list(record.keys())}."
            )

        value = record[field]

        # B. Sprawdź czy nie None
        if value is None:
            raise CriticalDataMissingError(
                f"{rec_prefix} narusza integralność: pole '{field}' ma wartość None."
            )

        # C. Sprawdź wartości tekstowe
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise CriticalDataMissingError(
                    f"{rec_prefix} narusza integralność: pole '{field}' jest pustym ciągiem znaków."
                )

            # Sprawdź zakazane ciągi fallbackowe
            for fallback in forbidden_fallbacks:
                if fallback.lower() in trimmed.lower():
                    raise CriticalDataMissingError(
                        f"{rec_prefix} narusza regułę Fail-Hard (wykryto cichy fallback): "
                        f"pole '{field}' zawiera zakazaną frazę '{fallback}' w wartości '{value}'."
                    )

        # D. Sprawdź typy liczbowe (np. strona, rok)
        elif isinstance(value, (int, float)):
            if field in ("year", "rok") and value <= 0:
                raise CriticalDataMissingError(
                    f"{rec_prefix} narusza integralność: pole '{field}' ma nieprawidłową wartość numeryczną {value}."
                )
            if field in ("page", "strona") and value <= 0:
                raise CriticalDataMissingError(
                    f"{rec_prefix} narusza integralność: numer strony '{field}' ({value}) musi być > 0."
                )


def validate_metadata_batch(
    records: Iterable[Dict[str, Any]],
    required_fields: Sequence[str] = ("title", "year", "page"),
    forbidden_fallbacks: Sequence[str] = DEFAULT_FORBIDDEN_FALLBACKS,
) -> int:
    """Waliduje kolekcję rekordów metadanych. Zwraca liczbę zwalidowanych rekordów.

    Rzuca CriticalDataMissingError przy pierwszym napotkanym błędzie (fail-fast).
    """
    count = 0
    for idx, rec in enumerate(records):
        validate_metadata_record(
            record=rec,
            required_fields=required_fields,
            forbidden_fallbacks=forbidden_fallbacks,
            record_index=idx,
        )
        count += 1
    return count


# ==============================================================================
# 4. ATOMIC TEST-AND-PROMOTE PIPELINE
# ==============================================================================

def calculate_sha256(file_path: Union[str, Path]) -> str:
    """Deterministycznie oblicza sumę kontrolną SHA-256 dla podanego pliku."""
    p = Path(file_path)
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


# ==============================================================================
# 5. STANDARYZACJA NAZEWNICTWA PRODUKTÓW (LAB 3.0)
# ==============================================================================

def format_release_filename(
    project_id: str,
    version_tag: str,
    description: str,
    ext: str,
) -> str:
    """Formatuje kanoniczną nazwę pliku produktu według standardu LAB 3.0:
    Wzorzec: [ID_PROJEKTU]_[WERSJA]_[OPIS].[EXT]
    np. PR-xxx_v1.0_Produkt_Koncowy.pdf

    Zapewnia idealną widoczność wersji w wąskich kolumnach Findera/Obsidiana
    oraz perfekcyjne sortowanie chronologiczne w folderze _wersje/.
    """
    clean_pid = project_id.strip()
    clean_v = version_tag.strip().lower()
    if not clean_v.startswith("v"):
        clean_v = f"v{clean_v}"
    clean_desc = description.strip().replace(" ", "_")
    clean_ext = ext.strip()
    if not clean_ext.startswith("."):
        clean_ext = f".{clean_ext}"
    return f"{clean_pid}_{clean_v}_{clean_desc}{clean_ext}"


def parse_release_filename(filename: str) -> Optional[Dict[str, str]]:
    """Parsuje nazwę pliku wydania według standardu LAB:
    Wzorzec: ^(PR-\\d{3})_(v\\d+\\.\\d+(?:\\.\\d+)?)_(.+)\\.([a-zA-Z0-9]+)$
    Zwraca dict z polami: project_id, version, description, extension lub None.
    """
    import re
    m = re.match(r"^(PR-\d{3})_(v\d+\.\d+(?:\.\d+)?)_(.+)\.([a-zA-Z0-9]+)$", filename)
    if not m:
        return None
    return {
        "project_id": m.group(1),
        "version": m.group(2),
        "description": m.group(3),
        "extension": f".{m.group(4)}",
    }



def promote_release(
    staged_file: Union[str, Path],
    target_dir: Union[str, Path],
    version_tag: str,
    test_command: Sequence[str],
    metadata: Optional[Dict[str, Any]] = None,
    vault_root: Optional[Union[str, Path]] = None,
    target_filename: Optional[str] = None,
) -> Path:
    """Wykonuje atomową promocję wydania z bramą testową i generacją manifestu.

    Kroki procedury:
    1. Weryfikuje poprawność ścieżek przez Canonical Path Guard.
    2. Sprawdza istnienie pliku roboczego staged_file.
    3. Określa ścieżkę pliku docelowego i weryfikuje Immutable Artifact Guard
       (jeśli plik wersji już istnieje, natychmiast blokuje procedurę).
    4. Uruchamia test_command (bramę testową).
       - Jeśli exit code != 0: kasuje staged_file i rzuca TestGateFailedError.
    5. Jeśli brama testowa przeszła sukcesem (exit code 0):
       - Atomowo przenosi (os.replace) staged_file do target_dir pod niezmienną nazwą.
       - Generuje obok plik *.manifest.json zawierający SHA-256, rozmiar, stempel ISO,
         wersję semantyczną oraz metadane audytowe.
    6. Zwraca Path do promowanego pliku wydania.
    """
    # 1. Canonical Path Guard & Allowlist Enforcement
    staged_path = validate_canonical_path(staged_file, vault_root=vault_root)
    target_dir_path = validate_canonical_path(target_dir, vault_root=vault_root)

    # Zasada Białej Listy: wydania mogą trafiać WYŁĄCZNIE do autoryzowanej przestrzeni Produkt/
    if not is_release_path(target_dir_path):
        raise CanonicalPathError(
            f"Zasada Białej Listy (Allowlist Violation): katalog docelowy promocji wydania '{target_dir_path}' "
            f"nie wskazuje na autoryzowaną przestrzeń 'Produkt/' ani 'Produkt/_wersje/'. "
            f"Wszelkie artefakty wydań MUSZĄ trafiać do Produkt/ (zakaz tworzenia katalogów typu artefakt, dzieło, output)."
        )

    # 2. Sprawdź czy plik roboczy istnieje
    if not staged_path.is_file():
        raise FileNotFoundError(f"Plik roboczy (staged_file) nie istnieje: '{staged_path}'.")

    # 3. Ustalenie nazwy pliku docelowego według standardu LAB 3.0 ([ID]_[Wersja]_[Opis].[ext])
    if target_filename:
        final_target = target_dir_path / target_filename
    else:
        import re
        stem = staged_path.stem
        suffix = staged_path.suffix
        m = re.match(r"^(PR-\d{3})_(.+)$", stem)
        if m:
            pid = m.group(1)
            rest = m.group(2)
            if re.match(r"^v\d+\.\d+", rest, re.IGNORECASE):
                final_target = target_dir_path / staged_path.name
            else:
                # Wstaw wersję bezpośrednio po ID projektu: [PR-xxx]_[vX.Y]_[opis].[ext]
                rest_clean = re.sub(r"_[vV]\d+\.\d+(?:\.\d+)?$", "", rest)
                canonical_name = format_release_filename(pid, version_tag, rest_clean, suffix)
                final_target = target_dir_path / canonical_name
        else:
            if version_tag in staged_path.name:
                final_target = target_dir_path / staged_path.name
            else:
                final_target = target_dir_path / f"{stem}_{version_tag}{suffix}"

    # Weryfikacja Immutable Artifact Guard
    assert_release_immutable(final_target)

    # 4. Wykonanie bramy testowej
    if not test_command:
        raise ValueError("Polecenie testu (test_command) nie może być pustą listą.")

    try:
        proc = subprocess.run(
            list(test_command),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as ex:
        # Jeśli nie udało się nawet uruchomić testu, skasuj staged_file i rzuć błąd
        if staged_path.exists():
            staged_path.unlink()
        raise TestGateFailedError(f"Nie udało się uruchomić bramy testowej: {ex}") from ex

    if proc.returncode != 0:
        # Test nie przeszedł - bezwzględne skasowanie staged_file
        if staged_path.exists():
            staged_path.unlink()
        raise TestGateFailedError(
            f"Brama testowa nie przeszła (kod wyjścia: {proc.returncode}). "
            f"Plik roboczy '{staged_path.name}' został usunięty.\n"
            f"Polecenie: {' '.join(test_command)}\n"
            f"--- STDOUT ---\n{proc.stdout}\n"
            f"--- STDERR ---\n{proc.stderr}"
        )

    # 5. Promocja atomowa
    target_dir_path.mkdir(parents=True, exist_ok=True)
    os.replace(staged_path, final_target)

    # 6. Generacja manifestu wydania (*.manifest.json)
    file_sha256 = calculate_sha256(final_target)
    file_size = final_target.stat().st_size
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    manifest_payload: Dict[str, Any] = {
        "manifest_schema": "lab-release-manifest-v1",
        "file_name": final_target.name,
        "semantic_version": version_tag,
        "sha256": file_sha256,
        "size_bytes": file_size,
        "created_at_utc": now_iso,
        "test_gate": {
            "command": list(test_command),
            "exit_code": proc.returncode,
            "status": "passed",
        },
        "audit_metadata": metadata or {},
    }

    manifest_path = final_target.parent / f"{final_target.name}.manifest.json"
    manifest_tmp = final_target.parent / f".{final_target.name}.manifest.json.tmp"
    manifest_tmp.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(manifest_tmp, manifest_path)

    return final_target


# ==============================================================================
# 6. HUMAN THOUGHTS PROTECTION GUARD (WARSZTAT / SPRINT INJECTION GUARD)
# ==============================================================================

def assert_sprint_task_authorized(
    task_title: str,
    is_user_confirmed: bool,
) -> None:
    """Weryfikuje autoryzację wpisania zadania do Sprintu lub Warsztatu Współpracy.

    Zgodnie z nienaruszalną wolą Właściciela Systemu i polityką [policies.human_thoughts]:
    - Samowolne dopisywanie nieuporządkowanych myśli, idei i haseł użytkownika
      do Warsztatu lub Sprintu jest bezwzględnie zakazane.
    - AI ma tendencję do nadinterpretacji haseł użytkownika i zapisywania ich
      jako własne sformułowania.
    - Każde zadanie wymaga uprzedniego zaproponowania i wyraźnej zgody człowieka.
    - Rzuca UnauthorizedSprintInjectionError, jeśli is_user_confirmed=False.
    """
    clean_title = str(task_title).strip() if task_title is not None else ""
    if not clean_title:
        raise UnauthorizedSprintInjectionError("Tytuł zadania sprintu nie może być pusty.")

    if not is_user_confirmed:
        raise UnauthorizedSprintInjectionError(
            f"Próba nieautoryzowanego wpisania zadania do Sprintu/Warsztatu: '{clean_title}'. "
            f"Zgodnie z polityką ochrony myśli użytkownika [policies.human_thoughts], "
            f"AI nie może samowolnie dodawać zadań ani interpretować nieuporządkowanych myśli. "
            f"Wymagana jest uprzednia propozycja i wyraźne potwierdzenie człowieka."
        )


def validate_no_unauthorized_sprint_injection(
    tasks: Iterable[Union[Dict[str, Any], Tuple[str, bool]]],
) -> int:
    """Waliduje kolekcję zadań przeznaczonych do sprintu/warsztatu.

    Przyjmuje kolekcję słowników (z kluczami title/task_title oraz is_user_confirmed/confirmed)
    lub krotek (task_title, is_user_confirmed).

    Dla każdego elementu egzekwuje assert_sprint_task_authorized.
    Zwraca liczbę pomyślnie zwalidowanych zadań.
    """
    count = 0
    for idx, item in enumerate(tasks):
        if isinstance(item, tuple) and len(item) == 2:
            title, confirmed = item
        elif isinstance(item, dict):
            title = item.get("title") or item.get("task_title") or ""
            confirmed = bool(item.get("is_user_confirmed", item.get("confirmed", False)))
        else:
            raise UnauthorizedSprintInjectionError(
                f"Element zadania [{idx}] ma nieprawidłowy format: {item!r}. "
                f"Oczekiwano słownika lub krotki (task_title, is_user_confirmed)."
            )
        assert_sprint_task_authorized(task_title=str(title), is_user_confirmed=confirmed)
        count += 1
    return count



# ==============================================================================
# 7. WORKSHOP & SPRINT INTEGRITY GUARD (PR, OP vs RS & CLEAN WORKBENCH)
# ==============================================================================

FORBIDDEN_WORKSHOP_SECTIONS: Tuple[str, ...] = (
    "Pokoje Robocze",
    "Zrzut ze Skrzynki",
    "Filar IV: Badania i Synteza Wiedzy",
)


def assert_workshop_task_type_allowed(task_title: str) -> None:
    """Weryfikuje typ zadania przeznaczonego na Warsztat lub do Sprintu.

    Zgodnie z wolą Właściciela Systemu i polityką [policies.workshop]:
    - Na Warsztacie i w sprincie dozwolone są wyłącznie zadania projektowe (PR-xxx)
      oraz operacyjne (OP-xxx).
    - Zadania badawcze/research (RS-xxx) realizowane są w Bazie Wiedzy (05 Baza Wiedzy)
      i nie mogą zaśmiecać stołu roboczego Warsztatu ani Sprintu.
    - W przypadku wykrycia zadania badawczego rzuca WorkshopPolicyViolationError.
    """
    clean = str(task_title).strip()
    import re
    # Sprawdź czy zadanie odnosi się do identyfikatora RS (Research / Badania)
    if re.search(r"\bRS-\d{3}", clean) or "[[RS-" in clean or clean.lower().startswith("badanie:"):
        raise WorkshopPolicyViolationError(
            f"Naruszenie polityki Warsztatu [policies.workshop]: zadanie badawcze '{clean}' "
            f"nie może znajdować się na Warsztacie ani w sprincie. "
            f"Zadania typu 'research' (RS-xxx) odbywają się i żyją wyłącznie w Bazie Wiedzy (05 Baza Wiedzy). "
            f"Na Warsztacie dozwolone są wyłącznie zadania: PR (projekty) oraz OP (operacje)."
        )


def assert_workshop_content_clean(content: str) -> None:
    """Weryfikuje strukturę notatek Warsztatu (Agenda Robocza / Dashboard) pod kątem czystości.

    Wymogi:
    1. Brak sekcji 'Pokoje Robocze' (każdy użytkownik zna topografię pokoi, nie zaśmiecać stołu).
    2. Brak sekcji 'Zrzut ze Skrzynki' (archiwizacja odbywa się automatycznie do archiwum Skrzynki i Dziennika Dnia).
    3. Brak sekcji 'Filar IV: Badania i Synteza Wiedzy' (badania żyją w 05 Baza Wiedzy).
    4. Brak zadań badawczych RS w sekcji 'Na tapecie (Bieżący Sprint)'.
    """
    import re

    # 1. Sprawdź zakazane sekcje
    for forbidden in FORBIDDEN_WORKSHOP_SECTIONS:
        if re.search(rf"^#+\s+.*{re.escape(forbidden)}", content, flags=re.MULTILINE | re.IGNORECASE):
            raise WorkshopPolicyViolationError(
                f"Naruszenie struktury Warsztatu: wykryto zakazaną sekcję '{forbidden}'. "
                f"Sekcje pokojów roboczych, zrzutów ze skrzynki oraz badań (RS) "
                f"są bezwzględnie usunięte ze stołu roboczego Warsztatu."
            )

    # 2. Sprawdź czy w sekcji sprintu nie ma zadań RS
    sprint_match = re.search(r"## 🎯 Na tapecie \(Bieżący Sprint\)(.*?)(?=\n## |\Z)", content, flags=re.DOTALL)
    if sprint_match:
        sprint_text = sprint_match.group(1)
        for line in sprint_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("- [") and ("RS-" in line_str or "[[RS-" in line_str):
                raise WorkshopPolicyViolationError(
                    f"Wykryto zadanie badawcze RS w bieżącym sprincie na Warsztacie: '{line_str}'. "
                    f"Zadania researchu należą do Bazy Wiedzy, a nie na Warsztat."
                )


def assert_raw_thoughts_preserved(old_content: str, new_content: str) -> None:
    """Weryfikuje Regułę 1 przy oznaczaniu/przetwarzaniu bloków myśli w sekcji '## ✍️' Agendy Roboczej.

    Dopuszcza dopisywanie przez Edytę nowych wątków do '## 🎯' oraz dodanie znacznika
    przetworzenia ('<!-- processed -->', '<!-- przetworzone -->', '<!-- distilled -->', '> ✅', '> 📦')
    w '## ✍️', pod warunkiem 100% zachowania dotychczasowych linii tekstu człowieka.
    """
    import re
    from lab_core.constants import THOUGHT_PROCESSED_MARKERS

    def _extract_thoughts_lines(text: str) -> List[str]:
        m = re.search(r"^##\s*(?:✍️|.*Dosłowny Zapis).*$", text, flags=re.MULTILINE | re.IGNORECASE)
        if not m:
            return []
        after = text[m.end():]
        nxt = re.search(r"^##\s+", after, flags=re.MULTILINE)
        body = after[:nxt.start()] if nxt else after
        out: List[str] = []
        for raw_ln in body.splitlines():
            ln = raw_ln.strip()
            if not ln or ln == "---":
                continue
            low = ln.lower()
            if any(mk in low for mk in THOUGHT_PROCESSED_MARKERS):
                continue
            if ln.startswith("> ✅") or ln.startswith("> 📦"):
                continue
            out.append(ln)
        return out

    old_lines = _extract_thoughts_lines(old_content)
    if not old_lines:
        return
    new_lines_set = set(_extract_thoughts_lines(new_content))
    missing = [ln for ln in old_lines if ln not in new_lines_set]
    if missing:
        raise UnauthorizedSprintInjectionError(
            f"Naruszenie Reguły 1 (ochrona tekstu użytkownika): w sekcji '## ✍️' usunięto lub zmieniono "
            f"oryginalną linię myśli człowieka: '{missing[0]}'."
        )


# ==============================================================================
# CLI / SELF-CHECK
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Strażnik wydań i egzekucji kodu LAB 3.0")
    parser.add_argument("--check-script", action="store_true", help="Zweryfikuj ścieżkę bieżącego skryptu")
    parser.add_argument("--sha256", help="Oblicz sumę SHA-256 dla podanego pliku")
    args = parser.parse_args()

    if args.check_script:
        try:
            validated = validate_script_execution()
            print(f"🟢 Canonical Path Guard OK: {validated}")
            return 0
        except CanonicalPathError as ex:
            print(f"🔴 Canonical Path Guard ERROR: {ex}", file=sys.stderr)
            return 1

    if args.sha256:
        p = Path(args.sha256)
        if not p.is_file():
            print(f"Błąd: plik '{args.sha256}' nie istnieje.", file=sys.stderr)
            return 1
        print(f"{calculate_sha256(p)}  {p.name}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
