#!/usr/bin/env python3
"""pytest.py - Zero-Pip standard library test runner for LAB 2.0 (Framework PZW)

Realizuje wywołanie 'python3 -m pytest' w 100% w oparciu o bibliotekę standardową (unittest + importlib).
Dzięki temu środowisko nie wymaga żadnych pakietów pip, zachowując pełną kompatybilność
z poleceniami automatycznymi i skryptami CI/CD.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import unittest
from pathlib import Path

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca LAB
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def load_test_suite_from_file(file_path: Path) -> unittest.TestSuite:
    """Ładuje testy z pojedynczego pliku bez wymogu, aby foldery były pakietami Python."""
    suite = unittest.TestSuite()
    module_name = f"dynamic_test_{file_path.stem}"

    # Dodaj katalog pliku oraz korzeń do sys.path
    if str(file_path.parent) not in sys.path:
        sys.path.insert(0, str(file_path.parent))
    cwd = str(Path.cwd().resolve())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        return suite

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)

    loader = unittest.TestLoader()
    file_suite = loader.loadTestsFromModule(mod)
    suite.addTest(file_suite)
    return suite


def main() -> int:
    raw_args = sys.argv[1:]
    target_paths = []
    verbosity = 2
    pattern = "test_*.py"

    i = 0
    while i < len(raw_args):
        arg = raw_args[i]
        if arg in ("-v", "--verbose"):
            verbosity = 2
        elif arg in ("-q", "--quiet"):
            verbosity = 0
        elif arg.startswith("-k"):
            if "=" not in arg and i + 1 < len(raw_args):
                i += 1
        elif not arg.startswith("-"):
            target_paths.append(arg)
        i += 1

    overall_suite = unittest.TestSuite()

    if target_paths:
        for t_path in target_paths:
            p = Path(t_path).resolve()
            if p.is_file():
                overall_suite.addTest(load_test_suite_from_file(p))
            elif p.is_dir():
                for test_file in p.rglob(pattern):
                    if test_file.is_file():
                        overall_suite.addTest(load_test_suite_from_file(test_file))
            else:
                print(f"Błąd: ścieżka '{t_path}' nie istnieje.", file=sys.stderr)
                return 1
    else:
        # Domyślnie szukaj w 08 Zespół AI/Skrypty
        scripts_dir = Path("08 Zespół AI/Skrypty").resolve()
        if scripts_dir.is_dir():
            for test_file in scripts_dir.glob(pattern):
                if test_file.is_file():
                    overall_suite.addTest(load_test_suite_from_file(test_file))

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(overall_suite)

    # Sprzątanie ewentualnych artefaktów bytecode
    for pycache_dir in [Path.cwd() / "__pycache__", scripts_dir / "__pycache__" if 'scripts_dir' in locals() else None]:
        if pycache_dir and pycache_dir.is_dir():
            try:
                shutil.rmtree(pycache_dir, ignore_errors=True)
            except Exception:
                pass

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
