# DAAgent 关键机制源码审计报告

审计日期：2026-10-24  
审计对象：已安装的 DAAgent 目录、Skill Evolution、Skill Deposition 及当前 Shutu-Skill-Workshop 工作树  
执行方式：只读源码/配置审计；未启动 DAAgent，未调用在线 GTS Skill Hub，未读取 default workspace 中的业务轨迹或数据库内容。

## 1. 基线、范围和证据等级

### 1.1 审计基线

当前工作区是一个尚未产生可解析提交的 Git 工作树：

- 当前分支为 main，但 git rev-parse --verify HEAD 失败，当前 HEAD 不存在。
- 参考提交 8c5ebe8da5aa33a7155edf53dd196bc6a09ed9ed 在本地对象库中也不存在。
- 因此本报告不能把参考提交作为基线；审计基线是当前工作树中的源码快照，差异记录为“无当前 HEAD，参考对象不可用”。
- 工作树在审计前已有 staged 新增文件和若干 untracked 临时目录/缓存；本次只新增 docs/audit 下的报告，没有修改已有业务代码、测试或数据库。
- 仓库根目录没有 .codegraph/，故没有使用 CodeGraph。

检查的主要路径：

- DAAgent/Lib/site-packages/gdeclaw/builtin_plugins/skill_evolution
- DAAgent/Lib/site-packages/gdeclaw/builtin_plugins/skill_deposition
- DAAgent/Lib/site-packages/gdeclaw/builtin_plugins/_skill_connector/evolution
- DAAgent/Lib/site-packages/gdeclaw/builtin_plugins/_skill_connector/deposition
- DAAgent/plugins/daagent/daagent_mcp
- DAAgent/plugins/daagent/plugin.json
- resources/app.asar 只检查了文件存在性和元数据；未解包
- .daagent/workspaces/default 只检查了目录元数据；未读取其内容

证据标记：

- SC（Source Confirmed）：源码中可直接追踪到的行为。
- CFG（Configuration Indicated）：配置、默认值或 manifest 表明的意图，不等于运行时已经启用。
- INF（Design Inference）：由多个源码事实推导出的架构结论。
- UNVERIFIED：本次没有运行或无法从只读源码确认。

### 1.2 总体判断

SC：DAAgent 的核心不是单个 proposer.py，而是“宿主钩子采集器 + 本地/sidecar HTTP 服务 + SQLite 任务/问题/提案库 + Skill 版本服务 + host-effect 回传”的组合。Skill Deposition 是另一条独立的“轨迹文件 + 聚类子进程 + LLM 验证/提炼 + 人工接受”流水线。

SC：DAAgent 已经具备较成熟的 Skill 专项问题台账、证据索引、版本新鲜度检查、应用操作日志和崩溃恢复设计。

SC：它仍然把 Skill 作为首要且近乎唯一的演化对象。MCP server/tool、Agent routing、模型调用、网络/权限依赖没有进入同一套可观察的能力模型。

INF：Shutu-Skill-Workshop 应保留当前的快照、补丁边界、版本族谱和回滚资产，但必须把 SkillWorkshop 从“全功能对象”降为兼容门面，新增独立的 Trace、Capability、Diagnosis、Evaluation、Release 和 Adapter 边界。

## 2. 真实调用链

### 2.1 Skill Evolution 调用链

源码链路如下：

1. builtin_plugins/__init__.py 的注册逻辑加载 Skill Evolution manifest/register。
2. skill_evolution/register.py:6-31 将注册转交给 _skill_connector.evolution.plugin。
3. _skill_connector/evolution/plugin.py:144-158 创建 SkillEvolutionServiceClient 和 SkillEvolutionRuntime；:379-403 注册宿主 startup/shutdown、on_session_start、pre_conv_llm_call、post_conv_llm_call、pre_tool_call、post_tool_call、on_session_end。
4. plugin.py:189-265 启动或连接 sidecar 服务；:304-331 注册 host effect，包括宿主 reload_agent、assessment refresh 和签名动作。
5. runtime.py:66-111 在会话开始建立内存 _SessionTrace；:113-181 收集 LLM 消息、工具调用和工具结果；:183-235 在会话结束通过 client.post_session_completed 提交。
6. evolution/client.py:220-234 将数据 POST 到 /api/v1/session-completed。
7. service/api/events.py:27-48 调用 application/ingestion.py:51-210 的 ingest_completed_session；完成后唤醒 diagnosis scheduler。
8. ingestion.py 记录 Skill 使用版本，然后按设置和 Skill 来源筛选候选，生成 SkillDiagnosisJob 并入库。
9. diagnosis_scheduler.py:194-327 负责调度、claim、lease、重试和完成回调；diagnosis_processor.py:28-236 负责分析、落 Issue、触发自动或人工提案。
10. diagnosis_analyzer.py:261-307 调用模型，解析并校验 SkillDiagnosisAnalysis；service_diagnosis.py:234-379 以置信度、可行动性、归因和证据索引为门槛写入 Issue/Occurrence。
11. proposer.py:476-541 组装 Skill 内容和轨迹上下文，模型输出严格的文件/patch JSON；service_proposals.py:29-178 建立版本基线、问题/Occurrence 关联和 Proposal。
12. service_evaluation.py:41-232 生成临时 overlay，完成静态检查，并可接收候选会话的前后对比；service_apply.py:248-355/408-687 负责 staging、journal、文件交换、版本提交和恢复。
13. 应用完成后由 host effect consumer 触发宿主 reload/assessment refresh；这一步来自宿主集成，不是纯 Skill 算法。

