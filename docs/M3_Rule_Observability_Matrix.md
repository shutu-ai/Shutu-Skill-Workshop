# M3 Rule Observability Matrix

M3 uses only persisted, capture-time-redacted `Trace`/`TraceEvent` fields. A
missing field is recorded as `not_observed`; it is never inferred from timing,
tool name, or a bare JSON-RPC id.

| Rule | Required evidence | Current source | Detectable result | Not observable / upper bound | False-positive control |
|---|---|---|---|---|---|
| R01 MCP business error | `mcp` result with `isError=true` or JSON-RPC `error`, event ID | MCP observer `payload_json`, `error_type`, request scope | `tool_failure_observed`, error severity | Server intent and task correctness; owner remains hypothesis | Success results and plain error text do not match; no cross-source join |
| R02 network/auth/transport | Structured `error_type`, HTTP status, or observer/relay layer | MCP observer status/error fields and safe source metadata | `auth_failure_observed`, `network_failure_observed`, or `observer_failure_observed` | Root cause behind a timeout/502; no Skill defect claim | 401/403 are auth; relay layer is observer; business `isError` is R01 |
| R03 schema mismatch | Schema hash/version plus explicit validation failure or server schema error | Temporary event metadata/payload; capability snapshot may provide version | `schema_mismatch_observed` | Argument keys alone, schema content, and parameter values | Insufficient evidence is returned as an audit finding and is not persisted as an Issue |
| R04 retry failure | Explicit `retry_of_event_id` or deterministic attempt chain in same scope and failed attempts | Trace event retry fields and source scope | `retry_failure_pattern` candidate | Whether retry was useful; no inference from time/tool/id | Same tool or same ID without explicit chain is ignored |
| R05 observation gap | Closed trace plus partial/incomplete status, missing response, or failure counters | Trace status/completeness, adapter/observer metadata | `incomplete_observation` warning | Events that were never captured; running traces are not failed | Running traces are excluded; task result remains unknown when absent |
| R06 missing Skill trigger | Explicit expected capability plus discoverability, trigger, and scope evidence | Controlled trace metadata and observed capability IDs | `skill_missing_trigger` candidate | Whether an unobserved Skill was actually available or appropriate | Without all expectation flags it is not assessable and creates no Issue |

Stable fields available to every rule are `trace_id`, `workspace_id`, adapter
identity/version, event ID/sequence, event type/status, capability/version IDs,
source/transport/request scope, retry linkage, and trace completeness. MCP
session identifiers are hashed by M2 before persistence. Payloads and source
metadata pass through the existing redaction policy.

`RuleFinding.attribution_state` is `hypothesis` or
`insufficient_evidence`; M3 never emits `verified_cause` or `verified_fix`.
