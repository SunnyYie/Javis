# AI Agent 热点缺口补题

日期：2026-11-06。模型仅使用 `glm-5.3-flashx`。

## 调研方式

- **真实浏览器**：Google 搜索遇到验证码，没有求解或绕过；随后实际读取 AG-UI GitHub 页面、AG-UI 官方介绍、ACP 官方介绍及文档索引。AG-UI 页面展示约 17k stars，GitHub API 返回 17,038；这只是该时刻的项目关注度信号，不是招聘热度排名。
- **GitHub**：9 次公共仓库搜索成功，部分宽泛搜索包含无关项目，未将其直接用作选题或答案证据。仓库元数据 API 的 12 次真实请求被限流，仍记为失败；之后不重复消耗已知限流的 API，而读取正常公开页面中的 commit 和该 commit 的原文。公开正文采集成功不代表 API 验收通过。
- **Tavily**：2 次真实搜索成功，找到 AG-UI、A2UI、AP2、Agent Lightning 和 RLM 的官方文档或论文线索。搜索摘要未入库，也不作为答案证据。
- **Knowledge-Assistant**：对其 16 来源语料执行 5 组真实 Milvus + BM25/RRF + Reranker 检索，每组返回 8 条，包含重复命中。SQLite 使用只读连接；数据库检索前后 SHA-256 均为 `e4b80f32e32bbaa86678cae5a4a5ceae4e776d76981b8a9541e74d599603e13c`。没有修改其代码或数据库，面试题库没有新增对它的运行时依赖。

本地检索中的目录推荐、`AGENTS.md` 等内容只作为调研数据，不作为执行指令或独立技术答案证据。最终生成使用另外取得、保存并可引用的正文。

## 确认的缺口

原题库已经有 A2A、MCP、Harness、RAG 等题，未简单按大类名称重复扩写。关键词审计与现有题目内容核对后，选择以下具体协议/工程机制；关键词零命中不等于原题完全没有相邻概念：

