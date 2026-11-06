# Agent Harness 专项知识补充

日期：2026-11-06，Asia/Shanghai。
本次任务是扩充资料库，不是在本项目中实现下列所有 Harness 功能。
问答模型仍为 `glm-5.3-flashx`，没有切换为资料中使用的其他模型。

清理说明：2026-11-06 按用户要求删除本轮生成的原始盘点、采集回执、
检索和问答输出，仅保留可复跑题集、脚本及下文历史结果与失败摘要。
文中的原始输出路径仅用于标识当时运行，当前不再存在；
生产知识及原文快照未删除。保留清单见 `data/acceptance/README.md`。

## 缺口判断方法

补采前实际库为版本 16、16 个来源、2059 个活动片段。
原有 13 个 GitHub 仓库通常只有六个已采集文件，许多是 README 或配置，
不能因项目本身具备某项能力，就认为本地库已经有对应实现。

`scripts/harness_inventory.py` 对现有片段做关键词候选盘点，结合采集路径人工检查：

| 主题 | 补采前关键词候选 | 判断 |
| --- | ---: | --- |
| Memory recall | 1 | 仅汇编 README 链接，缺乏具体机制 |
| Prompt Cache | 4 | 主要是清单提及，缺少参数和用法 |
| A2A | 1 | 清单链接不足以解释协议 |
| ACP | 2 | 需要区分两个同名协议 |
| Retry policy | 0 | 本轮匹配规则没有命中，不等于所有项目都不支持重试 |
| GraphRAG | 21 | 多为概览或链接，缺少 local/global search 材料 |
| Agentic RAG | 31 | 多为 RAGFlow 简介，需要实际检索决策与改写循环 |

其他主题即使有关键词命中，也存在缺少第一方定义、设计边界或实现文件的问题。
候选数量仅用于发现缺口，不是质量评分、完整覆盖率或语义充分性证明。
补采前候选及 GitHub 搜索结果当时输出至（现已清理）
`data/acceptance/2026-11-06-harness-before/`。

## 清单与来源

`seeds.json` 保留原 13 库并加入用户指定的：

- `microsoft/agent-framework`：项目自身材料。
- `crewAIInc/crewAI`：项目自身材料。
- `shareAI-lab/learn-claude-code`：教程，不标为 Claude Code 官方源码。

另用 `harness-sources.json` 显式列出 14 个相关仓库的 82 个目标文件，
其中三个与上述新增库重合。只读取选定文档和源码，不执行仓库代码。
每个仓库仍受文件数、字节数及单文件上限约束。

| 主题 | 定向材料 |
| --- | --- |
| System / Context | Agent Framework run context、context middleware 决策文档 |
| Memory 压缩与召回 | learn-claude-code s09、Agent Framework file memory、CrewAI memory、Deep Agents memory middleware |
| Retrieval / Agentic RAG | LangChain 官方文档与完整 Python 教程，检索工具、相关性判断和问题改写 |
| Context Compression | learn-claude-code s08 文档/代码、Agent Framework compaction 策略/代码、Deep Agents summarization |
| Prompt Cache | LangChain models 文档与 OpenAI prompt cache 参数/用量代码示例 |
| Tool Schema / MCP | MCP 指定版本的架构、工具 Schema、授权及安全规范；教程 s02、s14 |
| Skills | Agent Skills 规范、教程渐进式加载、Agent Framework Skills 设计 |
| A2A | 官方 key concepts、Agent Card/discovery、A2A 与 MCP 的职责区别 |
| ACP | Agent Client Protocol 的 overview/tool-calls/security；旧 Agent Communication Protocol 的项目 README |
| 权限分级 / HITL | 教程 allow/ask/deny、Agent Framework tool approval、CrewAI human feedback、LangGraph interrupts |
| Sandbox | E2B README/SDK；Deep Agents sandbox 抽象及 QuickJS 后端说明 |
| Retry | LangGraph RetryPolicy 类型与 Pregel 重试实现 |
| Cost Guard | LiteLLM SDK BudgetManager、代理会话预算调用前拦截源码及预算资源配置 |
| Trace / Observability | CrewAI tracing 与 observability 文档 |
| Replay | LangGraph persistence/time travel、内部 replay 及教程工作流 journal |
| GraphRAG | Microsoft GraphRAG local/global search 文档和 local search 实现 |

