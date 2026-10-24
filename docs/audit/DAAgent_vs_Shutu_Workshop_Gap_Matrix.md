# DAAgent vs Shutu-Skill-Workshop 能力差异矩阵

审计基线：当前工作树无可解析 Git HEAD；参考提交 8c5ebe8da5aa33a7155edf53dd196bc6a09ed9ed 不在本地对象库中。因此“Shutu 当前状态”按工作树源码判断，不假设参考提交仍然最新。

状态解释：

- 已实现：源码中有相对完整的闭环，但仍不代表当前运行环境已启用。
- 部分实现：有数据字段或局部流程，缺少闭环/验证。
- 数据基础：能保存或传递相关信息，但没有质量判定。
- 未发现：在本次检查范围内没有相应实现。
- UNVERIFIED：源码不足以证明运行时效果，本次未启动或未执行。

| 能力 | DAAgent 状态 | Shutu 当前状态 | 目标状态 | 可复用性 | 优先级 | 依据 |
|---|---|---|---|---|---|---|
| Agent 事件接入 | 部分实现；Skill Evolution 通过宿主 on_session_start、LLM/tool hooks 采集 | 未发现 Adapter；只有 CLI 直接操作本地目录 | Codex CLI/Windows App、OpenCode 都可插拔接入 | 采集生命周期概念可复用，hook 名称不可复用 | P0 | DA runtime.py:66-235；Shutu cli.py:83-132 |
| Session/Turn/Task 关联 | 部分实现；session_id、turn_id、interaction_id 存在 | 未发现 | trace_id、parent_trace_id、session_id、turn_id、event_id 统一模型 | 数据概念可复用 | P0 | ingestion.py:27-42；Shutu models.py:31-45 |
| Model call 轨迹 | 仅以 role/content 字符串附在 session events | 未发现 | provider/model/request/response/token/cost/latency | 不直接复用 | P1 | runtime.py:113-143；Shutu 无对应字段 |
| Tool call/result 轨迹 | 有工具名、参数 JSON 和结果字符串；无 call id/结构化结果 | 未发现 | typed call/result、retry、timeout、cancel、error code | 事件类型可复用 | P0 | runtime.py:145-181 |
| Skill 版本识别 | 已实现；ingestion 同步 worktree 得到 version id/tree hash | 只有 SkillSnapshot content hash 和 Version package hash | CapabilityVersion 快照覆盖 Skill/MCP/Agent | hash/parent 设计可复用 | P1 | ingestion.py:95-128；engine.py:120-142 |
| Skill 发现/加载/执行三态 | 只近似识别 native Skill tool call；没有发现/加载事件 | 未发现 | discovery/loaded/invoked 独立事件及证据等级 | 仅 invoked 概念可复用 | P0 | diagnostic_policy.py:67-77 |
| Missing trigger 发现 | 未实现；没有 Skill 使用负样本 | 未实现 | 能力声明 + 任务意图 + routing evidence + counterfactual | 不能直接复用 | P0 | ingestion.py:148-163 只处理已使用 Skill |
| 子 Agent 父子关系 | 未发现 parent/child trace 字段 | 未发现 | parent_trace_id、spawn/join 事件 | 不直接复用 | P1 | runtime.py:19-37、:376-405 |
| 失败/取消/超时/重试 | session status 和 diagnosis job retry/lease 有；工具级生命周期弱 | 未发现 | 统一 outcome/error/retry/cancel/timeout 事件 | job lease 可复用 | P1 | ingestion.py:27-42；sqlite_repository.py:385-580 |
| 轨迹持久化 | SQLite 保存 job/Issue/Occurrence，原始轨迹作为 JSON 文本；还有 session 文件回读 | SQLite 只有 issues/proposals/versions | Trace 与 TraceEvent 可查询、可脱敏、可归档 | schema 思路可复用，不能搬 DA 字段 | P0 | schema.py:18-130；store.py:32-63 |
| 隐私/脱敏 | ingestion 有 redaction；hook 先捕获原始输入，tracking 另一路投影 | 未发现脱敏策略 | capture-time redaction、payload reference、retention policy | 只复用原则 | P0 | ingestion.py:166-180；tracking.py:1112-1128 |
| 规则初筛 | error signal regex、Skill eligibility、auto gate | record_issue 只接受人工字段 | 规则/统计初筛与高成本诊断分离 | 思路可复用 | P1 | proposer.py:350-472；models.py:350-359 |
| LLM 诊断 Schema | 已实现；严格 JSON、证据索引、归因、confidence、actionable | 未发现 LLM 调用和诊断 Schema | 统一 DiagnosisFinding 支持 Agent/Skill/MCP owner | schema 思路可复用 | P0 | diagnosis_analyzer.py:27-162；models.py:394-451 |
| 诊断归因 | 有 skill_defect/agent_noncompliance/transient_environment/user_or_auth/unknown | EvolutionIssue 只有 category/severity/title/summary | 支持 agent_decision、skill、mcp_tool、mcp_server、network、auth、user 等 owner | 枚举和 evidence 概念可复用 | P0 | models.py:394-451；Shutu models.py:31-45 |
| 因果验证 | 没有通用反事实；依赖模型判断和可选候选重跑 | 未发现 | 基线/候选同任务重跑、任务 oracle、干预证据 | 不直接复用 | P0 | service_evaluation.py:204-232 |
| 问题去重/聚合 | Issue/Occurrence/dedup_key/row_version/重开已实现 | 无 dedup key、occurrence、状态迁移，仅人工 evidence_count | 问题指纹、Occurrence、生命周期和复发观察 | 数据模型可复用 | P1 | service_diagnosis.py:289-379；store.py:65-87 |
| 严重度与证据门槛 | confidence >= 0.65、actionable、skill_defect、auto gate | severity 可输入，但无自动门槛 | severity + confidence + evidence threshold + causal gate | 规则可复用 | P1 | service_diagnosis.py:270-287 |
| LLM 提案上下文 | Skill 内容最多 50k、轨迹证据、错误信号、工具/Skill 列表 | create_proposal 只接收人工/外部传入 exact patches | EvidenceBundle + capability snapshot + trace spans | “上下文先裁剪”可复用 | P1 | proposer.py:476-541、:963-983；engine.py:207-248 |
| 提案格式 | 仅 Skill 文件 patch；支持多文件、hunk、evidence refs、hunk revision | 仅文本 old/new patch；支持多 patch 文件但无模型/证据关联 | 按 artifact 类型选择 text diff、JSON patch、code suggestion、config change | Patch 安全边界可复用 | P1 | proposer.py:183-267；engine.py:173-248 |
| 提案范围控制 | 路径白名单、脚本审批、full-file code review | 路径白名单，但没有文件/hunk 风险策略 | capability-specific change policy 和 reviewer scope | path validation 可复用 | P1 | review_policy.py:12-24；engine.py:90-104 |
| 提案新鲜度 | base version/tree hash/package fingerprint，stale proposal superseded | base_hash 检查当前内容，但无 row_version/候选 fingerprint | candidate_fingerprint 与 proposal/evaluation 绑定 | hash 基础可复用 | P1 | service_freshness.py:16-84；engine.py:314-315 |
| 审核状态机 | 有 file/hunk/all 审核、revision、row_version、多种 proposal/apply 状态 | pending_review/accepted/rejected/test_passed/test_failed/applied，粒度较粗 | review/test/approval/release/observe/revert 分层状态 | 状态思想可复用 | P1 | models.py:27-105；engine.py:250-279 |
| 测试候选 | 默认静态 overlay；可接收候选 session | test_proposal 只是临时复制、补丁和 SKILL.md/无 symlink 检查 | 统一 candidate build + static + real task evaluation | 临时 staging 可复用 | P0 | service_evaluation.py:41-179；engine.py:303-340 |
| 测试结果失效 | 有 freshness，但未看到所有审阅变更都自动使 test result 失效；apply 本身缺少 test_passed 硬门禁 | 明确存在缺陷：test 后 accept_patch 改变 accepted set 不清空 test_result/status | candidate hash 覆盖 accepted change set，任何候选变更自动 invalidates eval | 不能复用现状 | P0 | service_apply.py:408-461；engine.py:260-266、:303-340、:403-416 |
| Apply 一致性 | apply operation journal、staging、swap、version commit、recovery | existing apply 有异常回退，但无 apply journal；fork 在目录替换后再写 DB | FS+SQLite journal/recovery/事后 reconcile | journal 设计可复用 | P0 | service_apply.py:248-355、:605-762；engine.py:446-482 |
| 并发控制 | SQLite WAL/busy_timeout/row_version；sidecar workspace lease；进程内 Skill lock | sqlite 默认连接，无 WAL/busy_timeout/row_version；无进程/跨进程锁 | workspace/capability release lock + optimistic concurrency | 事务要素可复用 | P0 | sqlite_repository.py:1351-1477；workspace_lock.py:13-39；store.py:26-30 |
| 宿主刷新 | host effect reload_agent/assessment refresh/sign | 未发现宿主回调；CLI 直接替换目录 | Adapter reload/ack/timeout/recovery | 回调契约可复用 | P1 | plugin.py:304-331；engine.py:403-519 |
| 版本族谱 | baseline/manual/evolution/fork/rollback、parent/head、备份 | Version 有 parent_version_id、snapshot_path、rollback 新版本 | 统一 ArtifactVersion/Release lineage | 强可复用 | P1 | skill_version/service.py:57-444；models.py:89-104 |
| 回滚 | 目标版本验证、expected head、backup、恢复 | 有版本 rollback，但无 expected head、journal、锁 | 原子 rollback operation + host reload + observation rollback | 基础实现可复用 | P1 | skill_version/service.py:324-444；engine.py:484-519 |
| Deposition 自动聚类 | 已实现文件化轨迹、dedup、TF-IDF/余弦、增量 clustering | 未发现 | 后期通用 workflow/capability mining，不阻塞首批平台 | 只复用 fingerprint/状态 | P2 | trajectory_clustering/core.py:535-940；docs/技能工坊复刻说明.md:59-68 |
| Deposition 质量验证 | LLM cluster validation、fingerprint stale、manual selection | 未发现 | 任务 oracle + 候选 Skill eval 后再进入 review | 验证前置可复用 | P2 | validation.py:147-338 |
| Deposition 生成/接受 | GeneratedSkill schema、临时目录、人工编辑、host materialization check | 未发现 | 作为后期 proposal/release 的一种 producer | 安全路径/人工接受可复用 | P2 | distillation.py:127-390；review.py:112-355 |
| MCP server 发现/版本 | DAAgent plugin 有一个 provider MCP manifest；Skill Evolution 不观察 MCP | 未发现 | server initialize + tools/list snapshot + schema hash + version unknown fallback | 不复制 DA MCP，抽象为 capability | P0 | daagent_mcp/protocol.py:13-269；plugin.json:20-29 |
| MCP 调用失败/重复/耗时 | 未发现 Observer | 未发现 | transparent stdio/Streamable HTTP proxy 记录 call/result/retry/error/latency | 新增模块 | P0 | Evolution/Deposition 目录未发现 MCP observer |
| Agent/Skill/MCP 联合调用链 | Skill-only candidate snapshots，工具仅名称字符串 | 未发现 | 所有事件共享 trace/span/capability version | 不直接复用 | P0 | runtime.py:19-37、:376-405 |
| 质量指标 | execution_status、Issue occurrence、启发式 evaluation | 未发现 | task correctness、trigger precision/recall、MCP correctness、cost、latency、regression、recurrence、post-release effect | 指标分类可复用，值需重建 | P0 | service_evaluation.py:415-618 |
| 发布后观察 | tracking、assessment snapshot、usage record 部分存在 | 未发现 | release observation window、baseline comparison、auto rollback gate | 只复用事件投影思想 | P1 | tracking.py:139-188；models.py:318-332 |
| API/服务化 | FastAPI versioned API + sidecar + host bridge | 只有 CLI | core library + optional local API + adapter service；保持 CLI | API 分层经验可复用 | P1 | skill_evolution/service/app.py:37-229；Shutu cli.py:26-132 |
| 授权/可复用性 | 安装包有第三方 COPYING，但专属模块授权未确认 | 当前代码是独立重实现 | 只复用概念，补充自有许可证和依赖清单 | 法律边界不可省略 | P0 | DAAgent site-packages/COPYING；本报告 A6.3 |

## 结论

当前 Shutu 的有效资产是本地 Skill 包安全边界、快照 hash、exact patch、临时 staging、版本快照和回滚测试；它还不是质量演化平台。最关键的差距不是 UI 或 LLM provider，而是统一 Trace/Capability/Adapter/MCP Observer/Evaluation 数据和一致性协议。

