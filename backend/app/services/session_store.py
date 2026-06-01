from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import get_root_dir


class SessionStore:
    def __init__(self) -> None:
        self.db_path = (get_root_dir() / ".storage" / "chat_sessions.sqlite3").resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    app_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_prompt TEXT NOT NULL DEFAULT '',
                    last_message_preview TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS apps (
                    session_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL UNIQUE,
                    entries_json TEXT NOT NULL DEFAULT '[]',
                    files_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            # Migration: add generation_duration_ms if it doesn't exist yet
            try:
                connection.execute("ALTER TABLE sessions ADD COLUMN generation_duration_ms INTEGER")
            except sqlite3.OperationalError:
                pass  # column already exists

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _title_from_prompt(self, prompt: str | None) -> str:
        if not prompt:
            return "New Build Session"
        normalized = " ".join(prompt.split())
        if len(normalized) <= 48:
            return normalized
        return f"{normalized[:45].rstrip()}..."

    def _session_summary(self, row: sqlite3.Row) -> dict[str, object]:
        keys = row.keys()
        return {
            "id": row["id"],
            "title": row["title"],
            "app_id": row["app_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_prompt": row["last_prompt"],
            "last_message_preview": row["last_message_preview"],
            "message_count": row["message_count"],
            "generation_duration_ms": row["generation_duration_ms"] if "generation_duration_ms" in keys else None,
        }

    def create_session(self, first_prompt: str | None = None) -> dict[str, object]:
        session_id = uuid4().hex[:12]
        app_id = f"app_{session_id}"
        now = self._now()
        title = self._title_from_prompt(first_prompt)
        last_prompt = " ".join(first_prompt.split()) if first_prompt else ""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, title, app_id, created_at, updated_at, last_prompt, last_message_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, title, app_id, now, now, last_prompt, last_prompt),
            )
            connection.execute(
                """
                INSERT INTO apps (session_id, app_id, entries_json, files_json, updated_at)
                VALUES (?, ?, '[]', '[]', ?)
                """,
                (session_id, app_id, now),
            )
        return self.get_session(session_id)

    def list_sessions(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.app_id,
                    s.created_at,
                    s.updated_at,
                    s.last_prompt,
                    s.last_message_preview,
                    COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.created_at DESC
                """
            ).fetchall()
        return [self._session_summary(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.app_id,
                    s.created_at,
                    s.updated_at,
                    s.last_prompt,
                    s.last_message_preview,
                    COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return self._session_summary(row)

    def add_message(self, session_id: str, role: str, agent: str, content: str) -> dict[str, object]:
        now = self._now()
        preview = " ".join(content.split())

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, agent, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, agent, content, now),
            )
            if role == "user":
                connection.execute(
                    """
                    UPDATE sessions
                    SET updated_at = ?, last_prompt = ?, last_message_preview = ?
                    WHERE id = ?
                    """,
                    (now, preview, preview, session_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE sessions
                    SET updated_at = ?, last_message_preview = ?
                    WHERE id = ?
                    """,
                    (now, preview, session_id),
                )
        return {
            "id": cursor.lastrowid,
            "role": role,
            "agent": agent,
            "content": content,
            "created_at": now,
        }

    def save_generation_time(self, session_id: str, duration_ms: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET generation_duration_ms = ? WHERE id = ?",
                (duration_ms, session_id),
            )

    def save_workspace(
        self,
        session_id: str,
        app_id: str,
        entries: list[dict[str, object]],
        files: list[dict[str, object]],
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO apps (session_id, app_id, entries_json, files_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    app_id = excluded.app_id,
                    entries_json = excluded.entries_json,
                    files_json = excluded.files_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, app_id, json.dumps(entries), json.dumps(files), now),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )

    def add_trace_event(self, session_id: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trace_events (session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, event_type, json.dumps(payload), now),
            )
        return {
            "id": cursor.lastrowid,
            "type": event_type,
            "payload": payload,
            "created_at": now,
        }

    def get_workspace_for_session(self, session_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT app_id, entries_json, files_json
                FROM apps
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            session = self.get_session(session_id)
            return {
                "app_id": session["app_id"],
                "entries": [],
                "files": [],
            }
        return {
            "app_id": row["app_id"],
            "entries": json.loads(row["entries_json"]),
            "files": json.loads(row["files_json"]),
        }

    def get_workspace_for_app(self, app_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT app_id, entries_json, files_json
                FROM apps
                WHERE app_id = ?
                """,
                (app_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "app_id": row["app_id"],
            "entries": json.loads(row["entries_json"]),
            "files": json.loads(row["files_json"]),
        }

    def get_session_detail(self, session_id: str) -> dict[str, object]:
        session = self.get_session(session_id)
        workspace = self.get_workspace_for_session(session_id)

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, role, agent, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
            trace_rows = connection.execute(
                """
                SELECT event_type, payload_json, created_at
                FROM trace_events
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        return {
            "session": session,
            "messages": [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "agent": row["agent"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
            "trace_events": [
                {
                    "type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in trace_rows
            ],
            "workspace": workspace,
        }
