# Shutu-Skill-Workshop 通用平台架构重构建议

本建议只定义后续重构边界，不在本次审计中实施。目标是保留当前仓库已验证的本地 Skill 版本资产，同时把平台扩展为可观察 Codex CLI/Windows App、OpenCode 和 MCP Server 的持续质量改进系统。

## 1. 架构决策

### 1.1 保留重构，不重新开始

建议保留当前仓库作为底座，原因：

- engine.py 已有安全路径、Skill.md、symlink、UTF-8、exact text patch、staging、snapshot hash；
- models.py/store.py 已有最小的 Proposal/Version/Issue 持久化；
- tests/test_skill_workshop.py 已覆盖 snapshot、stale proposal、fork、路径边界和 rollback 的基础行为；
- 版本快照和 rollback 是后续发布链的真实资产，不值得删除重写。

但不建议继续向 SkillWorkshop 类中添加 Trace、MCP、诊断、评估、发布和宿主适配逻辑。当前 engine.py:40-532 已经同时承担文件系统、patch、review、test、version、apply、rollback；继续扩展会把一致性和宿主耦合混在一起。

建议将 SkillWorkshop 保留为兼容门面，内部逐步委托给：

1. CapabilityRegistry：能力和版本快照。
2. TraceStore/TraceIngestor：统一轨迹。
3. DiagnosisService：初筛、归因、问题台账。
4. ProposalService：按 artifact 类型生成/审阅候选变更。
5. EvaluationService：基线/候选对照执行。
6. ReleaseService：发布、reload、journal、rollback。
7. AdapterRegistry：Agent Adapter 和 MCP Observer。

### 1.2 总体数据流

    Agent Adapter ─┐
    MCP Observer ──┼─> Normalized Trace ─> Issue Detector ─> Diagnosis
    Manual import ─┘                                      │
                                                        v
    Capability Snapshot <─ Proposal <─ Evidence Bundle <─┘
              │
              v
    Candidate Build ─> Static Checks ─> Task Evaluation ─> Review
              │                                  │
              v                                  v
        Release Journal ─> Host Reload ─> Observation ─> Rollback/Improve

核心原则：采集、诊断、提案和发布之间通过持久化 ID/快照关联，不通过“当前目录内容”隐式关联。

## 2. Capability 通用模型

### 2.1 两层对象

不要把 Skill、MCP Server、MCP Tool 和 Agent policy 强行做成同一种文件。统一的是身份、版本和证据，不是修改格式。

Capability：

- capability_id：稳定逻辑身份，例如 skill:workspace/foo、mcp-server:vendor/name、mcp-tool:vendor/name/search。
- kind：skill、mcp_server、mcp_tool、agent_policy。
- owner：agent、workspace、team 或 external。
- adapter_id：发现它的 Adapter。
- locator：脱敏后的本地路径、命令、URL 或注册名。
- current_version_id：当前生效版本。
- metadata_json：能力类型特有信息。

CapabilityVersion：

- version_id、capability_id、parent_version_id；
- content_hash：Skill 包/Agent 配置/Server 代码快照；
- schema_hash：MCP server/tool schema 快照；
- source：baseline、manual、proposal、release、rollback、external；
- snapshot_ref：本地不可变目录或内容存储引用；
- observed_at、created_by、environment_fingerprint；
- materialization_state：present、missing、unknown。

对于没有正式版本号的 MCP Server，version_id 仍由平台生成，但 provider_version 应为 unknown；不能从文件名或启动时间猜版本。

### 2.2 快照要求

- Skill：至少包含允许的相对文件、内容 hash 和 manifest；是否包含签名/权限由 Adapter 提供。
- MCP Server：保存 initialize 返回的 serverInfo、protocolVersion、capabilities 和 transport identity。
- MCP Tool：保存 tools/list 中的 name、description、inputSchema、输出 schema（存在时）和 schema_hash。
- Agent：保存 Adapter 版本、配置 hash、可用 Skill/MCP registry 版本。

快照要成为诊断和评估的输入，不能只记录“当前路径”。路径可能被宿主重写、MCP server 可能来自远程进程，且旧轨迹需要在未来仍可解释。

## 3. Trace Schema

### 3.1 Trace 主记录

建议最小 Trace 表/模型字段：

