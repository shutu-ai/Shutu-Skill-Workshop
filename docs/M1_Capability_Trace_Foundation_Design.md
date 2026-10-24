# M1 Capability / Trace 基础层设计

## 1. 范围

M1 增加一个不依赖 Agent 宿主的本地数据底座，代码位于 `servers/shutu_workshop/`。它可以保存并检索模拟的 Codex、OpenCode 和 MCP 事件，但不实现真实宿主 Hook、OpenCode 插件、MCP 透明代理、LLM 诊断或自动发布。

旧的 `plugins.daagent.skill_workshop` 仍是 M0 兼容门面；M1 使用独立包和独立 CLI，不把 Trace/MCP 逻辑塞进 `engine.py`。

## 2. 数据契约

### Capability

`Capability` 表示稳定的逻辑能力，而不是一个文件目录：

| 字段 | 含义 |
| --- | --- |
| `capability_id` | 工作区内稳定身份，例如 `skill:demo`、`mcp-tool:server/search` |
| `kind` | `skill`、`mcp_server`、`mcp_tool` 或 `agent_policy` |
| `adapter_id` | 发现/采集来源；不会从文件名猜测宿主版本 |
| `locator` | 可选的本地或协议定位信息 |
| `metadata` | 脱敏后的发现证据 |
| `discovered_from` / `discovered_at` | 证据来源和时间 |

`CapabilityVersion` 是不可变证据引用，包含 `version_id`、`parent_version_id`、`content_hash`、`schema_hash`、`source`、`provider_version`、`observed_at`、`environment_fingerprint` 和 `snapshot_ref`。同一 `version_id` 的内容不一致时返回冲突，绝不静默覆盖。

M0 Skill 版本可通过 `register_skill_version()` 映射：`Version.package_hash` 写入 `content_hash`，M0 的版本 ID 可直接作为 `version_id`，旧版本目录不移动。MCP 的 `serverInfo`、协议/传输标识和 `tools/list` 工具描述只作为测试快照保存；没有 `provider_version` 时写 `unknown`。

### Trace / TraceEvent

`Trace` 表示一次任务或观测运行，含 `workspace_id`、`adapter_id`、可选的 session/turn/parent trace、状态、时间、观察完整度、脱敏策略版本和可选 token/cost 摘要。`task_result` 默认是 `unknown`，工具成功不会被提升为任务成功。

`TraceEvent` 使用 `trace_id + seq` 排序，并保留可选的 `span_id`/`parent_span_id`、能力版本、`adapter_event_id`、重试关联、错误分类、`correlation_quality` 和脱敏 `payload_json`。`skill.discovered`、`skill.loaded`、`skill.invoked` 是不同事件；未观测不写成 `false`。无法观测的父 Trace 不会按时间自动推断。

## 3. SQLite 迁移

数据库仍是 `<workspace>/.skill_workshop/state.db`；也可以直接传入 `state.db` 路径。`M1Database` 使用 `PRAGMA foreign_keys=ON`、30 秒 `busy_timeout`、WAL 和显式 `BEGIN IMMEDIATE`。

`schema_migrations` 的版本含义如下：

1. 保证空库具备 M0 的 `issues`、`proposals`、`versions`、`release_operations` 兼容形状；已有 M0 表只保留，不重建。
2. 增加 `capabilities`、`capability_versions`、`traces`、`trace_events` 及必要索引。

迁移在一个事务内执行，重复打开数据库不会重复插入版本或破坏数据。`trace_events` 有 `(trace_id, seq)` 唯一约束、来源事件 ID 局部唯一索引及时间、能力、错误分类索引。`CapabilityVersion.parent_version_id` 保留 lineage 引用，也允许指向 M0 的历史版本 ID，因此不会伪造一条新的 Trace 或诊断证据。

## 4. 摄取和幂等

JSONL 每行是一个对象，推荐格式：

```json
{"record_type":"trace","trace":{"trace_id":"t1","workspace_id":"ws","adapter_id":"codex-simulated"}}
{"record_type":"event","event":{"trace_id":"t1","seq":0,"type":"task.started","adapter_event_id":"source-0","payload_json":{}}}
```

