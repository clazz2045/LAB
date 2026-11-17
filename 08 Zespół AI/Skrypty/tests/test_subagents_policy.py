"""Testy polityki subagentów: SubagentsPolicyInspector i walidator destylatu SAT."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab_core import ConfigManager, find_vault_root
from lab_core.distillate import estimate_tokens, validate_sat_distillate
from lab_core.quality import QualityContext, SubagentsPolicyInspector

GOOD = "Fakty: zbadano 3 źródła.\nWnioski: synteza wskazuje A.\nRekomendacja: plik w Bazie Wiedzy."


class TestSATDistillate(unittest.TestCase):
    def test_accepts_valid(self):
        ok, problems = validate_sat_distillate(GOOD)
        self.assertTrue(ok, problems)

    def test_rejects_too_long(self):
        ok, problems = validate_sat_distillate(GOOD + "\n" + "x" * 3000)
        self.assertFalse(ok)
        self.assertTrue(any("limit" in p for p in problems))

    def test_rejects_missing_sections(self):
        ok, _ = validate_sat_distillate("samo gadanie")
        self.assertFalse(ok)

    def test_rejects_personification(self):
        bad = "Fakty: zbadano dane.\nWnioski: jako sztuczna inteligencja czuję postęp.\nRekomendacje: plik w Bazie Wiedzy."
        ok, problems = validate_sat_distillate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("personifikacj" in p for p in problems))


class TestSATDistillateEdgeCases(unittest.TestCase):
    def test_rejects_empty(self):
        self.assertFalse(validate_sat_distillate("   ")[0])

    def test_rejects_raw_dump_many_lines(self):
        ok, problems = validate_sat_distillate(GOOD + "\n" + "\n".join(["log"] * 50))
        self.assertFalse(ok)
        self.assertTrue(any("zbyt wiele linii" in p for p in problems))

    def test_rejects_dense_single_line_dump(self):
        ok, problems = validate_sat_distillate("Fakty: " + "a" * 900 + "\nWnioski: b\nRekomendacje: c")
        self.assertFalse(ok)
        self.assertTrue(any("linia dłuższa" in p for p in problems))

    def test_diacritics_headers_accepted(self):
        ok, problems = validate_sat_distillate("## Fakty\nx\n## Wnioski\ny\n## Rekomendacje\nz")
        self.assertTrue(ok, problems)

    def test_keywords_inside_prose_do_not_count(self):
        ok, _ = validate_sat_distillate("Zrobiłem plik i wynik jest analizą bez żadnych nagłówków.")
        self.assertFalse(ok)


class TestSubagentsInspector(unittest.TestCase):
    def _ctx(self):
        root = find_vault_root()
        return QualityContext(vault_root=root, config=ConfigManager(vault_root=root)), root

    def test_real_vault_clean(self):
        ctx, _ = self._ctx()
        SubagentsPolicyInspector().inspect(ctx)
        errors = [
            f for f in ctx.findings
            if f.level == "error" and "Brak delegacji do subagenta" not in f.message
        ]
        self.assertEqual(errors, [], errors)
        self.assertEqual(ctx.stats["verified_subagent_roles"], ctx.stats["expected_subagent_roles"])
        self.assertEqual(ctx.stats["expected_subagent_roles"], 7)

    def _temp_vault(self):
        """Kopia konfiguracji i ról w katalogu tymczasowym (prawdziwy skarbiec pozostaje nietknięty)."""
        ctx, root = self._ctx()
        tmp = Path(tempfile.mkdtemp())
        shutil.copy2(root / "lab.config.toml", tmp / "lab.config.toml")
        shutil.copy2(root / "AGENTS.md", tmp / "AGENTS.md")
        ai_rel = ctx.config.get_room_by_role("governance-and-engine").path
        shutil.copytree(root / ai_rel / "Role", tmp / ai_rel / "Role")
        return tmp, ai_rel

    def _run_on(self, tmp):
        cfg = ConfigManager(vault_root=tmp)
        ctx = QualityContext(vault_root=tmp, config=cfg)
        SubagentsPolicyInspector().inspect(ctx)
        return ctx

    def test_missing_role_detected(self):
        tmp, ai_rel = self._temp_vault()
        try:
            shutil.rmtree(tmp / ai_rel / "Role" / "Audytor - Ada")
            ctx = self._run_on(tmp)
            self.assertTrue(any("'Ada'" in f.message for f in ctx.findings if f.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_broken_agent_contract_detected(self):
        tmp, ai_rel = self._temp_vault()
        try:
            (tmp / ai_rel / "Role" / "Research - Klara" / "AGENT.md").write_text("brak frontmattera", encoding="utf-8")
            ctx = self._run_on(tmp)
            self.assertTrue(any("type: agent" in f.message for f in ctx.findings if f.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_missing_routing_description_detected(self):
        tmp, ai_rel = self._temp_vault()
        try:
            f = tmp / ai_rel / "Role" / "Research - Klara" / "AGENT.md"
            f.write_text("---\ntype: agent\nname: Klara\nrole: R\n---\n# x", encoding="utf-8")
            ctx = self._run_on(tmp)
            self.assertTrue(any("routing_description" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_empty_agent_file_detected(self):
        tmp, ai_rel = self._temp_vault()
        try:
            (tmp / ai_rel / "Role" / "Research - Klara" / "AGENT.md").write_text("", encoding="utf-8")
            ctx = self._run_on(tmp)
            self.assertTrue(any("type: agent" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_missing_roles_dir_detected(self):
        tmp, ai_rel = self._temp_vault()
        try:
            shutil.rmtree(tmp / ai_rel / "Role")
            ctx = self._run_on(tmp)
            self.assertTrue(any("katalogu ról" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_role_dir_name_nfd_matches(self):
        import unicodedata
        tmp, ai_rel = self._temp_vault()
        try:
            roles = tmp / ai_rel / "Role"
            nfc_dir = next(d for d in roles.iterdir() if "Maks" in d.name)
            nfd_name = unicodedata.normalize("NFD", nfc_dir.name)
            if nfd_name != nfc_dir.name:
                # na APFS rename NFC->NFD bywa normalizowany; test sprawdza jedynie brak fałszywego błędu
                nfc_dir.rename(roles / nfd_name)
            ctx = self._run_on(tmp)
            self.assertEqual([x for x in ctx.findings if x.level == "error"], [])
        finally:
            shutil.rmtree(tmp)

    def test_role_as_file_not_accepted(self):
        tmp, ai_rel = self._temp_vault()
        try:
            roles = tmp / ai_rel / "Role"
            shutil.rmtree(roles / "Design - Maja")
            (roles / "Design - Maja").write_text("to plik", encoding="utf-8")
            ctx = self._run_on(tmp)
            self.assertTrue(any("'Maja'" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_missing_agents_md_detected(self):
        tmp, _ = self._temp_vault()
        try:
            (tmp / "AGENTS.md").unlink()
            ctx = self._run_on(tmp)
            self.assertTrue(any("Brak głównego kontraktu" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_hollowed_rule8_detected(self):
        tmp, _ = self._temp_vault()
        try:
            (tmp / "AGENTS.md").write_text(
                "8. **Twarde reguły delegacji i wywoływania subagentów** (puste)", encoding="utf-8"
            )
            ctx = self._run_on(tmp)
            self.assertTrue(any("pełnej Reguły 8" in x.message for x in ctx.findings if x.level == "error"))
        finally:
            shutil.rmtree(tmp)

    def test_checkquality_reports_subagents_category(self):
        from check_quality import QualityChecker
        tmp, ai_rel = self._temp_vault()
        try:
            shutil.rmtree(tmp / ai_rel / "Role" / "Audytor - Ada")
            report = QualityChecker(vault_root=tmp).check_all()
            self.assertTrue(any(f.category == "subagents" and f.level == "error" for f in report.findings))
        finally:
            shutil.rmtree(tmp)

    def test_missing_rule8_detected(self):
        tmp, _ = self._temp_vault()
        try:
            (tmp / "AGENTS.md").write_text("# pusty kontrakt", encoding="utf-8")
            ctx = self._run_on(tmp)
            self.assertTrue(any("Reguły 8" in f.message for f in ctx.findings if f.level == "error"))
        finally:
            shutil.rmtree(tmp)


class TestInstallerRoles(unittest.TestCase):
    def _install_and_inspect(self, **kw):
        from lab_core.installer import install_lab_vault
        tmp = Path(tempfile.mkdtemp())
        try:
            vault = tmp / "V"
            install_lab_vault(vault, skip_preflight=True, **kw)
            ctx = QualityContext(vault_root=vault, config=ConfigManager(vault_root=vault))
            SubagentsPolicyInspector().inspect(ctx)
            return [f.message for f in ctx.findings if f.level == "error"]
        finally:
            shutil.rmtree(tmp)

    def test_default_install_passes(self):
        self.assertEqual(self._install_and_inspect(), [])

    def test_custom_names_install_passes(self):
        self.assertEqual(self._install_and_inspect(coordinator_name="Zed", governance_name="Nova"), [])


class TestDispatchRegistry(unittest.TestCase):
    def test_log_accepts_none_fields(self):
        from lab_core.schema import init_master_db, log_subagent_dispatch
        tmp = Path(tempfile.mkdtemp())
        try:
            db = tmp / "lab_master.db"
            init_master_db(db)
            self.assertGreater(log_subagent_dispatch(db, "Ada", "audit", "rejected"), 0)
        finally:
            shutil.rmtree(tmp)

    def test_log_and_read_back(self):
        from lab_core.schema import init_master_db, log_subagent_dispatch, get_master_db_connection, verify_master_db
        tmp = Path(tempfile.mkdtemp())
        try:
            db = tmp / "lab_master.db"
            init_master_db(db)
            self.assertTrue(verify_master_db(db)["valid"])
            row_id = log_subagent_dispatch(db, "Klara", "research", "done", 1200, "x" * 1000)
            conn = get_master_db_connection(db)
            try:
                row = conn.execute("SELECT * FROM subagent_dispatches WHERE id=?", (row_id,)).fetchone()
            finally:
                conn.close()
            self.assertEqual(row["agent_name"], "Klara")
            self.assertEqual(row["tokens_used"], 1200)
            self.assertEqual(len(row["summary_excerpt"]), 300)
        finally:
            shutil.rmtree(tmp)


class TestSubagentGateCLI(unittest.TestCase):
    def _gate_vault(self):
        ctx = QualityContext(vault_root=find_vault_root(), config=ConfigManager(vault_root=find_vault_root()))
        tmp = Path(tempfile.mkdtemp())
        shutil.copy2(ctx.vault_root / "lab.config.toml", tmp / "lab.config.toml")
        db_rel = ctx.config.get_room_by_role("sqlite-relational").path
        (tmp / db_rel).mkdir(parents=True)
        return tmp, db_rel

    def test_gate_accepts_and_logs(self):
        from lab_core.subagents import run_gate
        from lab_core.schema import get_master_db_connection
        tmp, db_rel = self._gate_vault()
        try:
            self.assertEqual(run_gate(GOOD, "Klara", "research", 1000, vault_root=tmp, enforce_binding=False), 0)
            conn = get_master_db_connection(tmp / db_rel / "lab_master.db")
            try:
                row = conn.execute("SELECT status, agent_name FROM subagent_dispatches").fetchone()
            finally:
                conn.close()
            self.assertEqual((row["status"], row["agent_name"]), ("accepted", "Klara"))
        finally:
            shutil.rmtree(tmp)

    def test_gate_rejects_and_logs(self):
        from lab_core.subagents import run_gate
        from lab_core.schema import get_master_db_connection
        tmp, db_rel = self._gate_vault()
        try:
            self.assertEqual(run_gate("x" * 5000, "Kinga", "code", vault_root=tmp, enforce_binding=False), 1)
            conn = get_master_db_connection(tmp / db_rel / "lab_master.db")
            try:
                row = conn.execute("SELECT status FROM subagent_dispatches").fetchone()
            finally:
                conn.close()
            self.assertEqual(row["status"], "rejected")
        finally:
            shutil.rmtree(tmp)


class TestSessionDelegationDiscipline(unittest.TestCase):
    """Testy twardej egzekucji Reguły 8 (dyscyplina delegacji sesyjnej Domena A i Domena B)."""

    def _setup_session_vault(self):
        import os
        import time
        from lab_core.installer import install_lab_vault
        from lab_core.schema import init_master_db

        tmp = Path(tempfile.mkdtemp())
        vault = tmp / "Vault"
        install_lab_vault(vault, skip_preflight=True)

        cfg = ConfigManager(vault_root=vault)
        db_path = vault / cfg.get_room_by_role("sqlite-relational").path / "lab_master.db"
        init_master_db(db_path, instance_name=cfg.system.instance_name)

        logs_dir = vault / cfg.get_room_by_role("governance-and-engine").path / "Dzienniki Sesji" / "2026" / "11"
        logs_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_log = logs_dir / "2026-11-11-10-00_adam_checkpoint.md"
        checkpoint_log.write_text("---\ntype: session-log\n---\n# Log\n", encoding="utf-8")

        past_ts = time.time() - 120.0
        for p in vault.rglob("*"):
            if p.is_file():
                os.utime(p, (past_ts, past_ts))

        return tmp, vault, past_ts

    def test_modified_py_without_dispatch_causes_broken_status_and_clears_after_dispatch(self):
        import os
        from check_quality import QualityChecker, render_terminal_report
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            cfg = ConfigManager(vault_root=vault)
            scripts_dir = vault / cfg.get_room_by_role("governance-and-engine").path / "Skrypty"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            py_file = scripts_dir / "nowy_modul.py"
            py_file.write_text("print('ok')\n", encoding="utf-8")
            os.utime(py_file, (past_ts + 30.0, past_ts + 30.0))

            report_before = QualityChecker(vault_root=vault).check_all()
            self.assertEqual(report_before.status, "broken")
            rendered_before = render_terminal_report(report_before)
            self.assertIn("STATUS: 🔴 BROKEN", rendered_before)
            sub_errors = [
                f for f in report_before.findings
                if f.category == "subagents" and f.level == "error" and "zmodyfikowano kod/interfejs" in f.message
            ]
            self.assertEqual(len(sub_errors), 1, report_before.findings)
            self.assertIn("nowy_modul.py", sub_errors[0].message)

            self.assertEqual(run_gate(GOOD, "Kinga", "code", vault_root=vault, enforce_binding=False), 0)

            report_after = QualityChecker(vault_root=vault).check_all()
            sub_errors_after = [
                f for f in report_after.findings
                if f.category == "subagents" and f.level == "error"
            ]
            self.assertEqual(sub_errors_after, [])
        finally:
            shutil.rmtree(tmp)

    def test_modified_distilled_note_without_dispatch_causes_broken_status_and_clears_after_dispatch(self):
        import os
        from check_quality import QualityChecker, render_terminal_report
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            cfg = ConfigManager(vault_root=vault)
            notes_dir = vault / cfg.get_room_by_role("reference-and-assets").path / "Notatki"
            notes_dir.mkdir(parents=True, exist_ok=True)
            distilled_note = notes_dir / "Synteza_SAT.md"
            distilled_note.write_text(
                "---\ntype: note\nnote_type: reference\nstage: distilled\nconsumed: false\n---\n# Synteza\n",
                encoding="utf-8",
            )
            os.utime(distilled_note, (past_ts + 30.0, past_ts + 30.0))

            report_before = QualityChecker(vault_root=vault).check_all()
            self.assertEqual(report_before.status, "broken")
            rendered_before = render_terminal_report(report_before)
            self.assertIn("STATUS: 🔴 BROKEN", rendered_before)
            sub_errors = [
                f for f in report_before.findings
                if f.category == "subagents" and f.level == "error" and "destylat wiedzy lub badanie" in f.message
            ]
            self.assertEqual(len(sub_errors), 1, report_before.findings)
            self.assertIn("Synteza_SAT.md", sub_errors[0].message)

            self.assertEqual(run_gate(GOOD, "Klara", "research", vault_root=vault, enforce_binding=False), 0)

            report_after = QualityChecker(vault_root=vault).check_all()
            sub_errors_after = [
                f for f in report_after.findings
                if f.category == "subagents" and f.level == "error"
            ]
            self.assertEqual(sub_errors_after, [])
        finally:
            shutil.rmtree(tmp)

    def test_raw_note_does_not_trigger_knowledge_subagent_requirement(self):
        import os
        tmp, vault, past_ts = self._setup_session_vault()
        try:
            cfg = ConfigManager(vault_root=vault)
            notes_dir = vault / cfg.get_room_by_role("reference-and-assets").path / "Notatki"
            notes_dir.mkdir(parents=True, exist_ok=True)
            raw_note = notes_dir / "Surowa_Notatka.md"
            raw_note.write_text(
                "---\ntype: note\nnote_type: reference\nstage: raw\nconsumed: false\n---\n# Surowa\n",
                encoding="utf-8",
            )
            os.utime(raw_note, (past_ts + 30.0, past_ts + 30.0))

            ctx = QualityContext(vault_root=vault, config=cfg)
            SubagentsPolicyInspector().inspect(ctx)
            errors = [f for f in ctx.findings if f.level == "error"]
            self.assertEqual(errors, [])
        finally:
            shutil.rmtree(tmp)

    def test_audit_gate_readonly_diagnostics_no_modified_files_ok(self):
        from check_quality import QualityChecker
        from lab_core.subagents import run_gate

        tmp, vault, _ = self._setup_session_vault()
        try:
            self.assertEqual(run_gate(GOOD, "Maks", "engineering", vault_root=vault, enforce_binding=False), 0)
            report = QualityChecker(vault_root=vault).check_all()
            audit_errors = [
                f for f in report.findings
                if f.category == "audit_gate" and f.level == "error"
            ]
            self.assertEqual(audit_errors, [])
            self.assertNotEqual(report.status, "broken")
        finally:
            shutil.rmtree(tmp)

    def test_audit_gate_code_change_without_ada_causes_broken(self):
        import os
        from check_quality import QualityChecker
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            script_file = vault / "08 Zespół AI" / "Skrypty" / "lab_core" / "sample_mod.py"
            script_file.parent.mkdir(parents=True, exist_ok=True)
            script_file.write_text("# sample\n", encoding="utf-8")
            os.utime(script_file, (past_ts + 30.0, past_ts + 30.0))

            self.assertEqual(run_gate(GOOD, "Kinga", "code", vault_root=vault, enforce_binding=False), 0)
            report = QualityChecker(vault_root=vault).check_all()
            self.assertEqual(report.status, "broken")
            audit_errors = [
                f for f in report.findings
                if f.category == "audit_gate" and f.level == "error"
            ]
            self.assertEqual(len(audit_errors), 1, report.findings)
        finally:
            shutil.rmtree(tmp)

    def test_audit_gate_code_change_with_ada_fail_causes_broken(self):
        import os
        from check_quality import QualityChecker
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            cfg_file = vault / "lab.config.toml"
            os.utime(cfg_file, (past_ts + 30.0, past_ts + 30.0))

            self.assertEqual(run_gate(GOOD, "Maja", "engineering", vault_root=vault, enforce_binding=False), 0)
            ada_fail = GOOD + "\nQuality Gate: FAIL\n"
            self.assertEqual(run_gate(ada_fail, "Ada", "audit", vault_root=vault, enforce_binding=False), 0)

            report = QualityChecker(vault_root=vault).check_all()
            self.assertEqual(report.status, "broken")
            audit_errors = [
                f for f in report.findings
                if f.category == "audit_gate" and f.level == "error"
            ]
            self.assertEqual(len(audit_errors), 1, report.findings)
            self.assertIn("Quality Gate: FAIL", audit_errors[0].message)
        finally:
            shutil.rmtree(tmp)

    def test_audit_gate_code_change_with_later_ada_pass_clears_broken(self):
        import os
        from check_quality import QualityChecker
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            script_file = vault / "08 Zespół AI" / "Skrypty" / "lab_core" / "sample_mod.py"
            script_file.parent.mkdir(parents=True, exist_ok=True)
            script_file.write_text("# sample\n", encoding="utf-8")
            os.utime(script_file, (past_ts + 30.0, past_ts + 30.0))

            self.assertEqual(run_gate(GOOD, "Maks", "code", vault_root=vault, enforce_binding=False), 0)
            ada_fail = GOOD + "\nQuality Gate: FAIL\n"
            self.assertEqual(run_gate(ada_fail, "Ada", "audit", vault_root=vault, enforce_binding=False), 0)

            report_fail = QualityChecker(vault_root=vault).check_all()
            self.assertEqual(report_fail.status, "broken")

            ada_pass = GOOD + "\nquality gate: pass\n"
            self.assertEqual(run_gate(ada_pass, "Ada", "audit", vault_root=vault, enforce_binding=False), 0)

            report_pass = QualityChecker(vault_root=vault).check_all()
            audit_errors = [
                f for f in report_pass.findings
                if f.category == "audit_gate" and f.level == "error"
            ]
            self.assertEqual(audit_errors, [])
            self.assertNotEqual(report_pass.status, "broken")
        finally:
            shutil.rmtree(tmp)

    def test_audit_gate_long_ada_distillate_preserves_verdict_past_300_chars(self):
        import os
        from check_quality import QualityChecker
        from lab_core.subagents import run_gate

        tmp, vault, past_ts = self._setup_session_vault()
        try:
            script_file = vault / "08 Zespół AI" / "Skrypty" / "lab_core" / "sample_mod.py"
            script_file.parent.mkdir(parents=True, exist_ok=True)
            script_file.write_text("# sample\n", encoding="utf-8")
            os.utime(script_file, (past_ts + 30.0, past_ts + 30.0))

            self.assertEqual(run_gate(GOOD, "Maks", "code", vault_root=vault, enforce_binding=False), 0)

            long_facts = "\n".join(f"- Zweryfikowano moduł nr {i} bez naruszeń." for i in range(15))
            long_ada_pass = (
                f"## Fakty\n{long_facts}\n"
                "## Wnioski\n- Implementacja jest spójna z KISS i Zero-Pip.\n"
                "## Rekomendacje\n- Quality Gate: PASS\n"
            )
            self.assertGreater(len(long_ada_pass), 500)
            self.assertEqual(run_gate(long_ada_pass, "Ada", "audit", vault_root=vault, enforce_binding=False), 0)

            report = QualityChecker(vault_root=vault).check_all()
            audit_errors = [
                f for f in report.findings
                if f.category == "audit_gate" and f.level == "error"
            ]
            self.assertEqual(audit_errors, [])
            self.assertNotEqual(report.status, "broken")
        finally:
            shutil.rmtree(tmp)

    def test_consumed_distilled_note_does_not_trigger_knowledge_subagent_requirement(self):
        import os
        tmp, vault, past_ts = self._setup_session_vault()
        try:
            cfg = ConfigManager(vault_root=vault)
            notes_dir = vault / cfg.get_room_by_role("reference-and-assets").path / "Notatki"
            notes_dir.mkdir(parents=True, exist_ok=True)
            consumed_note = notes_dir / "Przeczytana_Notatka.md"
            consumed_note.write_text(
                "---\ntype: note\nnote_type: reference\nstage: distilled\nconsumed: true\n---\n# Przeczytana\n",
                encoding="utf-8",
            )
            os.utime(consumed_note, (past_ts + 30.0, past_ts + 30.0))

            ctx = QualityContext(vault_root=vault, config=cfg)
            SubagentsPolicyInspector().inspect(ctx)
            errors = [f for f in ctx.findings if f.level == "error"]
            self.assertEqual(errors, [])
        finally:
            shutil.rmtree(tmp)


class TestDispatchGuard(unittest.TestCase):
    def test_blocks_cross_domain_dispatches(self):
        from lab_core.distillate import validate_dispatch_guard
        ok, err = validate_dispatch_guard("engineer", "Maks", "research")
        self.assertFalse(ok)
        self.assertIn("Dispatch Guard", err)

        ok, err = validate_dispatch_guard("database_code", "Kinga", "knowledge")
        self.assertFalse(ok)
        self.assertIn("Dispatch Guard", err)

        ok, err = validate_dispatch_guard("researcher", "Klara", "code")
        self.assertFalse(ok)
        self.assertIn("Dispatch Guard", err)

        ok, err = validate_dispatch_guard("engineer", "Maks", "code")
        self.assertTrue(ok)
        self.assertIsNone(err)

        ok, err = validate_dispatch_guard("researcher", "Klara", "research")
        self.assertTrue(ok)
        self.assertIsNone(err)


class TestVariantBDistillate(unittest.TestCase):
    def test_estimate_tokens_ignores_urls(self):
        base = "Fakty: raport.\nWnioski: ok.\nRekomendacje: gotowe."
        with_http = base + " https://example.com/very/long/url/path/to/resource?query=123"
        with_file = base + " file:///tmp/sample-vault/docs/file.md"
        self.assertEqual(estimate_tokens(base), estimate_tokens(with_http))
        self.assertEqual(estimate_tokens(base), estimate_tokens(with_file))

    def test_validate_sat_distillate_custom_max_tokens(self):
        lines = ["- punkt: " + "a" * 150 for _ in range(16)]  # ~600+ tokenów
        text = "Fakty:\n" + "\n".join(lines) + "\nWnioski:\n- ok\nRekomendacje:\n- ok"
        tokens = estimate_tokens(text)
        # Zaakceptuj przy wyższym limicie (np. 850)
        ok_high, _ = validate_sat_distillate(text, max_tokens=tokens + 50)
        self.assertTrue(ok_high)
        # Odrzuć przy niższym limicie (np. tokens - 50)
        ok_low, problems = validate_sat_distillate(text, max_tokens=tokens - 50)
        self.assertFalse(ok_low)
        self.assertTrue(any("limit" in p for p in problems))

    def test_subagent_gate_check_only_task_type_thresholds(self):
        from lab_core.subagents import run_check_only
        lines = ["- punkt: " + "b" * 150 for _ in range(17)]  # ~660-700 tokenów
        text = "Fakty:\n" + "\n".join(lines) + "\nWnioski:\n- ok\nRekomendacje:\n- ok"
        tokens = estimate_tokens(text)
        self.assertTrue(650 < tokens < 850)
        # code ma limit 650 -> odrzucenie
        self.assertEqual(run_check_only(text, task_type="code"), 1)
        # research ma limit 850 -> akceptacja
        self.assertEqual(run_check_only(text, task_type="research"), 0)


class TestAgentContractAntiBloat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        root = find_vault_root()
        cfg_mgr = ConfigManager(vault_root=root)
        shutil.copy2(root / "lab.config.toml", self.tmp / "lab.config.toml")
        shutil.copy2(root / "AGENTS.md", self.tmp / "AGENTS.md")
        ai_rel = cfg_mgr.get_room_by_role("governance-and-engine").path
        shutil.copytree(root / ai_rel / "Role", self.tmp / ai_rel / "Role")
        self.ai_rel = ai_rel

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _inspect(self):
        cfg = ConfigManager(vault_root=self.tmp)
        ctx = QualityContext(vault_root=self.tmp, config=cfg)
        SubagentsPolicyInspector().inspect(ctx)
        return ctx

    def test_warns_on_missing_allowlist(self):
        adam_file = self.tmp / self.ai_rel / "Role" / "Koordynator - Adam" / "AGENT.md"
        adam_file.write_text(
            "---\ntype: agent\nname: Adam\nrole: C\nrouting_description: R\n---\n"
            "# Adam\nBrak białej listy\n",
            encoding="utf-8",
        )
        ctx = self._inspect()
        warnings = [f for f in ctx.findings if f.level == "warning" and "Białej Listy" in f.message]
        self.assertTrue(len(warnings) > 0)

    def test_warns_on_over_50_lines(self):
        adam_file = self.tmp / self.ai_rel / "Role" / "Koordynator - Adam" / "AGENT.md"
        long_body = "\n".join([f"linia {i}" for i in range(60)])
        adam_file.write_text(
            "---\ntype: agent\nname: Adam\nrole: C\nrouting_description: R\n---\n"
            "# Adam\n## Allowlist\n" + long_body,
            encoding="utf-8",
        )
        ctx = self._inspect()
        warnings = [f for f in ctx.findings if f.level == "warning" and "limit 50 linii" in f.message]
        self.assertTrue(len(warnings) > 0)

    def test_table_lines_excluded_from_line_limit(self):
        adam_file = self.tmp / self.ai_rel / "Role" / "Koordynator - Adam" / "AGENT.md"
        table_lines = "\n".join([f"| Narzędzie {i} | Opis {i} |" for i in range(40)])
        text = (
            "---\ntype: agent\nname: Adam\nrole: C\nrouting_description: R\n---\n"
            "# Adam\n## Allowlist\n- dialog\n| Kolumna 1 | Kolumna 2 |\n| --- | --- |\n"
            + table_lines
        )
        adam_file.write_text(text, encoding="utf-8")
        ctx = self._inspect()
        warnings = [f for f in ctx.findings if f.level == "warning" and "limit 50 linii" in f.message and "Adam" in f.message]
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
