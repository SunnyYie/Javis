# Knowledge Assistant

在终端连续对话的技术知识助手。资料优先来自本地缓存和知识库，证据不足时才到 GitHub 与网页补充，并保存可追溯的原文和向量。

其他 Agent 的调用约定、维护边界及验收要求见 [Agent.md](Agent.md)。

## 启动

需要 Python 3.12、uv、Docker。数据库端口仅绑定本机 `19531`（Milvus）与 `16379`（Redis），不占用常见默认端口。

```bash
cd /Users/sunyi/ai/Agent/Javis/agents/Knowledge-Assistant
uv sync --extra test
docker compose up -d --wait
```

在项目根目录创建 `.env`，参照 `.env.example` 配置：

```dotenv
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://你的兼容网关/v1
LLM_MODEL=glm-5.3-flashx
# 浏览器导入不需要 token；API 搜索可选
GITHUB_TOKEN=
GITHUB_IMPORT_MODE=browser
GITHUB_TREE_PAGES=12
TAVILY_API_KEY=你的搜索密钥
```

模型必须支持流式输出和结构化输出 / tool calling。使用 LangChain 的 `init_chat_model(..., model_provider="openai")`，与指定参考项目相同，不硬编码网关或自动读取其他项目的密钥。

也可显式设置 `LLM_ENV_FILE=/绝对路径/现有项目/.env`，只读取其 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TIMEOUT`。当前环境变量和本项目 `.env` 的显式配置优先，不导入其他凭证；不要提交 `.env`。

### 本机已配置

按用户授权，本项目 `.env` 已从 `01.simple-agent` 实际使用的
`/Users/sunyi/ai/Agent/hello-agent/.env` 导入模型与 Tavily 所需字段，权限为 `0600`，原项目未修改。
网关已实际验证支持 OpenAI 兼容接口；按用户最新要求，当前模型为 `glm-5.3-flashx`，知识助手仍使用
`init_chat_model(..., model_provider="openai")`，不是切换为 Anthropic SDK。
源配置不含 GitHub Token。仓库导入现默认使用无登录态 Chromium，
不依赖 GitHub API 配额；发现来源的 GitHub API 搜索仍受共享 IP 配额限制，失败会明确报告。

仅在新环境需要同样的显式配置导入时运行：

```bash
uv run python scripts/configure_from_env.py \
  --source /绝对路径/已授权的.env --model 已验证兼容的模型名
