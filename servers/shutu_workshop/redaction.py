"""Capture-time redaction used by every M1 write path."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import PayloadLimitError


class RedactionPolicy:
    """Small deterministic policy; it never stores the original value in errors."""

    version = "m1-default-v1"

    _secret_keys = {
        "authorization",
        "cookie",
        "set-cookie",
        "bearer",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "private_key",
    }
    _summary_keys = {
        "prompt",
        "user_prompt",
        "system_prompt",
        "file_content",
        "file_contents",
        "environment",
        "environment_variables",
        "env",
    }
    _bearer = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
    _header_credential = re.compile(
        r"(?i)\b(?:authorization|cookie|x-api-key)\s*:\s*[^\r\n]+"
    )
    _credential_assignment = re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+"
    )
    _personal_path = re.compile(
        r"(?i)(?:[A-Z]:[\\/](?:users|documents and settings)[\\/][^\"'\r\n]+|/(?:users|home)/[^\"'\r\n]+|/mnt/[a-z]/users/[^\"'\r\n]+)"
    )

    def __init__(
        self,
        *,
        max_payload_bytes: int = 64 * 1024,
        max_string_length: int = 4096,
    ) -> None:
        if max_payload_bytes < 256 or max_string_length < 64:
            raise ValueError("redaction limits are too small")
        self.max_payload_bytes = max_payload_bytes
        self.max_string_length = max_string_length

    @staticmethod
    def _summary(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"<redacted len={len(encoded)} sha256={digest}>"

    def _redact_string(self, value: str) -> str:
        value = self._bearer.sub("Bearer <redacted>", value)
        value = self._header_credential.sub("<redacted-header>", value)
        value = self._credential_assignment.sub(r"\1<redacted>", value)
        value = self._personal_path.sub("<user-path>", value)
        if len(value) > self.max_string_length:
            return self._summary(value)
        return value

    def redact(self, value: Any, *, key: str = "") -> Any:
        key_name = key.casefold().replace("-", "_")
        if key_name in self._secret_keys:
            return "<redacted>"
        if key_name in self._summary_keys:
            return self._summary(value)
        if isinstance(value, dict):
            return {str(name): self.redact(item, key=str(name)) for name, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self._redact_string(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._summary(value)

    def redact_payload(self, payload: Any) -> Any:
        redacted = self.redact(payload)
        try:
            encoded = json.dumps(
                redacted,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise PayloadLimitError("payload is not valid JSON") from exc
        if len(encoded.encode("utf-8")) > self.max_payload_bytes:
            raise PayloadLimitError("payload exceeds the configured limit")
        return redacted

    def redact_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        value = self.redact_payload(metadata or {})
        if not isinstance(value, dict):
            raise PayloadLimitError("metadata must be a JSON object")
        return value
