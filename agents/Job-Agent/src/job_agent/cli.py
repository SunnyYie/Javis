from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated

import typer
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from .config import Settings
from .models import AgentError
from .service import Service
from .storage import Storage

app = typer.Typer(no_args_is_help=True, help="只读岗位搜索与简历匹配 Agent")
resume_app = typer.Typer(no_args_is_help=True)
jobs_app = typer.Typer(no_args_is_help=True)
sessions_app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
app.add_typer(resume_app, name="resume")
app.add_typer(jobs_app, name="jobs")
app.add_typer(sessions_app, name="sessions")
app.add_typer(auth_app, name="auth")
console = Console(highlight=False, markup=False)
SessionOption = Annotated[str | None, typer.Option("--session", "-s")]


def output(value):
    console.print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def fail(exc):
    if isinstance(exc, AgentError):
        console.print(f"错误 [{exc.code}]：{exc.message}")
    else:
        console.print("错误 [internal_error]：执行失败；未输出可能含凭证的异常正文")
    raise typer.Exit(1)


def make_service(session, allow_remote=False) -> Service:
    service = Service(Settings(), session, emit=console.print)
    if allow_remote:
        service.consent()
    console.print("会话：" + service.sid)
    return service


def snapshot(service):
    output(service.snapshot(presented=True))
    if not service.snapshot()["confirmed"]:
        console.print("请核对以上摘要和条件；补齐后输入 /confirm 或使用 search --confirm。")
    for warning in sorted(service.cache.warnings):
        console.print("警告：" + warning)


def show_results(service):
    state = service.state()
    jobs = {j["id"]: j for j in service.result_jobs()}
    results = service.results()
    if not results:
        return
    table = Table(title="本次评估的岗位原始字段")
    for name in ("ID", "岗位 / 公司", "城市", "薪资原文", "结论", "来源 / 采集时间"):
        table.add_column(name, overflow="fold")
    order = {"满足条件": 0, "可尝试": 1, "信息不足": 2, "不符合": 3, "评估失败": 4}
    for result in sorted(results, key=lambda r: order[r["level"]]):
        job = jobs[result["job_id"]]
        table.add_row(
            job["id"], f"{job['title']}\n{job['company']}", job["city"] or "未知",
            job["salary"]["raw"] or "未知", result["level"],
            f"{job['source']}\n{job['fetched_at'] or '未知'}",
        )
    console.print(table)
    console.print(f"结果任务：{state.get('result_run', '')}；/detail ID 查看链接与证据。")


@resume_app.command("parse")
def parse_resume(
    path: Path,
    session: SessionOption = None,
    force_ocr: bool = False,
    extract_only: bool = False,
    allow_remote: bool = False,
):
    """解析简历；--extract-only 只做本地提取，不调用模型。"""
    try:
        service = make_service(session, allow_remote)
        approved = service.authorize_file(path, "resume")
        output(service.parse_resume(str(approved), force_ocr, extract_only))
        if not extract_only:
            snapshot(service)
        for warning in sorted(service.cache.warnings):
            console.print("警告：" + warning)
    except Exception as exc:
        fail(exc)


@jobs_app.command("import")
def jobs_import(path: Path, session: SessionOption = None):
    """导入 ohmyboss JSON 或 CSV，不调用模型或访问网站。"""
    try:
        service = make_service(session)
        approved = service.authorize_file(path, "jobs")
        output(service.import_file(str(approved)))
    except Exception as exc:
        fail(exc)


@auth_app.command("import-cookies")
def auth_import(path: Path, session: SessionOption = None):
    """显式导入用户提供的 BOSS Cookie；不调用模型，不验证登录。"""
    from .cookies import import_cookies

    try:
        service = make_service(session)
        output(import_cookies(path, service.directory, service.settings.data_dir))
    except Exception as exc:
        fail(exc)


