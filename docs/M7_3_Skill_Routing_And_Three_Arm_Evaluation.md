# M7.3 Skill Routing and Three-Arm Evaluation

## Routing labels

Routing cases are JSON-serializable `SkillSelectionLabel` values.  A minimal
label set contains:

```json
{
  "case_id": "target-1",
  "task_input_hash": "sha256-of-task-input",
  "target_skill_id": "skill:project:example:root-digest",
  "should_use": "yes",
  "expected_tools": [],
  "scenario": "target",
  "label_source": "human_review",
  "selection_mode": "NATURAL",
  "observed_invocation": null,
  "observation_evidence_tier": "NOT_OBSERVABLE"
}
```

`yes`, `no` and `review` are different labels.  `FORCED` is reserved for an
explicitly requested Skill condition and is never included in natural
precision/recall.  A document mention, script reference or a successful MCP
call is not a native invocation event.

CLI:

```powershell
$env:PYTHONPATH = "servers"
python -m shutu_workshop --workspace .test-m73\workspace quality routing --labels .test-m73\routing.json
```

When `observed_invocation` is missing, the result contains `N/A`,
`not_observable`, an unknown case list and the excluded forced count.

## Three arms

The runner requires three physically separate frozen roots:

| Arm | Root rule | Selection mode | Evidence |
|---|---|---|---|
| A / no_skill | no `SKILL.md` for the target | `DISABLED` | `FIXTURE_EXECUTED` or host tier |
| B / baseline | frozen published/current tree | `FORCED` | artifact hash |
| C / candidate | frozen candidate tree with a different hash | `FORCED` | artifact hash |

Cases must include `target`, `normal-regression` and `holdout`.  The runner
passes only the task input to `scripts/validator.py`; expected outputs and the
Oracle stay outside that process.  The Oracle returns a boolean or a bounded
mapping and is recorded by ID/version in the report.

Metrics include per-arm task success rate, sample size, missing-field reasons,
and the frozen artifact hashes.  Tool count and token/cost are `N/A` when the
runner does not expose them.  A fixture result can be `IMPROVED` inside the
demo report, but the production Skill benefit remains `INCONCLUSIVE` until a
real controlled baseline/candidate run with independent Oracle evidence is
available.

Negative guarantees covered by tests:

- a no-Skill arm containing `SKILL.md` is rejected;
- equal baseline/candidate hashes are rejected;
- expected answers are not inserted into runner input;
- unknown execution does not become success;
- forced samples do not raise natural recall.