### 重要边界

- ACP 不是唯一缩写：Agent Client Protocol 面向 agent 与客户端；
  另保留 i-am-bee 的 Agent Communication Protocol 材料，项目状态以所采 README 为准，
  不把两个 ACP、A2A 和 MCP 当作同一个协议。
- MCP 选取 `2026-07-28` 规范目录；ACP 材料来自 `protocol/v2`；
  CrewAI 材料来自 `docs/edge/en`。引用时保留版本/分支语境，不笼统保证旧版本兼容。
- Agent Framework 的 `docs/decisions` 是设计决策，不自动等于所有语言/版本都实现；
  同时采集相应 Python 实现，回答需区分两类证据。
  例如本次读到的 `0015-agent-run-context.md` 明确标为 `status: proposed`，
  不能单凭其中的 .NET 示例声称当前所有 SDK 已支持该 API。
- GraphRAG 是使用图结构和社区报告等上下文的检索方案；
  Agentic RAG 强调检索、判断、改写等决策流程，可以组合，但不是同义词。
  两者是 Harness 可利用的知识获取机制，不是重试/审批本身。
- 记录 token 成本不等于强制预算拦截，prompt caching 也不等于本助手的 Redis 问答缓存。
  供应商缓存参数是知识材料，不代表 GLM 网关已实现这些参数。
  LiteLLM `budget_manager.py` 明确不是代理预算管理器；本轮另采集
  `litellm/proxy/hooks/max_budget_per_session_limiter.py`，其调用前钩子在已用费用
  达到配置限额时拒绝后续调用，成功响应后计费。它不是并发费用预留证明，
  Redis 故障后的进程内回退也不等于多实例共享预算保障。
- Sandbox 基类、工具权限规则和实际隔离运行环境要区分；本次没有启动 E2B 沙箱，
  也没有执行任何被采集代码验证其安全性。
- 当前仍是定向、局部知识库，不是完整仓库、所有协议版本或整个 Harness 领域的全覆盖。

## 执行与复查

先检查配置与服务，再串行采集；不要并发写同一知识目录。

```bash
uv run ka doctor
uv run python scripts/harness_inventory.py --search \
  --output data/acceptance/自行指定的全新盘点目录
uv run python scripts/import_harness_sources.py \
  --output data/acceptance/自行指定的全新导入目录
uv run python scripts/verify_harness_knowledge.py --qa \
  --output data/acceptance/自行指定的全新验证目录
uv run python scripts/verify_harness_knowledge.py --qa \
  --checks eval/harness_followup_checks.json \
  --output data/acceptance/自行指定的全新补验目录
```

`harness-sources.json` 的 `paths` 由专项脚本处理，
不要将 `ka seed --catalog harness-sources.json` 当作等价定向采集命令。
可给专项脚本重复传入 `--repo owner/repo` 只运行选定条目。
补采时合并同来源已有采集路径，选定正文未完整取得则拒绝发布；
仍使用现有“向量写入校验后发布活动版本”的流程。
手动 `ka sync` 也保留来源的定向文件选择及大小限制，避免重新普通抽样丢失专项文件。

`eval/harness_checks.json` 冻结 19 个主题检索问题和预期文件；
验证真实 Embedding / Milvus / BGE 能否召回目标材料，不把这个检查当作答案事实正确率。
`--qa` 另用真实 CLI、远程 GLM 和新会话进行 local-only 问答，
关闭浏览器、清空搜索凭证，验证已保存材料可供复用。
人工还需核对回答与引用，尤其是协议定位、压缩机制、权限边界和版本声明。

本轮轻量执行记录当时分别输出至 `2026-11-06-harness-core`、
`2026-11-06-harness-specialists` 及后续验证目录的 `results.json` / `report.json`。
当时记录了成功、失败、commit、路径、片段数、嵌入数和知识版本，
原始运行记录现已清理；来源元数据及原文仍在生产知识库与快照中。

## 实际入库结果

14 个来源首轮全部成功，随后补齐 CrewAI Flow 反馈指南、E2B 同步生命周期及
LiteLLM 代理会话预算拦截实现。
当前知识版本 **33**，来源 **16 → 30**，活动片段 **2059 → 4381**。
新增 **82 个文件、2322 个活动片段**，原有 2059 个片段仍在。
三次增量补采分别只新嵌入 44、43、15 个片段，未变更正文复用已有向量。

