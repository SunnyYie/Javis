# 实施与验收记录

日期：2026-11-05（Asia/Shanghai）。

清理说明：按用户要求，旧轮次原始输出、隔离测试数据库与工具缓存已删除。
下文历史运行结论和失败说明保留，不表示对应原始目录仍存在。
当前仅保留最新必要验收记录，清单见 [data/acceptance/README.md](data/acceptance/README.md)。

## 最新优化：检索覆盖与稳定性

模型始终为 **`glm-5.3-flashx`**。默认 RRF 去重候选从 32 扩为最多 40，
增加主 README 文件族选择、保留完整问题、实际语料绑定的 BM25 缓存，
并在最终 8 条配额内保护稠密 / 词法首位，避免已召回的关键证据被重排全部挤出。
新增结构化响应边界校验及脱敏错误位置；检索 / 提示词版本变化隔离旧缓存。
生产库仍为版本 16、2059 个活动片段，未为评估补料。

- 最终真实完整 E2E：**1 passed，419.43 秒**，
  `data/acceptance/2026-11-05-rerank-v3-web/`。缓存、本地充分回答、上下文、
  联网补充保存、新会话 local-only 复用、版本失效及真实 PTY 均通过。
- 两次中间失败原样保留：321.53 秒的 `AttributeError` 根因未确证；
  291.53 秒的联网证据不足确认有词法首位被重排至第 12、排除出 Top 8。
  未降低断言或把失败覆盖为通过。
- 最终 RAGAs：原六题 **24/24**、新增四题 **16/16** 评分有效，两份报告均完成。
  原六题 Faithfulness / Answer Relevancy / Context Precision / Context Recall
  为 **0.9861 / 0.8056 / 0.8354 / 1.0000**；
  新四题为 **1.0000 / 0.8745 / 0.8958 / 1.0000**。
  相比历史原六题覆盖与相关性改善，忠实度和上下文精确率略低，不宣称全面提升。
- 人工发现最终复用回答误写 `get_context()` 为“2.11 新增”，证据为 **2.2.11**。
  Context 注入与请求 ID 的核心引用核对通过，但不能称为全部事实通过。
- 普通回归 **124 passed、7 skipped**；另行真实 BGE / Embedding / Milvus / Redis
  最小集成 **2 passed，29.98 秒**。Ruff、format、依赖锁与服务检查通过。
- 最终凭证扫描覆盖 145 个文件，未命中实际配置密钥；`.env` 权限保持 `0600`。

完整对照、配置、限制及两次失败详见 [OPTIMIZATION.md](OPTIMIZATION.md)。
以下为历史阶段，32 候选等配置和旧耗时不代表当前默认值。

## 前阶段：混合检索、重排与 RAGAs

模型仍为 **`glm-5.3-flashx`**，问答与评估均未回退旧模型。
现有 Milvus 稠密检索 / 中文 BM25 各最多召回 20 条，以 RRF 融合至最多 32 个候选，
本地 `BAAI/bge-reranker-v2-m3-int8` Cross-Encoder 重排后取最多 8 条。
跨仓库问题共享总召回预算并平衡各仓库证据；重排配置隔离缓存，缓存命中跳过检索和重排。

- 真实完整网页 E2E：**1 passed，367.64 秒**，覆盖本地充分命中、Redis 缓存、
  上下文追问、未知问题联网、保存、新会话禁止外部检索后复用、版本失效及 PTY 会话。
  证据位于 `data/acceptance/2026-11-05-rerank-web-e2e/`。
  人工核对另发现首答将 Model 列为 Harness 组成的边界混淆，详见该目录 `REVIEW.md`；
  功能通过不等于每句话事实正确。
- 真实本地 Reranker 相关性探测与 Embedding / Milvus / Redis 集成：**2 passed，14.17 秒**。
  前者输入是明确标注的合成相关 / 无关文本，不当作真实知识问答质量证明。