@auth_app.command("check")
def auth_check(session: Annotated[str, typer.Option("--session", "-s")]):
    """只访问 BOSS 首页验证登录；不搜索、发消息或投递。"""
    from .boss import BossProvider
    from .cookies import save_private_state

    try:
        service = make_service(session)
        report = BossProvider(service.settings, service.directory, console.print).check_login()
        save_private_state(service.directory / "auth-check.json", report)
        output(report)
        if not report["login_verified"]:
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(exc)


@app.command()
def search(
    session: SessionOption = None,
    roles: Annotated[list[str] | None, typer.Option("--role")] = None,
    cities: Annotated[list[str] | None, typer.Option("--city")] = None,
    salary: Annotated[float | None, typer.Option("--salary")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    confirm: bool = False,
    allow_remote: bool = False,
    limit: int = 30,
    expand_limit: bool = False,
):
    """查询实时岗位。薪资单位为税前月薪 K；先展示条件，--confirm 才执行。"""
    try:
        service = make_service(session, allow_remote)
        changes = {
            k: v for k, v in {
                "roles": roles, "cities": cities, "target_monthly_k": salary,
                "excluded_terms": exclude,
            }.items() if v is not None
        }
        service.update_preferences(**changes)
        snapshot(service)
        if not confirm:
            console.print("未开始采集。核对后使用相同会话和 --confirm。")
            return
        service.confirm()
        run = service.search(limit, expand_limit)
        output(run)
        if run["job_ids"]:
            output(service.match(run["id"]))
            show_results(service)
        if run["status"] in ("failed", "partial"):
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(exc)


@app.command("match")
def match_existing(session: SessionOption = None, run: str | None = None, confirm: bool = False):
    """匹配已获取/导入的岗位，不重新访问招聘网站。"""
    try:
        service = make_service(session)
        snapshot(service)
        if confirm:
            service.confirm()
        output(service.match(run))
        show_results(service)
    except Exception as exc:
        fail(exc)


@app.command("export")
def export_results(session: SessionOption = None, format: str = "json"):
    """导出 JSON / CSV / Markdown，输出仅写入本会话目录。"""
    try:
        service = make_service(session)
        console.print(service.export(format))
    except Exception as exc:
        fail(exc)


@sessions_app.command("list")
def list_sessions():
    output(Storage(Settings().data_dir).sessions())


@sessions_app.command("delete")
def delete_session(session: str, yes: bool = False):
    """删除会话、简历副本、登录态、快照、导出和该会话缓存，不删除原始输入文件。"""
    if not yes and not Confirm.ask("确认删除该会话的所有本地数据和 Redis 缓存？", default=False):
        return
    try:
        service = Service(Settings(), session)
        service.delete()
        console.print("会话数据已删除。原始输入文件未修改。")
    except Exception as exc:
        fail(exc)


@app.command()
def doctor(probe_model: bool = False):
    """检查配置、OCR、浏览器和 Redis；--probe-model 才调用远程模型。"""
    try:
        import importlib.metadata

        import redis
        from playwright.sync_api import sync_playwright

        settings = Settings()
        options = settings.model_options()
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1,
        )
        try:
            redis_ok = bool(client.ping())
        except redis.RedisError:
            redis_ok = False
        with sync_playwright() as pw:
            browser_present = Path(pw.chromium.executable_path).exists()
        report = {
            "model": options["model"], "model_configured": bool(options["api_key"]),
            "model_source": str(settings.llm_env_file) if settings.llm_env_file else "project/env",
            "redis": redis_ok, "browser_installed": browser_present,
            "browser_channel": settings.browser_channel or "bundled Chromium",
            "rapidocr": importlib.metadata.version("rapidocr"),
            "onnxruntime": importlib.metadata.version("onnxruntime"),
            "data_dir": str(settings.data_dir.resolve()),
            "privacy": "OCR 本地运行；模型会接收脱敏后的职业信息，普通 doctor 不调用模型",
        }
        if probe_model:
            from pydantic import BaseModel

            from .llm import Models

            class Probe(BaseModel):
                ok: bool

            report["structured_model_probe"] = Models(settings).structured(
                Probe, "这是结构化输出测试，返回 ok=true。", "不包含简历或用户资料。"
            ).ok
        output(report)
    except Exception as exc:
        fail(exc)


