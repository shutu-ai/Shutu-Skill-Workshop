# M6 Windows operations runbook

All M6 operations below are read-only with respect to user Skills, MCP settings, OpenCode global configuration and production targets. Runtime state belongs under `.test-m6/`, which is ignored by Git.

## Preflight

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
$env:PYTHONPATH = 'servers'
python --version
codex --version
codex exec --help
codex mcp --help
opencode --version
opencode plugin --help
git status --short
```

Record the versions and the pre-existing dirty list before editing. Do not copy an existing `CODEX_HOME`, auth file, cookie or API key into the isolated probe home.

## Local Codex/MCP probe

Create a task input file containing no secret or business data, for example:

```powershell
New-Item -ItemType Directory -Force .test-m6 | Out-Null
@'
{"instruction":"Use the shutu-fixture MCP echo tool to echo m6-real","text":"m6-real"}
'@ | Set-Content -Encoding utf8 .test-m6\task-input.json
```

Run the explicit start/configure/execute path:

```powershell
$env:PYTHONPATH = 'servers'
.\scripts\m6_real_agent.ps1 `
  -TaskInputPath .test-m6\task-input.json `
  -Workspace .test-m6\agent-workspace `
  -CodexHome .test-m6\codex-home `
  -CaseId m6-real-echo `
  -Side baseline `
  -ArtifactHash ('b' * 64) `
  -CapabilityVersionId baseline-v1 `
  -PairId m6-real-pair `
  -ExperimentId m6-real-experiment `
  -TaskCategory m6-real-task
```

The script registers only `tests.fixtures.mcp_test_server` in the supplied `CODEX_HOME`, runs no background process, and restores `CODEX_HOME`/`PYTHONPATH` when it exits. Inspect the returned evidence for `execution_status`, `trace_ids`, `event_refs`, `artifact_hash`, `case_id`, `oracle_result` and `missing_fields`. A provider/transport error must remain `BLOCKED_ENV`; it is not `REAL_TASK_EXECUTED`.

## Freeze and observe

Prepare a manifest JSON with baseline/candidate artifact and capability hashes, task suite/category, runner, environment, sample limits and correlation identity. Freeze it before collecting the comparison:

```powershell
python -m shutu_workshop --workspace .test-m6\workspace `
  release freeze-manifest <RELEASE_ID> --manifest .test-m6\comparison-manifest.json --json

python -m shutu_workshop --workspace .test-m6\workspace `
  release observe <RELEASE_ID> `
  --before-start 2026-01-01T00:00:00+00:00 `
  --before-end 2026-01-02T00:00:00+00:00 `
  --after-start 2026-01-02T00:00:00+00:00 `
  --after-end 2026-01-03T00:00:00+00:00 `
  --manifest-id <MANIFEST_ID> --min-samples 2 --json
```

Before traces need the frozen baseline binding, not a future release ID. Do not use a bare JSON-RPC ID or time proximity as an exact join. Check denominators, unknown rates, exclusions and source tiers before interpreting a signal.

## Record feedback

```powershell
python -m shutu_workshop --workspace .test-m6\workspace `
  feedback record --observation <OBSERVATION_ID> --json
python -m shutu_workshop --workspace .test-m6\workspace `
  feedback list --release <RELEASE_ID> --json
```

Issue/Diagnosis/Proposal/Evaluation IDs are optional and are retained only when their database rows and trace references verify. A regression requests human review or the existing controlled rollback path; no automatic rollback or deployment is performed.

## OpenCode diagnosis

```powershell
opencode --version
opencode plugin list
opencode debug config
opencode debug paths
opencode run --help
```

Run any local `.opencode/plugins` experiment only in `.test-m6\opencode-workspace`. Separate plugin load, `observer.ready`, MCP `tools/list`, Provider tool support, permission/tool visibility, model response and session step evidence. A missing Provider credential or unavailable tool-calling path is `OPEN_CODE_REAL_TOOL_HOOK=BLOCKED_ENV`; do not replace it with a mock hook or manual POST.

Context7 is not required for the local run. If a documentation client is available later, use a temporary process-scoped proxy and remove it afterwards; never write a global proxy setting:

```powershell
$env:HTTP_PROXY = 'http://proxyae.huawei.com:8080'
$env:HTTPS_PROXY = 'http://proxyae.huawei.com:8080'
# run the documentation client here
Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY -ErrorAction SilentlyContinue
```

## Cleanup and recovery

The probe writes only `.test-m6/`. After retaining the redacted evidence needed for review, it may be removed explicitly:

```powershell
Remove-Item -LiteralPath (Resolve-Path .test-m6) -Recurse -Force
```

Do not remove user-level Codex/OpenCode state. M5 rollback remains an explicit, reviewed operation in the existing isolated release workflow. The final report must state the local commit and remote HEAD separately.
