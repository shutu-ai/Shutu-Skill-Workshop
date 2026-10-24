# M4.0 M3 diagnostics consistency closure

Baseline: `196d20a987478afac47eae63e972f6743746ea3c`.

## Defects reproduced

1. `DiagnosisService._trace_ids()` ordered every trace from the beginning and
   applied `LIMIT`. With more traces than the limit, the tail never became
   eligible.
2. `issue_occurrences` was deleted by `trace_id` only. A second ruleset could
   remove the first ruleset's evidence even though checkpoints were keyed by
   `(trace_id, ruleset_fingerprint)`.
3. `_transport_class()` treated `relay` in the transport path as observer
   failure. A relay is a route, not proof of the failing owner.

## Closure implementation

The M4.0 commit (`a018dc1`) keeps parallel rulesets isolated. Schema migration
5 adds `ruleset_fingerprint` to Issue/Occurrence rows, a durable
`analysis_cursors` table, and `archived_at` for Issue tombstones. Migration 6
adds the M4 diagnosis/proposal/evaluation tables.

`analyze_new(limit=N)` now:

- prioritizes traces without a completed checkpoint for the active ruleset;
- advances a durable rowid cursor only after the bounded batch has been
  processed;
- rotates a small reconciliation window over completed traces, so a late
  event is eventually rechecked without re-running the whole history;
- leaves failed traces without a completed checkpoint, so the next call can
  retry them; and
- reports `scanned`, `eligible`, `analyzed`, `skipped`, and
  `remaining_or_unknown`. Backlog is deliberately `unknown`, not fabricated.

Occurrence replacement filters by the active ruleset and includes that
fingerprint in Issue and occurrence identity. Empty clusters are retained as
auditable tombstones and hidden from the default active Issue listing. Human
status changes still use `row_version`; a new finding reopens a resolved or
ignored Issue and records the transition.

R02 attribution now uses explicit failure-layer evidence, status codes, and
error classes. The transport field remains explanatory metadata only:

| Evidence | Category | Owner |
| --- | --- | --- |
| upstream HTTP 401/403 | `auth_failure_observed` | `auth` |
| timeout/connection/DNS | `network_failure_observed` | `network` |
| observer/capture/persistence/SQLite evidence | `observer_failure_observed` | `observer` |
| relay/proxy 5xx with no boundary evidence | `upstream_boundary_failure_observed` | `unknown` |
| MCP `isError=true` | R01 `tool_failure_observed` | MCP hypothesis, not R02 |

## Reproducible evidence

`tests/test_m4_m40.py` creates 305 traces and runs 13 batches at `limit=25`.
It verifies that trace 300 receives a completed checkpoint, that a late event
on trace 001 is reanalysed, and that replay does not increase its two stored
occurrences. It also simulates an interrupted detector, A/B rulesets, and the
R02 matrix. `tests/test_m3_diagnostics.py` remains green after migration.

The result is parallel ruleset isolation, not an implicit active-ruleset
switch. Existing v1-v4 rows are retained with an empty legacy ruleset marker;
new M4 analyses use their own fingerprint. No M0/M1/M2 tables are rebuilt.