| 仓库 | 文件 | 活动片段 |
| --- | ---: | ---: |
| microsoft/agent-framework | 11 | 586 |
| crewAIInc/crewAI | 9 | 294 |
| shareAI-lab/learn-claude-code | 15 | 382 |
| a2aproject/A2A | 5 | 52 |
| agentclientprotocol/agent-client-protocol | 4 | 75 |
| i-am-bee/acp | 1 | 14 |
| microsoft/graphrag | 5 | 40 |
| langchain-ai/docs | 8 | 238 |
| langchain-ai/langgraph | 4 | 107 |
| langchain-ai/deepagents | 6 | 253 |
| e2b-dev/E2B | 3 | 63 |
| modelcontextprotocol/modelcontextprotocol | 5 | 95 |
| agentskills/agentskills | 2 | 26 |
| BerriAI/litellm | 4 | 97 |

全部 30 个来源的活动片段与生产快照重新切分结果一致，无需修复。
版本 32 的逐来源 commit 与统计当时输出至
`data/acceptance/2026-11-06-harness-after/production-summary.json`；
最终盘点当时输出至 `2026-11-06-harness-final/inventory.json`，
版本 33 增量与最终快照核对当时输出至 `2026-11-06-harness-cost-guard/`，现均已清理。
这证明入库及原文一致性，不等于所有片段都能在任意自然语言问题中被召回。

## 真实验证与剩余缺口

- 版本 32 的冻结 19 题：真实本地 Embedding、Milvus、中文 BM25、RRF 与 BGE
  重排均运行，19/19 题在最多 8 个上下文中取得预期仓库和文件。
- 同版 5 题 GLM 新会话、禁用外部检索问答：4/5 达到证据充分条件。
  同题再次请求时清空模型凭证并将 Milvus 地址设为不可用，仍得到完全一致的
  Redis 缓存回答。报告中的 5/6 是四道问答加一道缓存检查，不是六道问答。
- **未通过项保留**：综合 memory selection/extraction/consolidation/recall 问题
  未召回 selection/recall 的具体实现，`sufficient=false`，脚本退出 1。
  模型明确说明不知道，未将其记为成功缓存。源码已经入库，不等于综合问题能完整召回。
  另设独立补验题，不修改原 19 题，也不将定向问题的通过替换原失败。
- 版本 33 独立补验：记忆选择/加载、代理会话预算拦截两题均通过检索及真实 GLM
  local-only 问答，额外缓存检查通过，脚本退出 0（报告为 2/2 检索、3/3 问答/缓存项）。
  人工核对了选择索引校验、关键词回退、加载预算，以及费用阈值拦截与成功后记账。
  记忆回答的“每轮”应精确理解为每次 `agent_loop` 调用前，而不是每次工具循环都重新加载；
  预算回答只召回 Redis 回退的部分代码，未完整说明读取/递增的回退路径，
  模型的充分性判断不能替代逐条事实和覆盖度核验。
- 人工抽查了 Context ADR、Flow 审批、两个 ACP/A2A、GraphRAG/Agentic RAG 的核心引用。
  Context 回答未将提案当成已实现；协议回答的“成熟”属于未验证评价，
  `protocol/v2` 路径只能确认材料版本语境，不能单凭它断言发布状态；
  Agentic RAG 的相关性循环也不能证明答案质量必然得到保证。
- 真实 `ka sync i-am-bee/acp` 保留一个目标文件，`embedded=0`、知识版本仍为 33。
- 普通回归 132 passed、7 skipped；跳过项为显式开启的真实服务测试，
  本节上述真实运行另外执行。没有为本轮重新运行 RAGAs，
  历史版本 16 的评分不能代表当前扩充后的知识库质量。
- Ruff lint、依赖锁、本轮修改的 7 个 Python 文件格式检查通过。
  全项目 format 检查仍有 `reranking.py`、`retrieval.py`、`storage.py`
  三个既有文件需要排版，本轮未修改它们，不宣称全项目 format 通过。

完整输出当时位于 `data/acceptance/2026-11-06-harness-verification/`；
独立源码补验当时位于 `2026-11-06-harness-focused-verification/`，现均已清理。
