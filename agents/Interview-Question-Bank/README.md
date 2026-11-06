# Interview Question Bank

有来源的技术面试题库 Agent。使用 LangChain / LangGraph、Milvus RAG 和 Redis 热点缓存，支持终端连续对话。

## 启动

需要 Python 3.12、uv、Docker：

```bash
cd /Users/sunyi/ai/Agent/Javis/agents/Interview-Question-Bank
uv sync --extra test --locked
docker compose up -d --wait
```

参照 `.env.example` 配置本项目 `.env`：

```dotenv
LLM_API_KEY=你的模型密钥
LLM_BASE_URL=https://你的兼容网关/v1
LLM_MODEL=glm-5.3-flashx
TAVILY_API_KEY=你的搜索密钥
GITHUB_TOKEN=可选的只读令牌
```

模型固定使用当前配置的 `glm-5.3-flashx`，不会自动回退到其他模型；通过 LangChain `init_chat_model(..., model_provider="openai")` 接入，必须支持 tool calling 结构化输出。首次下载多语言 MPNet Embedding 约需 1 GB 磁盘，默认本地 CPU 推理、768 维；可用 `MODEL_CACHE_DIR` 指定模型缓存目录。这不代表 LLM 或搜索离线运行。

默认同时启用本地多语言 `BAAI/bge-reranker-v2-m3-int8`，首次下载约 570 MB，不需要额外模型 API Key。`RETRIEVAL_CANDIDATES=48` 控制每路召回和 RRF 融合后的候选上限，随后交叉编码器按问题打分并截取 Top-K：新会话使用原始问题，有历史的追问使用解析出的完整主题，避免对“解释第二题”等指代句直接重排。`RERANKER_BATCH_SIZE=8` 控制 CPU 推理批量。重排模型失败明确报错，需要显式设置 `RERANKER_ENABLED=false` 才使用无重排基线，不静默降级。

本机配置按用户授权一次性导入，权限为 `0600`，不需要旁边项目才能运行。新环境可显式运行：

```bash
uv run python scripts/configure_from_env.py --source /已授权的配置/.env
```

脚本只复制模型密钥、网关、超时、Tavily 与可选 GitHub 字段，模型固定配置为 `glm-5.3-flashx`，不沿用来源项目的其他模型；不打印值，不覆盖已有 `.env`。不提交密钥。公共 GitHub API 可无 Token 使用，但共享 IP 限流可能导致失败；代码搜索和私有仓库不在首版范围内。

```bash
uv run iqb doctor
uv run iqb doctor --probe-models
uv run iqb chat
```

`--probe-models` 会真实调用收费模型并加载 Embedding。动态网页回退需要 `uv run playwright install chromium`；没有浏览器或页面被阻挡会报告采集失败，不绕过登录或验证码。

## 使用

```bash
uv run iqb chat -q "给我 Redis 持久化相关的面试题"
uv run iqb chat -q "给我 3 道高阶 RAG 面试题" --json
uv run iqb chat --session 上一次返回的会话ID
uv run iqb chat -q "解释第二题" --session 会话ID --local-only --json
uv run iqb ingest https://github.com/owner/repo
uv run iqb ingest https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
uv run iqb sources --json
uv run iqb sync
uv run iqb hot
```

默认 5 题：基础 2、进阶 2、高阶 1；明确指定数量支持 1–20。未指定数量但指定单一难度时为 5 道该难度题。其他混合难度按列表顺序均分，余数优先分配前面的难度。

每题包含题目、难度、知识点、参考答案、回答要点、原题/生成题标记与引用。支持“解释第二题”“再给几道高阶题”。来源原题需匹配正文原文，依据文档组织的练习题明确标注“基于资料生成”；不是公司真实面试记录证明。

连续对话命令：`/new`、`/sessions`、`/resume ID`、`/sources`、`/quit`。`Ctrl-C` 取消当前请求，不保存未完成会话；已经发布的来源不会回滚。为保证结构化核验，题目完成验证后整体输出，不逐 token 展示未核验答案。

会话持久化保存，模型推理和缓存隔离使用最近六轮上下文；题单超出该窗口后追问，请带上完整题目。未命中热点时会多次调用远程模型，五题生成及核验可能需要数分钟。

`--local-only` 禁止 GitHub、Tavily、网页及来源刷新，**不禁止远程 LLM**。JSON 输出字段包括 `questions`、`request`、`explanation`、`citations`、`route`（cache/local/online）、`sufficient`、`persisted`、`warnings`、`hot_count`、`cache_promoted`、`session`。

## 指定题库导入

用户提供的五个公开题库有独立的可复跑导入脚本，不把目录页冒充题目正文：

