# 指定面试题库导入记录

日期：2026-11-05。使用当前项目正式数据目录 `data/`，不是验收隔离库。

## 实际结果

| 来源 | 本次实际导入 | 向量片段 | 覆盖边界 |
| --- | --- | ---: | --- |
| AgentAlpha | 60 个题目详情页 | 129 | 目录发现 573 页；保留前 10 个基础题并跨目录均匀抽样，另 513 页未导入 |
| GitHub `bcefghj/ai-agent-interview-guide` | 11 份 Markdown 和 1 份 LICENSE | 876 | 九个面试八股文模块、模块目录和项目面试问答集；不包含项目代码等其他目录 |
| 面灵AI | 用户指定的 AI Agent 面试题单正文 | 20 | 题单原文及其中公开参考回答；不是每道题都有答案 |
| 拿个offer | 公开 Top50 题目表 | 9 | 页面实际有 50 行题目；含分类、难度和公司标签，不含配套答案 |
| 卡码笔记 | 指定目录中的全部 34 篇详情文章 | 635 | 当前目录可见链接，不代表全站或未来新增内容 |
| 合计 | 97 条来源记录，108 份页面或文档 | 1,669 | 包括目录文档与许可证，不能换算为 1,669 道题 |

Milvus：`http://127.0.0.1:19532`，集合 `iqb_ba808bb5d0180b56`，768 维。
SQLite 活动清单知识版本：`97`。正文快照：`data/snapshots/`。

GitHub 固定 commit：`9a987322f2d82ddabe4c1aabc7b4795749fa90a2`，每个 chunk 保留文件路径、行号和 commit 引用。
网站资料默认一天有效，仓库资料七天有效，过期后需要刷新。

这次仅采集原始证据、向量化和验证检索，没有调用聊天模型，也没有生成结构化题目或晋升 Redis 热点。模型配置仍为 `glm-5.3-flashx`。

## 访问异常

- GitHub 公共 API 真实返回 `rate_limited`，未将其记为成功。普通公开仓库页面和原始文件可读取，因此从页面结构化元数据取得 commit，再读取固定 commit 的原始 Markdown；不调用被限流的树目录接口，不绕过验证码或登录。
- 拿个offer首轮返回的 HTML 正文夹杂空字节，通用读取器报告 `network: binary_content`，当轮未入库。补录脚本仅对明确标为 `text/html` 的响应，按 HTML 解析规则替换空字符，并记录本次替换 7 处；其他二进制 MIME 仍拒绝。随后成功入库。其 `robots.txt` 返回的是普通 HTML 首页，并非有效 robots 文件，报告保留此事实。
- 其余三个网站在本次普通公网请求中可读取，没有观察到需要解决的验证码、登录或访问挑战。没有使用代理轮换、验证码处理或搜索摘要代替正文。
- GitHub LICENSE 标为 MIT，但 README 另有学习资料非商用说明，两者都保留在来源信息中；公开可读不代表已取得商用授权。网页许可标记为 unknown。
- 网站“公司真题”标签仅为来源方说法，没有独立核实；所有资料保持 `unverified`。

## 验证

离线测试和静态检查：

```bash
.venv/bin/pytest -q
# 104 passed, 6 skipped in 2.35s
.venv/bin/ruff check src tests scripts
# All checks passed!
```

新增测试覆盖详情链接范围、编码路径穿越、跨目录抽样、Markdown 链接解析、题目标题保留、访问挑战拒绝及 HTML 空字符修复边界。六项跳过的测试不是这次真实验证结果。

实际数据库和检索验证，不使用 mock：

- 97 份活动来源快照的内容哈希全部一致，共 108 份页面或文档。
- 逐 ID 从真实 Milvus 读取全部 1,669 个活动向量，验证存在、维度为 768 且数值有限。
- 对五个来源分别用真实 MPNet query embedding 做受来源范围约束的稠密检索，各返回 3 个真实片段。
- 三组普通混合检索均返回 5 个片段：RAG/BM25/RRF、Agent 长短期记忆、ReAct 工具调用与终止条件。
- RRF 查询首条命中 GitHub 项目问答集 `Q9: RRF 融合排序的公式是什么？权重怎么调？`，引用固定 commit 的 `README.md#L1036-L1066`。其余查询命中记忆分类、ReAct 终止条件及 Loop Engineering 正文。
- 重复导入 AgentAlpha `what-is-agent` 快照，`chunks=2, embedded=0, version=97`，活动片段总数和知识版本均不变。
- `iqb doctor` 确认 Milvus、Redis 健康，模型为 `glm-5.3-flashx`。

以上验证证明真实采集、持久化和检索，不代表本次已重新执行聊天模型问答 E2E，也不保证来源技术观点完全正确。检索中仍可能出现目录片段，Agent 应继续做答案依据核验。

## 命令与证据

```bash
cd /Users/sunyi/ai/Agent/Javis/Interview-Question-Bank
.venv/bin/python scripts/import_interview_sources.py \
  --report data/imports/provided-sources-20261105.json
.venv/bin/python scripts/import_interview_sources.py --only nageoffer \
  --report data/imports/provided-sources-nageoffer-20261105.json
.venv/bin/iqb doctor
.venv/bin/iqb sources --json
```

首轮脚本约 783 秒；失败页面没有激活，第二轮单独补录。数据库验证通过一次性 Python 调用 `Store`、`Knowledge` 和 `MilvusClient.get` 完成。

保留的机器可读证据：

- `data/imports/provided-sources-20261105.json`：逐页结果、GitHub API 限流、拿个offer首轮失败、未选择的 513 个详情链接。
- `data/imports/provided-sources-nageoffer-20261105.json`：HTML 格式修复及成功入库结果。
- `data/imports/provided-sources-verification-20261105.json`：集合、维度、逐 ID 校验数量、检索原文和引用、重复入库结果。
- `data/imports/provided-sources-manifest-20261105.json`：`iqb sources --json` 的真实输出，97 条来源状态均为 `ok`。

可直接用当前库提问：

```bash
.venv/bin/iqb chat -q "给我 5 道 Agent 和 RAG 相关面试题" --local-only --json
```

`--local-only` 禁止外部采集，但问答仍调用配置的远程 GLM。扩大 AgentAlpha 覆盖范围时可显式调整 `--alpha-limit`；每次指定新的报告文件，并保持单写入进程。
