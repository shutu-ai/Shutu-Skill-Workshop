# Shutu-Skill-Workshop 审计执行摘要与最终架构决策

## 1. 审计基线与结论边界

- 审计工作区：D:\dev-projects\Shutu-Skill-Workshop。
- 当前 Git HEAD：不存在。当前分支为未提交的 unborn main；参考提交 8c5ebe8da5aa33a7155edf53dd196bc6a09ed9ed 也不在本地对象库。因此，本报告以当前工作树源码为 Shutu 对象，并将“缺少可比较提交历史”标记为 UNVERIFIED。
- DAAgent 检查范围：Skill Evolution 服务、宿主 evolution connector/runtime、diagnosis/diagnostic policy、proposer、evaluation、apply/version/lock、Skill Deposition pipeline/clustering/validation/distillation/review/scheduler，以及 daagent MCP protocol/server/router。未启动 DAAgent，未调用在线 GTS Skill Hub，未读取默认工作区中的业务轨迹或凭据。
- 证据等级：源码确认使用 SC；配置或 manifest 表明使用 CFG；基于实现作出的判断使用 INF；无法由静态检查确认的运行状态使用 UNVERIFIED。

详细调用链见 DAAgent_Key_Mechanisms_Audit.md；逐项差异见 DAAgent_vs_Shutu_Workshop_Gap_Matrix.md；接口与阶段建议见 Shutu_Workshop_Generic_Architecture_Recommendations.md。

## 2. 最终判断：CONDITIONAL GO

Shutu-Skill-Workshop 适合作为通用 Agent Skill/MCP 质量演化平台的重构底座，但不适合直接扩展现有 SkillWorkshop 类后上线，也尚未具备通用平台所需的真实轨迹、MCP 观察、联合归因和对照评估能力。

结论是 CONDITIONAL GO，而不是重新开始：

- 应保留现有的 Skill 文件边界校验、快照哈希、补丁审核、版本 lineage 和 rollback 资产；这些是当前最有价值的本地发布内核，见 servers/plugins/daagent/skill_workshop/engine.py:72-149、342-519。
- 应把 engine.py:40-532 降为兼容门面，新增独立的 Trace、Diagnosis、Evaluation、Release 和 Adapter 边界；不能继续把跨宿主采集和 MCP 逻辑塞进 SkillWorkshop。
- 在 P0 发布一致性问题和轨迹基础层完成前，不应宣称平台支持 Codex、OpenCode 或 MCP 的持续质量闭环。

## 3. 明确回答十个架构问题

### 3.1 保留重构，还是重新开始？

保留并重构。现有代码规模小、测试资产集中，且已经覆盖安全路径、静态补丁、版本快照和回滚；重写会丢失这些已验证边界。需要重构的是职责边界和数据模型，而不是删除整个项目。

### 3.2 最值得借鉴的五项 DAAgent 机制

1. 宿主事件到异步摄取：runtime 的 session/tool/LLM 生命周期记录，配合 ingestion、job lease 和 scheduler，见 _skill_connector/evolution/runtime.py:66-235、service/application/ingestion.py:51-210、service/diagnosis_scheduler.py:194-327。
2. 证据、Occurrence、Issue、DiagnosisFinding 分层：把一次轨迹证据与聚合问题、归因结果和提案分开，见 service/domain/skill_evolution/models.py:305-451。
3. 内容哈希与版本族谱：保存基线、候选、父版本和回滚版本，见 service/skill_version/service.py:93-188、324-411；当前项目也已具备相同方向的简化资产。
4. staging、apply journal 和恢复：DAAgent 的 ApplyOperation、swap、commit、recover 形成了比单步文件替换更可靠的发布边界，见 service/service_apply.py:248-355、605-762、service/sqlite_repository.py:1263-1385。
5. 沉淀中的去重、指纹、陈旧验证和人工审核：适合作为后期能力，见 skill_deposition/service/trajectory_clustering/validation.py:147-338、service/deposition/service/review.py:31-319。

这些是设计经验，不等于可以复制 DAAgent 源码。安装目录的 COPYING 只清楚覆盖一个第三方 BSD 风格版权文本；gdeclaw 专属模块的再发布授权未确认，见 D:/Program Files/DAAgent/Lib/site-packages/COPYING。

### 3.3 哪些机制不值得复刻？

- 绑定 QwenPaw/DAAgent 的宿主 hook、workspace 解析和 reload_agent 调用；目标平台应使用 Agent Adapter。
- 仅把工具名 Skill 当作技能使用事实；它无法表达 MCP capability、版本、发现但未加载、加载但未执行。
- 以工具调用事件数量和文本相似度推断任务质量；它可以筛选候选，但不能替代任务级 oracle。
- 固定 DAAgent 轨迹格式、TF-IDF 聚类阈值和 Skill-only 沉淀流程；应先定义通用 Trace 和 Capability 模型。
- 依赖外部 GTS/运营 tracking 的闭环和 Electron UI；它们不是通用质量内核，且运行生效范围未由本次静态审计确认。

