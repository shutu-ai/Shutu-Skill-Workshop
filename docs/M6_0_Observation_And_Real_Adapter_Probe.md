# M6.0 Observation and real-adapter probe

## Status

`M6_STATUS=PARTIAL`. The observation fixes and the local real-agent adapter are implemented and tested. A real Codex process was launched in an isolated `CODEX_HOME`, but the provider rejected the request with `401 Unauthorized: Missing bearer`; therefore no real MCP tool call or paired Agent task is claimed.

The baseline for this work was local `HEAD=88cefcb0981882b009837a8e9767df4495cd6db`. Existing dirty files from M1–M5 were preserved and are not part of the M6 commit.

## M5 P1 fixes

1. `before` traces are selected by the frozen baseline artifact/capability, task, environment, runner and manifest. They do not need the future `release_id`.
2. `after` traces still require the release link and candidate artifact binding.
3. Failure comparison uses `n_failed / n_observed`, while `n_total`, unknown counts and denominators remain in the result. Unpaired windows with different sample sizes return `NO_CLEAR_CHANGE` instead of a false regression.
4. Any `SIMULATED_TRACE` causes `SIMULATION_ONLY / DEMONSTRATION_ONLY`.
5. Trace and event content participates in the source fingerprint, so a late event invalidates the cached observation and recomputes the same observation identity.

The regression test uses 100 before samples with 10 failures and 200 after samples with 15 failures. It reports rates `0.10` and `0.075`, but does not claim improvement because the unpaired denominators differ.

## Frozen comparison identity

`FrozenComparisonManifest` is persisted by additive SQLite migration v9 in `comparison_manifests`. It freezes:

- baseline and candidate artifact hashes;
- baseline and candidate capability versions;
- task suite/category, runner, agent/provider and environment;
- capture window, pairing strategy, minimum samples and unknown-rate limit;
- experiment/correlation identity and a canonical manifest hash.

The observation result retains the manifest, its hash, included/excluded trace IDs, per-trace digests, denominators, error/tool/retry/duration metrics and source tiers.

## Real Codex probe

The reusable entry point is `scripts/m6_real_agent.ps1`. It creates only `.test-m6/` state, registers the repository's no-side-effect `fixtures.mcp_test_server` in the isolated Codex home, runs `codex exec --json --ephemeral --sandbox read-only`, and restores the caller's environment variables in `finally`.

The probe observed:

| Field | Result |
| --- | --- |
| Codex | `codex-cli 0.163.0` |
| MCP registration | `PASS_REPORTED` in isolated home |
| Host invocation | real CLI started and emitted thread/turn/error JSONL |
| Provider response | `401 Unauthorized: Missing bearer` on WebSocket and HTTPS fallback |
| Tool calls | `0` |
| Oracle | `unknown`, because no task output was available |
| Execution status | `BLOCKED_ENV` after environment-error classification |
| Skill lifecycle | `NOT_OBSERVABLE`; no Codex public event was observed |

The failure is retained as redacted summaries/digests in `.test-m6/`; raw prompt, credentials and full provider output are not persisted by the runner. A host trace is not promoted to a successful task sample, and the run is not labelled `REAL_TASK_EXECUTED`.

## Adapter field matrix

| Evidence | Current source | Status |
| --- | --- | --- |
| Codex thread/turn/item/error JSONL | `codex exec --json` | observed when the host starts |
| MCP `initialize`/`tools/list`/`tools/call` | Codex-to-fixture session | blocked before tool dispatch by provider auth |
| `trace_id`, event refs, case/pair/experiment IDs | local M1 store and `real_agent_runs` | persisted |
| artifact/capability binding | runner input plus frozen manifest | verified only when the manifest matches |
| Skill discovered/loaded/invoked | no public Codex event in this probe | `NOT_OBSERVABLE` |
| independent task correctness | caller-supplied Oracle only | unknown without a completed task |
| token/cost | no real host report available | `null` |

The adapter never infers Skill invocation from prompt words, disk presence, or a model message. Cross-source IDs remain scoped by workspace/run/session/transport; a bare JSON-RPC ID is not a verified join key.
