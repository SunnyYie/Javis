---
name: interview
description: 启动有状态的逐轮模拟面试
allowed_tools: current_state, start_interview
---
# 模拟面试
适用：用户明确要求 technical、project、behavioral 或 mixed 模式面试。

1. 明确范围和题量，默认五道主问题、最多两次追问。
2. 项目面试必须有已确认项目事实；技术题必须有核验通过的依据。
3. 调用 start_interview 后停止其他工具，交由程序逐轮主持。
4. 一次只展示当前题目，不展示答案、内部评分或未来题单。
5. 暂停不等于结束；用户 `/finish` 后才能复盘。

产出：当前题目、作答 token 和公开评分规则。面试状态不由模型自由改变。