| 字段 | 作用 |
|---|---|
| trace_id | 一次用户任务/评估任务的稳定 ID |
| parent_trace_id | 子 Agent 或派生任务的父关系，可为空 |
| workspace_id | 脱敏后的工作空间身份 |
| agent_id / adapter_id | 哪个 Agent 以及哪个采集适配器 |
| session_id / turn_id | 宿主原生关联字段 |
| task_query / task_labels | 脱敏任务输入和可选预期能力标签 |
| started_at / ended_at | 任务级时间 |
| status / execution_status | succeeded、failed、cancelled、timeout、interrupted |
| environment_fingerprint | OS、Agent、MCP server/tool snapshot、关键配置 hash |
| redaction_policy_version | 允许重放/审计的脱敏策略版本 |
| input_payload_ref / output_payload_ref | 大文本或受限内容的引用，不把敏感原文塞入索引列 |
| token/cost summary | provider、input/output tokens、估算成本，可为空 |

### 3.2 TraceEvent

每个事件至少有：

- event_id、trace_id、seq、span_id、parent_span_id；
- type、actor、occurred_at、duration_ms；
- capability_id、capability_version_id；
- adapter_event_id、attempt_index；
- payload_json（已脱敏）和 payload_ref；
- outcome、error_code、retry_of_event_id。

推荐事件类型：

- task.started、task.finished、task.cancelled；
- model.request、model.response；
- skill.discovered、skill.loaded、skill.invoked；
- tool.call、tool.result；
- mcp.initialize、mcp.tools_list、mcp.tool_call、mcp.tool_result；
- retry.started、retry.finished；
- host.error、network.error、auth.error、permission.error。

事件类型应是平台枚举，payload 才允许 Agent/MCP 扩展字段。这样可以用同一套查询发现“工具调用失败”和“Skill 没有触发”，又不会把所有宿主内部字段硬编码进核心。

### 3.3 原始内容和隐私

首版低复杂度方案：

- SQLite 只存事件索引、摘要、hash、状态和小 payload；
- 长文本或候选 prompt 存在 workspace-owned payload 文件，按 retention policy 清理；
- capture-time redaction 先于任何远程上传；
- 凭据、Authorization、Cookie、绝对个人路径、完整文件内容默认不入 Trace；
- 为诊断保存可解释的 evidence excerpt，而不是默认保存整个原始会话；
- 远程统计仅接收聚合 mutation，原始轨迹不出本地，除非明确配置和授权。

## 4. Agent Adapter Contract

目标是让核心只依赖契约，而不是依赖 Codex/OpenCode 的内部类名。

建议的最小接口：

    register() -> AdapterDescriptor
    health() -> AdapterHealth
    ingest(event: AdapterEvent) -> IngestResult
    resolve_capabilities(context) -> list[CapabilityRef]
    snapshot_capability(ref) -> CapabilityVersion
    run_evaluation(case, candidate_overlay) -> EvaluationRun
    reload(capability_ref, release_id) -> ReloadResult

契约要求：

1. Adapter 可以是 native hook、wrapper、日志转换器或人工导入器；核心不假设来源。
2. ingest 必须能提供 host_session_id、host_turn_id 和 host_event_id；拿不到时生成平台 ID，并把 correlation_quality 标为 weak。
3. Adapter 必须明确 supported_event_types 和 missing_event_types。不能因为没有事件就填“没有发生”。
4. resolve_capabilities 要区分 discovered、loaded、invoked，缺失信息用 unknown。
5. run_evaluation 要返回 baseline/candidate 的 trace_id、环境 fingerprint 和执行状态；不能只返回 exit code。
6. reload 要有 ack、timeout 和 unsupported 状态。reload 未确认时不能把 release 标成 fully_active。

首批实现建议：

- Codex CLI/Windows App：优先使用官方/稳定事件或显式 wrapper；拿不到宿主内部 hook 时只提供 limited adapter，明确缺失 model/skill lifecycle 字段。
- OpenCode：独立 adapter，把其 session/turn/tool/skill 事件转换成平台事件，不把 OpenCode 字段泄漏到 core model。
- Agent-independent core：允许 JSONL/HTTP ingest 作为测试和人工回放入口。

