# M5 Controlled Release and Post-Release Observation

基线：`a39744bd0dda8db1476a4f5382418413698a12da`。本阶段只把 M4 的人工审核与可信评估接到 M0 的本地 Skill 文件发布链；目标范围固定为 `isolated_workspace`，不触碰真实 Agent、MCP、SSH、SQL、SMS 或用户全局配置。

## Contract / gap table

| 已存在 | 需复用 | 需新增 | 不可复用 |
|---|---|---|---|
| M4 `evolution_proposals`、`evaluation_runs`、`EvidenceTier`、Runner/Oracle | M0 `SkillWorkshop.test_proposal/apply_proposal/rollback`、文件锁、release journal、Version lineage | v8 additive tables、`ArtifactManifest`、File-backed Runner、Eligibility、ReleasePlan/Approval/Link、Observation | M4 内置 `m4-fixture-*` 不能为真实文件放行；M0 `Proposal` 与 M4 `Proposal` 不是同一类型 |
| M1 `TraceStore`、M3 `issue_occurrences`、CapabilityVersion | SQLite WAL、`BEGIN IMMEDIATE`、row version、现有 hash 算法 | before/after window 与摘要快照 | `IMPROVED` 字符串、预生成答案、LLM 自评、advisory 文本不能变成可执行 Patch |

## Trusted boundary and artifact lineage

M5 只接受 M4 Proposal payload 中版本化的结构化契约：

```json
{
  "file_patch": [{
    "path": "scripts/validator.py",
    "old_string": "...",
    "new_string": "...",
    "replace_all": false,
    "old_sha256": "optional source-file hash"
  }]
}
```

只允许 `SKILL.md`、`references/`、`templates/`、`scripts/`、`assets/`；路径、symlink、绝对路径、模糊替换和 advisory-only 类别都会拒绝。`FileArtifactService` 从实际 baseline 目录复制出不可变 baseline snapshot 和 candidate 目录，计算包含相对路径与文件内容的确定性树 hash，并写入 `.skill_workshop/m5-artifacts/<proposal>-<revision>.json`。Manifest 固定：

`proposal revision + proposal candidate hash + patch digest + baseline artifact hash -> candidate artifact hash`。

Eligibility 同时核对 proposal/evaluation/manifest/target 的版本和 hash。`ISOLATED_ARTIFACT_EXECUTED` 是新证据等级，和 `FIXTURE_EXECUTED`、`REAL_TASK_EXECUTED` 分离；fixture 结果没有真实文件血缘时永远 `DENIED`。

## File-backed execution

`FileBackedIsolatedRunner` 只把脱敏 task input、allowed tools、资源限制和 candidate artifact ref 传给 Runner；expected output 只在独立 Oracle 侧的 EvaluationSuite 中存在。Runner 用受限 Python 子进程读取 `scripts/validator.py`，校验启动前树 hash、输出大小和超时。执行失败、超时、崩溃、篡改或非 JSON 输出是未知，不是成功。

M5 的本地 Skill suite 有一个修复案例和一个 holdout。Good Candidate 的文件内容确实改变第一个案例，且不破坏 holdout；Bad Candidate 使 holdout 失败，结果为 `REGRESSED`。这只是隔离文件执行证据，不是 Agent 行为改善证明。

## Eligibility and plan states

`ReleaseEligibilityService.assess` 的正向条件是：proposal 已人工审核且 evaluated、evaluation 已完成且 `IMPROVED`、证据等级为 `ISOLATED_ARTIFACT_EXECUTED`、baseline/candidate/target/manifest hash 一致、target scope 为 `isolated_workspace`，并且存在与这些指纹绑定的人工批准。缺批准时返回 `NEEDS_EVIDENCE`，而不是放行。

状态约束：

```text
draft -> eligible -> approved -> executing -> completed
                                      |-> failed/recovering
completed -> rolled_back
```

所有迁移写入 `release_plan_events`。plan 的乐观 `row_version`、唯一 idempotency key (`plan + target + candidate + expected base`) 和 SQLite 唯一约束共同防止并发审批/发布产生两个结果。live evaluation 的 `running` 行不会被当作缓存成功；lease 过期后可在同一 run 上重试，仍保留不可信历史状态。

## v8 persistence

M1Database 只做 additive migration 8，保留 v1-v7 与历史 `LEGACY_UNTRUSTED_EVAL`：

- `release_plans`：冻结 proposal/evaluation/target/artifact/manifest 与资格快照；
- `release_approvals`：reviewer、时间、准确 plan/evaluation/candidate/target hash；
- `release_links`：M5 release 与 M0 `operation_id/version_id`、rollback version 的显式关联；
- `release_plan_events`：每次状态迁移的脱敏证据；
- `release_observation_runs`：release/window 幂等摘要。

M0 bridge 只把 `file_patch` 转成 M0 `Patch`，重新走 M0 `test_proposal`、candidate fingerprint、journal、文件交换、版本提交和 rollback。M5 不实现第二套文件替换算法。M0 已完成而 M5 尚未落库时，新进程根据 M0 journal、目标 hash 和 plan id 对账为 `completed`。

## Observation

`ReleaseObservationService` 只在离线/手动分析阶段读取 M1 Trace 与 M3 occurrence。它按 `release_id`、phase、artifact hash、时间窗、task category、environment、runner 和 verified bridge 过滤，保留纳入 trace 与排除理由，计算成功/失败/未知、Issue occurrence、tool call、retry、median/p95、完整率；token/cost 没有真实观测时是 `null`。

结论为 `INSUFFICIENT_EVIDENCE`、`NOT_COMPARABLE`、`NO_CLEAR_CHANGE`、`REGRESSION_SIGNAL` 或 `IMPROVEMENT_SIGNAL`；正向 signal 另标 `SUPPORTED_IMPROVEMENT`，不是因果证明。无 after Trace 必须是 `INSUFFICIENT_EVIDENCE`。同一窗口只有在 source Trace fingerprint 未变时复用；晚到 Trace/事件会触发重新分析。回滚只产生新的 M0 version 和后续 observation window，不改写旧结果。

## Explicit non-goals

本阶段不实现真实 Agent Candidate 对照、OpenCode tool hook、官方 MCP SDK interop、生产 target 发布、全局代理/Git 配置、后台监控服务或自动回滚。真实能力继续分别记为 `BLOCKED_ENV`/`UNVERIFIED`。
