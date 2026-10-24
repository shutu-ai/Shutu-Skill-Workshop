# M4 operations runbook

## Local diagnosis and review

```powershell
$env:PYTHONPATH = 'servers'
python -m shutu_workshop --workspace .test-m4 diagnose new --limit 50 --json
python -m shutu_workshop --workspace .test-m4 issue list --status open --json
python -m shutu_workshop --workspace .test-m4 issue show <issue-id> --json
python -m shutu_workshop --workspace .test-m4 evolution diagnose <issue-id> --provider fake --json
python -m shutu_workshop --workspace .test-m4 evolution proposals <issue-id> --json
python -m shutu_workshop --workspace .test-m4 evolution review <proposal-id> --decision accept-for-eval --expected-row-version 0 --json
python -m shutu_workshop --workspace .test-m4 eval run --proposal <proposal-id> --suite m4-local-fixture-v2 --mode fixture --json
python -m shutu_workshop --workspace .test-m4 eval report <eval-id> --json
```

Use a copied `.test-m4/` workspace for experiments. The default provider is
offline FakeProvider. Do not pass production paths or credentials into a
bundle. Re-run `diagnose new` after a late event; it uses the ruleset
checkpoint and reconciliation cursor.

## Trusted local Fixture comparison

The old proposal produced by FakeProvider is advisory-only and intentionally
has no executable artifact. To run the bounded Fixture, attach a checked and
hashed local artifact, then review it again:

```powershell
$env:PYTHONPATH = 'servers'
python -c "from shutu_workshop import ProposalService, local_fixture_candidate_payload; s=ProposalService('.test-m4'); p=s.get('<proposal-id>'); p=s.revise(p.proposal_id, {**p.payload, **local_fixture_candidate_payload()}, expected_row_version=p.row_version); p=s.review(p.proposal_id, 'ready-for-review', expected_row_version=p.row_version); print(s.review(p.proposal_id, 'accept-for-eval', expected_row_version=p.row_version).proposal_id)"
python -m shutu_workshop --workspace .test-m4 eval run --proposal <proposal-id> --suite m4-local-fixture-v2 --mode fixture --json
```

The trusted result must show `IMPROVED` together with
`evidence_tier=FIXTURE_EXECUTED`. Result rows must contain
`provenance=fixture_executed`, a runner identity, an artifact hash, a task input
hash, and an output digest. `m4-fixture-bad-v2` is the negative control and
must return `REGRESSED`.

Do not use `fixture_outputs`, `candidate_outputs`, or `baseline_outputs` as a
shortcut. They are retained only as `imported_unverified` compatibility data
and cannot create a publishable verdict. A missing artifact, missing Holdout,
failed Runner, or incomplete observation is `INCONCLUSIVE`.

To inspect evidence without exposing raw output:

```powershell
python -c "from shutu_workshop import M1Database; db=M1Database('.test-m4'); c=db.connection(); rows=c.execute('SELECT run_id, status, verdict, evidence_tier, execution_manifest_hash FROM evaluation_runs ORDER BY started_at').fetchall(); print([dict(r) for r in rows]); c.close()"
```

The `execution_manifest_hash` changes when the Suite, Baseline/Candidate
artifact, Runner/Oracle identity, environment, or task input changes. Only a
completed run with complete Fixture/Real evidence can be reused; blocked,
inconclusive, static-failed, and imported history are never cache hits.

## Provider and evaluation controls

Use an explicit HTTP provider only when an authorised local endpoint is
available:

```powershell
$env:SHUTU_DIAGNOSIS_ENDPOINT = 'http://127.0.0.1:PORT/diagnose'
$env:SHUTU_DIAGNOSIS_API_KEY = '<process-only value>'
python -m shutu_workshop --workspace .test-m4 evolution diagnose <issue-id> --provider http-json --model <model>
```

The key is read by the process and is not written to SQLite. If the endpoint
is unavailable, report `LLM_REAL_RUN=BLOCKED_ENV` or `UNVERIFIED`; do not
relabel FakeProvider output as a real run. `eval --mode real` records
`BLOCKED_ENV` and performs no external action.

If Context7 is used later for documentation lookup, route it through the
corporate proxy in the current process rather than assuming direct egress:

```powershell
$env:HTTP_PROXY = 'http://proxyae.huawei.com:8080'
$env:HTTPS_PROXY = 'http://proxyae.huawei.com:8080'
$env:ALL_PROXY = 'http://proxyae.huawei.com:8080'
```

## Recovery and rollback

- A failed analysis run has no completed checkpoint; rerun the same bounded
  command.
- A stale proposal must be revised and reviewed again. Never apply its old
  candidate hash to a new Issue or CapabilityVersion.
- SQLite migrations are additive and idempotent. Back up the local DB before
  manual repair; do not delete Issue tombstones or evaluation rows as a
  shortcut.
- A fixture regression is a release signal for the candidate, not permission
  to modify the live Skill/MCP artifact.
- The historical M4 `3/5 -> 5/5 IMPROVED` is retained as
  `LEGACY_UNTRUSTED_EVAL`; use `docs/M4_3_Acceptance.json` for the integrity
  closure and do not present the old score as release evidence.

## Git handoff

Commit only M4 files. Verify the remote head after pushing. If direct GitHub
push fails, retry with:

```powershell
git -c http.proxy=http://proxyae.huawei.com:8080 push origin main
git -c http.proxy=http://proxyae.huawei.com:8080 ls-remote origin refs/heads/main
```
