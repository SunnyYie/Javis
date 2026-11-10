# Brain-Leader

新增通用入口 `uv run javis chat`，支持静态 Career/Rental 团队调度。
`bl` 仍是原求职兼容入口；职业业务已归属 `agents/Career-Team`，旧导入仅转发。
租房配置与用户确认见 [Rental Team](../agents/Rental-Team/README.md)，
真实来源未通过项及本轮结果见 [实施与验收](../docs/Javis实施与验收.md)。
高德公共地点与公交路线接口已真实验证；自如尚未通过，不能视为双来源完整发布。

单用户终端求职 Agent。连接现有三个助手，贯通职业事实、简历版本、岗位匹配、
有来源的备考、逐轮模拟面试、复盘与确认后的长期记忆。

## 启动

```bash
cd /Users/sunyi/ai/Agent/Javis/Brain-Leader
uv sync --extra test --locked
uv run bl doctor
uv run bl chat
```

依赖 Python 3.12。现有知识助手与题库的 Milvus/Redis 服务需已启动；
岗位缓存使用 Job-Agent 原有 Redis。主应用不新增容器；性能优化复用原助手的检索实现，
不修改数据库结构。
新虚拟环境通过当前路径安装三个助手及共享工具包，不依赖旧虚拟环境的绝对路径入口。

模型默认显式读取 `../agents/Knowledge-Assistant/.env` 的四个模型字段，
沿用 `glm-5.3-flashx`。其他模型来源、运行目录参照 `.env.example`；
所有主应用环境变量使用 `BL_` 前缀。子助手分别加载自己的搜索、数据库配置，
主应用不修改全局环境变量，也不复制密钥。

`doctor` 检查配置及服务，不调用模型。`doctor --probe-model` 才发送收费结构化探针。
`--local-only` 禁止两个知识助手的资料联网补充，**不禁止远程 LLM，也不禁止显式 `/search`**。

首次交互需确认远程数据范围。批处理可显式使用：

```bash
uv run bl chat --allow-remote --session my-career -q "我希望先整理项目经历，不要搜索岗位。" --json
uv run bl chat --session my-career
```

`--json` 仅支持单次 `-q`，标准输出为一个 JSON 对象，进度写入标准错误。
输出统一包含 `status`、`data`、`citations`、`warnings`、`artifacts`、`error`。
失败返回非零退出码；`partial` 表示真实部分结果，不等于全部要求已满足。

## 简历

已有简历：

```text
/resume "/绝对路径/我的简历.pdf"
/facts
/draft
```

复用 Job-Agent 的 PDF 文本层、图片和扫描页 OCR、文件大小限制和引用核验。
原简历工作、项目、教育引用及可定位的技能进入已确认事实；它们是用户材料的自述，
不表示程序验证了实际任职或业绩。可通过 `--force-ocr` 强制识别。

从零收集：

```text
/facts add projects 我负责内部检索工具的API和结果列表，用请求日志排查重复调用。
/facts add skills Python、React、LangChain。
/facts add education 示例大学 计算机相关专业 本科。
/draft
```

以上均为演示内容，不是用户默认经历。自然语言也可提供经历，由 Agent 提出候选，
再用 `/facts confirm ID` 确认。`/facts add` 本身是用户明确录入并确认。

简历模型只编排已确认事实；正文不能直接使用模型新写的经历。
润色建议保存为未确认候选，检查后确认并重新 `/draft`，才会进入后续版本。
已确认润色在新草稿中替代对应原表述，不影响旧草稿及其事实追溯。
用户确认是事实真实性的最后一道人工检查，不应盲目批准模型建议。
`/facts reject ID` 拒绝候选；已确认事实和已有简历版本不原地覆盖。

```text
/adopt resume_返回的ID
/contact {"name":"本地姓名","phone":"本地电话","email":"本地邮箱"}
/export resume
/export resume resume_草稿ID preview
```

