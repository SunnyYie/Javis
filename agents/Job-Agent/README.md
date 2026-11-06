# Job-Agent

根据简历和明确的岗位、城市、薪资要求获取岗位，给出有原文证据的匹配解释。
终端连续对话，支持 BOSS 实时查询及 ohmyboss JSON/CSV 导入。
只读搜索，不自动打招呼、投递或向招聘平台上传简历。

## 启动

需要 Python 3.12、uv、Docker 和可见桌面环境。

```bash
cd /Users/sunyi/ai/Agent/Javis/agents/Job-Agent
uv sync --extra test
docker compose up -d --wait
uv run playwright install chromium
uv run ja doctor
uv run ja chat
```

本机已按授权配置 `.env`，通过 `LLM_ENV_FILE` 显式读取相邻知识助手的
`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TIMEOUT`，不修改源文件，
不导入其他服务凭证。本项目环境变量及 `.env` 中的显式模型配置优先。
其他环境参照 `.env.example` 创建配置；模型须支持 OpenAI 兼容接口与 tool calling。

`ja doctor` 不调用模型；`ja doctor --probe-model` 才发送一次合成结构化探针，可能产生费用。
普通检查中的浏览器安装状态不等于浏览器已启动或网站可访问。

Redis 独立绑定 `127.0.0.1:16381`，不修改其他项目容器。
已有 Chrome 时可配置 `BROWSER_CHANNEL=chrome`。Redis 不可用会明确跳过缓存，
SQLite 与本地文件仍然保存；模型、浏览器和来源失败不会伪装成成功。

## 连续对话

首次启动说明远程模型数据范围并征求同意。OCR 本地运行；对话、脱敏职业资料和岗位正文
会发送至配置模型，不发送原始 PDF/图片。LangSmith 自动追踪在调用期间关闭。
姓名采用常见中文头部/字段规则去除；复杂版式、英文名或不常见身份信息的识别并不完备，
含敏感材料时应先提供自己确认过的脱敏副本。

```text
/resume /Users/sunyi/Documents/Codex/2026-11-04/new-chat-2/outputs/resume.pdf
```

Agent 展示职业摘要后，用自然语言提供你的实际期望，例如：

```text
我想找前端工程师，杭州，税前月薪25K。先不要搜索。
/status
/confirm
/search
为什么推荐这个岗位？
只看杭州，把目标薪资提高到30K，先不要搜索。
/confirm
/match
/export md
/quit
```

以上岗位、城市与薪资仅为使用示例，不是预设的个人偏好。
`/confirm` 只确认当前摘要及条件，不自动查询。条件或简历变化后确认失效，
旧结果不能继续导出为当前推荐；重新匹配不必重新采集。
自然语言明确要求查找且已确认时，Agent 也可调用搜索工具。

终端命令：

| 命令 | 行为 |
| --- | --- |
| `/resume 路径 [--force-ocr]` | 明确授权并解析该简历 |
| `/import 路径` | 明确授权并导入岗位文件 |
| `/prefs JSON` | 直接更新条件 |
| `/status`、`/confirm` | 查看摘要、确认当前版本 |
| `/search`、`/match` | 实时查询、重新匹配 |
| `/detail ID` | 原始字段、官方链接、证据、差距与未知项 |
| `/export json\|csv\|md` | 导出当前匹配，包含排除原因 |
| `/new`、`/quit` | 新建会话、退出 |

模型只能读取终端命令明确授权后复制到本会话的文件，不能自行读取 `.env` 等其他文件。
使用 `/prefs` 时字段为 `roles`、`cities`、`target_monthly_k`、`excluded_terms`：

```text
/prefs {"roles":["前端工程师"],"cities":["杭州"],"target_monthly_k":25,"excluded_terms":[]}
```

`Ctrl-C` 中断当前操作，保留已完成的解析、采集和工具操作，不保存半截模型回答。
启动时显示会话 ID，用 `uv run ja chat --session 会话ID` 恢复条件和已完成对话。
首版为单用户；不要并发操作同一会话。采集使用跨进程文件锁，一次仅一个浏览器任务。

