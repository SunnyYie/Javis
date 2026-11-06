# Brain-Leader 项目记忆

运行时的 `profile.sqlite3` 只保存用户显式确认的职业事实、偏好和薄弱项。
它不是 Codex 自身的记忆目录，不存放公共知识库或模型生成的事实来源。

`/memory` 查看，`/memory confirm ID` 确认，`/memory delete ID` 删除，
`/memory correct ID 新内容` 提出待确认修订。删除来源会话同时删除该会话产生的确认记忆。
数据库为私有运行数据，不提交 Git；测试应设置独立 `BL_MEMORY_DIR`。
