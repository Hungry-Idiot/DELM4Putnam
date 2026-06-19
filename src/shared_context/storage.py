from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared_context.schema import SharedNote
from src.shared_context.verifier import validate_note


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                problem_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                target_seq INTEGER NULL,
                attempt_path TEXT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def append_note(
    db_path: Path, note: SharedNote, writer_role: str = "agent"
) -> SharedNote:
    is_valid, reason = validate_note(note, writer_role=writer_role)
    if not is_valid:
        raise ValueError(f"Invalid shared context note: {reason}")

    init_db(db_path)
    created_at = _utc_now()
    metadata_json = json.dumps(note.metadata, ensure_ascii=False, sort_keys=True)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO notes (
                problem_id,
                worker_id,
                type,
                content,
                target_seq,
                attempt_path,
                status,
                metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note.problem_id,
                note.worker_id,
                note.type,
                note.content,
                note.target_seq,
                note.attempt_path,
                note.status,
                metadata_json,
                created_at,
            ),
        )
        seq = int(cursor.lastrowid)

    return replace(note, seq=seq, created_at=created_at)


def get_notes(
    db_path: Path, since_seq: int = 0, problem_id: str | None = None
) -> list[SharedNote]:
    init_db(db_path)
    params: list[Any] = [since_seq]
    where = "seq > ?"
    if problem_id is not None:
        where += " AND problem_id = ?"
        params.append(problem_id)

    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                seq,
                problem_id,
                worker_id,
                type,
                content,
                target_seq,
                attempt_path,
                status,
                metadata_json,
                created_at
            FROM notes
            WHERE {where}
            ORDER BY seq ASC
            """,
            params,
        ).fetchall()

    return [_row_to_note(row) for row in rows]


def get_latest_seq(db_path: Path) -> int:
    init_db(db_path)
    with _connect(db_path) as conn:
        value = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM notes").fetchone()[0]
    return int(value)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _row_to_note(row: sqlite3.Row | tuple[Any, ...]) -> SharedNote:
    metadata = json.loads(row[8]) if row[8] else {}
    return SharedNote(
        seq=row[0],
        problem_id=row[1],
        worker_id=row[2],
        type=row[3],
        content=row[4],
        target_seq=row[5],
        attempt_path=row[6],
        status=row[7],
        metadata=metadata,
        created_at=row[9],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
