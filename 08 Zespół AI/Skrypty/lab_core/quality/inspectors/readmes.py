"""lab_core.quality.inspectors.readmes - Inspektor zakazu obcych plików README.md
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import os
from pathlib import Path

from lab_core.constants import IGNORED_DIRS
from lab_core.quality.context import QualityContext, QualityInspector


class ReadmesInspector(QualityInspector):
    """Weryfikuje brak obcych plików README.md w pokojach (decyzja audytowa COO)."""
    name: str = "readmes"

    def inspect(self, ctx: QualityContext) -> None:
        readmes_found = 0
        for root, dirs, files in os.walk(ctx.vault_root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

            for f in files:
                if f.lower() == "readme.md":
                    readmes_found += 1
                    rel_p = Path(root).relative_to(ctx.vault_root) / f
                    ctx.add_finding(
                        level="error",
                        category="readme",
                        path=str(rel_p),
                        message=(
                            f"Wykryto obcy plik README.md: '{rel_p}'. "
                            "W LAB 2 generyczne pliki README są wycofane na rzecz dedykowanych spisów pokoi."
                        ),
                    )
        ctx.stats["foreign_readmes"] = readmes_found