## 简历与 OCR

支持 PDF、PNG、JPEG、TXT 和 Markdown。输入限 10 MiB、PDF 最多 20 页；
图片最多 4000 万像素。PDF 逐页优先用 pdfplumber 提取文字与位置，
扫描页通过 pypdfium2 渲染，再以 RapidOCR 3.10 / ONNX Runtime CPU 中文识别。
首次 OCR 下载约 30 MB 模型；文字不足、损坏、识别失败显式报错。

```bash
# 仅本地提取，无远程模型调用
uv run ja resume parse /绝对路径/resume.pdf --extract-only --session my-local

# 即使 PDF 带文本层也实际运行 OCR
uv run ja resume parse /绝对路径/resume.pdf --extract-only --force-ocr --session my-ocr

# 结构化职业信息，明确允许远程模型
uv run ja resume parse /绝对路径/resume.pdf --allow-remote --session my-search
```

保留每页原文、坐标、提取方式和 OCR 置信度；工作、项目、教育条目附页码与原文引用，
引用不在原文中则拒绝发布该结构化结果。OCR 平均置信度不是全文正确率。
工作年限只合并工作经历的月份区间，排除重叠、未来月份和当前未完成月份，
不累计教育或项目时长；缺失日期不猜测，列为待确认。

## 岗位获取与匹配

BOSS 使用项目独立的可见 Playwright 浏览器；没有有效登录态时需用户手动登录。
登录态保存在会话内，不复用其他项目或个人浏览器 Cookie。

也支持用户主动提供的 BOSS Cookie JSON（Cookie-Editor 数组或 Playwright
`cookies` 字段），必须由用户显式执行导入，不能让模型读取凭证：

```bash
uv run ja auth import-cookies /绝对路径/cookies.json --session my-search
uv run ja auth check --session my-search
```

若页面持续超时，可单独运行一次首页网络诊断（macOS/Linux）：

```bash
uv run python scripts/diagnose_boss.py --session my-search
```

该诊断有 60 秒总时限，只记录域名、无查询参数的路径、状态码及失败类型，
报告保存到私有 `data/acceptance/boss-diagnostic-*/report.json`；
不采集岗位，不记录 Cookie、请求头或响应正文。未确认登录时退出码为 2。

导入仅接受 `zhipin.com` 及子域名，跳过过期 Cookie，丢弃 localStorage，
以 `0600` 权限原子保存到该会话。原始文件不修改，Cookie 不进入模型工具、
聊天记录或 Redis。`auth check` 仅访问首页，不读取岗位或等待自动登录，
必须看到已登录页面标识才返回 `login_verified: true`；失败退出码为 2。
导入成功不等于登录成功。凭证失效时重新手动登录或显式提供新文件，
不要从其他浏览器、项目或他人的会话寻找登录态。
仅访问官方站点及指定静态资源域，不跟随任意来源的岗位链接；
不会点击沟通、投递、提交简历等操作。

默认最多 30 个去重岗位，单浏览器串行采集，请求和滚动间隔至少 3 秒，
每个关键词/城市最多三轮列表读取。提供的城市编码见 `boss.py` 中的 `CITIES`，
不支持的城市明确报错，不静默换城市。
同一轮模型对话最多启动一次采集；如需超过 30 条，用户必须显式使用：

```bash
uv run ja search --session my-search --role 前端工程师 --city 杭州 \
  --salary 25 --confirm --limit 50 --expand-limit
```

不带 `--confirm` 时只展示条件，不采集。单次模型评估最多 30 条；
扩大采集后的剩余未评估数量会明确报告，首版不会自动增加模型调用批次。

遇到登录失效、验证码、访问限制或页面结构变化即停止并记录原因及本地截图，
保留已读到的卡片与详情，不把失败当作零岗位。不采用 CloakBrowser、反检测伪装或验证码绕过。
请遵守招聘平台条款与适用法律；普通浏览器也可能被平台拒绝，不能保证稳定采集。

```bash
uv run ja jobs import /绝对路径/ohmyboss_jobs.json --session my-search
uv run ja match --session my-search --confirm
uv run ja export --session my-search --format json
```

