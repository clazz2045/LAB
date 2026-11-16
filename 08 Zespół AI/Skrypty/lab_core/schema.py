"""lab_core.schema - Relacyjna Baza Danych SQLite (lab_master.db) dla LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (sqlite3, pathlib, datetime).
Zapewnia atomową inicjalizację bazy w trybie WAL oraz schemat relacyjny:
system_meta, projects, tasks, session_telemetry, assets_registry, subagent_dispatches.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.dont_write_bytecode = True

# Schemat DDL relacyjnej bazy danych
SCHEMA_SQL = """
-- 1. Tabela metadanych systemowych instancji
CREATE TABLE IF NOT EXISTS system_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

-- 2. Tabela projektów (Filar II portfela PR-xxx)
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    focus_rank INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Tabela zadań (zadania operacyjne oraz podzadania projektowe)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    signal TEXT,
    context TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- 4. Tabela telemetrii tokenów i obciążenia sesji Antigravity/Gemini
CREATE TABLE IF NOT EXISTS session_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    session_number INTEGER DEFAULT 0,
    tokens_active INTEGER NOT NULL,
    tokens_limit INTEGER NOT NULL,
    load_zone TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    planner_calls INTEGER DEFAULT 0,
    subagents_count INTEGER DEFAULT 0,
    subagents_input_tokens INTEGER DEFAULT 0,
    subagents_cache_read_tokens INTEGER DEFAULT 0,
    subagents_output_tokens INTEGER DEFAULT 0,
    subagents_planner_calls INTEGER DEFAULT 0,
    peak_context_tokens INTEGER DEFAULT 0
);

-- 5. Rejestr zasobów i notatek referencyjnych (z flagą consumed)
CREATE TABLE IF NOT EXISTS assets_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    room TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    consumed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 6. Rejestr wywołań subagentów (polityka subagentów, Reguła 8)
CREATE TABLE IF NOT EXISTS subagent_dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_time TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    tokens_used INTEGER,
    status TEXT NOT NULL,
    summary_excerpt TEXT,
    note TEXT,
    subagent_id TEXT,
    content_sha256 TEXT
);

-- 7. Rejestr audytu zgodności sesji głównej (detekcja po fakcie, Faza 6)
CREATE TABLE IF NOT EXISTS session_compliance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    transcript_path TEXT NOT NULL,
    status TEXT NOT NULL,
    reads_before_first_invoke INTEGER NOT NULL,
    write_calls INTEGER NOT NULL,
    details TEXT,
    note TEXT
);

-- Indeksy optymalizujące zapytania relacyjne i analityczne
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_telemetry_session ON session_telemetry(session_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_date ON session_telemetry(session_date);
CREATE INDEX IF NOT EXISTS idx_assets_consumed ON assets_registry(consumed);
CREATE INDEX IF NOT EXISTS idx_assets_room ON assets_registry(room);
CREATE INDEX IF NOT EXISTS idx_dispatch_agent ON subagent_dispatches(agent_name);
CREATE INDEX IF NOT EXISTS idx_dispatch_time ON subagent_dispatches(dispatch_time);
"""

EXPECTED_TABLES = (
    "system_meta",
    "projects",
    "tasks",
    "session_telemetry",
    "assets_registry",
    "subagent_dispatches",
)


def get_master_db_connection(db_path: Path | str) -> sqlite3.Connection:
    """Otwiera i konfiguruje optymalne połączenie z bazą lab_master.db."""
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Wymuś tryb WAL i foreign keys
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


DISPATCH_EXTRA_COLUMNS = ("note", "subagent_id", "content_sha256")
TELEMETRY_EXTRA_COLUMNS = (
    "input_tokens",
    "cache_read_tokens",
    "output_tokens",
    "planner_calls",
    "subagents_count",
    "subagents_input_tokens",
    "subagents_cache_read_tokens",
    "subagents_output_tokens",
    "subagents_planner_calls",
    "peak_context_tokens",
)


def ensure_dispatch_columns(conn: sqlite3.Connection) -> None:
    """Wstecznie zgodna migracja: dodaje brakujące kolumny adnotacji do subagent_dispatches."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(subagent_dispatches)")}
    if not existing:
        return
    for col in DISPATCH_EXTRA_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE subagent_dispatches ADD COLUMN {col} TEXT")


