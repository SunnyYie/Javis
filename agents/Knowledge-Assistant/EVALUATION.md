# 混合检索、Reranker 与 RAGAs 真实评估

日期：2026-11-05，Asia/Shanghai。

本文保留首轮 32 候选策略的历史基线；后续优化与新配置结果见 `OPTIMIZATION.md`。
按用户要求清理测试产物后，本文所述旧轮次原始文件已删除，仅保留文档中的历史结论。
2026-11-06 进一步清理了剩余的最终评分、答案及环境原始输出；
仅保留题集、执行代码和本文指标摘要。清理范围见 `data/acceptance/README.md`，
不要将旧路径视为当前可用证据。

## 配置与范围

- 问答与裁判均为真实远程 **`glm-5.3-flashx`**，没有模型回退。
- 本地 Embedding：`sentence-transformers/paraphrase-multilingual-mpnet-base-v2`。
- 本地 Cross-Encoder：`BAAI/bge-reranker-v2-m3-int8`，CPU / ONNX。
- 两路召回各最多 20，RRF 候选最多 32，最终最多 8；跨仓库按仓库平衡预算。
- RAGAs 0.4.3，现代 metrics collections / Instructor 接口，
  兼容依赖 `langchain-community 0.3.31`，依赖已锁定。
- 生产知识库版本 16：13 个仓库的有限采集加 3 个网页，共 2059 个活动片段。
  本轮评估未修改知识库，不代表 13 个仓库的完整源码覆盖。
- 人工固定集 `eval/questions.json`：6 题，中文、英文、代码用法、跨项目比较；
  两组共 12 份真实新鲜回答。参考与支持标签仅在生成完成后附加，不参与检索、生成或入库。
- 生成阶段绕过缓存，禁用外部检索，但仍使用远程 LLM，不能称为全离线。
  评分使用全部实际生成上下文，不只使用回答最终引用的片段。

## 执行与保留失败

```bash
uv sync --extra test --extra eval
.venv/bin/ka evaluate --compare \
  --output data/acceptance/2026-11-05-ragas-ab

# 同一批回答，仅重新评分，原始失败目录不覆盖
.venv/bin/ka evaluate \
  --answers data/acceptance/2026-11-05-ragas-ab/answers.jsonl \
  --output data/acceptance/2026-11-05-ragas-ab-rescore
```

首轮使用 4096 token 裁判输出预算：12 份回答全部生成，41/48 项评分成功，
7 项忠实度评分抛出 `IncompleteOutputException`，命令退出 1，`complete=false`。
Instructor 此异常对应输出长度上限截断，不将残缺 JSON 解析成成功评分。
原始失败报告与逐项事件完整保留。

将裁判预算改为 `EVAL_MAX_TOKENS=16384` 后，对**原样保存的同一批答案**重新执行全部四项指标；
没有更换问题、参考、上下文、回答或评价标准，没有跳过困难题。
复评 **48/48 项有效，0 项失败，退出 0，complete=true**。
两目录的 `answers.jsonl` 内容一致，复评另保存 answers digest。
`complete` 表示执行完整，不是质量达标标志。

## 最终结果

以下均取自复评 `report.json`，每项每组均为 **6/6 有效**，均值按题等权。

| 指标 | 混合检索 | 混合检索 + Reranker | 差值 |
| --- | ---: | ---: | ---: |
| Faithfulness / 忠实度 | 0.9735 | 0.9928 | +0.0192 |
| Answer Relevancy / 回答相关性 | 0.6820 | 0.6878 | +0.0057 |
| Context Precision / 上下文平均精确率 | 0.7505 | 0.8412 | +0.0907 |
| Context Recall / 上下文召回率 | 0.7667 | 0.7389 | -0.0278 |
| 原文字面支持标签召回 | 0.6667 | 0.5833 | -0.0833 |
| 首个支持标签 MRR | 0.4861 | 0.5889 | +0.1028 |
| 助手判断证据充分 | 4/6 | 4/6 | 0 |
| 每题生成总耗时均值（秒） | 31.06 | 50.77 | +19.71 |
| 每题检索阶段耗时均值（秒） | 0.39 | 13.56 | +13.17 |

