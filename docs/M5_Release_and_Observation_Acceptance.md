# M5 Acceptance

## Scope

`M5_STATUS` 只表示本地隔离 Skill 的 release/rollback/observation 闭环，不表示生产可自动发布。基线是 `a39744bd0dda8db1476a4f5382418413698a12da`；旧 M0-M4.3 用户改动和未跟踪任务文件保留。

## Verification commands

在 Windows PowerShell：

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m5_release_observation -v
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
python -m shutu_workshop --help
git diff --check
```

当前隔离测试覆盖：文件 candidate materialization、双侧 File-backed Runner、Oracle/holdout 隔离、`IMPROVED`、candidate mutation、advisory 拒绝、`running` lease 冲突、plan/approval/dry-run、M0 journal 发布、重复 execute、Windows Python 子进程异常退出后的 journal reconcile、rollback、无 after 样本门禁、晚到 Trace 重算、regression signal。

## G0-G22 ledger

| Gate | 结果 | 证据/限制 |
|---|---|---|
| G0 | PASS | 基线与 origin/main 可追溯；用户 dirty/untracked work 未清理 |
| G1 | PASS | 全量旧测试、编译通过 |
| G2 | PASS | live `running` 返回 status=running/no verdict；lease 过期可同 run 重试 |
| G3 | PASS | Fixture/Legacy/Static/advisory 不通过真实文件发布资格 |
| G4 | PASS | proposal revision、file patch digest、artifact manifest、M0 candidate hash 链接 |
| G5 | PASS | baseline/candidate 均从磁盘子进程执行，证据为 `ISOLATED_ARTIFACT_EXECUTED` |
| G6 | PASS | Runner 入参没有 expected output；holdout 缺失不 PASS |
| G7 | PASS | Good Candidate `IMPROVED`；M4 Bad Candidate `REGRESSED` |
| G8 | PASS | target drift、candidate mutation、hash mismatch 拒绝 |
| G9 | PASS | approval 绑定 plan/evaluation/candidate/target hash、reviewer、时间 |
| G10 | PASS | isolated root、相对路径、symlink 和 M0 Skill 边界检查 |
| G11 | PASS | 复用 M0 `test_proposal/apply_proposal/rollback` 与 journal |
| G12 | PASS | plan idempotency key、唯一 link、重复 execute 复用同一 release |
| G13 | PASS | 新 Windows Python 子进程在 M0 journal 完成后异常退出，重启 reconcile 不重复发版 |
| G14 | PASS | 新进程根据 M0 operation/target hash 补齐 M5 link |
| G15 | PASS | rollback 后目录 hash 等于冻结 baseline，M0 rollback journal 存在 |
| G16 | PASS | MCP/Agent/advisory-only 没有执行适配器 |
| G17 | PASS | release/version/hash/window/trace/exclusion 摘要关联 |
| G18 | PASS | 0 after 或样本不足返回 `INSUFFICIENT_EVIDENCE` |
| G19 | PASS | failure-rate 恶化返回 `REGRESSION_SIGNAL`，默认不自动回滚 |
| G20 | PASS | 不保存凭据；统计不在 relay 热路径，成本缺失为 null |
| G21 | PASS | Windows CLI、恢复、回滚、临时代理 Runbook 已写 |
| G22 | VERIFIED_AFTER_PUSH | 本地 commit 与 remote SHA 在最终终端报告核对；仓库 JSON 避免自引用 |

## Separate environment accounting

- `REAL_AGENT_CANDIDATE_EVAL=BLOCKED_ENV`：本任务没有配置真实 Agent Runner。
- `REAL_PRODUCTION_TARGET_RELEASE=NOT_EXECUTED`：只发布隔离目录。
- `OPENCODE_REAL_TOOL_HOOK=BLOCKED_ENV`、`OFFICIAL_MCP_SDK_INTEROP=UNVERIFIED`：不被本地隔离 PASS 覆盖。
- `SIMULATED_TRACE` 只用于本地 observation contract 演示，不能称为真实 Codex Agent 验收。

## Recovery notes

如果 execute 进程在 M0 journal 完成后退出：重新创建 `ReleasePlanService(workspace)`，调用 `reconcile(plan_id)`，确认 `release_links.status=completed`、目标 hash 等于 candidate hash，再继续观察。目标漂移或 candidate mutation 时不要重试 execute，重新 materialize/evaluate/approve。
