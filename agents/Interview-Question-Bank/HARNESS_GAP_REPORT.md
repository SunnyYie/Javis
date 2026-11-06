# Agent Harness 定向补题

日期：2026-11-06。生成与核验模型仅使用 `glm-5.3-flashx`。

## 调研与缺口判断

本轮基线为正式题库 1,156 题、29 类。只追加题目，不重分类、不修改旧题，不设置整库总题数上限。

对标题和题目正文分别审计，再结合已有题目内容与新仓库原始资料选择实现级缺口。下表是补题前的标题关键词命中数，不代表完整语义评估；零命中也不代表从未提及相邻概念。

| 方向 | 旧题标题命中数 | 判断与补题范围 |
| --- | ---: | --- |
| Microsoft Agent Framework | 0 | 增加 Agent/Workflow、运行响应、上下文中间件、审批与观测专项 |
| CrewAI | 0 | 旧题答案中已有少量提及，缺 Crews/Flows、状态、记忆与任务机制专项 |
| learn-claude-code | 0 | 增加 s01–s12 教学 Harness 的执行循环、加载、压缩、团队与隔离机制 |
| System context | 10 | 已有概念题；补动态装配、运行时上下文和具体 middleware |
| Context compression | 8 | 已有压缩原则；补 LLMLingua、token 选择与框架 compaction 实现 |
| Prompt cache | 2 | 补断点/前缀、TTL、创建与读取 usage、失效和应用缓存边界 |
| Tool schema | 3 | 补 SDK Schema、运行时注入、MCP filtering 与 Skills 装配 |
| MCP / Skills | 62 | 并非缺失，不重复添加协议介绍；只补具体适配与权限机制 |
| A2A / ACP | 20 | 既有 ACP 专项主要是 Agent Client Protocol；补 Agent Communication Protocol 历史定位、与 A2A 合并及同名区分 |
| 权限分级 | 0 | 精确规则未命中，但旧库有权限原则题；补权限回调、hook、审批恢复 |
| Sandbox / HITL / Retry | 17 / 8 / 16 | 通用概念已有；补 SDK 隔离责任、middleware 失败恢复与中断状态 |
| Cost guard | 6 | 补 call limit、max turns、usage 与真正强制预算的边界 |
| Trace / Replay | 26 / 9 | 补 SDK trace/span、敏感信息控制、checkpoint 分叉和框架 replay |
| GraphRAG | 5 | 补索引产物、社区报告、local/global/DRIFT 与 LightRAG 模式 |
| Agentic RAG | 1 | 补路由、相关性评判、改写、重新检索与终止约束 |
| 记忆压缩与召回 | 2 | 补 OpenViking 分层加载、递归检索、session commit 与 consolidation |

题目正文审计不包含引用的整段来源文本，避免“来源提到了某概念”被误算成“已有相应题目”。没有因为关键词零命中就断言知识完全缺失，也没有把宽泛搜索的所有结果纳入题库。

## 实际来源发现

通过已配置 Token 的 GitHub 公共 API，实际完成 10 次仓库搜索、16 个候选仓库的元数据/固定 commit/文件树检查。搜索摘要与 star 数只用于发现候选，不作为技术答案证据或招聘热度证明。

从中选择 13 个仓库：用户指定的三个，加上 Microsoft GraphRAG、LightRAG、OpenAI Agents SDK、Claude Agent SDK、LLMLingua、Anthropic SDK、LangChain 官方文档、OpenViking、历史 ACP 和当前 A2A。另采集 Prompt Cache 官方文档；协议同名比较复用当天已入库的 Agent Client Protocol 原始正文，不重新发布旧来源。未选用目录列表、无关配置仓库或仅凭营销性能数字作为答案依据。

CrewAI 固定使用仓库 `v1.15.36` 英文文档，不与 `edge` 混写。Microsoft Agent Framework 的 ADR 必须保留提案/接受状态与语言 API 边界。learn-claude-code 标为教学实现，不是 Claude Code 官方源码。阅读 Anthropic 项目不代表调用其模型；全部生成和核验仍用 GLM。

## 执行与复跑

专题配置：[harness_gaps_20261106.json](config/harness_gaps_20261106.json)。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/research_harness_gaps.py \
  --output data/bank/harness-gap-expansion-20261106
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/research_harness_gaps.py \
  --output data/bank/harness-gap-expansion-20261106 \
  --extend-repo langchain-ai/docs --extend-repo volcengine/OpenViking
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/research_harness_gaps.py \
  --output data/bank/harness-gap-expansion-20261106 \
  --extend-repo i-am-bee/acp --extend-repo a2aproject/A2A \
  --query "agent communication protocol ACP in:name,description"
