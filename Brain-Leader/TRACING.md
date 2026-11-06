# LangSmith 集成与调用链分析

日期：2026-11-06。结论：**代码接入、离线隐私/失败验证及真实本地链路已完成；
LangSmith 云端接收与控制台可见性未验收**，当前未配置专用追踪 Key。
不以 SDK 合约测试、本地文件或真实 LLM 成功代替云端验收。

## 1. 接入边界

- `BL_TRACE_MODE=off|local|langsmith`，默认关闭；`--trace-local` 强制只落本地。
- `langsmith` 另需专用 `BL_LANGSMITH_API_KEY` 和用户 `--allow-trace-upload` /
  交互确认。LLM 处理同意不包括遥测，模型工具不能启用上传。
- 保持 LangSmith 自动正文追踪关闭。自定义 LangChain 回调只记录固定图节点、
  工具和模型事件；适配器实例埋点补充助手、检索、缓存、Embedding、向量查询和 Reranker。
- 用 `copy_context()` 把父子关系带入原有知识线程，不更改 SQLite 的线程所有权。
- 只上传随机请求/节点 UUID、时间、静态名称、状态、数量、缓存命中及已返回 Token 用量。
  `inputs/outputs` 为空，原始异常、提示词、文件路径、简历、作答及参考答案均不上传。
  未采集业务会话 ID，不提供跨请求的完整对话回放。
- 使用独立 LangSmith SDK Client 向 `/runs/batch` 发送有界批次，禁用自动环境采集、
  全局工作区继承、重定向和自动重试；不修改全局环境或三个助手的独立入口。
- 单请求最多 512 个 span，队列最多 8 个请求，连接/读取超时各 2 秒，
  关闭最多等待 3 秒。失败不回滚、不重跑业务，可能尚有待发送的 best-effort 元数据。
- `queued` 只表示排队；`sent_unverified` 只表示 HTTP 接收，不表示项目中已可见。
  本地 JSON 保存最终发送状态，可用 `bl trace-report 路径` 核对。

