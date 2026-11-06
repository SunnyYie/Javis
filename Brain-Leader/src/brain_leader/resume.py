from .models import BrainError, FactInput, ResumePlan, digest, now, private

HEADINGS = {
    "summary": "职业概述", "skills": "专业技能", "work": "工作经历",
    "projects": "项目经历", "education": "教育经历", "achievements": "成果",
}


class Resumes:
    def __init__(self, service):
        self.s = service

    def add_fact(self, section, text, *, confirmed=False, source=None):
        value = FactInput(section=section, text=private(text.strip()))
        if not value.text:
            raise BrainError("empty_fact")
        return self.s.store.put(self.s.sid, "fact", {
            **value.model_dump(), "confirmed": confirmed,
            "source": source or {"type": "conversation_candidate"},
            "created_at": now(),
        })

    def confirm(self, fact_id):
        fact = self.s.store.get(self.s.sid, fact_id, "fact")
        if fact.get("rejected"):
            raise BrainError("fact_rejected")
        fact["confirmed"] = True
        self.s.store.put(self.s.sid, "fact", fact, record_id=fact_id, mutable=True)
        return fact

    def import_profile(self, profile):
        for section, key in (("work", "work"), ("projects", "projects"), ("education", "education")):
            for item in profile[key]:
                evidence = item["evidence"]
                self.add_fact(section, evidence["quote"], confirmed=True, source={
                    "type": "resume_quote", "resume_id": profile["id"], **evidence,
                })
        for skill in profile["skills"]:
            page = next((p for p in profile["pages"] if skill in p["text"]), None)
            if page:
                self.add_fact("skills", skill, confirmed=True, source={
                    "type": "resume_quote", "resume_id": profile["id"],
                    "page": page["page"], "quote": skill,
                })

    def generate(self):
        self.s.require_remote()
        self.s.not_interview()
        facts = self.s.store.records(self.s.sid, "fact")
        replaced = {
            f["source"]["fact_id"] for f in facts
            if f["confirmed"] and f["source"].get("type") == "proposed_rewrite"
        }
        facts = {f["id"]: f for f in facts if f["confirmed"] and f["id"] not in replaced}
        if not facts:
            raise BrainError("facts_required", "请提供并确认真实经历、项目或教育事实")
        plan = self.s.models.structured(ResumePlan, """
根据已确认事实和可选岗位做简历内容编排。sections 每项只选择给定 fact_ids，heading 使用简短分类标题，
不要重复事实。diagnosis 给出具体诊断，missing 列缺失信息。可在 rewrites 提议润色，
必须指向已有 fact_id，保留原意且不添加经历或数字；这些建议需人工确认，不直接进入正文。
不得把岗位要求写成个人已具备的能力。
""", {"facts": list(facts.values()), "target": self.s.state().get("target")})
        sections, used = [], set()
        for section in plan.sections:
            lines = []
            for fid in section.fact_ids:
                if fid not in facts or fid in used:
                    raise BrainError("invalid_fact_reference", "模型返回未知或重复事实，未发布简历")
                used.add(fid)
                lines.append({"text": facts[fid]["text"], "fact_ids": [fid]})
            # Section headings are program-owned, not another channel for generated claims.
            category = facts[section.fact_ids[0]]["section"]
            sections.append({"heading": HEADINGS[category], "lines": lines})
        if any(r.fact_id not in facts for r in plan.rewrites):
            raise BrainError("invalid_fact_reference")
        suggestions = []
        for rewrite in plan.rewrites:
            if private(rewrite.text) == facts[rewrite.fact_id]["text"]:
                continue
            candidate = self.add_fact(
                facts[rewrite.fact_id]["section"], rewrite.text,
                source={"type": "proposed_rewrite", "fact_id": rewrite.fact_id,
                        "reason": private(rewrite.reason)},
            )
            suggestions.append(candidate)
        draft = self.s.store.put(self.s.sid, "resume", {
            "sections": sections, "diagnosis": [private(x) for x in plan.diagnosis],
            "missing": [private(x) for x in plan.missing],
            "suggestions": suggestions, "created_at": now(),
            "target": self.s.state().get("target"),
            "facts_hash": digest([facts[i] for i in sorted(used)]),
        })
        return draft

    def adopt(self, draft_id):
        self.s.require_remote()
        self.s.not_interview()
        draft = self.s.store.get(self.s.sid, draft_id, "resume")
        for section in draft["sections"]:
            for line in section["lines"]:
                fact = self.s.store.get(self.s.sid, line["fact_ids"][0], "fact")
                if not fact["confirmed"] or line["text"] != fact["text"]:
                    raise BrainError("unconfirmed_fact")
        # Re-parse the user-adopted local text through the existing Job-Agent contract.
        text = "\n\n".join(
            section["heading"] + "\n" + "\n".join(line["text"] for line in section["lines"])
            for section in draft["sections"]
        )
        path = self.s.directory / f"{draft_id}.md"
        self.s.write_private(path, text)
        job = self.s.adapters.job
        authorized = job.authorize_file(path, "resume")
        result = job.parse_resume(str(authorized))
        state = self.s.state()
        state["adopted_resume"], state["target"], state["latest_questions"] = draft_id, None, None
        self.s.store.save(self.s.sid, state)
        jstate = job.state()
        jstate["result_context"] = None
        job.storage.save_state(self.s.sid, jstate)
        return {"adopted": draft_id, "job_profile": result}

    def export(self, draft_id=None, preview=False):
        from javis_tools.documents import render_resume

        self.s.not_interview()
        state = self.s.state()
        draft_id = draft_id or state["adopted_resume"]
        if not draft_id:
            raise BrainError("resume_required")
        if not preview and draft_id != state["adopted_resume"]:
            raise BrainError("adoption_required", "正式导出前请显式采纳；草稿可使用 preview")
        draft = self.s.store.get(self.s.sid, draft_id, "resume")
        # A new unique directory preserves previous exports and their provenance.
        from .models import uid

        return render_resume(
            draft, state["contacts"], self.s.directory / "exports" / uid(), preview,
            font_path=self.s.settings.resume_font_path,
            font_index=self.s.settings.resume_font_index,
        )