### 2.2 Skill Deposition 调用链

1. skill_deposition/register.py:1-7 注册 _skill_connector.deposition.plugin。
2. _skill_connector/deposition/plugin.py:20-78 启动 HostLlmBridgeServer、DepositionSidecar 和 SkillDepositionScheduler，并注册会话开始/结束活动钩子。
3. connector/router.py:340-652 把宿主 API 转发到独立 service/app.py；路由覆盖聚类、手工轨迹选择、验证、distillation、pending review、accept/abandon。
4. skill_deposition/service/app.py:45-76 组装 ClusteringRunner、ValidationService、DistillationService、AbandonmentService、DepositionPipeline 和 ReviewService。
5. pipeline.py:30-77 创建 deposition run；:162-206 先运行聚类，:215-355 对 cluster 逐个验证；运行状态落在 workspace/.skill_deposition/service 下的 JSON 文件。
6. clustering.py:30-225 启动 trajectory_clustering/run.py 子进程；run.py:407-680 负责输入预处理、全量/增量聚类、按 scope 分流和可选的 Stage 3 分析。
7. session_pipeline/features.py:306-421 规范化用户文本、工具序列、参数 key/value、文件名和事件 digest；core.py:535-640 做去重、TF-IDF 多通道特征和矩阵构造；:640-802 使用余弦层次聚类或 MiniBatchKMeans 粗分桶后细分。
8. validation.py:51-130 对 cluster 调 LLM，要求 can_distill_skill、skill_name、description、workflow_mermaid；:147-338 以 cluster fingerprint 缓存和判 stale。
9. distillation.py:49-124 要求模型输出 GeneratedSkill；:127-390 校验 Skill.md、路径和安全名称，写入临时目录再 promote。
10. review.py:31-438 管理 pending/accepted/abandoned；accept 时校验人工编辑后的所有文件、frontmatter、实际目录文件，并可验证宿主已 materialize。

### 2.3 运行状态声明

SC：service/app.py:172-192 的 health 返回 capabilities 和 schema 版本，app.py:232 创建 FastAPI app；这只说明源码具备健康端点和能力声明。

UNVERIFIED：本次没有启动 DAAgent、sidecar 或 FastAPI app，因此不能据此断言当前安装环境中的服务正在监听、插件已经成功注册、宿主钩子已经执行，或远端 tracking 已经可达。

## 3. A1：轨迹采集与上下文关联

### 3.1 采集来源

SC：主来源是宿主事件钩子，不是独立的通用日志采集服务。plugin.py:384-391 明确注册 session、LLM、tool 四类事件。

SC：runtime.py:19-37 的 _SessionTrace 只在内存中保存 session_id、agent_id、user_id、channel、turn_id、query、use_tools、use_skills、events、起止时间和 workspace_dir。runtime.py:214-277 在会话结束时组装 payload 并通过 HTTP 提交。

SC：trajectory.py:19-189 另有从保存的 QwenPaw session JSON、agent.state_dict() 和当前消息读取轨迹的能力，主要用于重新执行/评估上下文，不是主要实时采集通道。

结论：轨迹来源可以分为：

- 宿主 hook：实时、语义较好，但强绑定 DAAgent/QwenPaw hook 名称。
- 宿主已保存 session：用于补读和重新评估，强绑定保存格式。
- 诊断服务 SQLite：保存 diagnosis job、Issue、Occurrence 和引用的轨迹片段，不是原始实时事件总线。
- tracking HTTP：用于产品统计投影，不是诊断的可靠主存储。

### 3.2 实际记录内容

SC：SessionCompletedEvent（ingestion.py:27-42）字段包括 interaction_id、session_id、user/chat/channel、status、execution_status、query、use_tools、use_skills、trajectory_events、turn_started_at_ms、turn_ended_at_ms。

SC：runtime.py:113-143 将 LLM messages 和 assistant response 写成字符串事件；:145-181 将 tool_call 输入和 tool_result 写成字符串事件。输入参数被完整 JSON 序列化到事件，然后在 ingestion.py:174-180 才做一次文本脱敏。