- 普通回归：**115 passed，7 skipped，2.11 秒**。
  跳过的是需显式启用的真实服务测试，不能把这些跳过算作真实验收通过。
- RAGAs 使用 6 题中英文、代码、跨项目固定集，两组各 6 份真实新鲜答案，
  生成时无参考答案、无缓存、禁用外部检索，使用版本 16 的原有知识库。
  两组各 4/6 题充分，其余不足的题仍参与评分，没有删除或降低断言。
  实际指标、失败 / 复评过程及质量边界见 `EVALUATION.md`。
- 首轮 RAGAs 忠实度 7/12 项因 4096 token 预算截断失败，原目录
  `data/acceptance/2026-11-05-ragas-ab/` 保留 `complete=false`，命令退出 1。
  复评使用同一 `answers.jsonl`，仅提高裁判输出预算，不重生成答案、不修改数据集。
  复评目录 `data/acceptance/2026-11-05-ragas-ab-rescore/` 最终 **48/48 项有效，0 项失败**，
  退出 0。上下文精确率 0.7505 → 0.8412，但召回率 0.7667 → 0.7389，不能宣称全面提升。
- 实际模型下载约 570.7 MB；首次受缓存访问错误影响的下载被中止，随后真实下载与推理成功。
  RAGAs 0.4.3 与 `langchain-community 0.4.2` 的真实导入失败通过兼容版本 0.3.31 修正，
  未修改第三方包或用替身绕过实际裁判。
- 最终 Ruff lint / format、锁文件检查通过；`ka doctor` 确认 Redis / Milvus 可用。
  凭证扫描覆盖 79 个源码、文档和本轮验收文件，未命中实际配置密钥；`.env` 权限仍为 `0600`。

以下保留本次扩展前的验收与历史失败记录，不与新增结果混算。

## 扩展前记录

当前模型按用户最新要求固定为 **`glm-5.3-flashx`**（本项目 `.env`、配置默认值及示例）。
此前旧模型产生的验收输出仅作为历史证据，不能算作 GLM 的验收结果；不做自动模型回退。
GLM 的真实 `doctor --probe-models` 已通过结构化输出探测及本地 Embedding / Redis / Milvus 检查。
GLM 新增仓库本地问答 → 同题缓存 → 上下文追问真实验收 **1 passed，85.98 秒**；
核心事实已核对 README 引用，详见 `data/acceptance/2026-11-05-glm-catalog-qa/REVIEW.md`。
GLM 完整网页知识闭环真实验收 **1 passed，262.11 秒**：
首次本地回答 → 缓存 → 会话追问 → 证据不足 → 联网补充 → 入库 → 新会话本地复用
→ 知识版本更新后缓存失效 → 真实 PTY 会话命令。
详见 `data/acceptance/2026-11-05-glm-web-qa/REVIEW.md`，核心事实核对与附加推断风险分开记录。

## 状态

**13 个精选仓库已通过真实 Chromium 导入本地知识库；此前真实网页知识问答 E2E 已通过。**
本次仓库采集每库限定 6 个文件、最多展开 3 个目录页，是局部索引，不是完整源码覆盖。
旧 API 限流失败保留在历史记录中，已不再阻塞默认浏览器导入。
本次 GLM 验收在独立目录运行，不复用旧模型缓存；隔离网页库为 6 个来源、45 个活动片段，
其中五个来自真实联网补充。远程模型调用、新会话本地复用及终端操作均实际完成。

## 浏览器导入与新增清单

默认仓库采集切换为 Playwright Chromium，无需 GitHub Token，不读取用户登录态。
导航仓库及固定 commit 的目录 / 文件页面，从页面结构化数据提取原文行；
不使用搜索摘要、不执行仓库代码，不在失败时偷偷回落到 API。
原 11 个仓库保留，新增 `ai-boost/awesome-harness-engineering`（第三方汇编）
和 `infiniflow/ragflow`（项目自身材料）。

### 实际导入结果

