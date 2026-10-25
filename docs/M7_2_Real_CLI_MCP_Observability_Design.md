# M7.2 Real CLI MCP Observability Design

## Data flow

```text
interactive Codex CLI
        |
        | original stdio frames
        v
m72_stdio_observer.py
        |                    \
        | unchanged bytes     \ redacted event summary
        v                      v
run_mcp.py              TraceIngestor -> TraceStore
                              |
                              v
                    existing M3 RuleDetector -> Issue Ledger
                              |
                              v
                    Workshop MCP read tools
```

The observer is an opt-in wrapper for one explicitly selected MCP server. It
does not create a second agent, change the server protocol, retry a tool call,
or replace the existing Workshop Core store.

## Event and correlation contract

Each event keeps the MCP server instance/connection, transport, direction,
JSON-RPC id, request/response role, tool name, status, error class, and a
bounded correlation scope. Pending calls are keyed by
`(connection_id, request_id, initiator_direction)`. A bare JSON-RPC id is never
enough for a cross-connection or cross-source exact join.

The adapter event key includes a unique sequence marker for notifications, so a
notification with no JSON-RPC id cannot collide with request id `1`. Responses
remove only their scoped pending entry. Server requests therefore do not steal
the duration or tool name of an unrelated client request.

Statuses are intentionally separate:

- `started` / `ok` for call and result observations;
- `mcp.error` for `result.isError=true` business failures;
- `mcp.protocol_error` for JSON-RPC `error` objects;
- `cancelled`, `startup_timeout`, `idle_timeout`, `run_timeout`, and EOF/child
  termination in the Observer report;
- `unknown` where a host Hook supplies no trustworthy result status.

## Lifecycle

The default `StdioObserver` lifetime is unbounded. An optional total timeout,
idle timeout, startup timeout, and shutdown timeout are explicit controls. The
startup boundary is the first server response; it is not enabled as a normal
30-second lifetime. EOF, child exit, Ctrl+C, and Windows process-tree cleanup
are recorded with a termination reason. Observer errors are fail-open for
forwarding and are recorded as observation gaps; no business request is
replayed.

## Diagnosis closure

An observed error is persisted before deterministic diagnosis is attempted.
The existing `DiagnosisService` and R01/R02 rules create or update the existing
Issue cluster and occurrence tables. `workshop_list_issues` performs a bounded
read-side reconciliation before listing issues, closing the race where the
next MCP read could arrive while the observer's best-effort diagnosis was
still running. This is still the same M3 ledger, not a second issue database.

## Installation boundary

`install-m72-observer.ps1 -Mode Enable` accepts only the exact owned M7.1
Compat server, saves a manifest, removes that one entry, and registers one
Observer entry whose child is the original `run_mcp.py`. Disable validates the
owned Observer and manifest, removes it, and restores the saved Compat
definition. `-WhatIf` performs no writes. Recursive Observer arguments,
missing targets, and same-name non-Workshop entries are rejected.

## Privacy and retention

Trace payloads use the existing redaction policy and summary-only fields.
Private install manifests remain under Codex HOME and are not checked in.
SQLite history is never removed by install/disable. The operator may archive
the normal workspace according to the existing Workshop retention policy; no
M7.2 command purges traces or Issues.