SC：ingestion.py:95-128 调用 SkillVersionService.sync_worktree，将每个使用到的 Skill 关联到 version id 和 tree_hash，并写入 tracking usage mutation。

缺失或不充分之处：

- 没有独立的 model_call_id、provider、model、prompt token、completion token、成本字段。
- 没有 tool_call_id、MCP server identity、MCP transport、JSON-RPC id、HTTP status 或结构化参数/结果字段；工具调用和结果只是字符串。
- session 与 turn 的关系主要通过 session_id + 当前 turn_id 形成 interaction_id；事件本身没有 event_id、parent_event_id 或严格时间序列 ID。
- _SessionTrace 只有一个 session key；没有父子 session、parent_agent_id、sub-agent invocation 或 task span 字段。

### 3.3 Skill 发现/加载/执行

SC：diagnostic_policy.py:67-77 只在事件中寻找字符串标记 tool_call Skill input=，并从 JSON payload 提取 Skill 名称。它能证明“宿主的 Skill 工具被调用”，不能证明 Skill 已被发现或加载。

SC：SkillVersionService.sync_worktree 在 ingestion.py:95-128 记录使用时的目录版本。

因此三种状态要分开：

- 被发现：未发现独立事件或 registry snapshot。
- 被加载：未发现 loader/cache 命中事件。
- 被实际执行：对 DAAgent 的 native Skill tool 有近似证据，即 Skill tool call；不等于 Skill 内容确实被模型遵守。

SC：ingest_completed_session 只对 event.use_skills 非空且有合格 Skill 时入诊断队列。  
INF：这条过滤会丢弃“任务应该触发 Skill 但没有触发”的负样本，所以现有系统不能可靠计算触发召回率，也不能仅凭现有链路发现 missing trigger。

### 3.4 任务关联、失败和代价

SC：同一任务内的工具调用通过同一个 session trace 的 events 顺序关联；不同工具没有结构化 span 关联。on_session_end 在 error 非空时追加 system_error，status 非 ok 时提交 execution_status=failed。

SC：diagnosis schema.py:18-58 的 evolution_diagnosis_jobs 有 queued/running/completed/failed、attempt_count、next_attempt_at、lease_expires_at、last_error 和结果 ID；sqlite_repository.py:385-580 用 BEGIN IMMEDIATE、lease、renew、retry、expired recovery 和 complete 实现诊断作业可靠性。

SC：tracking.py:1112-1128 默认从环境变量或内置 endpoint 创建 HTTP backend；tracking_http_backend.py:61-108 使用 10 秒 timeout 和本地失败 JSONL 诊断日志。tracking 是 best-effort，不能替代本地诊断数据。

CFG：tracking 默认 endpoint、身份读取和事件字段表明可能存在远程数据投影，但本次未读取/发送任何数据，也未验证配置是否启用。

隐私/性能成本：

- hook 阶段会把 LLM 消息、工具参数和工具结果转成内存字符串；每个 session 的完整轨迹在结束前驻留内存。
- ingestion 的 redaction 发生在入诊断 job 前，tracking mutation 另有自己的投影路径；源码不能证明所有远程 mutation 都已去除敏感内容。
- 轨迹、Skill 快照和模型提示可能产生较高 token、磁盘和网络成本；scheduler 通过 idle/night 调度和 issue gate 控制成本。
- 远端 tracking 的身份函数会尝试读取员工/机器标识；报告不记录实际值。

### 3.5 对 Codex、OpenCode、MCP 的可移植性

SC：采集器直接依赖 DAAgent API 的 hook 名称、QwenPaw chat_manager、workspace resolver、Skill 工具 payload 和宿主 reload 回调。

结论：

- 纯概念可复用：session/turn/event、能力版本快照、完成后异步入队、脱敏和失败重试。
- 不能直接移植：runtime.py、QwenPaw session loader、native Skill marker、host bridge、reload_agent。
- Codex/OpenCode 需要 Adapter；MCP 需要独立透明观察器或 server 内埋点。
- “同一个任务”的通用 key 应由平台生成，至少包含 trace_id、parent_trace_id、session_id、turn_id、event_id 和 adapter instance，而不能只用 session_id。

## 4. A2：问题发现与诊断归因

### 4.1 触发与初筛

SC：events.py:27-48 在 session completed 入库后注册/唤醒 scheduler。diagnosis_scheduler.py:23-83 定义 immediate、idle、night schedule，默认 poll 30 秒、idle 10 分钟，:194-245 做 due check、claim 和处理。

SC：diagnosis_analyzer.py:350 附近的 detect_trajectory_error_signals 由 proposer.py:350-472 的错误信号逻辑配合，识别 tool_result 中的 JSON ok/success/status/exit_code/error/exception 以及 timeout、permission denied、connection refused 等文本。

