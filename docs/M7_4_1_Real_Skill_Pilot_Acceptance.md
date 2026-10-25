# M7.4.1 Real Skill Three-Arm Acceptance

This closure keeps two conclusions separate:

- engineering evidence integrity: the runner rejects leaked or ambiguous Skill
  conditions, preserves pre/runtime/post hashes, and never lets one arm claim
  `IMPROVED`;
- real Pilot outcome: the Provider must be ready in three isolated HOME
  directories before any Codex arm is counted.

## Current acceptance

`codex-cli 0.163.0` is installed. A fresh project-controlled isolated HOME
preflight returned `NOT_AUTHENTICATED` and the real Provider smoke returned
`BLOCKED_ENV` with error class `AUTHENTICATION`. No credentials were copied and
no default HOME was changed. Therefore the planned 3 cases × 3 arms were not
started: actual attempts, completions, and Oracle-verifiable runs are all 0.

The native Host does not expose a verifiable `skill.loaded` or `skill.invoked`
event in this CLI path. Filesystem inventories are retained as
`STATIC_CONFIG_VERIFIED` evidence only; they are not promoted to Host truth.
The real runner calls the external Oracle only when the Host discovery record
is explicitly `HOST_OBSERVED` and matches the frozen Skill hash.

## Engineering gates

The implementation covers:

- isolated arm HOME enforcement and duplicate-HOME rejection;
- bounded effective Skill inventory across project `.agents/skills`, isolated
  HOME skills, upper-scope `.agents/skills`, and bounded plugin paths;
- no-Skill leakage, missing discovery path, hash mismatch, and same-name
  version/scope collision rejection;
- safe `completed + condition_unverified` handling with stable reasons;
- per-arm execution/Oracle evidence separated from the cross-arm comparison;
- explicit suite IDs, case IDs, sample counts, holdout regressions, and
  `PRODUCTION_BENEFIT_VERDICT=UNVERIFIED` for a small Pilot.

Engineering verification completed with the M7.4.1 module at `10/10`, the
full suite at `112/112`, `skipped=0`, and `compileall` passing.

One controlled direct/Observer stdio pair completed with 3 responses each,
zero observation failures, zero pending requests, and Observer session close
`COMPLETE`. The sample is intentionally reported as `PARTIAL_SAMPLE`; P50/P95
and production overhead claims are not made from one pair.

## Required next run after authorization

Authenticate each isolated HOME independently with the supported `codex login`
flow. Do not copy the default `auth.json`, cookies, or tokens. Run the READY
smoke once per HOME, then run `target`, `normal-regression`, and `holdout` in
all three arms. Only a complete set of nine runs with matching Host-observed
Skill inventories and independent Oracle results can produce a finite
`PILOT_SUITE_*` comparison. Production benefit remains `UNVERIFIED` even then.

See [M7_4_1_Operations_Runbook.md](M7_4_1_Operations_Runbook.md) for the
repeatable Windows procedure and recovery boundaries.
