# M2 Agent Adapter / MCP Observer 设计

状态：实现说明与约束冻结（2026-10-24）。  
范围：M2.1–M2.4；不包含 LLM 诊断、自动修复、发布、全局配置改写或生产回滚。

## 1. 边界和数据流

```text
Codex JSONL ─────┐
OpenCode JSON ───┼─> Agent Adapter ─┐
                 │                  ├─> M1 TraceIngestor ─> SQLite
MCP stdio ───────┤                  │
MCP HTTP ────────┘   MCP Observer ──┘
                         │
                         └─> CapabilityVersion / tools schema snapshot
```

Core 只依赖 `Trace`、`TraceEvent`、`TraceIngestor`、`CapabilityRegistry` 和脱敏策略。Codex/OpenCode 的宿主字段只存在于 `adapters/`；MCP framing、HTTP header、JSON-RPC 处理只存在于 `observers/mcp/`。旧的 `plugins.daagent.skill_workshop` 兼容门面不被新模块导入。

## 2. M1 增量契约

### Trace

M2 在 M1 `traces` 表上以 migration 3 增加 `adapter_version`。`observation_completeness` 默认是 `unknown`；Adapter 只在明确声明范围并有真实证据时才可传 `complete`。迁移会把旧库无证据的 `complete` 映射为 `unknown`，避免把历史模拟数据解释成真实完整观测。

### TraceEvent

M2 增加以下可查询字段，同时保留 `payload_json` 扩展空间：

| 字段 | 语义 |
|---|---|
| `request_id` | JSON-RPC/request/call ID；缺失为空 |
| `tool_name` | 旁路能确认的工具身份；只从消息字段取得 |
| `transport` | `codex-cli`、`opencode-cli`、`stdio`、`streamable-http` 等 |
| `observation_source` | `agent_adapter` 或 `mcp_observer` |
| `adapter_event_id` | 宿主原生事件 ID 或稳定本地转换 ID |
| `capability_version_id` | 实际 snapshot 版本；未知时为空 |
| `correlation_quality` | `exact`、`derived`、`weak`、`unknown`；默认 `unknown` |

旧 `observed` 值迁移为 `unknown`，不改变事件正文，不自动补造关联。事件仍由 `(trace_id, seq)`、来源 ID和内容摘要去重；冲突返回结果，不覆盖原记录。

## 3. Adapter 合约

`adapters/base.py` 提供：

```text
AdapterDescriptor:
  adapter_id, adapter_version, source_type, host_version
  supported_event_types[], missing_event_types[]
  correlation_methods[], redaction_policy_version
  health_status, last_error, last_event_at, field_status{}

BaseAdapter:
  descriptor() -> AdapterDescriptor
  health() -> AdapterHealth
  ingest_jsonl(text, trace_id?, close?) -> IngestResult
  capture(command, cwd?, timeout?) -> CaptureResult
```

Adapter 的转换顺序是：公开源事件 → `AdapterEvent` → M1 `TraceEvent` → capture-time redaction → SQLite transaction。没有事件时写 `missing_event_types`，不写一条“未发生”的假事件。

### Codex

第一来源是公开 `codex exec --json` JSONL。支持 `thread/turn/item` 基本生命周期、工具 item、错误和完成状态；不能取得的 Skill load、桌面 App 内部事件、模型 token/cost、子 Agent 关系保持 `not_observed`。相同输入二次摄取依靠宿主事件 ID/稳定 event ID 去重。

### OpenCode

Python Adapter 接受 V2 `tool.execute.before/after` 语义、V1 字符串 hook 语义及 CLI JSON stream；V1/V2 的差异只在 Adapter 层。项目局部 `adapters/opencode/plugin.ts` 使用 V2 `Plugin.define` 和 `ctx.tool.hook`，只发受限元数据，不改 input/output、权限、prompt 或 tool result；sink 未配置或发送失败时 fail-open。

插件默认不自动加载：只有复制到隔离工作区 `.opencode/plugins/` 或在显式局部 `opencode.json(c)` 中加入路径才启用；清理时删除该局部目录/配置即可，不触碰用户全局配置。

## 4. Checkpoint 与背压

`capture/checkpoints.py` 使用一个工作区拥有的 JSON checkpoint，记录 source path、byte offset、file key、offset anchor hash 和 reset 次数。读取以完整 JSONL 行为单位，每批有 `batch_size` 上限；未写完整的末行留到下一轮。

