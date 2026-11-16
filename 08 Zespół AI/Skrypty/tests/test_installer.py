"""test_installer.py - Zestaw testów jednostkowych dla silnika instalatora LAB 2.0 (lab_core.installer)
Standard inżynieryjny: 100% Python Standard Library (unittest, tempfile, pathlib, subprocess, sys).
Weryfikuje deterministyczne powołanie czystej instancji, brak danych prywatnych i fail-safe.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.dont_write_bytecode = True

# Bootstrap sys.path
_scripts_dir = Path(__file__).resolve().parent.parent
_root_dir = _scripts_dir.parent.parent.resolve()
for _p in (_scripts_dir, _root_dir):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lab_core.config import ConfigManager
from lab_core.diagnostics import DiagnosticItem, PreflightReport
from lab_core.installer import (
    InstallOptions,
    InstallResult,
    LabEngineInstaller,
    generate_initial_config_toml,
    install_lab_vault,
    install_project_capsule,
)
from lab_core.schema import verify_master_db


class TestLabInstaller(unittest.TestCase):
    """Testy powoływania nowej instancji LAB 2.0 przez LabEngineInstaller."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp_dir.name) / "NewLabVault"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_clean_installation_structure_and_files(self):
        result = install_lab_vault(
            target_dir=self.target,
            instance_name="NEW_INSTANCE",
            user_name="Aleksander",
            call_sign="Olek",
            theme_preset="nord",
            coordinator_name="Adam",
            governance_name="Ada",
            skip_preflight=True,
            copy_engine=False,  # Izolowany test struktury bez kopiowania gigabajtów
        )
        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)

        # 1. Sprawdzenie 8 pokoi rdzenia
        rooms = [
            "01 Skrzynka",
            "02 Warsztat",
            "03 Projekty",
            "04 Operacje",
            "05 Baza Wiedzy",
            "06 Zasoby",
            "07 Bazy Danych",
            "08 Zespół AI",
        ]
        for r in rooms:
            room_p = self.target / r
            self.assertTrue(room_p.is_dir(), f"Brak katalogu pokoju: {r}")

        # 2. Podfoldery specjalne
        self.assertTrue((self.target / "02 Warsztat" / "_archiwum").is_dir())
        self.assertTrue((self.target / "03 Projekty" / "_archiwum").is_dir())
        self.assertTrue((self.target / "04 Operacje" / "_archiwum").is_dir())
        self.assertTrue((self.target / "06 Zasoby" / "Notatki").is_dir())
        self.assertTrue((self.target / "06 Zasoby" / "Dokumenty").is_dir())

        # 3. Kanoniczne pliki zarządcze
        workshop_md = self.target / "02 Warsztat" / "Dashboard LAB.md"
        projects_md = self.target / "03 Projekty" / "_Spis Projektów.md"
        operations_md = self.target / "04 Operacje" / "_Spis Operacji.md"
        agents_md = self.target / "AGENTS.md"

        self.assertTrue(workshop_md.is_file())
        self.assertTrue(projects_md.is_file())
        self.assertTrue(operations_md.is_file())
        self.assertTrue(agents_md.is_file())

        # 4. Weryfikacja czystości od danych prywatnych
        agents_content = agents_md.read_text(encoding="utf-8")
        self.assertIn("NEW_INSTANCE", agents_content)
        self.assertNotIn("Zbigniew", agents_content)
        self.assertNotIn("PR-002 Moja Muzyka", agents_content)

        workshop_content = workshop_md.read_text(encoding="utf-8")
        self.assertIn("NEW_INSTANCE", workshop_content)
        self.assertNotIn("Zbigniew", workshop_content)

        # 5. Konfiguracja .obsidian/
        obs_dir = self.target / ".obsidian"
        self.assertTrue((obs_dir / "appearance.json").is_file())
        self.assertTrue((obs_dir / "app.json").is_file())
        self.assertTrue((obs_dir / "bookmarks.json").is_file())
        self.assertTrue((obs_dir / "snippets" / "lab-design-tokens.css").is_file())

        # Weryfikacja app.json (attachmentFolderPath -> pokój ingestion, userIgnoreFilters -> biała lista root)
        app_cfg = json.loads((obs_dir / "app.json").read_text(encoding="utf-8"))
        self.assertEqual(app_cfg.get("attachmentFolderPath"), "01 Skrzynka")
        ignore_filters = app_cfg.get("userIgnoreFilters", [])
        self.assertTrue(ignore_filters)
        import re
        whitelist_filter = ignore_filters[0]
        self.assertTrue(
            whitelist_filter.startswith("/") and whitelist_filter.endswith("/"),
            f"Obsidian wymaga otoczenia RegExp ukośnikami /.../: {whitelist_filter}",
        )
        whitelist_re = re.compile(whitelist_filter[1:-1])
        self.assertIsNone(whitelist_re.search("AGENTS.md"))
        self.assertIsNone(whitelist_re.search("01 Skrzynka"))
        self.assertIsNone(whitelist_re.search("08 Zespół AI"))
        self.assertIsNone(whitelist_re.search("01 Skrzynka/notatka.md"))
        for hidden_root in ("LAB-Installer.bat", "LAB-Installer.command", "lab.config.toml", "GEMINI.md"):
            self.assertIsNotNone(whitelist_re.search(hidden_root), f"Powinno ukryć: {hidden_root}")

        # Weryfikacja bookmarks.json
        bkm = json.loads((obs_dir / "bookmarks.json").read_text(encoding="utf-8"))
        self.assertIn("items", bkm)
        self.assertGreaterEqual(len(bkm["items"]), 4)

        # 6. Dziewicza baza SQLite Kingi
        db_path = self.target / "07 Bazy Danych" / "lab_master.db"
        self.assertTrue(db_path.is_file())
        db_verify = verify_master_db(db_path)
        self.assertTrue(db_verify["valid"])
        self.assertEqual(db_verify["meta"]["instance_name"], "NEW_INSTANCE")

        # 7. Weryfikacja poprawności nowego lab.config.toml przez ConfigManager
        config_file = self.target / "lab.config.toml"
        self.assertTrue(config_file.is_file())
        cfg = ConfigManager(config_path=config_file, vault_root=self.target)
        self.assertEqual(cfg.system.instance_name, "NEW_INSTANCE")
        self.assertEqual(cfg.user.name, "Aleksander")
        self.assertEqual(cfg.user.call_sign, "Olek")
        self.assertEqual(cfg.presentation.theme_preset, "nord")

    def test_project_capsule_structure(self):
        result = install_project_capsule(
            target_dir=self.target,
            project_name="GUS-F01",
            user_name="Anna",
            theme_preset="tokyonight",
            coordinator_name="Adam",
            governance_name="Ada",
            skip_preflight=True,
            copy_engine=False,
        )
        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)

        # 1. Sprawdzenie obecności 5 pokoi Kapsuły Projektowej
        capsule_rooms = [
            "01 Skrzynka",
            "02 Warsztat",
            "03 Projekty",
            "07 Bazy Danych",
            "08 Zespół AI",
        ]
        for r in capsule_rooms:
            room_p = self.target / r
            self.assertTrue(room_p.is_dir(), f"Brak katalogu pokoju kapsuły: {r}")

        # Pokoje wyłączone w profilu project nie mogą istnieć
        self.assertFalse((self.target / "04 Operacje").exists())
        self.assertFalse((self.target / "05 Baza Wiedzy").exists())
        self.assertFalse((self.target / "06 Zasoby").exists())

        # 2. Pliki zarządcze
        self.assertTrue((self.target / "02 Warsztat" / "Dashboard LAB.md").is_file())
        self.assertTrue((self.target / "02 Warsztat" / "Dashboard Projektu.md").is_file())
        self.assertTrue((self.target / "03 Projekty" / "_Spis Projektów.md").is_file())
        self.assertFalse((self.target / "04 Operacje" / "_Spis Operacji.md").exists())
        self.assertTrue((self.target / "AGENTS.md").is_file())
        self.assertTrue((self.target / "07 Bazy Danych" / "lab_master.db").is_file())

        # 3. Weryfikacja konfiguracji TOML
        cfg = ConfigManager(config_path=self.target / "lab.config.toml", vault_root=self.target)
        self.assertEqual(len(cfg.rooms), 5)
        self.assertEqual(cfg.system.instance_name, "LAB-GUS-F01")

    def test_preflight_failure_blocks_installation(self):
        failing_report = PreflightReport(
            os_info="MockOS",
            python_version="3.9.0",
            items=[
                DiagnosticItem(name="Python Interpreter", category="critical", passed=False),
            ],
        )
        with patch("lab_core.installer.run_preflight_diagnostics", return_value=failing_report):
            options = InstallOptions(
                target_dir=self.target,
                skip_preflight=False,
            )
            installer = LabEngineInstaller(options)
            result = installer.run()

            self.assertFalse(result.success)
            self.assertIsNotNone(result.error_message)
            self.assertIn("Pre-flight check nie powiódł się", result.error_message)
            # Upewnij się, że nie utworzono plików
            self.assertFalse((self.target / "AGENTS.md").exists())

    def test_idempotence_and_force_flag(self):
        # Pierwsza instalacja
        install_lab_vault(target_dir=self.target, instance_name="IDEM_TEST", skip_preflight=True, copy_engine=False)
        test_file = self.target / "02 Warsztat" / "Dashboard LAB.md"
        test_file.write_text("MOJA_ZMIANA", encoding="utf-8")

        # Druga instalacja bez force -> powinna pominąć plik
        res2 = install_lab_vault(target_dir=self.target, instance_name="IDEM_TEST", force=False, skip_preflight=True, copy_engine=False)
        self.assertTrue(res2.success)
        self.assertEqual(test_file.read_text(encoding="utf-8"), "MOJA_ZMIANA")
        self.assertTrue(any("Dashboard LAB.md" in s for s in res2.skipped_items))

        # Trzecia instalacja z force=True -> powinna nadpisać plik
        res3 = install_lab_vault(target_dir=self.target, instance_name="IDEM_TEST", force=True, skip_preflight=True, copy_engine=False)
        self.assertTrue(res3.success)
        self.assertNotEqual(test_file.read_text(encoding="utf-8"), "MOJA_ZMIANA")