```

脚本只导入模型字段、Tavily 与可选 GitHub Token，不执行原项目 Python 文件、不打印密钥，
并拒绝覆盖已有 `.env`。Anthropic 网关并非都兼容 OpenAI，须先确认该网关支持 `/v1/chat/completions`。

### 启动聊天

```bash
uv run ka doctor
uv run ka doctor --probe-models
uv run playwright install chromium
uv run ka seed --list
uv run ka seed
uv run ka chat
```

`doctor --probe-models` 会真实调用远程模型、检查结构化输出并加载本地 Embedding，可能产生费用；普通 `doctor` 只检查配置与本地数据库。浏览器导入前需安装 Chromium。未配置 GitHub token 时仍可使用公共 API 搜索，但限流较严。未配置搜索密钥时保留 GitHub 检索能力，并显式报告网页搜索不可用。

首次使用 Embedding 会从 Hugging Face 下载约 1 GB 模型。默认多语言 MPNet，768 维，本地 CPU 推理；模型目录默认 `data/models`，可通过 `MODEL_CACHE_DIR` 共享。对话 LLM 始终是远程模型，本地 Embedding 不等于全离线应用。

## 命令

```bash
uv run ka ingest https://github.com/langchain-ai/langchain
uv run ka ingest https://公开文档页面
uv run ka sync langchain-ai/langchain
uv run ka sync
uv run ka sources
uv run ka sources --json
uv run ka chat --session 会话ID
uv run ka chat --local-only
uv run ka chat -q "LangChain 如何定义工具？" --local-only --json
```

`--local-only` 只禁止 GitHub、搜索、网页及资料自动刷新，仍允许远程 LLM 调用。会话命令：`/new`、`/sessions`、`/resume ID`、`/sources`、`/help`、`/quit`。`Ctrl-C` 中断当前请求，不保存未完成回答；已经成功发布的来源索引不会回滚。

`seed` 只在显式执行时导入 `seeds.json` 中的 13 个仓库：原有 11 个，加上
`ai-boost/awesome-harness-engineering` 和 `infiniflow/ragflow`。awesome 清单标为第三方汇编，
RAGFlow 标为项目自身材料。可编辑清单，或用 `ka seed --catalog /绝对路径/清单.json`。
启动聊天不会自动扫描整个清单。

## 检索和持久化

流程：精确缓存 → 独立问题改写 → 本地混合检索 → 证据判断 → 必要时两轮联网补充 → 原文入库 → 重检索 → 引用回答 → 缓存。

- Redis 缓存包含有效上下文、模型 / 提示词版本、语料库独立 ID 和知识版本；不跨知识目录误用缓存。不充分、无有效引用、过期或引用尚未保存资料的答案不缓存。
- Milvus 与 BM25 各召回最多 20 条，默认将 RRF 去重后的完整并集（最多 40 条）交给重排；
  本地多语言 BGE Cross-Encoder 用独立问题重排，再选择最多 8 条供判断和回答。
  默认 `HYBRID_HEAD=1` 在证据总配额内保护 BM25 / 稠密各首位，余下名额按重排分数选择，
  最终仍按重排顺序呈现。多仓库配额很小时优先保护词法首位；设为 0 可关闭保护。
  中文分词与英文标识符的文档/问题处理一致；BM25、向量距离和重排分数不直接混加，
  任何分数都不是答案可信度。
- 问题显式指定 `owner/repo` 时限定对应仓库；跨项目问题平分召回和证据配额，避免单库挤占。
  明确指定 README 时只查询这些仓库的根 README；显式指定文件时尊重具体路径。
  召回和重排均保留独立问题的完整要求，不将多方面问题替换为泛化的项目简介。
- 默认 `README_POLICY=canonical`：同一来源、commit、目录有 `README.md` 时，
  在检索前优先使用它，避免不同语言的重复简介挤占名额；显式点名的其他语言 README 仍可检索。
  没有主 README 的目录保留现有语言版本，其他文档 / 源码不受影响。
  这是文件族选择而非语义等价证明，非主版本可能含独有信息；查这类内容时显式指定文件，
  或用 `README_POLICY=all` 关闭策略。原文和向量均不删除。
- 重排采用 FastEmbed `BAAI/bge-reranker-v2-m3-int8`，CPU 本地运行，首次下载约 570 MB。
  `RERANK_ENABLED=false` 可显式关闭作对照；`RERANKER_MODEL` 和 `RERANK_CANDIDATES` 可配置。
  改变重排配置、首位保护或 README 策略会隔离答案缓存，不需要重建 Embedding 向量；
  设 `RERANK_CANDIDATES` 小于 40 会主动裁剪并集。模型失败明确报错，不悄悄退回旧排序。
- `chat --json` 的 `contexts` 保存实际给回答模型的全部片段，不仅是被引用的片段；
  `retrieval` 记录两路候选、RRF 次序、重排原始分数、受保护 ID 及最终 ID。缓存命中时这些是原回答的历史追踪，
  不表示本次重新检索。默认 BGE 的配对窗口为 8192 tokens；若更换为小窗口模型，
  `windowing.windowed_ids` 明确记录被窗口限制的候选 ID，原文和生成上下文不因此截断。
- SQLite 保存元数据、会话、知识版本、失败事件、活动 chunk 清单和可复用向量；`data/snapshots` 保存原文快照。向量存 Milvus，不能只备份向量而丢弃原文和元数据。
- 导入先写向量并逐批读取校验，再以 SQLite 事务发布活动清单。失败不会激活半成品；查询只允许活动 ID，未发布和旧版本向量不会进入答案。重复内容不重新 Embedding，文件删除后退出活动清单。
- 仓库引用固定 commit、路径与行号。仓库自身资料、第三方分析、教程和未核验网页分开标注；许可证未知就记录 `unknown`。
- 旧向量和未发布快照暂时保留，首版没有自动垃圾回收。长期高频同步会增加磁盘占用。

## 采集边界

默认每仓库最多 60 个文件、10 MiB，单文件最多 256 KiB。优先 README、文档、示例和关键源码；这是**局部索引，不是完整源码研究**。`ka sources --json` 查看采集和跳过清单。排除依赖、生成文件、二进制和符号链接；API 树截断或文件请求失败时不发布残缺快照。

默认 `GITHUB_IMPORT_MODE=browser`：Playwright 导航 GitHub 仓库、固定 commit 目录和文件页，
从页面的结构化数据读取原文行，不把导航栏、Issue 列表或搜索摘要当源码。
仓库模式禁用网页 JavaScript，只允许经过公网校验的文档请求，不执行仓库内容，也不读取登录态。
浏览器模式同样可能遇到 403、429、页面结构变化或网络错误，不绕过验证码/权限限制，不悄悄回落到 API。
显式设置 `GITHUB_IMPORT_MODE=api` 可使用原有 API 采集路径。

浏览器默认最多展开 12 个目录页（`GITHUB_TREE_PAGES`），之后按已发现文件采样。
目录未展开、文件超限和排除项均写入覆盖记录，不能把已发现条目数当成完整仓库文件数。
目录或文件页面截断、commit/路径不匹配、选中文件读取失败时不发布新快照，保留旧知识。
普通网络连接失败最多重试一次；限流、权限错误不重试。

awesome-harness-engineering 的 README 在本次检查约 285 KB，超过默认 256 KiB，
因此仅该清单项显式设置 `max_file_bytes=524288`（512 KiB）；`ka seed` 应用该覆盖，
手动 `sync` 和过期刷新保留原采集范围中的大小策略。直接 `ka ingest` 仍采用环境配置的限制。
其他仓库保持默认。所有来源仍受每仓库总量限制，不截断大文件后冒充完整原文。

大仓库在根 README 后按文档、示例、源码轮转采样，避免文档占满全部名额；运行时代码优先于根目录配置文件，但仍不承诺自动识别出所有核心实现。

Markdown 按标题、代码按 Tree-sitter 符号边界切分，再使用模型 tokenizer 限制为 384 tokens、重叠 64 tokens。计数使用无截断的 tokenizer 副本，不破坏推理时的 batch padding。自定义 Embedding 必须与这个 token 上限兼容；首版验证对象为默认模型。

未覆盖实现可在 API 模式下通过 GitHub code search 定向补采，并作为补充来源保存，不覆盖原有仓库大快照。
此 API 通常需要 GitHub token。浏览器匿名 code search 要求登录，当前明确返回不可用，不假装“没有结果”；
已知文件路径仍可通过补充来源 URL 的 `paths` 参数定向采集，并可通过网页搜索发现相关文档。
每轮最多读取五份正文，最多两轮；搜索摘要不当作证据。动态网页在正文不足时由无登录态 Chromium 渲染，不绕过验证码或登录限制。

仓库七天、网页一天过期。检索涉及过期来源时刷新，失败保留旧内容并标注；`sync` 可显式更新。服务限流、无权限、404、连接错误分别记录，不能当作“没有知识”。

外部内容不作为指令，也不执行安装脚本、Skill 或 MCP。正文读取限制协议、端口、响应大小，校验 DNS 并固定连接到公网地址，每次重定向及浏览器子请求重新检查；阻止私网、回环、元数据地址、WebSocket 与非 GET 浏览器请求。提示词注入防护仍需持续对抗测试，不能宣称对所有攻击免疫。

## 运行和维护

- 数据目录默认固定到项目根目录，不受启动目录影响，可用 `DATA_DIR` 隔离。Docker 使用独立命名卷，不修改其他项目容器。
- Compose 固定镜像版本或摘要；本次 MinIO 使用本机已缓存官方镜像的不可变摘要，避免旧 tag 无法拉取。其他机器若注册表不再提供该摘要，需要可访问的可信 MinIO 镜像，不要改用来源不明镜像。
- Milvus 设置 `restart: unless-stopped`，应对休眠后 etcd 会话丢失等异常退出；不保证任意故障自动恢复，仍需检查 `ka doctor`。
- `docker compose stop` 停止服务而保留数据。备份 SQLite、snapshots、模型标识和 Docker 命名卷；不要用删除卷的命令代替停止。
- 切换 Embedding 模型 / 维度必须使用新的 `DATA_DIR`，重新 `seed` / `ingest`；不要绕过模型校验或把新向量写进旧集合。可用 `INDEX_NAMESPACE` 隔离测试 Milvus 集合。
- 首版只支持同一个知识目录的单写入进程；不要并发执行 `seed`、`ingest`、`sync` 或会触发采集的多个聊天进程。
- 锁文件使用已验证的 `tree-sitter 0.25.2`。本机 `0.26.0` 在长源码符号解析后出现错误行边界和进程崩溃；
  原文件及五种语言长源码回归在固定版本后通过，不能只更新原生解析器而跳过回归。
- Redis 故障可跳过缓存；Milvus 故障必须明确报错，不能悄悄变成联网助手。若先取得网页、随后入库失败，则可引用未保存材料回答，但显式警告且不缓存。
- LLM 结构化返回空值或解析失败时按 schema 校验、最多尝试两次，仍失败则明确报错，
  不视为充分证据，也不触发联网兜底。CLI 意外错误只记录脱敏代码位置，不输出原始异常正文。
- 没有后台定时任务，也没有完整离线 LLM、自动执行下载代码、私有仓库采集或登录态浏览器能力。
- 会话问题、回答和缓存保存在本机；远程 LLM 会接收问题及相关上下文 / 证据，联网搜索会向配置的检索服务发送检索问题。不要把“本地知识库”理解为所有数据都不离开本机。

## 测试

测试代码、夹具与人工核对题位于 `tests/`，固定 RAGAs 题集位于 `eval/`。
最新必要验收记录见 [data/acceptance/README.md](data/acceptance/README.md)；
旧轮次产物、隔离测试库及工具缓存已清理，不作为运行测试的依赖。

```bash
uv run pytest -q
uv run ruff check src tests
KA_BROWSER=1 uv run pytest -q tests/test_live.py::test_real_browser_repository_body
KA_BROWSER_REPO=1 uv run pytest -q tests/test_live.py::test_real_browser_repository_commit_and_source_lines
KA_SERVICES=1 uv run pytest -q tests/test_live.py::test_real_milvus_embedding_redis_and_restart
KA_RERANK=1 uv run pytest -q tests/test_live.py::test_real_cross_encoder_prefers_relevant_evidence
KA_LIVE=1 uv run pytest -q tests/test_live.py::test_real_terminal_seed_local_cache_online_reuse
uv run python scripts/browser_seed_acceptance.py --files 6 --tree-pages 3 \
  --evidence data/acceptance/手动指定的新目录
