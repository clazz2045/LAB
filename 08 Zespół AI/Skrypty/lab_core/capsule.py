#!/usr/bin/env python3
"""package_capsule.py - Pakowanie wzorca Kapsuły Projektowej do ZIP wraz z weryfikacją (Quality Gate).

Standard inżynieryjny:
- 100% Python Standard Library (zipfile, tempfile, shutil, os, sys).
- Zapisuje wpisy katalogów (directory entries) oraz pliki ukryte (.gitkeep), zapobiegając ucinaniu pustej Skrzynki.
- Po spakowaniu przeprowadza automatyczny test rozpakowania i weryfikację obecności wszystkich 5 pokoi.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
from lab_core.env import find_vault_root
_root_dir = find_vault_root(_script_dir)
for _p in (_script_dir, _root_dir):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lab_core.config import ConfigManager
from lab_core.installer import install_project_capsule


def assert_portability(capsule_src: Path) -> None:
    """Quality Gate Ady: Bezwzględny zakaz pakowania plików z lokalnymi ścieżkami konta gospodarza."""
    forbidden = ("/Users/", "/home/", "C:\\Users", "C:/Users")
    violations = []
    
    for p in capsule_src.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in {".json", ".toml", ".yaml", ".yml", ".sh"}:
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                for f_prefix in forbidden:
                    if f_prefix in content:
                        violations.append(f"{p.relative_to(capsule_src)} (wykryto '{f_prefix}')")
            except Exception:
                pass
        elif ext == ".md":
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                for f_prefix in forbidden:
                    # W plikach markdown sprawdzamy rzeczywiste linki do ścieżek
                    if f"({f_prefix}" in content or f'"{f_prefix}' in content or f"file://{f_prefix}" in content:
                        violations.append(f"{p.relative_to(capsule_src)} (wykryto link '{f_prefix}')")
            except Exception:
                pass

    if violations:
        raise RuntimeError(
            f"Quality Gate FAILED: Wykryto bezwzględne ścieżki konta gospodarza w plikach szablonu: {violations}"
        )


def build_and_verify_capsule_zip(
    capsule_src: Path,
    target_zip: Path,
    expected_rooms: tuple[str, ...] = ("01 Skrzynka", "02 Warsztat", "03 Projekty", "07 Bazy Danych", "08 Zespół AI"),
) -> None:
    # 0. Weryfikacja przenośności (brak ścieżek /Users/ itp.)
    assert_portability(capsule_src)

    # 1. Upewnij się, że Skrzynka ma .gitkeep
    inbox_dir = capsule_src / "01 Skrzynka"
    if inbox_dir.is_dir():
        keep_file = inbox_dir / ".gitkeep"
        if not keep_file.is_file():
            keep_file.write_text("", encoding="utf-8")

    # 2. Pakowanie z jawnym zapisem wpisów katalogowych
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    if target_zip.exists():
        target_zip.unlink()

    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(capsule_src):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache"}]
            # Wpisz katalogi (aby puste foldery zostały zachowane)
            for d in dirs:
                dp = Path(root) / d
                arc = dp.relative_to(capsule_src)
                zf.write(dp, str(arc))
            # Wpisz pliki
            for f in files:
                if f in {".DS_Store"} or f.endswith(".pyc"):
                    continue
                fp = Path(root) / f
                arc = fp.relative_to(capsule_src)
                zf.write(fp, str(arc))

    # 3. Quality Gate Ady: Automatyczna ekstrakcja do tmp i weryfikacja
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        with zipfile.ZipFile(target_zip, "r") as zf:
            zf.extractall(tmp_dir)

        missing = []
        for r in expected_rooms:
            if not (tmp_dir / r).is_dir():
                missing.append(r)

        if missing:
            raise RuntimeError(
                f"Quality Gate FAILED: Po rozpakowaniu ZIP brakuje pokoi: {missing}"
            )

    print(f"✅ Paczka ZIP zweryfikowana pomyślnie: '{target_zip.name}' ({target_zip.stat().st_size} B). Wszystkie pokoje obecne po rozpakowaniu.")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Budowa paczki ZIP Kapsuły Projektowej LAB.")
    parser.add_argument("--target-dir", type=Path, help="Katalog źródłowy szablonu kapsuły")
    parser.add_argument("--out-zip", type=Path, help="Ścieżka wynikowego pliku .zip")
    args = parser.parse_args()

    if not args.target_dir or not args.out_zip:
        print("ℹ️ Szablony kapsuł projektowych w LAB są tworzone dynamicznie wg Konstytucji LAB.")
        print("   Użyj: python3 -m lab_core.capsule --target-dir <katalog> --out-zip <plik.zip>")
        return 0

    build_and_verify_capsule_zip(args.target_dir, args.out_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
