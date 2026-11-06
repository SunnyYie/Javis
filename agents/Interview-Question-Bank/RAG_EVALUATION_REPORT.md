# 混合检索、Reranker 与 RAGAs 真实验收

日期：2026-11-05，Asia/Shanghai。

清理说明：验收完成后按用户要求删除了历史测试库、重复输出和探针。当前保留的真实样例、评分与失败摘要见 [data/evaluations/README.md](data/evaluations/README.md)；下文其余文件路径为历史运行位置，不表示原始文件仍全部存在。

**第二轮完整通过**：3/3 单题 E2E、2/2 功能检查和 12/12 RAGAs 有效评分，命令退出 0，`complete=true`。生成和评判均使用 `glm-5.3-flashx`。功能通过不代表排序质量提升；本轮指定来源的平均 Recall@8 从基线 0.8333 降至重排 0.5000，完整对照与首轮失败保留如下。

## 实现

检索链路为本地多语言 MPNet 768 维向量召回 + 中文 BM25，经 RRF 融合后，由本地 `BAAI/bge-reranker-v2-m3-int8` 交叉编码器排序，再选择生成上下文。默认每路及融合候选上限为 48，重排批量为 8。重排失败明确报错，不静默退回基线；可显式配置 `RERANKER_ENABLED=false` 做无重排对照。

JSON 和 SQLite 事件保留两路召回 ID、融合顺序、每个候选的重排分数、最终排序、实际生成上下文及耗时。新会话按原始问题重排；有历史的追问按模型解析出的完整主题重排，避免把“解释第二题”单独送入交叉编码器。缓存身份包含检索版本、候选数量、重排开关和模型，避免复用旧检索配置的答案。没有刷新正文时不再重复执行相同检索。

生成模型与 RAGAs 评判模型均为 **`glm-5.3-flashx`**。RAGAs 使用实际 `Faithfulness`、`AnswerRelevancy`、`ContextPrecision`、`ContextRecall`，相关性计算使用项目本地 MPNet。没有使用默认 OpenAI 模型或模拟评分。

## 可复现命令

在本项目目录执行：

```bash
UV_HTTP_TIMEOUT=180 uv sync --cache-dir data/uv-cache --extra test --extra eval --locked
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
IQB_SERVICES=1 .venv/bin/pytest -q -s \
  tests/test_live.py::test_real_services_persistence_dedup_atomic_cache_and_expiry
.venv/bin/iqb evaluate --output data/evaluations/hybrid-rerank-20261105-01
# 首轮失败修复后的独立重评与全新完整验收：
.venv/bin/iqb evaluate --output data/evaluations/hybrid-rerank-20261105-01 --score-only
.venv/bin/iqb evaluate --output data/evaluations/hybrid-rerank-20261105-02
```

评估输出目录必须不存在；复测请换新目录。仅重新评分可使用原目录和 `--score-only`，每次评分保留独立调用日志与报告，不覆盖前次尝试。默认数据集是 `evals/interview_rag.json`，也支持 `--dataset`。

首次安装发现 RAGAs 0.4.3 与新版 `langchain-community` 的 VertexAI 导入不兼容，已将评估依赖锁定为兼容的 0.3 系列。初次依赖下载超时后以更长下载超时重试成功，未替换真实服务。

## 测试结果

| 范围 | 结果 |
| --- | --- |
| 离线回归 | 修复追问后 118 passed，6 skipped，1.77 秒；跳过项是显式启用的真实测试 |
| Ruff | All checks passed |
| 真实数据库回归 | 最终代码 1 passed，23.52 秒；修复前首次 1 passed，17.53 秒 |
| 依赖锁校验 | `uv lock --check --offline --cache-dir data/uv-cache` 退出 0，139 个依赖 |
| 重排模型真实探针 | RRF 正文 2.436790，无关正文 -10.336843 |
| RAGAs 接口真实探针 | GLM 完成两次结构化评判，Faithfulness 为 1.0，6.36 秒 |

数据库回归使用真实 Milvus、Redis、本地 MPNet 和 BGE 重排模型，验证重复导入、持久化新客户端复用、原子计数、过期和知识版本隔离。接口探针只证明模型兼容性，不作为题库质量结果。

## 数据与隔离

真实 CLI 使用原题库的独立副本：97 个来源、1669 个活动分块，来源知识版本 97。SQLite、正文快照、语料库 ID、Milvus 集合和 Redis 缓存身份均隔离；模型文件使用本项目模型缓存。

原始语料库 ID 为 `9351b108a54f4f10acdbbe491a82bc9b`，原 Milvus 集合为 `iqb_ba808bb5d0180b56`。执行期间核对原库仍为上述语料库 ID、97 个来源、1669 个活动分块、版本 97。Compose 的 etcd、MinIO、Milvus、Redis 均 healthy。