uv run python scripts/verify_snapshots.py --report data/acceptance/快照一致性.json
KA_CATALOG_QA=1 KA_EVIDENCE_DIR="$PWD/data/acceptance/新增项目问答" \
  uv run pytest -q tests/test_live.py::test_real_imported_catalog_qa_and_cache
KA_QA_LIVE=1 KA_EVIDENCE_DIR="$PWD/data/acceptance/手动指定的新目录" \
  uv run pytest -q -s tests/test_live.py::test_real_web_qa_cache_context_online_and_reuse
```

普通测试使用明确标注的测试替身验证分支，不等于真实模型或 E2E。`KA_SERVICES=1` 使用真实 Embedding、Milvus、Redis，但输入为合成集成样本，不证明真实项目问答效果。`KA_LIVE=1` 用真实子进程导入当前全部 13 个精选清单的有限文件，验证本地、缓存、联网和再复用；缺少凭证或发生真实失败就失败，不降级为 mock。
`KA_RERANK=1` 下载并运行真实本地重排模型，但相关 / 无关输入仍为明确标注的合成探测样本。

## RAGAs 评估

```bash
uv sync --extra test --extra eval
uv run ka evaluate --compare --output data/acceptance/自行指定的新评估目录
# 复用已生成的答案，只重新评估；新输出目录不会覆盖原始失败记录
uv run ka evaluate --answers data/acceptance/原目录/answers.jsonl \
  --output data/acceptance/新的评分目录