不能假设 Windows App 一定暴露完整内部事件。未公开的内部 API 应标为 UNVERIFIED，并保留降级模式。

## 5. MCP Observer Contract

### 5.1 观察器职责

MCP Observer 是独立于 Agent Adapter 的组件，最小职责：

    discover(server_ref) -> ServerSnapshot
    snapshot_tools(server_ref) -> ToolSnapshot[]
    proxy(request) -> response
    emit(event) -> TraceEvent
    close() -> ObserverReport

Observer 不能修改请求内容，除非用户显式开启 fault-injection/evaluation 模式。默认只旁路记录和转发。

### 5.2 stdio

stdio 观察器应：

- 启动被观察 server 子进程，保持 stdin/stdout 协议透明；
- 将诊断日志写 stderr 或独立文件，禁止污染 protocol stdout；
- 记录启动命令的脱敏 hash、子进程 pid、退出码、signal、stderr 摘要；
- 按 JSON-RPC id 配对 request/result，记录 notification 没有 response 的事实；
- 正确转发取消、断开和进程退出；
- 记录 initialize 和 tools/list 的快照及其 schema_hash；
- 不把命令行中的 token、环境变量值、完整路径直接写入 Trace。

stdio 的 framing 应由 transport adapter 针对实际实现处理；不能从一个 DAAgent provider 的 /sse 实现推断所有 MCP stdio 行为。

### 5.3 Streamable HTTP

Streamable HTTP 观察器应：

- 对 initialize、tools/list、tools/call 等请求记录 URL 的逻辑身份而非敏感 query；
- 保留 JSON-RPC id、HTTP status、响应错误、headers 中的 session/correlation 线索；
- 将 POST request 和流式/SSE response 关联到同一 span；
- 记录连接复用、断开、超时、重试和取消；
- 不改变 response body、顺序和流式边界；
- 对代理自身的开销单独记录 observer_overhead_ms。

### 5.4 MCP 版本和质量字段

工具调用事件要带：

- server_capability_version_id、tool_snapshot_id；
- tool_name、schema_hash；
- arguments_summary、arguments_redaction_status；
- result_summary、is_error、error_code；
- latency_ms、retry_index、duplicate_group_id。

参数错误可先由 JSON Schema validator 做规则诊断；服务端业务失败再由诊断模型归因。这样可以区分“Agent 参数不符合 schema”“MCP server 内部错误”“网络/权限失败”。

## 6. Diagnosis Contract

### 6.1 两阶段管道

第一阶段是低成本 detector：

- schema mismatch；
- tool result 明确 isError；
- timeout、connection、permission、auth；
- 重复相同调用；
- 无效 retry；
- 任务标签要求的 capability 未出现；
- Skill/MCP snapshot 在调用前后不一致。

第二阶段才调用 LLM：

- 输入 EvidenceBundle，而不是整条未脱敏轨迹；
- 限制候选 owner；
- 限制 evidence event ids；
- 读取相关 capability version snapshot；
- 读取已存在的 IssueCluster 和历史评估；
- 输出结构化 finding。

### 6.2 输出模型

最小 finding：

- issue_id/dedup_key；
- primary_owner：agent_decision、skill、mcp_tool、mcp_server、network、auth、user、unknown；
- contributing_capabilities；
- category/subtype/object；
- severity、confidence、actionable；
- evidence_event_ids、evidence_excerpt_refs；
- hypothesis；
- required_verification；
- proposed_change_kinds；
- regression_risk。

归因必须允许“primary owner unknown + contributing owners”，不能强迫模型把所有错误归给 Skill。

### 6.3 何时诊断

不对每条 Trace 直接调用高成本模型：

- 规则 detector 命中才入队；
- 相同 fingerprint 在 cooldown 内合并；
- 按 severity 和出现次数 gate；
- 对成功轨迹按采样率评估 Skill trigger precision；
- 对 schema mismatch/明确 error code 直接生成低成本 Issue；
- 对已关闭问题只在新 version 或 evidence pattern 变化时重诊断；
- 对一条 Trace 的多个 MCP retries 只形成一个候选问题和多个 occurrences。

### 6.4 缺失触发和联合归因

“没有调用 Skill/MCP”本身不是故障。只有以下证据同时存在时，才把 missing trigger 作为候选：