| 仓库 | 文件 | 活动片段 |
| --- | ---: | ---: |
| adongwanai/AgentGuide | 6 | 201 |
| liuup/claude-code-analysis | 6 | 152 |
| xiaonancs/codex-source-analysis | 6 | 161 |
| zai-org/ZCode | 6 | 120 |
| deepseek-ai/deepseek-harness | 6 | 48 |
| earendil-works/pi | 6 | 67 |
| chuckwu0/larkway | 6 | 82 |
| langchain-ai/langchain | 6 | 91 |
| datawhalechina/all-in-rag | 6 | 95 |
| milvus-io/milvus | 6 | 280 |
| vllm-project/vllm | 6 | 219 |
| ai-boost/awesome-harness-engineering | 6 | 317 |
| infiniflow/ragflow | 6 | 191 |
| 合计 | 78 | 2024 |

默认知识库另保留原有 3 个网页来源、35 个片段，总计 16 个来源、2059 个活动片段，
知识版本为 16。所有仓库来源的覆盖记录均为 `transport=browser`、`partial=true`，
引用包含实际 40 位 commit、文件路径和行号。awesome README 约 285 KB，
仅该清单项设置 512 KiB 上限，其他仓库维持默认 256 KiB；没有截断后入库。

首轮 **7 成功、6 失败**。四库遇到 GitHub 折叠目录路径，修正为验证后代路径且继续禁止越界；
Milvus、vLLM 在真实长源码解析时进程退出 -11。定位到 `tree-sitter 0.26.0`
原生解析器错误行边界 / 崩溃，依赖锁定 `0.25.2`，同一 Go 原文件及五种语言长源码回归通过。
只重试首轮失败的六库，结果 **6 成功、0 失败**，未删除或覆盖首轮失败证据。

原文快照与当前切分器逐项重算，**16/16 来源一致，无需修复已有知识**。
真实 Chromium 正文读取、RAGFlow 固定 commit 原文导入、
本地 Embedding / Milvus / Redis 集成最终合跑 **3 passed，24.16 秒**。
普通回归 **103 passed，6 skipped，2.85 秒**；跳过项为显式启用的真实服务测试。
Ruff 检查与依赖锁一致性检查通过。
最终凭证扫描覆盖 140 个源码、文档及本轮验收文件，未发现实际配置密钥；`.env` 保持 `0600`。

### 新增项目问答验收

首次真实跨项目问题失败，保留 `data/acceptance/2026-11-05-browser-qa/`：
用时 141.86 秒，`sufficient=false`。README 简介已入库，但全库混合召回漏掉简介，
助手没有凭记忆补出完整项目定义。随后为显式仓库问题增加仓库范围检索、跨项目配额平衡，
并在问题明确指定 README 时限制文档范围；总计仍最多召回 20 条稠密 / 20 条 BM25，
融合后最多 8 条，不降低充分性断言。

### 可复查证据

- `data/acceptance/2026-11-05-browser-seed/`：13 个真实 CLI 子进程的首轮 stdout、stderr、耗时、退出码。
- `data/acceptance/2026-11-05-browser-retry/`：六库重试结果及 `snapshot-verification.json`。
- `data/acceptance/2026-11-05-browser-qa/`：保留未通过的首次问答，不能计为通过。
- `data/acceptance/2026-11-05-browser-qa-retry/`：相同问题检索修复后的真实问答证据；
  分支测试通过，但人工发现“汇编不提供可运行软件”的无依据推断，不算完整事实质量通过。
  已加强提示约束并升级提示词版本，使旧缓存失效。
- `data/acceptance/2026-11-05-browser-qa-final/`：旧模型最终一轮首问和缓存输出；
  用户要求切换模型时主动中断追问，测试退出码 2，不计为完整通过。
