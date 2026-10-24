# M1 Capability / Trace 基础层验收

## 测试命令

在 Windows PowerShell 仓库根目录执行：

```powershell
$env:PYTHONPATH = "servers"
python -m unittest discover -s tests -v
```

独立 M1 测试：

```powershell
python -m unittest tests.test_m1_capability_trace -v
```

M0 CLI 冒烟仍使用：

```powershell
python -m plugins.daagent.skill_workshop --help
```

## 实测结果

本次 Windows/Python 3.14 验证记录：

| 范围 | 结果 |
| --- | --- |
| M1 专项测试 | `11/11 passed` |
| 全量回归测试 | `24/24 passed`（M0 13 + M1 11） |
| M0 回归测试 | 见最终命令输出；必须全绿 |
| 编译检查 | `python -m compileall -q servers/shutu_workshop` 通过 |
| JSONL 夹具 | Codex/OpenCode 均可导入、按 `seq` 查询 |
| HTTP | 未授权 401；loopback + token 合法写入 |

专项测试覆盖 G1/G2/G3/G4/G5/G6/G7/G8/G9/G10/G11/G12/G13/G14；M0 全量命令覆盖 G15/G16。

## 通过门禁

- [x] 空库和 M0 旧库增量迁移；重复迁移幂等。
- [x] 不删除 `issues`、`proposals`、`versions`、`release_operations`。
- [x] Skill `content_hash`、M0 version ID 和 parent lineage 可关联。
- [x] MCP server/tool 虚拟快照保存 schema hash、来源和未知 provider version。
- [x] Codex/OpenCode 共享核心 Trace Schema，同时保留 `adapter_id`。
- [x] MCP tool call/result、错误、span 和 retry 可以关联。
- [x] 重复来源事件幂等；同一 `seq` 或来源 ID 的不同内容显式冲突。
- [x] 批次验证/脱敏/引用检查失败回滚，不保留半批数据。
- [x] SQLite 写入设置外键、busy timeout、事务和查询索引；线程并发基础场景通过。
- [x] Authorization、Cookie、Bearer、password 和个人路径在 capture-time 脱敏后才写入数据库。
- [x] HTTP 默认不启动；缺 token 不创建，绑定限制为 loopback。
- [x] 提供显式 payload 保留清理入口，清理不删除事件关联。
- [x] M0 CLI 和 release journal 代码未被 M1 core 依赖或重写。

## 观察缺口和已知限制

`SIMULATED_PASS` 只表示平台能够保存和检索夹具中的字段，不表示真实 Codex/OpenCode/MCP 已经向平台提供这些字段。当前缺少真实宿主事件源、MCP stdio/Streamable HTTP 旁路、任务正确性 oracle、真实 token/cost 语义、进程崩溃恢复和跨进程压力验证。HTTP 仅用于本地测试，不是生产 API。

## 下一阶段 Adapter 所需字段

真实 Adapter 至少应提供：

`adapter_id`、`adapter_version`、workspace/session/turn/trace ID、宿主原生 event ID、seq 或可靠时间顺序、事件类型、actor、span/parent span、能力 ID 与当时 `capability_version_id`、MCP server/tool identity、JSON-RPC request ID、transport、请求/响应状态、错误分类、retry 关联、started/ended time、观测完整度、脱敏状态，以及明确的任务结果标签。缺失字段必须报告为 `unknown`/`not_observed`，不能由 M1 推断。

## 状态

M1 core、迁移、JSONL、loopback HTTP、脱敏、幂等和模拟夹具已实现。真实适配器状态保持：

```text
CODEX_REAL_ADAPTER=NOT_IMPLEMENTED
OPENCODE_REAL_ADAPTER=NOT_IMPLEMENTED
MCP_REAL_OBSERVER=NOT_IMPLEMENTED
NEXT_STAGE=M2_ADAPTER_AND_MCP_OBSERVER
```
