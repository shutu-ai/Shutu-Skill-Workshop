"""Optional loopback-only HTTP ingestion; nothing starts it by default."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from .ingestion import TraceIngestor
from .models import IngestValidationError


class IngestHTTPError(ValueError):
    pass


def create_ingest_server(
    ingestor: TraceIngestor,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
    max_request_bytes: int = 256 * 1024,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M1 HTTP ingestion is loopback-only")
    if not token:
        raise ValueError("HTTP ingestion is disabled without a local token")
    if max_request_bytes < 256:
        raise ValueError("max_request_bytes is too small")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShutuWorkshopM1/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15.0)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args  # Do not log headers or request payloads.

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            provided = self.headers.get("X-Workshop-Token", "")
            authorization = self.headers.get("Authorization", "")
            if authorization.casefold().startswith("bearer "):
                provided = authorization[7:]
            return hmac.compare_digest(provided, token)

        def _body(self) -> Any:
            raw_length = self.headers.get("Content-Length", "")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise IngestHTTPError("content length is required") from exc
            if length < 0 or length > max_request_bytes:
                raise IngestHTTPError("request is too large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise IngestHTTPError("request body is incomplete")
            try:
                return json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IngestHTTPError("request is not valid JSON") from exc

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                self._json(HTTPStatus.OK, {"enabled": True, "loopback_only": True})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                if self.path == "/v1/events:batch":
                    if isinstance(body, dict):
                        records = body.get("records", body.get("events"))
                    else:
                        records = body
                    if not isinstance(records, list):
                        raise IngestHTTPError("events must be a JSON array")
                    result = ingestor.ingest_records(records, source="http")
                elif self.path == "/v1/traces":
                    trace = body.get("trace", body) if isinstance(body, dict) else None
                    if not isinstance(trace, dict):
                        raise IngestHTTPError("trace must be a JSON object")
                    result = ingestor.ingest_records(
                        [{"record_type": "trace", "trace": trace}],
                        source="http",
                    )
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, result.to_dict())
            except IngestValidationError as exc:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    if "limit" in str(exc).casefold() or "large" in str(exc).casefold()
                    else HTTPStatus.BAD_REQUEST,
                    {
                        "accepted": 0,
                        "duplicates": 0,
                        "conflicts": 0,
                        "rejected": max(1, exc.rejected),
                        "errors": ["invalid input"],
                    },
                )
            except (IngestHTTPError, ValueError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid input"})

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return LocalServer((host, port), Handler)


def serve_in_thread(server: ThreadingHTTPServer) -> Thread:
    thread = Thread(target=server.serve_forever, name="shutu-workshop-ingest", daemon=True)
    thread.start()
    return thread
