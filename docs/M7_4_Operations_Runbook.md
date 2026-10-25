# M7.4 Operations Runbook

## Safe Windows preflight

Use a project-controlled short directory and an explicitly isolated HOME:

```powershell
$env:PYTHONPATH = (Resolve-Path .\servers).Path
python -c "from shutu_workshop.adapters.real_agent import codex_preflight; import json; print(json.dumps(codex_preflight('.test-m74/workspace', codex_home='.test-m74/codex-home', cwd='.test-m74/workspace', timeout=45), ensure_ascii=False))"
```

The result records CLI version, auth state, provider error class, proxy
variable presence, and configuration fingerprints only. It never records
token values, cookies, auth databases, full config, or proxy values. A 401 is
diagnosed as provider/auth-scope or route mismatch; do not clear a working
system proxy as a reflex. If the isolated HOME is unauthenticated, the user
must run normal `codex login` in that HOME and rerun the smoke test. Never
copy the default HOME credentials.

## Real Pilot

Run only after the isolated `READY` smoke passes. Prepare independent A/B/C
workspaces and HOME directories, verify the frozen artifact hashes, then run
the three categories across all three arms. The external Oracle runs the same
tests against each workspace. Stop on Provider 401, timeout, missing result,
or condition mismatch and retain `BLOCKED_ENV`/`unknown` evidence.

## MCP Observer

Use only an explicitly owned local stdio fixture. The Observer forwards bytes
without replay and records trace-scoped event IDs. A normal stdin EOF with all
responses observed is `SESSION_CLOSE=COMPLETE`; pending requests, recording
errors, timeout, cancellation, or externally owned shutdown is `PARTIAL`.
Disable the temporary registration after the probe and do not modify the
default production registration.

## Query and recovery

Quality reads are available through:

```powershell
python -m shutu_workshop.cli --workspace .test-m74/workspace quality summary --kind skill_real_three_arm --json
python -m shutu_workshop.cli --workspace .test-m74/workspace quality detail --json
```

Unknown samples and missing lifecycle evidence remain visible. There is no
automatic Skill publication, production mutation, rollback, or second Agent
for daily use in this stage.
