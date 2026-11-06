import csv
import io
import json
import os
import shutil
import uuid
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from .boss import BossProvider
from .cache import Cache
from .config import Settings
from .jobs import import_jobs
from .llm import Models
from .matching import match_job
from .models import (
    AgentError,
    JobPosting,
    JobPreferences,
    ResumeProfile,
    SearchRun,
    digest,
    now,
)
from .privacy import redact
from .resume import MAX_BYTES, ResumeParser
from .storage import Storage


class Service:
    def __init__(self, settings: Settings, sid: str | None = None, emit=print):
        self.settings = settings
        self.storage = Storage(settings.data_dir)
        self.sid = self.storage.session(sid)
        self.directory = self.storage.directory(self.sid)
        self.cache = Cache(settings, self.sid)
        self.models = Models(settings)
        self.parser = ResumeParser(settings, self.cache, self.models)
        self.emit = emit
        self.lock = RLock()
        self.collection_used = False

    def state(self):
        return self.storage.state(self.sid)

    def require_remote(self):
        if not self.state()["remote_allowed"]:
            raise AgentError("consent_required", "请先允许向配置模型发送脱敏职业内容")

    def consent(self):
        state = self.state()
        state["remote_allowed"] = True
        self.storage.save_state(self.sid, state)

    def context_hash(self, state=None):
        state = state or self.state()
        return digest({
            "resume": state["resume"]["id"] if state["resume"] else None,
            "preferences": state["preferences"],
        })

    def snapshot(self, presented=False) -> dict:
        state = self.state()
        profile = state["resume"]
        current = self.context_hash(state)
        if presented:
            state["presented"] = current
            self.storage.save_state(self.sid, state)
        return {
            "session": self.sid,
            "resume": {
                "summary": profile["summary"], "skills": profile["skills"],
                "work_months": profile["work_months"], "as_of": profile["as_of"],
                "uncertainties": profile["uncertainties"],
            } if profile else None,
            "preferences": state["preferences"],
            "missing": JobPreferences.model_validate(state["preferences"]).missing(),
            "confirmed": state["confirmed"] == current,
            "results_stale": bool(state["results"]) and state["result_context"] != current,
        }

    def confirm(self):
        state = self.state()
        if not state["resume"]:
            raise AgentError("resume_required", "请先导入并解析简历")
        missing = JobPreferences.model_validate(state["preferences"]).missing()
        if missing:
            raise AgentError("missing_preferences", "请补充：" + "、".join(missing))
        if state.get("presented") != self.context_hash(state):
            raise AgentError("snapshot_required", "请先查看当前简历摘要与求职条件，再确认")
        state["confirmed"] = self.context_hash(state)
        self.storage.save_state(self.sid, state)

    def ready(self):
        state = self.state()
        if not state["resume"]:
            raise AgentError("resume_required", "请先解析简历")
        missing = JobPreferences.model_validate(state["preferences"]).missing()
        if missing:
            raise AgentError("missing_preferences", "请补充：" + "、".join(missing))
        if state["confirmed"] != self.context_hash(state):
            raise AgentError("confirmation_required", "请查看条件摘要后输入 /confirm")
        self.require_remote()
        return state

    def update_preferences(self, **changes):
        state = self.state()
        merged = {**state["preferences"], **changes}
        try:
            prefs = JobPreferences.model_validate(merged)
        except ValidationError:
            raise AgentError("invalid_preferences", "求职条件字段或取值无效，请查看 /prefs 格式") from None
        state["preferences"] = prefs.model_dump()
        if self.context_hash(state) != state["confirmed"]:
            state["confirmed"] = None
        self.storage.save_state(self.sid, state)
        return self.snapshot()

    def authorize_file(self, path: Path, kind: str) -> Path:
        path = path.expanduser().resolve()
        allowed = {
            "resume": (".pdf", ".png", ".jpg", ".jpeg", ".md", ".txt"),
            "jobs": (".json", ".csv"),
        }
        maximum = MAX_BYTES if kind == "resume" else 20 * 1024 * 1024
        if (
            path.suffix.lower() not in allowed[kind] or not path.is_file()
            or not 0 < path.stat().st_size <= maximum
        ):
            raise AgentError("invalid_input_file", "文件格式、大小或路径不符合要求")
        inputs = self.directory / "inputs"
        inputs.mkdir(exist_ok=True, mode=0o700)
        target = inputs / f"{kind}-{uuid.uuid4().hex[:12]}{path.suffix.lower()}"
        shutil.copyfile(path, target)
        os.chmod(target, 0o600)
        state = self.state()
        state.setdefault("approved_files", {}).setdefault(kind, []).append(str(target))
        self.storage.save_state(self.sid, state)
        return target

    def approved(self, path: str, kind: str) -> Path:
        authorized = self.state().get("approved_files", {}).get(kind, [])
        resolved = str(Path(path).expanduser().resolve())
        if resolved not in authorized:
            raise AgentError(
                "file_not_authorized", f"请由用户通过 /{'resume' if kind == 'resume' else 'import'} "
                "命令明确选择文件；Agent 不能自行读取其他文件",
            )
        return Path(resolved)

    def parse_resume(self, path: str, force_ocr=False, extract_only=False):
        path = self.approved(path, "resume")
        if extract_only:
            pages = self.parser.extract(path, force_ocr)
            output = self.directory / "extracted-pages.json"
            output.write_text(
                json.dumps([p.model_dump() for p in pages], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.chmod(output, 0o600)
            return {
                "path": str(output), "pages": len(pages),
                "methods": [p.method for p in pages],
                "confidence": [p.confidence for p in pages],
            }
        self.require_remote()
        profile = self.parser.parse(path, force_ocr)
        state = self.state()
        state["resume"] = profile.model_dump()
        state["confirmed"] = None
        self.storage.save_state(self.sid, state)
        output = self.directory / f"resume-{profile.id[:12]}.json"
        output.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        os.chmod(output, 0o600)
        return self.snapshot()

    def import_file(self, path: str):
        path = self.approved(path, "jobs")
        jobs, issues = import_jobs(path)
        run = SearchRun(
            id=uuid.uuid4().hex, source="ohmyboss_import",
            conditions_hash=self.context_hash(), limit=len(jobs), job_ids=[j.id for j in jobs],
            status="partial" if issues and jobs else "failed" if issues else "complete",
            issues=issues, finished_at=now(),
        )
        for job in jobs:
            self.storage.put("jobs", self.sid, job, run_id=run.id)
        state = self.state()
        state["result_context"] = None
        self.storage.save_state(self.sid, state)
        self.storage.put("runs", self.sid, run)
        return {"run": run.model_dump(), "imported": len(jobs)}

    def search(self, limit: int | None = None, allow_expansion=False):
        state = self.ready()
        limit = limit or self.settings.search_limit
        if not 1 <= limit <= 200 or (limit > 30 and not allow_expansion):
            raise AgentError("limit_requires_permission", "超过 30 条需用户显式 --expand-limit")
        if self.collection_used:
            raise AgentError("collection_already_used", "本轮已采集过；继续查询需用户新指令")
        self.collection_used = True
        state["result_context"] = None
        self.storage.save_state(self.sid, state)
        run = SearchRun(
            id=uuid.uuid4().hex, source="boss_live",
            conditions_hash=self.context_hash(state), limit=limit,
        )
        self.storage.put("runs", self.sid, run)
        raw_dir = self.directory / "snapshots"
        raw_dir.mkdir(exist_ok=True, mode=0o700)

        def on_job(job):
            self.storage.put("jobs", self.sid, job, run_id=run.id)
            path = raw_dir / f"{run.id}-{job.id}.json"
            path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
            os.chmod(path, 0o600)
            if job.id not in run.job_ids:
                run.job_ids.append(job.id)
                self.storage.put("runs", self.sid, run)
            self.emit(f"已采集 {len(run.job_ids)}/{limit}：{job.title} · {job.company}")

        try:
            provider = BossProvider(self.settings, self.directory, self.emit)
            run.issues = provider.collect(
                JobPreferences.model_validate(state["preferences"]), limit, on_job,
            )
            run.status = "partial" if run.issues and run.job_ids else (
                "failed" if run.issues else "complete"
            )
        except KeyboardInterrupt:
            run.status = "interrupted"
            run.issues.append("interrupted")
            raise
        except AgentError as exc:
            run.status = "partial" if run.job_ids else "failed"
            run.issues.append(exc.code)
        finally:
            run.finished_at = now()
            self.storage.put("runs", self.sid, run)
        return run.model_dump()

    def match(self, run_id: str | None = None, limit: int = 30):
        state = self.ready()
        if not 1 <= limit <= 30:
            raise AgentError("match_limit", "单次最多评估 30 条；可指定其他任务分批评估")
        runs = self.storage.list("runs", self.sid)
        run = next((r for r in runs if r["id"] == run_id), None) if run_id else (
            runs[-1] if runs else None
        )
        if not run:
            raise AgentError("no_search_run", "请先获取或导入岗位")
        by_id = {j["id"]: j for j in self.storage.run_jobs(self.sid, run["id"])}
        if any(job_id not in by_id for job_id in run["job_ids"][:limit]):
            raise AgentError("run_snapshot_missing", "该旧任务缺少独立岗位版本，请重新导入或采集")
        profile = ResumeProfile.model_validate(state["resume"])
        prefs = JobPreferences.model_validate(state["preferences"])
        results = []
        for job_id in run["job_ids"][:limit]:
            result = match_job(
                JobPosting.model_validate(by_id[job_id]), prefs, profile, self.models, self.cache
            )
            results.append(result.model_dump())
            state["results"] = results
            state["result_context"] = self.context_hash(state)
            state["result_run"] = run["id"]
            self.storage.save_state(self.sid, state)
            self.emit(f"已评估 {len(results)}/{min(len(run['job_ids']), limit)}：{result.level}")
        # An empty latest run must replace, not inherit, previous recommendations.
        state["results"] = results
        state["result_context"] = self.context_hash(state)
        state["result_run"] = run["id"]
        self.storage.save_state(self.sid, state)
        return {
            "results": results, "run_id": run["id"], "source_status": run["status"],
            "source_issues": run["issues"], "unevaluated": max(0, len(run["job_ids"]) - limit),
        }

    def results(self, allow_stale=False):
        state = self.state()
        if state["results"] and state["result_context"] != self.context_hash(state):
            if not allow_stale:
                raise AgentError("stale_results", "条件或简历已变更，请确认后重新匹配")
        return state["results"]

    def detail(self, job_id: str):
        job = next((j for j in self.result_jobs() if j["id"] == job_id), None)
        if not job:
            raise AgentError("unknown_job", "未找到本会话中的岗位")
        job.pop("raw", None)
        job.pop("source_file", None)
        result = next((r for r in self.results() if r["job_id"] == job_id), None)
        return json.loads(redact(json.dumps({"job": job, "match": result}, ensure_ascii=False)))

    def result_jobs(self):
        run_id = self.state().get("result_run")
        return self.storage.run_jobs(self.sid, run_id) if run_id else self.storage.list(
            "jobs", self.sid
        )

    def export(self, format: str):
        if format not in ("json", "csv", "md"):
            raise AgentError("invalid_export_format")
        results = self.results()
        if not results:
            raise AgentError("no_results", "没有可导出的当前评估")
        state = self.state()
        jobs = {j["id"]: j for j in self.result_jobs()}
        rows = []
        for record in results:
            job = jobs[record["job_id"]].copy()
            job.pop("raw", None)
            job.pop("source_file", None)
            rows.append({"job": job, "match": record})
        directory = self.directory / "exports"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / f"jobs-{uuid.uuid4().hex[:10]}.{format}"
        if format == "json":
            content = json.dumps({
                "exported_at": now(), "preferences": state["preferences"],
                "run": next(r for r in self.storage.list("runs", self.sid)
                            if r["id"] == state["result_run"]),
                "results": rows,
            }, ensure_ascii=False, indent=2)
        elif format == "csv":
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow([
                "id", "title", "company", "city", "salary", "experience", "url", "source",
                "fetched_at", "imported_at", "level", "reasons", "gaps", "pending",
            ])
            for row in rows:
                j, r = row["job"], row["match"]
                cells = [
                    j["id"], j["title"], j["company"], j["city"], j["salary"]["raw"],
                    j["experience"], j["url"], j["source"], j["fetched_at"] or "unknown",
                    j["imported_at"] or "", r["level"], "；".join(r["reasons"]),
                    "；".join(r["gaps"]), "；".join(r["pending"]),
                ]
                writer.writerow([
                    "'" + str(c) if str(c).lstrip().startswith(("=", "+", "-", "@"))
                    else c for c in cells
                ])
            content = "\ufeff" + stream.getvalue()
        else:
            content = "# 岗位匹配结果\n\n" + f"导出时间：{now()}\n\n"
            for row in rows:
                j, r = row["job"], row["match"]
                title = j["title"].replace("\n", " ")
                content += f"## {title} · {j['company']} · {r['level']}\n\n"
                content += (
                    f"- ID：{j['id']}\n- 城市：{j['city'] or '未知'}\n"
                    f"- 薪资原文：{j['salary']['raw'] or '未知'}\n"
                    f"- 来源：{j['source']}，采集时间：{j['fetched_at'] or '未知'}\n"
                    f"- 链接：{j['url'] or '未知'}\n"
                )
                for label, key in (("依据", "reasons"), ("差距", "gaps"), ("待确认", "pending"),
                                   ("简历原文", "resume_quotes"), ("岗位原文", "job_quotes")):
                    content += f"- {label}：" + "；".join(r[key]).replace("\n", " ") + "\n"
                content += "\n"
        path.write_text(redact(content), encoding="utf-8")
        os.chmod(path, 0o600)
        return str(path)

    def delete(self):
        try:
            self.cache.purge()
        except Exception:
            self.storage.delete(self.sid)
            raise AgentError(
                "cache_delete_incomplete",
                "本地会话已删除，但 Redis 不可用，缓存可能残留至 TTL；恢复 Redis 后重试清理",
            ) from None
        self.storage.delete(self.sid)
