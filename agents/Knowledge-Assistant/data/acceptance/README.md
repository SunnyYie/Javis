# 测试用例与产物清理

2026-11-06 按用户要求仅保留可复跑测试、必要题集和执行脚本。
本目录中的 12 个历史运行目录、58 个生成文件已删除：
包括问答输出、检索轨迹、搜索盘点、入库回执、RAGAs 评分及终端日志。
项目自身的 Python 字节码缓存也已清理；本目录目前只保留此说明。
这些输出不是测试输入，不从历史回答中制作通过断言。

## 保留的测试材料

- `../../tests/`：单元、集成、真实 E2E 测试、内置夹具及 `acceptance_questions.json`。
- `../../eval/questions.json`：原六题 RAGAs 回归集。
- `../../eval/holdout.json`：新增四题回归集，不宣称为独立盲测。
- `../../eval/harness_checks.json`：19 个 Harness 主题检索问题与预期文件。
- `../../eval/harness_followup_checks.json`：两道独立源码定向补验题，不替换综合题失败。
- `../../scripts/`：配置导入、浏览器采集、盘点、真实验证和快照核对脚本。

## 历史结论与保护范围

历史指标及失败结论保留在根目录 `VALIDATION.md`、`EVALUATION.md`、
`OPTIMIZATION.md` 和 `HARNESS_KNOWLEDGE.md` 中。
例如综合记忆题未通过、FastMCP 版本号误写等结论仍保留，
但其原始输出已删除；文档中的旧路径仅为历史运行标识，不再是可读取的证据。

生产 SQLite、原文快照、本地模型、配置、虚拟环境及 Docker 数据未清理。
未清空 Redis，也未删除任何远程向量集合。

## 重跑

普通回归可避免重新生成项目缓存：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
```

真实 E2E 与 RAGAs 的命令见根目录 `README.md`。
使用新的 `KA_EVIDENCE_DIR` / `--output` 目录，不伪造或恢复历史运行记录；
新运行需要真实服务及凭证，可能产生模型 / 搜索费用。
