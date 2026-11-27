"""test_schema.py - Zestaw testów jednostkowych dla bazy danych (lab_core.schema)
Standard inżynieryjny: 100% Python Standard Library (unittest, sqlite3, tempfile).
Testy weryfikują poprawność DDL, tryb WAL, integralność i idempotentność.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

# Bootstrap sys.path
_scripts_dir = Path(__file__).resolve().parent.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from lab_core.schema import (
    EXPECTED_TABLES,
    ensure_dispatch_columns,
    get_master_db_connection,
    init_master_db,
    log_subagent_dispatch,
    verify_master_db,
)


class TestLabMasterSchema(unittest.TestCase):
    """Testy jednostkowe schematu relacyjnego i funkcji bazy danych."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_lab_master.db"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_init_master_db_creates_file_and_tables(self):
        result = init_master_db(self.db_path, instance_name="TEST_LAB")
        self.assertTrue(result)
        self.assertTrue(self.db_path.is_file())

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cur.fetchall()}
            for exp in EXPECTED_TABLES:
                self.assertIn(exp, tables, f"Brakująca tabela: {exp}")
        finally:
            conn.close()

    def test_init_master_db_wal_mode_and_pragmas(self):
        init_master_db(self.db_path, instance_name="TEST_LAB")
        conn = get_master_db_connection(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            jm = cur.fetchone()[0]
            self.assertEqual(jm.lower(), "wal")

            cur.execute("PRAGMA synchronous;")
            sync = cur.fetchone()[0]
            # 1 to NORMAL
            self.assertEqual(sync, 1)

            cur.execute("PRAGMA foreign_keys;")
            fk = cur.fetchone()[0]
            self.assertEqual(fk, 1)
        finally:
            conn.close()

    def test_system_meta_content(self):
        init_master_db(self.db_path, instance_name="LAB_PROD", schema_version="2.0")
        v = verify_master_db(self.db_path)
        self.assertTrue(v["valid"])
        meta = v["meta"]
        self.assertEqual(meta["instance_name"], "LAB_PROD")
        self.assertEqual(meta["schema_version"], "2.0")
        try:
            from lab_core.config import ConfigManager
            expected_architect = ConfigManager().get_agent_name("database_code")
        except Exception:
            expected_architect = "database_code"
        self.assertEqual(meta["architect"], expected_architect)
        self.assertIn("initialized_at", meta)

    def test_crud_and_indexes(self):
        init_master_db(self.db_path, instance_name="TEST_LAB")
        conn = get_master_db_connection(self.db_path)
        try:
            # 1. Projects
            conn.execute(
                """
                INSERT INTO projects (id, title, status, focus_rank, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("PR-015", "Modularyzacja Silnika", "active", 1, "2026-11-04", "2026-11-05"),
            )
            # 2. Tasks
            conn.execute(
                """
                INSERT INTO tasks (id, project_id, title, status, signal, context, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("TD-001", "PR-015", "Utworzyć schemat SQLite", "done", "kinga-db", "lab_master.db", "2026-11-05"),
            )
            # 3. Session telemetry
            conn.execute(
                """
                INSERT INTO session_telemetry (session_id, session_date, session_number, tokens_active, tokens_limit, load_zone, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("ses-042", "2026-11-05", 8, 42000, 1000000, "safe", "2026-11-05T10:00:00Z"),
            )
            # 4. Assets registry
            conn.execute(
                """
                INSERT INTO assets_registry (filename, room, path, consumed, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("notatka.md", "06 Zasoby", "06 Zasoby/Notatki/notatka.md", 0, "2026-11-05"),
            )
            conn.commit()

            # Weryfikacja danych
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM projects WHERE id='PR-015';")
            self.assertEqual(cur.fetchone()[0], 1)

            cur.execute("SELECT title FROM tasks WHERE project_id='PR-015';")
            self.assertEqual(cur.fetchone()[0], "Utworzyć schemat SQLite")

            cur.execute("SELECT consumed FROM assets_registry WHERE filename='notatka.md';")
            self.assertEqual(cur.fetchone()[0], 0)

            # Sprawdzenie obecności indeksów
            cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
            indices = {r[0] for r in cur.fetchall()}
            self.assertIn("idx_projects_status", indices)
            self.assertIn("idx_tasks_project", indices)
            self.assertIn("idx_telemetry_session", indices)
            self.assertIn("idx_assets_consumed", indices)
        finally:
            conn.close()

    def test_idempotence_does_not_wipe_data(self):
        init_master_db(self.db_path, instance_name="TEST_LAB")
        conn = get_master_db_connection(self.db_path)
        try:
            conn.execute(
                "INSERT INTO projects (id, title, status, focus_rank, created_at, updated_at) VALUES ('PR-001', 'Test', 'active', 1, '2026-11-05', '2026-11-05')"
            )
            conn.commit()
        finally:
            conn.close()

        # Drugie wywołanie init_master_db
        re_init = init_master_db(self.db_path, instance_name="TEST_LAB")
        self.assertTrue(re_init)

        conn2 = get_master_db_connection(self.db_path)
        try:
            cur = conn2.cursor()
            cur.execute("SELECT title FROM projects WHERE id='PR-001';")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Test")
        finally:
            conn2.close()

    def test_verify_master_db_negative_cases(self):
        # 1. Nieistniejący plik
        non_existent = Path(self.tmp_dir.name) / "ghost.db"
        res1 = verify_master_db(non_existent)
        self.assertFalse(res1["exists"])
        self.assertFalse(res1["valid"])
        self.assertEqual(len(res1["missing_tables"]), len(EXPECTED_TABLES))

        # 2. Pusta baza sqlite bez tabel
        empty_db = Path(self.tmp_dir.name) / "empty.db"
        c = sqlite3.connect(str(empty_db))
        c.close()

        res2 = verify_master_db(empty_db)
        self.assertTrue(res2["exists"])
        self.assertFalse(res2["valid"])
        self.assertGreater(len(res2["missing_tables"]), 0)

    def test_subagent_dispatches_skill_used_migration_and_logging(self):
        # 1. Symulacja starszej bazy bez kolumn note, subagent_id, content_sha256, skill_used
        conn = get_master_db_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE subagent_dispatches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dispatch_time TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    tokens_used INTEGER,
                    status TEXT NOT NULL,
                    summary_excerpt TEXT
                );
                """
            )
            conn.execute(
                """
                INSERT INTO subagent_dispatches (dispatch_time, agent_name, task_type, tokens_used, status, summary_excerpt)
                VALUES ('2026-11-01T10:00:00Z', 'Maks', 'code', 100, 'accepted', 'legacy')
                """
            )
            conn.commit()
            ensure_dispatch_columns(conn)
            cols = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(subagent_dispatches)")}
            self.assertIn("skill_used", cols)
            self.assertEqual(cols["skill_used"], "''")
            legacy = conn.execute("SELECT skill_used FROM subagent_dispatches WHERE summary_excerpt='legacy'").fetchone()
            self.assertEqual(legacy["skill_used"], "")
        finally:
            conn.close()

        # 2. Idempotentne init_master_db oraz log_subagent_dispatch z i bez skill_used
        self.assertTrue(init_master_db(self.db_path, instance_name="TEST_LAB"))
        r1 = log_subagent_dispatch(self.db_path, "Kinga", "code", "accepted", skill_used="sqlite-wal")
        r2 = log_subagent_dispatch(self.db_path, "Ada", "audit", "accepted")

        conn2 = get_master_db_connection(self.db_path)
        try:
            row1 = conn2.execute("SELECT skill_used FROM subagent_dispatches WHERE id=?", (r1,)).fetchone()
            row2 = conn2.execute("SELECT skill_used FROM subagent_dispatches WHERE id=?", (r2,)).fetchone()
            self.assertEqual(row1["skill_used"], "sqlite-wal")
            self.assertEqual(row2["skill_used"], "")
        finally:
            conn2.close()


if __name__ == "__main__":
    unittest.main()
