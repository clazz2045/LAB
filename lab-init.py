#!/usr/bin/env python3
"""
lab-init.py - Zunifikowany Punkt Wejścia Instalatora Środowiska LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (Zero-Pip).

Deleguje do zunifikowanego silnika `lab_core.installer` oraz kreatora `lab_core.setup`
(kreator WWW `http.server` + tryb `--non-interactive` + Post-Install Smoke Check).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent
_scripts_ai = _script_dir / "08 Zespół AI" / "Skrypty"
for _candidate in [_script_dir, _scripts_ai]:
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from lab_core.config import ConfigError, ConfigManager
from lab_core.env import ensure_vault_in_sys_path

try:
    ensure_vault_in_sys_path(_script_dir)
except Exception:
    pass

from lab_core.installer import (
    LAB_VERSION,
    InstallOptions,
    LabEngineInstaller,
)
from lab_core.setup import main as setup_main
from lab_core.themes import (
    THEME_PRESETS,
    generate_design_tokens_css,
    get_theme_preset,
    list_theme_presets,
)
from lab_core.templates import (
    generate_agents_contract,
    generate_operations_index,
    generate_projects_index,
    generate_room_readme,
    generate_workshop_entry,
)


class LabInitializer:
    """Adapter wstecznej kompatybilności delegujący do LabEngineInstaller (LAB 3.0)."""

    def __init__(
        self,
        config: ConfigManager,
        force: bool = False,
        verbose: bool = True,
        install_ai_team: bool = False,
        template_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.config = config
        self.vault_root = config.vault_root
        self.force = force
        self.verbose = verbose
        self.install_ai_team = install_ai_team
        self.template_dir = Path(template_dir).resolve() if template_dir else None
        self.ai_team_installed_count = 0
        self.actions_log: List[Tuple[str, str]] = []

    def run(self) -> Dict[str, Any]:
        """Uruchamia zunifikowany instalator dla wskazanej konfiguracji."""
        opts = InstallOptions(
            target_dir=self.vault_root,
            instance_name=self.config.system.instance_name,
            user_name=self.config.user.name,
            call_sign=self.config.user.call_sign,
            theme_preset=self.config.presentation.theme_preset,
            theme_preset_light=self.config.presentation.theme_preset_light,
            theme_preset_dark=self.config.presentation.theme_preset_dark,
            theme_mode=self.config.presentation.theme_mode,
            coordinator_name=self.config.get_agent_name("coordinator"),
            governance_name=self.config.get_agent_name("auditor"),
            force=self.force,
            skip_preflight=True,
            copy_engine=self.install_ai_team,
            run_smoke_check=False,
        )
        res = LabEngineInstaller(opts).run()
        self.actions_log = [("CREATE", item) for item in res.created_items] + [
            ("SKIP", item) for item in res.skipped_items
        ]
        if self.install_ai_team:
            self.ai_team_installed_count = sum(1 for item in res.created_items if "08 Zespół AI" in item)
        return {
            "status": "success" if res.success else "error",
            "version": LAB_VERSION,
            "vault_root": str(self.vault_root),
            "instance_name": self.config.system.instance_name,
            "elapsed_seconds": res.elapsed_seconds,
            "created_count": len(res.created_items),
            "skipped_count": len(res.skipped_items),
            "updated_count": 0,
            "ai_team_installed": self.ai_team_installed_count,
            "log": list(self.actions_log),
        }


def init_environment(
    config_path: Optional[Union[str, Path]] = None,
    vault_root: Optional[Union[str, Path]] = None,
    force: bool = False,
    verbose: bool = True,
    theme_preset: Optional[str] = None,
    install_ai_team: bool = False,
    template_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Programistyczny punkt wejścia do inicjalizacji środowiska LAB 3.0."""
    config = ConfigManager(config_path=config_path, vault_root=vault_root)
    initializer = LabInitializer(
        config=config,
        force=force,
        verbose=verbose,
        install_ai_team=install_ai_team,
        template_dir=template_dir,
    )
    return initializer.run()


def main() -> int:
    """Zunifikowany punkt wejścia CLI/WWW (deleguje do lab_core.setup.main)."""
    return setup_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
