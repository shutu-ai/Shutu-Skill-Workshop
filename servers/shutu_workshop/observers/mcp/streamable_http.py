"""Explicit loopback Streamable HTTP MCP observer."""

from __future__ import annotations

import http.client
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

from .common import McpTraceRecorder, ObserverReport


@dataclass(frozen=True)
class HTTPProxyResult:
    status: int
    headers: dict[str, str]
    body: bytes
    report: ObserverReport


class StreamableHTTPObserver:
    def __init__(
        self,
        url: str,
        *,
        recorder: McpTraceRecorder | None = None,
        timeout: float = 30.0,
        chunk_size: int = 16 * 1024,
        max_observe_bytes: int = 64 * 1024,
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("MCP HTTP observer only accepts explicit loopback URLs")
        if not parsed.path:
            parsed = parsed._replace(path="/")
        if timeout <= 0 or chunk_size < 256 or max_observe_bytes < 256:
            raise ValueError("invalid HTTP observer limits")
        self.url = parsed.geturl()
        self._parsed = parsed
        self.recorder = recorder or McpTraceRecorder(transport="streamable-http")
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.max_observe_bytes = max_observe_bytes
        self._active: dict[str, http.client.HTTPConnection] = {}
        self._active_lock = threading.Lock()

    def request(
        self,
        body: bytes,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HTTPProxyResult:
        chunks: list[bytes] = []
        status = 0
        response_headers: dict[str, str] = {}
        report = self.recorder.report
        for status, response_headers, chunk in self.stream(
            body,
            method=method,
            headers=headers,
            timeout=timeout,
        ):
            chunks.append(chunk)
        return HTTPProxyResult(status, response_headers, b"".join(chunks), report)

    def stream(
        self,
        body: bytes,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Iterator[tuple[int, dict[str, str], bytes]]:
        headers = dict(headers or {})
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        self.recorder.start()
        request_message = self._json_body(body)
        if request_message is not None:
            self.recorder.observe_json("client_to_server", request_message, headers=headers)
        connection = self._connection(timeout or self.timeout)
        active_key = str(id(connection))
        with self._active_lock:
            self._active[active_key] = connection
        response: http.client.HTTPResponse | None = None
        parser = _HTTPMessageParser(
            "text/event-stream" if "text/event-stream" in headers.get("Accept", "") else ""
        )
        started = time.perf_counter()
        try:
            path = self._parsed.path or "/"
            if self._parsed.query:
                path += "?" + self._parsed.query
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = {key: value for key, value in response.getheaders()}
            content_type = response_headers.get("Content-Type", response_headers.get("content-type", ""))
            parser.content_type = content_type
            if response.status >= 400:
                self.recorder.observe_json(
                    "server_to_client",
                    {"jsonrpc": "2.0", "id": request_message.get("id") if request_message else "", "error": {"code": response.status}},
                    headers=response_headers,
                    status_code=response.status,
                )
            observed_bytes = 0
            yielded = False
            while True:
                chunk = response.read(self.chunk_size)
                if not chunk:
                    break
                yielded = True
                yield response.status, response_headers, chunk
                if observed_bytes < self.max_observe_bytes:
                    allowed = self.max_observe_bytes - observed_bytes
                    observed_bytes += min(allowed, len(chunk))
                    for message in parser.feed(chunk[:allowed]):
                        self.recorder.observe_json("server_to_client", message, headers=response_headers)
                if observed_bytes >= self.max_observe_bytes and not parser.truncated:
                    parser.truncated = True
            for message in parser.finish():
                self.recorder.observe_json("server_to_client", message, headers=response_headers)
            if not yielded:
                yield response.status, response_headers, b""
            if parser.truncated:
                self.recorder.report.failed_observations += 1
                self.recorder.report.errors.append("response_summary_truncated")
            self.recorder.report.observer_overhead_ms += max(0.0, (time.perf_counter() - started) * 1000)
        except (OSError, http.client.HTTPException) as exc:
            self.recorder.report.errors.append(type(exc).__name__)
            self.recorder.report.failed_observations += 1
            raise
        finally:
            with self._active_lock:
                self._active.pop(active_key, None)
            if response is not None:
                response.close()
            connection.close()

    def cancel(self, request_id: str = "") -> bool:
        """Close an active connection; never resubmits the request."""

        with self._active_lock:
            connections = list(self._active.values())
        if not connections:
            return False
        for connection in connections:
            connection.close()
        self.recorder.report.errors.append("client_cancelled")
        return True

    def close(self, *, status: str = "ok") -> ObserverReport:
        return self.recorder.close(status=status)

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        if self._parsed.scheme == "https":
            return http.client.HTTPSConnection(
                self._parsed.hostname,
                self._parsed.port or 443,
                timeout=timeout,
            )
        return http.client.HTTPConnection(
            self._parsed.hostname,
            self._parsed.port or 80,
            timeout=timeout,
        )

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


class _HTTPMessageParser:
    def __init__(self, content_type: str = "") -> None:
        self.content_type = content_type
        self.buffer = b""
        self.truncated = False

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self.buffer += chunk
        if "text/event-stream" in self.content_type.casefold():
            return self._sse_messages()
        return []

    def finish(self) -> list[dict[str, Any]]:
        if "text/event-stream" in self.content_type.casefold():
            return self._sse_messages(final=True)
        if not self.buffer:
            return []
        try:
            value = json.loads(self.buffer.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.truncated = True
            return []
        self.buffer = b""
        return [value] if isinstance(value, dict) else []

    def _sse_messages(self, *, final: bool = False) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        while b"\n\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n\n", 1)
            value = self._decode_sse(raw)
            if value is not None:
                values.append(value)
        if final and self.buffer:
            value = self._decode_sse(self.buffer)
            self.buffer = b""
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _decode_sse(raw: bytes) -> dict[str, Any] | None:
        data = b"\n".join(
            line[5:].lstrip() for line in raw.splitlines() if line.startswith(b"data:")
        )
        if not data:
            return None
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
