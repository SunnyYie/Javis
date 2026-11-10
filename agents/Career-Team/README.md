# Career Team

职业业务实现从 `Brain-Leader/src/brain_leader` 原位抽出到 `src/career_team`，
没有维护第二份业务。旧 `brain_leader.service/agent/models/storage/resume/interview/llm`
均为兼容导入；`bl` 的领域命令转发到 `career_team.commands`。

保留原职业数据目录、会话 ID、SQLite 记录引用、专用知识线程、追踪与清理。
未增加职业数据库列，也不重建旧库。旧长期记忆没有 `scope` 时按 `career` 解释；
租房不读取该数据库。

面试中的明确找房请求先挂起人工确认，不提交为回答。
`/team-switch confirm|reject` 由终端宿主处理，未注册为模型工具。
暂停保留同题 token；恢复使用 `javis` 的 `/career resume` 或 `bl` 的 `/continue`。
暂停仍禁止通过职业题单、知识、导出及复盘泄露参考答案。

团队输入/输出使用中立 `javis_core` 契约；团队不导入主控。
独立 `bl daily` 不属于本次迁移范围，原文件与调度不改。
