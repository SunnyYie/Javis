# 验收记录

日期：2026-11-05（Asia/Shanghai）。所有真实调用使用本项目获授权导入的模型和 Tavily 配置，未打印密钥；没有 GitHub Token。

清理说明：用户随后要求仅保留必要测试样例，本文中的历史 `data/acceptance/` 产物已删除。历史通过与失败结论保持原样；当前保留的评估样例、报告和失败摘要见 [data/evaluations/README.md](data/evaluations/README.md)。

本文保留初版功能验收及其失败历史。新增混合检索、BGE Reranker 和 RAGAs 的最终代码验收见 [RAG_EVALUATION_REPORT.md](RAG_EVALUATION_REPORT.md)：118 项离线测试、真实数据库回归、第二轮 CLI E2E 与 12/12 项真实评分已完成，排序质量回退另行列明。

当前模型为 **`glm-5.3-flashx`**。`.env`（0600）、代码默认值、配置模板及配置导入脚本均已切换。用户指定模型之后不再调用原模型；原模型的证据仅作为历史保留，不作为当前模型的验收证明。

## 已通过

| 检查 | 实际结果 |
| --- | --- |
| 独立虚拟环境 | `uv sync --extra test --locked --offline` 退出 0；109 个锁定依赖；本项目独立安装，已移除复制环境中的旧项目安装 |
| 离线测试 | `.venv/bin/pytest -q`：93 passed，6 skipped，2.16 秒；跳过项均为显式 opt-in 的真实测试 |
| 静态检查 | `.venv/bin/ruff check src tests scripts`：All checks passed |
| 服务启动 | `docker compose up -d --wait` 退出 0；etcd、MinIO、Milvus、Redis 均 healthy |
| GLM 与本地服务探测 | `.venv/bin/iqb doctor --probe-models` 退出 0；`glm-5.3-flashx` 结构化输出、Embedding、Tokenizer、Milvus、Redis、搜索端点检查均通过；GitHub Token 为 false |
| 当前模型显示 | `.venv/bin/iqb doctor` 明确显示 `"LLM model": "glm-5.3-flashx"`，Redis、Milvus 为 true；没有自动模型回退 |
| 真实数据库回归 | `IQB_SERVICES=1 .venv/bin/pytest -q -s tests/test_live.py::test_real_services_persistence_dedup_atomic_cache_and_expiry`：1 passed，模型切换后回归 17.44 秒 |
| 真实容器重启恢复 | 增加 `IQB_RESTART=1` 执行同一真实测试：1 passed，44.23 秒；实际重启本项目 Milvus 与 Redis 后，新客户端恢复原向量结果及 Redis AOF 中的探测值 |
| Tavily 搜索与正文 | 第二轮供应商测试：Tavily 通过，GitHub 失败；合计 1 passed、1 failed，11.44 秒 |
| GLM 单题与真实终端 | `IQB_SMOKE=1 IQB_EVIDENCE_DIR=data/acceptance/glm-smoke-03 .venv/bin/pytest -q -s tests/test_live_smoke.py`：1 passed，58.04 秒；包括 `/sessions`、`/new`、`/resume ID`、`/sources`、`/quit` |
| GLM 真实缓存链路 | `IQB_CACHE_LIVE=1 IQB_SMOKE_SOURCE_DIR=data/acceptance/glm-smoke-03/single IQB_EVIDENCE_DIR=data/acceptance/glm-cache-03 .venv/bin/pytest -q -s tests/test_live_cache.py`：1 passed，115.34 秒 |
| GLM 完整终端 E2E | `IQB_LIVE=1 IQB_EVIDENCE_DIR=data/acceptance/glm-e2e-05 .venv/bin/pytest -q -s tests/test_live.py::test_real_terminal_empty_to_online_local_context_hot_and_invalidation`：1 passed，1059.36 秒（17 分 39 秒） |

