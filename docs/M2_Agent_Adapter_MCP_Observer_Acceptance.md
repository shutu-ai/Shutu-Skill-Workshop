# M2 Agent Adapter / MCP Observer 验收

验收日期：2026-10-24（Windows，Africa/Cairo）  
最终阶段状态：`PARTIAL`。本地 MCP wire path、统一入库、checkpoint、脱敏和回归已通过；Codex 真实会话只到部分工具证据，OpenCode 会话在观察窗口内未完成安全工具调用，因此两个宿主及其 Agent+MCP 真实端到端场景不提升为 `PASS`。

## 1. 证据和运行环境

证据标签：

- `SOURCE_CONFIRMED`：源码或官方文档确认。
- `RUNTIME_VERIFIED`：本机命令/测试实际运行。
- `INFERRED`：由已观察字段推导，不能代替原生证据。
- `UNVERIFIED`：本次没有运行证据。

运行时版本（`RUNTIME_VERIFIED`）：Python `3.14.7150.1013`、Node `24.19.0`、Codex `codex-cli 0.163.0`、OpenCode `v2.1.3`。本地仓库没有可解析的初始 Git HEAD；远端直连 `github.com:443` 在探测时失败，失败原因不能解释为远端不存在。

## 2. 可复现命令和结果

在仓库根目录执行：

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest discover -s tests -v
```

结果（`RUNTIME_VERIFIED`）：`Ran 36 tests ... OK`，`36/36`，`skipped=0`。其中 M0/M1 回归 24 项，M2 测试 12 项。另执行：

```powershell
python -m compileall -q servers/shutu_workshop
```

没有编译错误。`tests/test_m2_end_to_end.py` 是同一 M1 数据库上的合成跨来源验收，不是 Codex/OpenCode 实际调用 MCP 的替代品。

### 2.1 Codex 真实探测

```powershell
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  --ignore-user-config 'Reply with exactly OK. Do not use tools or modify files.'
```

真实输出包含 `thread.started`、`turn.started`、`item.completed(agent_message)` 和 `turn.completed`。第二次只读命令探测中，Codex 发起了工具尝试，但当前宿主策略拒绝了 `exec_command`；随后回合仍完成。结论：真实 session/turn/item/完成事件已验证，工具成功和 MCP 原生字段未验证，`CODEX_REAL_ADAPTER=PARTIAL`。

### 2.2 OpenCode 真实探测

```powershell
opencode run --standalone --format json --auto --log-level error `
  'Reply with exactly OK. Do not use tools or modify files.'
```

真实输出包含 `sessionID` 和多条 `step_start`，但 30 秒观察窗口内未完成；显式启动的本次进程已停止，没有把 `step_start` 猜成工具调用。结论：保留 `PARTIAL`，工具 before/after 仍为 `UNVERIFIED`，真实 Agent+MCP 端到端为 `BLOCKED_ENV`。

### 2.3 MCP 与统一存储

M2 测试在本地无副作用 fixture 上实际运行了：

- legacy `2025-11-25`：initialize、tools/list、tools/call、错误、通知和 stdio/HTTP。
- modern `2026-07-28`：server/discover、现代 header、SSE、请求级摘要和 HTTP 流关闭路径。
- stdio：原始 frame 业务结果透明、Content-Length 解析、stderr 限量、超时/坏 JSON 不重发。
- HTTP：loopback 限制、status/header/body 透明、JSON/SSE 解析、脱敏和取消不重试。
- tools/list：CapabilityVersion/schema hash 可追溯；未知 provider version 保留 `unknown`。
- Codex、OpenCode、MCP 事件：同一 SQLite 中按 explicit request/call ID 关联，没有 ID 则保留 `unknown` 独立事件。

这些是 fixture 的 `RUNTIME_VERIFIED` 结果；官方 SDK 跨实现互操作仍为 `UNVERIFIED`。

## 3. G0–G14 门禁