HELP = """命令：
/resume 路径 [--force-ocr]   授权并解析简历
/import 路径                授权并导入岗位 JSON/CSV
/prefs JSON                 直接更新求职条件，也可用自然语言
/status                     查看并核对当前摘要与条件
/confirm                    确认当前摘要与条件，不自动查询
/search                     查询 BOSS 并匹配
/match                      重新匹配已获取岗位
/detail ID                  查看岗位链接、原文及匹配依据
/export json|csv|md         导出当前结果
/new                        新建独立会话
/help                       查看命令
/quit                       退出
其他输入进入模型连续对话。Ctrl-C 中断当前操作，已采集数据保留。"""


@app.command()
def chat(session: SessionOption = None, allow_remote: bool = False):
    """启动可恢复的终端对话。"""
    from .agent import build_agent, chat_turn

    service = make_service(session, allow_remote)
    if not service.state()["remote_allowed"]:
        console.print(
            "OCR 在本地运行；后续对话和脱敏职业内容会发送至配置的远程模型。"
            "联系方式不用于岗位匹配；模型不会收到原始 PDF/图片。"
        )
        if not Confirm.ask("允许当前会话调用该远程模型？", default=False):
            return
        service.consent()
    console.print(HELP)
    snapshot(service)
    prompt = PromptSession()
    agent = None
    while True:
        try:
            text = prompt.prompt("你> ").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            continue
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        try:
            parts = shlex.split(text) if text.startswith("/") else []
            command = parts[0] if parts else ""
            if command == "/help":
                console.print(HELP)
            elif command == "/status":
                snapshot(service)
            elif command == "/confirm":
                service.confirm()
                console.print("已确认。输入 /search 查询，或 /match 匹配已有岗位。")
            elif command == "/resume":
                if len(parts) < 2:
                    raise AgentError("path_required", "请输入 /resume 文件路径")
                path = service.authorize_file(Path(parts[1]), "resume")
                output(service.parse_resume(str(path), "--force-ocr" in parts))
                snapshot(service)
            elif command == "/import":
                if len(parts) != 2:
                    raise AgentError("path_required", "请输入 /import 文件路径")
                path = service.authorize_file(Path(parts[1]), "jobs")
                output(service.import_file(str(path)))
            elif command == "/prefs":
                values = json.loads(text.partition(" ")[2])
                if not isinstance(values, dict):
                    raise AgentError("invalid_preferences")
                output(service.update_preferences(**values))
                snapshot(service)
            elif command == "/search":
                service.collection_used = False
                run = service.search()
                output(run)
                if run["job_ids"]:
                    output(service.match(run["id"]))
                    show_results(service)
            elif command == "/match":
                output(service.match())
                show_results(service)
            elif command == "/detail":
                output(service.detail(parts[1]))
            elif command == "/export":
                console.print(service.export(parts[1] if len(parts) > 1 else "json"))
            elif command == "/new":
                service = make_service(None)
                service.consent()
                agent = None
                snapshot(service)
            elif command:
                console.print("未知命令，输入 /help 查看。")
            else:
                agent = agent or build_agent(service)
                console.print("助手> " + chat_turn(agent, service, text))
                snapshot(service)
                if not service.snapshot()["results_stale"]:
                    show_results(service)
            for warning in sorted(service.cache.warnings):
                console.print("警告：" + warning)
        except KeyboardInterrupt:
            console.print("当前操作已中断；已保存的简历、岗位和工具操作保留。")
            agent = None
        except AgentError as exc:
            console.print(f"错误 [{exc.code}]：{exc.message}")
        except Exception:
            console.print("操作失败，未提交未完成的对话。输入 /status 检查状态。")
            agent = None


if __name__ == "__main__":
    app()
