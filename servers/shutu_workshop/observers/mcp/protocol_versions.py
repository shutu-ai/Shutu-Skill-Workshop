"""MCP era detection without assuming one handshake for every version."""

from __future__ import annotations

from typing import Any, Mapping

LEGACY_PROTOCOL = "2025-11-25"
MODERN_PROTOCOL = "2026-07-28"
LEGACY = "legacy"
MODERN = "modern"
UNKNOWN = "unknown"


def detect_era(message: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> str:
    message = message or {}
    lowered = {str(key).casefold(): str(value) for key, value in (headers or {}).items()}
    version = lowered.get("mcp-protocol-version", "") or str(message.get("protocolVersion", ""))
    method = str(message.get("method", ""))
    if version == MODERN or method == "server/discover" or "mcp-method" in lowered:
        return MODERN
    if version or method == "initialize" or "mcp-session-id" in lowered:
        return LEGACY
    return UNKNOWN


def protocol_label(era: str, version: str = "") -> str:
    if version:
        return version
    if era == LEGACY:
        return LEGACY_PROTOCOL
    if era == MODERN:
        return MODERN_PROTOCOL
    return "unknown"
