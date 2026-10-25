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

from ...ingestion import TraceIngestor
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
            _, _, first_value = first.partition(b":")
            try:
                content_length = int(first_value.strip())
            except ValueError:
                content_length = None
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
        timeout: float | None = None,
        startup_timeout: float = 10.0,
        idle_timeout: float | None = None,
        shutdown_timeout: float = 5.0,
        max_stderr_bytes: int = 16 * 1024,
    ) -> None:
        if not command:
            raise ValueError("MCP stdio command is required")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive when supplied")
        if startup_timeout <= 0 or (idle_timeout is not None and idle_timeout <= 0) or shutdown_timeout <= 0 or max_stderr_bytes < 256:
            raise ValueError("invalid stdio limits")
        self.command = tuple(command)
        self.recorder = recorder or McpTraceRecorder(transport="stdio")
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env or {})
        # ``timeout`` remains the opt-in bounded test lifetime.  The default
        # is deliberately unbounded so a normal interactive MCP session lives
        # until Codex closes its stdio connection.
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self.idle_timeout = idle_timeout
        self.shutdown_timeout = shutdown_timeout
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
        server_activity = threading.Event()
        client_eof = threading.Event()
        server_eof = threading.Event()
        last_activity = [started]

        def mark_activity() -> None:
            last_activity[0] = time.perf_counter()

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
                    mark_activity()
                    self._observe(frame, "client_to_server")
            except (BrokenPipeError, OSError) as exc:
                report.errors.append(type(exc).__name__)
            finally:
                client_eof.set()
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
                    mark_activity()
                    server_activity.set()
                    self._observe(frame, "server_to_client")
            except (BrokenPipeError, OSError) as exc:
                report.errors.append(type(exc).__name__)
            finally:
                server_eof.set()

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
        cancelled = False
        termination_reason = ""
        try:
            while process.poll() is None:
                now = time.perf_counter()
                if self.timeout is not None and now - started >= self.timeout:
                    timed_out = True
                    termination_reason = "run_timeout"
                    break
                if self.idle_timeout is not None and now - last_activity[0] >= self.idle_timeout:
                    timed_out = True
                    termination_reason = "idle_timeout"
                    break
                if not server_activity.is_set() and now - started >= self.startup_timeout:
                    timed_out = True
                    termination_reason = "startup_timeout"
                    break
                # Both pipes reached EOF.  A child that ignores stdin EOF is
                # no longer a usable MCP session, so close it after the normal
                # shutdown path instead of leaving a zombie process behind.
                if client_eof.is_set() and server_eof.is_set():
                    termination_reason = "connection_closed"
                    break
                time.sleep(0.05)
        except KeyboardInterrupt:
            cancelled = True
            termination_reason = "cancelled"
        if process.poll() is None:
            self._stop_process(process)
            try:
                process.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                self._kill_process(process)
                process.wait(timeout=self.shutdown_timeout)
        elif not termination_reason:
            termination_reason = "child_exit"
        for thread in threads:
            thread.join(timeout=2)
        report.process_exit_code = process.returncode
        report.timed_out = timed_out
        report.termination_reason = termination_reason or "child_exit"
        if cancelled:
            report.errors.append("cancelled")
        if timed_out:
            report.errors.append(termination_reason or "timeout")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        report.stderr_summary = str(self.recorder.policy.redact(stderr)) if stderr else ""
        report.observer_overhead_ms += max(0.0, (time.perf_counter() - started) * 1000 - report.observer_overhead_ms)
        close_status = "cancelled" if cancelled else ("incomplete" if timed_out or report.errors else "ok")
        self.recorder.close(status=close_status)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return report

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        process.terminate()

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        process.kill()

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transparent MCP stdio observer")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--server-name", default="test-server")
    parser.add_argument("--timeout", type=float, default=None, help="optional bounded test lifetime")
    parser.add_argument("--startup-timeout", type=float, default=10.0)
    parser.add_argument("--idle-timeout", type=float, default=None)
    parser.add_argument("--shutdown-timeout", type=float, default=5.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a child MCP server command is required after --")
    workspace = args.workspace or Path.cwd()
    recorder = McpTraceRecorder(
        TraceIngestor(workspace),
        server_name=args.server_name,
        transport="stdio",
    )
    report = StdioObserver(
        command,
        recorder=recorder,
        timeout=args.timeout,
        startup_timeout=args.startup_timeout,
        idle_timeout=args.idle_timeout,
        shutdown_timeout=args.shutdown_timeout,
    ).run()
    sys.stderr.write(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if report.process_exit_code == 0 and not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