PYTHONDONTWRITEBYTECODE=1 EMBEDDING_BATCH_SIZE=8 .venv/bin/python -B scripts/expand_agent_hotspots.py collect \
  --output data/bank/harness-gap-expansion-20261106 --plan config/harness_gaps_20261106.json
PYTHONDONTWRITEBYTECODE=1 EMBEDDING_BATCH_SIZE=8 .venv/bin/python -B scripts/expand_agent_hotspots.py build \
  --output data/bank/harness-gap-expansion-20261106 --plan config/harness_gaps_20261106.json \
  --workers 4 --per-facet 3 --retries 2
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/review_harness_questions.py \
  --output data/bank/harness-gap-expansion-20261106 \
  --decisions data/bank/harness-gap-expansion-20261106/review-decisions.json \
  --plan config/harness_gaps_20261106.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/expand_agent_hotspots.py finalize \
  --output data/bank/harness-gap-expansion-20261106 --plan config/harness_gaps_20261106.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/expand_agent_hotspots.py validate \
  --output data/bank/harness-gap-expansion-20261106 --plan config/harness_gaps_20261106.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/expand_agent_hotspots.py terminal \
  --output data/bank/harness-gap-expansion-20261106 --plan config/harness_gaps_20261106.json \
  --query "给我 3 道 CrewAI Crews 与 Flows、状态持久化和任务编排的面试题"
```

首次研究命令只在新目录执行；已有基线不允许覆盖。后续采集/生成可按报告续跑。单个目录的写入命令不能并发。显式审查下架命令只执行一次，拒绝删除基线或其他批次题；已有 `manual_review` 的已完成运行不要再执行 build，以免把复核留下的预算缺口当作必须补足的目标。

## 验收方法

离线回归覆盖文档预算、指定正文的完整性、大小与软链接限制、整条旧记录及其引用/向量/分类的哈希、配置不设全库目标及题目正文审计。真实验收另检查 Milvus 全量向量、独立题目集合、原始证据、混合召回/重排、专项检索、增量导出和实际终端问答。

13 个专项、59 个细分方向，每个方向本轮预算 3 题；这不是整库总量限制。证据不足或与全库重复时，允许少于预算，不降低 0.93 的语义去重阈值来补齐。

生成与依据核验分开调用；生成题不能反向作为事实证据。通过模型核验不等于专家逐题事实审查，向量相似度去重也不能证明没有任何语义交叉。本轮未重跑 RAGAs，不套用旧评分。

## 结果

### 题量与来源

新增 **168 道、13 类**，正式题库现为 **1,324 道、42 类**。原有 1,156 条记录（包括题目、答案、引用、分类、向量和落表元数据）哈希均未变化。所有新增题标为“基于资料生成”、`llm_verified`，不是公司真题证明。

| 新增专项 | 题数 |
| --- | ---: |
| Microsoft Agent Framework | 16 |
| CrewAI Crews、Flows 与生产编排 | 18 |
| learn-claude-code 教学 Harness | 24 |
| Harness 上下文装配与压缩实现 | 9 |
| Harness Prompt Cache 与缓存观测 | 9 |
| Harness Tool Schema、Skills 与协议适配 | 12 |
| Harness 协议边界与 ACP/A2A 迁移 | 11 |
| Harness 权限分级与审批实现 | 12 |
| Harness Sandbox、Retry 与 Cost Guard 实现 | 12 |
| Harness Trace、Replay 与状态分叉 | 9 |
| GraphRAG 索引与查询机制 | 15 |
| Agentic RAG 路由与证据反馈闭环 | 10 |
| Harness 分层记忆与召回实现 | 11 |
| **合计** | **168** |

新增难度：基础 61、进阶 56、高阶 51。全库为基础 415、进阶 667、高阶 242。原有 Harness Engineering 100 题未重分配；热点策略标签现共 1,089 题，不表示实时招聘热度测量。

新增 **14 个来源、126 份正文、3,570 个活动切片**，当前为 **656 来源、8,070 活动正文切片**，知识版本 **657**。13 个仓库均通过真实 GitHub API 取得元数据、固定 commit 和文件树；本轮没有 API 限流或网页降级成功冒充 API 成功的情况。LangChain 文档在发现与采集之间有新提交，最终正文与引用使用导入记录中的实际固定 commit，而非浮动分支名。

### 生成与复核

完成 64 个生成/核验批次，曾有一次 Agentic RAG 的 `structured_output_failed:Draft:ValueError`，原始失败保留在 `build.json`，通过后续正常重试补齐，未改成 mock 或放宽校验。批次数不等于准确 API 调用次数，内部可能重试。

最初有 177 道通过模型核验并发布；随后额外复核剔除 **9 道**：包括绝对化迁移推断、以 ADR 状态推断当前发布状态、跨语言 SDK API 混写、过时成熟度判断、由检索切片缺口推断整份文档缺口、遗漏 commit 分支条件，以及未被向量阈值挡住的语义重复。

剔除前的模型结论与题目保存在 [review-exclusions.json](data/bank/harness-gap-expansion-20261106/review-exclusions.json)，明确不属于正式题库或事实 RAG。保留了原批次的 `model_verified_added_before_review`、原 IDs 和显式剔除记录，没有改写模型原判来营造全绿。题目从 SQLite 正式目录下架，Milvus 题目向量也删除并读取验证。

59 个细分方向均仍有题。八个方向比本轮 3 题预算合计少 9 题，这是复核的主动取舍，`unfilled` 如实保留，不强行改写标题补数。

### 离线与真实验收

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider \
  --basetemp=/private/tmp/iqb-harness-gap-verification-tests-20261106
.venv/bin/ruff check --no-cache src tests scripts
```