### 3.4 当前 Shutu 的 P0/P1 问题

P0（阻塞通用安全发布）：

1. 没有跨宿主 Trace、Adapter 和 MCP Observer，因此无法回答真实使用、工具链、版本和父子调用关系。证据：models.py:31-45 只有手工 EvolutionIssue；cli.py:26-132 只有本地命令；docs/技能工坊复刻说明.md:59-68 明确未集成宿主自动采集。
2. 测试结果可被测试后修改的补丁集合污染。源码确认：engine.py:250-279 的 accept_patch() 会改变 Patch.status 但不清除 Proposal.test_result/status；engine.py:303-340 的 test_proposal() 保存的是当时 accepted changes；engine.py:403-444 的 apply_proposal() 只检查旧 test_result 和 test_passed，之后又按当前 accepted changes 应用。复现条件是：至少两个补丁全部接受并测试通过，测试后接受或拒绝其中一个，再执行 apply。
3. 文件交换和 SQLite 提交不是一个可恢复事务。现有 apply/rollback 在 engine.py:342-519 中先处理文件再写版本/提案记录，store.py:32-163 没有 apply journal、锁或跨文件数据库事务；进程中断可能留下文件已换而 DB 未记账的状态。

P1（阻塞质量闭环，但可在 P0 后实施）：

1. Issue 没有 trace/session/occurrence/owner/attribution/dedup key，不能联合归因或重复问题聚合，见 models.py:31-45。
2. test_proposal() 仅做临时目录、文本替换、技能树和 symlink 静态验证，见 engine.py:303-340；没有真实 Agent 任务、MCP 调用、基线/候选对照。
3. 无并发锁、宿主 reload 确认、发布后观察或回滚触发策略；engine.py:403-519 只对单进程调用路径做异常恢复。

上述严重度是架构影响判断，不是运行时事故结论；本次没有启动应用或执行故障注入。

### 3.5 轨迹采集如何同时支持 Codex、OpenCode、MCP？

采用统一 Trace/TraceEvent 合约加三类采集适配器：

- Codex/OpenCode：优先使用各自可公开、稳定的 Agent Adapter 事件；至少提供 task/session/turn、model call、skill discovery/load/use、tool call/result、final output、status、timing 和 parent relation。
- MCP：优先透明 Observer；stdio 由本地子进程 stdin/stdout 代理解析 JSON-RPC，Streamable HTTP 由 HTTP/SSE 转发层记录 request/response/notification。Observer 只记录元数据和脱敏摘要，必须保持 id、错误、耗时和传输顺序。
- Server 内部埋点作为补充，而不是唯一来源；它能看到实现内部异常，但不能可靠看到 Agent 是否选择、重试或替换了工具。

事件必须携带 agent_session_id、turn_id、trace_id、parent_event_id、capability_id、capability_version、transport、request_id、status、started_at/ended_at、redaction_state。发现、加载、执行分别建事件；MCP tools/list 与 tools/call 分开。这样既能区分“应该触发却没有触发”，也能把一次 Skill 决策与后续 MCP 调用关联起来。

### 3.6 MCP 优化与 Skill 共用哪些模块？

共用：Capability/CapabilityVersion 注册与快照、Trace/TraceEvent、问题 Occurrence/Issue、Diagnosis、Evaluation、Proposal/Release、发布后 Observation、SQLite lease/journal/版本 lineage。

保持独立：Codex/OpenCode Agent Adapter、MCP stdio/HTTP Observer、Skill 文件 materializer、MCP 描述/Schema change-set、MCP Server 代码建议、不同宿主的 reload/health check。共享的是证据和发布协议，不是所有文件都使用同一种补丁格式。

### 3.7 哪些模块必须保持独立？

至少独立出：

- adapters/agent：宿主事件、会话和父子 Agent 关系；
- observers/mcp：协议代理、传输、tools/list/tools/call 和连接健康；
- capabilities：Skill、MCP Server、MCP Tool、Agent workflow 的版本化注册；
- diagnosis/evaluation：平台级证据、归因和 oracle；
- release/materializers：Skill 文件、MCP 描述/schema、MCP server source 的不同变更类型；
- host_effects/observation：reload、健康检查和发布后效果观察。

### 3.8 下一阶段首先实施什么？

最小范围为：SQLite additive migration；Capability/CapabilityVersion；Trace/TraceEvent ingest 与脱敏；一个 JSONL/HTTP Agent Adapter；只读 MCP stdio/Streamable HTTP Observer；将现有 SkillWorkshop snapshot/version 通过兼容 service 接入。先建立可观察数据和发布安全门禁，不先做 LLM diagnosis、自动 proposer 或 deposition。

### 3.9 是否仍有必要继续分析 DAAgent？

没有必要继续做大范围功能复刻或逆向。只有在以下三个问题影响实现时进行定向、只读核验：目标宿主事件权限/稳定性、DAAgent 运行时 reload 实际语义、专属模块授权和可引用接口。当前架构可以不依赖 DAAgent 私有模型先行实施。

