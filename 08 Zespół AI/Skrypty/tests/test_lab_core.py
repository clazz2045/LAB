"""test_lab_core.py - Zestaw testów jednostkowych dla pakietu lab_core (Projekt PR-015, Faza 1)
Standard: 100% Python Standard Library (unittest). Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Bootstrap sys.path
_current_dir = Path(__file__).resolve().parent.parent
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

from lab_core.env import ensure_vault_in_sys_path
ensure_vault_in_sys_path(_current_dir)

import lab_core
from lab_core import (
    CANONICAL_MANAGEMENT_FILES,
    CORE_ROOM_CODES,
    DEFAULT_CANONICAL_ROOMS,
    FORBIDDEN_WORKSHOP_SECTIONS,
    IGNORED_DIRS,
    IGNORED_FILES,
    CanonicalPathError,
    ConfigManager,
    dump_frontmatter,
    ensure_vault_in_sys_path,
    extract_wikilinks,
    ensure_consumed_field,
    find_vault_root,
    normalize_nfc,
    parse_frontmatter,
    validate_canonical_path,
)


class TestLabCoreEnv(unittest.TestCase):
    """Testy modułu env: wykrywanie skarbca i ochrona ścieżek."""

    def test_find_vault_root_finds_current_vault(self):
        root = find_vault_root(_current_dir)
        self.assertTrue((root / "lab.config.toml").is_file())
        self.assertTrue((root / "AGENTS.md").is_file())

    def test_ensure_vault_in_sys_path(self):
        root = ensure_vault_in_sys_path(_current_dir)
        self.assertIn(str(root), sys.path)
        self.assertIn(str(root / "08 Zespół AI" / "Skrypty"), sys.path)

    def test_validate_canonical_path_inside_vault(self):
        root = find_vault_root(_current_dir)
        valid_path = root / "01 Skrzynka"
        resolved = validate_canonical_path(valid_path, vault_root=root)
        self.assertEqual(resolved, valid_path.resolve())

    def test_validate_canonical_path_rejects_tmp(self):
        root = find_vault_root(_current_dir)
        with self.assertRaises(CanonicalPathError):
            validate_canonical_path("/tmp/malicious.md", vault_root=root)
        with self.assertRaises(CanonicalPathError):
            validate_canonical_path("/var/tmp/bad.md", vault_root=root)

    def test_validate_canonical_path_rejects_outside_vault(self):
        root = find_vault_root(_current_dir)
        outside_path = root.parent / "non_existent_sibling_folder"
        with self.assertRaises(CanonicalPathError):
            validate_canonical_path(outside_path, vault_root=root)


class TestLabCoreConstants(unittest.TestCase):
    """Testy rejestru stałych: Jedno Źródło Prawdy."""

    def test_ignored_dirs_contains_essentials(self):
        self.assertIn(".obsidian", IGNORED_DIRS)
        self.assertIn(".git", IGNORED_DIRS)
        self.assertIn("_archiwum", IGNORED_DIRS)

    def test_management_files_exactly_three(self):
        self.assertEqual(len(CANONICAL_MANAGEMENT_FILES), 3)
        self.assertIn("02 Warsztat/Dashboard LAB.md", CANONICAL_MANAGEMENT_FILES)
        self.assertIn("03 Projekty/_Spis Projektów.md", CANONICAL_MANAGEMENT_FILES)
        self.assertIn("04 Operacje/_Spis Operacji.md", CANONICAL_MANAGEMENT_FILES)

    def test_core_rooms_count(self):
        self.assertEqual(len(CORE_ROOM_CODES), 8)
        self.assertEqual(len(DEFAULT_CANONICAL_ROOMS), 8)


class TestLabCoreConfig(unittest.TestCase):
    """Testy konfiguracji i zgodności fasady core_config."""

    def test_config_manager_loads_cleanly(self):
        cfg = ConfigManager.load()
        self.assertEqual(cfg.user.name, "Zbigniew")
        self.assertEqual(cfg.user.call_sign, "Zbigniew")
        self.assertEqual(len(cfg.rooms), 8)
        self.assertEqual(cfg.system.instance_name, "LAB")

    def test_facade_core_config_backward_compatibility(self):
        import core_config
        cfg = core_config.ConfigManager.load()
        self.assertEqual(cfg.user.call_sign, "Zbigniew")
        self.assertEqual(cfg.get_agent_name("Adam"), "Adam")
        self.assertEqual(cfg.get_agent_name("Ada"), "Ada")

    def test_role_key_agent_decoupling(self):
        """Weryfikacja pełnego uniezależnienia kodu od imion (oddzielenie role_key od assigned_name)."""
        import core_config
        cfg = core_config.ConfigManager.load()
        expected_roles = [
            "coordinator",
            "auditor",
            "researcher",
            "engineer",
            "designer",
            "knowledge",
            "database_code",
        ]
        self.assertEqual(cfg.get_all_roles(), expected_roles)
        self.assertEqual(cfg.get_all_role_keys(), expected_roles)

        # Weryfikacja pobierania imion po role_key
        self.assertEqual(cfg.get_agent_name("coordinator"), "Adam")
        self.assertEqual(cfg.get_agent_name("auditor"), "Ada")
        self.assertEqual(cfg.get_agent_name("researcher"), "Klara")
        self.assertEqual(cfg.get_agent_name("engineer"), "Maks")
        self.assertEqual(cfg.get_agent_name("designer"), "Maja")
        self.assertEqual(cfg.get_agent_name("knowledge"), "Edyta")
        self.assertEqual(cfg.get_agent_name("database_code"), "Kinga")

        # Weryfikacja pobierania plików kontraktów
        for r in expected_roles:
            rel_path = cfg.get_agent_contract_file(r)
            self.assertFalse(rel_path.is_absolute())
            self.assertTrue(str(rel_path).endswith("AGENT.md"))

            abs_path = cfg.get_agent_contract_file(r, absolute=True)
            self.assertTrue(abs_path.is_absolute())
            self.assertTrue(abs_path.is_file(), f"Plik kontraktu nie istnieje: {abs_path}")

        # Weryfikacja aliasów i niewrażliwości na wielkość liter
        self.assertEqual(cfg.get_agent_name("Koordynator"), "Adam")
        self.assertEqual(cfg.get_agent_name("audytor"), "Ada")
        self.assertEqual(cfg.get_agent_name("bazy danych"), "Kinga")

        # Błędna rola podnosi ConfigValidationError
        with self.assertRaises(core_config.ConfigValidationError):
            cfg.get_agent_name("non_existent_role_xyz")

    def test_external_storage_config_and_resolver(self):
        import core_config
        cfg = core_config.ConfigManager.load()
        self.assertTrue(cfg.external_storage.enabled)
        self.assertEqual(cfg.external_storage.provider, "gdrive")
        self.assertEqual(cfg.external_storage.heavy_file_threshold_mb, 15)
        url = cfg.resolve_external_link("Czasopisma/GN/GN45.pdf")
        self.assertTrue(url.startswith("https://drive.google.com/drive/folders/lab-czasopisma/"))
        self.assertTrue(url.endswith("GN45.pdf"))
        local = cfg.resolve_external_link("Czasopisma/GN/GN45.pdf", mode="local")
        self.assertTrue(local.endswith("GN45.pdf"))


class TestLabCoreMarkdown(unittest.TestCase):
    """Testy parsera i serializera Markdown / YAML Frontmatter."""

    def test_parse_frontmatter_from_string(self):
        sample = """---