采纳会使用现有解析器建立当前匹配画像，使旧条件确认失效。
三格式导出从同一内容生成 Markdown、可编辑 DOCX 和 PDF，不再调用模型。
Word 和 PDF 嵌入有授权的 TrueType 字体子集，避免依赖阅读设备的中文字体。
默认检测 macOS 黑体、Windows 宋体或 Linux 文泉驿；缺少字体时设置
`BL_RESUME_FONT_PATH`，TTC 可额外设置 `BL_RESUME_FONT_INDEX`。缺字或禁止嵌入的字体明确报错。
联系方式仅本地合入，不进入主 Agent 的工具状态；预览标识“未采纳”，不等于正式版本。
导出保存到会话内的新目录，不覆盖已有文件。

## 岗位与备考

```text
/prefs {"roles":["你实际期望的岗位"],"cities":["你实际期望的城市"],"target_monthly_k":20}
/status
/confirm
/import "/绝对路径/ohmyboss_jobs.json"
/match
/jobs
/detail 岗位ID
/target 岗位ID 批次ID
/questions RAG检索增强生成 --count 3
/knowledge LangChain如何定义工具？
/links RAG
/learn 补充检索评估和引用核验
```

岗位、城市、薪资必须由用户明确提供。`/confirm` 前必须展示当前摘要；
简历或条件变化后需重新确认。`/search` 显式调用原 BOSS 可见浏览器，
只读、限量、每轮至多一次，手动登录，不绕过验证码、登录或访问限制。
**BOSS 登录后实时搜索当前仍不能宣称通过验收。** 文件导入和合成 JD 测试不是实时招聘库存。

目标岗位保存固定批次与内容，不随刷新改写。条件变化优先 `/match`，
无需反复抓取；原助手的城市、薪资、经验差距、未知项及旧结果禁止导出规则保持不变。
`/show 记录ID` 可查看本会话已有简历、题单、知识回答和复盘产物。

题目保留难度、原题/生成题标记、答案和引用。题量或依据不足按实际返回，
不虚构补齐。SQLite 保存岗位、知识点、题目、原始资料和作答关联；
程序区分原文引用与模型推断，不额外引入图数据库。
个人简历、联系方式、完整聊天和模拟作答不写入共享知识语料。
技术检索词和模型生成的题目仍会按原助手规则保存在其记录与缓存中。

## 面试与复盘

```text
/interview technical 5 2
/answer turn_当前题目的token 我的回答
/pause
/continue
/finish
/review
/export review
```

模式为 `technical`、`project`、`behavioral`、`mixed`，默认五道主问题、每题最多两次追问。
项目模式需要已确认项目事实；技术题使用已核验的题单。题目、参考资料及评分规则在开始时冻结。

面试期间可直接输入回答，也可用 `/answer TOKEN 内容`。自动化重试须沿用同一 token，
相同内容返回已完成结果，不重复推进；同 token 改内容会被拒绝。
直接自然语言回答按当前题提交，不具备跨题自动重试识别。
`Ctrl-C` 取消未完成评价，不推进题目；已完成步骤保留。

暂停后用同一会话恢复，`/continue` 继续；暂停不开放答案。
程序阻止题单、知识、导出和复盘等旁路，直到用户明确 `/finish`。
逐题评分按三维 0–4 分，无法评价记为空，不按零分计算；
模型通过当前回答的片段编号引用原话，程序核对并还原引文，不接受编造的片段编号。
核验失败只重试当前未完成评价一次，不重复已保存回答或已完成采集。
追问后的最终评价替代本题较早评分，避免追问多的题权重更大。
复盘展示作答原话、依据、差距、参考回答或带占位的表达框架、覆盖率和学习任务，
不验证项目真实性，不推断面试官意图，不代表招聘结果。

导入真实面试记录：

```text
/transcript "/绝对路径/面试记录.md"
/review transcript_返回的ID
```

支持 UTF-8 TXT、Markdown、JSON，限 1 MiB、60000 字符；按用户授权复制并脱敏。
复盘引用必须存在于原记录，不为不完整记录虚构分数、对话或面试官意图。

## 记忆与运行数据

```text
/memory
/memory add preference 我希望先练技术题
/memory confirm memory_候选ID
/memory correct memory_旧ID 修订后的内容
/memory delete memory_ID
/tasks
/done learning_任务ID
```

会话自动保存；只有明确确认的事实、偏好和薄弱项跨会话使用。
评分产生的薄弱项首先是候选。修订先删除旧确认项，新内容重新待确认。
主状态位于 `Brain-Leader/data`，长期记忆位于根目录 `memory/profile.sqlite3`，
均为私有运行数据，默认文件权限 `0600`、目录 `0700`，不是加密存储。
规则脱敏不是通用 PII 检测器，敏感材料应先人工脱敏。

