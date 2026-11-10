import json
from contextlib import contextmanager

import typer
from filelock import FileLock, Timeout
from javis_core.models import BrainError, ToolResult
from prompt_toolkit import PromptSession
from rich.prompt import Confirm

from .cli import console, display, errors
from .config import Settings
from .orchestrator import Javis

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
sessions = typer.Typer(no_args_is_help=True)
app.add_typer(sessions, name="sessions")
HELP = """Javis | Career Team + Rental Team
/javis consent                 同意路由与所选团队远程模型处理
/javis status                  团队与任务状态
/javis task ID pause|cancel     暂停或取消尚未结束的调度任务
/career /原bl命令              求职命令保持原语义
/career resume                 恢复暂停前的面试原题
/team-switch confirm|reject    确认或拒绝暂停面试
/rental requirements JSON      提交并展示新版本租房需求
/rental confirm 版本           人工确认该版本
/rental search                 按确认条件搜索，唯果优先、自如补充
/rental status|results         当前条件或最近结果
/rental authorize-location 版本 单独授权向高德发送必要位置
/rental geocode "地址"          查询候选位置，不自动选择
/rental route 来源 房源ID GCJ02  按人工确认的房源坐标查询路线
/share career-destination      预览已选岗位的最小目的地摘要
/share confirm                 人工确认跨域共享；仍须重确认租房需求
/quit                          退出；用同一 --session 恢复
"""


@contextmanager
def context(sid=None):
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(settings.data_dir / ".leader.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise BrainError("runtime_busy") from None
    service = None
    try:
        service = Javis(settings, sid)
        yield service
    finally:
        if service:
            service.close()
        lock.release()


def run(service, text):
    try:
        if text == "/help":
            return ToolResult(data={"help": HELP}).model_dump()
        return service.handle(text)
    except Exception as exc:
        return ToolResult(
            status="blocked" if isinstance(exc, BrainError) and exc.code in {
                "consent_required", "requirements_confirmation_required",
                "selected_destination_required", "rental_requirements_required",
            } else "failed",
            error=exc.code if isinstance(exc, BrainError) else type(exc).__name__,
            warnings=["未输出原始异常正文；未自动重试来源或放宽条件"],
        ).model_dump()


def show(result, json_output=False):
    display(result, json_output)
    if not json_output:
        for key in ("pending_action", "coverage"):
            if result.get(key):
                errors.print(key + ": " + json.dumps(result[key], ensure_ascii=False))


@app.command()
def chat(
    question: str | None = typer.Option(None, "--question", "-q"),
    session: str | None = typer.Option(None, "--session", "-s"),
    json_output: bool = typer.Option(False, "--json"),
    allow_remote: bool = False,
):
    """通用终端助手；搜索、地图、跨域共享有独立人工门。"""
    if json_output and question is None:
        raise typer.BadParameter("--json 需要 -q")
    try:
        with context(session) as service:
            if allow_remote:
                service.consent()
            if question is not None:
                result = run(service, question)
                result["data"] = {"session": service.sid, **result["data"]}
                show(result, json_output)
                if result["status"] in ("blocked", "failed"):
                    raise typer.Exit(1)
                return
            console.print("Javis | session " + service.sid)
            console.print("远程模型接收当前任务脱敏文本；房源请求仅发送已确认条件。地图与跨域共享另行授权。")
            if not service.store.state(service.sid).get("remote_allowed") and Confirm.ask(
                "允许远程模型处理？", default=False,
            ):
                service.consent()
            prompt = PromptSession()
            while True:
                try:
                    text = prompt.prompt("javis> ").strip()
                    if text in ("/quit", "/exit"):
                        return
                    if text:
                        show(run(service, text))
                except EOFError:
                    return
                except KeyboardInterrupt:
                    errors.print("已取消当前操作；检查点保留。")
    except typer.Exit:
        raise
    except Exception as exc:
        errors.print(getattr(exc, "code", type(exc).__name__) + "；启动失败")
        raise typer.Exit(1) from None


@app.command()
def doctor():
    """只读本地配置诊断；不触发模型或 MCP 请求，不消耗唯果额度。"""
    from rental_team.budget import DailyQuota
    from rental_team.config import Settings as RentalSettings
    settings = RentalSettings()
    console.print(json.dumps({
        "vigo_configured": bool(settings.vigo_api_key.get_secret_value()),
        "vigo_cap": settings.vigo_daily_limit,
        "vigo_local_usage": DailyQuota(settings.quota_path, settings.vigo_daily_limit).used(),
        "ziroom_configured": bool(settings.ziroom_python and settings.ziroom_python.is_file()),
        "chrome_exists": settings.chrome_path.is_file(),
        "amap_configured": bool(settings.amap_api_key.get_secret_value()),
        "network_requests": 0,
    }, ensure_ascii=False, indent=2))


@sessions.command("delete")
def delete_session(session: str, yes: bool = False):
    """删除对应主控/职业/租房会话，不删除共享知识库或每日额度台账。"""
    if not yes and not Confirm.ask("删除该会话及其职业、租房域数据？", default=False):
        return
    with context(session) as service:
        console.print(json.dumps(service.delete(), ensure_ascii=False, indent=2))
