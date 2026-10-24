"""Loopback JSON event sink for the project-local OpenCode plugin."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from ...ingestion import TraceIngestor
from .adapter import OpenCodeAdapter


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _bounded_event(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "type",
        "id",
        "sessionID",
        "session_id",
        "callID",
        "call_id",
        "messageID",
        "message_id",
        "turnID",
        "turn_id",
        "runID",
        "run_id",
        "tool",
        "toolName",
        "status",
        "error_type",
        "timestamp",
        "occurred_at",
        "plugin_id",
        "api_generation",
        "host_version",
        "dropped",
        "failed",
    }
    return {key: value[key] for key in allowed if key in value}


def create_event_sink_server(
    ingestor: TraceIngestor,
    *,
    token: str,
    host: str = "127.0.0.1",
    port: int = 0,
    path: str = "/events",
    max_request_bytes: int = 64 * 1024,
    workspace_id: str = "local",
    host_version: str = "unknown",
) -> ThreadingHTTPServer:
    """Create an explicit, authenticated, loopback-only OpenCode event sink."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OpenCode event sink is loopback-only")
    if not token:
        raise ValueError("OpenCode event sink requires a local token")
    if not path.startswith("/") or max_request_bytes < 256:
        raise ValueError("invalid event sink configuration")
    adapter = OpenCodeAdapter(ingestor, workspace_id=workspace_id, host_version=host_version)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShutuWorkshopOpenCodeSink/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10.0)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            provided = self.headers.get("X-Shutu-Workshop-Token", "")
            authorization = self.headers.get("Authorization", "")
            if authorization.casefold().startswith("bearer "):
                provided = authorization[7:]
            return hmac.compare_digest(provided, token)

        def _body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise ValueError("content length is required") from exc
            if length < 1 or length > max_request_bytes:
                raise ValueError("request is too large")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("request body is incomplete")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("event must be a JSON object")
            return value

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ready": True,
                        "loopback_only": True,
                        "adapter_id": adapter.adapter_id,
                        "accepted": adapter.health().accepted,
                        "rejected": adapter.health().rejected,
                    },
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != path:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                event = _bounded_event(self._body())
                session_id = str(event.get("sessionID") or event.get("session_id") or "")
                run_id = str(event.get("runID") or event.get("run_id") or session_id or "sink")
                trace_id = f"trace_opencode_sink_{_opaque(workspace_id + ':' + run_id)}"
                result = adapter.ingest_event(event, trace_id=trace_id)
                self._json(HTTPStatus.OK, result.to_dict())
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid event"})
            except Exception:
                # Never return raw exception details or block the host on diagnostics.
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "sink unavailable"})

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = LocalServer((host, port), Handler)
    server.event_sink_adapter = adapter  # type: ignore[attr-defined]
    server.event_sink_path = path  # type: ignore[attr-defined]
    return server


def serve_in_thread(server: ThreadingHTTPServer) -> Thread:
    thread = Thread(target=server.serve_forever, name="opencode-event-sink", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Loopback OpenCode JSON event sink")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--path", default="/events")
    parser.add_argument("--token-env-var", default="SHUTU_WORKSHOP_EVENT_SINK_TOKEN")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env_var, "")
    if not token:
        parser.error(f"set {args.token_env_var} for this process")
    server = create_event_sink_server(
        TraceIngestor(args.workspace),
        token=token,
        host=args.host,
        port=args.port,
        path=args.path,
    )
    sys.stderr.write(f"OpenCode event sink listening at http://{args.host}:{server.server_port}{args.path}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