- `data/acceptance/2026-11-05-glm-catalog-qa/`：指定 GLM 模型下的新增仓库问答。
- `data/acceptance/2026-11-05-glm-web-qa/`：指定 GLM 模型下的隔离网页知识闭环，包含模型名、
  逐条命令、耗时、退出码、回答、引用及真实终端记录。

手动扩充范围或同步仍须显式执行；本次不宣称真实增量更新、全部源码问题、
恶意来源对抗、浏览器页面结构任意变更下均已验收。

## 本次补验：授权配置与真实问答

用户指定读取 `01.simple-agent` 的配置后，检查其 `llm.py`，确认实际配置位于
`/Users/sunyi/ai/Agent/hello-agent/.env`。该文件提供 Anthropic 网关和 Tavily，
不含 GitHub Token。原项目未修改，未执行原项目代码。

- 已实际验证该网关支持 `/v1/chat/completions`，继续使用 LangChain OpenAI provider；
  此前采用源项目默认模型 `claude-opus-4-8`，该模型现已按用户要求停用；
  以下本节结果均为切换 GLM 之前的历史记录，并非 GLM 验收。
- 通过显式脚本只导入所需模型字段、Tavily；独立 `.env` 权限 `0600`，拒绝覆盖已有配置。
  配置导入及异常输出不打印密钥；源码、文档和本次验收文件扫描未发现实际凭证。
- Milvus 初次探测不可用。日志显示约 1 小时 44 分钟时钟跳变和 etcd 会话丢失后进程退出。
  重建本项目 Milvus 容器后健康，增加 `restart: unless-stopped`，未清空卷或改动其他项目。
- `ka doctor --probe-models` 最终退出码 **0**：LLM 配置、真实结构化输出、Embedding、
  无截断 tokenizer、Redis、Milvus、公网搜索端点均通过。可选 GitHub Token=false 仍明确显示，
  但不再仅因为缺少可选 token 返回失败。
- 普通回归 **77 passed，4 skipped**；新增配置导入和 doctor 测试。
  普通测试隔离环境凭证，并禁用 pymilvus 导入时隐式 `load_dotenv()`，不触发真实搜索。
- 本次真实 Milvus / Embedding / Redis 集成回归 **1 passed，19.00 秒**。
- 真实完整网页问答 E2E **1 passed，702.77 秒**，未使用假模型、假搜索或假数据库。

执行命令：

```bash
KA_QA_LIVE=1 \
KA_EVIDENCE_DIR="$PWD/data/acceptance/2026-11-05-qa" \
.venv/bin/pytest -q -s \
  tests/test_live.py::test_real_web_qa_cache_context_online_and_reuse
```

| 环节 | 真实结果 |
| --- | --- |
| 精选官方网页入库 | LangChain overview，8 chunks，8 次新嵌入 |
| 幂等重复导入 | 0 次新嵌入，版本保持 1 |
| 首次本地问答 | create_agent / harness 问题，route=local，充分且有引用；50.42 秒；未启用 local-only，仍未进入搜索分支 |
| 同题精确缓存 | route=cache，0.82 秒；故意改为不可用 Milvus 地址并清空 LLM key，仍返回相同答案 |
| 有效会话追问 | 同一 session 追问“它与 LangGraph 的定位有什么区别”，正确承接前文，route=local |
| 本地缺失 | FastMCP Context 问题在 local-only 下 sufficient=false，明确拒绝凭记忆给出代码 |
| 联网补充 | 真实 GitHub 搜索 / Tavily / HTTP 正文；两轮共取得 10 个新网页来源，成功入库；329.88 秒 |
| 联网回答 | route=online，sufficient=true，persisted=true；引用含 gofastmcp.com，给出 Context 注入及 ctx.request_id 代码 |
| 新会话复用 | 改写问题并启用 local-only；session 不同，route=local，充分且引用已保存原文；78.15 秒 |
| 知识版本失效 | 补充资料后再次问原题，route=local 而非旧 cache；重新生成并缓存 |
| 真实终端 | pexpect PTY 实际问答缓存命中，执行 /sources、/sessions、/new、/resume、/quit，退出码 0 |

