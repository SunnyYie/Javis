---
name: resume
description: 使用已确认事实生成和优化简历
allowed_tools: current_state, propose_fact, generate_resume
---
# 简历优化
适用：从零生成、诊断已有简历、围绕目标岗位优化。

1. 读取当前已确认事实及固定目标岗位；缺乏事实先走画像收集。
2. 区分岗位要求与候选人已有经历；不编造技能、任职日期、数据或业绩。
3. 调用 generate_resume 编排；润色建议是待确认候选，不直接替换正文。
4. 解释诊断、缺口和版本 ID；用户显式 `/adopt ID` 后才能正式导出。

产出：简历版本、修改诊断、事实引用、待确认润色和缺失项。预览不是采纳。