真实数据库回归使用真实 Redis、Milvus、本地 MPNet Embedding 和 Redis 官方 pipelining 网页正文，验证重复导入零新增 Embedding、新存储实例检索复用、12 次并发计数、固定窗口过期重置和版本隔离。它不调用 LLM，不等于真实题目生成 E2E。

首次安装遇到 uv 缓存权限与重复下载等待，已通过授权完成；本机采用相同锁定版本依赖的独立写时复制虚拟环境，并由 uv 校验、重新安装本项目及移除旧项目。项目模型缓存也已独立复制，不需要旁边项目在运行时存在。

## 当前模型

命令：

```bash
IQB_LIVE=1 IQB_EVIDENCE_DIR=data/acceptance/glm-e2e-01 \
  .venv/bin/pytest -q -s tests/test_live.py::test_real_terminal_empty_to_online_local_context_hot_and_invalidation
```

GLM 第一轮 **未通过，287.22 秒**。空库本地检查成功；真实联网正文六份成功保存，生成及依据核验调用均完成。两道题因无效证据 ID 被丢弃，实际只返回基础、进阶、高阶各一题，`sufficient=false`、`persisted=true`、`hot_count=0`，没有错误晋升缓存。

针对该失败，模型侧改用 `E1` 等短编号，程序严格映射回持久化 ID；未知编号仍会被拒绝。历史只保留理解追问需要的题目、答案、解释，不带旧证据 ID 与正文。提示词明确只需满足本次题量，不要求穷举未请求的子主题；未减少题量、难度、原文引用或充分性断言。

GLM 第二轮 **未通过，304.86 秒**。短编号避免了首轮的无效 ID，但一题包含当前正文里存在、该题却未引用的结论；核验后实际返回四题，仍为 `sufficient=false`，没有缓存。

GLM 第三轮 **未通过，302.07 秒**。新增一次修订后遇到核验模型自相矛盾：`sufficient=true`，通过题号却只有四个。最终确定性检查仍拒绝五题达标并返回四题，没有错误缓存，但此前修订触发条件未覆盖这种矛盾。现已要求充分标记、通过题号、题量、难度全部一致；增加回归确保矛盾会触发修订、失败修订不会替换掉更好的有据部分。

GLM 第四轮 **未通过，681.03 秒**。空库、联网入库后默认五题及 2/2/1 难度分配、新会话换措辞本地复用均通过；题号追问有据，但核验错误要求“解释模式”额外生成五道新题，因此标记不足。没有放宽断言；修正为解释模式只核验解释，模型请求不再携带不适用的题量、难度字段，并增加模式隔离回归。

最多修订一次、修订后再按相同标准核验，失败保留原先已核验的部分。当前提示词版本为 `interview-v4`，与旧缓存隔离；新证据清单记录模型和提示词版本。**第五轮完整 E2E 已通过**，前四轮真实失败记录保持不变。

### 追问回归

实际 CLI 用同一故障会话验证修复，退出 0，`mode=explain`、`target_number=2`、`sufficient=true`、`persisted=true`，7 条当前证据引用，`route=local`；没有外部采集：

```bash
DATA_DIR=data/acceptance/glm-e2e-04/e2e/knowledge \
INDEX_NAMESPACE=iqbe2e_4ab1a677 \
MODEL_CACHE_DIR="$PWD/data/models" LLM_MODEL=glm-5.3-flashx \
  .venv/bin/iqb chat -q "解释第二题" --session 27011eb8a777 --local-only --json
```

响应保存为 `glm-e2e-04/e2e/04-context-v4.stdout`，没有覆盖原先失败的 `04-context.stdout`。第五轮全新空库流程也已通过题号追问：`target_number=2`、充分、有 8 条有效引用。

### 单题与缓存

当前模型的修订版窄范围真实测试已通过，不替代完整五题 E2E。以下为脱敏摘要（`question_count` 为题量统计，原始响应的 `questions` 是数组）：