安装、环境变量、上传授权与复现命令见 [README.md](README.md#langsmith-调用链)。
本地链路是墙钟时间而非 CPU profile，节点包含子节点，**不能跨行求和**。
流式解析器的非业务内部节点不发布，内部模型 HTTP 重试也不等于新的模型 span。

## 2. 真实链路

模型：现有配置的 `glm-5.3-flashx`。使用真实 LLM、Redis、Milvus 和本地推理模型；
资料使用独立 SQLite 副本与新命名空间，输入仅为技术问题、已有合成简历。
`local_only` 禁止在线补充资料，不表示 LLM 离线。

| 场景 | 根请求耗时 | LLM 调用 | LLM 合计 | 检索 | Reranker | Token 合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 主控 → 知识助手 | 44.673 s | 4 | 28.900 s | 15.276 s | 11.268 s | 22,173 |
| `/questions` → 题库 | 78.500 s | 4 | 43.535 s | 34.447 s | 24.882 s | 17,470 |
| 合成简历 → Job-Agent 解析 | 16.316 s | 1 | 15.829 s | 不涉及 | 不涉及 | 3,066 |
| 主控 → 题库（精简节点后的验证） | 91.595 s | 6 | 58.128 s | 32.695 s | 23.204 s | 37,038 |

四次业务均返回 `ok`，15 次 LLM 调用均有 Token 用量，未丢弃 span。
前两条检索、最后一条检索的答案缓存均未命中；不是用完整缓存答案替代检索测试。
这不是严格 A/B：输入路径、模型输出及节点版本不同，不据此宣称性能提升。

最后一条请求的实际父子结构如下；同轮还执行了一次约 8 ms 的 `current_state`：

```text
chat                                                   91.595 s
  LangGraph（主控）
    model → llm                                         7.140 s
    tools → generate_questions → tool
      assistant.questions                              75.025 s
        LangGraph（题库）
          cache → answer_cache                         未命中
          parse → models.parse → llm                    5.065 s
          retrieve → retrieval                         32.695 s
            embedding（两次，含首次加载）                5.980 s
            vector_search                               0.126 s
            reranker                                   23.204 s
          assess → models.assess → llm                 10.196 s
          generate
            models.draft → llm                         14.767 s
            models.verify → llm                        12.031 s
          save                                          0.031 s
    model → llm                                         8.929 s
```

### 分析结论

1. **完整请求主要耗在远程模型调用。** 最后样本约 58.13 / 91.59 秒，即 63%；
   主控两次模型调用约 16.07 秒。异步 HTTP 或 FastAPI 本身不能缩短这些串行依赖。
2. **检索内部以 Reranker 为主。** 最后样本约 23.20 / 32.69 秒，即 71%；
   向量查询仅约 126 ms，不支持“当前瓶颈在 Milvus 网络”的判断。
3. **冷初始化不能忽略。** 最后样本两次 Embedding 共约 5.98 秒，
   其中第一次约 5.95 秒，包含首次模型加载，不应全部称为稳态编码耗时。
4. 题库除生成外还执行独立依据核验；不能只为少一次调用而直接删除核验。
   重复需求可复用原有经验证缓存，新的需求再分别测量模型、候选量和重排成本。
5. 下一轮优化应以同输入、同覆盖要求、冷热分开采样，优先评估重排成本、
   缓存命中和主控不必要轮次；批大小变化仍须验证 INT8 分数/排序。
   现有严格性能结果和局限见 [PERFORMANCE.md](PERFORMANCE.md)。

单个请求不能产生可靠 QPS/P99、成本估算或容量结论。本轮没有重新执行负载测试，
没有增加 FastAPI，也没有改变原推理批大小或题目核验策略。
受限运行环境出现 ONNX/macOS 本机缓存告警，但本轮实际业务和检查均成功；
这些冷机样本不能用作稳定延迟承诺。

## 3. 验收分层

| 层次 | 结果与限制 |
| --- | --- |
| Brain-Leader 普通测试 | 91 passed，7 live deselected，含 19 个新增追踪用例 |
| Knowledge-Assistant 普通测试 | 最终 122 passed、2 failed，7 live deselected；见下方 seed 变更说明 |
| Interview-Question-Bank 普通测试 | 142 passed，6 live deselected |
| Job-Agent 普通测试 | 105 passed，5 skipped，6 live deselected |
| SDK 传输/隐私合约 | 使用真实 SDK、替换网络传输；验证白名单 body、专用 Key/工作区、无重定向、401/429/500/302 不冒充发送成功 |
| 真实 CLI / LLM / 知识服务 | 上表三个 CLI 场景通过；精简节点后另通过主控→题库链路 |
| 真实 PTY | `bl chat --trace-local --allow-remote` → `/status` → 显示 trace → `/quit`，通过；未调用模型 |
| 静态检查 | Ruff 与 `git diff --check` 通过 |
| LangSmith 云端 | **未执行：缺少专用 Key**；尚不能确认租户、项目、接收和控制台可见性 |
| 网站、文档导出及原完整职业闭环 | 本轮未重验；不改变原独立验收结论 |

最终普通测试合计 **460 passed、2 failed、5 skipped**，26 个 live 测试未执行。
“跳过”不计为通过，SDK 替换网络测试不计为真实云端验收。

最初一轮知识助手为 124 passed；最终回归时，工作区中并行出现了
`agents/Knowledge-Assistant/seeds.json` 从 13 条扩到 16 条的改动，
而以下两项测试仍硬编码 13，分别实际得到 16：

- `tests/test_cache_config_cli.py::test_cli_seed_list_and_help_do_not_require_keys`
- `tests/test_github_browser.py::test_new_catalog_has_thirteen_repos_and_scoped_large_readme_exception`

seed、相关采集脚本及根目录 `docs/` 的并行改动不属于本次追踪实现，未撤销、未改写；
也未修改上述断言或将失败作为通过。当前不能宣称整个工作区普通测试全部通过。

本次隔离运行与清理检查时，正式知识库从测试前到清理后均保持：

- Knowledge：`corpus_id=013c9a8a0aff4b5a947d828a819eaca4`，版本 `16`。
- Questions：`corpus_id=9351b108a54f4f10acdbbe491a82bc9b`，版本 `643`。

本轮约 156 MiB 隔离副本、临时职业状态、两个自建 Milvus 测试集合及对应测试缓存已清理，
不清理正式库、其他测试命名空间、用户原始简历或共享模型缓存。
以下五份约 81 KiB 的 metadata-only JSON 原保存在被 Git 忽略的 `data/traces/`。
2026-11-06 再次按用户要求清理测试产物时已删除；下面的 ID 仅为历史运行标识，
不是当前可读取的文件或 LangSmith 云端链接。复跑依赖测试与脚本，不依赖这些旧输出。

| 场景 | trace_id |
| --- | --- |
| 主控 → 知识助手 | `27ab9715-0b4b-477c-8572-058edf399793` |
| 题库 CLI | `8a0239c2-4ad7-4411-b774-aaa59174f54a` |
| 合成简历解析 | `23512abf-a822-4370-8c57-5da46290bc54` |
| 主控 → 题库 | `67c6d87c-aa92-4d7f-93f1-a4d4cf271631` |
| PTY `/status` | `650b13c1-7996-4b1f-8a4a-8b6e6c5859ee` |

原临时运行目录及上述本地输出均已删除，重新测试应执行
`scripts/trace_smoke.py` 创建新目录，不复用已删除的数据库或命名空间。