```bash
uv run bl sessions list
uv run bl sessions delete 会话ID
```

删除会话清理自身输入副本、导出、面试记录、来源于该会话的长期记忆及岗位缓存，
不删除用户原始文件和共享知识库。Redis 不可用时报告缓存可能残留至 TTL。
外部备份和用户自行复制的文件不在清理范围。

主应用通过运行目录锁禁止同目录并发。**现有三个助手是独立应用，不共享此锁；
使用 Brain-Leader 时不得同时运行同库的 ka/iqb chat、ingest、sync、seed 等操作。**
首版没有多用户、后台爬虫、自动投递、招聘消息、语音和 Web。

## 每日一题

`bl daily` 提供独立的 AI Agent 技术学习题、逐项答案来源、细分领域和联网筛选的相关文章，
通过指定飞书机器人向账号邮箱发送，不读取职业会话或个人材料。
使用飞书 Card 2.0 分层展示，延伸阅读最多三篇。工作日北京时间 11:00 运行，
仅从 RAG、LangChain、Harness engineering、Claude Code底层实现、Multi-Agent 五类选题，
每周持久化随机安排顺序，同一大类一周最多推送一次。
具备来源核验、内容边界检查和持久发送去重。
私有配置、命令、定时运行条件与故障处理见 [DAILY.md](DAILY.md)。

## LangSmith 调用链

默认关闭，不依赖全局 `LANGSMITH_*` 开关。不需要 FastAPI 或将助手改成 HTTP 服务。
本次实现的实测链路及验收边界见 [TRACING.md](TRACING.md)。

仅记录本地调用链：

```bash
uv run bl chat --trace-local --allow-remote -q "解释 LangChain 工具调用" --json
uv run bl trace-report /绝对路径/runtime/traces/返回的trace_id.json
```

上传前，在私有 `Brain-Leader/.env` 中配置专用凭证，不能使用 LLM API Key 替代：

```dotenv
BL_TRACE_MODE=langsmith
BL_LANGSMITH_API_KEY=填写专用LangSmithKey
BL_LANGSMITH_PROJECT=brain-leader
BL_LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# 组织级 Key 如需工作区，显式填写授权工作区 UUID：
# BL_LANGSMITH_WORKSPACE_ID=
```

```bash
uv run bl chat --allow-trace-upload
uv run bl chat --allow-remote --allow-trace-upload -q "解释 LangChain 工具调用" --json
```

交互模式也可逐次启动时确认上传；授权不保存到职业画像，模型不能授予。
`--allow-remote`、`/consent` 只授权原有模型处理，**不授权 LangSmith**。
`--trace-local` 强制本地；缺少专用 Key 或上传授权时只落本地。
普通 `bl doctor` 仅显示配置状态，不创建 LangSmith 客户端或探测云端。

追踪只保留随机 trace/span ID、固定节点名、父子关系、时间、状态、数量、
缓存命中及模型返回的 Token 数；不保留提示词、工具参数、简历、联系方式、
面试作答、参考答案、外部正文、原始异常、文件路径或业务会话 ID。
云端 `inputs/outputs` 均为空，禁用自动运行环境采集，不能用它回放私有对话或评估答案正文。
Token 用量缺失时显示 `token_usage_runs` 覆盖，不推算费用或把缺失当实测零值。

调用链位于 `BL_DATA_DIR/traces`，权限为目录 `0700`、文件 `0600`，已被 Git 忽略。
这是不含业务会话 ID 的独立运行元数据，不随会话删除；可按本机保留策略单独清理。
每次请求最多 512 个 span，上传队列最多 8 个请求，单次网络连接/读取超时各 2 秒、
不自动重试，关闭最多等待 3 秒；失败、队列满或本地落盘失败不改变业务结果。
`queued` 不表示上传成功；文件中的 `sent_unverified` 也只表示 HTTP 接收，
仍须在授权项目按 `trace_id` 核验可见性，不能据此宣称云端验收完成。

