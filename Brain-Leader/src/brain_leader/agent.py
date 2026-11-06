import json

from langchain.agents import create_agent
from langchain.tools import tool
from langsmith import tracing_context

from .llm import SAFETY
from .models import private
from .service import SKILLS


def build_agent(service):
    @tool
    def current_state() -> dict:
        """读取当前事实、条件、简历版本、任务、长期记忆与确认状态。"""
        return service.tool("current_state")

    @tool
    def load_skill(name: str) -> dict:
        """按需读取流程技能。可选 profile、resume、jobs、prepare、interview、review。"""
        return service.tool("load_skill", name=name)

    @tool
    def propose_fact(section: str, text: str, quote: str) -> dict:
        """提出待确认事实。section=summary/skills/work/projects/education/achievements；
        quote 必须逐字来自本轮用户输入。不能自行确认事实。"""
        return service.tool("propose_fact", section=section, text=text, quote=quote)

    @tool
    def generate_resume() -> dict:
        """使用已确认事实编排简历，返回诊断、缺口和待确认润色建议。不能替用户采纳。"""
        return service.tool("generate_resume")

    @tool
    def update_preferences(
        roles: list[str] | None = None, cities: list[str] | None = None,
        target_monthly_k: float | None = None, excluded_terms: list[str] | None = None,
    ) -> dict:
        """仅更新用户明确提出的求职条件。更新后需要用户 /status、/confirm。"""
        changes = {k: v for k, v in locals().items() if v is not None and k != "service"}
        return service.tool("update_preferences", **changes)

    @tool
    def search_jobs() -> dict:
        """已确认条件且用户明确要求搜索时只读查询 BOSS，每轮最多一次；失败不等于无岗位。"""
        return service.tool("search_jobs")

    @tool
    def match_jobs() -> dict:
        """按已确认的当前条件重匹配现有岗位，不重新采集。"""
        return service.tool("match_jobs")

    @tool
    def list_jobs() -> dict:
        """读取已保存的岗位、批次、匹配结果和过期标记，不重新采集或匹配。"""
        return service.tool("list_jobs")

    @tool
    def select_job(job_id: str, run_id: str | None = None) -> dict:
        """选择本会话某个岗位批次的固定内容，作为简历与备考目标。"""
        return service.tool("select_job", job_id=job_id, run_id=run_id)

    @tool
    def job_detail(job_id: str) -> dict:
        """读取本会话岗位原文、匹配依据、缺口和未知项，解释推荐原因。"""
        return service.tool("job_detail", job_id=job_id)

    @tool
    def question_detail(number: int = 1, question_set_id: str = "") -> dict:
        """读取最新或指定题单中的第 N 题与原文引用，解析“解释第二题”等追问。"""
        return service.tool("question_detail", number=number, question_set_id=question_set_id)

    @tool
    def generate_questions(topic: str = "", count: int = 5) -> dict:
        """从有依据的题库生成 1-20 题，保留答案、原题标记、知识点与来源。"""
        return service.tool("generate_questions", topic=topic, count=count)

    @tool
    def explain_knowledge(question: str) -> dict:
        """调用知识助手解释技术问题，返回正文依据和知识覆盖状态。"""
        return service.tool("explain_knowledge", question=question)

    @tool
    def knowledge_links(topic: str = "") -> dict:
        """查看已保存的岗位、题目、知识点、资料和作答关联。"""
        return service.tool("knowledge_links", topic=topic)

    @tool
    def start_interview(
        mode: str = "mixed", count: int = 5, max_followups: int = 2, topic: str = "",
    ) -> dict:
        """用户明确要求模拟面试时开始。mode=technical/project/behavioral/mixed。
        返回当前题目和作答 token，不返回参考答案。"""
        return service.tool(
            "start_interview", mode=mode, count=count, max_followups=max_followups, topic=topic,
        )

    @tool
    def review_interview(record_id: str | None = None) -> dict:
        """复盘已明确结束的面试或已授权导入的记录；进行中的面试不可查看。"""
        return service.tool("review_interview", record_id=record_id)

    @tool
    def learning_plan(topic: str = "") -> dict:
        """根据确认的薄弱项和复盘制定可执行学习任务，不伪造完成状态。"""
        return service.tool("learning_plan", topic=topic)

    @tool
    def propose_memory(kind: str, text: str, source: str = "") -> dict:
        """提出跨会话记忆候选，kind=fact/preference/weakness；必须等待用户 /memory confirm。"""
        return service.tool("propose_memory", kind=kind, text=text, source=source)

    return create_agent(
        service.models.llm,
        tools=[
            current_state, load_skill, propose_fact, generate_resume, update_preferences,
            search_jobs, match_jobs, list_jobs, select_job, job_detail, question_detail,
            generate_questions, explain_knowledge,
            knowledge_links, start_interview, review_interview, learning_plan, propose_memory,
        ],
        system_prompt=SAFETY + """
你是 Brain-Leader，连接画像、简历、岗位、备考、模拟面试和复盘。
先 current_state，再按需 load_skill；一次只推进用户请求涉及的步骤，不强制整个流水线。
事实需用户通过 /facts confirm 确认。文件只能由 /resume、/import、/transcript 授权。
没有事实先询问真实经历；未知求职城市、薪资、岗位先问，不从简历猜测意愿。
展示条件请用户 /status 再 /confirm；工具不能替用户确认。
润色建议不是事实，采纳简历必须由用户 /adopt ID。导出由用户 /export 执行。
面试未结束不能展示答案；开始面试后停止其他工具，等待用户逐轮作答。
失败、证据不足、未持久化和实时来源未验收必须明确表达。
技能目录：""" + json.dumps(SKILLS, ensure_ascii=False),
    )


def chat_turn(service, text):
    service.require_remote()
    if not text.strip() or len(text) > 12000:
        from .models import BrainError
        raise BrainError("invalid_input", "输入需为 1-12000 字符")
    interview = service.interviews.current()
    if interview and interview["status"] != "finished":
        if interview["status"] == "waiting":
            return service.interviews.answer(interview["token"], text)
        return {"interview": service.interviews.public(interview)}
    service.calls = 0
    service.last_results = []
    service.user_text = text
    service.adapters.job.collection_used = False
    messages = [*service.store.history(service.sid), {"role": "user", "content": private(text)}]
    with tracing_context(enabled=False):
        result = build_agent(service).invoke(
            {"messages": messages}, config={
                "recursion_limit": service.settings.tool_limit * 2 + 5,
                "max_concurrency": 1,
            },
        )
    interview = service.interviews.current()
    if interview and interview["status"] != "finished":
        answer = {"interview": service.interviews.public(interview)}
    else:
        content = result["messages"][-1].content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        answer = {
            "text": private(str(content)),
            "operations": [
                {"tool": r["tool"], "status": r["status"], "error": r["error"],
                 "record_id": r.get("data", {}).get("id"),
                 "warnings": r["warnings"], "artifacts": r["artifacts"]}
                for r in service.last_results
            ],
        }
    service.store.turn(service.sid, private(text), json.dumps(answer, ensure_ascii=False))
    return answer