```json
{"route":"local","sufficient":true,"persisted":true,"hot_count":1,"question_count":1}
```

实际题目为 Redis Pipeline 与 RTT，难度为基础，标记“基于资料生成”，正文引用为 `https://redis.io/docs/latest/develop/using-commands/pipelining/`。每题证据 ID 在实际 JSON 中可与引用逐一对应。

接续相同问题的新会话请求：第二次 51.31 秒，第三次 50.14 秒，分别返回 `hot_count=2/3`；仅第三次晋升。第四次同时将 `LLM_API_KEY` 清空、Milvus 指向不可用地址，0.80 秒返回 `route=cache`、`hot_count=4`，题目与第三次一致。真实导入新来源后，知识版本变化；相同无模型密钥请求不再命中旧缓存，退出 1 并明确提示缺少模型密钥（0.83 秒）。

此前 `glm-smoke-01` 单题 1 passed、68.71 秒，但接续缓存测试 `glm-cache-01` 在第二次请求未通过（71.21 秒）：答案多写了未在该题引用的细节，被核验拒绝，没有增加热点计数。`interview-v3` 下 `glm-smoke-02` 与 `glm-cache-02` 分别 1 passed、110.41 秒及 120.79 秒；本次使用最新 `interview-v4` 全新重跑，没有复用旧版本缓存。全部失败与成功目录均保留，不用 Redis 测试替身替换真实模型结果。

## 外部服务

- **GitHub 正文验收未通过。** 两轮真实仓库搜索均得到结果，但每轮三个仓库正文读取均遇到 `api.github.com: rate_limited`。保留为失败，不替换成 mock。需要配置只读 `GITHUB_TOKEN` 后，在新的证据目录重新执行供应商测试。
- 真实网页中的 403、连接失败、浏览器回退失败和 GitHub 限流作为事件保留，不能当作知识不存在，也不用于放宽 E2E 断言。

## 历史记录

以下均在用户指定 GLM **之前**执行，不代表当前模型结果：

- **E2E 第一轮未通过。** 空库本地检查成功，随后联网抓取并保存三份 Redis 持久化正文，但模型结构化调用最终失败；测试在 510.94 秒后失败。已保留命令、输出与数据，未修改原记录。
- **E2E 第二轮未通过。** 长题单改为流式后，流程进入答案核验阶段，但当时仍为非流式的核验返回 `structured_output_failed:Verification:OpenAITimeoutError`；整个测试 711.02 秒失败，其中联网请求 691.39 秒。请求在发出中断命令前已自行失败，没有修改断言。
- **E2E 第三轮未通过。** 全部结构化阶段改为流式后，空库检查通过、三份正文成功入库，但默认五题生成触发 `structured_output_failed:Draft:TimeoutError`；测试 549.22 秒失败。180 秒处理预算在解析对象到达时检查，不能保证等待网络读取时立即硬中断。
- 单题及真实终端：`IQB_SMOKE=1 IQB_EVIDENCE_DIR=data/acceptance/smoke-01 .venv/bin/pytest -q -s tests/test_live_smoke.py`：1 passed，136.34 秒。真实官方正文、入库、单题本地生成及终端命令通过；这不是默认五题验收。
- 小型流式传输探测通过；仅验证结构化接入，不是题目质量证明。
- 针对超时，全部结构化阶段使用内部流式接收，结束后再做结构及依据校验，并限制核验说明长度、增加响应处理预算；错误包含脱敏的 schema 名和异常类型，不包含原始异常中的凭证或地址。

## 端到端验收

**最新完整 E2E 已通过：1 passed，1059.36 秒。** 各轮均采用全新数据目录和独立 Milvus 命名空间，没有用已有知识、mock 或弱化断言掩盖失败。当前 GLM 前四轮未全部达标，第五轮从空库贯穿全部步骤通过。

