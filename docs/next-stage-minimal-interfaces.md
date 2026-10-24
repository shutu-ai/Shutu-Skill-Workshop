# 下一阶段最小接口设计

本文冻结 Capability、Trace、Agent Adapter 和 MCP Observer 的边界。M1 已实现 Capability/Trace 的本地基础存储、JSONL/loopback 测试摄取和脱敏；真实 Agent Adapter 与 MCP Observer 仍留给后续阶段。目标是让后续实现可以接入现有 Skill 快照、版本和 ReleaseOperation，而不改变现有 CLI。

## 1. Capability

Capability 表示稳定的逻辑能力，不等同于某个文件目录。

```text
CapabilityRef {
  capability_id: string        # skill:foo / mcp-server:vendor/name / mcp-tool:...
  kind: skill | mcp_server | mcp_tool | agent_policy
  adapter_id: string
}

CapabilityVersion {
  version_id: string
  capability_id: string
  parent_version_id: string?
  content_hash: string?
  schema_hash: string?
  source: baseline | manual | proposal | release | rollback | external
  provider_version: string?    # unknown is valid; never infer it from a path
  observed_at: string
  environment_fingerprint: string?
}

CapabilityRegistry:
  register(ref: CapabilityRef) -> CapabilityRef
  snapshot(ref: CapabilityRef) -> CapabilityVersion
  get_version(version_id: string) -> CapabilityVersion
```

Skill 的 `content_hash` 复用现有快照哈希；MCP tool/server 使用 schema 或 initialize 快照哈希。Registry 不负责修改 artifact。

## 2. Trace

Trace 是一次任务或评估运行，TraceEvent 是有序、可脱敏的事件。

```text
Trace {
  trace_id: string
  parent_trace_id: string?
  session_id: string?
  turn_id: string?
  adapter_id: string
  environment_fingerprint: string?
  status: running | ok | failed | incomplete
  started_at: string
  ended_at: string?
}

TraceEvent {
  event_id: string
  trace_id: string
  seq: int
  span_id: string?
  parent_span_id: string?
  type: string                 # platform event name; payload may be extensible
  capability_id: string?
  capability_version_id: string?
  adapter_event_id: string?
  attempt_index: int?
  status: string?
  occurred_at: string
  payload_json: object         # capture-time redacted
}

TraceStore:
  start(trace: Trace) -> Trace
  append(event: TraceEvent) -> TraceEvent
  close(trace_id: string, status: string, ended_at: string) -> Trace
  get(trace_id: string) -> Trace
  list_events(trace_id: string) -> list[TraceEvent]
```

事件顺序由 `seq` 保证。Token、Authorization、Cookie、绝对个人路径和完整文件内容默认不进入 `payload_json`；缺失事件必须标记为未观测，不能伪造为“没有发生”。

## 3. Agent Adapter

Adapter 只负责把宿主事件转换为平台事件，不把 Codex 或 OpenCode 字段泄漏进核心模型。

```text
AgentAdapter:
  register() -> AdapterDescriptor
  health() -> AdapterHealth
  ingest(event: AdapterEvent) -> IngestResult
  resolve_capabilities(context: AdapterContext) -> list[CapabilityRef]
  snapshot_capability(ref: CapabilityRef) -> CapabilityVersion

AdapterDescriptor {
  adapter_id: string
  adapter_version: string
  supported_event_types: list[string]
  missing_event_types: list[string]
}
```

`missing_event_types` 是一等结果，不用空事件替代。评估和宿主重载回调属于后续扩展；若需要重载，单独使用 `reload(capability_ref, release_id) -> ReloadResult`，不把宿主 API 放进 SkillWorkshop。

## 4. MCP Observer

Observer 是透明旁路组件，与 Agent Adapter 分离。默认不修改请求，只关联 transport、JSON-RPC id、调用结果、错误和耗时。

```text
McpObserver:
  open(context: ObserverContext) -> ObserverHandle
  observe(direction: request | response | notification,
          message: bytes,
          metadata: TransportMetadata) -> TraceEvent
  close() -> ObserverReport
```

`ObserverHandle` 负责 stdio 或 Streamable HTTP 的转发/关闭，必须保持协议字节、JSON-RPC id 和通知顺序；脱敏摘要只写 Trace。`fault-injection` 是显式评估模式，不能成为默认行为。

## 5. M3、M1、M0 与后续边界

- M0 继续负责 Proposal 测试指纹、Release Journal、锁和恢复；M1 的 `servers/shutu_workshop` 通过同一 `.skill_workshop/state.db` 做 additive migration，不改写 M0 门面。
- M1 只提供模拟来源的 JSONL/loopback ingest；夹具成功标为 `SIMULATED_PASS`，不宣称真实 Codex/OpenCode/MCP 接入。
- M1 已以 SQLite additive migration 承载 CapabilityVersion、Trace、TraceEvent，并提供 JSONL/HTTP 模拟摄取；下一步再做真实 JSONL/宿主 Adapter 和只读 MCP Observer。
- 现有 `SkillWorkshop.snapshot_skill/list_versions` 将来通过兼容 service 映射到 Capability，但 CLI `snapshot`、`versions`、`apply`、`rollback` 仍保持原入口。
- ReleaseService 是唯一文件发布入口；Adapter reload/ack 和发布后 observation 不应绕过 Journal。
- M3 已在同一 `state.db` 上以 v4 additive migration 提供确定性 RuleDetector、Issue/Occurrence 账本、checkpoint、状态历史和本地 CLI。M3 只记录 evidence-backed finding 与保守 owner hypothesis，不产生 LLM 诊断、自动修复或发布。
- M4 的最小输入是 `DiagnosisService.show_issue()` 返回的 Issue、Occurrence、EvidenceRef、unknown fields 和 capability version refs；M4 负责提案/评估边界，不能把 M3 的 hypothesis 当成 verified cause。