def ensure_telemetry_columns(conn: sqlite3.Connection) -> None:
    """Wstecznie zgodna migracja: dodaje brakujące kolumny sum i subagentów do session_telemetry."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(session_telemetry)")}
    if not existing:
        return
    for col in TELEMETRY_EXTRA_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE session_telemetry ADD COLUMN {col} INTEGER DEFAULT 0")


def init_master_db(
    db_path: Path | str,
    instance_name: str = "LAB",
    schema_version: str = "2.0",
    architect: Optional[str] = None,
) -> bool:
    """Inicjalizuje bazę SQLite lab_master.db.

    Zwraca True, jeśli baza została zainicjalizowana pomyślnie.
    Działanie jest idempotentne (CREATE TABLE IF NOT EXISTS).
    """
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    if architect is None:
        try:
            from .config import ConfigManager
            cfg = ConfigManager()
            architect = cfg.get_agent_name("database_code")
        except Exception:
            architect = "database_code"

    conn = get_master_db_connection(path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
            ensure_dispatch_columns(conn)
            ensure_telemetry_columns(conn)

            # Inicjalne metadane systemowe
            metadata = [
                ("instance_name", instance_name, now_iso),
                ("schema_version", schema_version, now_iso),
                ("initialized_at", now_iso, now_iso),
                ("engine_role", "master-registry", now_iso),
                ("architect", architect, now_iso),
            ]
            conn.executemany(
                """
                INSERT INTO system_meta (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                metadata,
            )
        return True
    finally:
        conn.close()


def log_subagent_dispatch(
    db_path: Path | str,
    agent_name: str,
    task_type: str,
    status: str,
    tokens_used: Optional[int] = None,
    summary_excerpt: Optional[str] = None,
    subagent_id: Optional[str] = None,
    content_sha256: Optional[str] = None,
    note: Optional[str] = None,
) -> int:
    """Zapisuje wywołanie subagenta w rejestrze i zwraca ID wiersza.

    Excerpt jest przycinany do 300 znaków (ochrona przed zrzutami w bazie).
    """
    excerpt = summary_excerpt[:300] if summary_excerpt else None
    conn = get_master_db_connection(db_path)
    try:
        with conn:
            ensure_dispatch_columns(conn)
            cur = conn.execute(
                """
                INSERT INTO subagent_dispatches
                    (dispatch_time, agent_name, task_type, tokens_used, status, summary_excerpt,
                     subagent_id, content_sha256, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    agent_name,
                    task_type,
                    tokens_used,
                    status,
                    excerpt,
                    subagent_id,
                    content_sha256,
                    note,
                ),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def mark_dispatch_invalid(
    db_path: Path | str,
    row_id: int,
    note: str,
    new_status: str = "invalid_reconstructed",
    expected_status: str = "accepted",
) -> bool:
    """Oznacza wpis jako nieważny BEZ kasowania (ślad audytowy). Jedna transakcja.

    Zmienia wyłącznie status i adnotację; zwraca False, gdy wpis nie istnieje lub ma inny status.
    """
    conn = get_master_db_connection(db_path)
    try:
        with conn:
            ensure_dispatch_columns(conn)
            cur = conn.execute(
                "UPDATE subagent_dispatches SET status = ?, note = ? WHERE id = ? AND status = ?",
                (new_status, note, row_id, expected_status),
            )
            return cur.rowcount == 1
    finally:
        conn.close()


def record_session_compliance(
    db_path: Path | str,
    transcript_path: str,
    status: str,
    reads_before_first_invoke: int,
    write_calls: int,
    details: str,
    note: Optional[str] = None,
) -> int:
    """Zapisuje wynik audytu zgodności sesji w rejestrze i zwraca ID wiersza."""
    conn = get_master_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_compliance (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "checked_at TEXT NOT NULL, transcript_path TEXT NOT NULL, status TEXT NOT NULL, "
                "reads_before_first_invoke INTEGER NOT NULL, write_calls INTEGER NOT NULL, details TEXT, note TEXT)"
            )
            cur = conn.execute(
                "INSERT INTO session_compliance (checked_at, transcript_path, status, reads_before_first_invoke, "
                "write_calls, details, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), transcript_path, status,
                 reads_before_first_invoke, write_calls, details[:2000], note),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def verify_master_db(db_path: Path | str) -> Dict[str, Any]:
    """Weryfikuje strukturę i integralność bazy lab_master.db.

    Zwraca słownik z wynikami kontroli:
    - 'exists': bool
    - 'tables': lista istniejących tabel
    - 'missing_tables': lista brakujących tabel
    - 'journal_mode': aktywny tryb journal_mode (oczekiwany 'wal')
    - 'meta': metadane odczytane z tabeli system_meta
    - 'valid': bool
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        return {
            "exists": False,
            "tables": [],
            "missing_tables": list(EXPECTED_TABLES),
            "journal_mode": None,
            "meta": {},
            "valid": False,
        }

    conn = get_master_db_connection(path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        row_jm = cur.fetchone()
        journal_mode = row_jm[0] if row_jm else None

        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cur.fetchall()]

        missing = [t for t in EXPECTED_TABLES if t not in tables]

        meta: Dict[str, str] = {}
        if "system_meta" in tables:
            cur.execute("SELECT key, value FROM system_meta;")
            for r in cur.fetchall():
                meta[r["key"]] = r["value"]

        valid = len(missing) == 0 and journal_mode == "wal"

        return {
            "exists": True,
            "tables": tables,
            "missing_tables": missing,
            "journal_mode": journal_mode,
            "meta": meta,
            "valid": valid,
        }
    finally:
        conn.close()
