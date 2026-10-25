# M7 Codex Native Plugin Acceptance

## Current result

`M7_PLUGIN_STATUS=PARTIAL`.

The package, schema, local Marketplace, isolated install, enable/disable,
uninstall, Core-backed MCP server, Hook safety, and regression tests are
implemented. A real authenticated `codex exec` probe did not expose the
plugin MCP tools, and no trusted normal session produced a real Skill or Hook
event. Those host claims remain blocked/unverified.

## Gate matrix

| Gate | Result | Evidence |
| --- | --- | --- |
| G0 | PASS | `HEAD`/remote checked; task prompts and unrelated files preserved. |
| G1 | FEATURE_MISMATCH / conservative path | Portable package is supported; current CLI runtime does not expose the local plugin MCP to `codex exec`; desktop is unverified. |
| G2 | PASS | `verify-m7-plugin.ps1 -Json`, manifest/Marketplace tests. |
| G3 | HOST_VERIFIED | Isolated `codex plugin add` and `plugin list --json`. |
| G4 | HOST_VERIFIED | Install, config-backed disable, re-enable, remove, and marketplace removal in isolated Homes. |
| G5 | PASS | Plugin package has no login command or provider credentials. Existing authenticated Home was reused for the probe. |
| G6 | PASS | Windows space/non-ASCII path tests and `${PLUGIN_ROOT}`/`PLUGIN_DATA` separation. |
| G7 | UNIT_TESTED | Source MCP initialize/list/call and installed launcher path tested; real Codex host call remains blocked. |
| G8 | BLOCKED_ENV | `codex exec` reported `workshop_health` unavailable. |
| G9 | BLOCKED_ENV | Same host boundary; no real Issue query in Codex. |
| G10 | BLOCKED_ENV | Hook package tests pass; trusted plugin Hook event not observed. |
| G11 | NOT_OBSERVABLE | No supporting host event record was available for plugin Hook correlation. |
| G12 | PASS | Hook is fail-open; malformed/oversized/retention cases do not block the host. |
| G13 | PASS | Summary-only records, bounded retention, source-scoped Core reads, and no raw payloads. |
| G14 | BLOCKED_ENV | Skill invocation was not observable in the CLI probe. |
| G15 | PASS | Empty issue ledger returns `NO_ACTIONABLE_ISSUE`. |
| G16 | PASS | Core issue/evidence/version summaries are exposed without duplicating rules. |
| G17 | PASS | No write tools are bundled; release automation is disabled. |
| G18 | PASS | Existing 10-version SQLite schema is reused; no migration was added. |
| G19 | UNVERIFIED | No reliable host on/off performance pair was available. |
| G20 | PASS | Bounded, redacted Hook data and isolated plugin data path. |
| G21 | PASS | README, install/uninstall/verify scripts, and this Runbook are present. |
| G22 | BLOCKED_ENV | CLI Marketplace is verified, daily plugin tool runtime is not exposed by `codex exec`. |
| G23 | UNVERIFIED | Codex desktop was not available for this run. |
| G24 | PARTIAL | M6.2 failure evidence remains separate; no false success promoted. |
| G25 | PASS | M7 tests plus full M0–M6.1 regression pass. |
| G26 | PASS or BLOCKED_ENV | Depends on final remote SHA verification; use `M7_Acceptance.json`. |

## Reproduction

```powershell
$env:PYTHONPATH = (Resolve-Path 'servers').Path
python -m unittest tests.test_m7_plugin -v
powershell -NoProfile -File .\scripts\verify-m7-plugin.ps1 -Json
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1 -CodexHome .test-m7\codex-home
codex plugin list --json
powershell -NoProfile -File .\scripts\uninstall-m7-plugin.ps1 -CodexHome .test-m7\codex-home -RemoveMarketplace
```

The `codex exec` result is evidence of the current host boundary only; it is
not evidence that the MCP server is broken.