1. task case 或用户明确标签声明了期望能力；
2. Adapter/registry 证明能力可发现且版本有效；
3. routing/intent evidence 证明触发条件满足；
4. Trace 中没有 invoked/loaded；
5. baseline/candidate 或人工判定确认结果受影响。

这比 DAAgent 的“仅从已使用 Skill 进入诊断”更适合通用平台。

## 7. Evaluation Contract

### 7.1 核心对象

EvalPlan：

- plan_id、task_case_ids；
- baseline capability version set；
- candidate capability version set；
- Adapter、MCP Observer 和 environment fingerprint；
- repeat_count、seed、timeout；
- oracle 类型和回归门禁；
- privacy/retention policy。

EvalRun：

- eval_run_id、plan_id、trace_id；
- candidate_hash、baseline_hash；
- status、started/ended、failure stage；
- task_result：passed、failed、unknown；
- metrics_json；
- evidence_refs。

### 7.2 最小质量指标

- task correctness：任务级 oracle 或人工标注；
- Skill trigger precision/recall：需有 labeled task set；
- MCP call correctness：schema correctness、business result correctness；
- tool failure/retry/duplicate rate；
- latency：task/model/tool/MCP 分层；
- token/cost；
- regression rate；
- issue recurrence；
- post-release observation delta。

所有分数必须带 measurement_source：oracle、rule、human、model_estimate。不能把启发式分数显示成事实。

### 7.3 候选失效规则

候选内容发生下列任一变化，原 test/evaluation 自动失效：

- accepted change set 变化；
- candidate tree/schema hash 变化；
- base capability version 变化；
- Adapter/MCP server/tool snapshot 变化；
- environment fingerprint 变化；
- eval prompt/oracle/policy 版本变化。

Evaluation 表必须保存 candidate_hash 和 input_fingerprint；ReleaseService 只接受完全匹配的 passed gate。

## 8. Proposal / Release Contract

### 8.1 提案按 artifact 类型分型

ProposalTarget：

- target_capability_id/version_id；
- target_kind；
- reason、issue_ids、occurrence_ids、source_trace_ids；
- change_set；
- risk、review_policy；
- base_snapshot_hash；
- candidate_snapshot_hash；
- evaluation_ids。

ChangeSet 类型：

| 类型 | 适用对象 | 最小验证 |
|---|---|---|
| TextPatch | SKILL.md、references、模板 | old text 唯一匹配 + tree hash |
| StructuredPatch | MCP schema、Agent config | JSON Schema + semantic diff |
| ScriptPatch | Skill scripts、adapter code | file-level review + static/test |
| ServerChangeSuggestion | 第三方 MCP server | 只生成建议，不直接修改外部 server |
| ToolDescriptionChange | MCP tool description/schema | tools/list snapshot diff + task eval |
| RoutingChange | Agent tool/Skill selection policy | labeled task eval + regression gate |

### 8.2 状态机

建议状态：

    draft
      -> pending_review
      -> partially_accepted / approved
      -> candidate_built
      -> static_passed
      -> evaluated
      -> released
      -> observing
      -> reverted / superseded / rejected

任何 candidate/evaluation 输入变化都回到 candidate_built 或 pending_review，不允许仅靠旧的 passed 标志继续发布。

### 8.3 Release Journal

ReleaseOperation 需要保存：

- operation_id、proposal_id、capability_id；
- base_version_id、candidate_hash、target_version_id；
- staging_ref、previous_ref；
- status：prepared、candidate_staged、files_swapped、version_committed、host_reload_pending、completed、failed；
- error、last_reconciled_at、attempt_count。

流程：

1. 校验 base version/head、candidate hash 和 evaluation gate。
2. 写 prepared journal。
3. 构建 candidate 到 staging。
4. 静态扫描和重新 hash。
5. 在 workspace/capability lock 下写 files_swapped 前状态。
6. 用可恢复的 rename/replace 交换。
7. 在 SQLite 事务中提交 version 和 operation state。
8. 调 Adapter.reload，等待 ack；不能确认时保持 reload_pending。
9. 写 completed，进入 observation window。
10. 启动时扫描未完成 operation，根据真实目录 hash 和 version snapshot reconcile；不自动假设成功。

## 9. SQLite 迁移方案

