# M6.2 Real MCP + Independent Oracle Closure

## Independent status

`M6_2_STATUS=PARTIAL` and remains separate from M7 plugin acceptance.

The authenticated M6.1 pair evidence at
`.test-m61/evidence/m61-pair-authenticated.json` recorded six completed
processes and six verified artifact activations. Only `target/baseline` showed
an actual Codex MCP call; its result failed. The other five runs had no MCP
tool event. Oracle verdicts remained `unknown`, so this is not a successful
real task result or optimization improvement.

| Measure | Observed |
| --- | --- |
| Real MCP result success | `0` |
| Actual logical MCP calls | `1` |
| Failed observed MCP calls | `1` |
| No-tool runs | `5` |
| Independent correctness | `UNVERIFIED` |
| Optimization benefit | `INCONCLUSIVE` |
| Raw task inputs persisted | `false` |
| Credentials persisted | `false` |

The M7 plugin does not reinterpret these records. Its evaluation tool preserves
`unknown`, evidence tier, execution status, and failure metadata. The minimum
next M6.2 step is to capture the full redacted initialize/list/call/result
boundary and a frozen independent Oracle for the same registered server/tool;
that work must not be hidden inside a plugin E2E claim.
