# M3 Rule Detector and Issue Ledger Acceptance

Date: 2026-10-24. Results below distinguish deterministic fixture evidence,
real Codex evidence, and host-dependent gaps.

| Gate | Result | Evidence |
|---|---|---|
| G0 Git/workspace baseline | PASS | M2.6 baseline `137e25d`; existing dirty M0/M1 files preserved |
| G1 M0–M2.6 regression | PASS | `python -m unittest discover -s tests -v`: 48/48, skipped 0 |
| G2 Rule registry | PASS | Six versioned rules; `tests/test_m3_diagnostics.py` |
| G3 MCP error detection | PASS | R01 positive/negative test and structured fixture result |
| G4 environment/auth separation | PASS | R02 tests classify auth, network, observer/relay independently |
| G5 schema evidence gate | PASS | R03 emits insufficient evidence for argument keys and detects explicit validation |
| G6 retry gate | PASS | R04 requires explicit `retry_of_event_id` and same scope |
| G7 missing-trigger conservatism | PASS | R06 requires explicit expectation flags |
| G8 evidence chain | PASS | Issue occurrence stores trace/event/capability-version refs |
| G9 dedup/replay | PASS | checkpoint skip and occurrence-count assertions |
| G10 scoped attribution | PASS | same request ID on different connections remains separate |
| G11 status/concurrency | PASS | row-version conflict, status history, and reopen tests |
| G12 incremental recovery | PASS | late event changes revision and reconciles findings |
| G13 additive migration | PASS | schema versions 1–4 and legacy tables retained |
| G14 privacy/bounds | PASS | redaction test and bounded JSON summaries |
| G15 CLI/operations | PASS | diagnose/issue subcommands compile and use local API |
| G16 real Codex + MCP | PASS | M2.6 real echo trace has zero tool-failure Issues; new isolated Codex `error` call produced one R01 occurrence in relay trace `mcp-trace_2be4474f2f1c4daba70e365e3695e32c` |
| G17 OpenCode/SDK | BLOCKED_ENV/UNVERIFIED | M2.6 OpenCode tool hook blocked; official SDK not installed |
| G18 delivery | PASS | compileall and diff check passed; final commit/push fields are in the completion report |

## Reproduction

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m3_diagnostics -v
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
```

The controlled real Codex success/failure run must use only the local fixture
and loopback relay in an ignored `.test-m3/` directory. If the provider does
not call the failure tool, the result is `PARTIAL` or `BLOCKED_ENV`; no fake
tool event is inserted. OpenCode's M2.6 `BLOCKED_ENV` state remains separate
from M3 core acceptance.

Context7 was attempted with the user-provided process-local proxy
`http://proxyae.huawei.com:8080`; no usable result was returned in the bounded
probe. The implementation therefore relies on installed CLI help and official
documentation, not an SDK interoperability claim.

## Measured analysis cost

On this Windows/Python 3.14 workspace, 30 synthetic local traces were scanned
in one bounded batch: `analysis_total_ms=728.374`, or
`24.279 ms/trace`. This is a local diagnostic measurement, not an SLA.
