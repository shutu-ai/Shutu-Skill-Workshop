---
name: workshop-review
description: Review real Shutu Skill Workshop evidence, diagnose actionable issues, and propose safe next steps without changing or releasing anything.
---

# Workshop review

Use the Workshop MCP tools as the source of truth for this review.

1. Call `workshop_health` first. State whether data is observed, absent, stale, or not observable.
2. Call `workshop_list_issues`. If it returns `NO_ACTIONABLE_ISSUE`, say exactly that and do not invent an example issue.
3. For each relevant issue, call `workshop_issue_detail`; use its issue ID, evidence references, capability version IDs, and occurrence count.
4. When version lineage matters, call `workshop_capability_versions`. Use `workshop_recent_activity` only for bounded summaries, never to request raw payloads.
5. Separate the answer into Fact, Inference, and Proposal. A proposal is not an approved change. Do not call a write operation, publish, or edit a Skill/MCP configuration.
6. If evaluation or release evidence is relevant, call `workshop_evaluation_report` or `workshop_release_status` and preserve `unknown`, `INCONCLUSIVE`, `BLOCKED_ENV`, and `NOT_OBSERVABLE` exactly.

Keep the answer short: issue, evidence, impact, safest next step, and a reproducible command. Explain when the plugin lacks enough host observability and when an independent A/B Runner or normal Codex review is the right tool instead.