### 9.1 迁移原则

当前 store.py:32-63 的 issues/proposals/versions 继续保留，不在首期删除或重命名。使用 state.db 的 additive migrations，增加 schema_migrations 表，所有迁移在单个 SQLite transaction 中完成。

现有数据不能伪造新的 Trace、Evaluation 或 causality 结论。回填时标记 source=legacy_manual、evidence_quality=unknown。

### 9.2 首批增量表

建议按以下最小集合分两批增加：

第一批，平台闭环所需：

- schema_migrations；
- capabilities；
- capability_versions；
- traces；
- trace_events；
- issue_occurrences；
- evaluations；
- release_operations。

第二批，Proposal/发布增强：

- proposal_targets；
- proposal_changes；
- releases；
- observations；
- adapter_runs。

现有 issues/proposals/versions 可先扩展可空字段：

- workspace_id/agent_id；
- target_kind/capability_id；
- base_version_id/candidate_hash；
- evidence_refs_json；
- row_version；
- legacy_source。

高频 TraceEvent 的可变字段使用 payload_json，查询依赖的字段（trace_id、seq、type、capability_id、status、occurred_at）单独列出并建索引。不要为所有未来字段建列。

### 9.3 回填

- versions → capability_versions，kind=skill，content_hash=package_hash，parent_version_id 保留。
- proposals → proposal_targets/proposal_changes，source=legacy，base_hash 保留。
- issues → issue_clusters，category/severity/title/summary 保留，occurrence_count=0 或从 evidence_count 转为 unknown，不伪造 occurrence。
- 没有现成原始轨迹的旧记录不回填 traces。
- 旧的 snapshot_path 作为 legacy snapshot_ref，先做路径存在性检查，不能移动旧版本目录。

### 9.4 一致性和并发

当前实现缺少 FS/DB 跨边界 journal。新 ReleaseService 应：

- 用 workspace.lock + capability lock 防止同一能力并发发布；
- SQLite 连接设置 foreign_keys、busy_timeout、WAL 或等价可靠模式；
- proposal、evaluation、release 使用 row_version 乐观并发；
- 所有 operation 先落 journal，再做 rename/replace；
- 每次启动先 reconcile operation 与真实目录 hash；
- 旧的 SkillWorkshop.apply/rollback 在过渡期走 ReleaseService，避免两套应用路径。

## 10. Skill/MCP 联合归因

联合归因必须依赖同一 Trace，而不是事后按时间猜测：

    task
      └─ agent routing span
          ├─ skill loaded/invoked
          ├─ model decision
          └─ mcp tool call
              └─ mcp server internal/downstream span

每个 span 绑定 capability_version_id。诊断时按以下优先级判断：

1. MCP schema 直接拒绝参数：primary owner=agent_decision 或 skill_instruction，MCP tool 是 contributing capability。
2. 参数符合 schema 但 server 返回确定性业务错误：primary owner=mcp_server。
3. 连接/超时/权限：primary owner=network/auth/environment，除非有重复且版本相关的证据。
4. Agent 明确选择错误工具：primary owner=agent_decision，关联 Skill/MCP tool。
5. Skill 说明缺少必要前置步骤：primary owner=skill。
6. 多个原因不能分开时：primary owner=unknown，保留候选 owners，不生成自动修改。

只有当候选版本的 Evaluation 证明主要指标改善且没有 owner 相关回归时，才允许将 attribution 从 hypothesis 提升为 verified_fix。

## 11. 当前代码保留与重构清单

### 11.1 保留

- engine.py 的 _validate_name、_validate_patch_path、_assert_no_symlinks；
- _snapshot_dir 的内容 hash 方式，后续扩展为 capability snapshot；
- _apply_text 的唯一匹配/replace_all 保护；
- staging directory 和 candidate tree validation；
- Version 的 parent_version_id、snapshot_path、source、message；
- 当前测试中 deterministic snapshot、stale proposal、fork、rollback 的行为资产；
- CLI 的 list/snapshot/propose/versions/rollback 作为兼容入口。

### 11.2 重构

