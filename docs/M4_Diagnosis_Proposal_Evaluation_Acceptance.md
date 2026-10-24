# M4 acceptance evidence

## Required gates

| Gate | Evidence | Result |
| --- | --- | --- |
| M4.0 scheduler | 305 traces, `limit=25`, tail checkpoint, late-event reanalysis | PASS |
| M4.0 ruleset consistency | A/B fingerprints retain both Occurrence sets; tombstones retain IDs/history | PASS |
| M4.0 attribution | 401/403, timeout, observer SQLite, relay 502, MCP `isError` matrix | PASS |
| Evidence Bundle | actual Trace/Event/CapabilityVersion validation, byte cap, redaction | PASS |
| Provider contract | Fake deterministic; invalid output/injection/static candidate rejected | PASS |
| Proposal review | row-version check, candidate hash changes, no apply path | PASS |
| Static validation | separate `STATIC_VALIDATION` result | PASS |
| Fixture evaluation | independent Oracle, paired baseline/candidate, holdout | PASS |
| Real LLM run | no authorised endpoint configured | BLOCKED_ENV |
| Real task evaluation | no side-effect-safe runner configured | BLOCKED_ENV |

## Reproduction

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m3_diagnostics tests.test_m4_m40 tests.test_m4_evolution -v
python -m compileall -q servers/shutu_workshop
python -m shutu_workshop --help
```

The evolution test performs the complete local chain and records a paired
fixture verdict of baseline `3/5`, candidate `5/5`, `IMPROVED`. A revised
candidate that breaks MCP echo is independently judged `REGRESSED`.

`FALSE_EXACT_JOIN_COUNT=0` is a static review result: the observer/relay path
does not join unrelated source identifiers, and the M4 fixtures use explicit
trace/event references. `ISSUE_REPLAY_DUPLICATES=0` is the replay assertion:
the late-event test has two occurrences before and after an unchanged replay.

## Safety boundary

Fixture evaluation is local and deterministic. It does not load a live Skill,
write a production MCP server, run production SQL, send SMS, use SSH, or
change global agent configuration. Real-task status must remain
`BLOCKED_ENV`/`UNVERIFIED` until an explicitly authorised isolated runner and
independent Oracle are available. OpenCode hook and official SDK
interoperability remain inherited M2.6 limitations.