class TestSetupLabCli(unittest.TestCase):
    """Test integracyjny wykonywalnego skryptu CLI setup_lab.py."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp_dir.name) / "CliVault"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_setup_lab_cli_non_interactive(self):
        setup_script = _scripts_dir / "lab_core" / "setup.py"
        if not setup_script.is_file():
            setup_script = _root_dir / "lab_core" / "setup.py"
        self.assertTrue(setup_script.is_file())

        cmd = [
            sys.executable,
            str(setup_script),
            "--target-dir", str(self.target),
            "--name", "CLI_LAB",
            "--user", "Krzysztof",
            "--theme", "tokyonight",
            "--non-interactive",
            "--skip-preflight",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, f"Błąd CLI: {proc.stderr}")
        self.assertIn("SUKCES", proc.stdout)
        self.assertTrue((self.target / "lab.config.toml").is_file())
        self.assertTrue((self.target / "07 Bazy Danych" / "lab_master.db").is_file())

    def test_lab_init_non_interactive_v3_and_qg1_icloud_guard(self):
        lab_init_script = _root_dir / "lab-init.py"
        self.assertTrue(lab_init_script.is_file())
        cmd = [
            sys.executable,
            str(lab_init_script),
            "--target-dir", str(self.target),
            "--name", "V3_LAB",
            "--user", "Marek",
            "--github",
            "--icloud",
            "--llm-provider", "claude",
            "--llm-model", "claude-sonnet-4-5",
            "--non-interactive",
            "--skip-preflight",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, f"Błąd lab-init.py: {proc.stderr}")
        self.assertIn("v3.0.0", proc.stdout)
        self.assertIn("QG-1", proc.stdout)
        cfg_txt = (self.target / "lab.config.toml").read_text(encoding="utf-8")
        self.assertIn('version = "3.0.0"', cfg_txt)
        self.assertIn('provider = "claude"', cfg_txt)
        # QG-1 wymusza enabled = false w [vcs] przy icloud=True
        self.assertIn("enabled = false", cfg_txt)

    def test_force_creates_bak_backup_and_web_wizard_form(self):
        from lab_core.setup import _build_options_from_form, _render_wizard_html
        html_out = _render_wizard_html()
        self.assertIn("LAB – Konfiguracja własna", html_out)
        self.assertNotIn("3.0", html_out)

        opts = _build_options_from_form({
            "user_name": ["Ewa"],
            "instance_name": ["EwaVault"],
            "location_preset": ["__custom__"],
            "custom_location": [str(self.target.parent)],
            "github_enabled": ["yes"],
            "icloud_enabled": ["no"],
            "llm_provider": ["antigravity"],
        })
        self.assertEqual(opts.llm_model, "auto")
        res1 = LabEngineInstaller(opts).run()
        self.assertTrue(res1.success)
        cfg_txt = (opts.target_dir / "lab.config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "auto"', cfg_txt)

        opts.force = True
        res2 = LabEngineInstaller(opts).run()
        self.assertTrue(res2.success)
        self.assertTrue((opts.target_dir / "lab.config.toml.bak").is_file())

    def test_web_wizard_sequential_steps_and_summary(self):
        from lab_core.setup import _render_result_html, _render_wizard_html
        from lab_core.installer import InstallResult

        html_out = _render_wizard_html()
        self.assertIn('data-step="1"', html_out)
        self.assertIn('data-step="12"', html_out)
        self.assertNotIn('data-step="13"', html_out)
        self.assertNotIn("f_llm_model", html_out)
        self.assertIn("Środowisko AI – wybór programu", html_out)
        self.assertIn("Koniec. Zakończyliśmy parametryzowanie. Teraz przechodzimy do instalacji.", html_out)
        self.assertIn("confirm_icloud_optimize_off", html_out)
        self.assertIn("<h1>LAB – Konfiguracja własna</h1>", html_out)
        self.assertNotIn("3.0", html_out)
        self.assertIn("function isIcloudEnabled()", html_out)
        self.assertIn("function getVisibleSteps()", html_out)
        self.assertIn("if (i === 5 && skipIcloud) continue;", html_out)
        self.assertIn('"Krok " + idx + " z " + visibleTotal', html_out)

        self.assertNotIn("Antigravity (wymaga .agents/hooks.json)", html_out)
        self.assertIn(">Antigravity</option>", html_out)

        res_html = _render_result_html(
            InstallResult(success=True, target_dir=self.target, instance_name="LAB", elapsed_seconds=0.1)
        )
        self.assertIn("<title>LAB – Wynik instalacji</title>", res_html)
        self.assertNotIn("3.0", res_html)
        self.assertNotIn("obsidian://open", res_html)
        self.assertIn("Open folder as vault", res_html)
        self.assertIn("Kopiuj ścieżkę sejfu", res_html)
        self.assertIn("Weryfikacja po instalacji:", res_html)
        self.assertNotIn("Post-Install Smoke Check", res_html)

    def test_icloud_blocks_venv_node_modules_and_placeholders(self):
        self.target.mkdir(parents=True, exist_ok=True)
        (self.target / ".venv").mkdir()
        (self.target / "node_modules").mkdir()
        res = LabEngineInstaller(
            InstallOptions(
                target_dir=self.target,
                icloud_enabled=True,
                skip_preflight=True,
                copy_engine=False,
            )
        ).run()
        self.assertFalse(res.success)
        self.assertIn(".venv", res.error_message or "")

        # Test placeholderów .icloud
        target2 = Path(self.tmp_dir.name) / "IcloudPlaceholderVault"
        target2.mkdir(parents=True, exist_ok=True)
        (target2 / ".note.md.icloud").write_text("placeholder", encoding="utf-8")
        res2 = LabEngineInstaller(
            InstallOptions(
                target_dir=target2,
                icloud_enabled=True,
                skip_preflight=True,
                copy_engine=False,
            )
        ).run()
        self.assertFalse(res2.success)
        self.assertIn(".icloud", res2.error_message or "")

    def test_antigravity_verifies_hooks_json(self):
        # Gdy copy_engine=True, hooks.json zostaje skopiowany i zwalidowany
        res = LabEngineInstaller(
            InstallOptions(
                target_dir=self.target,
                llm_provider="antigravity",
                skip_preflight=True,
                copy_engine=True,
            )
        ).run()
        self.assertTrue(res.success, msg=res.error_message)
        self.assertTrue((self.target / ".agents" / "hooks.json").is_file())

        # Uszkodzony JSON w hooks.json powinien zablokować wynik
        (self.target / ".agents" / "hooks.json").write_text("{bad json", encoding="utf-8")
        res_bad = LabEngineInstaller(
            InstallOptions(
                target_dir=self.target,
                llm_provider="antigravity",
                skip_preflight=True,
                copy_engine=False,
            )
        ).run()
        self.assertFalse(res_bad.success)
        self.assertIn("hooks.json", res_bad.error_message or "")


class TestConfigPortability(unittest.TestCase):
    """Quality Gate Ady: Testy deterministycznej przenośności konfiguracji (Zero Host-Paths)."""

    def test_vault_shellcommands_portability(self):
        sc_file = _root_dir / ".obsidian" / "plugins" / "obsidian-shellcommands" / "data.json"
        if not sc_file.is_file():
            self.skipTest("Brak wtyczki obsidian-shellcommands w skarbcu.")

        content = sc_file.read_text(encoding="utf-8")
        for bad in ("/Users/", "/home/", "C:\\Users", "C:/Users"):
            self.assertNotIn(bad, content, f"Wykryto prywatną ścieżkę konta '{bad}' w {sc_file}")

        data = json.loads(content)
        self.assertIsInstance(data.get("prompts"), list)
        for sc in data.get("shell_commands", []):
            cmd = sc.get("platform_specific_commands", {}).get("default", "")
            if "08 Zespół AI" in cmd or "set_theme.py" in cmd or "lab-export-pdf.py" in cmd:
                self.assertIn("{{vault_path}}", cmd, f"Komenda {sc.get('id')} nie używa {{vault_path}}")

    def test_capsule_template_portability(self):
        template_dir = _root_dir / "06 Zasoby" / "Szablony" / "LAB-Kapsula-Projektowa"
        if not template_dir.is_dir():
            self.skipTest("Brak folderu szablonu Kapsuły Projektowej.")

        from lab_core.capsule import assert_portability
        # Powinno przejść bez rzucania błędu
        assert_portability(template_dir)

    def test_assert_portability_catches_hardcoded_user_path(self):
        from lab_core.capsule import assert_portability
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test_config.json"
            p.write_text('{"path": "/Users/' + 'jan/Documents/Projekt"}', encoding="utf-8")
            with self.assertRaises(RuntimeError) as cm:
                assert_portability(Path(td))
            self.assertIn("Quality Gate FAILED", str(cm.exception))

    def test_launchers_support_root_and_payload_mode(self):
        cmd_txt = (_root_dir / "LAB-Installer.command").read_text(encoding="utf-8")
        bat_txt = (_root_dir / "LAB-Installer.bat").read_text(encoding="utf-8")
        self.assertIn("$SCRIPT_DIR/lab-init.py", cmd_txt)
        self.assertIn("$SCRIPT_DIR/.lab-payload/lab-init.py", cmd_txt)
        self.assertNotIn("3.0", cmd_txt)
        self.assertIn("%~dp0lab-init.py", bat_txt)
        self.assertIn("%~dp0.lab-payload\\lab-init.py", bat_txt)
        self.assertNotIn("3.0", bat_txt)

    def test_karta_startowa_clean_of_legacy_versions(self):
        docs_dir = _root_dir / "03 Projekty" / "PR-018 LAB Open Source"
        for fname in ("Karta_Startowa_LAB.md", "Karta_Startowa_LAB.html"):
            fpath = docs_dir / fname
            self.assertTrue(fpath.is_file(), f"Brak pliku {fpath}")
            txt = fpath.read_text(encoding="utf-8")
            self.assertNotIn("3.0", txt)
            self.assertNotIn("PR-018", txt)
            self.assertNotIn("Uruchom_Instalator", txt)
            self.assertIn("LAB-Installer.command", txt)
            self.assertIn("LAB-Installer.bat", txt)
            self.assertIn("Obsidian", txt)
            self.assertIn("Gatekeeper", txt)


if __name__ == "__main__":
    unittest.main()
