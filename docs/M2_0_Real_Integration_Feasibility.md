# M2.0 真实集成能力探测

探测日期：2026-10-24（Windows，Africa/Cairo）
证据标签：`SOURCE_CONFIRMED` = 源码/官方文档确认；`RUNTIME_VERIFIED` = 本机命令实际运行；`INFERRED` = 由已观测字段推导；`UNVERIFIED` = 没有可用运行证据。

## 结论

M2.0 = `PARTIAL`。本机存在可运行的 Codex CLI 和 OpenCode CLI，且各自真实输出过宿主事件；Codex 的 `--json` 流可以稳定进入第一版 Adapter。Codex 安全工具调用被当前执行策略拒绝，OpenCode 安全会话在 30 秒内没有完成，因此两个宿主都不能宣称完整真实工具轨迹。

MCP 两个协议时代在本地无副作用测试 Server 上均完成请求/响应、tools/list/discover、错误、SSE 和脱敏验证。这里的 `PASS` 只表示仓库代理在已覆盖的 wire path 上透明；尚未用官方 TypeScript SDK 做跨实现互操作，因此 SDK 互操作仍是 `UNVERIFIED`。

## 工作区和版本基线

| 项目 | 结果 | 证据 |
|---|---|---|
| 本地 Git HEAD | `UNAVAILABLE`：仓库是 unborn `main`，`git rev-parse HEAD` 失败 | `RUNTIME_VERIFIED`：命令输出 |
| origin | `https://github.com/shutu-ai/Shutu-Skill-Workshop.git` | `SOURCE_CONFIRMED`：`git remote -v` |
| origin/main | 本次无法读取；`git ls-remote` 连接 `github.com:443` 失败 | `RUNTIME_VERIFIED`，不是远端不存在 |
| Python | 3.14.7150.1013 | `RUNTIME_VERIFIED` |
| Node | 24.19.0 | `RUNTIME_VERIFIED` |
| Codex | `codex-cli 0.163.0` | `RUNTIME_VERIFIED`：`codex --version` |
| OpenCode | `opencode v2.1.3` | `RUNTIME_VERIFIED`：`opencode --version` |

## Codex

### 已验证接口

`codex exec --help` 实际提供 `--json`、`--ephemeral`、`--sandbox read-only`、`--skip-git-repo-check` 和 `--ignore-user-config`。这使 CLI JSONL 成为本阶段唯一已选定的 Codex 来源；Windows App 内部事件没有采用。

运行的安全探测：

```powershell
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  --ignore-user-config 'Reply with exactly OK. Do not use tools or modify files.'
```

结果为 `thread.started`、`turn.started`、`item.completed(agent_message)`、`turn.completed`，并返回 `OK`。期间 stderr 出现模型列表刷新超时，但不影响该回合完成。`RUNTIME_VERIFIED`：真实会话、thread/turn、agent message、完成状态；没有工具事件。

第二次探测要求执行一个只读 `Get-Location`：

```powershell
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  --ignore-user-config 'Run one read-only shell command to print the current directory, then reply exactly OK. Do not modify files.'
```

结果有真实 `thread.started`/`turn.started`，但 `exec_command` 被当前宿主策略拒绝；Codex 随后仍输出 `OK` 并结束。`RUNTIME_VERIFIED` 的工具尝试证据不能升级成工具成功，因此 CLI 支持级别为 `PARTIAL_REAL`。

官方资料把 `codex exec` 定位为有界非交互工作流，把 app-server 定位为创建 thread、启动 turn、流式接收事件的长期应用接口；本机只选 CLI JSONL，app-server 没有在 M2.0 中擅自接入。来源：

- <https://developers.openai.com/blog/codex-as-a-platform>
- <https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server>

### Adapter 边界

`servers/shutu_workshop/adapters/codex.py` 只解析公开 JSONL。已归一化：

`thread.started` → `task.started`、`turn.started` → `task.turn.started`、`item.started/completed` → 工具或输出事件、`turn.completed` → `task.finished`、错误事件 → `host.error`。

