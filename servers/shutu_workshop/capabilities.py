"""Capability identity and immutable version evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from .models import Capability, CapabilityVersion, ConflictError, ValidationError, new_id, utc_now
from .redaction import RedactionPolicy
from .storage import M1Database


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CapabilityRegistry:
    def __init__(
        self,
        workspace_or_db: str | M1Database,
        *,
        policy: RedactionPolicy | None = None,
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.policy = policy or RedactionPolicy()

    def register(self, capability: Capability) -> Capability:
        if not capability.capability_id or not capability.adapter_id:
            raise ValidationError("capability_id and adapter_id are required")
        if capability.kind not in Capability.KINDS:
            raise ValidationError("unsupported capability kind")
        stored = replace(
            capability,
            metadata=self.policy.redact_metadata(capability.metadata),
        )
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capabilities WHERE capability_id = ?",
                (stored.capability_id,),
            ).fetchone()
            if row:
                existing = self._from_row(row)
                identity = ("kind", "adapter_id", "locator", "metadata", "discovered_from")
                if any(getattr(existing, name) != getattr(stored, name) for name in identity):
                    raise ConflictError("capability identity conflict")
                return existing
            connection.execute(
                """
                INSERT INTO capabilities(
                    capability_id, kind, adapter_id, locator, metadata_json,
                    discovered_from, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.capability_id,
                    stored.kind,
                    stored.adapter_id,
                    stored.locator,
                    _canonical(stored.metadata),
                    stored.discovered_from,
                    stored.discovered_at,
                ),
            )
        return stored

    def get(self, capability_id: str) -> Capability | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM capabilities WHERE capability_id = ?",
                (capability_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    get_capability = get

    def list(
        self,
        *,
        kind: str | None = None,
        adapter_id: str | None = None,
    ) -> list[Capability]:
        query = "SELECT * FROM capabilities"
        values: list[str] = []
        conditions: list[str] = []
        if kind:
            conditions.append("kind = ?")
            values.append(kind)
        if adapter_id:
            conditions.append("adapter_id = ?")
            values.append(adapter_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY capability_id"
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._from_row(row) for row in rows]

    list_capabilities = list

    def register_version(self, version: CapabilityVersion) -> CapabilityVersion:
        if not version.version_id or not version.capability_id or not version.source:
            raise ValidationError("version_id, capability_id and source are required")
        if self.get(version.capability_id) is None:
            raise ValidationError("capability must be registered before its version")
        stored = replace(
            version,
            provider_version=version.provider_version or "unknown",
            metadata=self.policy.redact_metadata(version.metadata),
        )
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capability_versions WHERE version_id = ?",
                (stored.version_id,),
            ).fetchone()
            if row:
                existing = self._version_from_row(row)
                if existing != stored:
                    raise ConflictError("capability version conflict")
                return existing
            connection.execute(
                """
                INSERT INTO capability_versions(
                    version_id, capability_id, parent_version_id, content_hash,
                    schema_hash, source, provider_version, observed_at,
                    environment_fingerprint, snapshot_ref, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.version_id,
                    stored.capability_id,
                    stored.parent_version_id,
                    stored.content_hash,
                    stored.schema_hash,
                    stored.source,
                    stored.provider_version,
                    stored.observed_at,
                    stored.environment_fingerprint,
                    stored.snapshot_ref,
                    _canonical(stored.metadata),
                ),
            )
        return stored

    def snapshot(
        self,
        capability: Capability | str,
        *,
        source: str = "snapshot",
        version_id: str = "",
        parent_version_id: str = "",
        content_hash: str = "",
        schema_hash: str = "",
        provider_version: str = "unknown",
        observed_at: str = "",
        environment_fingerprint: str = "",
        snapshot_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CapabilityVersion:
        if isinstance(capability, Capability):
            self.register(capability)
            capability_id = capability.capability_id
        else:
            capability_id = capability
        version = CapabilityVersion(
            capability_id=capability_id,
            source=source,
            version_id=version_id or new_id("capver"),
            parent_version_id=parent_version_id,
            content_hash=content_hash,
            schema_hash=schema_hash,
            provider_version=provider_version or "unknown",
            observed_at=observed_at or utc_now(),
            environment_fingerprint=environment_fingerprint,
            snapshot_ref=snapshot_ref,
            metadata=metadata or {},
        )
        return self.register_version(version)

    register_snapshot = snapshot

    def get_version(self, version_id: str) -> CapabilityVersion | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM capability_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def list_versions(self, capability_id: str) -> list[CapabilityVersion]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM capability_versions
                WHERE capability_id = ?
                ORDER BY observed_at ASC, rowid ASC
                """,
                (capability_id,),
            ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def resolve(
        self,
        capability_id: str,
        *,
        version_id: str = "",
        observed_at: str = "",
    ) -> CapabilityVersion | None:
        if version_id:
            version = self.get_version(version_id)
            if version and version.capability_id == capability_id:
                return version
            return None
        query = "SELECT * FROM capability_versions WHERE capability_id = ?"
        values: list[str] = [capability_id]
        if observed_at:
            query += " AND observed_at <= ?"
            values.append(observed_at)
        query += " ORDER BY observed_at DESC, rowid DESC LIMIT 1"
        with self.db.connection() as connection:
            row = connection.execute(query, values).fetchone()
        return self._version_from_row(row) if row else None

    def register_skill_version(
        self,
        skill_name: str,
        *,
        package_hash: str,
        version_id: str = "",
        parent_version_id: str = "",
        snapshot_ref: str = "",
        source: str = "m0-version",
        adapter_id: str = "skill-workshop",
    ) -> CapabilityVersion:
        capability = Capability(
            capability_id=f"skill:{skill_name}",
            kind="skill",
            adapter_id=adapter_id,
            locator=skill_name,
            metadata={"skill_name": skill_name},
            discovered_from="m0",
        )
        self.register(capability)
        return self.snapshot(
            capability.capability_id,
            source=source,
            version_id=version_id,
            parent_version_id=parent_version_id,
            content_hash=package_hash,
            snapshot_ref=snapshot_ref,
        )

    def register_mcp_server_snapshot(
        self,
        server_name: str,
        *,
        server_info: dict[str, Any] | None = None,
        protocol: str = "",
        transport: str = "",
        tools: list[dict[str, Any]] | None = None,
        version_id: str = "",
        provider_version: str = "",
        adapter_id: str = "mcp-simulated",
    ) -> CapabilityVersion:
        info = dict(server_info or {})
        listed_tools = list(tools or [])
        capability = Capability(
            capability_id=f"mcp-server:{server_name}",
            kind="mcp_server",
            adapter_id=adapter_id,
            locator=server_name,
            metadata={"server_info": info, "protocol": protocol, "transport": transport},
            discovered_from="mcp.tools_list.fixture",
        )
        self.register(capability)
        snapshot = {
            "server_info": info,
            "protocol": protocol,
            "transport": transport,
            "tools": listed_tools,
        }
        return self.snapshot(
            capability.capability_id,
            source="mcp_snapshot",
            version_id=version_id,
            content_hash=content_hash(snapshot),
            schema_hash=content_hash(listed_tools),
            provider_version=provider_version or str(info.get("version") or "unknown"),
            metadata=snapshot,
        )

    def register_mcp_tool_snapshot(
        self,
        server_name: str,
        tool: dict[str, Any],
        *,
        provider_version: str = "unknown",
        version_id: str = "",
        adapter_id: str = "mcp-simulated",
    ) -> CapabilityVersion:
        name = str(tool.get("name") or "")
        if not name:
            raise ValidationError("MCP tool name is required")
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        capability = Capability(
            capability_id=f"mcp-tool:{server_name}/{name}",
            kind="mcp_tool",
            adapter_id=adapter_id,
            locator=f"{server_name}/{name}",
            metadata={"name": name, "description": tool.get("description", "")},
            discovered_from="mcp.tools_list.fixture",
        )
        self.register(capability)
        return self.snapshot(
            capability.capability_id,
            source="mcp_tools_list",
            version_id=version_id,
            content_hash=content_hash(tool),
            schema_hash=content_hash(schema),
            provider_version=provider_version or "unknown",
            metadata={"name": name, "description": tool.get("description", ""), "inputSchema": schema},
        )

    @staticmethod
    def _from_row(row: Any) -> Capability:
        return Capability(
            capability_id=row["capability_id"],
            kind=row["kind"],
            adapter_id=row["adapter_id"],
            locator=row["locator"],
            metadata=json.loads(row["metadata_json"]),
            discovered_from=row["discovered_from"],
            discovered_at=row["discovered_at"],
        )

    @staticmethod
    def _version_from_row(row: Any) -> CapabilityVersion:
        return CapabilityVersion(
            version_id=row["version_id"],
            capability_id=row["capability_id"],
            parent_version_id=row["parent_version_id"],
            content_hash=row["content_hash"],
            schema_hash=row["schema_hash"],
            source=row["source"],
            provider_version=row["provider_version"],
            observed_at=row["observed_at"],
            environment_fingerprint=row["environment_fingerprint"],
            snapshot_ref=row["snapshot_ref"],
            metadata=json.loads(row["metadata_json"]),
        )
