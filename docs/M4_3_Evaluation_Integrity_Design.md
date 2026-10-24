# M4.3 Evaluation Integrity Design

## 执行边界

评估链路固定为：

```text
TaskCase.input_ref.task_input
  -> ExecutableCaseInput + ExecutableCandidate
  -> Runner
  -> bounded Execution Output + provenance
  -> independent Oracle(expected_behavior)
  -> EvaluationVerdict + EvidenceTier
```

Runner 不接收 `expected_behavior`、`baseline_output`、另一侧输出或 Oracle
对象。`LocalFixtureRunner` 只执行内存中的确定性工件，不访问生产 Skill、MCP
Server、用户配置或真实数据库业务表。

`LocalFixtureRunner` 的工件引用为：

- `m4-fixture-baseline-v2`：safe-path 与 add 的旧行为；
- `m4-fixture-candidate-v2`：修复 safe-path 和 add 的候选行为；
- `m4-fixture-bad-v2`：故意破坏 echo 的负候选。

每个引用都由 Runner 根据内置定义计算哈希并校验。Candidate Payload 只保存
工件引用和哈希，不保存标准答案或预生成输出。

## 证据契约

每个 `evaluation_case_results` 都保存有界摘要：

```text
run_id / case_id / side
execution_status / provenance
runner_id / runner_version / artifact_hash
task_input_hash / environment_fingerprint
output_digest / observation_completeness
```

`task_success` 只有 Runner `completed`、输出为对象且 provenance 为
`fixture_executed` 或 `real_executed` 时才有布尔值；否则为 `NULL`，
`oracle_verdict` 为 `unknown`。SQLite 不保存原始 Prompt、参数、凭据或完整
输出，只保存 digest 与错误摘要。

## 缓存身份

`evaluation_runs.execution_manifest_hash` 覆盖：

```text
baseline_artifact_hash
candidate_artifact_hash
candidate_hash
suite_hash
oracle_id / oracle_version
runner_id / runner_version / runner_config_hash
environment_fingerprint
mode
case_id + task_input_hash + allowed_tools + limits
legacy input digests (when supplied)
```

调用方注入的 `candidate_outputs`、`baseline_outputs`、`metrics` 会进入清单
摘要，但不会被信任为 Runner 结果，并使该次运行不可缓存。不可执行、
`BLOCKED_ENV`、`INCONCLUSIVE`、静态失败和缺少可信证据的旧记录也不会命中
可信缓存。SQLite v7 的唯一清单索引配合进程锁，防止同键并发制造多个完成结果。

## Verdict 与 EvidenceTier

```text
Verdict: IMPROVED | EQUIVALENT | REGRESSED | INCONCLUSIVE | BLOCKED_ENV
EvidenceTier: STATIC_ONLY | SYNTHETIC_OR_IMPORTED | FIXTURE_EXECUTED | REAL_TASK_EXECUTED
```

`IMPROVED + FIXTURE_EXECUTED` 仅限该 Fixture 套件；它不等价于 Real Task。
缺少任一门禁用例、Holdout 未执行、Runner 失败、直接答案注入或只提供建议的
Candidate，都只能是 `INCONCLUSIVE`/`BLOCKED_ENV`。

## 历史数据

v1-v6 表和历史 verdict 保持不变。v7 只增加执行身份、证据等级和有界摘要。
旧 M4 的 `3/5 -> 5/5` 原文仍可审计，但报告层标为
`LEGACY_UNTRUSTED_EVAL`，不能作为 M5 发布门禁输入，也不被新 Fixture 结果
覆盖。

M5 应同时检查 Verdict、EvidenceTier、来源、完整性、工件哈希、清单哈希和
人工批准；不能只检查字符串 `IMPROVED`。
