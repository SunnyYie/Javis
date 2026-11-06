---
name: jobs
description: 获取与匹配只读岗位
allowed_tools: current_state, update_preferences, search_jobs, match_jobs, list_jobs, job_detail, select_job
---
# 岗位匹配
适用：设定求职条件、搜索、导入后的重匹配和选择目标岗位。

1. 询问用户明确的岗位、城市和目标税前月薪，默认不排除公司类型。
2. 条件变化后请用户 `/status` 再 `/confirm`，模型不能确认。
3. 用户明确要求搜索时才采集；调整条件优先重匹配，不重复采集。
4. 严格区分实时来源与文件导入，失败与零岗位，匹配等级与录用概率。
5. 选择目标时固定岗位及批次，不用后续刷新的内容覆盖旧依据。

产出：有来源和时间的岗位、匹配证据、缺口、未知项及失败状态。禁止投递、消息和上传简历。
