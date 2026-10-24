# M4.3 Evaluation Integrity Acceptance

## 执行命令

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m43_evaluation_integrity -v
python -m unittest tests.test_m4_evolution -v
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
python -m shutu_workshop --help
git diff --check
```

## Gate 结果

| Gate | 结果 | 证据 |
|---|---|---|
| G0 | PASS | 三条旧逻辑红灯及 `M4_3_Evaluation_Integrity_RCA.md` |
| G1 | PASS | Recording Runner 只收到 `ExecutableCaseInput`/Candidate |
| G2 | PASS | 每个可信 PASS 都有 `fixture_executed`、工件哈希和输出 digest |
| G3 | PASS | 缺失输出、工件、哈希或执行结果返回 unknown |
| G4 | PASS | StaticValidator 与 Runner/Oracle 证据分层 |
| G5 | PASS | Baseline/Candidate 对同一 task input 分别执行 |
| G6 | PASS | candidate-v2 实际得到 `IMPROVED + FIXTURE_EXECUTED` |
| G7 | PASS | bad-v2 实际破坏 echo 后得到 `REGRESSED` |
| G8 | PASS | Holdout 缺失为 unknown 并阻止正向 verdict |
| G9 | PASS | 空 Runner、advisory-only、直接答案注入不能通过 |
| G10 | PASS | 执行输入摘要变化导致新 manifest/run |
| G11 | PASS | 并发同清单复用唯一完成 run |
| G12 | PASS | v7 additive migration，v1-v6 与历史行保留 |
| G13 | PASS | 旧 `3/5 -> 5/5` 标为 `LEGACY_UNTRUSTED_EVAL` |
| G14 | PASS | Verdict 与 EvidenceTier 独立返回 |
| G15 | PASS | M5 可按 tier/source/hash/approval 检查，不能只看 verdict |
| G16 | PASS | 全量 M0-M4 回归通过 |
| G17 | PASS | 只改评估代码、测试和文档；未触碰生产工件 |
| G18 | PASS | 最终命令核验提交与远端 HEAD；报告不写自引用 SHA |

## 可信结果

`m4-fixture-candidate-v2` 的输出来自本地 Runner 的实际计算：Baseline 为
3/5，Candidate 为 5/5，Holdout 也经过同一 Runner 执行，Oracle 独立比较
冻结答案，结果为 `IMPROVED`，EvidenceTier 为 `FIXTURE_EXECUTED`。

`m4-fixture-bad-v2` 会破坏原本通过的 echo 用例，结果为 `REGRESSED`。

## 不可信/受限结果

- 空 Candidate、缺失 Holdout、Runner 不存在或 advisory-only：`INCONCLUSIVE`；
- 直接提供 `candidate_outputs`/`fixture_outputs`：`SYNTHETIC_OR_IMPORTED`，
  不具备发布资格；
- `eval --mode real`：`BLOCKED_ENV`；
- 真实 LLM、真实 Agent Runner、OpenCode tool hook 和官方 MCP SDK：保持
  `BLOCKED_ENV` 或 `UNVERIFIED`。

## 交付说明

`docs/M4_3_Acceptance.json` 只记录可复核的基线、测试和证据等级，不嵌入本次
最终提交自身的 SHA。最终提交和远端 HEAD 由交付命令输出及最终报告核验。