三个人工整理样本覆盖 RRF、ReAct 循环与终止、Agent 短期/长期记忆。每个参考答案均配原始文件路径与字面引用，评估前必须在实际活动分块中找到引用，否则停止。评分只传入该次生成实际收到的上下文，不将完整题库或参考答案混入生成证据。

## 首轮失败与修复

`hybrid-rerank-20261105-01` 的三个单题 CLI 与 Redis 热点链路通过，默认五题也满足题量和 2/2/1 分配，但题号追问返回 `sufficient=false`，因此整轮未通过。追踪发现召回使用了正确主题，交叉编码器却只收到“解释第二题”。已修复为有历史时使用解析后的完整问题，并增加两种输入路径的回归，检索版本升至 `hybrid-rrf-crossencoder-v2`，隔离首轮缓存。

首轮 RAGAs 尝试 `f24b4e51` 只完成 10/12 个有效分值：RRF 和记忆的 Faithfulness 抛出 `IncompleteOutputException`，保留为失败与 `null`。其他三项的三样本均值分别为 AnswerRelevancy 0.7390、ContextPrecision 0.9872、ContextRecall 1.0000；不能把只有一个成功样本的 Faithfulness 均值 1.0 当成全体分值。

评判输出上限从 4096 增至 16384 token，保留原始评判口径与输入上下文。首轮答案使用 `--score-only` 独立重评，新代码在全新 `hybrid-rerank-20261105-02` 目录重跑完整 CLI 与评估；不覆盖原始失败报告 `report-f24b4e51.json`。

首轮独立重评 `6c036720` 已完成 12/12 个有效分值：Faithfulness 1.0000、AnswerRelevancy 0.7389、ContextPrecision 0.9872、ContextRecall 1.0000。该命令仍退出 1、`complete=false`，因为原始追问验收失败保持不变；没有用重新评分改写历史 E2E 结论。

## 第二轮终端验收

第二轮目录 `hybrid-rerank-20261105-02`，独立集合 `iqbeval_e06141af003b_ba808bb5d0180b56`。从环境清单时间戳到最终报告约 872.82 秒，不含此前复制语料与建集合的初始化时间。

| 实际 CLI 步骤 | 结果 | 秒 |
| --- | --- | ---: |
| RRF 单题 | 本地、充分、已保存、有原文引用 | 84.721 |
| ReAct 单题 | 本地、充分、已保存、有原文引用 | 79.271 |
| 记忆单题 | 本地、充分、已保存、有原文引用 | 90.524 |
| 第二次相同新会话请求 | 本地、成功计数 2、未晋升 | 82.047 |
| 第三次相同新会话请求 | 本地、成功计数 3、晋升 | 73.434 |
| 第四次相同新会话请求 | 模型密钥为空且 Milvus 不可用，仍命中 Redis，计数 4 | 0.961 |
| 默认五题 | 充分、已保存，基础 2 / 进阶 2 / 高阶 1 | 161.214 |
| 解释第二题 | `mode=explain`、题号 2、充分、已保存、8 条有效引用 | 118.520 |

此外，把首轮失败时的原始五题及原始请求复制到隔离库的新会话后，以最终代码真实复测“解释第二题”，也通过充分性、持久化、题号与引用断言。该场景的实际重排问题为 `Explain the question "如何防止 ReAct 死循环？" (How to prevent ReAct infinite loop) covering termination conditions and loop execution`。证据为首轮目录的 `followup-fixed-v2.stdout`、`.stderr` 及 `followup-fix-check.json`，原始失败输出保持不变。

## 第二轮 RAGAs

RAGAs 0.4.3，评判尝试 `54159a7d`，GLM 温度 0、输出上限 16384 token。实际记录 42 条框架结构化评判调用，全部成功；每项超时上限 900 秒。三个原始用户请求及实际生成答案、实际上下文全部进入评估，没有使用探针答案代替。

| 样本 | Faithfulness | AnswerRelevancy | ContextPrecision | ContextRecall |
| --- | ---: | ---: | ---: | ---: |
| RRF | 1.0000 | 0.6122 | 1.0000 | 1.0000 |
| ReAct | 1.0000 | 0.6335 | 1.0000 | 1.0000 |
| 记忆 | 1.0000 | 0.8394 | 1.0000 | 1.0000 |
| 平均 | 1.0000 | 0.6950 | 1.0000 | 1.0000 |

ContextPrecision 原始值为约 `0.9999999999875`，表格四舍五入为 1.0000。12/12 指标均为有效分值，未丢弃失败项后再宣称全体均值通过。这里的 AnswerRelevancy 比较“请生成面试题”的原始请求与题目答案，含有元请求和技术答案的表达差异；需要结合题量、难度、引用约束及人工抽查解读，不能单看一个平均分。

## 首轮排序对照

