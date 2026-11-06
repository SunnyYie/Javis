# 性能分析与隔离压测

这些工具不是生产 Web API。Locust 通过仅绑定 `127.0.0.1` 的临时 HTTP 适配器，
调用真实助手的片段读取或检索方法；所有请求仍排入同一工作线程。
请求耗时包含本机 HTTP 和串行排队，不代表多用户服务已具备支持能力。

## 安装与隔离

在 `Brain-Leader` 目录执行：

```bash
uv sync --extra test --extra perf --locked
uv run python -m perf.benchmark prepare data/performance/选择一个新目录
```

prepare 从现有语料建立独立 SQLite 副本和 Milvus 命名空间，不调用 LLM，
不把个人简历或作答写入共享语料。文件可能较大，运行目录受 Git 忽略。
避免与独立助手的写入操作同时运行。每轮 `measure` 会检查正式库身份和版本；
此检查不能证明所有表的每个字段都未变化。

## Profiling

```bash
uv run python -m perf.benchmark profile data/performance/运行目录 \
  --phase baseline --kind knowledge
uv run python -m perf.benchmark profile data/performance/运行目录 \
  --phase baseline --kind questions
```

输出独立线程上下文中的 `retrieval.prof`、`snapshot.prof` 和可读统计。
`profile` 在调用线程直接执行真实检索，不运行 HTTP 服务，避免 Python 3.12
多线程 profiling 将事件循环等待混入调用树。`measure` 内嵌的 `warm.prof`
只作辅助诊断，不用于精确推断各函数的 CPU 占比。
初始化单独计时，复用磁盘模型缓存，不称为全冷启动。

需要采样时，可在测试后端运行期间，使用其 `ready.json` 中的 PID：

```bash
uv run py-spy record --pid 测试进程PID --duration 30 \
  --format speedscope --output data/performance/运行目录/采样.json
```

macOS 通常要求 root；没有授权时记录失败，不修改 SIP，不采样其他应用，
也不将 cProfile 结果冒充 py-spy 结果。

## Locust 对照

先在优化前执行，修改后用同一份隔离语料和不同 phase 重复：

```bash
uv run python -m perf.benchmark measure data/performance/运行目录 \
  --phase baseline --kind knowledge --duration 60 --users 1 4
uv run python -m perf.benchmark measure data/performance/运行目录 \
  --phase optimized --kind knowledge --duration 60 --users 1 4 \
  --operations snapshot retrieve retrieve_uncached
```

另一助手将 kind 改为 `questions`。不要同时跑两种 kind，以免相互争抢推理资源。
不要复用已存在的 phase/kind 输出目录；失败产物保留，不覆盖为成功。

- `snapshot`：真实 SQLite 活跃片段读取，不执行模型推理。
- `retrieve`：固定五个技术主题重复检索，允许本地精确推理缓存；不使用完整回答缓存。
- `retrieve_uncached`：每次清空该测试实例的查询向量/重排缓存后，执行同一真实检索，
  不改变查询、候选数、模型或评分规则。不能将此结果与热缓存结果混称为“模型加速”。

输出 Locust CSV 和精确请求样本汇总 JSON：完成 QPS、成功 QPS、失败数、P50/P95/P99、
样本数及缓存命中数。QPS 使用从 test_start 到 test_stop 的实际时间，包含结束时排空
在途请求；分位数使用成功请求的 nearest-rank，失败另计，不将超时计为成功。
少于 1000 个成功样本时标记 P99 样本不足，不作为 SLA 或容量保证。
压测为闭环、零思考时间，不模拟真实用户到达分布，可能存在 coordinated omission。

## 批处理实验与端到端抽查

```bash
uv run python -m perf.benchmark tune data/performance/运行目录 --kind knowledge
uv run python -m perf.benchmark smoke data/performance/运行目录 --phase baseline
```

tune 比较实际 Embedding/Reranker 批大小、长度分组、分数偏移及排序是否一致；
不能仅看吞吐量决定上线，INT8 模型可能对批组成敏感。
smoke 会执行一次真实主控模型请求，可能收费；只删除当前隔离知识助手中该问题的
精确回答缓存键，不清空 Redis。它不是压测，不从单个样本计算 QPS 或 P99。

修改后应回归主应用与三个助手普通测试，并核对相同输入的检索 ID、顺序和来源。
正式库版本变化、来源不足、模型错误、无权限采样等结果必须如实报告。
本轮结果与范围见项目 `PERFORMANCE.md`，不以局部热缓存收益宣称全部 Agent 加速。

完成两种 kind 的 `baseline`、`optimized` measure/profile 后，可以离线汇总：

```bash
uv run python -m perf.compare data/performance/运行目录
```

生成 `comparison.json` 并核对五主题及直接 profiling 的检索 ID/顺序、片段数量、
正式库身份/版本和请求失败数；任何检查失败返回非零，不重跑或覆盖失败原始数据。