```bash
.venv/bin/python scripts/import_interview_sources.py \
  --report data/imports/provided-sources-新的运行编号.json
# 仅刷新指定来源，或扩大 AgentAlpha 详情页数量：
.venv/bin/python scripts/import_interview_sources.py --only agentalpha \
  --alpha-limit 120 --report data/imports/agentalpha-新的运行编号.json
# 导入 AgentAlpha 当前全部详情，已有来源跳过；扩展 GitHub 技术 Markdown：
.venv/bin/python scripts/import_interview_sources.py --alpha-limit 600 \
  --skip-existing --report data/imports/expanded-sources-新的运行编号.json
```

默认 AgentAlpha 从目录跨主题选取 60 页，卡码笔记读取目录中最多 60 篇详情，面灵AI读取指定题单，拿个offer读取 Top50 原题列表（没有配套答案）。GitHub 从 README 遍历关联的技术 Markdown，包括基础、框架、RAG、工具、记忆、多 Agent、工程化与 Prompt，不执行仓库代码；API 限流时读取正常公开页面中的 commit 与该 commit 的原始 Markdown，限流事实仍保留。遵守公开 `robots.txt`，不进入登录、验证码或访问挑战流程。

写入当前 `DATA_DIR` 和 Milvus 命名空间；报告记录每页实际结果、未选择链接、chunk 数和知识版本，正文仍保存到 `data/snapshots`。不调用聊天模型，不自动整理或虚增结构化题目数量。每次使用新的报告文件保留历史证据；不要与聊天、`ingest`、`sync` 等其他写入并行运行。GitHub 公共页面解析只用于这份指定题库脚本，不改变通用采集器的 API 行为；需要相同采集路径刷新时重新运行该脚本。

2026-11-05 扩充后共有 611 条来源记录、3,067 个活动正文向量片段，AgentAlpha 当前发现的 573 页全部导入，GitHub 固定 commit 的 Markdown/许可证文件共 21 份。拿个offer仅提供题目表，未凭空为其补写答案。最初 97 条来源的历史验收见 [IMPORT_REPORT.md](IMPORT_REPORT.md)，本次逐页结果见 `data/imports/expanded-sources-20261105.json`。

## 按技术栈整理题库

2026-11-05 基线已完成 1,000 道题，覆盖 18 个方向，其中 Harness Engineering 100 道、热点方向共 765 道。该次真实全库验收 36/36 通过，离线回归 133 项通过。基线题库、结构化导出、真实检索样例和验收边界见 [BANK_BUILD_REPORT.md](BANK_BUILD_REPORT.md)。后续热点题采用下面的追加流程，不受这份基线配额限制；当前数量以 `iqb bank stats` 为准。

```bash
uv run iqb bank build --target 1000 --workers 3 \
  --output data/bank/ai-agent-1000-harness-20261105
uv run iqb bank stats
uv run iqb bank search -q "Harness 执行循环与失败恢复" --topic harness
uv run iqb bank search -q "多 Agent 任务分配和失败恢复" --topic multi_agent --limit 5
uv run iqb bank search -q "RAG 混合检索与重排" --topic rag
uv run iqb bank export --output data/bank/questions.jsonl
uv run iqb bank export --output data/bank/questions.md
# 完成构建后，只读核验真实 Milvus、引用、配额、全库近似度和终端检索：
uv run python scripts/validate_question_bank.py \
  --output data/bank/ai-agent-1000-harness-20261105
```

上面的 `build --target 1000` 与 `validate_question_bank.py` 是原 1,000 题基线命令；追加后不应再把它们作为当前全库验收。`build` 会拒绝低于已有题量的目标，不会删除新增题。

构建优先整理正文中已有且有答案的题目，再使用配置的 `glm-5.3-flashx` 补齐缺口，生成与核验是独立模型调用。只保留核验支持且引用检查通过的单题，来源不足时输出实际题量、缺口和非零退出码。构建逐批写入，可重复运行续跑；同主题按不同子方向并行检索和补题，落库仍由主线程串行执行。不要与其他写入命令并行执行。目录题单、搜索摘要和模型记忆不充当答案依据。更换配额或分类规则时更新配置版本并使用新的报告目录；不会为降低配额删除已有题目。

默认目标分配在 `config/ai_agent_curriculum.json`，热点是可调整的人工权重，不代表实时招聘统计：