`TraceIngestor.import_jsonl()` 与 HTTP 使用同一条规范化、脱敏和 SQLite 事务路径。单批次先完整验证，随后一次性提交；无效 JSON、未知 Trace、未知能力版本、重复 `seq` 冲突或超限都会拒绝，不能留下半批数据。

- 相同来源事件和相同内容再次摄取报告 `duplicates`；不覆盖原记录。
- 相同来源事件 ID 对应不同内容报告 `conflicts`。
- 缺少 `adapter_event_id` 时，使用 `trace_id + seq + 脱敏规范事件` 的摘要生成稳定事件 ID；这只覆盖相同输入，不承诺无 ID 的任意日志完美去重。
- 乱序到达允许，查询始终按 `seq` 返回；同一 Trace 的 `seq` 只能出现一次。

## 5. 采集时脱敏

`RedactionPolicy` 在任何 SQLite 写入前执行，默认处理 Authorization、Cookie、Bearer、API key、password、secret、access token，嵌套对象和数组中的相同键，以及 Windows/Unix 个人绝对路径。完整 prompt、文件内容和环境变量保存为长度 + SHA-256 摘要；过长字符串和 payload 被限制或拒绝。HTTP 响应只返回计数和通用错误，不回显请求体。

默认 payload 上限为 64 KiB，事件/批次仍受批次数量限制；可以通过构造 `RedactionPolicy` 和 `TraceIngestor(max_batch_events=...)` 调整。策略版本写入 Trace，避免后续策略变化被误认为历史观测事实。

M1 不运行后台定时任务，但提供显式 `TraceStore.delete_payloads(before=...)` 和 CLI 清理入口；它只把旧 payload 替换为 `{"retention":"deleted"}`，保留事件顺序、状态、错误和关联字段。

## 6. 本地 HTTP

服务不会自动启动。显式配置 token 后才可创建：

```python
from shutu_workshop import TraceIngestor, create_ingest_server, serve_in_thread

ingestor = TraceIngestor("/path/to/workspace")
server = create_ingest_server(ingestor, host="127.0.0.1", port=0, token="local-secret")
thread = serve_in_thread(server)
```

写入端点为 `POST /v1/traces` 和 `POST /v1/events:batch`，只允许 loopback bind，并要求 `Authorization: Bearer ...` 或 `X-Workshop-Token`。缺少 token 会禁用创建；不会默认暴露局域网或互联网。

## 7. CLI / API

从仓库根目录运行：

```powershell
$env:PYTHONPATH = "servers"
python -m shutu_workshop --workspace . capability list
python -m shutu_workshop --workspace . capability snapshot skill:demo --kind skill --adapter-id skill-workshop --version-id skill-version-1 --content-hash fixture-skill-hash
python -m shutu_workshop --workspace . capability snapshot mcp-tool:demo-server/search --kind mcp_tool --adapter-id mcp-simulated --version-id mcp-tool-version-1 --schema-hash fixture-schema-hash
python -m shutu_workshop --workspace . capability versions skill:demo
python -m shutu_workshop --workspace . trace import --jsonl tests/fixtures/codex_trace.jsonl
python -m shutu_workshop --workspace . trace events trace-codex-001 --error-type network.error
python -m shutu_workshop --workspace . trace show trace-codex-001
python -m shutu_workshop --workspace . trace prune --before 2026-01-01T00:00:00+00:00
```

Python API 的最小入口是 `CapabilityRegistry`、`TraceStore`、`TraceIngestor`、`RedactionPolicy` 和 `M1Database`。原有 `python -m plugins.daagent.skill_workshop ...` 命令不变。

## 8. 取舍与限制

- 夹具证明的是统一 Schema 能保存和检索模拟信息，状态只能标为 `SIMULATED_PASS`；Codex/OpenCode 真适配器和 MCP Observer 是 `REAL_ADAPTER_UNVERIFIED`。
- M1 没有后台 TTL 清理任务、远程上传、用户管理、任务正确性 Oracle 或真实宿主 reload。
- 缺少稳定来源事件 ID 的去重受 `trace_id + seq + digest` 限制；未来 Adapter 应尽量提供宿主原生事件 ID、session/turn、请求 ID、能力版本和观测完整度。
