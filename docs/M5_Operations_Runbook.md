# M5 Windows Operations Runbook

所有操作都在隔离 workspace 执行。不要把 `--isolated-root` 指向真实 Skill/MCP/Agent 配置目录；不要执行 `git clean`、`git reset --hard` 或持久化修改系统代理。

## Prepare and materialize

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
$env:PYTHONPATH = 'servers'
New-Item -ItemType Directory -Force .test-m5\skills\demo, .test-m5\skills\demo-candidate | Out-Null
```

先通过 M4 CLI 创建并人工接受 Proposal，再把 payload 中的 `file_patch` 修订为 `skill_script`。`file_patch` 必须包含相对路径、唯一 `old_string`、`new_string`，可选 `old_sha256`；自然语言 advisory 不能直接物化。

```powershell
python -m shutu_workshop --workspace .test-m5 release materialize `
  --proposal <PROPOSAL_ID> `
  --baseline-root .test-m5\skills\demo `
  --candidate-root .test-m5\skills\demo-candidate `
  --isolated-root .test-m5\skills --json
```

Manifest 自动保存到 `.test-m5\.skill_workshop\m5-artifacts\<proposal>-<revision>.json`，也可从 Python API 调用 `FileArtifactService.materialize` 后显式写出。baseline snapshot 与 candidate 都必须保持不变直到 execute 完成。

## Isolated evaluation

```powershell
python -m shutu_workshop --workspace .test-m5 eval run `
  --proposal <PROPOSAL_ID> `
  --mode isolated `
  --baseline-root .test-m5\skills\demo `
  --candidate-root .test-m5\skills\demo-candidate `
  --json
python -m shutu_workshop --workspace .test-m5 eval report <EVAL_RUN_ID> --json
```

必须看到 `verdict=IMPROVED`、`evidence_tier=ISOLATED_ARTIFACT_EXECUTED`。`fixture`、`static_failed`、`blocked_env`、`INCONCLUSIVE` 和历史 `LEGACY_UNTRUSTED_EVAL` 不能用于发布。

## Eligibility, plan, approval, execute

```powershell
python -m shutu_workshop --workspace .test-m5 release eligible `
  --proposal <PROPOSAL_ID> --evaluation <EVAL_RUN_ID> `
  --target-scope isolated_workspace --json

python -m shutu_workshop --workspace .test-m5 release plan `
  --proposal <PROPOSAL_ID> --evaluation <EVAL_RUN_ID> `
  --isolated-root .test-m5\skills --json

python -m shutu_workshop --workspace .test-m5 release approve <PLAN_ID> `
  --expected-hash <PLAN_HASH> --reviewer <HUMAN_ID> --json

python -m shutu_workshop --workspace .test-m5 release verify <PLAN_ID> `
  --isolated-root .test-m5\skills --json

python -m shutu_workshop --workspace .test-m5 release execute <PLAN_ID> `
  --isolated-root .test-m5\skills --json
python -m shutu_workshop --workspace .test-m5 release status <PLAN_ID> --json
```

`plan`、`eligible`、`verify`、`status` 不替换文件。只有 `execute` 触发 M0 journal。审批后改动任意 target/candidate/manifest/evaluation hash 都会被拒绝。

## Observe and rollback

没有 after Trace 时必须得到 `INSUFFICIENT_EVIDENCE`。本地演示 Trace 必须显式标记 `source=SIMULATED_TRACE`，不能当真实 Agent 结果：

```powershell
python -m shutu_workshop --workspace .test-m5 release observe <RELEASE_ID> `
  --before-start 2026-01-01T00:00:00+00:00 `
  --before-end 2026-01-02T00:00:00+00:00 `
  --after-start 2026-01-02T00:00:00+00:00 `
  --after-end 2026-01-03T00:00:00+00:00 `
  --min-samples 2 --json
```

观察只读 M1/M3 数据，聚合不在 relay 转发热路径。晚到 Trace 会改变 source fingerprint 并触发同一 window 重算；没有真实 token/cost 时输出 `null`。

出现 `REGRESSION_SIGNAL` 时先人工检查摘要和纳入/排除理由，默认不自动回滚。`release status` 中的 `release.payload.baseline_version_id` 是唯一可用的冻结基线版本：

```powershell
python -m shutu_workshop --workspace .test-m5 release rollback <RELEASE_ID> `
  --version <BASELINE_VERSION_ID> `
  --isolated-root .test-m5\skills --json
```

rollback 后核对目录树 hash、M0 `release_operations`、M5 `release_links` 和新 observation window；不要重写旧观察结果。

## Crash/restart and diagnostics

```powershell
python -m shutu_workshop --workspace .test-m5 release status <PLAN_ID> --json
python -c "from shutu_workshop.release import ReleasePlanService; print(ReleasePlanService('.test-m5').reconcile('<PLAN_ID>'))"
```

如果 M0 journal 已有 `completed` operation 且目标 hash 是 candidate，`reconcile` 会补齐 M5 link；如果目标漂移，保留失败状态并重新 materialize/evaluate/approve，不覆盖外部修改。

只检查必要的脱敏字段：不要导出原始模型输入、凭据、完整 trace payload 或用户敏感内容。

## Temporary network fallback

M5 本地闭环不需要 Context7、外部模型或网络。若后续确需 Context7/官方文档访问，临时在当前 PowerShell 进程设置代理并在结束后清除，不写全局配置：

```powershell
$env:HTTP_PROXY = 'http://proxyae.huawei.com:8080'
$env:HTTPS_PROXY = 'http://proxyae.huawei.com:8080'
# use the documentation client here
Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY -ErrorAction SilentlyContinue
```

GitHub 直连失败时只对单次命令使用代理：

```powershell
git push origin main
git -c http.proxy=http://proxyae.huawei.com:8080 push origin main
git -c http.proxy=http://proxyae.huawei.com:8080 ls-remote origin refs/heads/main
```

不持久化 Git/system proxy；代理仍失败时保留本地提交并报告 `GIT_PUSH=BLOCKED_ENV`。
