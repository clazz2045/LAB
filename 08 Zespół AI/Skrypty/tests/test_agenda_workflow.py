#!/usr/bin/env python3
"""test_agenda_workflow.py - Testy deterministycznego workflow Agendy Roboczej (LAB 3.0)

Pokrywa:
- typy wątków w '## 🎯' ([PROMPT], [PYTANIE], [ZADANIE], [DECYZJA]) i ich zachowanie przy rolloverze,
- wykrywanie nieprzetworzonych bloków '---' w '## ✍️' jako ATTENTION (workshop.py),
- ochronę myśli człowieka przy oznaczaniu bloków (security.py: assert_raw_thoughts_preserved),
- uprawnienia edycji Agendy przez subagenta (pre_tool_guard.py).

Standard: 100% Python Standard Library (Zero-Pip).
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from core_config import ConfigManager
from lab_core.installer import install_lab_vault
from lab_core.pre_tool_guard import evaluate_pre_tool
from lab_core.quality.context import QualityContext
from lab_core.quality.inspectors.workshop import (
    WorkshopAndThoughtsInspector,
    detect_unprocessed_thought_blocks,
    extract_agenda_section,
)
from lab_core.security import UnauthorizedSprintInjectionError, assert_raw_thoughts_preserved
from rollover_agenda import AgendaRollover, parse_thread_type


class TestAgendaRolloverTypedThreads(unittest.TestCase):
    def test_parse_thread_type(self):
        self.assertEqual(parse_thread_type("- [ ] [PROMPT] Zbuduj workflow"), "PROMPT")
        self.assertEqual(parse_thread_type("- [x] [pytanie] Jak działa X?"), "PYTANIE")
        self.assertEqual(parse_thread_type("- [ ] [ZADANIE] Napraw testy"), "ZADANIE")
        self.assertEqual(parse_thread_type("- [ ] [DECYZJA] Wybór wariantu A/B"), "DECYZJA")
        self.assertIsNone(parse_thread_type("- [ ] Zwykły wątek bez tagu"))

    def test_rollover_preserves_typed_threads_and_ignores_thoughts_section(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            ws_dir = vault / "02 Warsztat"
            yesterday = datetime.date(2026, 11, 12)
            today = datetime.date(2026, 11, 13)

            old_agenda = ws_dir / f"{yesterday.isoformat()} – Agenda Robocza.md"
            old_agenda.write_text(
                "---\nstatus: in_progress\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [x] [ZADANIE] Zamknięte zadanie\n"
                "- [ ] [PROMPT] Odłożony prompt o architekturze Google Drive\n"
                "  - Podpunkt kontekstowy\n"
                "- [ ] [PYTANIE] Pytanie badawcze o środowiska AI\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew (Skrzynka & Brudnopis)\n\n"
                "- [ ] Fałszywy checkbox w myślach użytkownika nie może być przeniesiony\n",
                encoding="utf-8",
            )

            res = AgendaRollover(vault_root=vault).run_rollover(target_date=today)
            self.assertEqual(res["tasks_carried_over"], 2)
            self.assertEqual(res["tasks_merged_into_today"], 2)
            self.assertEqual(res["carried_over_by_type"], {"PROMPT": 1, "PYTANIE": 1})

            new_content = (ws_dir / f"{today.isoformat()} – Agenda Robocza.md").read_text(encoding="utf-8")
            self.assertNotIn("### 🔄 Zadania przeniesione z poprzedniej agendy:", new_content)
            self.assertIn("- [ ] [PROMPT] Odłożony prompt o architekturze Google Drive", new_content)
            self.assertIn("  - Podpunkt kontekstowy", new_content)
            self.assertIn("- [ ] [PYTANIE] Pytanie badawcze o środowiska AI", new_content)
            self.assertNotIn("Zamknięte zadanie", new_content)
            threads_section = extract_agenda_section(new_content, "threads")
            self.assertNotIn("Fałszywy checkbox", threads_section)
            thoughts_section = extract_agenda_section(new_content, "thoughts")
            self.assertIn("Fałszywy checkbox", thoughts_section)

    def test_rollover_uses_template_and_carries_over_unprocessed_thoughts_and_subtrees(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            tpl_dir = vault / "08 Zespół AI" / "Szablony"
            tpl_dir.mkdir(parents=True, exist_ok=True)
            real_tpl = Path(__file__).resolve().parents[2] / "Szablony" / "Szablon Agendy Roboczej.md"
            (tpl_dir / "Szablon Agendy Roboczej.md").write_text(
                real_tpl.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            ws_dir = vault / "02 Warsztat"
            yesterday = datetime.date(2026, 11, 13)
            today = datetime.date(2026, 11, 14)

            old_agenda = ws_dir / f"{yesterday.isoformat()} – Agenda Robocza.md"
            old_agenda.write_text(
                "---\ntype: workbench-agenda\nstatus: in_progress\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [x] [ZADANIE] Rodzic zamknięty\n"
                "  - [ ] [ZADANIE] Osierocony podpunkt do przeniesienia\n"
                "- [ ] [PROMPT] Zadanie wieloliniowe\n"
                "  Kontynuacja tekstu 2 spacje\n"
                "    - Podpunkt 4 spacje\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew (Skrzynka & Brudnopis)\n\n"
                "Przetworzona myśl\n<!-- processed -->\n\n---\n\n"
                "Nieprzetworzona myśl krocząca do przeniesienia 1:1\n",
                encoding="utf-8",
            )

            res = AgendaRollover(vault_root=vault).run_rollover(target_date=today)
            self.assertEqual(res["tasks_carried_over"], 2)
            self.assertEqual(res["thoughts_carried_over"], 1)
            self.assertEqual(res["thoughts_merged_into_today"], 1)

            new_content = (ws_dir / f"{today.isoformat()} – Agenda Robocza.md").read_text(encoding="utf-8")
            self.assertNotIn("### 🔄", new_content)
            self.assertIn("- [ ] [ZADANIE] Osierocony podpunkt do przeniesienia", new_content)
            self.assertIn("  Kontynuacja tekstu 2 spacje", new_content)
            self.assertIn("    - Podpunkt 4 spacje", new_content)
            self.assertIn("*(Wątek kroczący z 2026-11-13", new_content)
            self.assertIn("Nieprzetworzona myśl krocząca do przeniesienia 1:1", new_content)
            self.assertNotIn("Przetworzona myśl", new_content)

            # Idempotentność ponownego uruchomienia
            res2 = AgendaRollover(vault_root=vault).run_rollover(target_date=today)
            self.assertEqual(res2["tasks_merged_into_today"], 0)
            self.assertEqual(res2["thoughts_merged_into_today"], 0)


class TestUnprocessedThoughtBlocks(unittest.TestCase):
    def test_detect_unprocessed_thought_blocks(self):
        content = (
            "## 🎯 Główne Wątki Robocze na Dziś\n\n"
            "- [ ] [ZADANIE] Wątek roboczy\n\n"
            "---\n\n"
            "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew (Skrzynka & Brudnopis)\n\n"
            "*(Źródło: `01 Skrzynka/2026-11-13.md` — zapis oryginalny 1:1)*\n\n"
            "---\n\n"
            "Stary blok już przetworzony przez Edytę\n"
            "<!-- processed -->\n\n"
            "---\n\n"
            "~~Przekreślony blok historyczny~~\n\n"
            "---\n\n"
            "> 📦 **Sekcja przeniesiona do projektu:** [[PR-011]]\n"
            "Blok z adnotacją przeniesienia\n\n"
            "---\n\n"
            "Nowa myśl dopisana w ciągu dnia bez markera\n\n"
            "---\n"
        )
        blocks = detect_unprocessed_thought_blocks(content)
        self.assertEqual(len(blocks), 1)
        self.assertIn("Nowa myśl dopisana w ciągu dnia", blocks[0])

    def test_workshop_inspector_warns_attention_on_unprocessed_blocks(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            agenda = vault / "02 Warsztat" / "2026-11-13 – Agenda Robocza.md"
            agenda.write_text(
                "---\ntype: workbench-agenda\nstatus: in_progress\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew (Skrzynka & Brudnopis)\n\n"
                "Nowy blok 1\n\n---\n\nNowy blok 2\n",
                encoding="utf-8",
            )
            cfg = ConfigManager(vault_root=vault)
            ctx = QualityContext(vault_root=vault, config=cfg)
            WorkshopAndThoughtsInspector().inspect(ctx)

            self.assertEqual(ctx.stats.get("unprocessed_thought_blocks"), 2)
            warnings = [f for f in ctx.findings if f.level == "warning" and f.category == "agenda_thoughts"]
            errors = [f for f in ctx.findings if f.level == "error"]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(errors, [])
            self.assertIn("2 nieprzetworzonych bloków myśli", warnings[0].message)

    def test_workshop_inspector_strict_checkpoint_and_rollover_gate(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            ws_dir = vault / "02 Warsztat"
            arch_dir = ws_dir / "_archiwum" / "2026" / "11"
            arch_dir.mkdir(parents=True, exist_ok=True)

            (arch_dir / "2026-11-13 – Agenda Robocza.md").write_text(
                "---\ntype: workbench-agenda\nstatus: archived\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [ ] [ZADANIE] Zgubione zadanie z wczoraj\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n"
                "Zgubiona myśl z wczoraj\n",
                encoding="utf-8",
            )
            (ws_dir / "2026-11-14 – Agenda Robocza.md").write_text(
                "---\ntype: workbench-agenda\nstatus: in_progress\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n"
                "Bieżąca nieprzetworzona myśl\n",
                encoding="utf-8",
            )

            cfg = ConfigManager(vault_root=vault)
            ctx = QualityContext(vault_root=vault, config=cfg, strict_checkpoint=True)
            WorkshopAndThoughtsInspector().inspect(ctx)

            rollover_errors = [f for f in ctx.findings if f.level == "error" and f.category == "agenda_rollover"]
            thought_errors = [f for f in ctx.findings if f.level == "error" and f.category == "agenda_thoughts"]
            self.assertEqual(len(rollover_errors), 1)
            self.assertIn("1 otwartych zadań", rollover_errors[0].message)
            self.assertIn("1 nieprzetworzonych bloków myśli", rollover_errors[0].message)
            self.assertEqual(len(thought_errors), 1)

    def test_workshop_inspector_rollover_allows_updated_trailing_progress_note(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            ws_dir = vault / "02 Warsztat"
            arch_dir = ws_dir / "_archiwum" / "2026" / "11"
            arch_dir.mkdir(parents=True, exist_ok=True)

            (arch_dir / "2026-11-15 – Agenda Robocza.md").write_text(
                "---\ntype: workbench-agenda\nstatus: archived\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [ ] [ZADANIE] **Broszura:** Poprawić tabelkę. *(Częściowo wykonane w Etapie 1).*\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n",
                encoding="utf-8",
            )
            (ws_dir / "2026-11-16 – Agenda Robocza.md").write_text(
                "---\ntype: workbench-agenda\nstatus: in_progress\n---\n\n"
                "## 🎯 Główne Wątki Robocze na Dziś\n\n"
                "- [ ] [ZADANIE] **Broszura:** Poprawić tabelkę. *(Zrealizowano w sesji 3; pozostało E2E).*\n\n"
                "---\n\n"
                "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n",
                encoding="utf-8",
            )

            cfg = ConfigManager(vault_root=vault)
            ctx = QualityContext(vault_root=vault, config=cfg)
            WorkshopAndThoughtsInspector().inspect(ctx)

            rollover_errors = [f for f in ctx.findings if f.level == "error" and f.category == "agenda_rollover"]
            self.assertEqual(rollover_errors, [])

    def test_workshop_inspector_allows_tmp_notes_and_rejects_invalid_files(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            vault = Path(tmp_str) / "Vault"
            install_lab_vault(vault, skip_preflight=True, run_smoke_check=False)
            ws_dir = vault / "02 Warsztat"

            (ws_dir / "TMP - Przykładowa notatka.md").write_text(
                "---\ntype: workbench-note\nstatus: wip\n---\n\n# Robocza notatka\n",
                encoding="utf-8",
            )
            (ws_dir / "Luźna notatka.md").write_text("# Niepoprawny plik\n", encoding="utf-8")
            (ws_dir / "TMP - .md").write_text("# Pusty tytuł po prefiksie\n", encoding="utf-8")
            (ws_dir / "TMP -   .md").write_text("# Same spacje po prefiksie\n", encoding="utf-8")
            (ws_dir / "TMP - folder.md").mkdir()

            cfg = ConfigManager(vault_root=vault)
            ctx = QualityContext(vault_root=vault, config=cfg)
            WorkshopAndThoughtsInspector().inspect(ctx)

            workshop_warnings = [f for f in ctx.findings if f.category == "workshop"]
            warned_paths = {f.path for f in workshop_warnings}
            self.assertNotIn("02 Warsztat/TMP - Przykładowa notatka.md", warned_paths)
            self.assertEqual(
                warned_paths,
                {
                    "02 Warsztat/Luźna notatka.md",
                    "02 Warsztat/TMP - .md",
                    "02 Warsztat/TMP -   .md",
                    "02 Warsztat/TMP - folder.md",
                },
            )
            self.assertTrue(all("TMP - <tytuł>.md" in f.message for f in workshop_warnings))


class TestSecurityAndPreToolGuardAgenda(unittest.TestCase):
    def test_assert_raw_thoughts_preserved_allows_marker_blocks_deletion(self):
        old = (
            "## 🎯 Główne Wątki Robocze na Dziś\n\n---\n\n"
            "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n"
            "Oryginalna myśl użytkownika 1:1\n"
        )
        valid_new = (
            "## 🎯 Główne Wątki Robocze na Dziś\n\n"
            "- [ ] [PYTANIE] Streszczenie pytania użytkownika\n\n---\n\n"
            "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n"
            "Oryginalna myśl użytkownika 1:1\n"
            "<!-- processed -->\n"
        )
        assert_raw_thoughts_preserved(old, valid_new)

        tampered_new = (
            "## 🎯 Główne Wątki Robocze na Dziś\n\n---\n\n"
            "## ✍️ Dosłowny Zapis Porannych Myśli Zbigniew\n\n"
            "Zmieniona myśl użytkownika\n"
        )
        with self.assertRaises(UnauthorizedSprintInjectionError):
            assert_raw_thoughts_preserved(old, tampered_new)

    def test_subagent_edyta_can_edit_agenda_in_pre_tool_guard(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            logs_dir = tmp / "brain" / "sub1" / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            tpath = logs_dir / "transcript.jsonl"
            tpath.write_text(
                json.dumps({"source": "SYSTEM", "content": "[Message] sender=parent-id content=Edyta"}) + "\n",
                encoding="utf-8",
            )
            payload = {
                "tool_name": "edit_file",
                "transcript_path": str(tpath),
                "tool_input": {"Path": "02 Warsztat/2026-11-13 – Agenda Robocza.md"},
            }
            decision = evaluate_pre_tool(payload, vault_root=tmp)
            self.assertEqual(decision.get("decision"), "allow")


if __name__ == "__main__":
    unittest.main()