SC：这是一层规则筛选，不是每条轨迹都直接调用 LLM。job 只有在有使用 Skill、有轨迹、有 eligible Skill 时才创建；auto gate 还要求严重度计数、冷却时间、无 active proposal 等条件。models.py:350-359 的默认门槛是 high=1、medium=2、low=3、cooldown=30 分钟、最多 5 个问题、每问题最多 3 个 occurrence。

### 4.2 LLM 输入/输出

SC：diagnosis_analyzer.py:165-258 构造输入：

- interaction/session/query/use_tools/use_skills；
- candidate Skill snapshot，包括 source、installed_from、eligible、root_candidate、activation_event_indices、content_hash、version_key、context；
- existing issue candidates；
- 带零基事件索引的完整轨迹；
- failure hints。

SC：diagnosis_analyzer.py:27-115 要求严格 JSON，单次最多 10 个 issue，字段包括 owner_skill、category、subtype、object、severity、title/summary en/zh、evidence_event_indices、related_skills、merge_issue_id、attribution、guidance、skill_gap、confidence、actionable、diagnosis_reason、skill_outcomes。

SC：parse_diagnosis_response（:118-162）验证 JSON、prompt_version、字段和内部事件索引；analyze_diagnosis_job（:261-307）最多尝试两次。

### 4.3 归因、聚合和去重

SC：models.py:394-451 将 attribution 限定为 skill_defect、agent_noncompliance、transient_environment、user_or_auth、unknown，并要求 evidence indices、confidence 0..1、actionable。

SC：service_diagnosis.py:234-287 只持久化 actionable、confidence >= 0.65 且 attribution=skill_defect、reason/guidance/skill_gap 不为空的 finding。

SC：service_diagnosis.py:289-379 校验 owner skill、证据索引、dedup_key 和可选 merge_issue_id；sqlite_repository.py:664-707 以同一 dedup key 聚合 IssueOccurrence；:1643-1760 负责问题合并、严重度提升和 resolved issue 重开。

SC：schema.py:60-130 的 issues/occurrences 表分别保留 dedup_key、severity、status、evidence_count、first_seen/last_seen、row_version、trajectory_excerpt、event_refs、error_signals、diagnosis_job_id。

结论：DAAgent 已有“证据门槛 + 归因枚举 + occurrence 聚合 + 置信度”的基础，但不是因果验证系统。是否真的改变 Skill 就能修复问题，主要依赖模型判断和后续 overlay/重新运行。

### 4.4 误归因风险

以下风险是源码可直接支持的设计推断，不是线上误归因统计：

1. ingestion.py:166-171 把 eligible_skills[0] 放入 job.skill_name，同时把所有 use_skills 作为候选；多 Skill 场景存在 root candidate 偏置。
2. diagnostic_policy.py 只认 native Skill 工具调用，不认隐式加载、系统注入、MCP tool 描述或 OpenCode skill 机制；MCP/Agent 问题可能被错误投影到 Skill。
3. timeout、权限和网络错误只由模型根据提示区分；规则层不能证明故障归属，且“有 error signal”会提高进入诊断的概率。
4. 没有反事实分支或最小干预实验；高 confidence 仍表示模型对证据的判断，不等于因果置信度。
5. 使用文本事件而不是结构化 error code/tool call id，重复字符串、组合错误和重试链可能导致错误合并。

要扩展为 Agent/Skill/MCP 联合诊断，必须把 owner 从单一 skill_name 扩为 capability candidates，并把每个 finding 绑定到事件 span、工具/服务器版本、环境指纹和 attribution evidence；仅扩展 attribution 枚举不够。

## 5. A3：LLM 优化提案

### 5.1 Proposer 读取的上下文

SC：proposer.py:476-541 的 generate_skill_evolution_result 接收 agent_id、skill_dir、skill_name、query、session_id、use_tools、use_skills、allow_scripts 和 trajectory_events。

SC：proposer.py:963-983 的 _collect_skill_context 递归读取 SKILL.md 及允许后缀（md/txt/py/js/ts/json/yaml/yml/toml/sh/ps1），单文件最多 20,000 字符，总上下文最多 50,000 字符。

SC：proposer.py:350-472 先抽取可重复的错误信号；:75-159 的 prompt 要求只返回 files[].patches[]，每个 patch 具备 old_string/new_string、reason、evidence/evidence_i18n、evidence_refs。

### 5.2 补丁控制与非法输出

SC：proposer.py:183-267 拒绝 new_content/full-file replacement，要求 patches[]；:954-961 解析 JSON/fenced JSON；:908-938 验证 patch 是否能对当前文件应用；:858-899 过滤脚本变更；:574-617 最多三轮模型审查和反馈重试。

SC：proposer.py:926-938 限制路径为 SKILL.md 或 references/templates/scripts/assets 下的相对路径。service.py:1217-1227 再次组装 change 并按 review_policy.py:12-24 对脚本、py/js/ps1/sh/ts 强制文件级审批。