- engine.py:40-532 拆成 SnapshotService、ProposalService、CandidateBuilder、ReleaseService；
- store.py:26-163 增加迁移、row_version、锁/事务边界，避免 payload-only upsert；
- models.py:31-104 保留旧模型，通过新模型或兼容字段承载 capability/trace/evaluation；
- record_issue 不再作为唯一问题入口，改为 IssueDetector/DiagnosisService 写入 issue cluster + occurrence；
- test_proposal 不再把静态检查命名为完整 test；拆成 build_candidate、static_validate、run_evaluation；
- apply_proposal/apply_proposal_as_new_skill/rollback 全部通过统一 ReleaseOperation；
- 未来 MCP observer 和 Agent adapter 独立目录/包，不放入 skill_workshop.engine。

### 11.3 不在首阶段增加

- 自动轨迹聚类和 Skill 自动沉淀；
- 通用“让 LLM 修改任意 server 代码”的自动写入；
- 远程市场/Hub/OAuth；
- 复杂多租户权限中心；
- 把所有 Agent 的宿主内部事件统一成同一组不可扩展字段。

## 12. 按依赖关系排序的实施阶段

### 阶段 0：安全基线

验收：

- 记录当前工作树状态；
- 为 existing/fork/rollback 补充故障注入和并发测试设计；
- 明确 legacy state.db 迁移策略；
- 任何旧命令仍能调用现有本地行为。

### 阶段 1：Capability + Trace 最小闭环

实现：

- additive SQLite migration；
- capability/version registry；
- normalized Trace/TraceEvent；
- redaction 和 payload retention；
- JSONL/HTTP ingest adapter 作为测试入口。

验收：可导入一条脱敏 Agent 轨迹，关联 Skill version，并从 SQLite 查询完整事件顺序；没有真实 Agent 依赖。

### 阶段 2：Codex/OpenCode Adapter + MCP Observer

实现：

- 两个最小 Agent adapter，只覆盖可稳定获得的事件；
- stdio proxy；
- Streamable HTTP proxy；
- initialize/tools/list snapshot；
- call/result/error/latency correlation。

验收：同一个 task trace 能看到 Agent、Skill 和 MCP tool 三者的 capability version；proxy 不改协议内容。

### 阶段 3：Detector + Diagnosis Ledger

实现：

- rule detector；
- IssueCluster/Occurrence；
- owner/attribution/confidence；
- LLM diagnosis 可替换 provider；
- cooldown、dedup、evidence gate。

验收：同一问题多次出现只形成一个 issue cluster；网络错误不会自动变成 Skill defect；missing trigger 只有有期望能力标签才进入候选。

### 阶段 4：Proposal + Evaluation

实现：

- artifact-specific change sets；
- candidate hash；
- static validation；
- 固定任务集和真实执行 adapter；
- correctness/cost/latency/regression gate；
- candidate 变化自动 invalidates 旧 test/eval。

验收：未通过真实任务 gate 的 proposal 不能进入 release；accept/revise 后旧结果不可复用。

### 阶段 5：Release + Reload + Observation

实现：

- unified ReleaseOperation；
- workspace/capability locks；
- FS/DB journal 和 crash recovery；
- Adapter reload/ack；
- observation window、回滚和版本族谱；
- 发布后真实使用指标。

验收：进程在 files_swapped/version_committed/reload_pending 各阶段退出后，重启 reconcile 结果可解释，且不会丢失版本记录。

### 阶段 6：Skill Deposition

只有阶段 1-5 形成稳定 Trace 和 Evaluation 后再做：

- 轨迹筛选和聚类；
- workflow candidate；
- LLM validation/distillation；
- 人工 review；
- 作为普通 Proposal/Release 输入。

沉淀不是第一阶段的依赖；没有质量 oracle 时先做自动沉淀会放大错误。

## 13. 推荐的最小下一步

下一阶段不应先做 LLM proposer 或聚类。最小可交付范围是：

1. additive SQLite schema_migrations + capability/version；
2. Trace/TraceEvent ingest 和脱敏；
3. 一个通用 JSONL/HTTP Adapter；
4. MCP stdio/Streamable HTTP Observer 的只读观测；
5. existing SkillWorkshop 的 snapshot/version 行为通过兼容 service 接入。

这五项完成后，才能用真实 Codex/OpenCode/MCP 数据验证后续诊断和评估模型，而不是继续围绕 DAAgent 字段做静态复刻。