提交顺序固定为：

```text
read bounded batch
  -> validate + redact + SQLite transaction
  -> transaction succeeds
  -> atomic checkpoint replace
```

源文件被截断、替换或 offset 附近内容被重写时，anchor/file key 检测会回到 0；不会从不可信 offset 继续，也不会把同一 MCP 请求重新提交给目标服务。checkpoint 写入使用同目录临时文件、flush、fsync、replace；无限内存缓存不在本实现中。

## 5. MCP Observer 合约

`McpTraceRecorder` 是 stdio/HTTP 共用的 fail-open 旁路记录器：

```text
start() -> Trace(partial)
observe_json(direction, message, headers?, status_code?) -> None
register_tools(tools, server_info?, protocol?) -> CapabilityVersion evidence
close(status) -> ObserverReport
```

Observer 记录摘要而不是原始 payload：JSON-RPC id、method、tool name、参数 key、result/error key、schema hash、HTTP status、方向、应用层顺序和延迟。参数值、prompt、Authorization、Cookie、query credential 和命令环境不写入 Trace。观察失败增加 `failed_observations`/health 缺口，不改变转发，也不重发请求。

### stdio

`stdio.py` 只接受显式子进程 command。它支持 newline JSON 和 Content-Length framing，client→server、server→client、notification、server request、乱序 response、EOF、stderr、超时和进程退出；协议 stdout 只写原始 frame，诊断报告写 stderr/SQLite。原始 JSON 字节不由 observer 重建，因此 id、字段顺序和通知 payload 不被修改。

请求与响应按 JSON-RPC id 配对；`tools/list` 结果产生 server/tool CapabilityVersion，`tools/call` 产生 `mcp.tool_call`/`mcp.tool_result`。工具 schema 尚未到达时工具事件仍可记录，但暂不绑定版本，避免并发时因 snapshot 尚未完成而阻断真实调用。

### Streamable HTTP

`streamable_http.py` 只接受显式 `http(s)://127.0.0.1|localhost|::1/...` URL。每次请求建立受控连接并流式读取 response；向调用方返回原始 status/header/body bytes。JSON 与 SSE 按应用层消息解析，HTTP chunk 边界不被当成 JSON-RPC 边界。摘要缓冲有上限，超限标为 `not_observed` 仍继续转发。

代理保留方法、协议 header、JSON-RPC id、HTTP status、SSE 顺序和错误。响应 header 的凭据值不写 Trace；只写 header 名存在性和 session header 是否存在。`cancel()` 只关闭当前连接，绝不重新提交 tools/call。没有自动 retry、外部 MITM 或系统代理修改。

## 6. 协议兼容矩阵

| 时代 | 识别 | 观察路径 |
|---|---|---|
| legacy `2025-11-25` | `initialize`、协议 header、旧 session header | 初始化/initialized、tools/list、tools/call、旧取消通知、SSE |
| modern `2026-07-28` | `server/discover`、`Mcp-Method`/`Mcp-Name`、modern protocol header | discovery、请求级元数据摘要、SSE/流关闭取消 |
| unknown | 没有足够字段 | 原始转发继续，事件标 `unknown`，不强行选择握手 |

这与官方 MCP SDK 的 legacy/modern 分代一致，但本项目只实现透明旁路，不声明自己是官方 SDK transport。

## 7. 关联、去重和状态

跨 Adapter/Observer 只有同一原生 request/call ID 时才用 `correlation_quality=exact` 合并为一个逻辑调用组；没有 ID 的事件保持独立且 `unknown`。`correlate_events()` 只按显式 `request_id` 分组，不能按时间或工具名猜测。

`task.completed`/`turn.completed` 与 `task_result=success` 分开。工具 `result.isError=false` 只说明工具响应没有协议业务错误，不代表用户任务正确。Trace 默认 `partial/unknown`，未观测字段由 Descriptor 和 source metadata 可检索。

## 8. 低开销和安全取舍

- Agent/MCP 关键路径不调用 LLM。
- Capture-time redaction 在 SQLite transaction 之前执行。
- Observer 使用有界摘要和有界 stderr，SQLite 写入失败只生成缺口，不重发业务请求。
- `ObserverReport.observer_overhead_ms` 是本次 wrapper 测量值，不是 SLA；验收必须给 baseline/observer 成对数据。
- 新模块只使用 Python 标准库和现有 M1 代码，没有新增第三方依赖。