SC：service_proposals.py:29-178 读取/保存 current skill package、同步 SkillVersionService 基线，并把 proposal 关联到 issue、occurrence、source session；每个 hunk 可以有 exact_patch_ref 或 proposal_fallback 证据。

SC：service.py:827-920 支持用户评论驱动的单 hunk 多轮 revision，保存 revision history，并把 active candidate 重置为 pending。

### 5.3 应用质量与提案范围

SC：Skill Evolution 的 patch 机制适合 Skill 文本、references 和有限脚本；没有 MCP tool schema 的结构化 diff、MCP server 代码变更、Agent routing 配置或多种 artifact 的统一 patch contract。

SC：service_evaluation.py:41-179 的 test_proposal 实际是临时目录复制、patch、skill scan 和 overlay materialization；其中 summary 明确写的是“通过 static checks，准备进行候选会话测试”。

SC：service_evaluation.py:204-232 接收 baseline_events/candidate_events 后生成 report；:415-618 的指标主要由 error/tool/assistant/answer 事件计数和启发式分数计算。它能比较轨迹表面信号，但不是任务级正确性 oracle。

因此必须区分：

- patch 可应用：old/new 文本能匹配，目录和 Skill 文件结构合法。
- patch 有效：候选版本在相同任务、相同环境下提高任务正确性或降低成本。

DAAgent 源码确认了第一项，第二项只有可选的候选会话和启发式比较，没有通用 A/B eval gate。

## 6. A4：审核、测试、应用和回滚

### 6.1 状态机和审核

SC：models.py:27-105 定义 proposal status，包括 generated、pending_review、partially_accepted、testing、test_passed、test_failed、approved、applied、rejected、superseded、reverted、apply_failed、applying；文件和 hunk 也各有 status。

SC：service.py:747-852 支持 hunk accept/reject/reset/revise；:986-1061 支持 file/all accept/reject；:1729-1760 根据子项状态刷新 Proposal status。代码文件的 review_policy 使脚本/代码文件不能只靠 hunk 操作。

SC：service_freshness.py:16-84 对 base version 和当前 worktree 做 tree hash/package fingerprint 检查；内容改变时把 proposal 标成 superseded，并保存 changed files；内容相同但产生了新 local head 时只 rebase base_version_id。

### 6.2 测试是否是真实 Agent 测试

结论：默认不是。

- SC：test_proposal 的第一阶段是静态 overlay 检查。
- SC：如果调用方真的用 overlay token 运行原问题，再把候选会话回传，才会生成 before/after evaluation report。
- SC：源码没有通用任务答案校验器、业务 oracle、固定任务集或回归门禁。
- UNVERIFIED：本次没有启动宿主验证 overlay 在实际 DAAgent 中能否被加载，也没有执行候选任务。

### 6.3 应用一致性、崩溃恢复和锁

SC：service_apply.py:248-300/408-558 的 existing apply 先创建 staging candidate，再 repository.begin_apply 写 journal，然后交换目录、commit version、记录 version_committed；:56-163、:605-762 在重启后按 apply operation 状态恢复、恢复 previous 或完成已提交版本。

SC：schema.py:290-316 的 apply operation 记录 prepared、files_swapped、version_committed、completed、failed；sqlite_repository.py:1263-1349 以事务记录开始 apply 和 operation。

SC：sqlite_repository.py:1351-1477 使用 WAL、foreign_keys、busy_timeout、BEGIN/BEGIN IMMEDIATE、schema migrations 和 row_version；service.py:2046-2060 使用 expected_row_version 防止审阅并发覆盖。

SC：skill_version/service.py:324-444 的 rollback 有 expected_head_version_id、备份目录和异常恢复；:578-585 的 _skill_lock 是进程内 per-workspace/agent/skill RLock。infrastructure/workspace_lock.py:13-39 另有 Windows/Unix 文件锁，用于 sidecar workspace lease。

限制：

- service_apply._prepare_apply_context（:408-461）验证的是当前版本、eligible、accepted changes 和 base version，没有看到“必须 test_passed 且 test_result 对应当前 accepted set”的硬门禁；API 调用者可以在静态检查前直接进入 apply staging。这个结论是源码确认的调用路径风险，不是运行复现。
- host reload 是 host effect；服务自身不能证明宿主已经重新加载。plugin.py:304-331 只显示注册 reload_agent 回调。
- DAAgent 的整体一致性依赖 sidecar workspace lease、SQLite journal 和恢复代码共同成立；若绕过 sidecar 直接实例化 service，只有进程内 Skill lock，保护范围变窄。

### 6.4 版本族谱和回滚

SC：SkillVersionService 有 baseline、manual、evolution、fork、rollback source，保存 files_snapshot、tree_hash、package_fingerprint、parent_version_id、lineage 和 proposal_id；rollback 不是删除历史，而是创建新的 rollback version。

