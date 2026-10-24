"""Explicit loopback Streamable HTTP MCP relay.

The relay forwards application bytes between a configured local client and a
configured local upstream.  Observation is a bounded side channel; failures
never resubmit a request or manufacture a successful response.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Mapping
from urllib.parse import urlsplit

from ...ingestion import TraceIngestor
from .common import McpTraceRecorder
from .streamable_http import _HTTPMessageParser

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_RELAY_TOKEN_HEADER = "x-shutu-workshop-relay-token"


def create_relay_server(
    upstream_url: str,
    *,
    recorder: McpTraceRecorder,
    host: str = "127.0.0.1",
    port: int = 0,
    listener_token: str = "",
    max_request_bytes: int = 256 * 1024,
    max_observe_bytes: int = 64 * 1024,
) -> ThreadingHTTPServer:
    upstream = urlsplit(upstream_url)
    if upstream.scheme not in {"http", "https"} or upstream.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("MCP relay upstream must be an explicit loopback URL")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("MCP relay listener is loopback-only")
    if max_request_bytes < 256 or max_observe_bytes < 256:
        raise ValueError("relay limits are too small")
    if not upstream.path:
        upstream = upstream._replace(path="/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ShutuWorkshopMCPRelay/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(30.0)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _authorized(self) -> bool:
            if not listener_token:
                return True
            return self.headers.get(_RELAY_TOKEN_HEADER, "") == listener_token

        def _read_body(self) -> bytes:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("content length is invalid") from exc
            if length < 0 or length > max_request_bytes:
                raise ValueError("request is too large")
            body = self.rfile.read(length) if length else b""
            if len(body) != length:
                raise ValueError("request body is incomplete")
            return body

        def _upstream_connection(self) -> http.client.HTTPConnection:
            if upstream.scheme == "https":
                return http.client.HTTPSConnection(upstream.hostname, upstream.port or 443, timeout=30)
            return http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=30)

        def _forward_headers(self) -> dict[str, str]:
            return {
                str(name): str(value)
                for name, value in self.headers.items()
                if str(name).casefold() not in _HOP_BY_HOP
                and str(name).casefold() != _RELAY_TOKEN_HEADER
                and str(name).casefold() != "host"
            }

        def _json_body(self, body: bytes) -> dict[str, Any] | None:
            if not body:
                return None
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            return value if isinstance(value, dict) else None

        def _send_error(self, status: int, message: str) -> None:
            body = json.dumps({"error": message}, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self._proxy("GET", b"")

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                body = self._read_body()
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "invalid request")
                return
            self._proxy("POST", body)

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                body = self._read_body()
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "invalid request")
                return
            self._proxy("DELETE", body)

        def _proxy(self, method: str, body: bytes) -> None:
            if not self._authorized():
                self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            connection_id = f"relay-{id(self):x}-{time.perf_counter_ns():x}"
            connection = self._upstream_connection()
            response: http.client.HTTPResponse | None = None
            parser = _HTTPMessageParser()
            request_message = self._json_body(body)
            request_headers = self._forward_headers()
            try:
                recorder.start()
                if request_message is not None:
                    recorder.observe_json(
                        "client_to_server",
                        request_message,
                        headers=self.headers,
                        connection_id=connection_id,
                    )
                path = upstream.path or "/"
                if upstream.query:
                    path += "?" + upstream.query
                connection.request(method, path, body=body or None, headers=request_headers)
                response = connection.getresponse()
                response_headers = {key: value for key, value in response.getheaders()}
                parser.content_type = response_headers.get("Content-Type", "")
                self.send_response(response.status)
                for name, value in response_headers.items():
                    lowered = name.casefold()
                    if lowered in _HOP_BY_HOP or lowered == "content-length":
                        continue
                    self.send_header(name, value)
                content_length = next(
                    (value for name, value in response_headers.items() if name.casefold() == "content-length"),
                    None,
                )
                if content_length is not None:
                    self.send_header("Content-Length", content_length)
                self.send_header("Connection", "close")
                self.end_headers()
                observed = 0
                while True:
                    chunk = response.read(16 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if observed < max_observe_bytes:
                        allowed = max_observe_bytes - observed
                        observed += min(allowed, len(chunk))
                        for message in parser.feed(chunk[:allowed]):
                            recorder.observe_json(
                                "server_to_client",
                                message,
                                headers=response_headers,
                                status_code=response.status,
                                connection_id=connection_id,
                            )
                for message in parser.finish():
                    recorder.observe_json(
                        "server_to_client",
                        message,
                        headers=response_headers,
                        status_code=response.status,
                        connection_id=connection_id,
                    )
                if response.status >= 400 and observed == 0:
                    recorder.observe_json(
                        "server_to_client",
                        {
                            "jsonrpc": "2.0",
                            "id": request_message.get("id") if request_message else "",
                            "error": {"code": response.status},
                        },
                        headers=response_headers,
                        status_code=response.status,
                        connection_id=connection_id,
                    )
                if observed >= max_observe_bytes:
                    recorder.report.failed_observations += 1
                    recorder.report.errors.append("response_summary_truncated")
            except (OSError, http.client.HTTPException) as exc:
                recorder.report.failed_observations += 1
                error_name = "client_cancelled" if isinstance(exc, (BrokenPipeError, ConnectionResetError)) else type(exc).__name__
                recorder.report.errors.append(error_name)
                if response is None:
                    self._send_error(HTTPStatus.BAD_GATEWAY, "upstream unavailable")
            except ValueError:
                if not response:
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid request")
            finally:
                if response is not None:
                    response.close()
                connection.close()
                self.close_connection = True

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = LocalServer((host, port), Handler)
    server.relay_recorder = recorder  # type: ignore[attr-defined]
    server.relay_upstream_url = upstream.geturl()  # type: ignore[attr-defined]
    return server


def serve_in_thread(server: ThreadingHTTPServer) -> Thread:
    thread = Thread(target=server.serve_forever, name="mcp-http-relay", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loopback MCP Streamable HTTP relay")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--token-env-var", default="SHUTU_WORKSHOP_RELAY_TOKEN")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env_var, "")
    server = create_relay_server(
        args.upstream,
        recorder=McpTraceRecorder(
            TraceIngestor(args.workspace),
            server_name="mcp-relay-upstream",
            adapter_id="mcp-observer-relay",
            transport="streamable-http-relay",
        ),
        host=args.host,
        port=args.port,
        listener_token=token,
    )
    sys.stderr.write(f"MCP relay listening at http://{args.host}:{server.server_port}/mcp\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
        server.relay_recorder.close(status="incomplete")  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