离线回归 **151 passed、6 skipped**，耗时 2.03 秒；Ruff 通过。跳过的是默认未启用的原有真实服务测试，不计为通过。它们不能代替本轮另外执行的真实验收。回归同时验证已显式复核的运行不能再执行 build 续跑，避免重新补入已拒绝的题目。

[真实数据与检索验收](data/bank/harness-gap-expansion-20261106/validation.json)为 **39/39 通过**，包括：

- 1,156 条基线完整数据库记录哈希不变，全库 1,324 题的原始引用均活动可追溯。
- 168 道新增题的原始证据新鲜，引用文本与当前原始切片逐字一致，生成题与事实集合隔离。
- 全库 1,324 个题目向量在真实 Milvus 中存在、为有限 768 维向量，且与 SQLite 一致。
- 九道被复核剔除的题及其 Milvus 向量，在新的验证进程中确认不存在。
- 13 个专项均执行真实 Top-3 题目检索；生成记录包含真实稠密/BM25/RRF/Reranker 轨迹。
- 所有计划的指定文档均实际入库；增量 JSONL 与正式新增题逐条一致，Markdown 恰有 168 个题目标题。
- 全库标题向量最大余弦相似度为 0.929254699554554，低于未改变的 0.93 阈值；这不等于证明没有任何语义重叠。

[真实终端 E2E](data/bank/harness-gap-expansion-20261106/terminal.json)为 **7/7 通过**。使用上列 CrewAI 查询执行真实 `iqb chat --local-only --json`，退出码 0，耗时 84.71 秒；返回 3 道题，`sufficient=true`、`persisted=true`、`route=local`，包含来源引用和会话 ID，无警告。`--local-only` 禁止外部采集，但仍真实调用配置的 GLM 模型，没有用 mock 替代。完整响应保留为 [chat-example.json](data/bank/harness-gap-expansion-20261106/chat-example.json)。终端响应不计入本轮正式目录的 168 道新增题。

## 真实失败与修复

采集原生进程三次以退出码 139 终止，不计为成功。前两次没有 traceback；第三次启用 faulthandler，定位到 Python 源码的正文分片阶段，原生 `tree_sitter_language_pack` 已加载，确切 C 级根因未确认。降低嵌入批大小未解决。

将 `.py` 符号边界改为标准库 `ast` 后，同一组 Claude Agent SDK 示例成功入库。保留函数/类、异步函数及装饰器边界，不执行源码，原文/行号/token 上限未放宽。其他语言的分片策略保持不变。新增相关回归并重跑；没有删除原有题目或已成功发布来源来恢复。

本轮使用检查点跳过已完成来源。原始失败及后续恢复记录见 [execution-attempts.json](data/bank/harness-gap-expansion-20261106/execution-attempts.json)；导入报告也保存当前阶段，不把原生终止记成普通空结果或 GitHub 限流。

## 保留资料

- [调研与基线哈希](data/bank/harness-gap-expansion-20261106/research.json)、[正文导入](data/bank/harness-gap-expansion-20261106/imports.json)、[生成/核验记录](data/bank/harness-gap-expansion-20261106/build.json)。
- [168 道新增题 Markdown](data/bank/harness-gap-expansion-20261106/questions.md)、[结构化 JSONL](data/bank/harness-gap-expansion-20261106/questions.jsonl)。
- [专项检索样例](data/bank/harness-gap-expansion-20261106/retrieval-examples.json)、[真实终端响应](data/bank/harness-gap-expansion-20261106/chat-example.json)、真实校验与失败/剔除摘要。没有额外复制整份旧题库。
- 正式 SQLite、正文快照、模型和虚拟环境保留；本轮六个测试临时目录已在测试结束后删除，项目扫描未发现新增的 Python、pytest 或 Ruff 缓存。
