from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiosqlite


@dataclass(frozen=True)
class SessionRow:
    id: int
    week_date: str
    status: str
    message_id: int | None
    created_at: str


@dataclass(frozen=True)
class AttendanceRow:
    id: int
    session_id: int
    user_id: int
    user_name: str
    attend_order: int
    attended_at: str


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week_date TEXT NOT NULL,
  status TEXT NOT NULL, -- 'active' | 'completed' | 'ended'
  message_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_week_date ON sessions(week_date);

CREATE TABLE IF NOT EXISTS attendances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  user_name TEXT NOT NULL,
  attend_order INTEGER NOT NULL,
  attended_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attendances_unique ON attendances(session_id, user_id);
CREATE INDEX IF NOT EXISTS idx_attendances_session_order ON attendances(session_id, attend_order);
"""


def _iso_now(dt: datetime | None = None) -> str:
    return (dt or datetime.utcnow()).replace(microsecond=0).isoformat() + "Z"


async def init_db(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def get_session_by_week_date(db_path: str, week_date: str) -> SessionRow | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, week_date, status, message_id, created_at FROM sessions WHERE week_date = ?",
            (week_date,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return SessionRow(
            id=row["id"],
            week_date=row["week_date"],
            status=row["status"],
            message_id=row["message_id"],
            created_at=row["created_at"],
        )


async def get_active_session(db_path: str) -> SessionRow | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, week_date, status, message_id, created_at FROM sessions WHERE status = 'active' LIMIT 1"
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return SessionRow(
            id=row["id"],
            week_date=row["week_date"],
            status=row["status"],
            message_id=row["message_id"],
            created_at=row["created_at"],
        )


async def upsert_session_active(
    db_path: str, week_date: str, message_id: int | None
) -> SessionRow:
    created_at = _iso_now()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO sessions(week_date, status, message_id, created_at)
            VALUES(?, 'active', ?, ?)
            ON CONFLICT(week_date) DO UPDATE SET
              status='active',
              message_id=excluded.message_id
            """,
            (week_date, message_id, created_at),
        )
        await db.commit()
    # re-read for id
    s = await get_session_by_week_date(db_path, week_date)
    assert s is not None
    return s


async def update_session_message_id(db_path: str, session_id: int, message_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE sessions SET message_id = ? WHERE id = ?", (message_id, session_id))
        await db.commit()


async def update_session_status(db_path: str, session_id: int, status: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
        await db.commit()


async def list_attendances(db_path: str, session_id: int) -> list[AttendanceRow]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, session_id, user_id, user_name, attend_order, attended_at
            FROM attendances
            WHERE session_id = ?
            ORDER BY attend_order ASC
            """,
            (session_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            AttendanceRow(
                id=r["id"],
                session_id=r["session_id"],
                user_id=r["user_id"],
                user_name=r["user_name"],
                attend_order=r["attend_order"],
                attended_at=r["attended_at"],
            )
            for r in rows
        ]


async def count_attendances(db_path: str, session_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM attendances WHERE session_id = ?", (session_id,))
        (n,) = await cur.fetchone()
        await cur.close()
        return int(n)


async def has_attended(db_path: str, session_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM attendances WHERE session_id = ? AND user_id = ? LIMIT 1",
            (session_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row is not None


async def insert_attendance_atomic(
    db_path: str,
    session_id: int,
    user_id: int,
    user_name: str,
    attended_at_iso: str | None = None,
) -> tuple[bool, int | None]:
    """
    Returns (inserted, attend_order).
    If already exists, inserted=False and attend_order=None.
    """
    attended_at = attended_at_iso or _iso_now()
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT 1 FROM attendances WHERE session_id = ? AND user_id = ? LIMIT 1",
                (session_id, user_id),
            )
            exists = await cur.fetchone()
            await cur.close()
            if exists:
                await db.execute("ROLLBACK")
                return False, None

            cur2 = await db.execute(
                "SELECT COALESCE(MAX(attend_order), 0) + 1 FROM attendances WHERE session_id = ?",
                (session_id,),
            )
            (next_order,) = await cur2.fetchone()
            await cur2.close()

            await db.execute(
                """
                INSERT INTO attendances(session_id, user_id, user_name, attend_order, attended_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (session_id, user_id, user_name, int(next_order), attended_at),
            )
            await db.commit()
            return True, int(next_order)
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def _debug_query(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows

