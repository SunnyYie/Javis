import os
import unicodedata
from pathlib import Path

from javis_tools.adapters import Assistants
from javis_tools.tracing import Telemetry, instrument

from .interview import Interviews
from .llm import Models
from .models import BrainError, LearningPlan, ToolResult, TranscriptReview, now, private, uid
from .resume import Resumes
from .storage import Memory, Store

SKILLS = {
    "profile": ("画像收集", "propose_fact,current_state"),
    "resume": ("简历优化", "generate_resume,propose_fact,current_state"),
    "jobs": ("岗位匹配", "update_preferences,search_jobs,match_jobs,list_jobs,job_detail,select_job,current_state"),
    "prepare": ("备考", "generate_questions,question_detail,explain_knowledge,knowledge_links,learning_plan"),
    "interview": ("模拟面试", "start_interview,current_state"),
    "review": ("复盘", "review_interview,learning_plan,propose_memory"),
}


class Service:
    def __init__(self, settings, sid=None, *, adapters=None, models=None, emit=lambda _: None):
        self.settings, self.emit = settings, emit
        self.store = Store(settings.data_dir)
        self.sid = self.store.session(sid)
        self.directory = self.store.directory(self.sid)
        self.memory = Memory(settings.memory_dir)
        self.adapters = adapters or Assistants(settings, self.sid, emit)
        self.models = models or Models(settings)
        self.resumes = Resumes(self)
        self.interviews = Interviews(self)
        self.user_text, self.calls = "", 0
        self.last_results = []
        self.telemetry = Telemetry(settings)
        instrument(self, "tool", "tool", "tool")
        instrument(self.models, "structured", "models.structured")

    def state(self):
        return self.store.state(self.sid)

    def write_private(self, path, text):
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)

    def require_remote(self):
        if not self.state()["remote_allowed"]:
            raise BrainError("consent_required", "请先 /consent，允许脱敏内容发送至配置模型")

    def consent(self):
        state = self.state()
        state["remote_allowed"] = True
        self.store.save(self.sid, state)
        self.adapters.job.consent()
        return {"remote_allowed": True}

    def not_interview(self):
        interview = self.interviews.current()
        if interview and interview["status"] != "finished":
            raise BrainError("interview_in_progress", "面试期间只能作答、暂停、恢复或明确结束")

    def snapshot(self, presented=False):
        state = self.state()
        interview = self.interviews.current()
        if interview and interview["status"] != "finished":
            return {"session": self.sid, "interview": self.interviews.public(interview)}
        return {
            "session": self.sid, "remote_allowed": state["remote_allowed"],
            "facts": self.store.records(self.sid, "fact"),
            "job_state": self.adapters.job.snapshot(presented=presented),
            "adopted_resume": state["adopted_resume"], "target": state["target"],
            "latest_questions": state["latest_questions"],
            "resumes": [r["id"] for r in self.store.records(self.sid, "resume")],
            "job_runs": [
                {"id": r["id"], "source": r["source"], "status": r["status"],
                 "count": len(r["job_ids"])}
                for r in self.adapters.job.storage.list("runs", self.sid)
            ],
            "tasks": state["tasks"], "interview": self.interviews.public(interview),
            "confirmed_memory": self.memory.all(), "candidate_memory": state["candidate_memory"],
        }

    def authorize(self, path, kind):
        self.not_interview()
        if kind in ("resume", "jobs"):
            return self.adapters.job.authorize_file(Path(path), kind)
        original = Path(path).expanduser().resolve()
        if original.suffix.lower() not in (".txt", ".md", ".json") or (
            not original.is_file() or not 0 < original.stat().st_size <= 1024 * 1024
        ):
            raise BrainError("invalid_transcript", "面试记录需为 1 MiB 内的 TXT、Markdown 或 JSON")
        text = private(original.read_text(encoding="utf-8"))
        if len(text) > 60000:
            raise BrainError("transcript_too_long", "面试记录最多 60000 字符，请先分段")
        target = self.directory / f"{uid('transcript_')}{original.suffix}"
        self.write_private(target, text)
        return self.store.put(self.sid, "transcript", {
            "text": text, "authorized_copy": str(target), "imported_at": now(),
        })

    def import_resume(self, path, force_ocr=False):
        self.require_remote()
        self.not_interview()
        approved = self.authorize(path, "resume")
        result = self.adapters.job.parse_resume(str(approved), force_ocr)
        profile = self.adapters.job.state()["resume"]
        self.resumes.import_profile(profile)
        state = self.state()
        state["adopted_resume"], state["target"], state["latest_questions"] = None, None, None
        self.store.save(self.sid, state)
        return result

    def preferences(self, **changes):
        self.not_interview()
        result = self.adapters.job.update_preferences(**changes)
        state = self.state()
        state["target"] = None
        state["latest_questions"] = None
        self.store.save(self.sid, state)
        return result

    def select_job(self, job_id, run_id=None):
        self.not_interview()
        job = self.adapters.job
        runs = job.storage.list("runs", self.sid)
        run_id = run_id or (runs[-1]["id"] if runs else None)
        if not run_id or run_id not in {r["id"] for r in runs}:
            raise BrainError("unknown_run")
        posting = next((j for j in job.storage.run_jobs(self.sid, run_id) if j["id"] == job_id), None)
        if posting is None:
            raise BrainError("unknown_job")
        posting.pop("raw", None)
        posting.pop("source_file", None)
        target = {"run_id": run_id, "job": posting}
        state = self.state()
        state["target"] = target
        state["latest_questions"] = None
        self.store.save(self.sid, state)
        self.store.link(self.sid, run_id, "target_job", job_id, {"type": "source_snapshot"})
        return target

    def list_jobs(self):
        self.not_interview()
        job = self.adapters.job
        return {
            "runs": job.storage.list("runs", self.sid),
            "jobs": [
                {k: v for k, v in j.items() if k not in ("raw", "source_file")}
                for j in job.result_jobs()
            ],
            "results": job.results(allow_stale=True),
            "results_stale": job.snapshot()["results_stale"],
        }

    def record_detail(self, record_id):
        self.not_interview()
        if self.store.kind(self.sid, record_id) not in {
            "fact", "resume", "questions", "knowledge", "review", "learning",
        }:
            raise BrainError("record_not_public")
        return self.store.get(self.sid, record_id)

    def questions(self, topic="", count=5):
        self.require_remote()
        self.not_interview()
        if not 1 <= count <= 20:
            raise BrainError("invalid_question_count")
        target = self.state().get("target")
        skills = [
            f["text"] for f in self.store.records(self.sid, "fact")
            if f["section"] == "skills" and f["confirmed"]
        ][:20]
        # Shared corpus receives technical topics only, never a resume or personal dialogue.
        query = private(topic).strip() or (
            "，".join(skills) if skills else target["job"]["title"] if target else ""
        )
        if not query or len(query) > 2000:
            raise BrainError("topic_required", "请提供明确的技术主题")
        answer = self.adapters.ask(
            "questions", f"请给我 {count} 道关于 {query} 的面试题。",
            local_only=self.settings.local_only,
        )
        citations = {c["id"]: c for c in answer["citations"]}
        questions = []
        for q in answer.get("questions", []):
            if not q.get("evidence_ids") or not set(q["evidence_ids"]) <= citations.keys():
                raise BrainError("invalid_question_evidence")
            questions.append(q)
        record = self.store.put(self.sid, "questions", {
            **answer, "questions": questions, "topic": query, "requested_count": count,
            "target": target, "created_at": now(),
        })
        state = self.state()
        state["latest_questions"] = record["id"]
        self.store.save(self.sid, state)
        for index, q in enumerate(questions):
            qid = f"{record['id']}:{index + 1}"
            for point in q["knowledge_points"]:
                point_id = self.point_id(point)
                self.store.link(self.sid, qid, "tests", point_id, {
                    "type": "model_association", "evidence_ids": q["evidence_ids"],
                })
                if target:
                    self.store.link(self.sid, target["job"]["id"], "requires", point_id, {
                        "type": "model_inferred", "run_id": target["run_id"],
                    })
                for cid in q["evidence_ids"]:
                    self.store.link(self.sid, point_id, "supported_by", cid, {
                        "type": "source_reference", "citation": citations[cid],
                    })
        return record

    @staticmethod
    def point_id(point):
        return "point:" + " ".join(unicodedata.normalize("NFKC", point).casefold().split())

    def explain(self, question):
        self.require_remote()
        self.not_interview()
        answer = self.adapters.ask(
            "knowledge", private(question), local_only=self.settings.local_only,
        )
        record = self.store.put(self.sid, "knowledge", {
            **answer, "question": private(question), "created_at": now(),
        })
        for citation in answer.get("citations", []):
            self.store.link(self.sid, self.point_id(question), "supported_by", citation["id"], {
                "type": "source_reference", "citation": citation,
            })
        return record

    def question_detail(self, number=1, question_set_id=""):
        self.not_interview()
        record_id = question_set_id or self.state().get("latest_questions")
        if not record_id:
            raise BrainError("questions_required")
        record = self.store.get(self.sid, record_id, "questions")
        if not 1 <= number <= len(record["questions"]):
            raise BrainError("invalid_question_number")
        question = record["questions"][number - 1]
        return {
            "question": question, "set_id": record_id, "number": number,
            "citations": [c for c in record["citations"] if c["id"] in question["evidence_ids"]],
        }

    def review(self, record_id=None):
        self.not_interview()
        if record_id:
            record = self.store.get(self.sid, record_id)
            if record.get("basis") and ("rows" in record or "observations" in record):
                return record
            if "authorized_copy" in record:
                self.require_remote()
                review = self.models.structured(TranscriptReview, """
对用户授权的面试记录做复盘。quote 必须逐字摘录记录正文；analysis 只分析对应表达和可观察缺口。
不推断面试官意图，不给招聘结论，不伪造评分或对话。列出可执行学习任务与信息缺口。
""", {"transcript": record["text"]})
                if any(o.quote not in record["text"] for o in review.observations):
                    raise BrainError("invalid_transcript_quote")
                return self.store.put(self.sid, "review", {
                    **review.model_dump(), "source": record_id,
                    "basis": "基于用户提供记录的分析，未核验实际面试过程",
                })
        result = self.interviews.review(record_id)
        saved = self.store.put(self.sid, "review", result)
        for point in result["weak_points"]:
            self.propose_memory("weakness", point, saved["id"])
        interview = self.interviews.current() if not record_id else self.store.get(
            self.sid, record_id, "interview",
        )
        for answer in interview["answers"]:
            q = interview["questions"][answer["question_index"]]
            for point in q["knowledge_points"]:
                self.store.link(
                    self.sid, answer["token"], "answer_of", self.point_id(point),
                    {"type": "saved_answer", "interview_id": interview["id"]},
                )
        return saved

    def learning_plan(self, topic=""):
        self.require_remote()
        self.not_interview()
        reviews = self.store.records(self.sid, "review")
        plan = self.models.structured(LearningPlan, """
根据已确认薄弱项、最近复盘和用户目标制定不超过12项可执行练习。
区分复习、动手验证、重新作答，不虚构完成状态、资料链接或用户时间安排。
""", {
            "topic": private(topic), "confirmed_memory": self.memory.all(),
            "review": reviews[-1] if reviews else None,
            "links": self.store.links(self.sid, topic)[:30],
        })
        record = self.store.put(self.sid, "learning", {**plan.model_dump(), "created_at": now()})
        state = self.state()
        state["tasks"].append({"id": record["id"], "status": "pending", "tasks": plan.tasks})
        self.store.save(self.sid, state)
        return record

    def propose_memory(self, kind, text, source=""):
        if kind not in ("fact", "preference", "weakness"):
            raise BrainError("invalid_memory_kind")
        if not text.strip() or len(text) > 2000:
            raise BrainError("invalid_memory")
        if source:
            self.store.get(self.sid, source)
        candidate = {
            "id": uid("memory_"), "kind": kind, "text": private(text.strip()),
            "source": source or "user_conversation", "source_session": self.sid,
            "proposed_at": now(),
        }
        state = self.state()
        existing = next((m for m in state["candidate_memory"] if m["kind"] == kind
                         and m["text"] == candidate["text"] and m["source"] == candidate["source"]), None)
        if existing:
            return existing
        state["candidate_memory"].append(candidate)
        self.store.save(self.sid, state)
        return candidate

    def confirm_memory(self, record_id):
        candidate = next((m for m in self.state()["candidate_memory"] if m["id"] == record_id), None)
        if candidate is None:
            raise BrainError("unknown_memory_candidate")
        return self.memory.confirm(candidate)

    def skill(self, name):
        if name not in SKILLS:
            raise BrainError("unknown_skill")
        path = self.settings.skills_dir / name / "SKILL.md"
        return {"name": name, "content": path.read_text(encoding="utf-8")}

    def tool(self, name, **kwargs):
        self.calls += 1
        if self.calls > self.settings.tool_limit:
            result = ToolResult(status="blocked", error="tool_budget_exceeded").model_dump()
            self.last_results.append({"tool": name, **result})
            self.store.event(self.sid, name, "blocked", {"code": "tool_budget_exceeded"})
            return result
        self.store.event(self.sid, name, "started")
        try:
            self.require_remote()
            self.not_interview()
            if name == "search_jobs":
                import re
                if not re.search(r"搜索|查找|搜岗位|找工作|找岗位", self.user_text) or re.search(
                    r"不要|先不|别搜|不搜索|不查找", self.user_text,
                ):
                    raise BrainError("search_request_required", "请明确要求搜索，或使用 /search")
            routes = {
                "current_state": lambda: self.snapshot(),
                "load_skill": lambda: self.skill(**kwargs),
                "generate_resume": self.resumes.generate,
                "update_preferences": lambda: self.preferences(**kwargs),
                "search_jobs": self.adapters.job.search,
                "match_jobs": self.adapters.job.match,
                "select_job": lambda: self.select_job(**kwargs),
                "list_jobs": self.list_jobs,
                "job_detail": lambda: self.adapters.job.detail(**kwargs),
                "question_detail": lambda: self.question_detail(**kwargs),
                "generate_questions": lambda: self.questions(**kwargs),
                "explain_knowledge": lambda: self.explain(**kwargs),
                "knowledge_links": lambda: {"links": self.store.links(self.sid, **kwargs)},
                "start_interview": lambda: self.interviews.prepare(**kwargs),
                "review_interview": lambda: self.review(**kwargs),
                "learning_plan": lambda: self.learning_plan(**kwargs),
                "propose_memory": lambda: self.propose_memory(**kwargs),
            }
            if name == "propose_fact":
                quote = kwargs.pop("quote")
                if not quote.strip() or quote not in self.user_text:
                    raise BrainError("invalid_user_quote", "个人事实必须指向本轮用户原话")
                value = self.resumes.add_fact(**kwargs, source={
                    "type": "user_quote", "quote": private(quote),
                })
            elif name in routes:
                value = routes[name]()
            else:
                raise BrainError("unknown_tool")
            if not isinstance(value, dict):
                value = {"value": value}
            status = "ok"
            if (
                value.get("sufficient") is False or value.get("persisted") is False
                or value.get("status") in ("partial", "failed")
            ):
                status = "partial" if value.get("status") != "failed" else "failed"
            if value.get("source_status") in ("partial", "failed", "interrupted"):
                status = "partial" if value.get("results") else "failed"
            result = ToolResult(
                status=status, data=value, citations=value.get("citations", []),
                warnings=value.get("warnings", []) + value.get("issues", [])
                + value.get("source_issues", []),
            )
            self.store.event(self.sid, name, status, {"record_id": value.get("id")})
            self.last_results.append({"tool": name, **result.model_dump()})
            return result.model_dump()
        except Exception as exc:
            code = getattr(exc, "code", getattr(exc, "category", type(exc).__name__))
            self.store.event(self.sid, name, "failed", {"code": code})
            result = ToolResult(status="failed", error=code).model_dump()
            self.last_results.append({"tool": name, **result})
            return result

    def delete(self):
        warnings = []
        try:
            self.adapters.job.delete()
        except Exception:
            warnings.append("岗位缓存清理未完成或缓存服务不可用，请检查 Redis TTL")
        self.adapters.close()
        self.memory.delete_session(self.sid)
        self.store.delete(self.sid)
        return {"deleted": self.sid, "warnings": warnings}

    def close(self):
        try:
            self.adapters.close()
        finally:
            self.telemetry.close()