基线与重排使用完全相同的融合候选池和 Top-8，候选池不一致则验收失败。以下金标准只标注指定来源的分块，其他来源也可能支持同样的事实，因此它不是所有相关分块的穷举标注。

| 样本 | 基线 Recall@8 | 重排 Recall@8 | 基线 MRR@8 | 重排 MRR@8 |
| --- | ---: | ---: | ---: | ---: |
| RRF | 0.5000 | 0.5000 | 0.5000 | 0.1429 |
| ReAct | 0.5000 | 0.0000 | 0.5000 | 0.0000 |
| 记忆 | 1.0000 | 1.0000 | 0.1429 | 0.5000 |
| 平均 | 0.6667 | 0.5000 | 0.3810 | 0.2143 |

该小样本没有显示重排带来总体提升：记忆样本排序改善，RRF 和 ReAct 的指定来源排序下降。保留该回退结果，没有调整标签或放宽断言。是否适合默认开启仍需更大、覆盖不同主题且穷举相关来源的数据集验证。

## 第二轮排序对照

最终检索版本 `hybrid-rrf-crossencoder-v2` 的三个单题再次通过：

| 样本 | 基线 Recall@8 | 重排 Recall@8 | 基线 MRR@8 | 重排 MRR@8 | 重排链路秒数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| RRF | 1.0000 | 0.5000 | 1.0000 | 0.1667 | 23.8029 |
| ReAct | 0.5000 | 0.0000 | 0.5000 | 0.0000 | 25.0612 |
| 记忆 | 1.0000 | 1.0000 | 0.1429 | 0.5000 | 27.0912 |
| 平均 | 0.8333 | 0.5000 | 0.5476 | 0.2222 | 25.3184 |

“重排链路秒数”包括真实 Embedding、Milvus、BM25、RRF 和重排，不只是 BGE 推理，也不含后续答案生成。两轮均由真实模型理解用户问题，解析出的检索词可能不同；严格对照仅在同一轮、同一候选池内进行，不将两轮差异解释为重排效果。

ReAct 重排结果虽然没有命中手工指定的 GitHub 金标片段，但实际答案引用了题库中的卡码网循环工程与 Agent 正文；不能将该金标召回 0 直接解释为“完全没有有用证据”。反过来，RAGAs 的相关性评判也不能推翻该指定来源召回回退。

实际题目及来源示例：

- RRF：“RRF 是如何把 BM25 关键词检索和向量检索的两路排序合并成统一排序的？”；引用卡码网 RAG 正文及 GitHub 问答集 1036–1066 行。
- ReAct：“请描述 ReAct 的 Thought-Action-Observation 循环是如何工作的，并说明生产环境中如何控制循环终止、防止死循环？”；引用卡码网 `loop_engineering_interview.html` 和 `agent_interview.html`。
- 记忆：“AI Agent 记忆系统中，短期记忆和长期记忆有什么区别？”；引用 GitHub 问答集 826–861 行及记忆系统文档 33–48 行。

三题均标为“基于资料生成”，不冒称公司真题。完整 URL、GitHub commit 与逐题证据 ID 见原始响应。

## 证据

最终通过目录：`data/evaluations/hybrid-rerank-20261105-02/`。首轮失败与独立重评目录：`data/evaluations/hybrid-rerank-20261105-01/`。

- `environment.json`、`dataset.json`：模型、索引、来源版本和数据集哈希。
- `commands.jsonl`、各次 `.stdout` / `.stderr`：实际 CLI 命令、退出码、耗时、结构化答案及引用。
- `samples.json`：实际答案、生成上下文、基线和重排检索指标。
- `checks.json`：真实缓存、默认五题、题号追问检查。
- `ragas-calls-*.jsonl`：实际 GLM 评判提示、结构化输出、状态和耗时，不含密钥。
- `ragas-scores-*.json`、`report-*.json`、`report.json`：逐项评分与汇总。

最终完成后再次核对，原语料库仍为 97 个来源、1669 个活动分块、知识版本 97，语料库 ID 未变；四个 Compose 服务均 healthy。后续已按用户要求删除文件形式的评估数据库副本，保留轻量样例与评分证据；未修改正式题库和服务数据。

## 边界

本轮是已有真实题库上的检索—生成—核验—缓存 CLI E2E，没有重新验收 GitHub/Tavily 联网采集或网站反爬。既有采集结果见 `IMPORT_REPORT.md`，旧版从空库联网的验收与失败记录见 `VALIDATION.md`，不作为本轮重排后的新结果。

仅有三个样本，不具有统计显著性。生成与评判使用同一 GLM，存在共同模型偏差。RAGAs 分值反映模型评判，不等于人工事实认证；实际使用仍应查看每题的原文引用。`complete` 表示所有流程与评分完成，不表示达到预设质量门槛；超时、异常、非有限分值均记为失败，不填零或换成模拟数据。
