"""Explicit, reversible registration and probing for local third-party stdio MCPs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ingestion import TraceIngestor
from .models import new_id, utc_now
from .observers.mcp.stdio import StdioObserver
from .observers.mcp.common import McpTraceRecorder
from .quality import digest
from .redaction import RedactionPolicy


_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SECRET = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|authorization|private[_-]?key)")
_DANGEROUS = re.compile(r"(?i)(send|delete|remove|write|update|publish|短信|ssh)")


@dataclass(frozen=True)
class ThirdPartyMcpRegistration:
    name: str
    scope: str
    transport: str
    command: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    identity_hash: str
    status: str
    owner: str
    manifest_path: str
    created_at: str
    updated_at: str

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["command"] = list(self.command)
        if redact:
            value["env"] = {key: "<redacted>" for key in self.env}
        return value

    def to_storage_dict(self) -> dict[str, Any]:
        return self.to_dict(redact=False)


class ThirdPartyMcpManager:
    """Own only manifests created by this manager; never auto-replace a target."""

    owner = "shutu-workshop-m73"

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.root = self.workspace / ".skill_workshop" / "mcp-manifests"
        self.policy = RedactionPolicy()

    def list(self) -> list[ThirdPartyMcpRegistration]:
        if not self.root.is_dir():
            return []
        values: list[ThirdPartyMcpRegistration] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                value = self._read(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            values.append(value)
        return values

    def host_list(self, *, codex_command: str = "codex", codex_home: str | Path | None = None) -> dict[str, Any]:
        """Read host registrations without returning env values or headers."""

        environment = os.environ.copy()
        if codex_home:
            environment["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
        try:
            completed = subprocess.run(
                [*self._codex_argv(codex_command), "mcp", "list", "--json"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "BLOCKED_ENV", "error_type": type(exc).__name__, "servers": []}
        if completed.returncode != 0:
            return {"status": "BLOCKED_ENV", "error_type": "host_inventory_failed", "servers": []}
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"status": "BLOCKED_ENV", "error_type": "host_inventory_not_json", "servers": []}
        if not isinstance(raw, list):
            return {"status": "BLOCKED_ENV", "error_type": "host_inventory_invalid", "servers": []}
        servers = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            transport = item.get("transport") if isinstance(item.get("transport"), dict) else {}
            servers.append(
                {
                    "name": str(item.get("name") or ""),
                    "enabled": bool(item.get("enabled", False)),
                    "transport": str(transport.get("type") or "unknown"),
                    "command": str(transport.get("command") or ""),
                    "args": [str(value) for value in transport.get("args", [])] if isinstance(transport.get("args"), list) else [],
                    "cwd": str(transport.get("cwd") or ""),
                    "env_keys": sorted(str(key) for key in (transport.get("env") or {}).keys()) if isinstance(transport.get("env"), dict) else [],
                    "env_vars": sorted(str(value) for value in transport.get("env_vars", [])) if isinstance(transport.get("env_vars"), list) else [],
                    "has_url": bool(transport.get("url")),
                }
            )
        return {"status": "PASS", "servers": servers, "count": len(servers)}

    @staticmethod
    def _codex_argv(command: str) -> list[str]:
        if os.name == "nt":
            resolved = shutil.which(command) or shutil.which(command + ".ps1")
            if resolved and resolved.casefold().endswith(".ps1"):
                return ["powershell", "-NoProfile", "-File", resolved]
            if resolved:
                return [resolved]
        return [command]

    def inspect(self, name: str) -> ThirdPartyMcpRegistration | None:
        path = self._path(name)
        if not path.is_file():
            return None
        return self._read(path)

    def enable(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        scope: str = "project",
        transport: str = "stdio",
    ) -> ThirdPartyMcpRegistration:
        self._validate_name(name)
        if scope not in {"project", "user"}:
            raise ValueError("scope must be project or user")
        if transport != "stdio":
            raise ValueError("UNSUPPORTED: only stdio MCP can be transparently observed")
        command_tuple = tuple(str(item) for item in command if str(item))
        if not command_tuple:
            raise ValueError("MCP command is required")
        self._reject_owned_or_recursive(name, command_tuple)
        explicit_env = {str(key): str(value) for key, value in (env or {}).items()}
        if any(_SECRET.search(key) for key in explicit_env):
            raise ValueError("sensitive environment variables cannot be persisted in an M7.3 manifest")
        if any(_SECRET.search(value) for value in command_tuple):
            raise ValueError("sensitive command arguments cannot be persisted in an M7.3 manifest")
        target_cwd = str(Path(cwd).expanduser().resolve()) if cwd else ""
        identity = digest({"name": name, "scope": scope, "transport": transport, "command": command_tuple, "cwd": target_cwd, "env": explicit_env})
        path = self._path(name)
        current = self.inspect(name)
        if current is not None:
            if current.identity_hash != identity:
                raise ValueError("CONFLICT: refusing to overwrite a non-identical owned manifest")
            return current
        now = utc_now()
        value = ThirdPartyMcpRegistration(name, scope, transport, command_tuple, target_cwd, explicit_env, identity, "enabled", self.owner, str(path), now, now)
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value.to_storage_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return value

    def disable(self, name: str) -> ThirdPartyMcpRegistration:
        current = self.inspect(name)
        if current is None:
            raise ValueError("NOT_FOUND: third-party MCP manifest does not exist")
        if current.owner != self.owner:
            raise ValueError("CONFLICT: manifest is not owned by M7.3")
        value = ThirdPartyMcpRegistration(**{**current.to_storage_dict(), "command": tuple(current.command), "status": "disabled", "updated_at": utc_now()})
        self._path(name).write_text(json.dumps(value.to_storage_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return value

    def verify(self, name: str) -> dict[str, Any]:
        current = self.inspect(name)
        if current is None:
            return {"status": "NOT_FOUND", "name": name, "transport": "unknown"}
        executable = shutil.which(current.command[0]) if current.command else None
        if Path(current.command[0]).is_file():
            executable = current.command[0]
        checks = {
            "owned": current.owner == self.owner,
            "stdio": current.transport == "stdio",
            "not_recursive": not self._looks_recursive(current.name, current.command),
            "identity": current.identity_hash == digest({"name": current.name, "scope": current.scope, "transport": current.transport, "command": current.command, "cwd": current.cwd, "env": current.env}),
            "command_resolvable": bool(executable),
            "enabled": current.status == "enabled",
        }
        structural = all(value for key, value in checks.items() if key != "enabled")
        return {"status": "PASS" if structural else "FAIL", "active": checks["enabled"], "name": name, "checks": checks, "registration": current.to_dict()}

    def run_probe(
        self,
        name: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        workspace: str | Path | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        current = self.inspect(name)
        if current is None or current.status != "enabled":
            return {"status": "BLOCKED_ENV", "error_type": "mcp_not_enabled", "name": name}
        for message in messages:
            params = message.get("params") if isinstance(message.get("params"), Mapping) else {}
            tool_name = str(params.get("name") or "")
            if _DANGEROUS.search(tool_name):
                return {"status": "BLOCKED_ENV", "error_type": "side_effecting_probe_rejected", "name": name}
        target_workspace = Path(workspace or self.workspace).expanduser().resolve()
        recorder = McpTraceRecorder(
            TraceIngestor(target_workspace),
            workspace_id="m73-third-party",
            server_name=current.name,
            transport="stdio",
            adapter_id="m73-third-party-observer",
        )
        payload = b"".join((json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8") for message in messages)
        output, report = StdioObserver(current.command, recorder=recorder, cwd=current.cwd or None, env=current.env, timeout=timeout).proxy_bytes(payload)
        return {"status": "PASS" if not report.errors and report.failed_observations == 0 else "PARTIAL", "name": name, "trace_id": report.trace_id, "report": report.to_dict(), "forwarded_bytes": len(payload), "observed_output_bytes": len(output)}

    def _path(self, name: str) -> Path:
        self._validate_name(name)
        return self.root / f"{name}.json"

    def _read(self, path: Path) -> ThirdPartyMcpRegistration:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest must be an object")
        value["command"] = tuple(str(item) for item in value.get("command", []))
        value["env"] = {str(key): str(item) for key, item in dict(value.get("env", {})).items()}
        return ThirdPartyMcpRegistration(**value)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _NAME.fullmatch(name or "") or name.casefold().startswith("workshop"):
            raise ValueError("invalid or reserved MCP name")

    def _reject_owned_or_recursive(self, name: str, command: Sequence[str]) -> None:
        if name.casefold() in {"shutu-workshop", "shutu-skill-workshop"}:
            raise ValueError("reserved Workshop MCP name")
        if self._looks_recursive(name, command):
            raise ValueError("recursive or self-wrapping Observer command is not allowed")

    @staticmethod
    def _looks_recursive(name: str, command: Sequence[str]) -> bool:
        text = " ".join(str(item).casefold() for item in command)
        return "m72_stdio_observer" in text or "m73" in text and "observer" in text or name.casefold() in text


__all__ = ["ThirdPartyMcpManager", "ThirdPartyMcpRegistration"]
