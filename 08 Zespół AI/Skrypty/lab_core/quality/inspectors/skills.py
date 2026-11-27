"""lab_core.quality.inspectors.skills - Inspektor spójności 3-poziomowego systemu skilli
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from lab_core.config import ConfigError
from lab_core.constants import IGNORED_DIRS
from lab_core.markdown import normalize_nfc, parse_frontmatter
from lab_core.quality.context import QualityContext, QualityInspector
from lab_core.skills import (
    VALID_PROJECT_TYPES,
    VALID_SKILL_MODES,
    discover_environment_skills,
    discover_skills,
    read_project_skill_config,
)

_ACTIVE_STATUSES: Set[str] = {"active", "w toku", "w_toku", "in_progress", "in-progress", "started"}
_ARCHIVED_OR_INACTIVE_STATUSES: Set[str] = {
    "completed",
    "zakończony",
    "zakonczony",
    "done",
    "closed",
    "cancelled",
    "anulowany",
    "canceled",
    "paused",
    "wstrzymany",
    "on_hold",
    "on-hold",
    "blocked",
    "parked",
    "zaparkowany",
    "queued",
    "oczekujący",
    "oczekujacy",
    "pending",
    "backlog",
}


class SkillsInspector(QualityInspector):
    """Weryfikuje półkę skilli (08 Zespół AI/Skille/) oraz spójność skills_map w aktywnych projektach."""

    name: str = "skills"

    def __init__(self, env_search_dirs: Optional[List[Path]] = None) -> None:
        self._env_search_dirs = env_search_dirs

    def inspect(self, ctx: QualityContext) -> None:
        # 1. Inspekcja półki skilli (08 Zespół AI/Skille/)
        shelf_skills = discover_skills(ctx.vault_root, config=ctx.config)
        ctx.stats["skills_shelf_count"] = len(shelf_skills)
        ctx.stats["skills_shelf_valid"] = sum(1 for s in shelf_skills if s.valid)

        shelf_keys: Set[str] = set()
        for skill in shelf_skills:
            if not skill.has_skill_file:
                ctx.add_finding(
                    level="warning",
                    category="skills",
                    path=skill.path,
                    message=skill.errors[0] if skill.errors else f"Brak pliku SKILL.md w '{skill.slug}'.",
                )
                continue

            for err in skill.errors:
                ctx.add_finding(level="error", category="skills", path=skill.path, message=err)
            for warn in skill.warnings:
                ctx.add_finding(level="warning", category="skills", path=skill.path, message=warn)

            shelf_keys.add(normalize_nfc(skill.slug).strip().lower())
            if skill.name:
                shelf_keys.add(normalize_nfc(skill.name).strip().lower())

        # 2. Spójność skilli w aktywnych projektach (03 Projekty/)
        ctx.stats["custom_skill_projects"] = 0
        ctx.stats["skill_consistency_errors"] = 0

        try:
            projects_room = ctx.config.get_room_by_role("active-projects")
        except ConfigError:
            try:
                projects_room = ctx.config.get_room("03")
            except ConfigError:
                return

        projects_dir = ctx.vault_root / projects_room.path
        if not projects_dir.is_dir():
            return

        skills_cfg = getattr(ctx.config, "_raw_data", {}).get("skills", {})
        if not isinstance(skills_cfg, dict):
            skills_cfg = {}

        missing_shelf_level = str(skills_cfg.get("missing_shelf_level", "warning")).strip().lower()
        if missing_shelf_level not in {"warning", "error"}:
            missing_shelf_level = "warning"
        if ctx.strict_checkpoint and bool(skills_cfg.get("strict_shelf", False)):
            missing_shelf_level = "error"

        missing_anywhere_level = str(skills_cfg.get("missing_anywhere_level", "error")).strip().lower()
        if missing_anywhere_level not in {"warning", "error"}:
            missing_anywhere_level = "error"

        env_skills: Optional[Dict[str, Path]] = None

        for proj_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
            if not proj_dir.is_dir() or proj_dir.name.startswith(".") or proj_dir.name.startswith("_") or proj_dir.name in IGNORED_DIRS:
                continue

            card_path = self._find_project_card(proj_dir)
            if not self._is_project_active(proj_dir, card_path):
                continue

            skill_cfg = read_project_skill_config(proj_dir, card_path=card_path, vault_root=ctx.vault_root)
            rel_source = skill_cfg.source_file or str(proj_dir.relative_to(ctx.vault_root))

            if skill_cfg.raw_project_type and skill_cfg.project_type not in VALID_PROJECT_TYPES:
                ctx.add_finding(
                    level="warning",
                    category="skills",
                    path=rel_source,
                    room=projects_room.id,
                    message=(
                        f"Nieznana wartość 'project_type: {skill_cfg.raw_project_type}' w projekcie '{proj_dir.name}'. "
                        f"Dozwolone: {', '.join(sorted(VALID_PROJECT_TYPES))}."
                    ),
                )

            if skill_cfg.raw_skill_mode and skill_cfg.skill_mode not in VALID_SKILL_MODES:
                ctx.add_finding(
                    level="warning",
                    category="skills",
                    path=rel_source,
                    room=projects_room.id,
                    message=(
                        f"Nieznana wartość 'skill_mode: {skill_cfg.raw_skill_mode}' w projekcie '{proj_dir.name}'. "
                        f"Dozwolone: {', '.join(sorted(VALID_SKILL_MODES))}."
                    ),
                )

            if skill_cfg.skill_mode != "custom":
                continue

            ctx.stats["custom_skill_projects"] += 1

            # Stan przejściowy (puste skills_map: {} lub same puste listy) jest poprawny
            non_empty_roles = {role: skills for role, skills in skill_cfg.skills_map.items() if skills}
            if not non_empty_roles:
                continue

            if env_skills is None:
                env_skills = discover_environment_skills(ctx.vault_root, search_dirs=self._env_search_dirs)

            for role_key, skill_list in non_empty_roles.items():
                for skill_name in skill_list:
                    norm_skill = normalize_nfc(skill_name).strip().lower()
                    if not norm_skill:
                        continue
                    if norm_skill in shelf_keys:
                        continue
                    if norm_skill in env_skills:
                        if missing_shelf_level == "error":
                            ctx.stats["skill_consistency_errors"] += 1
                        ctx.add_finding(
                            level=missing_shelf_level,
                            category="skills",
                            path=rel_source,
                            room=projects_room.id,
                            message=(
                                f"Projekt '{proj_dir.name}' (rola '{role_key}') wskazuje skill '{skill_name}', "
                                "który istnieje w środowisku, ale brak jego fizycznej kopii na półce '08 Zespół AI/Skille/'."
                            ),
                        )
                    else:
                        if missing_anywhere_level == "error":
                            ctx.stats["skill_consistency_errors"] += 1
                        ctx.add_finding(
                            level=missing_anywhere_level,
                            category="skills",
                            path=rel_source,
                            room=projects_room.id,
                            message=(
                                f"Projekt '{proj_dir.name}' (rola '{role_key}') wskazuje nieistniejący skill '{skill_name}' "
                                "(brak zarówno w '08 Zespół AI/Skille/', jak i w skillach środowiska)."
                            ),
                        )

    @staticmethod
    def _find_project_card(proj_dir: Path) -> Optional[Path]:
        for cand in sorted(proj_dir.glob("PR-*.md")):
            return cand
        for cand in sorted(proj_dir.glob("*.md")):
            if cand.name not in {"progress-report.md", "PLAN.md"} and not re.match(r"^\d{4}-", cand.name):
                return cand
        return None

    @staticmethod
    def _is_project_active(proj_dir: Path, card_path: Optional[Path]) -> bool:
        """Ustala czy projekt jest aktywny na podstawie frontmattera karty lub PLAN.md."""
        for candidate in (card_path, proj_dir / "PLAN.md"):
            if candidate is None or not candidate.is_file():
                continue
            fm, _ = parse_frontmatter(candidate)
            raw_status = str(fm.get("status", "")).strip().lower()
            if not raw_status:
                continue
            if any(k in raw_status for k in _ARCHIVED_OR_INACTIVE_STATUSES):
                return False
            if any(k in raw_status for k in _ACTIVE_STATUSES):
                return True
        return True