| 技术方向 | 目标题量 | 热点 |
| --- | ---: | :---: |
| Harness Engineering | 100 | 是 |
| RAG 与检索优化 | 140 | 是 |
| 工具调用、MCP 与 Skill | 100 | 是 |
| Agent 编排与推理范式 | 80 | 是 |
| 多 Agent 协作 | 70 | 是 |
| 评估、可观测性与调试 | 70 | 是 |
| 安全、权限与治理 | 75 | 是 |
| 记忆与上下文工程 | 70 | 是 |
| 推理服务与成本优化 | 60 | 是 |
| Prompt 与结构化交互 | 35 | 否 |
| 工程部署与可靠性 | 35 | 否 |
| 文档解析与数据工程 | 30 | 否 |
| Embedding 与向量数据库 | 30 | 否 |
| 多模态、语音与视觉 Agent | 30 | 否 |
| 微调、对齐与训练 | 25 | 否 |
| LLM 与 Agent 基础 | 25 | 否 |
| 后端、缓存与系统基础 | 10 | 否 |
| 场景设计与产品落地 | 15 | 否 |

总目标 1,000 道，热点占 765 道（76.5%）。Harness Engineering 单列 100 道，覆盖模型与运行时的职责、执行循环与预算、上下文装配、工具沙箱与权限、检查点与回放、验收回归和 Trace。标题采用 Unicode/标点规范化去重，并用真实 MPNet 标题向量拒绝余弦相似度达到 0.93 的近似题；该阈值不能保证所有语义重复都消失。原题保存完整来源答案，`review=source_exact` 表示逐字提取，未逐题经模型核验；补题标记 `review=llm_verified`，模型核验也不能代替人工审阅。难度和分类是自动标签，第三方公司名称不代表已独立验证的公司真题。

每题保存答案、要点、知识点、分类、难度和原文引用，并先校验 Milvus 写入再发布 SQLite 记录。整理题目使用独立的 `iqb_questions_*` 向量集合，正文仍位于原 RAG 集合；生成答案不能反过来充当事实证据。`bank search` 直接检索已存题目并返回引用，不调用聊天模型；`chat` 保留正文混合检索、重排、核验和会话流程。

## 热点缺口追加

`config/agent_hotspots_20261106.json` 保存浏览器、GitHub、Tavily 和本地 Knowledge-Assistant 调研后选择的专项方向。它不替换基线分类，不重新分配或删除原题，也不设置题库总量上限。热点判断来自公开项目/协议与资料线索，不是实时招聘排行榜。

2026-11-06 已追加 **156 道、11 个专项**，当前共 **1,156 道、29 类**，包括 AG-UI/A2UI/MCP Apps、WebMCP、ACP、DBOS、Agent Lightning、AP2、MCP 高级能力、RLM、CodeAct、Deep Agents 和 Mem0/Letta。真实数据/向量验收 32/32、终端本地 RAG E2E 7/7 通过，原 1,000 题逐题保持不变；完整边界与来源见 [HOTSPOT_EXPANSION_REPORT.md](HOTSPOT_EXPANSION_REPORT.md)。

```bash
# 首次调研必须使用新目录，已有 baseline 不覆盖；Knowledge-Assistant 只读检索：
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/research_agent_hotspots.py \
  --output data/bank/新的热点运行编号
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py collect \
  --output data/bank/新的热点运行编号
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py build \
  --output data/bank/新的热点运行编号 --workers 4 --per-facet 4
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py finalize \
  --output data/bank/新的热点运行编号
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py validate \
  --output data/bank/新的热点运行编号
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/expand_agent_hotspots.py terminal \
  --output data/bank/新的热点运行编号
uv run iqb bank search -q "AG-UI 共享状态" --topic agent_ui
```

`--per-facet` 是每个具体子方向单次补题的预算，不是全库上限。模型生成与逐题核验分开调用；不足、失败、重复题均不强行补齐，缺口保存在 `build.json` 的 `unfilled`。生成用选定来源中的新鲜正文，仍经过真实 Milvus + BM25/RRF + Reranker；不会用搜索摘要、目录推荐语或历史生成答案作依据。所有写入串行执行，以上命令不能与其他写入操作并发。

采集器记录 API 真实限流，允许读取正常公开页面中的固定 commit 和关联正文，不绕过验证码或登录。目录/文件读取失败和未知许可证如实记录；没有取得全文的资料不作为答案证据。`research.json` 保存原题 ID 与本地混合检索轨迹，`imports.json` 保存采集结果，`questions.jsonl` / `questions.md` 只导出本次新增题，`validation.json` / `retrieval-examples.json` 保存真实向量验收与专项检索样例。完整当前题库通过 `iqb bank export` 导出。

## 数据与缓存

流程：热点缓存 → 解析问题与上下文 → 本地混合检索 → 充分性判断 → 必要时两轮联网补充 → 正文入库 → 重检索 → 结构化生成与依据核验 → 保存题目并更新热点。

