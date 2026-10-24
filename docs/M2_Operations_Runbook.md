# M2 Operations Runbook

本手册只覆盖项目局部、显式启动的观察。它不会修改 Codex/OpenCode 全局配置、系统代理、MCP Server 安装文件，也不会把原始 prompt、工具参数或凭据上传到外部服务。

## 1. 准备隔离工作区

在仓库根目录打开 PowerShell：

```powershell
$env:PYTHONPATH = 'servers'
New-Item -ItemType Directory -Force .test-m2 | Out-Null
```

所有命令都应先确认当前目录是本仓库。`servers` 是 M1/M2 Python 模块路径；运行 fixture 时追加 `tests`：

```powershell
$env:PYTHONPATH = 'servers;tests'
```

## 2. Codex Adapter

M2 使用公开的 `codex exec --json` JSONL，不读取私有 session 文件。先执行无副作用会话并把输出留在隔离目录：

```powershell
$env:PYTHONPATH = 'servers'
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  --ignore-user-config 'Reply with exactly OK. Do not use tools or modify files.' `
  1> .test-m2\codex.jsonl
```

将已脱敏、用户允许的 JSONL 显式导入 M1：

```powershell
python -m shutu_workshop.adapters.codex --workspace .test-m2 .test-m2\codex.jsonl
python -m shutu_workshop --workspace .test-m2 trace events --adapter-id codex
```

`--ignore-user-config` 和 `--sandbox read-only` 只适用于本次探测；不要把它们改成全局配置。没有工具事件时，Descriptor 会保留缺口，不补造 Skill/MCP/tool 记录。

## 3. OpenCode 局部插件

仓库中的 `servers/shutu_workshop/adapters/opencode/plugin.ts` 是 OpenCode V2 的最小观察桥接样例。只有在确认当前 OpenCode 安装能加载该插件及 `@opencode/plugin` 依赖后，才在一次性测试工作区复制它：

```powershell
$pluginRoot = Join-Path (Get-Location) '.test-m2\.opencode\plugins\shutu-workshop-observer'
New-Item -ItemType Directory -Force $pluginRoot | Out-Null
Copy-Item servers/shutu_workshop/adapters/opencode/plugin.ts (Join-Path $pluginRoot 'index.ts')
$env:SHUTU_WORKSHOP_EVENT_SINK = (Join-Path (Get-Location) '.test-m2\opencode-events.jsonl')
opencode run --standalone --format json --auto --log-level error `
  'Reply with exactly OK. Do not use tools or modify files.'
```

本机本轮只验证了 OpenCode `sessionID`/`step_start` JSON 输出，未完成真实 tool hook；因此该插件接线仍按 `UNVERIFIED` 操作。sink 只允许受限元数据，发送失败 fail-open。停止 OpenCode 后删除 `.test-m2\.opencode` 和 `SHUTU_WORKSHOP_EVENT_SINK` 环境变量即可卸载；不要复制到用户目录下的全局插件目录。

## 4. MCP stdio Observer

该 wrapper 的 stdout 只能输出被代理 Server 的原始协议 frame，诊断报告写 stderr。先运行仓库的无副作用 fixture：

```powershell
$env:PYTHONPATH = 'servers;tests'
$request = @'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"fixture-only"}}}
'@
$request | python -m shutu_workshop.observers.mcp.stdio `
  --workspace .test-m2 --server-name shutu-fixture --timeout 10 -- `
  python -m fixtures.mcp_test_server --protocol legacy `
  1> .test-m2\mcp-protocol.out 2> .test-m2\mcp-report.json
Get-Content .test-m2\mcp-report.json
```

对现代 MCP fixture 使用 `--protocol modern`，并改用 `server/discover` 请求；不要强行添加 legacy `initialize`。生产接线时仅把显式的 Server command 放在 `--` 后，不要把密码、token 或未经审查的 shell 字符串拼入命令。观察失败不会重发 `tools/call`；报告中的 `failed_observations`、`errors`、`timed_out` 必须进入运行记录。

## 5. Streamable HTTP Observer

HTTP Observer 默认只允许 `127.0.0.1`、`localhost` 或 `::1`。本仓库没有提供任意外部 URL 的 MITM CLI；HTTP 实际路径由本地测试覆盖：

```powershell
$env:PYTHONPATH = 'servers;tests'
python -m unittest tests.test_m2_mcp_http -v
```

在应用内使用 `StreamableHTTPObserver('http://127.0.0.1:<port>/mcp')`，传入原始 request body 和协议 headers；用 `request()` 取得原始 status、headers、body，用 `stream()` 逐块读取 SSE/流式响应，用 `cancel()` 关闭当前连接。取消只关闭连接，绝不自动 retry。HTTP header 只记录名称存在性和 session header 是否存在，不记录 Authorization、Cookie 或代理凭据。

## 6. Checkpoint、查询和恢复

长时间 JSONL 摄取应使用 `CheckpointedJSONL`，而不是每次重新读完整文件：

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest tests.test_m2_capture_checkpoints -v
```

checkpoint 只有在 M1 transaction 成功后原子替换；进程在事务失败、文件截断、文件替换或 offset anchor 改变时会保留旧 offset 或回到安全位置。不要手工把 offset 改到文件中间。重复输入依靠稳定来源事件 ID/幂等冲突处理，不通过重新提交 MCP 请求恢复。

常用只读查询：

```powershell
python -m shutu_workshop --workspace .test-m2 trace events --adapter-id mcp-observer
python -m shutu_workshop --workspace .test-m2 capability list
python -m shutu_workshop --workspace .test-m2 trace show <trace-id>
```

## 7. 故障处理

| 现象 | 处理 |
|---|---|
| stdout 出现非 JSON-RPC 文本 | 立即停止接线，检查 Server/launcher 是否把日志写到 stdout；Observer 自身只写 stderr |
| `invalid_json` 或 `failed_observations` | 保留原始转发结果，收集限量报告；不要 retry tools/call，修复 framing/协议后重新做一次受控测试 |
| stdio 超时 | 先看 `timed_out`、进程退出码和限量 stderr；结束显式测试进程，不重发未确认结果 |
| HTTP 非 loopback 被拒绝 | 这是默认安全边界；将测试服务绑定到 loopback，或由用户明确设计新的受控 transport，不修改系统代理 |
| checkpoint 不前进 | 检查 M1 transaction/SQLite 锁错误；确认成功后再重跑同一批，不能手改 checkpoint 或清空数据库 |
| OpenCode 插件加载失败 | 按当前安装版本核对 V1/V2 API 与依赖；移除局部 `.opencode` 目录，恢复无插件运行 |
| 真实宿主缺少工具事件 | 标记 `PARTIAL`/`BLOCKED_ENV`，不要把 session/step 或 task completion 推断为 tool success |

## 8. 停止与清理

先用 Ctrl+C 停止显式运行的 Codex/OpenCode/fixture 进程，再确认没有需要保留的报告。仅清理本次隔离目录：`.test-m2` 及其中的局部 `.opencode`、JSONL 和 SQLite；不要执行仓库根目录递归清理，不要删除用户全局 OpenCode/Codex 配置。清理后重新运行 M0/M1/M2 测试，确保卸载没有改变源代码或全局行为。
