"""Small SQLite persistence layer for the Skill Workshop replica."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import EvolutionIssue, Proposal, Version


class WorkshopStore:
    """Persist workshop state below one workspace-owned directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "state.db"
        self.staging_root = self.root / "staging"
        self.versions_root = self.root / "versions"
        self.staging_root.mkdir(exist_ok=True)
        self.versions_root.mkdir(exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_issues_skill
                    ON issues(skill_name, created_at);
                CREATE INDEX IF NOT EXISTS idx_proposals_skill
                    ON proposals(skill_name, updated_at);
                CREATE INDEX IF NOT EXISTS idx_versions_skill
                    ON versions(skill_name, created_at);
                """
            )

    def save_issue(self, issue: EvolutionIssue) -> EvolutionIssue:
        payload = json.dumps(issue.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
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
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [EvolutionIssue(**json.loads(row[0])) for row in rows]

    def save_proposal(self, proposal: Proposal) -> Proposal:
        payload = json.dumps(proposal.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proposals(
                    id, skill_name, status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    proposal.id,
                    proposal.skill_name,
                    proposal.status,
                    proposal.created_at,
                    proposal.updated_at,
                    payload,
                ),
            )
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return Proposal.from_dict(json.loads(row[0])) if row else None

    def list_proposals(self, skill_name: str | None = None) -> list[Proposal]:
        query = "SELECT payload_json FROM proposals"
        parameters: tuple[Any, ...] = ()
        if skill_name:
            query += " WHERE skill_name = ?"
            parameters = (skill_name,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Proposal.from_dict(json.loads(row[0])) for row in rows]

    def save_version(self, version: Version) -> Version:
        payload = json.dumps(version.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        return Version.from_dict(json.loads(row[0])) if row else None

    def list_versions(self, skill_name: str) -> list[Version]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM versions
                WHERE skill_name = ?
                ORDER BY created_at ASC
                """,
                (skill_name,),
            ).fetchall()
        return [Version.from_dict(json.loads(row[0])) for row in rows]
