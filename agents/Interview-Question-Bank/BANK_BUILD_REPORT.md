# AI Agent 题库扩充与验收

日期：2026-11-05。模型：`glm-5.3-flashx`。

## 状态

已完成 1,000 道题的整理、真实入库与验收，18 个方向均达到配额，Harness Engineering 为 100 道，热点方向为 765 道。

- [完整题库 Markdown](data/bank/ai-agent-1000-harness-20261105/questions.md)
- [结构化 JSONL](data/bank/ai-agent-1000-harness-20261105/questions.jsonl)
- [构建记录](data/bank/ai-agent-1000-harness-20261105/report.json)
- [真实验收结果](data/bank/ai-agent-1000-harness-20261105/validation.json)
- [18 个方向的真实检索样例](data/bank/ai-agent-1000-harness-20261105/retrieval-examples.json)

统计口径：804 道标记为来源原题，196 道为基于资料生成；按处理方式统计，769 道为原文提取，231 道为 GLM 整理并核验。难度为基础 273、进阶 574、高阶 153。全部题目都有参考答案、要点和有效原始引用。

## 资料扩充

- 来源记录由 97 增至 611，正文文档共 631 份，活动正文向量切片由 1,669 增至 3,067。
- AgentAlpha 当前发现的 573 篇详情全部导入，卡码笔记共 35 篇，面灵 AI 保留指定文章。
- GitHub 固定 commit `9a987322f2d82ddabe4c1aabc7b4795749fa90a2`，导入 21 份 Markdown/许可证文件。公共 API 真实限流一次，随后通过公开页面与固定 commit 原始文件完成导入；并非完整仓库索引。
- 拿个offer只有题目目录，不将其当作答案证据。不绕过登录、验证码或访问挑战，不执行仓库代码。
- 本次导入记录含 515 次保存、96 次跳过已有来源、1 次 GitHub API 限流。保存包括 GitHub 来源更新，净新增来源为 514。
- [逐页导入记录](data/imports/expanded-sources-20261105.json)保存实际结果；原文保留在 `data/snapshots`。

## 技术栈分配

目标共 1,000 道，覆盖 18 个方向。Harness Engineering 单列 100 道，占 10%；热点方向共 765 道，占 76.5%。这是人工配置的技术侧重，不是实时热度或招聘统计。

完整配额见 [课程配置](config/ai_agent_curriculum.json) 与 [README](README.md)。Harness 覆盖职责边界、执行循环与预算、上下文与反馈、沙箱与权限、恢复与回放、验收回归及可观测性。

## 整理与依据

优先从已保存正文提取完整原题和答案，按内容分类和配额选取；剩余缺口经真实混合检索、RRF 和本地 BGE Reranker 获取正文，由 GLM 生成并单独核验。只有单题核验支持、引用 ID 有效、原题引用逐字匹配的结果才能入库。

`review=source_exact` 表示答案从来源逐字提取，未逐题经过模型事实审查；`review=llm_verified` 表示模型整理并通过独立核验调用。`origin` 另行区分来源原题和基于资料生成。模型生成和核验均使用同一 GLM，仍存在共同偏差；第三方公司标签未独立核实。

标题经过 NFKC、大小写与标点规范化，并用真实 MPNet 向量拒绝余弦相似度达到 0.93 的近似题。这不能保证排除所有语义重叠。分类和难度也是自动标签。

题目先写入并读取校验独立 Milvus 集合，再用 SQLite 事务发布。原始正文集合仍有 3,067 个活动片段；整理题目不作为独立事实证据反哺 RAG。

## 验收记录

- 离线：`133 passed, 6 skipped`；六个真实服务测试为显式可选项，本轮离线命令没有执行它们。
- 静态检查：`ruff check --no-cache src tests scripts` 通过。
- 真实全库验收：36/36 通过。逐一从 Milvus 读回全部 1,000 条 768 维向量，与 SQLite 核对；每题引用有效，原文提取答案逐字存在于正文快照中；JSONL 全字段与数据库一致，Markdown 恰有 1,000 个题目标题。
- 18 个方向各检索 3 题，共 54 个真实结果；另外通过新终端进程执行 Harness 检索，退出码为 0。生成题 ID 没有混入原始事实索引。
- 全库规范化标题唯一，标题向量两两最大相似度为 `0.929254699554554`，低于 `0.93` 阈值。
- 完成后以 `--rounds 0` 重跑构建，仍为 1,000 道、66 个已处理批次，没有重新生成题目。
- 首次真实验收为 35/36：两个多行回答要点带有原文二级标题，Markdown 被识别为 1,002 个题目标题。修复要点层级与缩进，补充回归测试后重跑相同断言，通过 36/36。[首次失败记录](data/bank/ai-agent-1000-harness-20261105/validation-initial.json)保留。
- 构建记录保留 7 次结构化输出失败、早期两次 `ValueError` 和续跑信息；早期异常没有保留具体消息，不能据此断言全部是同一种原因。失败及未通过核验的结果不计数。66 是已处理批次数，不是包括中断请求在内的供应商调用总数。
- [加入 Harness 独立配额前的记录](data/bank/ai-agent-1000-harness-20261105/initial-curriculum-report.json)一并保留在最终目录，没有保留旧测试库或重复题库导出。
- 本轮未逐题执行 RAGAs，不把此前三个小样本的指标当作这 1,000 道题的整体质量评分；原评估入口及历史结果仍保留。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/import_interview_sources.py \
  --alpha-limit 600 --skip-existing \
  --report data/imports/expanded-sources-20261105.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/iqb bank build --target 1000 --workers 4 \
  --output data/bank/ai-agent-1000-harness-20261105
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests scripts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_question_bank.py \
  --output data/bank/ai-agent-1000-harness-20261105
.venv/bin/iqb bank search -q "Harness 执行循环与失败恢复" --topic harness --limit 3
```
