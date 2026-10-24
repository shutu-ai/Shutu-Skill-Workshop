# M2.6 Integration Fixes Design

## Scope

M2.6 closes the missing runtime seams without changing M1's trace contract or
starting M3 diagnosis. The implementation stays in the existing Python/SQLite
layers and uses one small project-local TypeScript plugin bridge.

## OpenCode event sink

`servers/shutu_workshop/adapters/opencode/event_sink.py` is an explicitly
started loopback HTTP collector. It:

1. binds only to a loopback host;
2. requires a bearer or project-local header token;
3. rejects oversized bodies before JSON parsing;
4. allow-lists event metadata before passing it to `OpenCodeAdapter`;
5. never persists prompt, input, output, permission, or arbitrary event bodies;
6. returns a bounded generic error and does not expose secrets;
7. ingests each event through the normal M1 store with a stable trace identity.

`plugin.ts` reads the endpoint and token from process environment, sends only
bounded metadata, limits in-flight requests, uses a short abort timeout, and
does not throw sink failures into the Agent tool path. The sink is therefore an
observation side channel: it can lose evidence, but it cannot alter or retry
the business tool call.

## Correlation scope

`CorrelationScope` is carried in redacted source metadata:

```text
workspace_id, run_id, agent_session_id,
mcp_server, mcp_connection, mcp_session,
transport, request_id
```

Within one source, an MCP response may match a request only when the same
connection/session scope and JSON-RPC ID are present. A bare ID is not a
cross-source identity. Cross-source grouping requires an explicit
`correlation_evidence` object with a verified non-empty `bridge_id`; otherwise
the original events remain separate and queryable.

The design deliberately does not add fields to the MCP request or response.
OpenCode session metadata is read from the existing `_meta` field when present;
the session value is hashed for metadata where a raw credential-like value
would be unsafe.

## Streamable HTTP relay

`servers/shutu_workshop/observers/mcp/relay.py` is a manually started,
loopback-only HTTP relay. It forwards `POST`, `GET`, and `DELETE` to a
configured loopback upstream and preserves application-layer status, body,
headers, JSON-RPC IDs, error results, notifications, and SSE bytes. Hop-by-hop,
`Host`, and transport-managed length headers are handled as HTTP headers rather
than copied as application data.

The recorder parses a bounded copy of the response. Parsing failure or SQLite
failure increments the observation failure report but does not stop forwarding;
upstream failure is returned as an upstream failure. There is no retry loop and
no synthetic successful result. A relay connection ID is generated per client
connection, so concurrent sessions can reuse JSON-RPC IDs without collisions.

The relay is explicit and opt-in. It does not modify system proxy settings,
global Codex/OpenCode configuration, or arbitrary upstream destinations.

## Bounded Agent capture

`BaseAdapter.capture_stream()` uses an explicit argument list, separate bounded
stdout/stderr reader threads, a bounded queue, complete-line parsing, and
timeout/terminate/kill handling. Valid complete JSON objects are ingested in
batches. A partial final line, oversized line, invalid JSON line, timeout, or
truncated sample is represented in `CaptureResult` rather than silently
discarded.

Restart idempotence uses the existing M1 event identity plus a stable source
fingerprint. The first trace's session/run identity is retained; later batches
do not rewrite the started trace. This keeps the implementation small while
making repeated complete events safe. A durable byte-offset checkpoint for an
external long-running pipe is intentionally not invented here; use
`CheckpointedJSONL` for file sources when a durable offset is required.

## Compatibility and trade-offs

- The fixture supports both legacy and modern protocol labels, JSON responses,
  notification `202`, and SSE responses.
- The relay has no third-party dependency and is intentionally narrower than a
  general-purpose MCP gateway.
- Official MCP SDK interoperability was not claimed because no compatible SDK
  was installed in the probe environment.
- OpenCode V2 plugin loading is runtime verified; a real OpenCode Agent tool
  call is separately marked `BLOCKED_ENV` when the provider did not emit one.