未观测字段明确保留：Skill discovery/load、桌面 App 内部事件、模型 token/cost、子 Agent 和 MCP tool 的完整宿主语义。工具结果不会自动把 Trace 的 `task_result` 写成 success。

## OpenCode

### 已验证接口

本机 `opencode --help` 提供 `run --format json`、`--standalone`、`plugin` 和 `session`；实际版本是 `v2.1.3`。一次显式启动：

```powershell
opencode run --standalone --format json --auto --log-level error `
  'Reply with exactly OK. Do not use tools or modify files.'
```

在 30 秒观察窗口内输出了真实 `sessionID` 和多条 `step_start`，但未得到终端结果；该次显式启动的 OpenCode 进程随后被停止。没有把 `step_start` 推断为工具调用，因此当前支持级别为 `PARTIAL_REAL/BLOCKED_ENV`。

官方 V2 插件文档确认本地 `.opencode/plugins/` 会自动加载，V2 使用 `Plugin.define({ id, setup })`，工具观察钩子是 `ctx.tool.hook("execute.before/after", ...)`；V1 返回字符串键的 hook 对象，不能直接复用 V2 实现。来源：

- <https://dev.opencode.ai/v2/docs/build/plugins/>
- <https://dev.opencode.ai/v2/docs/build/plugins/migrate-v1>

仓库提供 `adapters/opencode/plugin.ts` 作为显式、项目局部、fail-open 的 V2 观察桥接样例；Python `OpenCodeAdapter` 同时接受 V1/V2 hook 记录和 `opencode run --format json` 记录。V2 事件字段的完整 session/tool 语义仍需在当前安装版本完成一次真实 tool run 后再标 `observed`。

## MCP 协议时代

官方 SDK 版本矩阵把 `2025-11-25` 及以前归入 legacy：`initialize` 握手、旧式 HTTP session/取消；`2026-07-28` 属于 modern：`server/discover`、请求级 `_meta`，不再依赖旧握手。HTTP 取消语义也不同：legacy 使用取消通知，modern 关闭请求流。来源：

- <https://blog.modelcontextprotocol.io/posts/2026-07-28/>
- <https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions>
- <https://ts.sdk.modelcontextprotocol.io/v2/migration/upgrade-to-v2>

本仓库不把任何一种握手硬编码为全部版本必需：`protocol_versions.py` 依据实际请求/header 识别 `legacy`、`modern` 或 `unknown`；stdio 和 HTTP 观察器只旁路记录已有消息。

| 时代 | 本地测试证据 | 状态 | 缺口 |
|---|---|---|---|
| 2025-11-25 legacy | `initialize`、`tools/list`、`tools/call`、错误、通知、stdio 和 HTTP 均有测试 | `PASS`（仓库 fixture） | 未用官方 SDK 做互操作 |
| 2026-07-28 modern | `server/discover`、现代 header、SSE、HTTP 关闭/流式解析有测试 | `PASS`（仓库 fixture） | `_meta` 全量语义和 SDK 互操作未验证 |

## 选择、回退和不观测字段

| 宿主/来源 | 当前选择 | 不可用部分 | 回退 |
|---|---|---|---|
| Codex CLI | JSONL Adapter，`PARTIAL_REAL` | 工具成功受宿主策略阻断；Skill/MCP 原生字段未观测 | 导入受控 JSONL 或标 `PARTIAL`，不读私有 session 文件 |
| Codex Windows App | `UNVERIFIED` | 未采用未公开内部 API | 使用 CLI/app-server 的公开接口，或保留缺口 |
| OpenCode V2 | 局部插件 + JSON stream Adapter | 当前运行未完成安全工具调用 | 显式项目插件；V1/V2 代码分层，不盲目复用 |
| MCP stdio | 透明子进程 wrapper | provider version 可能缺失 | `unknown`，继续转发；观察失败不重发 |
| MCP HTTP | loopback URL 透明流式代理 | 远程/任意 MITM 不支持 | 拒绝非 loopback URL；调用方直接访问目标服务 |

无证据字段统一为 `unknown`/`not_observed`，不把近似时间、工具返回成功或 session step 事件提升为 task success 或完整观测。
