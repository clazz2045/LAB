"""test_git_sync.py - Testy jednostkowe dla lab_core.git (biała lista, QG-2, orphan, zip)
Standard inżynieryjny: 100% Python Standard Library (unittest, tempfile, pathlib, zipfile).
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True

_scripts_dir = Path(__file__).resolve().parent.parent
_root_dir = _scripts_dir.parent.parent.resolve()
for _p in (_scripts_dir, _root_dir):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lab_core.git import (
    ALLOWED_AI_SUBDIRS,
    ALLOWED_OBSIDIAN_FILES,
    ALLOWED_ROOT_FILES,
    DEFAULT_ROOM_PATHS,
    GitSyncManager,
    is_allowed_export_relpath,
    scan_qg2,
)


class TestGitSyncAndQG2(unittest.TestCase):
    """Testy eksportu repozytorium Open Source w rygorze ścisłej białej listy i QG-2."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "RepoOut"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_qg2_detects_secrets_paths_private_urls_and_allowlist_violation(self) -> None:
        sample_dir = Path(self.tmp.name) / "ScanMe"
        sample_dir.mkdir(parents=True)
        # Dozwolona ścieżka z wyciekami treści
        (sample_dir / "core_config.py").write_text(
            "\n".join([
                'API = "ghp_' + '1234567890abcdefghij1234567890"',
                'HOME = "/Users/' + 'alice/secret"',
                'URL = "https://drive.google.com/' + 'drive/folders/1AbCdEfGhIjK12345678"',
                'SAFE = "/Users/' + 'alice/ok"  # QG2-IGNORE',
            ]),
            encoding="utf-8",
        )
        # Plik spoza białej listy
        (sample_dir / "nieautoryzowany.txt").write_text("cokolwiek", encoding="utf-8")

        findings = scan_qg2(sample_dir)
        rules = {f.rule for f in findings}
        self.assertEqual(
            rules,
            {"API_KEY_GITHUB", "HARDCODED_HOME_MAC", "PRIVATE_URL_GDRIVE", "ALLOWLIST_VIOLATION"},
        )

    def test_sync_files_strict_allowlist_and_zip(self) -> None:
        # Przygotuj śmieciowy plik oraz .git/ w katalogu docelowym
        self.target.mkdir(parents=True)
        (self.target / ".git").mkdir()
        (self.target / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        stale = self.target / "stale_old_file.txt"
        stale.write_text("old", encoding="utf-8")

        mgr = GitSyncManager(vault_root=_root_dir, target_dir=self.target)
        mgr.sync_files(dry_run=False, clean_first=True)

        # 1. Stare pliki usunięte, .git zachowane
        self.assertFalse(stale.exists())
        self.assertTrue((self.target / ".git" / "HEAD").is_file())

        # 2. Ścisła weryfikacja białej listy na całym wyeksportowanym drzewie
        actual_root_entries = {p.name for p in self.target.iterdir() if p.name != ".git"}
        expected_root_entries = ALLOWED_ROOT_FILES | DEFAULT_ROOM_PATHS | {".agents", ".obsidian", "08 Zespół AI", "docs"}
        self.assertEqual(actual_root_entries, expected_root_entries)

        actual_docs_entries = {p.name for p in (self.target / "docs").iterdir()}
        self.assertEqual(
            actual_docs_entries,
            {
                "Podrecznik_Korzystania_z_AI.pdf",
                "AI_Handbook_EN.pdf",
                "Przyklady_Uzycia_LAB.pdf",
                "Karta_Startowa_LAB.md",
                "Karta_Startowa_LAB.html",
            },
        )
        self.assertFalse(is_allowed_export_relpath(Path("docs/prywatny.txt")))

        actual_ai_subdirs = {p.name for p in (self.target / "08 Zespół AI").iterdir() if p.is_dir()}
        self.assertEqual(actual_ai_subdirs, ALLOWED_AI_SUBDIRS)

        actual_obs_entries = {p.name for p in (self.target / ".obsidian").iterdir()}
        self.assertEqual(actual_obs_entries, ALLOWED_OBSIDIAN_FILES | {"snippets"})

        # Każdy wyeksportowany plik spełnia is_allowed_export_relpath
        for p in self.target.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                rel = p.relative_to(self.target)
                self.assertTrue(is_allowed_export_relpath(rel), f"Plik spoza białej listy: {rel}")

        # 3. Weryfikacja treści README.md (brak wersji 3.0/PR-018, jawne 8 pokoi, Obsidian, Gatekeeper), CONTRIBUTING.md i LICENSE (MIT)
        self.assertIn("MIT License", (self.target / "LICENSE").read_text(encoding="utf-8"))
        contributing_txt = (self.target / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("Showcase / As-Is", contributing_txt)
        self.assertIn("Zero-Pip", contributing_txt)
        self.assertNotIn("3.0", contributing_txt)
        self.assertNotIn("PR-018", contributing_txt)
        gitignore_txt = (self.target / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("3.0", gitignore_txt)
        readme_txt = (self.target / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("3.0", readme_txt)
        self.assertNotIn("PR-018", readme_txt)
        self.assertIn("Gatekeeper", readme_txt)
        self.assertIn("com.apple.quarantine", readme_txt)
        self.assertIn("Control+klik", readme_txt)
        self.assertIn("Aktualizacja istniejącego środowiska LAB (1 kliknięcie)", readme_txt)
        self.assertIn("LAB-Update.command", readme_txt)
        self.assertIn("LAB-Update.bat", readme_txt)
        self.assertIn("Obsidian", readme_txt)
        self.assertIn("AGENTS.md", readme_txt)
        self.assertIn("_Księga Governance LAB.md", readme_txt)
        self.assertIn("integrations/raycast/", readme_txt)
        for room_label in (
            "01 Skrzynka/",
            "02 Warsztat/",
            "03 Projekty/",
            "04 Operacje/",
            "05 Baza Wiedzy/",
            "06 Zasoby/",
            "07 Bazy Danych/",
            "08 Zespół AI/",
        ):
            self.assertIn(room_label, readme_txt)

        # 4. Budowa paczki LAB-Installer.zip (na wierzchu wyłącznie launchery, wsad w .lab-payload/)
        zip_path = mgr.build_release_zip()
        self.assertEqual(zip_path.name, "LAB-Installer.zip")
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            top_files = {n for n in names if "/" not in n}
            self.assertEqual(
                top_files,
                {"LAB-Installer.command", "LAB-Installer.bat", "LAB-Update.command", "LAB-Update.bat"},
            )
            for cmd_name in ("LAB-Installer.command", "LAB-Update.command"):
                mode = (zf.getinfo(cmd_name).external_attr >> 16) & 0o777
                self.assertEqual(mode, 0o755, f"{cmd_name} w ZIP musi mieć uprawnienia POSIX 0755")
            payload_names = [n for n in names if n.startswith(".lab-payload/")]
            self.assertEqual(len(names) - len(top_files), len(payload_names))
            self.assertIn(".lab-payload/lab.config.example.toml", payload_names)
            self.assertIn(".lab-payload/LICENSE", payload_names)
            self.assertIn(".lab-payload/lab-init.py", payload_names)
            self.assertTrue(
                all(is_allowed_export_relpath(Path(n.removeprefix(".lab-payload/"))) for n in payload_names)
            )

        # 5. Test blokady init_and_push przez QG-2 przy wstrzyknięciu pliku spoza białej listy
        (self.target / "rogue_file.md").write_text("rogue", encoding="utf-8")
        ok, msg = mgr.init_and_push(dry_run=False)
        self.assertFalse(ok)
        self.assertIn("QG-2", msg)

    def test_unauthorized_source_subdir_excluded_from_export(self) -> None:
        # Mini-skarbiec źródłowy z nieautoryzowanym podkatalogiem w 08 Zespół AI
        src_vault = Path(self.tmp.name) / "MockVault"
        (src_vault / "08 Zespół AI" / "Governance").mkdir(parents=True)
        (src_vault / "08 Zespół AI" / "Governance" / "_Księga Governance LAB.md").write_text("# Gov\n", encoding="utf-8")
        (src_vault / "08 Zespół AI" / "NieautoryzowanyKatalog").mkdir(parents=True)
        (src_vault / "08 Zespół AI" / "NieautoryzowanyKatalog" / "sekret.md").write_text("x", encoding="utf-8")
        shutil.copy2(_root_dir / "lab.config.example.toml", src_vault / "lab.config.toml")

        out_dir = Path(self.tmp.name) / "MockOut"
        mgr = GitSyncManager(vault_root=src_vault, target_dir=out_dir)
        mgr.sync_files(dry_run=False)

        actual_ai_subdirs = {p.name for p in (out_dir / "08 Zespół AI").iterdir() if p.is_dir()}
        self.assertEqual(actual_ai_subdirs, {"Governance", "Dzienniki Sesji"})
        self.assertEqual(mgr.run_qg2_scan(), [])

    def test_qg2_whitelist_includes_codex_config_toml(self) -> None:
        from lab_core.git import is_allowed_export_relpath

        self.assertTrue(is_allowed_export_relpath(Path(".codex/config.toml")))
        self.assertFalse(is_allowed_export_relpath(Path(".codex/auth.json")))

        src_vault = Path(self.tmp.name) / "SrcCodex"
        (src_vault / ".codex").mkdir(parents=True)
        (src_vault / ".codex" / "config.toml").write_text(
            '[[hooks.PreToolUse]]\nmatcher = ".*"\ncommand = "python3 \\"08 Zespół AI/Skrypty/lab_core/pre_tool_guard.py\\""\n',
            encoding="utf-8",
        )
        shutil.copy2(_root_dir / "lab.config.example.toml", src_vault / "lab.config.toml")

        out_dir = Path(self.tmp.name) / "OutCodex"
        mgr = GitSyncManager(vault_root=src_vault, target_dir=out_dir)
        mgr.sync_files(dry_run=False)
        self.assertTrue((out_dir / ".codex" / "config.toml").is_file())
        self.assertEqual(mgr.run_qg2_scan(), [])


if __name__ == "__main__":
    unittest.main()
