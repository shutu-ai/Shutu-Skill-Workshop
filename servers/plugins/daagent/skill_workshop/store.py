"""SQLite persistence, locking, and durable release journaling."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .models import EvolutionIssue, Proposal, ReleaseOperation, Version, utc_now


class ConcurrentUpdateError(RuntimeError):
    """Raised when a persisted proposal changed since it was read."""


class WorkshopStore:
    """Persist workshop state below one workspace-owned directory."""

    _thread_lock_guard = threading.Lock()
    _thread_locks: dict[str, threading.RLock] = {}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "state.db"
        self.staging_root = self.root / "staging"
        self.versions_root = self.root / "versions"
        self.locks_root = self.root / "locks"
        self.staging_root.mkdir(exist_ok=True)
        self.versions_root.mkdir(exist_ok=True)
        self.locks_root.mkdir(exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    row_version INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            proposal_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(proposals)")
            }
            if "row_version" not in proposal_columns:
                connection.execute(
                    "ALTER TABLE proposals ADD COLUMN row_version INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS release_operations (
                    operation_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    target_skill_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_issues_skill ON issues(skill_name, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_proposals_skill ON proposals(skill_name, updated_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_versions_skill ON versions(skill_name, created_at)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_release_operations_status
                    ON release_operations(status, updated_at)
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utc_now()),
            )
            connection.commit()

    @classmethod
    def _get_thread_lock(cls, path: Path) -> threading.RLock:
        key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
        with cls._thread_lock_guard:
            lock = cls._thread_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._thread_locks[key] = lock
            return lock

    @contextmanager
    def release_lock(self, skill_names: Iterable[str] | str) -> Iterator[None]:
        """Serialize releases in-process and across Windows/POSIX processes."""

        if isinstance(skill_names, str):
            names = [skill_names]
        else:
            names = list(skill_names)
        lock_paths = [self.locks_root / "workspace.lock"]
        for name in sorted(set(names)):
            token = hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:24]
            lock_paths.append(self.locks_root / f"skill-{token}.lock")
        thread_locks = [self._get_thread_lock(path) for path in lock_paths]
        handles: list[Any] = []
        acquired_thread_locks: list[threading.RLock] = []
        try:
            for lock in thread_locks:
                lock.acquire()
                acquired_thread_locks.append(lock)
            for path in lock_paths:
                handle = path.open("a+b")
                try:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                handles.append(handle)
            yield
        finally:
            for handle in reversed(handles):
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            for lock in reversed(acquired_thread_locks):
                lock.release()

    def save_issue(self, issue: EvolutionIssue) -> EvolutionIssue:
        payload = json.dumps(issue.to_dict(), ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO issues(id, skill_name, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (issue.id, issue.skill_name, issue.created_at, payload),
            )
        return issue

    def list_issues(self, skill_name: str | None = None) -> list[EvolutionIssue]:
        query = "SELECT payload_json FROM issues"
        parameters: tuple[Any, ...] = ()
        if skill_name:
            query += " WHERE skill_name = ?"
            parameters = (skill_name,)
        query += " ORDER BY created_at DESC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [EvolutionIssue(**json.loads(row[0])) for row in rows]

    @staticmethod
    def _proposal_from_row(row: sqlite3.Row) -> Proposal:
        payload = json.loads(row["payload_json"])
        payload["row_version"] = int(row["row_version"])
        return Proposal.from_dict(payload)

    def save_proposal(self, proposal: Proposal) -> Proposal:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT row_version FROM proposals WHERE id = ?",
                (proposal.id,),
            ).fetchone()
            if row is None:
                proposal.row_version = 0
                payload = json.dumps(proposal.to_dict(), ensure_ascii=False)
                connection.execute(
                    """
                    INSERT INTO proposals(
                        id, skill_name, status, created_at, updated_at,
                        row_version, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal.id,
                        proposal.skill_name,
                        proposal.status,
                        proposal.created_at,
                        proposal.updated_at,
                        proposal.row_version,
                        payload,
                    ),
                )
                return proposal
            current_row_version = int(row["row_version"])
            if proposal.row_version != current_row_version:
                raise ConcurrentUpdateError(
                    f"Proposal changed concurrently: {proposal.id}"
                )
            proposal.row_version = current_row_version + 1
            payload = json.dumps(proposal.to_dict(), ensure_ascii=False)
            updated = connection.execute(
                """
                UPDATE proposals SET
                    status = ?, updated_at = ?, row_version = ?, payload_json = ?
                WHERE id = ? AND row_version = ?
                """,
                (
                    proposal.status,
                    proposal.updated_at,
                    proposal.row_version,
                    payload,
                    proposal.id,
                    current_row_version,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrentUpdateError(
                    f"Proposal changed concurrently: {proposal.id}"
                )
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json, row_version FROM proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._proposal_from_row(row) if row else None

    def list_proposals(self, skill_name: str | None = None) -> list[Proposal]:
        query = "SELECT payload_json, row_version FROM proposals"
        parameters: tuple[Any, ...] = ()
        if skill_name:
            query += " WHERE skill_name = ?"
            parameters = (skill_name,)
        query += " ORDER BY updated_at DESC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def save_version(self, version: Version) -> Version:
        payload = json.dumps(version.to_dict(), ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO versions(id, skill_name, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (version.id, version.skill_name, version.created_at, payload),
            )
        return version

    def get_version(self, version_id: str) -> Version | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        return Version.from_dict(json.loads(row[0])) if row else None

    def list_versions(self, skill_name: str) -> list[Version]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM versions
                WHERE skill_name = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (skill_name,),
            ).fetchall()
        return [Version.from_dict(json.loads(row[0])) for row in rows]

    def save_release_operation(self, operation: ReleaseOperation) -> ReleaseOperation:
        payload = json.dumps(operation.to_dict(), ensure_ascii=False)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO release_operations(
                    operation_id, kind, skill_name, target_skill_name, status,
                    created_at, updated_at, attempt_count, payload_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    kind=excluded.kind,
                    skill_name=excluded.skill_name,
                    target_skill_name=excluded.target_skill_name,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    attempt_count=excluded.attempt_count,
                    payload_json=excluded.payload_json,
                    error=excluded.error
                """,
                (
                    operation.operation_id,
                    operation.kind,
                    operation.skill_name,
                    operation.target_skill_name,
                    operation.status,
                    operation.created_at,
                    operation.updated_at,
                    operation.attempt_count,
                    payload,
                    operation.error,
                ),
            )
        return operation

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> ReleaseOperation:
        return ReleaseOperation.from_dict(json.loads(row["payload_json"]))

    def get_release_operation(self, operation_id: str) -> ReleaseOperation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM release_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._operation_from_row(row) if row else None

    def list_release_operations(
        self, status: str | None = None
    ) -> list[ReleaseOperation]:
        query = "SELECT payload_json FROM release_operations"
        parameters: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY created_at ASC, rowid ASC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._operation_from_row(row) for row in rows]
