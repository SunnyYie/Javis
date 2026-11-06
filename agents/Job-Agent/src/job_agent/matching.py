import json
import re

from .models import JobPosting, JobPreferences, MatchResult, ResumeProfile, SemanticMatch, digest
from .privacy import redact
from .resume import compact

MATCH_VERSION = "match-v1"


def rule_check(job: JobPosting, prefs: JobPreferences, profile: ResumeProfile) -> dict:
    result = {"reasons": [], "gaps": [], "pending": [], "reject": False}
    if not job.city:
        result["pending"].append("工作城市未知")
    elif job.city.removesuffix("市") not in {c.removesuffix("市") for c in prefs.cities}:
        result["reasons"].append(f"工作城市 {job.city} 不符合要求")
        result["reject"] = True
    searchable = "\n".join([job.title, job.company, job.description])
    for term in prefs.excluded_terms:
        if term.casefold() in searchable.casefold():
            result["reject"] = True
            result["reasons"].append(f"命中用户排除词：{term}（需结合原文核对语境）")
    salary = job.salary
    if prefs.target_monthly_k:
        target = prefs.target_monthly_k
        if salary.period != "month" or salary.currency != "CNY" or salary.max_k is None:
            result["pending"].append("薪资面议、周期或币种不明，未换算为月薪")
        elif salary.max_k < target:
            result["reject"] = True
            result["reasons"].append(f"薪资上限低于目标 {target:g}K")
        elif salary.min_k < target:
            result["gaps"].append(f"可谈岗位：薪资下限低于目标 {target:g}K")
    experience = job.experience
    minimum = None
    match = re.search(r"(\d+)\s*[-–－]\s*\d+\s*年|(\d+)\s*年以上", experience)
    if match:
        minimum = int(match[1] or match[2]) * 12
    if minimum is not None:
        if not profile.work:
            result["pending"].append("简历工作年限无法确认")
        elif minimum > profile.work_months:
            result["gaps"].append(
                f"要求 {experience}，简历可核对的已完成工作月份为 {profile.work_months}"
            )
    elif not experience:
        result["pending"].append("岗位经验要求未知")
    if any(not w.start or not w.end for w in profile.work):
        result["pending"].append("简历存在待确认的工作日期")
    if not job.description:
        result["pending"].append("缺少岗位正文")
    return result


def match_job(job, prefs, profile, models, cache) -> MatchResult:
    rules = rule_check(job, prefs, profile)
    base = {
        "job_id": job.id, "preferences_hash": digest(prefs),
        "reasons": rules["reasons"], "gaps": rules["gaps"], "pending": rules["pending"],
    }
    if rules["reject"]:
        return MatchResult(level="不符合", **base)
    if not job.description:
        return MatchResult(level="信息不足", **base)
    safe_profile = {
        "summary": redact(profile.summary), "skills": profile.skills,
        "work_months": profile.work_months, "as_of": profile.as_of,
        "work": [w.model_dump() for w in profile.work],
        "projects": [p.model_dump() for p in profile.projects],
        "education": [e.model_dump() for e in profile.education],
    }
    safe_profile = json.loads(redact(json.dumps(safe_profile, ensure_ascii=False)))
    job_text = redact("\n".join([
        job.title, job.company, job.experience, job.education, job.description,
    ]))
    payload = {"profile": safe_profile, "job": job_text, "preferences": prefs.model_dump()}
    key = cache.key("match", {
        "payload": payload, "model": models.identity(), "version": MATCH_VERSION,
    })
    cached = cache.get(key)
    try:
        semantic = SemanticMatch.model_validate(cached) if cached else models.structured(
            SemanticMatch,
            """根据期望方向、技能、项目证据、学历与岗位正文评估。
满足条件只用于有证据支持的匹配，存在技能或经验差距为可尝试，无关为不符合，
不足以判断为信息不足。不要把工具调用失败或未知学历判为匹配。
从 profile 原文证据中逐字复制 resume_quotes，从 job 中逐字复制 job_quotes；
不允许省略号或改写。reason 简述依据，gaps 记录真实差距。""",
            json.dumps(payload, ensure_ascii=False),
        )
        def text_values(value):
            if isinstance(value, dict):
                return "\n".join(text_values(v) for v in value.values())
            if isinstance(value, list):
                return "\n".join(text_values(v) for v in value)
            return str(value)

        resume_text = compact(text_values(safe_profile))
        if any(compact(q) not in resume_text for q in semantic.resume_quotes):
            raise ValueError("Resume evidence not grounded")
        if any(compact(q) not in compact(job_text) for q in semantic.job_quotes):
            raise ValueError("Job evidence not grounded")
        cache.set(key, semantic.model_dump())
    except Exception:
        return MatchResult(
            level="评估失败", error="model_or_evidence_validation_failed", **base
        )
    level = semantic.level
    if level == "满足条件" and rules["pending"]:
        level = "信息不足"
    elif level == "满足条件" and (rules["gaps"] or semantic.gaps):
        level = "可尝试"
    base["reasons"] = [*base["reasons"], semantic.reason]
    base["gaps"] = [*base["gaps"], *semantic.gaps]
    return MatchResult(
        level=level, resume_quotes=semantic.resume_quotes, job_quotes=semantic.job_quotes,
        cached=bool(cached), **base,
    )