导入接受数组 JSON 或 UTF-8 CSV，兼容 ohmyboss 的 `name`、`link`、`company`、
`city`、`salary`、`experience`、`education`、`description` 等字段。
按规范化官方链接去重；无链接时按标题、公司、城市、地址去重，缺失字段可能误合并，
因此建议保留原始链接。非法链接逐条拒绝并报告，不访问导入文件中的任意 URL。
来源分别标记 `boss_live` 与 `ohmyboss_import`；导入文件没有带时区的采集时间则记为未知，
不能把导入时间当实时抓取时间。原始字段、文件副本和快照在本地保留。

匹配先做确定性筛选，再以模型判断技能、学历和项目关联：

- 明确城市不符、薪资上限低于目标或命中用户排除词，不能被模型改成符合。
- 目标 25K、岗位 20–30K 保留为“可尝试／可谈”，显示下限不足，不承诺实际报价。
- 面议、币种和周期不明列为待确认；不把年薪、日薪、奖金和多薪擅自换算。
- 27 个月经历面对 3–5 年岗位可以尝试，但必须显示经验差距。
- 默认不排除初创、外包、大小周等公司或岗位。`excluded_terms` 是原文字面筛选，
  可能命中否定语境，结果中保留原因与原文以便核对。
- 结果为“满足条件／可尝试／不符合／信息不足／评估失败”；
  模型失败、无效 JSON 或引用校验失败不作为匹配成功，不缓存失败。

JSON、CSV、Markdown 导出包含原始岗位信息、来源时间、证据、缺口和排除原因。
CSV 对公式前缀进行转义。来源字段由程序展示，模型解释不代表录用概率或招聘方承诺。

## 存储与清理

SQLite 保存会话、当前简历、条件、岗位、任务和完整已完成对话；
`data/sessions/<ID>` 保存简历版本、输入副本、登录态、快照和导出。
每次采集/导入固定独立岗位版本，后续刷新同一岗位不会改写旧匹配所引用的版本。
对话模型每次使用最近 20 条已完成消息，并通过工具读取当前结构化状态。
Redis 按绝对数据目录和会话隔离页面解析与模型结果，默认 24 小时 TTL；
键包含内容、解析/提示词版本、模型及条件，不缓存实时搜索列表。
`DATA_DIR` 可隔离运行目录；模型仍使用显式配置的远程服务。

```bash
uv run ja sessions list
uv run ja sessions delete my-search
docker compose stop
```

删除会话同时清理该会话的 Redis 缓存、输入副本、登录态、快照与导出，
不删除用户原始文件，不影响其他会话；Redis 不可用时明确报告缓存可能残留至 TTL。
备份和自己另行复制的文件不在清理范围。
`.env`、简历和全部运行数据均被忽略，不能提交到版本库。

ohmyboss 的引用版本、改造范围及 MIT 许可证见 `NOTICE`。

## 验证

```bash
uv run pytest -q
uv run ruff check src tests scripts

# 真实简历、OCR、远程模型、Redis 和终端；会产生模型费用
JA_LIVE=1 JA_RESUME=/绝对路径/resume.pdf \
JA_EVIDENCE_DIR="$PWD/data/acceptance/新目录" \
  uv run pytest -q tests/test_live.py

# 可见浏览器，只读网站连接/登录探针，不等于登录后搜索验收
uv run python scripts/probe_boss.py
```

可复用的静态导入样例保存在 `tests/fixtures/synthetic-jobs-NOT-LIVE.json`，
说明见 `tests/fixtures/README.md`。该文件完全合成，不是真实招聘库存；
其他测试输入按需生成到临时目录，无需保存历史运行数据库或真实简历衍生物。

普通测试使用显式测试替身验证分支；live 测试中的 JD 明确为合成输入，
不能当作真实招聘库存。BOSS 探针和登录后的完整搜索验收单独记录。
真实服务缺失或断言失败时保留失败，不降级为 mock。
验收状态见 `ACCEPTANCE.md`。
