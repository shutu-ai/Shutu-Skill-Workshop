# M7.1 Acceptance

## Result

`M7_1_OVERALL_STATUS=PARTIAL`

The interactive CLI gate passes in the explicit compatibility mode:

- `M7_1_CLI_STATUS=PASS`
- `M7_1_CLI_INTEGRATION_MODE=COMPAT_MCP_SKILL_HOOKS`
- real `shutu-workshop.workshop_health` Tool Call/Result: `PASS`
- real `shutu-workshop.workshop_list_issues` Tool Call/Result: `PASS`
- real `workshop-review` Skill workflow with those MCP results: `PASS`

The overall result remains partial because the current CLI did not expose the
plugin's native MCP namespace, the plugin Hook surface was not installed in
`/hooks`, Desktop was unavailable, and the independent `codex exec` probe did
not produce a completed Tool Result.

## Gate table

| Gate | Result | Evidence |
|---|---|---|
| G0 baseline/dirty/host | `PASS` | `M7_1_Host_Probe.json`, Git metadata |
| G1 docs and capability matrix | `PASS` | official references plus matrix |
| G2 plugin install/enable | `PASS` | `plugin list --json`, installer/verify |
| G3 Native versus Compat distinction | `PASS` | Native host absence recorded; Compat named server |
| G4 interactive `workshop_health` | `PASS` | actual interactive Tool Call/Result |
| G5 interactive `workshop_list_issues` | `PASS` | actual interactive Tool Call/Result |
| G6 Skill workflow | `PASS` / lifecycle `NOT_OBSERVABLE` | explicit Skill run and MCP calls |
| G7 duplicate MCP registration | `PASS` | one matching `shutu-workshop` compat entry |
| G8 install/disable/enable/uninstall | `PASS` | isolated HOME script checks |
| G9 Hook trust and source | `HOST_UNSUPPORTED` | `/hooks`: Workshop PostToolUse `0/0` |
| G10 spool to Core importer | `PASS` by bounded tests | `test_m71_hook_to_core.py` |
| G11 real Hook to recent activity | `BLOCKED_ENV` | no host Hook event emitted |
| G12 real Hook to Issue | `BLOCKED_ENV` | no host Hook event emitted; controlled test passes |
| G13 source/session/request scope | `PASS` by importer tests | hashes and bounded source metadata |
| G14 no fabricated Skill invocation | `PASS` | invocation observability kept separate |
| G15 Desktop | `BLOCKED_ENV` | Desktop host unavailable |
| G16 `codex exec` | `HOST_UNSUPPORTED` | no completed Tool Result |
| G17 Hook overhead | `UNVERIFIED` | no real Hook stream |
| G18 privacy/fail-open/recovery | `PASS` | existing Hook tests plus importer tests |
| G19 no second Codex/provider auth | `PASS` | no second agent, no extra auth |
| G20 regression | `PASS` | `87/87` tests, skipped `0` |
| G21 Windows runbook | `PASS` | scripts and runbook |
| G22 Git remote | pending final handoff | final/remote SHA recorded after push |

## Explicit non-claims

Package installation, direct stdio tests, and controlled spool fixtures do not
prove Desktop support, native MCP support, Hook trust, or real Hook capture.
M6.2 remains independent `PARTIAL/INCONCLUSIVE`.
