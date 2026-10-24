# M3 Operations Runbook

## Scan new traces

Use an isolated workspace and the existing local database path:

```powershell
$env:PYTHONPATH = 'servers'
python -m shutu_workshop --workspace .test-m3 diagnose new --limit 100 --json
python -m shutu_workshop --workspace .test-m3 diagnose trace <trace-id> --json
```

`diagnose new` is bounded. A completed checkpoint with the same ruleset and
trace revision returns `skipped`; it does not increase occurrence counts.
Late events, trace state changes, or a ruleset version change produce a new
revision and reconcile the trace's old occurrences before inserting new ones.

## Query and review

```powershell
python -m shutu_workshop --workspace .test-m3 issue list --status open --json
python -m shutu_workshop --workspace .test-m3 issue show <issue-id> --json
python -m shutu_workshop --workspace .test-m3 issue status <issue-id> acknowledged
python -m shutu_workshop --workspace .test-m3 issue status <issue-id> resolved --expected-row-version 1
```

Review the evidence refs and source scope first. `owner_candidates` are
hypotheses. `unknown_fields` and `evidence_quality` are part of the decision;
do not promote an Issue to a Skill or server defect without independent
verification.

## False positives and reopen

- A successful MCP result is not a finding.
- A bare request ID, same tool name, or nearby timestamp cannot join sources.
- R03 needs a schema/version and explicit validation result; argument keys are
  only an insufficient-evidence audit signal.
- R05 ignores a still-running Trace.
- R06 requires all explicit expectation flags.

Manual `resolved` and `ignored` states are not silently overwritten. A later
new occurrence reopens the cluster as `open` and records the transition in
`issue_status_history`. Use `--expected-row-version` for automation so stale
operators fail safely.

## Privacy, cleanup, and rollback

Issue tables contain references, safe summaries, hashes, scopes, and bounded
metadata only. Do not copy raw JSONL, credentials, prompts, or the real user
database into `.test-m3/`. The test workspace is ignored by Git.

To remove disposable test state, delete only the explicitly named isolated
workspace after confirming it is not the production or user workspace. Do not
run `git clean`, `reset --hard`, or drop tables. There is no M3 destructive
rollback: restore the workspace/database from its own backup, or leave the
additive v4 tables in place. Existing M0/M1/M2 data is not rewritten by the
migration.

## Health checks

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m3_diagnostics -v
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
```

If analysis fails, inspect `analysis_runs.errors_summary`; retrying is safe
after the process exits because incomplete checkpoints are not treated as
completed.
