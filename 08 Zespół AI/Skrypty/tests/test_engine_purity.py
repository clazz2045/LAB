#!/usr/bin/env python3
"""test_engine_purity.py - Testy 3-strefowego standardu skryptów i inspektora EnginePurityInspector
Standard: 100% Python Standard Library (unittest, tempfile). Zero-Pip.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from core_config import ConfigManager, find_vault_root
from lab_core.constants import CANONICAL_ENGINE_ALLOWLIST
from lab_core.quality.context import QualityContext
from lab_core.quality.inspectors.engine_purity import EnginePurityInspector
from lab_core.quality.inspectors.readmes import ReadmesInspector
from lab_core.quality.inspectors.workshop import WorkshopAndThoughtsInspector


class TestEnginePurityAndCodeLayout(unittest.TestCase):
    """Weryfikuje [storage.code_layout], białą listę jądra oraz wyjątki dla 02 Warsztat/Skrypty/."""

    def test_config_loads_storage_code_layout(self) -> None:
        vault_root = find_vault_root(_script_dir)
        cfg = ConfigManager(vault_root=vault_root)
        self.assertEqual(cfg.code_layout.canonical_engine, "08 Zespół AI/Skrypty")
        self.assertEqual(cfg.code_layout.workshop_tools, "02 Warsztat/Skrypty")
        self.assertEqual(cfg.code_layout.projects_pattern, "03 Projekty/{project_id}/**")

    def test_real_engine_passes_allowlist(self) -> None:
        vault_root = find_vault_root(_script_dir)
        cfg = ConfigManager(vault_root=vault_root)
        ctx = QualityContext(vault_root=vault_root, config=cfg)
        EnginePurityInspector().inspect(ctx)
        self.assertEqual(ctx.stats.get("engine_purity_violations"), 0, ctx.findings)
        self.assertEqual(ctx.findings, [])

    def test_engine_purity_rejects_non_canonical_files_and_ignores_temp(self) -> None:
        vault_root = find_vault_root(_script_dir)
        cfg_text = (vault_root / "lab.config.toml").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_vault = Path(tmp_str) / "Vault"
            tmp_vault.mkdir(parents=True, exist_ok=True)
            (tmp_vault / "lab.config.toml").write_text(cfg_text, encoding="utf-8")

            scripts_dir = tmp_vault / "08 Zespół AI" / "Skrypty"
            (scripts_dir / "lab_core").mkdir(parents=True, exist_ok=True)
            (scripts_dir / "__pycache__").mkdir(parents=True, exist_ok=True)
            (scripts_dir / ".tmp_sandbox").mkdir(parents=True, exist_ok=True)

            # Dozwolone kanoniczne + ignorowane tymczasowe
            (scripts_dir / "check_quality.py").write_text("# ok\n", encoding="utf-8")
            (scripts_dir / "lab_core" / "__init__.py").write_text("# ok\n", encoding="utf-8")
            (scripts_dir / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
            (scripts_dir / "__pycache__" / "check_quality.cpython-315.pyc").write_bytes(b"0")
            (scripts_dir / ".tmp_sandbox" / "temp_helper.py").write_text("# tmp\n", encoding="utf-8")
            (scripts_dir / "scratch.tmp").write_text("tmp", encoding="utf-8")
            (scripts_dir / ".DS_Store").write_bytes(b"")

            # Niekanoniczne pliki (w głównym katalogu oraz w podkatalogu)
            (scripts_dir / "ad_hoc_fix.py").write_text("print(1)\n", encoding="utf-8")
            (scripts_dir / "lab_core" / "unofficial_mod.py").write_text("print(2)\n", encoding="utf-8")

            cfg = ConfigManager(vault_root=tmp_vault)
            ctx = QualityContext(vault_root=tmp_vault, config=cfg)
            EnginePurityInspector().inspect(ctx)

            errors = [f for f in ctx.findings if f.level == "error" and f.category == "engine_purity"]
            self.assertEqual(len(errors), 2)
            self.assertEqual(ctx.stats.get("engine_purity_violations"), 2)
            msgs = "\n".join(e.message for e in errors)
            self.assertIn("Wykryto niekanoniczny plik w jądrze: ad_hoc_fix.py", msgs)
            self.assertIn("Wykryto niekanoniczny plik w jądrze: lab_core/unofficial_mod.py", msgs)
            self.assertIn("Przenieś go do '02 Warsztat/Skrypty/'", msgs)

    def test_workshop_scripts_readme_allowed_by_readmes_and_workshop_inspectors(self) -> None:
        vault_root = find_vault_root(_script_dir)
        cfg_text = (vault_root / "lab.config.toml").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_vault = Path(tmp_str) / "Vault"
            ws_scripts = tmp_vault / "02 Warsztat" / "Skrypty"
            ws_scripts.mkdir(parents=True, exist_ok=True)
            (tmp_vault / "lab.config.toml").write_text(cfg_text, encoding="utf-8")
            (ws_scripts / "README.md").write_text("# Skrypty Warsztatowe\n", encoding="utf-8")

            cfg = ConfigManager(vault_root=tmp_vault)

            ctx_readme = QualityContext(vault_root=tmp_vault, config=cfg)
            ReadmesInspector().inspect(ctx_readme)
            self.assertEqual(ctx_readme.stats.get("foreign_readmes"), 0)
            self.assertEqual(ctx_readme.findings, [])

            ctx_ws = QualityContext(vault_root=tmp_vault, config=cfg)
            WorkshopAndThoughtsInspector().inspect(ctx_ws)
            ws_warnings = [f for f in ctx_ws.findings if f.category == "workshop"]
            self.assertEqual(ws_warnings, [])

            # Obce README poza 02 Warsztat/Skrypty nadal powoduje błąd
            (tmp_vault / "03 Projekty").mkdir(parents=True, exist_ok=True)
            (tmp_vault / "03 Projekty" / "README.md").write_text("# Obce\n", encoding="utf-8")
            ctx_readme_bad = QualityContext(vault_root=tmp_vault, config=cfg)
            ReadmesInspector().inspect(ctx_readme_bad)
            self.assertEqual(ctx_readme_bad.stats.get("foreign_readmes"), 1)

    def test_allowlist_contains_core_entrypoints(self) -> None:
        for req in (
            "session_bootstrap.py",
            "check_quality.py",
            "checkpoint.py",
            "rollover_agenda.py",
            "core_config.py",
            "projects.py",
            "drain_inbox.py",
            ".gitignore",
            "README",
        ):
            self.assertIn(req, CANONICAL_ENGINE_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
