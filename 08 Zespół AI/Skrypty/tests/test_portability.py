#!/usr/bin/env python3
"""test_portability.py - Testy inspektora przenośności (PortabilityInspector)
Standard: 100% Python Standard Library (Zero-Pip).
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

from core_config import ConfigManager
from lab_core.installer import install_lab_vault
from lab_core.quality.context import QualityContext
from lab_core.quality.inspectors.portability import PortabilityInspector


class TestPortabilityInspector(unittest.TestCase):
    def test_detects_legacy_instance_names_and_hardcoded_lab_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True)
            scripts_dir = vault / "08 Zespół AI" / "Skrypty"
            scripts_dir.mkdir(parents=True, exist_ok=True)

            bad_script = scripts_dir / "bad_module.py"
            bad_script.write_text(
                "# Komentarz ze słowem Studio i Larry jest ignorowany\n"
                'NAME = "myICOR"\n'
                'META = \'instance: "LAB"\'\n'
                'TARGET = ws_dir / "Dashboard LAB.md"\n',
                encoding="utf-8",
            )

            # Plik w tests/ musi być ignorowany
            tests_dir = scripts_dir / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_ok.py").write_text('X = "JAZZ"\n', encoding="utf-8")

            cfg = ConfigManager(vault_root=vault)
            ctx = QualityContext(vault_root=vault, config=cfg)
            PortabilityInspector().inspect(ctx)

            errors = [f for f in ctx.findings if f.level == "error" and f.category == "portability"]
            self.assertEqual(len(errors), 3)
            self.assertEqual(ctx.stats.get("portability_violations"), 3)
            joined = "\n".join(e.message for e in errors)
            self.assertIn("myICOR", joined)
            self.assertIn("LAB", joined)
            self.assertNotIn("JAZZ", joined)


if __name__ == "__main__":
    unittest.main()