type: note
title: "Test Notatka"
status: active
count: 42
tags:
  - test
  - python
---
# Treść Notatki
To jest ciało notatki.
"""
        meta, body = parse_frontmatter(sample)
        self.assertEqual(meta["type"], "note")
        self.assertEqual(meta["title"], "Test Notatka")
        self.assertEqual(meta["status"], "active")
        self.assertEqual(meta["count"], 42)
        self.assertEqual(meta["tags"], ["test", "python"])
        self.assertIn("# Treść Notatki", body)

    def test_parse_frontmatter_without_yaml(self):
        sample = "# Zwykły plik Markdown\nBez nagłówka."
        meta, body = parse_frontmatter(sample)
        self.assertEqual(meta, {})
        self.assertEqual(body, sample)

    def test_dump_frontmatter_roundtrip(self):
        meta = {
            "type": "project",
            "id": "PR-015",
            "status": "active",
            "tags": ["refaktoryzacja", "lab-core"],
        }
        body = "\n# Projekt PR-015\nOpis projektu."
        output = dump_frontmatter(meta, body)
        meta_parsed, body_parsed = parse_frontmatter(output)
        self.assertEqual(meta_parsed["type"], meta["type"])
        self.assertEqual(meta_parsed["id"], meta["id"])
        self.assertEqual(meta_parsed["tags"], meta["tags"])
        self.assertIn("# Projekt PR-015", body_parsed)

    def test_extract_wikilinks(self):
        content = "Zobacz [[Notatka A]], a także [[Folder/Notatka B|Inna Nazwa]] oraz [[Koncepcja#Sekcja]]."
        links = extract_wikilinks(content)
        self.assertEqual(links, ["Notatka A", "Folder/Notatka B", "Koncepcja"])

    def test_ensure_consumed_field_injects_when_missing(self):
        sample = "---\ntype: note\ntitle: Test\n---\nTreść."
        updated = ensure_consumed_field(sample, default=False)
        self.assertIn("consumed: false", updated)
        fm, body = parse_frontmatter(updated)
        self.assertFalse(fm["consumed"])

    def test_ensure_consumed_field_preserves_existing(self):
        sample = "---\ntype: note\ntitle: Test\nconsumed: true\n---\nTreść."
        updated = ensure_consumed_field(sample, default=False)
        self.assertEqual(sample, updated)
        fm, _ = parse_frontmatter(updated)
        self.assertTrue(fm["consumed"])


class TestLabCoreQuality(unittest.TestCase):
    """Testy podsystemu jakości i polimorficznych inspektorów."""

    def test_all_inspectors_instantiate_and_have_names(self):
        from lab_core.quality import (
            RoomsInspector,
            ReadmesInspector,
            ManagementFilesInspector,
            InboxInspector,
            NotesAndLinksInspector,
            ProjectsAndProductsInspector,
            WorkshopAndThoughtsInspector,
            AssetsAndReferencesInspector,
            SubagentsPolicyInspector,
            GovernanceStyleInspector,
            PortabilityInspector,
            EnginePurityInspector,
        )
        inspectors = [
            RoomsInspector(),
            ReadmesInspector(),
            ManagementFilesInspector(),
            InboxInspector(),
            NotesAndLinksInspector(),
            ProjectsAndProductsInspector(),
            WorkshopAndThoughtsInspector(),
            AssetsAndReferencesInspector(),
            SubagentsPolicyInspector(),
            GovernanceStyleInspector(),
            PortabilityInspector(),
            EnginePurityInspector(),
        ]
        self.assertEqual(len(inspectors), 12)
        for insp in inspectors:
            self.assertTrue(hasattr(insp, "name"))
            self.assertTrue(callable(insp.inspect))

    def test_quality_checker_decomposition(self):
        from check_quality import QualityChecker
        root = find_vault_root(_current_dir)
        checker = QualityChecker(vault_root=root)
        self.assertEqual(len(checker.inspectors), 12)
        report = checker.check_all()
        self.assertIn(report.status, {"ok", "attention"})
        self.assertEqual(report.rooms_count, 8)

    def test_governance_style_inspector_detects_skynet_and_meta(self):
        from lab_core.quality import GovernanceStyleInspector, QualityContext
        root = find_vault_root(_current_dir)
        config = ConfigManager(vault_root=root)
        findings = []
        stats = {}
        ctx = QualityContext(root, config, findings, stats)
        inspector = GovernanceStyleInspector()
        inspector.inspect(ctx)
        # W skarbcu nie powinno być błędów meta-refleksji
        meta_errors = [f for f in findings if f.category == "governance_style" and f.level == "error"]
        self.assertEqual(len(meta_errors), 0, f"Znaleziono niedozwolone błędy stylu: {meta_errors}")


class TestLabCoreRouting(unittest.TestCase):
    """Testy polimorficznego modułu routingu drenażu (DrainRouter)."""

    def setUp(self) -> None:
        self.root = find_vault_root(_current_dir)
        self.config = ConfigManager.load(vault_root=self.root)
        self.router = lab_core.DrainRouter(vault_root=self.root, config=self.config)
        self.tmp_dir = self.root / "08 Zespół AI" / "Skrypty" / ".tmp_routing_test"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_router_documents(self):
        f = self.tmp_dir / "analiza.pdf"
        f.write_bytes(b"%PDF mock")
        dest = self.router.resolve_destination(f)
        self.assertEqual(dest, self.root / "06 Zasoby" / "Dokumenty")

    def test_router_images(self):
        f = self.tmp_dir / "wykres.png"
        f.write_bytes(b"PNG mock")
        dest = self.router.resolve_destination(f)
        self.assertEqual(dest, self.root / "06 Zasoby" / "Obrazy")

    def test_router_audio(self):
        f = self.tmp_dir / "wywiad.m4a"
        f.write_bytes(b"M4A mock")
        dest = self.router.resolve_destination(f)
        self.assertEqual(dest, self.root / "06 Zasoby" / "Audio")

    def test_router_raw_thought_capture_precedence(self):
        """Surowe myśli nawet z linkiem nigdy nie trafiają do zasobów."""
        f = self.tmp_dir / "Wispr_2026-11-05_1200_Pomysl.md"
        f.write_text("---\ntype: raw-capture\nsource_url: https://example.com\n---\nLuźna myśl", encoding="utf-8")
        dest = self.router.resolve_destination(f)
        self.assertIn("01 Skrzynka", str(dest))
        self.assertIn("_archiwum", str(dest))
        self.assertNotIn("06 Zasoby", str(dest))

    def test_router_reference_note_sat(self):
        f = self.tmp_dir / "SAT_Research.md"
        f.write_text("---\ntype: note\nnote_type: reference\n---\n# Notatka SAT", encoding="utf-8")
        dest = self.router.resolve_destination(f)
        self.assertEqual(dest, self.root / "06 Zasoby" / "Notatki")

    def test_router_reflection_note(self):
        """Notatka z polem my_thought lub tagiem refleksja trafia do 05 Baza Wiedzy/Refleksje nawet gdy ma source_url."""
        f = self.tmp_dir / "Wspaniale_perspektywy.md"
        f.write_text("---\ntype: capture\nsource_url: https://example.com\nmy_thought: wspaniala mysl\n---\nTekst", encoding="utf-8")
        dest = self.router.resolve_destination(f)
        self.assertEqual(dest, self.root / "05 Baza Wiedzy" / "Refleksje")

    def test_router_fallback(self):
        f = self.tmp_dir / "nieznany_plik.xyz"
        f.write_text("dane", encoding="utf-8")
        dest = self.router.resolve_destination(f)
        self.assertIn("01 Skrzynka", str(dest))
        self.assertIn("_archiwum", str(dest))


class TestLabCoreThemesAndTemplates(unittest.TestCase):
    """Testy katalogu motywów, generatora CSS i szablonów systemowych."""

    def setUp(self) -> None:
        self.root = find_vault_root(_current_dir)
        self.config = ConfigManager.load(vault_root=self.root)

    def test_theme_presets_catalog(self):
        themes = lab_core.list_theme_presets()
        self.assertGreater(len(themes), 10)
        self.assertIn("rosepine", themes)
        self.assertIn("evergreen", themes)
        self.assertIn("tokyonight", themes)

        preset = lab_core.get_theme_preset("rosepine")
        self.assertEqual(preset["name"], "Rosé Pine")
        self.assertIn("canvas", preset)
        self.assertIn("accent", preset)

    def test_generate_design_tokens_css(self):
        css = lab_core.generate_design_tokens_css(self.config)
        self.assertIn(":root {", css)
        self.assertIn("--lab-canvas:", css)
        self.assertIn("--lab-room-01-color:", css)
        self.assertIn(".theme-dark", css)
        self.assertIn('.nav-file:has(> .nav-file-title:not([data-path*="/"]):not([data-path="AGENTS.md"]))', css)
        self.assertNotIn("patch_builder_2.py", css)

    def test_generate_templates(self):
        room = self.config.get_room("01")
        readme = lab_core.generate_room_readme(room, self.config)
        self.assertIn(f"# {room.code} {room.name}", readme)
        self.assertIn(f"room_id: \"{room.id}\"", readme)

        pr_room = self.config.get_room("03")
        pr_index = lab_core.generate_projects_index(pr_room, self.config)
        self.assertIn("Spis Projektów", pr_index)

        op_room = self.config.get_room("04")
        op_index = lab_core.generate_operations_index(op_room, self.config)
        self.assertIn("Spis Operacji", op_index)

        ws_room = self.config.get_room("02")
        ws_entry = lab_core.generate_workshop_entry(ws_room, self.config)
        self.assertIn("Dashboard LAB", ws_entry)

        agents_contract = lab_core.generate_agents_contract(self.config)
        self.assertIn("AGENTS.md", agents_contract)
        self.assertIn("Adam", agents_contract)
        self.assertIn("Ada", agents_contract)
        self.assertIn("Twarde reguły delegacji i wywoływania subagentów", agents_contract)
        self.assertIn("Zasada Destylatu SAT", agents_contract)

        # Weryfikacja fizycznego pliku AGENTS.md w skarbcu
        real_agents_md = (self.config.vault_root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Twarde reguły delegacji i wywoływania subagentów", real_agents_md)
        self.assertIn("Zasada Destylatu SAT", real_agents_md)


if __name__ == "__main__":
    unittest.main()
