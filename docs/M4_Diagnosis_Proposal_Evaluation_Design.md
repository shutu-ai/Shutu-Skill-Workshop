# M4 diagnosis, proposal, and evaluation design

M4 adds an offline-first path:

`Issue -> EvidenceBundle -> DiagnosisReport -> Proposal -> Review -> Evaluation -> Verdict`

The existing deterministic detector and Issue ledger remain the source of
stored facts. The new `servers/shutu_workshop/evolution/` package consumes
references and emits bounded advisory records. It never applies a Skill,
writes an MCP server, sends a production request, or changes an agent policy.

## Evidence Bundle

`EvidenceBundleBuilder` reads the current Issue, its Occurrences, referenced
Trace/Event rows, and CapabilityVersion snapshots. It validates every
`trace_id/event_id` pair and checks a referenced capability version against the
stored event. It stores only:

- Issue identity, row version, rule/category/status, and safe unknown fields;
- bounded Occurrence summaries and EvidenceRefs;
- event type/status/error class/tool/transport/observation-source summaries;
- content/schema hashes and bounded schema/description metadata; and
- explicit user constraints after the existing redaction policy.

Payload bodies, prompts, full arguments, user paths, bearer values, cookies,
API keys, and complete SQL are not sent to a provider. The bundle has a byte
budget and an `input_bundle_hash`; truncation is explicit and lowers diagnosis
confidence.

## Diagnosis provider contract

`DiagnosisProvider.diagnose(ProviderRequest) -> ProviderResponse` is the only
provider boundary. `FakeProvider` is deterministic and the default. The
optional `HTTPDiagnosisProvider` is explicit and reads its endpoint/model/key
from the current process or explicit config; credentials are never persisted.
No default code path opens a network connection. Timeout, output size, and
retry counts are bounded. Missing token or price data is stored as `null`
(`unknown`), never estimated.

The validator rejects malformed output, unbound observed facts, references not
in the bundle, out-of-bundle targets, destructive patch keys, credentials, and
prompt-injection markers. `DiagnosisReport` contains hypotheses and evidence
gaps only; `verified_cause` and `verified_fix` are forbidden. Reports are
idempotent by issue row version, evidence hash, provider/model, and config
hash. `--force` creates a new auditable report rather than overwriting one.

## Proposal lineage and review

Proposal records live in `evolution_proposals`, separate from the M0 manual
`proposals` table. Supported target types are `skill_instruction`,
`skill_script`, `mcp_description`, `mcp_schema`,
`mcp_implementation_advisory`, `agent_policy_advisory`, and
`environment_action`.

Each proposal stores issue/evidence/diagnosis IDs, capability version,
target hash, proposal version, candidate hash, reviewer, and row version. A
new target or payload produces a new candidate hash and clears the prior
evaluation. Review transitions are `draft -> ready_for_review ->
accepted_for_evaluation | rejected | needs_revision`; an evaluation may move
an accepted candidate to `evaluated`. Stale issue or capability evidence is
marked `stale` and must be revised. There is no `apply` operation.

## Evaluation layers

`StaticValidator` checks candidate structure, target category, path safety,
bounded payloads, and production-write markers. It does not prove quality.

`EvaluationService` then pairs the same suite cases for baseline and candidate
against an independent fixture Oracle. Correctness is primary; tool calls,
retries, duration, tokens, and cost are separate metrics. Token/cost fields
remain unknown when not observed. Holdout cases are retained in the suite.

The trusted `m4-local-fixture-v2` covers a Skill safe-path case plus MCP
`echo`, `add`, `error`, and an independent echo holdout. Baseline and Candidate
are actually executed by a bounded local Runner; a bad artifact is
`REGRESSED`, while incomplete observations are `INCONCLUSIVE`. The former
`m4-local-fixture` score is historical and `LEGACY_UNTRUSTED_EVAL`.
`mode=real` remains an explicit, side-effect-free `BLOCKED_ENV` record until a
separately authorised runner exists.

SQLite migration 6 creates separate tables for reports, proposals, suites,
candidates, runs, case results, and verdicts. Migration 7 adds execution
manifest identity, Runner/Oracle metadata, evidence tier, and bounded case
provenance. Only bounded summaries and output digests are persisted.
