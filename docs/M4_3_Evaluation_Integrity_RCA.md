# M4.3 Evaluation Integrity RCA

## 结论

M4.2 的 `EvaluationService.run()` 把 Oracle 的 `expected_output` 回填成了
Candidate 的执行输出。它还把 `baseline_output` 当成了本轮 Baseline 结果，并
按 `(candidate_id, suite_hash, mode)` 复用评估。这样可以得到绿色的
`3/5 -> 5/5 IMPROVED`，但没有证明 Candidate 被执行过。

该历史结果保留用于审计，状态为 `LEGACY_UNTRUSTED_EVAL`，不再具有发布资格。

## 最小复现

在旧基线上执行：

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m43_evaluation_integrity -v
```

在修复前，以下三个断言分别失败：

1. 空 Candidate 输出仍得到 `IMPROVED`；
2. 只改变 `candidate_outputs` 的执行输入仍命中旧 `run_id`；
3. 缺失 Holdout 输出仍能得到非 `INCONCLUSIVE` 结果。

红灯证据来自 `tests/test_m43_evaluation_integrity.py`，而不是来自旧的 M4
绿色测试。旧回填路径位于 `servers/shutu_workshop/evolution/evaluation.py`
的 Candidate 输出选择分支；旧缓存路径位于 `_existing_run()`。

## 根因

| 概念 | 旧实现的混淆 | M4.3 处理 |
|---|---|---|
| Expected | 同时被当作 Runner 输出 | 只由 Oracle 读取 |
| Candidate Artifact | 可以没有 | 必须引用已存在且哈希校验的 Fixture 工件 |
| Execution Output | 可以由调用方字典或默认值生成 | 只能来自 Runner，缺失就是 unknown |
| Oracle Verdict | 由输出相等直接推导 | 仅对可信 Runner 完成结果判定 |
| Evaluation Evidence | 没有执行来源等级 | 保存 provenance、工件/输入摘要和 EvidenceTier |

另一个独立问题是 Holdout 没有可验证的 Runner 边界。M4.3 的 Runner 只接收
`ExecutableCaseInput` 和 `ExecutableCandidate`，不接收 `expected_behavior`、
另一侧输出或 Oracle 对象。

## 修复决策

- 引入 `LocalFixtureRunner`，Baseline 与 Candidate 对同一 `task_input` 分别执行；
- 忽略旧 `fixture_outputs`，旧 `candidate_outputs`/`baseline_outputs` 只作为
  `imported_unverified` 兼容输入，不能产生可发布 `IMPROVED`；
- 未执行、超时、异常、工件缺失或哈希不匹配均不能产生 PASS；
- 评估清单包含两侧工件、套件、Runner、Oracle、环境和本次执行输入摘要；
- SQLite v7 additive migration 保存清单哈希、执行证据和 EvidenceTier；
- 可信缓存只复用完成且有 Fixture/Real execution evidence 的结果；
- 并发同键由进程锁和 SQLite 唯一清单索引共同约束；
- 历史 M4 结果只读保留，不覆盖、不重算成新证据。

## 可信边界

`IMPROVED + FIXTURE_EXECUTED` 只表示本地隔离 Fixture 中的实际行为更好，
不表示真实 Agent、LLM 或生产 MCP 已被验证。真实任务和真实 LLM 仍保持
`BLOCKED_ENV`/`UNVERIFIED`。