这是值得保留的设计：回滚是新版本事件，且能保留 parent/head 关系。需要移植的是“不可变候选 + journal + expected parent + recover”，不是具体目录名。

## 7. A5：技能沉淀机制

### 7.1 轨迹选择和预处理

SC：trajectory_clustering/session_pipeline/raw_normalization.py 与 features.py:41-421 递归读取 JSON/JSONL，规范化文件名、URL、UUID、长数字、路径和参数，提取 user_text、system_output、tool_sequence、parameter_keys、parameter_values、event_digest_sequence。

SC：core.py:535-583 的 prepare 使用 input signature、已知 fingerprints、minimum event 数、read_skill_md、explicit_skill_intent 和 output overlap 去重；同一 archive group 的事件序列还会做 equal/contiguous_subset/ordered_content_subset 折叠。

这意味着它主要按行为文本/工具序列聚类，不是直接按 Skill 版本或任务答案聚类。

### 7.2 聚类算法和门槛

SC：config.yaml:16-28 使用 semantic/tool/parameter key/parameter value 四通道加权，默认权重约为 0.55/0.15/0.10/0.20，distance_threshold=0.55，incremental_similarity_threshold=0.45，random_seed=42。

SC：core.py:599-740 用 char n-gram TF-IDF、稀疏矩阵和 cosine；小规模用 AgglomerativeClustering，大规模先 MiniBatchKMeans 粗分桶再做 cosine hierarchical refinement；:861-938 以 cluster prototype 做增量归属。

SC：pipeline.py:31 定义 MIN_CLUSTER_SIZE=3；config.yaml:35-36 另有 single/cross min_cluster_size=2，run.py:528-565 把配置传给外部 Stage 3 分析。两套门槛承担不同层级：聚类输出可以保留 singleton，但 pipeline 验证阶段默认跳过少于 3 条的 cluster。

INF：数量门槛的设计意图是降低 LLM 验证/提炼成本并避免单条偶然轨迹直接固化；源码没有证明“3”是统计学上足够的通用阈值。

### 7.3 验证、提炼和审核

SC：validation.py:51-130 的 LLM 输入是完整 cluster 数据压缩体，输出严格四字段；ValidationService:147-338 用 cluster_fingerprint 做缓存和 stale 检查。

SC：pipeline.py:296-355 对每个 cluster 先 skip below_min_size/unchanged/distilled/abandoned，再调用 validation。未通过验证的 cluster 不会进入 distillation。

SC：distillation.py:49-124 要求模型生成符合 GeneratedSkill 的文件集合，必须包含 SKILL.md，名称必须与 validation.skill_name 一致；:263-390 只允许安全相对路径，使用临时目录 promote，失败删除临时目录。

SC：review.py:112-188 支持 abandon/accept；:212-355 校验最终名称、文件集合、SKILL.md frontmatter、实际文件内容和额外文件。connector/router.py:594-652 的 host_materialized 路径还会反向核对宿主目录。

### 7.4 缺口和不宜直接复刻的部分

- 没有源码确认“已有 Skill 覆盖新能力”的语义匹配；主要是 cluster fingerprint、validation 和名称冲突。
- 生成的 Skill 仍是 LLM 内容，验证规则检查格式和流程共性，不能证明新 Skill 在独立任务集上可复用。
- 错误轨迹会先进入 cluster；虽然 prompt 禁止编造工具/结果，仍缺少任务级成功 oracle。
- manual cluster 使用进程内 transient storage（assignments.py:12-45），跨进程恢复能力弱。
- deposition run 使用 workspace JSON 文件和进程内 task map；pipeline.get 能把无任务的 queued/clustering/validating 标成 interrupted，但不能恢复中断的聚类过程。
- scheduler.py:45-254 以 idle/night window 和 scheduler_state.json 去重；同一 scheduler loop 每轮只返回一个 workspace，多个 workspace 需要多轮处理。

值得借鉴的是“去重 fingerprint、cluster stale、LLM 验证前置、人工编辑后再接受、临时目录 promote、宿主 materialization 反校验”。不应直接复刻固定 TF-IDF 权重、文件命名规则、单一 Skill 生成模型或 DAAgent session JSON 格式。

## 8. A6：宿主耦合、独立化和授权

### 8.1 纯算法与耦合边界

可相对独立的模块：

- diagnosis prompt/schema 校验、confidence/attribution/dedup 规则；
- text patch/path validation；
- snapshot/tree hash/version lineage；
- deposition 的特征提取、fingerprint、cluster orchestration；
- SQLite job lease、row_version、apply journal 的模式。

必须 Adapter 化的模块：

- DAAgent hook names 和 QwenPaw session/chat_manager；
- workspace registry、sidecar、FastAPI/Bearer auth、host bridge；
- DAAgent model factory 和 host LLM bridge；
- Skill manifest origin、GTS Skill Hub eligibility；
- host reload_agent、sign_skill、assessment refresh；
- 远程 tracking endpoint、员工/机器身份；
- DAAgent MCP server 的具体启动和路由。

