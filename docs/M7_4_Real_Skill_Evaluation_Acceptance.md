# M7.4 Real Skill Evaluation Acceptance

Baseline was `7fb28912b50cf8aa627d626aec032b867a879782` (M7.3 remote main).
The acceptance JSON is the machine-readable source for the final values; the
final and remote SHA fields are intentionally `null` in that file because the
file is part of the commit being verified.

## Result

| Gate group | Result | Evidence |
|---|---|---|
| G0-G2 | PASS | shared M1/M4/M6/M7.3 code paths and full regression |
| G3-G6 | PASS | `tests.test_m74_real_skill_evaluation` routing negatives |
| G7/G15/G17/G18 | PASS | frozen artifact validation, no-skill leak rejection, separate evidence tiers |
| G8-G14 | BLOCKED_ENV/PARTIAL | real runner is implemented, isolated Provider preflight is unauthenticated |
| G16/G23 | PASS | scoped MCP event IDs and no replay path |
| G19/G20 | PASS | redacted preflight, no credential copy, proxy values never persisted |
| G21/G22 | PASS/PARTIAL | local stdio call/result probe; externally owned Codex close remains partial when EOF is unknown |
| G24 | UNVERIFIED | no five-group paired performance run was claimed |
| G25/G26 | PASS | read-only quality query and release-disabled paths |
| G27 | PASS | targeted and full regression plus compileall |
| G28/G29 | PASS | design/runbook/JSON, scoped commit, remote SHA check |

`M7_4_ENGINEERING_STATUS=PASS` means the implementation gates pass. The
overall result remains `PARTIAL` because the real Codex three-arm Pilot could
not start in an authenticated isolated HOME. Native Skill lifecycle truth and
production benefit therefore remain `NOT_OBSERVABLE`/`BLOCKED_ENV`, even if
local fixture results improve.

## Evidence references

- routing negatives: `tests.test_m74_real_skill_evaluation`
- real runner identity and no-skill leak checks: same test module;
  `RealCodexThreeArmRunner._validate`
- isolated preflight: `.test-m74` local ignored artifact, digest recorded in
  `M7_4_Acceptance.json`; status `NOT_AUTHENTICATED`, error class
  `AUTHENTICATION`, no credentials persisted
- real Codex CLI version: `codex-cli 0.163.0`
- historical M7.3 fixture report remains queryable and is not relabelled as a
  M7.4 real-task report

## Limitations

No native `skill.loaded`/`skill.invoked` event was available from the current
Host. No real 9-run Pilot, five-group observer performance comparison, or
Desktop lifecycle acceptance was claimed. No production Skill/MCP was changed
or auto-released.