### 3.10 是否存在阻碍通用架构实施的关键技术问题？

存在需要在首阶段验证的集成不确定性，但没有阻止架构落地的单一技术阻断。主要风险是 Codex/OpenCode 可用 hook 的稳定性、stdio/Streamable HTTP 代理的透明性、任务正确性的 oracle、隐私/Token 成本、Windows 文件交换与宿主重新加载。若某宿主没有可用事件源，应降级为显式 Adapter/代理或标记观测不完整，而不是伪造完整轨迹。

## 4. 当前能力与目标能力

| 指标 | DAAgent 静态能力 | Shutu 当前能力 | 目标补强 |
|---|---|---|---|
| 任务成功率 | 有 trajectory stats/evaluation 结构，实际 oracle 未确认 | 未发现 | 可重复任务集 + task oracle |
| Skill 触发准确率 | 有 Skill activation marker 和 eligible 筛选 | 未发现 | 应触发/发现/加载/执行事件与漏触发规则 |
| MCP 调用正确率 | 有 MCP server 协议实现；未见完整质量评估闭环 | 未发现 | Observer + schema/参数/结果校验 |
| 重试/失败率 | 轨迹和 error signal 有基础 | 未记录 | 统一状态与 retry classification |
| Token 成本 | 当前 runtime trace 未见模型 token/cost 字段 | 未发现 | Provider-neutral usage event |
| 耗时 | session/event timestamps 有基础 | 未发现 | event/span timing 与分位数 |
| 无效调用 | diagnosis/proposer 可读错误信号，非完整指标 | 未发现 | tool/schema/oracle 分类 |
| 回归失败率 | 有评估结果模型，但真实任务门禁未确认 | 未发现 | baseline/candidate paired eval |
| 问题复发率 | DAAgent 有 issue/occurrence/dedup；Shutu 无 | 未发现 | occurrence 聚合与发布后观察 |
| 修改后实际效果 | DAAgent 有使用 tracking 结构，闭环运行未确认 | 未发现 | release cohort/observation/rollback gate |

## 5. 架构决策记录

### 保留

- 本地 Skill 目录的安全边界和 symlink 检查；
- 快照内容哈希、版本父子关系和 rollback；
- 精确文本 patch 作为 Skill 指令变更的一种 change-set；
- 当前测试夹具和边界测试；
- CLI 作为本地开发/维修入口。

### 重构

- 将 Issue 从手工记录改为 Trace-backed Occurrence + 聚合 Issue；
- 将 Proposal 增加 evaluation fingerprint、accepted change digest、base capability version 和 release journal；
- 将 store 升级为带迁移、事务、busy timeout、row version、lease 的 repository；
- 将静态 test_proposal 改成 Evaluation service 的一个检查层；
- 将应用和回滚纳入可恢复 Release service，并加入宿主 reload/health 后置状态。

### 后置

- 自动聚类、LLM 提炼、Skill deposition；
- 代码级 MCP Server 修改建议；
- 自动发布和无人工确认的高风险门禁；
- 外部 GTS/远程 tracking、Electron UI。

## 6. 验收顺序

1. P0：补 schema migration、trace ingest、capability/version、proposal test fingerprint 和 release journal；补并发/崩溃恢复测试。
2. P1：实现 Codex/OpenCode Adapter 的最小事件集和 MCP stdio/Streamable HTTP Observer；补脱敏、父子 trace、错误分类。
3. P1：实现基线/候选 paired evaluation、任务 oracle 接口、回归门禁和发布后 observation。
4. P2：将多 owner 诊断、规则预筛选、LLM diagnosis/proposer 接入 evidence/occurrence。
5. P2：最后接入 deposition，要求任务级质量验证、重复技能检测和人工审核。

只有第 1-3 步具备可回放证据和失败恢复后，才建议把项目对外称为通用 Agent Skill/MCP 质量演化平台。

## 7. 尚未验证事项

- 无当前 Git HEAD，故无法对参考提交做历史 diff。
- 未运行 DAAgent、未执行真实 Agent 任务、未连接 GTS/远程 tracking；manifest、health capabilities、plugin config 不能证明线上已生效。
- 未做 Windows 进程崩溃、并发应用、宿主 reload、stdio/HTTP 代理透明性和实际 LLM 输出的运行验证。
- gdeclaw 专属模块的许可证和可再发布范围未确认。

## 8. 交付物

- DAAgent_Key_Mechanisms_Audit.md：源码调用链、数据模型、风险与证据索引。
- DAAgent_vs_Shutu_Workshop_Gap_Matrix.md：逐项能力差异和优先级矩阵。
- Shutu_Workshop_Generic_Architecture_Recommendations.md：通用模型、契约、迁移和实施阶段。
- Shutu_Workshop_Audit_Executive_Summary.md：本执行摘要与最终 CONDITIONAL GO 决策。
