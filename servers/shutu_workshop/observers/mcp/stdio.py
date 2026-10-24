"""Transparent MCP stdio wrapper for an explicitly configured child process."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence

from .common import McpTraceRecorder, ObserverReport


class StdioFrameCodec:
    """Read newline JSON and Content-Length framed messages without rewriting them."""

    @staticmethod
    def read(stream: BinaryIO) -> bytes | None:
        first = stream.readline()
        if not first:
            return None
        if first.lower().startswith(b"content-length:"):
            headers = [first]
            content_length: int | None = None
            while True:
                line = stream.readline()
                if not line:
                    return b"".join(headers)
                headers.append(line)
                if line in {b"\r\n", b"\n"}:
                    break
                name, _, value = line.partition(b":")
                if name.strip().casefold() == b"content-length":
                    try:
                        content_length = int(value.strip())
                    except ValueError:
                        content_length = None
            if content_length is None or content_length < 0:
                return b"".join(headers)
            body = StdioFrameCodec._read_exact(stream, content_length)
            return b"".join(headers) + body
        return first

    @staticmethod
    def _read_exact(stream: BinaryIO, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def payload(frame: bytes) -> bytes:
        if b"\r\n\r\n" in frame:
            return frame.split(b"\r\n\r\n", 1)[1]
        if b"\n\n" in frame and frame.lstrip().casefold().startswith(b"content-length:"):
            return frame.split(b"\n\n", 1)[1]
        return frame.strip()


class StdioObserver:
    def __init__(
        self,
        command: Sequence[str],
        *,
        recorder: McpTraceRecorder | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_stderr_bytes: int = 16 * 1024,
    ) -> None:
        if not command:
            raise ValueError("MCP stdio command is required")
        if timeout <= 0 or max_stderr_bytes < 256:
            raise ValueError("invalid stdio limits")
        self.command = tuple(command)
        self.recorder = recorder or McpTraceRecorder(transport="stdio")
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env or {})
        self.timeout = timeout
        self.max_stderr_bytes = max_stderr_bytes

    def proxy_bytes(self, payload: bytes) -> tuple[bytes, ObserverReport]:
        input_stream = io.BytesIO(payload)
        output_stream = io.BytesIO()
        report = self.run(input_stream, output_stream)
        return output_stream.getvalue(), report

    def run(self, input_stream: BinaryIO | None = None, output_stream: BinaryIO | None = None) -> ObserverReport:
        input_stream = input_stream or sys.stdin.buffer
        output_stream = output_stream or sys.stdout.buffer
        environment = os.environ.copy()
        environment.update(self.env)
        started = time.perf_counter()
        process = subprocess.Popen(
            list(self.command),
            cwd=self.cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self.recorder.start()
        report = self.recorder.report
        stderr_chunks: list[bytes] = []
        stderr_size = 0
        lock = threading.Lock()

        def client_to_server() -> None:
            try:
                while True:
                    frame = StdioFrameCodec.read(input_stream)
                    if frame is None:
                        break
                    if process.stdin is None:
                        break
                    process.stdin.write(frame)
                    process.stdin.flush()
                    report.forwarded_messages += 1
                    self._observe(frame, "client_to_server")
            except (BrokenPipeError, OSError) as exc:
                report.errors.append(type(exc).__name__)
            finally:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

        def server_to_client() -> None:
            try:
                while process.stdout is not None:
                    frame = StdioFrameCodec.read(process.stdout)
                    if frame is None:
                        break
                    output_stream.write(frame)
                    output_stream.flush()
                    report.forwarded_messages += 1
                    self._observe(frame, "server_to_client")
            except (BrokenPipeError, OSError) as exc:
                report.errors.append(type(exc).__name__)

        def drain_stderr() -> None:
            nonlocal stderr_size
            if process.stderr is None:
                return
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = self.max_stderr_bytes - stderr_size
                    if remaining > 0:
                        stderr_chunks.append(chunk[:remaining])
                        stderr_size += min(len(chunk), remaining)

        threads = [
            threading.Thread(target=client_to_server, name="mcp-stdio-client", daemon=True),
            threading.Thread(target=server_to_client, name="mcp-stdio-server", daemon=True),
            threading.Thread(target=drain_stderr, name="mcp-stdio-stderr", daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            report.timed_out = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=2)
        report.process_exit_code = process.returncode
        report.timed_out = timed_out
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        report.stderr_summary = str(self.recorder.policy.redact(stderr)) if stderr else ""
        report.observer_overhead_ms += max(0.0, (time.perf_counter() - started) * 1000 - report.observer_overhead_ms)
        self.recorder.close(status="incomplete" if timed_out or report.errors else "ok")
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return report

    def _observe(self, frame: bytes, direction: str) -> None:
        try:
            value = json.loads(StdioFrameCodec.payload(frame).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.recorder.report.failed_observations += 1
            if "invalid_json" not in self.recorder.report.errors:
                self.recorder.report.errors.append("invalid_json")
            return
        if isinstance(value, dict):
            self.recorder.observe_json(direction, value)
            if direction == "server_to_client":
                result = value.get("result") if isinstance(value.get("result"), dict) else {}
                method = self._pending_method(value.get("id"))
                if method == "tools/list" and isinstance(result.get("tools"), list):
                    self.recorder.register_tools(
                        result["tools"],
                        server_info=result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {},
                        protocol=self.recorder.report.protocol_era,
                    )

    def _pending_method(self, response_id: object) -> str:
        key = json.dumps(response_id, sort_keys=True, separators=(",", ":"))
        pending = self.recorder._pending.get(key)  # local observer correlation, never sent to the server
        return pending[0] if pending else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transparent MCP stdio observer")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--server-name", default="test-server")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a child MCP server command is required after --")
    recorder = McpTraceRecorder(
        TraceIngestor(args.workspace) if args.workspace else None,
        server_name=args.server_name,
        transport="stdio",
    )
    report = StdioObserver(command, recorder=recorder, timeout=args.timeout).run()
    sys.stderr.write(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if report.process_exit_code == 0 and not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