验收库独立于默认用户知识库：共 **11 个网页来源、113 个活动片段**，其中 1 个为预导入页面，
10 个为联网补充；**不是 11 个精选仓库**。CLI 启动和模型处理时间计入表中耗时。
实际远程回答通过 `.stream()` 接收；PTY 此轮验证的是缓存展示及会话命令，未单独验收首次 token 延迟或中途 Ctrl-C。

逐条记录：

- `data/acceptance/2026-11-05-qa/commands.jsonl`：命令、耗时和退出码。
- `03-local.stdout`、`05-context.stdout`、`06-insufficient.stdout`：
  本地、上下文和证据不足的原始 JSON 回答。
- `07-online.stdout`、`08-reuse.stdout`：联网及新会话复用，含引用原文。
- `09-invalidated.stdout`、`10-sources.stdout`、`11-terminal.txt`：失效重答、来源和真实终端。
- `data/acceptance/2026-11-05-qa/REVIEW.md`：本次核心事实人工核对及覆盖边界。

默认用户知识库另已导入 LangChain overview 和 FastMCP v2 Context 官方页面，
与此前 AgentGuide README 合计 3 个网页来源；未把隔离验收库整体复制为生产库。

### 本次保留的失败与边界

- `KA_LIVE=1` 的原始 11 仓库完整测试实际 **1 failed，13.55 秒**：
  `ka seed` 对全部 11 个仓库返回 `api.github.com: rate_limited`，退出码 1。
  删除了“不具备 token 就提前失败”的不必要前置断言，保留真正公共 API 的失败结果；
  没有跳过失败仓库，也没有把网页 QA 测试冒充该测试通过。
- 另以英文问题对默认库实际提问，route=local、充分且带引用，但回答为中文。
  因此本次不声称严格英文语言跟随已通过。
- 核对了本次问题核心事实及代码对应片段，不代表已审核所有附加背景、全部 API 版本或所有生成句子。
  网页 role 保持 unverified，官方域名由本次人工核对；GitHub `/blob/main/` 页面按网页采集，
  并非 GitHub API 的固定 commit 仓库索引。
- 原问题集的跨仓库源码实现、真实增量同步、真实恶意来源对抗、PTY 流式中断等仍需单独验收。

## 此前已验证

| 项目 | 实际结果 |
| --- | --- |
| `.venv/bin/pytest -q` | 73 passed，3 skipped；跳过的是三个需要显式启用的真实测试 |
| `.venv/bin/ruff check src tests` | 通过 |
| `docker compose config --quiet` | 通过 |
| `docker compose ps` | etcd、MinIO、Milvus、Redis 均 healthy；独立 Compose 项目 |
| `ka doctor` | Redis=true、Milvus=true；LLM / GitHub token / 搜索密钥未配置，因此退出码为 1 |
| `ka doctor --probe-models` | Embedding=true、无截断 tokenizer=true；LLM structured output=false，缺少模型凭证 |
| 本地多语言模型 | 实际维度 768；长文本计数 1002 tokens；不同长度的中英文 batch 均返回 768 维向量 |
| 真实服务集成测试 | 单独执行 1 passed；最终与浏览器回归合跑 2 passed（26.50 秒）。真实 Embedding、Milvus 写入校验/检索、重开元数据、Redis Cache 层；存储集成输入明确为合成样本 |
| 真实终端交互 | pexpect 启动 `ka chat --local-only`，执行 `/new`、`/sessions`、`/quit`；退出码 0 |
| 真实网页材料入库 | 固定 commit 的 AgentGuide README 通过普通网页入口导入：1 份正文、19 chunks、19 次新嵌入、版本 1 |
| 同一材料重复导入 | 19 chunks、0 次新嵌入，版本仍为 1 |
| 中英文检索 | 对上述真实 README 执行两种语言的查询，返回来源可追溯的片段；不代表回答正确率已验收 |
| 浏览器仓库页面 | 修复异步子请求后，同一页面的 `AgentGuide` 和 `LangGraph` 两个关键词断言通过，读取 23534 字符 |