检索阶段耗时包括向量查询、BM25、融合及启用时的本地重排，不是纯 Cross-Encoder 推理时间。
生成耗时还受模型响应与充分性判断等因素影响，不能把全部差值归因于重排。
本次没有事先约定质量阈值，因此不将这张表标为“质量全部通过”。

## 漏召回与人工检查

| 题目 | 两组充分性 | 实际观察 |
| --- | --- | --- |
| awesome harness 清单与设计原语 | 充分 | 正确回答按问题而非厂商组织；严格字面标签各命中 1/2，另有语义支持 |
| RAGFlow 部署及开发语言版本 | 不足 | 找到定位，但具体版本要求所在片段未进入两路召回 |
| awesome 与 RAGFlow 项目对比 | 充分 | 区分汇编和引擎，区分 third_party / project，并保留未覆盖范围提示 |
| LangChain create_agent 与 harness | 充分 | 重排将关键原文的首个支持标签从第 6 位提至第 1 位 |
| FastMCP Context 与请求 ID 代码 | 不足 | 请求 ID 原文被一路召回，但落在 RRF 前 32 候选之外，无法交给重排 |
| DeepSeek Harness 英文架构 / 稳定性 | 充分 | 两组都答出 Cordis、插件架构及 Developer Preview / breaking changes |

RAGFlow 与 FastMCP 的不足答案没有被剔除。两题在库中有目标原文，不是目标知识根本未采集；
失败分别发生在召回、融合候选阶段，不能仅靠重排修复。
禁用外部检索时明确承认证据不足；真实联网补充与复用由独立 E2E 验证，而非本固定集自动补料。

DeepSeek 重排回答改用中文 README 支持稳定性结论，而原文字面标签限定英文 `README.md`，
所以严格支持标签召回从 1 降到 0.5；这是该辅助标注的语言 / 路径局限，
不能直接等同于语义事实缺失。没有为提高结果临时修改标签。
高忠实度也可能来自谨慎拒答，不能替代问题覆盖率；FastMCP 两组回答相关性均为 0。

另在真实网页 E2E 的首答中发现 **Model / Harness 组成边界混淆**：
引用原文明确是 Agent = Model + Harness，而回答一处又将 Model 列为 Harness 组成。
该事实瑕疵单独保留在 E2E 的 `REVIEW.md`，不能用上述平均分掩盖。

结论：本小集上证据排序、平均精确率改善，回答相关性提升很小，召回有下降且仍有缺口，
本地 CPU 重排增加延迟。不能宣称整体检索质量、答案正确率或全部源码问题均通过。
后续应使用独立保留集验证候选覆盖、查询改写与多语言去重，不应只针对这 6 题调参。

## 真实 E2E 与回归

- 完整真实终端网页问答闭环：**1 passed，367.64 秒**，包含缓存、会话追问、
  联网取正文、入库、新会话 local-only 复用、缓存版本失效及真实 PTY。
- 真实本地 Cross-Encoder 探测及 Embedding / Milvus / Redis 集成：
  **2 passed，14.17 秒**。合成集成输入不计作真实项目知识问答。
- 普通回归：**115 passed，7 skipped，2.11 秒**；Ruff lint / format、`uv lock --check` 通过。
- 实际问答与评估使用配置中的真实服务，不用 mock 替代真实失败。

## 复查入口

- `data/acceptance/2026-11-05-ragas-ab/dataset.json`：运行时冻结题集。
- 同目录 `answers.jsonl`：问题、全部实际生成上下文、回答、引用、检索候选 / 重排分数及耗时。
- 同目录 `report.json` / `metric-events.jsonl`：首次未完成评分，原样保留。
- `data/acceptance/2026-11-05-ragas-ab-rescore/report.json`：最终完整评分及有效分母。
- 复评目录 `scores.jsonl` / `metric-events.jsonl`：逐题、逐项真实结果和耗时。
- `data/acceptance/2026-11-05-rerank-web-e2e/REVIEW.md`：实际终端 E2E 核对与事实瑕疵。

这是单次、小样本、同模型生成与评判的回归评估。即使裁判 temperature=0，
远程推理仍存在波动，首轮与复评的部分非失败指标也有变化；两组答案生成本身也有随机性。
未做多轮重复或显著性检验，未逐句审核所有回答，未执行代码示例，
不证明提示注入防御的完备性，也不将裁判得分解释为事实正确概率。
