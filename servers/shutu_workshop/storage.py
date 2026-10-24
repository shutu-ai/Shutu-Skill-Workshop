"""Additive SQLite storage shared by the M1 registry and trace store."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def database_path(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.suffix.casefold() == ".db":
        return path
    if path.name == ".skill_workshop":
        return path / "state.db"
    return path / ".skill_workshop" / "state.db"


class M1Database:
    """Open a workspace DB and apply only the M0-compatible M1 migrations."""

    latest_schema_version = 5

    def __init__(self, workspace_or_db: str | Path) -> None:
        self.db_path = database_path(workspace_or_db)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row[0])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                self._create_legacy_schema(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (1, datetime('now'))"
                )
            if 2 not in applied:
                self._create_m1_schema(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'))"
                )
            if 3 not in applied:
                self._create_m2_compat_schema(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (3, datetime('now'))"
                )
            if 4 not in applied:
                self._create_m3_diagnostics_schema(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (4, datetime('now'))"
                )
            if 5 not in applied:
                self._create_m4_consistency_schema(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (5, datetime('now'))"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _create_legacy_schema(connection: sqlite3.Connection) -> None:
        """Create the small M0 shape only when an actually empty DB is opened."""

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

    @staticmethod
    def _create_m1_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS capabilities (
                capability_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                locator TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL,
                discovered_from TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS capability_versions (
                version_id TEXT PRIMARY KEY,
                capability_id TEXT NOT NULL REFERENCES capabilities(capability_id),
                parent_version_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                schema_hash TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                provider_version TEXT NOT NULL DEFAULT 'unknown',
                observed_at TEXT NOT NULL,
                environment_fingerprint TEXT NOT NULL DEFAULT '',
                snapshot_ref TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                adapter_version TEXT NOT NULL DEFAULT 'unknown',
                parent_trace_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                environment_fingerprint TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                observation_completeness TEXT NOT NULL DEFAULT 'unknown',
                redaction_policy_version TEXT NOT NULL,
                token_summary_json TEXT NOT NULL,
                cost_summary_json TEXT NOT NULL,
                task_result TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                span_id TEXT NOT NULL DEFAULT '',
                parent_span_id TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL,
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                duration_ms INTEGER,
                capability_id TEXT NOT NULL DEFAULT '',
                capability_version_id TEXT NOT NULL DEFAULT '',
                adapter_event_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL DEFAULT '',
                transport TEXT NOT NULL DEFAULT '',
                observation_source TEXT NOT NULL DEFAULT '',
                retry_of_event_id TEXT NOT NULL DEFAULT '',
                attempt_index INTEGER,
                status TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                correlation_quality TEXT NOT NULL DEFAULT 'unknown',
                payload_json TEXT NOT NULL,
                source_metadata_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                UNIQUE(trace_id, seq)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_capability_versions_capability ON capability_versions(capability_id, observed_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(adapter_id, started_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_events_trace_seq ON trace_events(trace_id, seq)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_events_time ON trace_events(occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_events_capability ON trace_events(capability_id, occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_events_error ON trace_events(error_type, occurred_at)"
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trace_events_source
                ON trace_events(trace_id, adapter_event_id)
                WHERE adapter_event_id <> ''
            """
        )

    @staticmethod
    def _create_m2_compat_schema(connection: sqlite3.Connection) -> None:
        """Add the M2 evidence fields without rebuilding or deleting M1 data."""

        trace_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(traces)")
        }
        if "adapter_version" not in trace_columns:
            connection.execute(
                "ALTER TABLE traces ADD COLUMN adapter_version TEXT NOT NULL DEFAULT 'unknown'"
            )

        event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(trace_events)")
        }
        for name in ("request_id", "tool_name", "transport", "observation_source"):
            if name not in event_columns:
                connection.execute(
                    f"ALTER TABLE trace_events ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )

        # M1 did not have evidence to justify these two optimistic values.
        connection.execute(
            "UPDATE traces SET observation_completeness = 'unknown' "
            "WHERE observation_completeness IN ('', 'complete')"
        )
        connection.execute(
            "UPDATE trace_events SET correlation_quality = 'unknown' "
            "WHERE correlation_quality IN ('', 'observed')"
        )

    @staticmethod
    def _create_m3_diagnostics_schema(connection: sqlite3.Connection) -> None:
        """Add the diagnosis ledger without changing the M0/M1/M2 tables."""

        for statement in (
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                detector_version TEXT NOT NULL,
                ruleset_fingerprint TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                trace_count INTEGER NOT NULL DEFAULT 0,
                findings_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                errors_summary TEXT NOT NULL DEFAULT '',
                environment_fingerprint TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS issue_clusters (
                issue_id TEXT PRIMARY KEY,
                dedup_key TEXT NOT NULL UNIQUE,
                rule_id TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                category TEXT NOT NULL,
                normalized_signature TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                primary_capability_id TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                occurrence_count INTEGER NOT NULL DEFAULT 0,
                confidence_grade TEXT NOT NULL DEFAULT 'unknown',
                primary_owner_hypothesis TEXT NOT NULL,
                attribution_state TEXT NOT NULL DEFAULT 'hypothesis',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                row_version INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS issue_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL REFERENCES issue_clusters(issue_id) ON DELETE CASCADE,
                finding_fingerprint TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                observed_at TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                capability_id TEXT NOT NULL DEFAULT '',
                capability_version_id TEXT NOT NULL DEFAULT '',
                evidence_refs_json TEXT NOT NULL,
                source_scope_json TEXT NOT NULL,
                error_fingerprint TEXT NOT NULL DEFAULT '',
                owner_candidates_json TEXT NOT NULL,
                unknown_fields_json TEXT NOT NULL,
                evidence_quality TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS analysis_checkpoints (
                trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                ruleset_fingerprint TEXT NOT NULL,
                trace_revision_fingerprint TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
                status TEXT NOT NULL,
                PRIMARY KEY(trace_id, ruleset_fingerprint)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS issue_status_history (
                history_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL REFERENCES issue_clusters(issue_id) ON DELETE CASCADE,
                old_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                old_row_version INTEGER NOT NULL,
                new_row_version INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_runs_started
                ON analysis_runs(started_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_clusters_status
                ON issue_clusters(workspace_id, status, updated_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_clusters_rule
                ON issue_clusters(rule_id, category, last_seen_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_occurrences_issue
                ON issue_occurrences(issue_id, observed_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_occurrences_trace
                ON issue_occurrences(trace_id, observed_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_occurrences_capability
                ON issue_occurrences(capability_id, capability_version_id);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_checkpoints_status
                ON analysis_checkpoints(status, analyzed_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_issue_status_history_issue
                ON issue_status_history(issue_id, changed_at);
            """,
        ):
            connection.execute(statement)

    @staticmethod
    def _create_m4_consistency_schema(connection: sqlite3.Connection) -> None:
        """Keep ruleset evidence isolated and retain empty issue audit history."""

        cluster_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(issue_clusters)")
        }
        if "ruleset_fingerprint" not in cluster_columns:
            connection.execute(
                "ALTER TABLE issue_clusters ADD COLUMN ruleset_fingerprint TEXT NOT NULL DEFAULT ''"
            )
        if "archived_at" not in cluster_columns:
            connection.execute(
                "ALTER TABLE issue_clusters ADD COLUMN archived_at TEXT NOT NULL DEFAULT ''"
            )

        occurrence_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(issue_occurrences)")
        }
        if "ruleset_fingerprint" not in occurrence_columns:
            connection.execute(
                "ALTER TABLE issue_occurrences ADD COLUMN ruleset_fingerprint TEXT NOT NULL DEFAULT ''"
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_cursors (
                ruleset_fingerprint TEXT PRIMARY KEY,
                last_rowid INTEGER NOT NULL DEFAULT 0,
                last_started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_issue_clusters_ruleset ON issue_clusters(ruleset_fingerprint, archived_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_issue_occurrences_ruleset_trace ON issue_occurrences(ruleset_fingerprint, trace_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_cursors_updated ON analysis_cursors(updated_at)"
        )
