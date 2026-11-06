---
name: prepare
description: 题单生成与知识关联备考
allowed_tools: generate_questions, question_detail, explain_knowledge, knowledge_links, learning_plan
---
# 备考
适用：生成题单、解释知识、查关联和制定学习任务。

1. 根据明确主题、目标岗位和已确认薄弱项选择技术范围。
2. 生成题单保留难度、原题或生成题标记及引用；不足时展示实际覆盖。
3. 查看题目—知识点—原文关联；模型推断的岗位要求不是已核实事实。
4. 知识解释调用知识助手；搜索摘要、模型答案和个人简历不作为新原始知识。
5. 任务需可执行，包括复习、动手验证与再次作答；不能宣称已完成。

产出：题单、资料引用、知识关联及学习任务。local-only 仍会调用远程模型。