测试断言包括：空库不虚构题目、联网生成默认五题及 2/2/1 难度分配、来源已保存、新会话本地复用、第二题追问、第三次晋升和第四次缓存命中、模型密钥及 Milvus 不可用时仍可命中有效缓存、知识版本变化后缓存失效、真实终端会话命令。

| 步骤 | 实际结果 | 秒 |
| --- | --- | --- |
| 空库本地查询 | 无题目、不充分、未保存，不虚构答案 | 12.13 |
| 联网补充 | `online`，5 题、基础 2/进阶 2/高阶 1、充分、已保存 | 374.00 |
| 新会话换措辞复用 | `local`，充分、已保存，成功计数 1 | 248.96 |
| 解释第二题 | `local`，`target_number=2`、充分，8 条有效引用 | 130.05 |
| 第二次相同新会话请求 | `local`，成功计数 2，未晋升 | 91.85 |
| 第三次相同新会话请求 | `local`，成功计数 3，已晋升 | 102.03 |
| 模型密钥为空且 Milvus 地址不可用 | `cache`，成功计数 4，与第三次题目完全相同 | 0.84 |
| 导入新正文 | 真实官方 pipelining 正文入库、知识版本更新 | 7.39 |
| 更新后相同请求 | `local` 而不是旧缓存，重新检索生成，充分、已保存 | 90.54 |

最后真实终端的 `/sessions`、`/new`、`/resume ID`、`/sources`、`/quit` 均完成，进程退出 0。上述单步耗时之和不含测试初始化、断言及终端操作，总耗时以 pytest 为准。答案、每题引用 ID、原文、来源 URL、处理路径、会话与事件均保留在证据目录。

真实单题缓存链路是独立验收；完整五题流程也单独通过，不用单题或离线测试代替完整 E2E。GitHub 供应商正文测试仍因限流失败，不因总体流程可使用 Tavily 而改判通过。

## 证据位置

- GitHub 搜索及失败：`data/acceptance/providers-02/github/`（另保留第一轮）。
- Tavily 实际搜索及正文：`data/acceptance/providers-02/tavily/`（另保留第一轮）。
- 当前 GLM 首轮失败：`data/acceptance/glm-e2e-01/e2e/`，环境清单包含 `LLM_MODEL`。
- GLM 短编号修复后重跑：`data/acceptance/glm-e2e-02/e2e/`。
- GLM 核验矛盾复现：`data/acceptance/glm-e2e-03/e2e/`。
- GLM 核验一致性修复后：`data/acceptance/glm-e2e-04/e2e/`。
- GLM 解释模式修复后：`data/acceptance/glm-e2e-05/e2e/`。
- GLM 单题与真实终端：`data/acceptance/glm-smoke-03/single/`（另保留前两轮）。
- GLM 热点链路：`data/acceptance/glm-cache-03/single-cache/`（另保留前两轮）。
- 第一轮失败 E2E：`data/acceptance/e2e-01/e2e/`。
- 第二轮隔离 E2E：`data/acceptance/e2e-02/e2e/`。
- 第三轮隔离 E2E：`data/acceptance/e2e-03/e2e/`。
- 单题与真实终端探测：`data/acceptance/smoke-01/single/`。

E2E 目录包含逐条命令、退出码、耗时、stdout/stderr、SQLite 元数据与正文快照；成功完成时还会写入会话操作记录和来源事件。数据库测试的临时集合及缓存键自动清理；E2E 知识数据保留供复核。旧版首次数据库测试因错误使用临时模型目录而触发重新下载，已中止并修正；后续真实数据库测试两次均通过。

## 边界

离线分支测试与真实模型/服务验收分别列出。没有把第三方题库当作公司真题，没有预置未知来源题目，没有验证私有仓库、Web 界面、模拟面试评分或生产级多用户并发。模型依据核验不等于人工事实认证，实际使用仍需阅读引用原文。
