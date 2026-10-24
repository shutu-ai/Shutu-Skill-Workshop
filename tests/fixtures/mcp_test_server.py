"""No-side-effect MCP fixture supporting stdio and loopback HTTP."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


TOOLS = [
    {
        "name": "echo",
        "description": "Return the supplied fixture text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two fixture integers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "error",
        "description": "Return a deterministic tool error.",
        "inputSchema": {"type": "object"},
    },
]


def response(request: dict[str, Any], result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result or {}
    return value


def handle(request: dict[str, Any], *, protocol: str = "legacy") -> list[dict[str, Any]]:
    method = request.get("method")
    if method == "initialize":
        version = "2025-11-25" if protocol == "legacy" else "2026-07-28"
        return [
            response(
                request,
                {
                    "protocolVersion": version,
                    "serverInfo": {"name": "shutu-fixture", "version": "1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        ]
    if method == "server/discover":
        return [
            response(
                request,
                {
                    "protocolVersion": "2026-07-28",
                    "serverInfo": {"name": "shutu-fixture", "version": "1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            )
        ]
    if method in {"initialized", "notifications/initialized"}:
        return []
    if method == "tools/list":
        return [response(request, {"tools": TOOLS})]
    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name == "echo":
            return [response(request, {"content": [{"type": "text", "text": str(arguments.get("text", ""))}]})]
        if name == "add" and isinstance(arguments.get("a"), int) and isinstance(arguments.get("b"), int):
            return [response(request, {"content": [{"type": "text", "text": str(arguments["a"] + arguments["b"])}]})]
        if name == "error":
            return [response(request, {"isError": True, "content": [{"type": "text", "text": "fixture error"}]})]
        return [response(request, error={"code": -32602, "message": "invalid fixture tool call"})]
    if method in {"$/cancelRequest", "$ /cancelRequest"}:
        return []
    if "id" in request:
        return [response(request, error={"code": -32601, "message": "method not found"})]
    return []


def run_stdio(protocol: str = "legacy") -> int:
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        for value in handle(request, protocol=protocol):
            sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


class FixtureHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    protocol = "legacy"
    sse = False

    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        values = handle(request, protocol=self.protocol)
        if not values and "id" not in request:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        wants_sse = self.sse or "text/event-stream" in self.headers.get("Accept", "")
        if wants_sse:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for value in values:
                self.wfile.write(("data: " + json.dumps(value, separators=(",", ":")) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(0.001)
            return
        body = json.dumps(values[0] if values else {}, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_http_server(
    *,
    protocol: str = "legacy",
    sse: bool = False,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    handler = type("FixtureHandler", (FixtureHTTPHandler,), {"protocol": protocol, "sse": sse})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def start_http_server(*, protocol: str = "legacy", sse: bool = False) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_http_server(protocol=protocol, sse=sse)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--protocol", choices=("legacy", "modern"), default="legacy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sse", action="store_true")
    args = parser.parse_args(argv)
    if args.transport == "http":
        server = create_http_server(
            protocol=args.protocol,
            sse=args.sse,
            host=args.host,
            port=args.port,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            server.shutdown()
            server.server_close()
        return 0
    return run_stdio(args.protocol)


if __name__ == "__main__":
    raise SystemExit(main())
