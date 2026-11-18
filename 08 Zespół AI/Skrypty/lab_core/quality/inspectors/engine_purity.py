"""lab_core.quality.inspectors.engine_purity - Inspektor czystości jądra silnika (Default-Deny Allowlist)
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from lab_core.config import ConfigError
from lab_core.constants import CANONICAL_ENGINE_ALLOWLIST, IGNORED_DIRS, IGNORED_FILES
from lab_core.markdown import normalize_nfc
from lab_core.quality.context import QualityContext, QualityInspector

_TEMP_FILE_SUFFIXES = (".pyc", ".pyo", ".tmp", ".bak", ".icloud")
_IGNORED_ENGINE_SUBDIRS = IGNORED_DIRS | {".venv", "venv", "env"}


class EnginePurityInspector(QualityInspector):
    """Weryfikuje czystość katalogu jądra silnika (08 Zespół AI/Skrypty/) względem CANONICAL_ENGINE_ALLOWLIST."""
    name: str = "engine_purity"

    def inspect(self, ctx: QualityContext) -> None:
        violations = 0

        aiteam_room_id: Optional[str] = None
        aiteam_room_path = "08 Zespół AI"
        try:
            aiteam_room = ctx.config.get_room_by_role("governance-and-engine")
            aiteam_room_id = aiteam_room.id
            aiteam_room_path = aiteam_room.path
        except ConfigError:
            if "08" in ctx.config.rooms:
                aiteam_room = ctx.config.get_room("08")
                aiteam_room_id = aiteam_room.id
                aiteam_room_path = aiteam_room.path

        code_layout = getattr(ctx.config, "code_layout", None)
        canonical_engine_rel = getattr(code_layout, "canonical_engine", f"{aiteam_room_path}/Skrypty")
        workshop_tools_rel = getattr(code_layout, "workshop_tools", "02 Warsztat/Skrypty").rstrip("/")

        scripts_dir = ctx.vault_root / canonical_engine_rel
        if not scripts_dir.is_dir():
            fallback_dir = ctx.vault_root / aiteam_room_path / "Skrypty"
            scripts_dir = fallback_dir if fallback_dir.is_dir() else scripts_dir

        if not scripts_dir.is_dir():
            ctx.stats["engine_purity_violations"] = 0
            return

        for root, dirs, files in os.walk(scripts_dir):
            dirs[:] = sorted(
                d for d in dirs
                if d not in _IGNORED_ENGINE_SUBDIRS and not d.startswith(".tmp")
            )
            root_path = Path(root)
            for fname in sorted(files):
                if (
                    fname in IGNORED_FILES
                    or fname.startswith(".tmp")
                    or fname.endswith(_TEMP_FILE_SUFFIXES)
                ):
                    continue

                rel_engine = (root_path.relative_to(scripts_dir) / fname).as_posix()
                norm_rel_engine = normalize_nfc(rel_engine)
                if norm_rel_engine not in CANONICAL_ENGINE_ALLOWLIST:
                    violations += 1
                    rel_vault = (root_path.relative_to(ctx.vault_root) / fname).as_posix()
                    ctx.add_finding(
                        level="error",
                        category="engine_purity",
                        room=aiteam_room_id,
                        path=rel_vault,
                        message=(
                            f"Wykryto niekanoniczny plik w jądrze: {norm_rel_engine}. "
                            f"Przenieś go do '{workshop_tools_rel}/'."
                        ),
                    )

        ctx.stats["engine_purity_violations"] = violations