| Gate | 状态 | 证据/缺口 |
|---|---|---|
| G0 | `PARTIAL` | 三类来源都有能力清单；Codex/OpenCode 完整工具轨迹受环境阻断 |
| G1 | `PASS` | M0/M1 24 项回归、旧门面与增量 schema migration 通过 |
| G2 | `PASS` | completeness/correlation 默认 unknown，缺失字段和缺口可查询 |
| G3 | `PASS` | bounded JSONL、原子 checkpoint、截断/替换重定位和失败不推进通过 |
| G4 | `PASS` | capture-time redaction、稳定事件 ID/幂等、冲突路径测试通过 |
| G5 | `PARTIAL` | Codex 真实 thread/turn/item；工具尝试被当前执行策略拒绝 |
| G6 | `PARTIAL` | OpenCode 真实 session/step 输出；安全会话未完成，未观测 tool before/after |
| G7 | `PASS` | stdio 请求/响应、错误、通知、framing 与业务透明测试通过 |
| G8 | `PASS` | loopback HTTP、legacy/modern 探测、SSE、错误、取消路径通过 |
| G9 | `PASS` | tools/list/discover 的 schema hash 与 CapabilityVersion 通过 |
| G10 | `PASS` | 仅 explicit ID 关联；无证据不按时间或工具名猜测 |
| G11 | `PASS` | 观察失败 fail-open、限量错误、无 retry/无重复 tools/call 通过 |
| G12 | `BLOCKED_ENV` | Codex+MCP、OpenCode+MCP 的真实宿主链路缺少可完成工具调用 |
| G13 | `PASS` | 本地 fixture 成对测量，见下节；不是生产 SLA |
| G14 | `PASS` | 36 项测试、编译检查、四份文档和运行手册完成 |

状态规则遵循任务定义：`PASS` 只用于实测支持的范围，fixture-only 的结果不升级成真实宿主 `PASS`。

## 4. 开销和隐私

同一 Windows 工作区、同一 Python fixture、5 次配对运行（`RUNTIME_VERIFIED`）：

| 路径 | 中位数 |
|---|---:|
| 直接启动 fixture baseline | 385.93 ms |
| 经过 stdio observer | 377.20 ms |
| 差值 | -8.73 ms（-2.3%） |

该场景主要测量 Python 子进程启动抖动；不能据此宣称 observer 零开销、固定 SLA 或生产吞吐。Observer 自身报告的 `observer_overhead_ms` 同样只是本次 wrapper 观测值。

测试使用的敏感植入值是 fixture 假值。写入 SQLite 的是 JSON-RPC 摘要：参数 key、result/error key、ID、schema hash、header 存在性；不写 prompt、参数值、Authorization、Cookie 或原始 stderr。完整原始宿主 JSONL 只作为用户显式指定的本地输入，不由模块上传。

## 5. 最终报告字段

```text
BASELINE_HEAD=UNAVAILABLE_NO_LOCAL_HEAD
FINAL_HEAD=see final delivery report
M2_STATUS=PARTIAL
M2_0_SOURCE_PROBE=PARTIAL
CODEX_REAL_ADAPTER=PARTIAL
OPENCODE_REAL_ADAPTER=PARTIAL
MCP_STDIO_OBSERVER=PASS
MCP_HTTP_OBSERVER=PASS
MCP_2025_PROTOCOL=PASS
MCP_2026_07_28_PROTOCOL=PASS
CROSS_SOURCE_CORRELATION=PASS
INGEST_CHECKPOINT_REDACTION=PASS
M0_M1_REGRESSION=PASS
TESTS=36/36, skipped=0
REAL_E2E_CODEX_MCP=BLOCKED_ENV
REAL_E2E_OPENCODE_MCP=BLOCKED_ENV
OBSERVER_OVERHEAD=baseline median 385.93 ms; observer median 377.20 ms; delta -8.73 ms (-2.3%), local fixture only
GIT_PUSH=see final delivery report
COMMIT=see final delivery report
REMOTE_HEAD=see final delivery report
DOCS=docs/M2_0_Real_Integration_Feasibility.md; docs/M2_Agent_Adapter_MCP_Observer_Design.md; docs/M2_Agent_Adapter_MCP_Observer_Acceptance.md; docs/M2_Operations_Runbook.md
KNOWN_LIMITATIONS=Codex tool success, OpenCode tool hook runtime, official SDK interop, and real host+MCP E2E remain unverified or blocked; no LLM diagnosis is implemented
NEXT_STAGE=M3_RULE_DETECTION_AND_DIAGNOSIS_LEDGER
```

