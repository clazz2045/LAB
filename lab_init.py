"""
lab_init.py - Moduł biblioteczny generatora środowiska LAB 2.0 (Framework PZW)
Mostek umożliwiający standardowy import Pythona `import lab_init`
oraz uruchamianie przez CLI `python3 lab-init.py`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_script_path = Path(__file__).parent / "lab-init.py"
_spec = importlib.util.spec_from_file_location("lab_init_core", _script_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Nie można załadować modułu z: {_script_path}")

_mod = importlib.util.module_from_spec(_spec)
sys.modules["lab_init"] = _mod
_spec.loader.exec_module(_mod)

THEME_PRESETS = _mod.THEME_PRESETS
get_theme_preset = _mod.get_theme_preset
list_theme_presets = _mod.list_theme_presets
LabInitializer = _mod.LabInitializer
generate_agents_contract = _mod.generate_agents_contract
generate_design_tokens_css = _mod.generate_design_tokens_css
generate_room_readme = _mod.generate_room_readme
generate_projects_index = _mod.generate_projects_index
generate_operations_index = _mod.generate_operations_index
generate_workshop_entry = _mod.generate_workshop_entry
init_environment = _mod.init_environment
main = _mod.main
