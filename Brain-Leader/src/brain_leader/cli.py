import json
import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from filelock import FileLock, Timeout
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.prompt import Confirm

from .agent import chat_turn
from .config import Settings
from .daily_cli import app as daily_app
from .models import BrainError, Probe, ToolResult, private
from .service import Service
from .storage import Store

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
sessions = typer.Typer(no_args_is_help=True)
app.add_typer(sessions, name="sessions")
app.add_typer(daily_app, name="daily")
console = Console(markup=False, highlight=False, soft_wrap=True)
errors = Console(stderr=True, markup=False, highlight=False, soft_wrap=True)
Session = Annotated[str | None, typer.Option("--session", "-s")]
HELP = """bl 命令
/consent                         同意远程模型处理脱敏内容
/resume 路径 [--force-ocr]         授权并解析简历
/facts                          查看事实
/facts add 分类 真实内容           分类 summary/skills/work/projects/education/achievements
/facts confirm ID               确认候选；/facts reject ID 拒绝候选
/draft                          编排简历及诊断
/adopt ID                       采纳简历版本
/contact JSON                   仅本地联系方式 name/phone/email/city/link
/prefs JSON                     设置 roles/cities/target_monthly_k/excluded_terms
/status /confirm                查看摘要，确认当前条件
/import 路径                    授权导入岗位 JSON/CSV
/search /match                  实时搜索或重匹配
/jobs /detail 岗位ID             查看保存的岗位、批次、匹配依据
/target 岗位ID [批次ID]           固定目标岗位
/questions 主题 [--count N]       有来源的题单
/knowledge 问题                 知识解释
/links [主题] /learn [主题]        关联或学习计划
/interview [模式] [题量] [追问数]  technical/project/behavioral/mixed
/answer TOKEN 回答               带 token 的幂等作答；也可直接输入回答
/pause /continue /finish         暂停、恢复、明确结束
/transcript 路径                 授权导入面试记录
/review [记录ID]                 复盘已结束面试或导入记录
/memory                         查看确认记忆与候选
/memory add 类型 内容             fact/preference/weakness
/memory confirm ID              确认跨会话保存
/memory delete ID               删除候选与已确认记忆
/memory correct ID 内容          删除旧记忆并提出待确认修订
/export resume [ID] [preview]    Markdown、DOCX、PDF
/export jobs [json|csv|md]       岗位结果
/export review [ID]             复盘 JSON 与 Markdown
/tasks /done ID                 查看任务或标记完成
/show 记录ID                    查看本会话简历、题单、知识回答等产物
/quit                           退出；使用 bl chat --session ID 恢复
"""
CONSENT = (
    "远程模型会接收脱敏职业内容、对话和相关证据；联网检索会发送技术检索词。"
    "OCR 本地运行，原始简历和联系方式不直接上传。规则脱敏并不完备，请优先使用脱敏副本。"
    "会话在本机保存，跨会话记忆须另行确认。"
)