主控图、工具、模型及三个助手通过同进程回调关联；知识线程显式复制上下文，
仍在原线程创建、使用和关闭 SQLite。记录检索、Embedding、向量查询、
Reranker、缓存及题库生成/核验阶段，不修改原助手的独立 CLI。
节点时间含子节点，流式调用可重叠，不能将表中各行相加，也不是 QPS/P99 压测。
SDK 内部 HTTP 重试不是独立模型调用，外部资料采集目前以图阶段记录，不逐 URL 抓包。

真实验证脚本会调用收费模型、复制语料到独立命名空间，必须使用全新输出目录：

```bash
uv run python -B scripts/trace_smoke.py /绝对路径/新隔离目录
# 仅在已有专用 Key 且明确同意遥测上传时添加 --upload
```

程序集成应使用 `with service.telemetry.request("chat"):` 包住业务调用并在最后
`service.close()`。上传另需宿主获得用户授权后设置
`service.telemetry.upload_allowed = True`；不能把这项设置注册为模型工具。

## 扩展与验证

根目录 `skills` 的六份 `SKILL.md` 是按需读取的流程指导，不是可执行代码；
Python 工具在根目录 `tools` 静态注册。增加文件不会自动获得文件权限或工具执行权。
主 Agent 基于 LangChain `create_agent`，面试提交链基于 LangGraph；
SQLite 存实际业务状态、不可变产物和工具事件，不依赖聊天摘要恢复。
主控工具按串行配置执行；两套知识助手的创建、调用和关闭使用同一专用线程，
避免复用 SQLite 连接时跨越 LangChain 工具线程。

```bash
uv run pytest -q
uv run ruff check src tests ../tools/src
```

普通测试使用明确测试替身验证分支，不能当作真实模型或 E2E。
真实验收先创建已有语料和向量的隔离副本：

```bash
uv run bl isolate-corpora data/acceptance/自行选择的新目录/corpora
```

根据其 manifest 设置 `BL_KA_DATA_DIR`、`BL_KA_NAMESPACE`、
`BL_IQB_DATA_DIR`、`BL_IQB_NAMESPACE`，再设置独立 `BL_DATA_DIR`、
`BL_MEMORY_DIR`、`BL_EVIDENCE_DIR`，执行：

```bash
BL_LIVE=1 uv run pytest -q -s tests/test_live.py
```

该测试调用真实 GLM、Embedding、重排、Milvus、Redis 和终端 PTY，但职业、岗位输入
明确为合成材料，不构成实时招聘验收。真实失败不得改为 mock。
可设置 `BL_LOCAL_ONLY=true` 做本地语料对照；已有库不一定覆盖操作级问题，
证据不足时该轮验收仍然失败，不把 `sufficient=false` 当通过。
每次使用新输出目录与隔离命名空间，不对正式库写入测试数据。
Word/PDF 需另外渲染逐页检查；JSON/XML 内容一致性不能代替视觉验收。
可复用输入保留在 `tests/fixtures`；岗位样例由测试按需生成，布局样例由
`scripts/visual_fixture.py` 生成。`data/acceptance` 仅用于可清理的测试运行产物，
不要在其中存放正式会话或唯一原始材料。
历史通过数量、失败摘要和未验证范围见 `ACCEPTANCE.md`；历史原始运行产物已按要求清理。

## 性能

真实 profiling、Locust 对照结果和限制见 [PERFORMANCE.md](PERFORMANCE.md)；
安装及复现命令见 [perf/README.md](perf/README.md)。压测使用临时 loopback HTTP 适配器，
不是生产 Web API，不向远程 LLM 或招聘网站发送压测流量。

两套知识助手默认使用每实例 32 条的精确推理 LRU，复用相同查询向量和相同有序候选的
重排分数，不缓存整份引用或绕过来源检查。缓存仅在进程内，重启清空；
活跃片段快照检查本连接写入及外部 SQLite 提交，返回独立对象。
`BL_INFERENCE_CACHE_SIZE=0` 可关闭推理缓存；未命中的新查询仍需完整 CPU 推理。
可选 `BL_EMBEDDING_BATCH_SIZE`、`BL_RERANKER_BATCH_SIZE` 覆盖子助手配置；
默认 Embedding 为 16，知识重排为 4、题库重排为 8。INT8 模型可能对批组成敏感，
调整前须比较质量，不能只看 QPS。