- Milvus 存原始正文 chunk 向量；SQLite 存元数据、活动索引、可复用向量、版本、结构化题目与会话；`data/snapshots` 存正文快照。题目存在独立 `question_records` 表，不作为新的原始事实参与 RAG，防止生成内容自我强化。
- 默认 Milvus 稠密召回与中文 BM25 分别排序，使用 RRF 融合并去重，再由本地交叉编码器重排，按题量最多送入 32 个 chunk；不直接相加不同量纲的分数。相似度或重排分数不等于置信度，仍独立判断答案依据。
- JSON 的 `retrieval` 保存检索词、原始重排问题、两路候选、融合顺序、逐候选重排分数、最终 ID、实际生成上下文 ID 和耗时。没有刷新来源时只检索一次；缓存命中沿用原答案中的历史检索记录，不意味着重新执行了重排。
- 模型使用当前请求内的短证据编号（如 `E1`），程序严格映射回持久化 ID；未知编号仍会被拒绝。历史保留题目顺序、参考答案和解释，不把旧引用正文作为当前证据，避免上下文引用串用。
- 首次核验未通过时最多修订一次，再做相同的独立核验；不放宽引用、题量或难度要求。修订失败保留原先已核验的部分，仍标记不足、不缓存。
- 正文按 Markdown 标题、代码符号与 tokenizer 窗口切分，384 tokens、重叠 64。向量写入并读取校验后，才用 SQLite 事务发布活动清单；失败不激活半成品，重复内容不重新 Embedding。
- GitHub 使用公开 API，固定 commit、文件路径和行号；按检索词优先相关文件。补采独立保存，避免覆盖完整仓库导入。显式仓库导入最多 60 文件、10 MiB，单文件 256 KiB，始终是局部索引，不是完整仓库理解。`sources --json` 显示覆盖及跳过原因。
- 每轮最多五份正文，最多两轮。搜索摘要不是证据。来源无法读取、没有题量覆盖或核验不通过时返回部分结果并标记不足，不虚构补齐。
- 同一问题经 NFKC、空白和英文大小写规范化，结合完整有效会话上下文计数，不合并语义近义句。首次成功建立固定 24 小时窗口；第三次充分、已保存的结果晋升 Redis，第四次起直接复用。上下文或联网模式不同不共用缓存。
- Redis Lua 原子更新计数与晋升。答案键隔离语料库、知识版本、模型、网关、提示词版本、Embedding、检索算法版本、候选数量、重排开关与模型、命名空间和联网模式。答案 TTL 最长 24 小时且不超过引用过期时间；失败、证据不足和未保存结果不计数。
- SQLite `events` 记录处理阶段、供应商搜索结果数量及脱敏失败类别，便于区分采集、生成和核验耗时。长题单内部流式接收工具调用，但只展示完整校验后的结构化结果。
- 结构化响应默认使用 `LLM_CALL_DEADLINE=180` 秒的流式处理预算，在解析对象到达时检查；正在等待的网络读取仍受 `LLM_TIMEOUT` 约束，因此不是精确的墙钟硬中断。超时明确报错，不保存半成品。
- 仓库七天、网页一天过期，相关检索可触发刷新，刷新失败显式使用旧资料。`sync` 手动刷新。知识更新使旧缓存失效；同内容仅刷新有效期不增加知识版本。

## 运维与边界

- Compose 名称及命名卷独立；仅本机监听 Milvus `19532`、健康检查 `19092`、Redis `16380`，不修改其他项目。使用 `docker compose stop` 保留数据，不要随意删除数据卷。
- `DATA_DIR` 默认固定到本项目目录，可隔离测试数据；`INDEX_NAMESPACE` 隔离 Milvus 集合及 Redis 前缀。Embedding 模型或维度变更时使用全新数据目录及命名空间并重建，不能混写旧集合。
- 首版仅支持单用户、同一数据目录单写入进程；不要同时运行会触发入库的 chat、ingest、sync。旧向量、快照和验收数据暂不自动清理。
- Redis 故障跳过缓存并警告；Milvus 故障明确报错，不冒充空库。联网后入库失败，可输出有引用的临时答案，但标记未保存、不缓存。
- HTTP 抓取校验公网 DNS、固定目标 IP、限制端口/重定向/响应大小/超时；浏览器子请求同样约束，禁止 WebSocket 和非 GET。外部文本不是系统指令，不执行仓库代码。安全测试不代表对所有提示词攻击免疫。
- 不提供 Web、评分模拟面试、私有仓库、登录态采集、后台爬虫或预置题库。
- 本地保存会话、缓存和原文；远程 LLM 会收到问题、上下文及相关证据，搜索服务会收到检索词。不要提交敏感个人数据。