# 新冻结的 4 题，不与原六题均值混算
uv run ka evaluate --dataset eval/holdout.json \
  --output data/acceptance/自行指定的新保留集目录
```

这是显式运行的付费远程评估，不在启动时自动触发。生成答案与评估裁判均使用当前
`glm-5.3-flashx` 配置，不回退到其他模型；回答相关性的 Embedding 仍在本地运行。
RAGAs 使用 0.4 系列现代 metrics collections / Instructor 接口，锁定兼容的
`langchain-community 0.3.31`，以避开其 0.4 系列移除 VertexAI 导入导致的上游兼容错误。
裁判输出预算 `EVAL_MAX_TOKENS=16384`，用于容纳忠实度的逐事实判断；超出预算仍明确失败，
不会把截断输出当有效评分。此设置只影响评估，不改变问答模型或检索配置。

默认 `eval/questions.json` 为人工核对原文的 6 题，包含中文、英文、Context 代码用法和跨项目对比。
`--compare` 对相同知识版本分别运行混合检索、混合检索＋重排，两组均绕过答案缓存，
只从本地检索但仍调用远程 LLM；不会将参考答案传给问答模型或写入知识库。
这是小样本回归，不是全仓基准或统计显著性实验；答案生成保持应用模型配置，
不同新鲜生成间存在随机性，同模型担任裁判也有偏差。

四项 RAGAs 指标：

- `faithfulness`：回答中的原子事实由实际生成上下文支持的比例。
- `answer_relevancy`：根据回答反向生成问题，与原问题比较本地向量相似度。
- `context_precision`：对照人工参考答案判断片段是否相关，计算排序的平均精确率。
- `context_recall`：人工参考答案的事实被检索上下文覆盖的比例。

`answers.jsonl` 保存答案、参考、全部生成上下文和候选追踪；`metric-events.jsonl`、
`scores.jsonl` 保存逐项结果，`report.json` 汇总每个配置的均值、有效/预期数量和失败数量。
另有严格原文标签的 `support_recall` / `support_mrr`，只用于辅助定位漏召回，不等同于完整语义标注。
API 错误、超时和 NaN 会保留为缺失并令命令退出 1，不改成零或静默丢弃；
`complete=true` 仅表示指标执行完成，不表示答案正确率达标或重排一定更好。
上轮真实基线、失败记录和质量边界见 `EVALUATION.md`；
本轮覆盖优化、新题集及质量权衡见 `OPTIMIZATION.md`，验收另存新目录，不覆盖基线。

## 真实验收记录

`browser_seed_acceptance.py` 在配置的真实知识库中逐仓库运行 CLI `seed`，
保存逐仓库的退出码、文件数、嵌入数、耗时和覆盖清单，并校验固定 commit / 文件 / 行号引用。
默认每仓库 6 个文件、3 个目录页，这是明确限定范围的小批量入库，不是全仓索引；用新证据目录重跑可调整限额。

`KA_QA_LIVE=1` 单独验证真实官方网页入库后的问答闭环，不替代 13 仓库导入验收：
重复导入零嵌入、本地充分命中、不可用模型/向量地址下的 Redis 缓存命中、上下文追问、
本地证据不足、Tavily 联网补充、新会话 `--local-only` 复用、知识版本更新后的缓存失效及真实终端操作。
逐条命令、退出码、耗时、回答和引用保存在指定目录。每次应指定新目录，避免旧知识干扰。

`tests/acceptance_questions.json` 是待人工核对的中英文、源码、跨项目问题集，不是已通过结果。每条需核对事实、引用和覆盖不足提示；项目本地设计与上游项目事实不能混为一谈。实际执行结果见 `VALIDATION.md`。
