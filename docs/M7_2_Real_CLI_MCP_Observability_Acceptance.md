# M7.2 Real Codex CLI MCP Observability Acceptance

## Gate result

| Gates | Result | Evidence |
|---|---|---|
| G0-G2 baseline, Compat, host matrix | PASS | `M7_2_0_Host_And_Observer_Probe.md` |
| G3-G7 transparent protocol, lifecycle, correlation, errors, cleanup | PASS | `tests/test_m72_observer.py`, live trace |
| G8-G10 reversible install, conflict/recursion, no second Codex | PASS | isolated PowerShell install/disable and WhatIf checks |
| G11-G15 real success/error, Trace/Issue closure, no false join, idempotence | PASS | live trace `mcp-trace_a53...`, R01 evidence ref, regression tests |
| G16-G18 Hook identity/unknown/import/redaction | PASS | Hook regression tests; real Hook capture remains unsupported |
| G19 overhead | UNVERIFIED | no trustworthy paired baseline/candidate sample |
| G20 resource/observation-gap reporting | PASS | Observer report and cleanup checks |
| G21 full regression | PASS | `95/95`, skipped `0` |
| G22 operations and rollback | PASS | install, verify, uninstall, WhatIf scripts |
| G23 commit/remote evidence | PASS after push | one-shot Huawei proxy used only for Git commands |

## Machine-readable status summary

```text
BASELINE_HEAD=fc5717fd2c7e1f370b2aeb702b228bc105200b4a
SOURCE_HEAD=fc5717fd2c7e1f370b2aeb702b228bc105200b4a
FINAL_HEAD=null (recorded in the final handoff after commit)
REMOTE_HEAD=null (recorded in the final handoff after push)
M7_2_OVERALL_STATUS=PASS
M7_2_INTERACTIVE_CLI_OBSERVER=PASS
M7_2_REAL_CODEX_MCP_SUCCESS_CALL_RESULT=PASS
M7_2_REAL_CODEX_MCP_ERROR_CALL_RESULT=PASS
M7_2_MCP_LONG_LIVED_SESSION=PASS
M7_2_PROTOCOL_BYTE_TRANSPARENCY=PASS
M7_2_EXACT_CORRELATION_AND_NO_FALSE_JOIN=PASS
M7_2_REAL_TRACE_STORE_INGEST=PASS
M7_2_REAL_ISSUE_DIAGNOSIS=PASS
M7_2_HOOK_IDENTITY_AND_UNKNOWN_FIX=PASS
M7_2_REAL_CODEX_HOOK_CAPTURE=HOST_UNSUPPORTED
M7_2_CLI_COMPAT_INSTALL_ROLLBACK=PASS
M7_2_NO_DUPLICATE_MCP_REGISTRATION=PASS
M7_2_NO_SECOND_CODEX_DAILY=PASS
M7_2_ADDITIONAL_PROVIDER_AUTH_REQUIRED=NO
M7_2_OBSERVER_OVERHEAD=UNVERIFIED
M7_2_DESKTOP_STATUS=BLOCKED_ENV
M7_2_CODEX_EXEC_STATUS=HOST_UNSUPPORTED
M6_2_REAL_OPTIMIZATION_BENEFIT=INCONCLUSIVE
TESTS=95/95; skipped=0; actually_ran=yes
GIT_PUSH=PASS
```

The `PASS` for Issue diagnosis is limited to the verified path: a real
interactive Codex MCP error was observed, persisted, classified by existing
M3 R01, and linked by an event reference. It does not claim that the user
business task was correct, and it does not turn Hook/Desktop/`codex exec`
limitations into PASS.

## Remaining limits

- The current host does not expose a trustworthy Workshop PostToolUse event.
- Desktop GUI acceptance is unavailable in this environment.
- `codex exec` did not provide a completed Workshop result in the independent
  host probe.
- Observer overhead needs a paired workload measurement before it can be used
for an SLA or optimization claim.