## 验证

```bash
uv run pytest -q
uv run ruff check src tests scripts
IQB_SERVICES=1 uv run pytest -q tests/test_live.py::test_real_services_persistence_dedup_atomic_cache_and_expiry
# 可选：真实重启本项目 Milvus/Redis，必须与其他验收、聊天分开执行。
IQB_SERVICES=1 IQB_RESTART=1 uv run pytest -q -s tests/test_live.py::test_real_services_persistence_dedup_atomic_cache_and_expiry
IQB_PROVIDERS=1 IQB_EVIDENCE_DIR="$PWD/data/acceptance/独立新目录" \
  uv run pytest -q -s tests/test_live.py::test_real_provider_search_and_body
IQB_LIVE=1 IQB_EVIDENCE_DIR="$PWD/data/acceptance/另一个新目录" \
  uv run pytest -q -s tests/test_live.py::test_real_terminal_empty_to_online_local_context_hot_and_invalidation
IQB_SMOKE=1 IQB_EVIDENCE_DIR="$PWD/data/acceptance/单题新目录" \
  uv run pytest -q -s tests/test_live_smoke.py
# 接续同一模型、同一提示词版本下尚未重复请求过的成功单题验收。
IQB_CACHE_LIVE=1 IQB_SMOKE_SOURCE_DIR="$PWD/data/acceptance/单题新目录/single" \
  IQB_EVIDENCE_DIR="$PWD/data/acceptance/缓存新目录" \
  uv run pytest -q -s tests/test_live_cache.py
```

普通测试使用明确测试替身，不等于真实 E2E。真实数据库测试验证 Embedding、持久化和 Redis 原子性；真实供应商测试分别验证 GitHub/Tavily 搜索正文；终端 E2E 使用真实模型和服务，从空库到联网入库、本地复用、上下文追问、热点晋升、不可用模型/Milvus 下命中缓存和知识更新失效。

真实外部失败不得替换为 mock。命令、耗时、输出、引用和真实失败保存在指定验收目录；每次指定新目录，避免旧数据影响判断。实际运行结果见 `VALIDATION.md`。

## RAGAs 评估

本轮真实执行结果及排序回退记录见 [RAG_EVALUATION_REPORT.md](RAG_EVALUATION_REPORT.md)。

测试产物已按要求精简，保留的样例和报告清单见 [data/evaluations/README.md](data/evaluations/README.md)。旧验收报告里的部分路径仅表示历史运行位置，历史测试库和重复输出已删除；正式题库和模型文件保留。

```bash
uv sync --extra test --extra eval --locked
uv run iqb evaluate --output data/evaluations/新的运行编号
# 仅重试评估阶段，保留原始问答和以往每次评判记录：
uv run iqb evaluate --output data/evaluations/已有运行编号 --score-only
```

默认使用 `evals/interview_rag.json` 三个有原文引用依据的人工小样本；`--dataset` 可指定其他同结构数据。先备份当前 SQLite、复制正文快照与真实已有向量到独立 Milvus 集合、数据目录及 Redis 前缀，再通过真实 `iqb chat --local-only --json` 执行问题、默认五题、连续追问和热点缓存验收。原始库不用于写入测试数据；仍保留隔离数据和集合供核查。

使用 RAGAs 0.4 的真实 `Faithfulness`、`AnswerRelevancy`、`ContextPrecision`、`ContextRecall`。生成和评判都显式使用当前配置的 GLM，本地 MPNet 提供相关性指标所需的 Embedding，不隐式调用 OpenAI 默认模型。RAGAs 与 `langchain-community` 锁定兼容版本，关闭其匿名遥测。

`samples.json` 保留原始请求、参考答案、真实模型回答、实际传给生成器的全部上下文，并在相同候选池上比较无重排基线与重排后的 gold chunk recall/MRR。`ragas-calls-*.jsonl` 保留每次真实评判的提示、结构化返回和耗时；`ragas-scores-*.json`、`report-*.json` 保留每轮指标与失败。非有限分数和接口异常标记失败，不补成 0，不用成功样本均值掩盖缺失项；有失败时命令返回非零。

这些分数用于诊断，不预设“重排必然提升”。三个样本不足以作统计结论，gold 仅覆盖特定原文片段，相同内容的其他来源可能未被标为 gold。生成器与评判器同为 GLM，存在共同偏差；评判结果不能代替人工核查。此命令验证已有真实题库的本地采集复用链路，不重新验收 GitHub/Tavily 联网采集。