@contextmanager
def service_context(session=None):
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(settings.data_dir / ".leader.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise BrainError("runtime_busy", "同一运行目录已有 Brain-Leader，暂不支持并发") from None
    service = None
    try:
        service = Service(settings, session, emit=lambda text: errors.print(text))
        yield service
    finally:
        if service:
            service.close()
        lock.release()


def command(service, text):
    if not text.startswith("/"):
        return chat_turn(service, text)
    parts = shlex.split(text)
    name, args = parts[0], parts[1:]
    interview = service.interviews.current()
    if interview and interview["status"] != "finished" and name not in {
        "/answer", "/pause", "/continue", "/finish", "/status", "/help", "/quit",
    }:
        raise BrainError("interview_in_progress", "面试期间请先作答、暂停、恢复或结束")
    if name == "/help":
        return {"help": HELP}
    if name == "/consent":
        return service.consent()
    if name == "/status":
        return service.snapshot(presented=True)
    if name == "/confirm":
        service.adapters.job.confirm()
        return service.adapters.job.snapshot()
    if name == "/resume" and args:
        return service.import_resume(args[0], "--force-ocr" in args)
    if name == "/import" and args:
        approved = service.authorize(args[0], "jobs")
        return service.adapters.job.import_file(str(approved))
    if name == "/prefs" and args:
        return service.preferences(**json.loads(text.split(maxsplit=1)[1]))
    if name == "/contact" and args:
        contacts = json.loads(text.split(maxsplit=1)[1])
        if not isinstance(contacts, dict) or not set(contacts) <= {
            "name", "phone", "email", "city", "link",
        } or any(not isinstance(v, str) or len(v) > 300 for v in contacts.values()):
            raise BrainError("invalid_contacts")
        state = service.state()
        state["contacts"] = contacts
        service.store.save(service.sid, state)
        return {"contacts_saved_locally": list(contacts)}
    if name == "/facts":
        if not args:
            return {"facts": service.store.records(service.sid, "fact")}
        if args[0] == "add" and len(args) >= 3:
            return service.resumes.add_fact(
                args[1], " ".join(args[2:]), confirmed=True, source={"type": "explicit_user_command"},
            )
        if args[0] == "confirm" and len(args) == 2:
            return service.resumes.confirm(args[1])
        if args[0] == "reject" and len(args) == 2:
            fact = service.store.get(service.sid, args[1], "fact")
            if fact["confirmed"]:
                raise BrainError("confirmed_fact_immutable", "已确认事实请新增修订，不覆盖旧版本")
            fact["rejected"] = True
            return service.store.put(
                service.sid, "fact", fact, record_id=fact["id"], mutable=True,
            )
    if name == "/draft":
        return service.resumes.generate()
    if name == "/adopt" and len(args) == 1:
        return service.resumes.adopt(args[0])
    if name == "/search":
        service.adapters.job.collection_used = False
        return service.adapters.job.search()
    if name == "/match":
        return service.adapters.job.match(args[0] if args else None)
    if name == "/jobs":
        return service.list_jobs()
    if name == "/detail" and len(args) == 1:
        return service.adapters.job.detail(args[0])
    if name == "/show" and len(args) == 1:
        return service.record_detail(args[0])
    if name == "/target" and args:
        return service.select_job(args[0], args[1] if len(args) > 1 else None)
    if name == "/questions" and args:
        count = 5
        if "--count" in args:
            index = args.index("--count")
            count = int(args[index + 1])
            args = args[:index] + args[index + 2:]
        return service.questions(" ".join(args), count)
    if name == "/knowledge" and args:
        return service.explain(" ".join(args))
    if name == "/links":
        return {"links": service.store.links(service.sid, " ".join(args))}
    if name == "/learn":
        return service.learning_plan(" ".join(args))
    if name == "/interview":
        return service.interviews.prepare(
            args[0] if args else "mixed", int(args[1]) if len(args) > 1 else 5,
            int(args[2]) if len(args) > 2 else 2,
        )
    if name == "/answer" and len(args) >= 2:
        return service.interviews.answer(args[0], " ".join(args[1:]))
    if name in ("/pause", "/continue", "/finish"):
        return service.interviews.control(
            {"/pause": "pause", "/continue": "resume", "/finish": "finish"}[name],
        )
    if name == "/transcript" and len(args) == 1:
        record = service.authorize(args[0], "transcript")
        return {"id": record["id"], "imported": True}
    if name == "/review":
        return service.review(args[0] if args else None)
    if name == "/memory":
        if not args:
            return {"confirmed": service.memory.all(), "candidates": service.state()["candidate_memory"]}
        if args[0] == "add" and len(args) >= 3:
            return service.propose_memory(args[1], " ".join(args[2:]))
        if args[0] == "confirm" and len(args) == 2:
            return service.confirm_memory(args[1])
        if args[0] in ("delete", "correct") and len(args) >= 2:
            if args[0] == "correct" and len(args) < 3:
                raise BrainError("missing_correction")
            all_memories = service.memory.all() + service.state()["candidate_memory"]
            item = next((m for m in all_memories if m["id"] == args[1]), None)
            if item is None:
                raise BrainError("unknown_memory")
            service.memory.delete(args[1])
            state = service.state()
            state["candidate_memory"] = [m for m in state["candidate_memory"] if m["id"] != args[1]]
            service.store.save(service.sid, state)
            if args[0] == "correct":
                return service.propose_memory(item["kind"], " ".join(args[2:]))
            return {"deleted": args[1]}
    if name == "/tasks":
        return {"tasks": service.state()["tasks"]}
    if name == "/done" and len(args) == 1:
        state = service.state()
        task = next((t for t in state["tasks"] if t["id"] == args[0]), None)
        if not task:
            raise BrainError("unknown_task")
        task["status"] = "done"
        service.store.save(service.sid, state)
        return task
    if name == "/export" and args:
        if args[0] == "resume":
            draft_id = next((a for a in args[1:] if a != "preview"), None)
            return {"artifacts": service.resumes.export(draft_id, "preview" in args)}
        if args[0] == "jobs":
            return {"artifacts": [{"path": service.adapters.job.export(args[1] if len(args) > 1 else "json")}]}
        if args[0] == "review":
            review = service.review(args[1] if len(args) > 1 else None)
            directory = service.directory / "exports"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / f"{review['id']}.json"
            service.write_private(path, json.dumps(review, ensure_ascii=False, indent=2))
            md = path.with_suffix(".md")
            service.write_private(md, "# 面试复盘\n\n" + "\n\n".join(
                f"## {key}\n\n{json.dumps(value, ensure_ascii=False, indent=2)}"
                for key, value in review.items()
            ))
            return {"artifacts": [{"path": str(path)}, {"path": str(md)}]}
    raise BrainError("invalid_command", "命令或参数无效；/help 查看格式")


def run(service, text):
    action = text.split(maxsplit=1)[0] if text.startswith("/") else "chat"
    with service.telemetry.request(action) as metrics:
        result = _run(service, text)
        metrics["status"] = result["status"]
    if service.telemetry.mode != "off" and service.telemetry.last:
        result["trace"] = dict(service.telemetry.last)
    return result


def _run(service, text):
    try:
        service.calls = 0
        action = text.split(maxsplit=1)[0] if text.startswith("/") else "chat"
        service.store.event(service.sid, action, "started")
        result = command(service, text)
        status = "ok"
        if (
            result.get("sufficient") is False or result.get("persisted") is False
            or result.get("status") == "partial"
        ):
            status = "partial"
        if result.get("status") == "failed":
            status = "failed"
        if result.get("source_status") in ("failed", "partial", "interrupted"):
            status = "partial" if result.get("results") else "failed"
        operations = result.get("operations", [])
        if any(o["status"] != "ok" for o in operations):
            status = "partial" if any(o["status"] == "ok" for o in operations) else "failed"
        service.store.event(service.sid, action, status, {"record_id": result.get("id")})
        return ToolResult(
            status=status, data=result, citations=result.get("citations", []),
            artifacts=result.get("artifacts", []),
            warnings=result.get("warnings", []) + result.get("issues", [])
            + result.get("source_issues", []),
        ).model_dump()
    except Exception as exc:
        code = getattr(exc, "code", getattr(exc, "category", type(exc).__name__))
        service.store.event(service.sid, "command", "failed", {"code": code})
        message = exc.message if isinstance(exc, BrainError) else "执行失败；未输出原始异常正文"
        return ToolResult(
            status="failed", error=code, warnings=[private(message)],
        ).model_dump()


def display(result, json_output=False):
    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    data = result["data"]
    if "text" in data and "operations" in data:
        console.print(data["text"])
        for operation in data.get("operations", []):
            if operation["status"] != "ok":
                errors.print(f"{operation['tool']}: {operation['status']} {operation['error'] or ''}")
    elif "help" in data:
        console.print(data["help"])
    else:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    for warning in result["warnings"]:
        errors.print("警告：" + warning)
    if result["error"]:
        errors.print("错误：" + result["error"])
    if result.get("trace") and not json_output:
        trace = result["trace"]
        errors.print(f"调用链：{trace['trace_id']} | {trace['upload']} | {trace['path']}")


@app.command()
def chat(
    question: Annotated[str | None, typer.Option("--question", "-q")] = None,
    session: Session = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    allow_remote: bool = False,
    local_only: bool = False,
    trace_local: bool = False,
    allow_trace_upload: bool = False,
):
    """连续对话或单次请求。--local-only 限制资料检索，不代表 LLM 离线。"""
    if json_output and question is None:
        errors.print("--json 需要 -q")
        raise typer.Exit(2)
    try:
        with service_context(session) as service:
            if trace_local:
                service.telemetry.mode = "local"
            service.telemetry.upload_allowed = allow_trace_upload
            if service.telemetry.mode == "langsmith":
                if question is None and not allow_trace_upload:
                    service.telemetry.upload_allowed = Confirm.ask(
                        "允许向配置的 LangSmith 上传节点、耗时、状态、Token 数？"
                        "不含提示词、简历、作答和原始异常正文", default=False,
                    )
                if service.telemetry.status() != "ready":
                    errors.print(
                        "追踪仅保留本地；LangSmith：" + service.telemetry.status(),
                    )
            if local_only:
                service.settings.local_only = True
            if allow_remote:
                service.consent()
            if question is not None:
                result = run(service, question)
                result["data"] = {"session": service.sid, **result["data"]}
                display(result, json_output)
                if result["status"] in ("failed", "blocked"):
                    raise typer.Exit(1)
                return
            console.print("Brain-Leader | session " + service.sid)
            console.print(CONSENT)
            if not service.state()["remote_allowed"] and Confirm.ask("允许远程处理？", default=False):
                service.consent()
            prompt = PromptSession()
            while True:
                try:
                    text = prompt.prompt("bl> ").strip()
                    if text in ("/quit", "/exit"):
                        return
                    if text:
                        display(run(service, text))
                except EOFError:
                    return
                except KeyboardInterrupt:
                    errors.print("已取消当前操作；已完成步骤保留，未完成回答不保存。")
    except typer.Exit:
        raise
    except Exception as exc:
        errors.print(getattr(exc, "code", type(exc).__name__) + "；启动失败，未显示原始异常")
        raise typer.Exit(1) from None


@app.command()
def doctor(probe_model: bool = False):
    """无模型费用的配置/服务检查；--probe-model 显式调用真实模型。"""
    import urllib.request

    from javis_tools.adapters import Assistants

    settings = Settings()
    adapters = Assistants(settings, "doctor")
    fields = settings.model_fields_for_children()
    report = {
        "model": fields["llm_model"], "model_configured": bool(fields["llm_api_key"]),
        "model_source_exists": settings.llm_env_file.is_file(),
        "data_dir": str(settings.data_dir), "memory_dir": str(settings.memory_dir),
        "skills": [name for name in ("profile", "resume", "jobs", "prepare", "interview", "review")
                   if (settings.skills_dir / name / "SKILL.md").is_file()],
        "shared_corpus_warning": "单用户串行；不要与 ka/iqb 的 chat、ingest、sync 同时运行",
        "boss_acceptance": "登录后实时搜索尚未通过独立验收",
        "tracing": {
            "mode": settings.trace_mode, "project": settings.langsmith_project,
            "api_key_configured": bool(settings.langsmith_api_key.get_secret_value()),
            "privacy": "metadata_only",
            "upload_requires_separate_consent": True,
            "probe_performed": False,
        },
    }
    for kind in ("knowledge", "questions"):
        s = adapters.knowledge_settings(kind)
        report[kind] = {
            "data_exists": (s.data_dir / "knowledge.sqlite3").is_file(),
            "namespace": s.index_namespace, "data_dir": str(s.data_dir),
        }
        try:
            import redis
            client = redis.Redis.from_url(
                s.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1,
            )
            report[kind]["redis"] = bool(client.ping())
            client.close()
        except Exception:
            report[kind]["redis"] = False
        try:
            with urllib.request.urlopen(s.milvus_uri.replace("19531", "19091").replace(
                "19532", "19092",
            ) + "/healthz", timeout=2) as response:
                report[kind]["milvus_health"] = response.status == 200
        except Exception:
            report[kind]["milvus_health"] = False
    if probe_model:
        from .llm import Models
        report["structured_probe"] = Models(settings).structured(
            Probe, "返回 ok=true，这是不含个人资料的结构化探针。", {},
        ).ok
    console.print(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("trace-report")
def trace_report(path: Path):
    """汇总本地 metadata-only 调用链；不调用模型或上传数据。"""
    from javis_tools.tracing import summarize

    console.print(json.dumps(
        summarize(json.loads(path.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2,
    ))


@sessions.command("list")
def list_sessions():
    console.print(json.dumps(Store(Settings().data_dir).sessions(), ensure_ascii=False, indent=2))


@app.command("isolate-corpora")
def isolate_corpora(output: Path):
    """显式复制现有语料及向量到新目录/集合，供真实验收；不调用 LLM。"""
    from javis_tools.adapters import Assistants
    from javis_tools.isolation import clone_corpus

    if output.exists():
        raise typer.BadParameter("请使用全新输出目录")
    settings = Settings()
    adapters = Assistants(settings, "isolation")
    output = output.expanduser().resolve()
    manifest = {}
    for kind in ("knowledge", "questions"):
        manifest[kind] = clone_corpus(adapters.knowledge_settings(kind), output / kind, kind)
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(json.dumps(manifest, ensure_ascii=False, indent=2))


@sessions.command("delete")
def delete_session(session: str, yes: bool = False):
    """删除本会话、其确认记忆及岗位缓存，不删除原始输入或共享知识库。"""
    if not yes and not Confirm.ask("删除本会话及其跨会话记忆？", default=False):
        return
    settings = Settings()
    if session not in {s["id"] for s in Store(settings.data_dir).sessions()}:
        errors.print("unknown_session")
        raise typer.Exit(1)
    with service_context(session) as service:
        console.print(json.dumps(service.delete(), ensure_ascii=False, indent=2))