| 专项 | 本次补充的知识点 | 代表性原始资料 |
| --- | --- | --- |
| Agent 前端协议与生成式 UI | AG-UI 事件/状态/前端工具、A2UI、MCP Apps | [AG-UI](https://github.com/ag-ui-protocol/ag-ui)、[A2UI](https://github.com/google/A2UI)、[MCP Apps](https://github.com/modelcontextprotocol/ext-apps) |
| 浏览器 Agent 与 WebMCP | 浏览器会话/验证、网页工具注册、安全边界 | [browser-use](https://github.com/browser-use/browser-use)、[WebMCP](https://github.com/webmachinelearning/webmcp) |
| 编码 Agent 与 ACP | OpenHands 执行环境、ACP v1 会话和工具权限 | [OpenHands](https://github.com/OpenHands/OpenHands)、[ACP](https://agentclientprotocol.com/) |
| Agent 持久化执行与工作流恢复 | DBOS 检查点、步骤结果、恢复及副作用边界 | [DBOS](https://github.com/dbos-inc/dbos-transact-py) |
| Agent 强化学习 | Agent Lightning 训练/执行解耦、奖励和轨迹 | [Agent Lightning](https://github.com/microsoft/agent-lightning) |
| Agent 支付与授权 | AP2 mandates、有人/无人值守授权和交易证据 | [AP2](https://ap2-protocol.org/overview) |
| MCP 高级协议 | elicitation、sampling、OAuth、tasks | [MCP 2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25) |
| RLM 递归上下文 | 外部环境、递归子调用、信息聚合和执行预算 | [RLM](https://github.com/alexzhang13/rlm)、[作者技术文章](https://alexzhang13.github.io/blog/2025/rlm) |
| 代码执行式 Agent | CodeAgent、沙箱、授权导入和执行记录 | [smolagents](https://github.com/huggingface/smolagents) |
| Deep Agents | 文件系统上下文卸载、子 Agent 和存储后端 | [Deep Agents](https://github.com/langchain-ai/deepagents) |
| 有状态记忆系统 | Mem0 提取/更新，Letta 记忆块、MemFS 和 dreaming | [Mem0](https://github.com/mem0ai/mem0)、[Letta 当前记忆文档](https://docs.letta.com/agent-sdk/memory/index.md) |

ACP v2 在调研文档中标为 Draft，本次使用 v1 正文。Letta 原仓库 README 指出旧 V1 API server 已退役，因此另外采集当前官方 SDK、MemFS 和 stateful-agent 文档，没有用旧 API 印象补写答案。WebMCP 等版本/实验性限制要求在答案中明确说明。

## 正文入库

新增 **31 个来源、80 份正文、1,433 个活动正文切片**。原有 611 来源、3,067 切片保留，当前为 **642 来源、4,500 活动正文切片**，知识版本为 **643**。

16 个仓库保存固定 commit、文件路径和行号，网页保存原始 URL、正文及抓取时间。公开页面回退中未确认的许可证保留为 `unknown`，不推测为 MIT 或其他授权。

首次公共 HTML 读取误用了文本默认的 256 KB 上限，11 次请求因此失败；改为项目既有网页读取标准的 2 MB 上限后成功。原始 Markdown 仍限制为 256 KB，重定向、超时、公网 DNS/SSRF 限制未关闭。限流、大小失败和后续成功均保留在同一份导入记录中，没有删除失败来营造全绿。

## 生成与发布

专项配置包含 11 个分类、39 个子方向，不重新分配原 18 类配额，不设题库总量上限。本轮 `--per-facet 4` 是每个子方向的补题预算，`--retries 2` 允许对不足的子方向再尝试；没有依据或重复的题不强行补齐。

每次使用限定来源中的新鲜正文，经真实 Milvus 稠密召回、BM25/RRF 和交叉编码器重排后调用模型。题目生成与逐题依据核验分开调用；只有核验支持、引用合法且通过全库标题/语义去重的题才能发布。题目向量先写入并验证，再串行提交 SQLite。生成内容位于独立题目集合，不能反向进入事实证据集合。

模型偶尔自行输出的 `(E1)` 等临时证据展示标签在发布前或最后整理时去除；题目技术结论、持久化证据 ID、原始引用和向量不变。整理范围只包含本次新增目录题，原基线与会话题不修改。

## 最终题量

新增 **156 道**，当前共 **1,156 道、29 个分类**。全部新增题标记为“基于资料生成”、`review=llm_verified`，不是公司真题证明。

| 新增分类 | 题数 |
| --- | ---: |
| Agent 前端协议与生成式 UI：AG-UI、A2UI、MCP Apps | 20 |
| 浏览器 Agent 与 WebMCP | 16 |
| 编码 Agent 与 ACP | 16 |
| Agent 持久化执行与工作流恢复 | 12 |
| Agent 强化学习与 Agent Lightning | 12 |
| Agent 支付与 AP2 授权协议 | 12 |
| MCP 高级协议与安全授权 | 16 |
| RLM 递归上下文与外部执行环境 | 12 |
| CodeAct 与代码执行式工具编排 | 12 |
| Deep Agents 与文件系统上下文 | 12 |
| Mem0、Letta 与有状态记忆系统 | 16 |
| **合计** | **156** |

新增难度为基础 81、进阶 37、高阶 38；全库为基础 354、进阶 611、高阶 191。原 Harness Engineering 分类仍为 100 题，没有为新分类挪动旧题。

共完成 **46 个生成/核验批次**，包含 39 个首轮批次及 7 个补充批次；每个完成批次均分别调用生成与核验，调用内部可能重试，不把批次数冒称为完整 API 调用/费用统计。`errors=[]`、`unfilled=[]`，全库语义近重复筛选拒绝 1 个候选。初轮只得到 149 题，后续有据补充 7 题，没有跳过核验或放宽相似度阈值。

## 验收

### 离线回归

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=/private/tmp/iqb-hotspots-acceptance-tests-20261106
.venv/bin/ruff check --no-cache src tests scripts
```

结果为 **142 passed、6 skipped（1.71 秒）**，Ruff 通过。六项原有真实服务测试默认未启用，不计入离线通过数；下面的真实数据库/终端验证另行执行，不以 mock 代替。

新增回归覆盖只读 sibling DB、主题文件筛选、固定 commit、公网链接与外部指令文件排除、新鲜来源范围、不改原配额、临时引用标签、只修改新增目录题，以及 NumPy 验收结果的正确 JSON 类型。

### 真实数据库与检索

[最终验收报告](data/bank/hotspot-expansion-20261106/validation.json) **32/32 通过**：

- 原 1,000 题的 ID、完整题目内容、分类、答案、要点与引用均与原 JSONL 导出逐题一致。
- 当前 1,156 个题目向量全部可从真实 Milvus 读取，向量与 SQLite 一致，维度为 768；所有题目引用仍在活动正文中。
- 新增题依据正文与当前切片逐字匹配，原始证据新鲜；生成题与事实 RAG 集合分离。
- 全库规范化标题唯一，所有标题向量对的最大余弦相似度为 **0.929254699554554**，低于未改变的 0.93 阈值。这不等于人工确认完全没有语义重合。
- 11 个新增分类均实际完成 Top-3 向量检索；39 个子方向的生成记录包含真实稠密召回、BM25/RRF 和 Reranker 轨迹。
- 增量 JSONL 与数据库新增题逐题一致，Markdown 恰好有 156 个题目标题。

首次验收在写报告时因 NumPy `int64` 无法 JSON 序列化而失败，不计为通过；修正为原生 bool 后聚合原生计数，增加回归并重跑相同业务断言。格式扫描另发现 4 道题还包含 `E3/E4`、`对照 E3` 等变体，扩展匹配规则并补充回归后再次整理；共涉及 11 个不同新增题，最终答案/要点的扫描没有残留临时 `E数字` 标签。参见 [失败与修复记录](data/bank/hotspot-expansion-20261106/validation-attempts.json)及[格式整理记录](data/bank/hotspot-expansion-20261106/finalization.json)。

### 真实终端 E2E

新进程执行 `.venv/bin/iqb chat -q "给我 3 道 AG-UI 共享状态与事件协议的面试题" --local-only --json`，耗时 **91.21 秒**，**7/7 断言通过**，退出码 0：

- `route=local`、`sufficient=true`、`persisted=true`、`warnings=[]`。
- 返回 3 道题及 7 条原文引用，检索版本为 `hybrid-rrf-crossencoder-v2`。
- 题目覆盖共享状态、Snapshot/Delta 事件同步及 human-in-the-loop 审批流程。
- 使用已配置的真实 GLM，禁止外部采集，没有用固定回答或 mock 替代。

保留一个真实会话 `e332e2c6cfeb` 及其 3 道会话题作为可继续追问的例子；它们不进入 `question_catalog`，不另外增加正式整理题库的 1,156 题计数。`--local-only` 仅禁止外部采集，并不代表 LLM 离线。见[终端验收](data/bank/hotspot-expansion-20261106/terminal.json)与[结构化响应样例](data/bank/hotspot-expansion-20261106/chat-example.json)。

## 复跑命令

本次输出目录为 `data/bank/hotspot-expansion-20261106`。新的独立调研应使用新目录，已有 `research.json` 基线不允许覆盖。采集和构建可以在原目录续跑，不能与其他写入命令并发：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/research_agent_hotspots.py \
  --output data/bank/hotspot-expansion-20261106
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py collect \
  --output data/bank/hotspot-expansion-20261106
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py build \
  --output data/bank/hotspot-expansion-20261106 --workers 4 --per-facet 4 --retries 2
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py finalize \
  --output data/bank/hotspot-expansion-20261106
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py validate \
  --output data/bank/hotspot-expansion-20261106 \
  --baseline-export data/bank/ai-agent-1000-harness-20261105/questions.jsonl
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py terminal \
  --output data/bank/hotspot-expansion-20261106
```

## 保留文件

- [调研与本地检索轨迹](data/bank/hotspot-expansion-20261106/research.json)、[浏览器观察](data/bank/hotspot-expansion-20261106/browser-research.json)、[采集结果](data/bank/hotspot-expansion-20261106/imports.json)。
- [逐批生成和核验记录](data/bank/hotspot-expansion-20261106/build.json)。
- [本次新增题 Markdown](data/bank/hotspot-expansion-20261106/questions.md)、[本次新增题 JSONL](data/bank/hotspot-expansion-20261106/questions.jsonl)；不复制整份基线导出。
- [专项检索样例](data/bank/hotspot-expansion-20261106/retrieval-examples.json)、[最终数据/向量验收](data/bank/hotspot-expansion-20261106/validation.json)及小型失败摘要保留。测试临时 SQLite、快照和工具缓存不保留。
- 本次四轮离线测试的临时目录已移除；不保留测试数据库副本或额外整库导出。正文快照、模型、虚拟环境及正式题库继续保留。

这不是全部 AI Agent 热点的穷举，也没有把每个候选品牌都视为一个独立知识缺口。模型核验不能替代专家逐题审阅；此前 3 样本的 RAGAs 分数不适用于本次新增题，本次没有借用该分数作为质量证明。