真实 README 地址：

`https://raw.githubusercontent.com/adongwanai/AgentGuide/9a68e14bb4e44275c6e144b2e2c86ff4b02457ac/README.md`

这是**普通网页正文来源**，不是通过 GitHub API 完成的仓库级索引：SQLite 中 `repo` / `commit_id` 为空，commit 固定在 URL 中。不能将这一份 README 说成完整导入 AgentGuide，更不能说成已经导入全部 11 个仓库。

## 此前实际失败与修复

- 最初 Docker 下载遇到系统 Keychain 权限错误；获准后继续。
- 最初指定的 MinIO 旧 tag 在 Docker Hub / Quay 不可拉取；改为本机已有官方 MinIO 的不可变摘要，容器已健康运行。
- 首轮离线测试为 27 通过、1 失败：断言误删了原文末尾换行。修正断言而非修改来源内容，后续通过。
- `MAX_FILES=1 ka ingest adongwanai/AgentGuide` 实际返回 `api.github.com: rate_limited`，退出码 1。没有把它解释为无搜索结果，也没有宣称仓库导入成功。
- 最初浏览器测试断言正文包含 `Example Domain` 未通过；实际正文与标题不同，保留该失败，不计为通过。
- 最初同步浏览器子请求在 GitHub 页面上超时并产生回调错误。改为并发最多 4 个、最多 48 个请求、16 MiB 总响应上限的异步读取，补齐取消及关闭处理；重跑原仓库页面同一断言后通过。
- 显式运行 `KA_LIVE=1` 的完整终端 / 远程模型 E2E，实际 **1 failed**，失败前置条件为 `LLM credentials missing`。没有改成 mock，也没有弱化为“有文本即通过”。
- 最终真实命令 `ka chat --local-only -q '解释 Agent 工具调用' --json` 退出码 1，明确提示 `LLM_API_KEY is missing`；缓存未命中且模型未配置时，会在下载模型或联网检索之前失败。
- ONNX 与系统 Python HTTP 缓存在沙箱中出现 telemetry / cache 写入警告；本地向量计算、写入和检索仍成功。未将这些警告算作模型问答成功证据。

## 此前尚未验收

以下为配置前遗留记录，当前状态以上方“本次补验”为准；保留以区分历史失败和后续真实验证。

- 真实远程 LLM 的结构化输出、流式回答、上下文追问与逐条引用支持情况。
- Tavily 真实搜索及完整的“本地不足 → 联网 → 入库 → 新会话本地复用”闭环。
- 全部 11 个精选仓库的 API 导入、关键源码覆盖与实际增量同步。
- 真实模型条件下的缓存命中不访问向量库 / 搜索。对应行为已有离线分支测试，真实 Redis Cache 层已有服务测试，但没有用假模型替代完整 E2E。
- `tests/acceptance_questions.json` 中问题的人工事实、源码和跨项目对比核对，以及对抗式提示词注入评测。

## 继续验收

本次已按用户授权配置模型与 Tavily。默认仓库导入使用浏览器，不再需要等待 API 配额；
只有显式 API 模式及 API 搜索仍受该限制。
此前未读取参考项目凭证的记录保留在历史结果中，不代表本次授权配置未执行。

```bash
uv run ka doctor --probe-models
uv run ka seed
KA_SERVICES=1 KA_BROWSER=1 uv run pytest -q tests/test_live.py -k 'not terminal'
KA_LIVE=1 uv run pytest -q tests/test_live.py::test_real_terminal_seed_local_cache_online_reuse
```

完整 E2E 的首次采集通过 `MAX_FILES=2` 限制规模，不代表生产清单已经全量覆盖。通过后仍须按人工问题集核对引用，不应只看进程退出码。
