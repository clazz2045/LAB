"""lab_core.quality.inspectors.management - Inspektor plików zarządczych (KISS Governance)
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

from pathlib import Path

from lab_core.config import ConfigError
from lab_core.quality.context import QualityContext, QualityInspector

ALLOWED_ENGINE_ENTRYPOINT_SCRIPTS = frozenset({
    "__init__.py",
    "check_quality.py",
    "checkpoint.py",
    "drain_inbox.py",
    "projects.py",
    "rollover_agenda.py",
    "session_bootstrap.py",
    "stop_guard.py",
})


class ManagementFilesInspector(QualityInspector):
    """Weryfikuje istnienie dokładnie 3 plików zarządczych i brak sztucznej symetrii."""
    name: str = "management_files"

    def inspect(self, ctx: QualityContext) -> None:
        present_count = 0

        # A. Weryfikacja 3 wymaganych plików zarządczych dynamicznie z konfiguracji
        expected_files = ctx.config.get_management_files(absolute=False)
        for rel_p in expected_files:
            abs_p = ctx.vault_root / rel_p
            if not abs_p.is_file():
                ctx.add_finding(
                    level="error",
                    category="management_files",
                    path=str(rel_p),
                    message=f"Brak wymaganego pliku zarządczego: '{rel_p}'",
                )
            else:
                present_count += 1

        ctx.stats["management_files_present"] = present_count

        # B. Weryfikacja braku nieautoryzowanych plików spisów w pozostałych pokojach
        for room_id, room in ctx.config.rooms.items():
            room_path = ctx.vault_root / room.path
            if not room_path.is_dir():
                continue

            # Pokoje 03 i 04 mają prawo mieć _Spis
            if room.code in {"03", "04"}:
                continue

            for item in room_path.iterdir():
                if item.is_file() and item.name.startswith("_Spis ") and item.name.endswith(".md"):
                    rel_item = item.relative_to(ctx.vault_root)
                    ctx.add_finding(
                        level="warning",
                        category="management_files",
                        path=str(rel_item),
                        message=(
                            f"Naruszenie zasady KISS (sztuczna symetria): niepotrzebny plik spisu '{rel_item}' "
                            f"w pokoju {room.code} '{room.name}'. Spis jest dozwolony wyłącznie w 03 Projekty i 04 Operacje."
                        ),
                    )

        # C. Weryfikacja obecności .agents/hooks.json (bramki governance Antigravity)
        hooks_path = ctx.vault_root / ".agents" / "hooks.json"
        if not hooks_path.is_file():
            ctx.add_finding(
                level="error",
                category="management_files",
                path=".agents/hooks.json",
                message="Brak wymaganego pliku hooków governance: '.agents/hooks.json'.",
            )

        # D. Higiena katalogu głównego Skrypty/ (allowlist stałych punktów wejścia silnika)
        try:
            aiteam_room = ctx.config.get_room_by_role("governance-and-engine")
        except ConfigError:
            aiteam_room = ctx.config.get_room("08") if "08" in ctx.config.rooms else None

        if aiteam_room is not None:
            scripts_dir = ctx.vault_root / aiteam_room.path / "Skrypty"
            if scripts_dir.is_dir():
                guard_path = scripts_dir / "lab_core" / "pre_tool_guard.py"
                if not guard_path.is_file():
                    ctx.add_finding(
                        level="error",
                        category="management_files",
                        room=aiteam_room.id,
                        path=str(guard_path.relative_to(ctx.vault_root)),
                        message="Brak wymaganego modułu bramki narzędziowej: 'lab_core/pre_tool_guard.py'.",
                    )

                for item in sorted(scripts_dir.iterdir()):
                    if item.is_file() and item.suffix == ".py" and item.name not in ALLOWED_ENGINE_ENTRYPOINT_SCRIPTS:
                        rel_item = item.relative_to(ctx.vault_root)
                        ctx.add_finding(
                            level="error",
                            category="management_files",
                            room=aiteam_room.id,
                            path=str(rel_item),
                            message=(
                                f"Nieautoryzowany skrypt w katalogu głównym silnika '{rel_item}'. "
                                f"Dozwolone są wyłącznie stałe punkty wejścia; jednorazowy skrypt usuń lub przenieś do "
                                f"'{aiteam_room.path}/_archiwum/migracje/'."
                            ),
                        )
