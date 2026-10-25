# M7.3 Quality Evaluation Acceptance

## Engineering capability

The M7.3 quality layer is implemented and tested.  It provides explicit Skill
discovery/version snapshots, routing labels with unknown-safe metrics, a
three-arm frozen-artifact runner with an external Oracle, a reversible
third-party stdio MCP manifest/probe path, cautious joint attribution, and
read-only quality summary/detail queries.

The local fixture three-arm report is evidence of `FIXTURE_EXECUTED` capability
only.  It is not a production Skill optimization claim.  The direct local
third-party MCP probe is real child-process stdio/Observer evidence, but it is
not upgraded to a production third-party Codex host claim without that host
trace.

## Gate summary

| Area | Result | Boundary |
|---|---|---|
| Skill registry/version/hash | PASS | explicit roots only; no native invocation claim |
| Routing labels | PASS | forced excluded; natural truth is unavailable on this host |
| Three-arm runner/Oracle isolation | PASS | repeatable local artifact run; production benefit unverified |
| Third-party stdio probe/rollback | PARTIAL | local real fixture pass; Codex host registration not independently re-run |
| Joint attribution | PASS | weak joins become `UNVERIFIED_HYPOTHESIS` |
| Quality summary/detail | PASS | read-only, no-data safe |
| Observer overhead | UNVERIFIED | no paired real workload SLA sample |
| Desktop/PostToolUse/complete `codex exec` | BLOCKED_ENV/HOST_UNSUPPORTED | host limitation, not a fabricated pass |

## Separate decisions

`ENGINEERING_CAPABILITY=PASS` for the implemented, tested bounded path.

`M7_3_OVERALL=PARTIAL` because native Skill lifecycle truth, a real Codex host
third-party MCP trace, and a real controlled Skill benefit comparison are not
all available in this environment.

`M7_3_SKILL_BENEFIT=INCONCLUSIVE`.  The fixture Oracle may report a local
`IMPROVED` comparison, but that does not establish production effectiveness.

Automatic release, patching, publishing and rollback of production Skills/MCPs
are disabled.

## Reproduction

```powershell
$env:PYTHONPATH = "servers"
python -m unittest -v tests.test_m73_quality
python -m unittest discover -s tests -p 'test_*.py'
python -m compileall -q servers/shutu_workshop scripts plugins/shutu-skill-workshop/bin/run_mcp.py
```

The acceptance JSON records actual test counts, the source baseline, host
version and the real local probe/fixture evidence IDs after execution.  Final
and remote commit SHAs are reported in handoff to avoid self-referential
commits in the JSON.