### 8.2 MCP 源码边界

SC：plugins/daagent/daagent_mcp/protocol.py:13-269 是一个 DAAgent MCP provider，定义 initialize、tools/list、tools/call、ToolRegistry；server.py:44-170 管理连接和消息路由；router.py:4-139 实现 GET/POST /sse 的 HTTP/SSE 入口。

SC：该 MCP server 的工具是 SmartDataCube、环境和设计时工具；在 skill_evolution 与 skill_deposition 目录中没有找到 MCP Observer、工具 schema snapshot、JSON-RPC call/result correlation 或 MCP-specific diagnosis。

CFG：plugins/daagent/plugin.json:20-29 表明 DAAgent Enhanced Plugin 的 MCP 配置默认 enabled、127.0.0.1、8089；这只是 manifest 配置，不能证明服务当前运行。

结论：DAAgent 的 MCP 部分是“被观察/被使用的服务提供者”，不是“质量观察器”。目标平台应独立实现 MCP Observer。

### 8.3 授权和代码复用限制

SC：site-packages/COPYING 是带有 Stavros Korokithakis/Stochastic Technologies 署名的 BSD 风格第三方许可证文本；它没有把 gdeclaw、skill_evolution 或 DAAgent 专属代码标为同一许可证。

SC：已检查的 gdeclaw 目录本身没有独立 LICENSE/NOTICE 文件，且没有找到能明确覆盖上述专属模块的包级授权说明。

UNVERIFIED：上游仓库、DAAgent 专属代码的完整版权链、第三方依赖逐文件许可证和再发布条款没有在本次离线安装审计中得到完整确认。

合规结论：本报告只提取功能需求、状态、数据结构概念和设计经验；不建议复制、移植或重新发布 DAAgent/gdeclaw 源码。在授权未明确前，技术上可复制不等于法律上允许复用。

## 9. A7：MCP 质量改进扩展

DAAgent 没有现成 Observer，因此以下是目标平台架构建议，不是 DAAgent 已实现能力。

三种采集方式：

| 方式 | 能看到什么 | 优点 | 代价/限制 | 建议 |
|---|---|---|---|---|
| Agent 原生事件 | Agent 选择、Skill 触发、模型/工具语义、父子 Agent | 语义最完整，能发现 routing/missing trigger | 绑定 Codex/OpenCode 宿主接口，版本漂移 | 作为 Adapter 增强源 |
| MCP 透明代理 | initialize、tools/list、tools/call、JSON-RPC id、请求/结果、延迟、断开 | 对 Agent 无侵入，stdio/HTTP 都可覆盖 | 需维护转发进程和 framing/session；流式响应关联复杂 | 首批 Observer 主路径 |
| MCP Server 内埋点 | 真实实现内部错误、下游调用、资源和权限 | 能定位 server 内部根因 | 需改每个 server，无法覆盖第三方 | 作为可选增强源 |

stdio 代理必须保持协议输出和日志隔离、保留子进程退出码/信号、按 JSON-RPC id 配对 request/result，并且记录启动参数和 server identity 的 hash。Streamable HTTP 代理必须保留 HTTP status、headers 中的 session 线索、POST 请求与 SSE/流式响应的关联、连接/重试/取消和服务端断开。不要把 DAAgent 的 /sse 路由当成所有 MCP 实现的通用 framing。

平台需要保存：

- server identity：transport、endpoint/启动命令的脱敏标识、initialize serverInfo/protocolVersion/capabilities；
- tool snapshot：工具名、description、inputSchema、输出 schema（若有）和 schema_hash；
- call history：trace_id、tool_call_id、jsonrpc_id、tool_snapshot_id、参数摘要/脱敏 payload、result/error、latency、retry_index；
- version fallback：服务没有版本号时记录 unknown，不从文件名猜版本。

## 10. A8：质量评估与持续反馈

| 指标 | DAAgent 结论 | 依据 |
|---|---|---|
| 任务成功率 | 部分实现/数据基础 | SessionCompletedEvent 有 execution_status；evaluation 只有事件启发式，非任务 oracle（ingestion.py:27-42，service_evaluation.py:204-232） |
| Skill 触发准确率 | 未实现 | 只记录 native Skill tool 使用，且无 missing-trigger 负样本（diagnostic_policy.py:67-77） |
| MCP 工具调用正确率 | 未发现 | Skill Evolution/Deposition 没有 MCP call schema 或 result 质量判定 |
| 重试与失败率 | 部分实现 | error signals、attempt/lease/retry 存在；没有结构化 retry span 和工具级失败率 |
| Token 成本 | 未发现 | 轨迹没有 model/token/cost 字段 |
| 执行耗时 | 只有粗粒度基础 | session 起止时间和 run duration 有；无模型/工具/MCP span latency |
| 无效工具调用 | 未实现 | 没有参数 schema 对照、重复调用或无效调用规则 |
| 回归失败率 | 部分实现 | 可接收 baseline/candidate 事件并算启发式指标，无固定任务集/回归门禁 |
| 问题复发率 | 部分实现 | IssueOccurrence、first/last seen、evidence_count 和 auto gate 存在 |
| 修改后真实使用效果 | 部分实现/未形成闭环 | 有 tracking、assessment snapshot 和可选候选重跑，但没有跨版本长期生产效果门禁 |

