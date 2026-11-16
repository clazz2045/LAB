"""lab_core.env - Wykrywanie środowiska, ścieżek i ochrona przed zanieczyszczeniami
Standard inżynieryjny: 100% Python Standard Library. Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

# Wymuszenie braku generowania folderów __pycache__ w iCloud
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Zakazane prefiksy tymczasowe OS
FORBIDDEN_TMP_PREFIXES: tuple[str, ...] = (
    "/tmp",
    "/private/tmp",
    "/var/tmp",
    "/var/folders",
    "/private/var/folders",
    "/private/var/tmp",
)


class EnvironmentError(Exception):
    """Bazowy błąd środowiska LAB."""
    pass


class CanonicalPathError(EnvironmentError):
    """Podnoszony w przypadku próby użycia ścieżki poza skarbem lub w folderze tymczasowym."""
    pass


def find_vault_root(
    start_dir: Optional[Union[str, Path]] = None,
    root_indicator: str = "lab.config.toml"
) -> Path:
    """Dynamicznie wykrywa katalog główny skarbca (Vault Root).

    1. Sprawdza zmienną środowiskową VAULT_ROOT (jeśli ustawiona i zawiera root_indicator).
    2. Wspina się w górę drzewa katalogów od start_dir (domyślnie katalog roboczy)
       w poszukiwaniu pliku root_indicator.
    3. Rzuca EnvironmentError (lub ConfigNotFoundError), jeśli wskaźnik korzenia nie zostanie znaleziony.
    """
    env_root = os.environ.get("VAULT_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / root_indicator).is_file():
            return candidate

    current = Path(start_dir).resolve() if start_dir else Path.cwd().resolve()
    if current.is_file():
        current = current.parent

    for directory in [current, *current.parents]:
        if (directory / root_indicator).is_file():
            return directory

    raise EnvironmentError(
        f"Nie odnaleziono korzenia skarbca. Plik '{root_indicator}' nie istnieje "
        f"w katalogu '{current}' ani w żadnym z jego katalogów nadrzędnych. "
        f"Upewnij się, że plik konfiguracyjny istnieje lub zdefiniuj zmienną środowiskową VAULT_ROOT."
    )


def ensure_vault_in_sys_path(start_path: Optional[Union[str, Path]] = None) -> Path:
    """Gwarantuje obecność korzenia skarbca oraz katalogu skryptów w sys.path.

    Eliminuje powielany w każdym skrypcie blok bootstrapu.
    Zwraca odnaleziony korzeń skarbca.
    """
    vault_root = find_vault_root(start_path)
    root_str = str(vault_root)
    scripts_str = str(vault_root / "08 Zespół AI" / "Skrypty")

    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)

    return vault_root


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
    try:
        resolved = p.resolve()
    except Exception as ex:
        raise CanonicalPathError(f"Nie udało się zresolwować ścieżki '{path}': {ex}") from ex

    resolved_str = str(resolved)
    for forbidden in FORBIDDEN_TMP_PREFIXES:
        if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
            raise CanonicalPathError(
                f"Ścieżka '{path}' narusza Canonical Path Guard: "
                f"leży w zakazanym katalogu tymczasowym ('{resolved_str}'). "
                f"Zabronione jest operowanie na plikach tymczasowych systemu."
            )

    if vault_root is not None:
        canonical_root = Path(vault_root).resolve()
    else:
        try:
            canonical_root = find_vault_root(resolved.parent if resolved.is_file() else resolved)
        except Exception:
            canonical_root = Path.cwd().resolve()

    canonical_root_str = str(canonical_root)
    for forbidden in FORBIDDEN_TMP_PREFIXES:
        if canonical_root_str == forbidden or canonical_root_str.startswith(forbidden + "/"):
            raise CanonicalPathError(
                f"Korzeń skarbca LAB '{canonical_root_str}' leży w zakazanym katalogu /tmp."
            )

    try:
        resolved.relative_to(canonical_root)
    except ValueError:
        raise CanonicalPathError(
            f"Ścieżka '{path}' ('{resolved_str}') narusza Canonical Path Guard: "
            f"leży poza kanonicznym drzewem LAB ('{canonical_root_str}')."
        )

    return resolved
