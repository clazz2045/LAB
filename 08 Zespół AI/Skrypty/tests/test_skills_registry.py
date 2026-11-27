#!/usr/bin/env python3
"""test_skills_registry.py - Testy jednostkowe 3-poziomowego systemu skilli LAB 3.0
Standard: 100% Python Standard Library (unittest, tempfile, subprocess, json). Zero-Pip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from core_config import ConfigManager, find_vault_root
from lab_core.quality.context import QualityContext
from lab_core.quality.inspectors.skills import SkillsInspector
from lab_core.skills import (
    discover_environment_skills,
    discover_skills,
    get_skills_dir,
    parse_skill_file,
    parse_skills_map_from_frontmatter_lines,
    read_project_skill_config,
)
from projects import ProjectManager
from session_bootstrap import SessionBootstrap


def _build_minimal_vault(vault_root: Path) -> ConfigManager:
    for room in (
        "01 Skrzynka",
        "02 Warsztat",
        "03 Projekty/_archiwum",
        "04 Operacje",
        "05 Baza Wiedzy",
        "06 Zasoby",
        "07 Bazy Danych",
        "08 Zespół AI/Skille",
    ):
        (vault_root / room).mkdir(parents=True, exist_ok=True)

    real_root = find_vault_root(_script_dir)
    shutil.copy(real_root / "lab.config.toml", vault_root / "lab.config.toml")
    shutil.copy(real_root / "AGENTS.md", vault_root / "AGENTS.md")

    real_hooks = real_root / ".agents" / "hooks.json"
    if real_hooks.is_file():
        (vault_root / ".agents").mkdir(parents=True, exist_ok=True)
        shutil.copy(real_hooks, vault_root / ".agents" / "hooks.json")

    real_roles = real_root / "08 Zespół AI" / "Role"
    if real_roles.is_dir():
        shutil.copytree(real_roles, vault_root / "08 Zespół AI" / "Role")

    (vault_root / "02 Warsztat" / "Dashboard LAB.md").write_text("---\ntype: cockpit-dashboard\n---\n# Dashboard\n", encoding="utf-8")
    (vault_root / "03 Projekty" / "_Spis Projektów.md").write_text("# Spis Projektów\n", encoding="utf-8")
    (vault_root / "04 Operacje" / "_Spis Operacji.md").write_text("# Spis Operacji\n", encoding="utf-8")
    return ConfigManager(vault_root=vault_root)


class TestSkillsParserAndRegistry(unittest.TestCase):
    """Testy parsera SKILL.md, półki 08 Zespół AI/Skille/ oraz mapy skills_map."""

    def test_discover_skills_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _build_minimal_vault(root)
            skills_dir = get_skills_dir(root, cfg)
            self.assertEqual(skills_dir, root.resolve() / "08 Zespół AI" / "Skille")

            (skills_dir / ".gitkeep").write_text("", encoding="utf-8")

            # 1. Poprawny skill
            ok_dir = skills_dir / "ui-ux-pro-max"
            ok_dir.mkdir()
            (ok_dir / "SKILL.md").write_text(
                "---\n"
                "name: ui-ux-pro-max\n"
                'description: "System projektowy i palety UI"\n'
                "role: designer\n"
                "source: community/awesome-skills\n"
                "---\n# UI/UX Pro Max\n",
                encoding="utf-8",
            )

            # 2. Skill bez wymaganych pól (error) i bez role/source (warning)
            bad_dir = skills_dir / "broken-skill"
            bad_dir.mkdir()
            (bad_dir / "SKILL.md").write_text(
                "---\nname: broken-skill\n---\n# Brak opisu\n",
                encoding="utf-8",
            )

            # 3. Katalog bez SKILL.md
            empty_dir = skills_dir / "missing-entry"
            empty_dir.mkdir()

            discovered = discover_skills(root, cfg)
            by_slug = {s.slug: s for s in discovered}
            self.assertEqual(set(by_slug.keys()), {"ui-ux-pro-max", "broken-skill", "missing-entry"})

            self.assertTrue(by_slug["ui-ux-pro-max"].valid)
            self.assertEqual(by_slug["ui-ux-pro-max"].role, "designer")
            self.assertEqual(by_slug["ui-ux-pro-max"].source, "community/awesome-skills")
            self.assertEqual(by_slug["ui-ux-pro-max"].errors, [])

            self.assertFalse(by_slug["broken-skill"].valid)
            self.assertTrue(any("description" in e for e in by_slug["broken-skill"].errors))
            self.assertTrue(any("role" in w for w in by_slug["broken-skill"].warnings))

            self.assertFalse(by_slug["missing-entry"].has_skill_file)
            self.assertFalse(by_slug["missing-entry"].valid)

    def test_parse_skill_file_unclosed_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "unclosed" / "SKILL.md"
            p.parent.mkdir()
            p.write_text("---\nname: test\n", encoding="utf-8")
            meta = parse_skill_file(p)
            self.assertFalse(meta.valid)
            self.assertTrue(any("frontmatter" in e.lower() for e in meta.errors))

    def test_parse_skills_map_variants(self) -> None:
        self.assertEqual(parse_skills_map_from_frontmatter_lines(["skills_map: {}"]), {})
        self.assertEqual(
            parse_skills_map_from_frontmatter_lines(["skills_map: {designer: [ui-ux, color-lab], engineer: []}"]),
            {"designer": ["ui-ux", "color-lab"], "engineer": []},
        )
        multiline = [
            "project_type: commercial",
            "skills_map:",
            "  designer:",
            "    - ui-ux-pro-max",
            '    - "typography-craft"',
            "  engineer: []",
            "  researcher: deep-source",
            "updated: 2026-11-27",
        ]
        self.assertEqual(
            parse_skills_map_from_frontmatter_lines(multiline),
            {
                "designer": ["ui-ux-pro-max", "typography-craft"],
                "engineer": [],
                "researcher": ["deep-source"],
            },
        )

    def test_read_project_skill_config_prefers_plan_over_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "PR-099 Test"
            proj.mkdir()
            card = proj / "PR-099 Test.md"
            card.write_text("---\nproject_type: internal\nskill_mode: standard\n---\n", encoding="utf-8")

            cfg_card_only = read_project_skill_config(proj, card_path=card)
            self.assertEqual(cfg_card_only.project_type, "internal")
            self.assertEqual(cfg_card_only.skill_mode, "standard")

            (proj / "PLAN.md").write_text(
                "---\nproject_type: commercial\nskill_mode: custom\nskills_map:\n  designer: [ui-ux]\n---\n",
                encoding="utf-8",
            )
            cfg_with_plan = read_project_skill_config(proj, card_path=card)
            self.assertEqual(cfg_with_plan.project_type, "commercial")
            self.assertEqual(cfg_with_plan.skill_mode, "custom")
            self.assertEqual(cfg_with_plan.skills_map, {"designer": ["ui-ux"]})


class TestProjectsAndBootstrapSkills(unittest.TestCase):
    """Testy integracji skilli w projects.py i session_bootstrap.py."""

    def test_create_project_defaults_and_commercial_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_minimal_vault(root)
            mgr = ProjectManager(root)

            # Domyślnie: internal + standard
            p1 = mgr.create_project("Projekt Wewnętrzny")
            plan1 = p1.folder_path / "PLAN.md"
            self.assertTrue(plan1.is_file())
            cfg1 = read_project_skill_config(p1.folder_path, vault_root=root)
            self.assertEqual(cfg1.project_type, "internal")
            self.assertEqual(cfg1.skill_mode, "standard")
            self.assertEqual(cfg1.skills_map, {})

            # Commercial + standard -> ostrzeżenie
            with self.assertWarns(UserWarning):
                p2 = mgr.create_project("Projekt Komercyjny Std", project_type="commercial", skill_mode="standard")
            cfg2 = read_project_skill_config(p2.folder_path, vault_root=root)
            self.assertEqual(cfg2.project_type, "commercial")
            self.assertEqual(cfg2.skill_mode, "standard")

            # CLI --project-type commercial --skill-mode custom
            proc = subprocess.run(
                [
                    sys.executable,
                    str(_script_dir / "projects.py"),
                    "--vault-root",
                    str(root),
                    "--create",
                    "Projekt Custom CLI",
                    "--project-type",
                    "commercial",
                    "--skill-mode",
                    "custom",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            cfg3 = read_project_skill_config(root / "03 Projekty" / "PR-003 Projekt Custom CLI", vault_root=root)
            self.assertEqual(cfg3.project_type, "commercial")
            self.assertEqual(cfg3.skill_mode, "custom")

            # CLI ostrzeżenie dla commercial + standard
            proc_warn = subprocess.run(
                [
                    sys.executable,
                    str(_script_dir / "projects.py"),
                    "--vault-root",
                    str(root),
                    "--create",
                    "Projekt Commercial Warn",
                    "--project-type",
                    "commercial",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_warn.returncode, 0)
            self.assertIn("OSTRZEŻENIE", proc_warn.stdout)

    def test_session_bootstrap_exposes_available_skills_and_project_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_minimal_vault(root)

            skill_dir = root / "08 Zespół AI" / "Skille" / "palette-pro"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: palette-pro\ndescription: Palety kolorów\nrole: designer\nsource: local\n---\n",
                encoding="utf-8",
            )

            mgr = ProjectManager(root)
            p = mgr.create_project("Aplikacja Testowa", project_type="commercial", skill_mode="custom")
            plan_file = p.folder_path / "PLAN.md"
            plan_file.write_text(
                plan_file.read_text(encoding="utf-8").replace(
                    "skills_map: {}",
                    "skills_map:\n  designer:\n    - palette-pro",
                ),
                encoding="utf-8",
            )

            res = SessionBootstrap(vault_root=root).run()
            self.assertIn("available_skills", res)
            self.assertEqual(len(res["available_skills"]), 1)
            self.assertEqual(res["available_skills"][0]["slug"], "palette-pro")

            details = res["projects_summary"]["active_details"]
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0]["project_type"], "commercial")
            self.assertEqual(details[0]["skill_mode"], "custom")
            self.assertEqual(details[0]["skills_map"], {"designer": ["palette-pro"]})


class TestSkillsInspector(unittest.TestCase):
    """Testy inspektora spójności skilli (SkillsInspector)."""

    def test_custom_empty_skills_map_and_archived_projects_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _build_minimal_vault(root)

            # Aktywny projekt z pustym skills_map: {} (stan przejściowy) -> 0 błędów/ostrzeżeń
            p_act = root / "03 Projekty" / "PR-001 Przejściowy"
            p_act.mkdir(parents=True)
            (p_act / "PR-001 Przejściowy.md").write_text(
                "---\nid: PR-001\nstatus: active\nproject_type: commercial\nskill_mode: custom\nskills_map: {}\n---\n",
                encoding="utf-8",
            )

            # Archiwalny projekt z nieistniejącym skillem -> ignorowany
            p_arch = root / "03 Projekty" / "_archiwum" / "PR-002 Stary"
            p_arch.mkdir(parents=True)
            (p_arch / "PR-002 Stary.md").write_text(
                "---\nid: PR-002\nstatus: completed\nskill_mode: custom\nskills_map:\n  designer: [ghost-skill]\n---\n",
                encoding="utf-8",
            )

            # Wstrzymany projekt z nieistniejącym skillem -> ignorowany
            p_paused = root / "03 Projekty" / "PR-003 Wstrzymany"
            p_paused.mkdir(parents=True)
            (p_paused / "PR-003 Wstrzymany.md").write_text(
                "---\nid: PR-003\nstatus: paused\nskill_mode: custom\nskills_map:\n  designer: [ghost-skill]\n---\n",
                encoding="utf-8",
            )

            ctx = QualityContext(vault_root=root, config=cfg)
            SkillsInspector(env_search_dirs=[]).inspect(ctx)
            self.assertEqual(ctx.findings, [])
            self.assertEqual(ctx.stats["custom_skill_projects"], 1)

    def test_shelf_env_and_missing_skill_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _build_minimal_vault(root)

            # Skill na półce LAB
            shelf_skill = root / "08 Zespół AI" / "Skille" / "shelf-skill"
            shelf_skill.mkdir(parents=True)
            (shelf_skill / "SKILL.md").write_text(
                "---\nname: shelf-skill\ndescription: Lokalny skill\nrole: designer\nsource: lab\n---\n",
                encoding="utf-8",
            )

            # Skill wyłącznie w środowisku testowym
            env_root = root / ".fake_env_skills"
            env_skill = env_root / "env-only-skill"
            env_skill.mkdir(parents=True)
            (env_skill / "SKILL.md").write_text(
                "---\nname: env-only-skill\ndescription: Skill środowiskowy\n---\n",
                encoding="utf-8",
            )

            # Aktywny projekt odwołujący się do: shelf-skill (OK), env-only-skill (warn), missing-skill (error), engineer: [] (skip)
            p_act = root / "03 Projekty" / "PR-010 Test Custom"
            p_act.mkdir(parents=True)
            (p_act / "PR-010 Test Custom.md").write_text("---\nid: PR-010\nstatus: active\n---\n", encoding="utf-8")
            (p_act / "PLAN.md").write_text(
                "---\n"
                "type: project-plan\n"
                "id: PR-010\n"
                "status: active\n"
                "project_type: commercial\n"
                "skill_mode: custom\n"
                "skills_map:\n"
                "  designer:\n"
                "    - shelf-skill\n"
                "    - env-only-skill\n"
                "    - missing-skill\n"
                "  engineer: []\n"
                "---\n",
                encoding="utf-8",
            )

            ctx = QualityContext(vault_root=root, config=cfg)
            SkillsInspector(env_search_dirs=[env_root]).inspect(ctx)

            warnings = [f for f in ctx.findings if f.level == "warning"]
            errors = [f for f in ctx.findings if f.level == "error"]
            self.assertEqual(len(warnings), 1)
            self.assertIn("env-only-skill", warnings[0].message)
            self.assertEqual(len(errors), 1)
            self.assertIn("missing-skill", errors[0].message)

            # Konfiguracja [skills].missing_shelf_level = "error" podnosi brak na półce do error
            toml_path = root / "lab.config.toml"
            toml_path.write_text(
                toml_path.read_text(encoding="utf-8") + '\n[skills]\nmissing_shelf_level = "error"\n',
                encoding="utf-8",
            )
            cfg_strict = ConfigManager(vault_root=root)
            ctx_strict = QualityContext(vault_root=root, config=cfg_strict)
            SkillsInspector(env_search_dirs=[env_root]).inspect(ctx_strict)
            errors_strict = [f for f in ctx_strict.findings if f.level == "error"]
            self.assertEqual(len(errors_strict), 2)

            # Weryfikacja discover_environment_skills
            env_map = discover_environment_skills(root, search_dirs=[env_root])
            self.assertIn("env-only-skill", env_map)


if __name__ == "__main__":
    unittest.main()