通用平台应该把“baseline/candidate artifact hash + 固定任务集 + 环境指纹 + 可重复执行 + 任务级 oracle + 性能/成本 + 回归门禁 + 发布后观察”作为 Evaluation Contract，而不是沿用事件计数作为 correctness。

## 11. 主要架构问题

1. 轨迹层和诊断层以 DAAgent native Skill tool 为前提，无法作为 Codex/OpenCode/MCP 通用观察基础。
2. 原始事件是字符串列表，模型调用、工具调用、MCP call、重试和父子关系不可精确查询。
3. 根因归因是模型结构化判断 + 规则阈值，没有通用反事实验证。
4. Proposer 的文件 patch contract 只覆盖 Skill 包，不覆盖 MCP schema、server 代码和 Agent routing。
5. overlay test 默认是静态检查；候选任务的真实正确性需要宿主额外配合，且没有通用 oracle。
6. host reload、Skill integrity、GTS origin 和 remote tracking 都属于 DAAgent 专属边界。
7. Deposition 的聚类质量依赖 DAAgent session 文件和固定特征，不应成为通用平台的第一层核心。

## 12. 未验证事项清单

- 当前 DAAgent 安装是否能成功启动并注册上述插件。
- Skill Evolution/Deposition sidecar 当前是否监听，健康端点和 bearer token 是否有效。
- 当前运行配置是否启用远端 tracking，远端 endpoint 是否可达。
- overlay candidate 是否真的改变 DAAgent 的 Skill 加载路径。
- default workspace 中是否存在与源码模型对应的真实数据量、失败率和版本记录。
- app.asar 内是否有 UI 对这些 API 的额外调用；本次没有解包。
- 当前 Shutu 工作树的 pytest 是否通过；本次没有运行，以避免改变工作区缓存/状态。

## 13. 关键源码证据索引

| 编号 | 证据 | 结论 |
|---|---|---|
| E1 | _skill_connector/evolution/plugin.py:379-403；runtime.py:66-235 | 宿主 hook 是 Skill Evolution 轨迹主入口 |
| E2 | runtime.py:19-37、:145-181、:376-405 | 轨迹字段以单 session 字符串事件为主 |
| E3 | ingestion.py:27-210 | session completed 入队、Skill 版本同步、eligible 过滤 |
| E4 | diagnostic_policy.py:45-77 | 只识别 native Skill tool activation |
| E5 | diagnosis_analyzer.py:27-115、:165-307 | LLM 诊断输入/输出、严格 JSON、两次尝试 |
| E6 | service_diagnosis.py:234-379 | confidence/actionable/attribution 门槛和证据持久化 |
| E7 | schema.py:18-332；sqlite_repository.py:385-707 | job lease、Issue/Occurrence、dedup 和状态 |
| E8 | proposer.py:476-541、:963-1038 | Proposer 上下文边界和 prompt contract |
| E9 | service_proposals.py:29-178；service.py:747-1061 | Proposal 证据关联、hunk/file 审核和 revision |
| E10 | service_evaluation.py:41-232、:415-618 | 静态 overlay 和事件启发式 eval |
| E11 | service_apply.py:56-163、:248-355、:408-687 | apply journal、staging、恢复及应用门禁边界 |
| E12 | skill_version/service.py:324-444、:511-585；workspace_lock.py:13-39 | 版本族谱、rollback 备份、锁 |
| E13 | skill_deposition/pipeline.py:30-355 | 沉淀运行、cluster 验证、去重/跳过 |
| E14 | trajectory_clustering/config.yaml:16-40；session_pipeline/core.py:535-940 | 特征、TF-IDF、聚类和增量阈值 |
| E15 | validation.py:51-338；distillation.py:49-390；review.py:112-355 | LLM 验证、Skill 提炼和人工接受 |
| E16 | plugins/daagent/daagent_mcp/protocol.py:13-269；router.py:4-139 | DAAgent MCP provider，不是 Observer |
| E17 | Shutu servers/plugins/daagent/skill_workshop/engine.py:207-519 | 当前项目补丁、静态 test、文件交换、版本和 rollback |
| E18 | Shutu servers/plugins/daagent/skill_workshop/store.py:26-163 | 当前 SQLite 只有 issues/proposals/versions，无 journal/lock/trace/eval |

