# M6.1 Artifact Activation and Paired Experiment Design

## Contract

`ArtifactActivationRecord` is persisted with the real-agent run and contains:

`experiment_id`, `pair_id`, `case_id`, `side`, `immutable_artifact_ref`, `capability_version_id`, `expected_artifact_hash`, `actual_prelaunch_hash`, `actual_runtime_hash_or_unknown`, `server_command_fingerprint`, `mcp_server_instance_id`, `mcp_connection_scope`, `codex_home_config_fingerprint`, `runner_version`, `environment_fingerprint`, `activation_status`, `activation_evidence_refs`, and `activated_at`.

Hashes come from the artifact tree bytes at activation time. A declared hash is never accepted as proof of activation. Status is one of `VERIFIED`, `MISMATCH`, `UNVERIFIED`, or `BLOCKED_ENV`.

## Side activation flow

1. Resolve the side-specific artifact root and calculate its actual tree hash.
2. Reject a declared/actual mismatch before starting an agent.
3. Register the side-specific server in a side-specific isolated `CODEX_HOME`.
4. Run a real local stdio `initialize` → `tools/list` → `tools/call` exchange and record scoped MCP event references.
5. Recalculate the artifact hash and require it to remain stable.
6. Launch Codex with the side-specific command, cwd, home, and manifest. Persist only redacted evidence.

The baseline and candidate fixtures are different files and different processes:

| Side | Artifact | Actual tree hash | MCP instance evidence |
|---|---|---|---|
| baseline | `.test-m61/artifacts/baseline` | `6d35d3cfcdf42cf2a1f54a0b751f7336afa91524fa1fa44e4a6056160201a88d` | separate `mcp-server_*` and `mcp-activation_*` scope per run |
| candidate | `.test-m61/artifacts/candidate` | `d5ac95000637bb82cc9bb56e74a96811a7e0e670914f7c94444ad32e6f089ec7` | separate `mcp-server_*` and `mcp-activation_*` scope per run |

`run_pair()` passes the real roots, commands, and homes independently; it does not switch versions by changing only a label or hash.

## Evidence tiers

- `REAL_AGENT_OBSERVED`: Codex emitted host trace events, but no successful task is implied.
- `REAL_MCP_TOOL_EXECUTED`: an observed real MCP call/result exists.
- `REAL_ARTIFACT_ACTIVATED`: the frozen artifact and side MCP activation were verified.
- `REAL_TASK_EXECUTED`: activated artifact, real MCP call/result, independent Oracle, and comparable environment all exist.
- `FIXTURE_EXECUTED`, `ISOLATED_ARTIFACT_EXECUTED`, and `SIMULATED_TRACE` never promote to `REAL_TASK_EXECUTED`.

The current pair reached `REAL_AGENT_OBSERVED` plus verified activation only. Codex made no MCP tool call because the provider/auth layer was blocked; the independent Oracle therefore returned `unknown`.

## Three-case pair

`scripts/m6_1_real_pair.py` runs `target`, `normal-regression`, and `holdout-boundary` for both sides. Its Oracle inspects only observed MCP result events, not task text, expected output, agent self-evaluation, or server labels. The final run contains 3 attempted pairs / 6 side runs, six `BLOCKED_ENV` results, six `VERIFIED` activations, zero Codex MCP calls, and zero successful task results.

## Negative assertions

Covered by `tests/test_m61_real_agent_closure.py`:

- declared candidate hash differs from bytes → activation is rejected;
- artifact mutation after freeze → activation is rejected;
- hash-only compatibility path → `UNVERIFIED` and cannot persist a successful task;
- MCP registration without a Codex tool call → not a real task pass;
- same JSON-RPC ids on independent sides remain scoped and do not exact-join (`FALSE_EXACT_JOIN_COUNT=0`);
- pre-release experiment identity is linked to a later release without rewriting old trace rows.

## Schema and historical identity

SQLite uses one additive migration, v10. It adds activation/evidence columns to `real_agent_runs` and separate `experiment_identities` / `experiment_release_links` tables. `freeze_experiment_identity()` requires a pre-release identity with no release id; `link_experiment_identity()` adds the later relationship and leaves historical traces unchanged.
