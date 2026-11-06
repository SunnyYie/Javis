import json
import time
from typing import Annotated

import typer
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.table import Table

from .agent import Assistant
from .bank_cli import app as bank_app
from .collectors import Collectors, repository
from .config import ROOT, get_settings
from .models import ServiceError
from .retrieval import Knowledge
from .storage import Store

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(bank_app, name="bank")
console = Console()
errors = Console(stderr=True)
STAGES = {
    "cache": "缓存查询",
    "parse": "理解问题与上下文",
    "retrieve": "本地检索",
    "assess": "证据判断",
    "online": "联网补充",
    "refresh": "刷新过期来源",
    "ingest": "资料入库",
    "generate": "生成并核验题目",
    "verify": "核验答案与原始证据",
    "revise": "根据核验反馈修订一次",
    "save": "保存题目并更新热点",
}


@app.command()
def evaluate(
    output: Annotated[str, typer.Option("--output")],
    dataset: Annotated[str, typer.Option("--dataset")] = str(ROOT / "evals/interview_rag.json"),
    score_only: Annotated[bool, typer.Option("--score-only")] = False,
    exercises: Annotated[bool, typer.Option("--exercises/--no-exercises")] = True,
):
    """隔离复制当前真实题库，执行 CLI E2E 与 RAGAs；会调用已配置的收费模型。"""
    from .evaluation import evaluate as run

    try:
        report = run(get_settings(), dataset, output, score_only=score_only, exercises=exercises)
        console.print_json(data=report)
        if not report["complete"]:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(exc)


def fail(exc):
    if isinstance(exc, ServiceError):
        errors.print(str(exc), style="red", markup=False)
    elif isinstance(exc, (ValueError, RuntimeError)):
        # Only our known actionable messages are emitted, never raw provider exceptions.
        message = str(exc)
        if isinstance(exc, ValueError) or message.startswith(
            ("LLM_API_KEY", "Embedding changed", "Index namespace changed", "Unknown session")
        ):
            errors.print(message, style="red", markup=False)
        else:
            errors.print(
                f"操作失败 ({type(exc).__name__})；可运行 iqb doctor 检查配置。", style="red"
            )
    else:
        errors.print(f"操作失败 ({type(exc).__name__})；未输出可能含凭证的异常详情。", style="red")
    raise typer.Exit(1)


def render_sources(answer):
    roles = {
        "project": "项目自身资料",
        "third_party": "第三方分析",
        "tutorial": "教程",
        "unverified": "来源性质未核验",
    }
    console.print()
    for c in answer.citations:
        stale = "；已过期" if c["expires_at"] <= time.time() else ""
        console.print(
            f"[{c['number']}] {roles.get(c['role'], c['role'])}{stale} | {c['url']}",
            markup=False,
        )
    console.print(
        f"来源路径: {answer.route} | 证据充分: {answer.sufficient} | 已保存: {answer.persisted}",
        style="dim",
    )
    console.print(
        f"本窗口成功请求: {answer.hot_count} | 已晋升热点: {answer.cache_promoted}",
        style="dim",
    )
    for warning in answer.warnings:
        console.print(warning, style="yellow", markup=False)


@app.command()
def chat(
    question: Annotated[str | None, typer.Option("--question", "-q")] = None,
    session: Annotated[str | None, typer.Option("--session")] = None,
    local_only: Annotated[bool, typer.Option("--local-only")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """连续对话；--question 单次提问；--local-only 仅限制外部检索，LLM 仍联网。"""
    s = get_settings()
    store = Store(s.data_dir)
    if session and session not in store.sessions():
        fail(RuntimeError("Unknown session. Use /sessions to list saved sessions."))
    sid = session or store.new_session()
    streamed = [False]

    def stage(name):
        if not json_output:
            if streamed[0]:
                console.print()
                streamed[0] = False
            console.print(STAGES.get(name, name), style="dim", markup=False)

    def emit(text):
        if not json_output:
            console.print(text, end="", markup=False, highlight=False)
            streamed[0] = True

    assistant = Assistant(s, store=store, stage=stage, emit=emit)
    last = None

    def ask(text):
        nonlocal last
        last = assistant.ask(text, store.history(sid), local_only)
        store.add_turn(sid, text, json.dumps(last.to_dict(), ensure_ascii=False))
        if json_output:
            console.print_json(data=dict(last.to_dict(), session=sid))
        else:
            if streamed[0]:
                console.print()
                streamed[0] = False
            render_sources(last)

    if question is not None:
        try:
            ask(question)
        except KeyboardInterrupt:
            errors.print("已取消；未保存本次会话回答。")
            raise typer.Exit(130)
        except Exception as exc:
            fail(exc)
        return
    if json_output:
        errors.print("--json requires --question")
        raise typer.Exit(2)
    console.print(f"Interview Question Bank | session {sid}", style="bold")
    prompt = PromptSession()
    while True:
        try:
            text = prompt.prompt("iqb> ").strip()
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                return
            if text == "/help":
                console.print("/new /sessions /resume ID /sources /quit", markup=False)
            elif text == "/new":
                sid, last = store.new_session(), None
                console.print(f"session {sid}")
            elif text == "/sessions":
                console.print("\n".join(store.sessions()), markup=False)
            elif text.startswith("/resume "):
                target = text.split(maxsplit=1)[1]
                if target not in store.sessions():
                    console.print("Unknown session", style="yellow")
                else:
                    sid, last = target, None
                    console.print(f"session {sid}")
            elif text == "/sources":
                render_sources(last) if last else show_sources(store)
            elif text.startswith("/"):
                console.print("Unknown command; /help", style="yellow")
            else:
                try:
                    ask(text)
                except KeyboardInterrupt:
                    console.print("\n已取消；未保存本次会话回答。", style="yellow")
                except Exception as exc:
                    if isinstance(exc, ServiceError):
                        console.print(str(exc), style="red", markup=False)
                    else:
                        console.print(f"请求失败 ({type(exc).__name__})", style="red")
        except EOFError:
            return
        except KeyboardInterrupt:
            console.print()


def ingest_one(url, role="unverified"):
    s = get_settings()
    store = Store(s.data_dir)
    try:
        bundle = Collectors(s).collect(url, role=role)
        result = Knowledge(s, store).ingest(bundle)
        console.print_json(
            data={
                "source": bundle.source,
                **result,
                "files": len(bundle.documents),
                "partial": bundle.coverage["partial"],
            }
        )
    except Exception as exc:
        category = exc.category if isinstance(exc, ServiceError) else type(exc).__name__
        store.event(url, category)
        fail(exc)


@app.command()
def ingest(url: str):
    """导入公开 GitHub 仓库或公开网页，不执行资料中的代码。"""
    ingest_one(url)


@app.command()
def sync(repo: Annotated[str | None, typer.Argument()] = None):
    """同步已收录来源；仓库参数可用 owner/repo 或 URL。"""
    s = get_settings()
    store = Store(s.data_dir)
    rows = store.sources()
    if repo:
        canonical = f"https://github.com/{repository(repo)}" if repository(repo) else repo
        rows = [r for r in rows if r["source"] == canonical]
        if not rows:
            errors.print("未收录该来源，请先 iqb ingest URL。")
            raise typer.Exit(1)
    failures = 0
    for row in rows:
        chunks = [c for c in store.active_chunks() if c.source == row["source"]]
        try:
            ingest_one(row["source"], chunks[0].role if chunks else "unverified")
        except typer.Exit:
            failures += 1
    if failures:
        raise typer.Exit(1)


def show_sources(store, detailed=False):
    rows = store.sources()
    if detailed:
        console.print_json(data=[dict(r, coverage=json.loads(r["coverage"])) for r in rows])
        return
    table = Table("来源", "文件", "覆盖", "状态")
    for row in rows:
        coverage = json.loads(row["coverage"])
        stale = "过期" if row["expires_at"] <= time.time() else row["status"]
        table.add_row(
            row["source"],
            str(len(coverage.get("selected", []))),
            "局部" if coverage.get("partial") else "正文",
            stale,
        )
    console.print(table)


@app.command()
def sources(detailed: Annotated[bool, typer.Option("--json")] = False):
    """列出来源；--json 包含已采集和跳过的文件及原因。"""
    show_sources(Store(get_settings().data_dir), detailed)


@app.command()
def hot():
    """列出当前语料库已达到晋升阈值的热点请求及窗口剩余秒数。"""
    from .cache import Cache

    s = get_settings()
    try:
        console.print_json(data=Cache(s).hot(Store(s.data_dir).corpus_id))
    except Exception as exc:
        fail(exc)


@app.command()
def doctor(
    probe_models: Annotated[bool, typer.Option("--probe-models")] = False,
):
    """检查服务；--probe-models 额外执行真实 LLM / Embedding 探测，会产生费用。"""
    import redis
    from pymilvus import MilvusClient

    s = get_settings()
    checks = {"LLM model": s.llm_model}
    try:
        configured = bool(s.model_settings()["api_key"])
        checks["LLM configured"] = configured
    except Exception:
        checks["LLM configured"] = False
    checks["GitHub token configured"] = bool(s.github_token.get_secret_value())
    checks["Search key configured"] = bool(s.tavily_api_key.get_secret_value())
    try:
        client = redis.Redis.from_url(s.redis_url.get_secret_value(), socket_connect_timeout=2)
        checks["Redis"] = bool(client.ping())
    except Exception:
        checks["Redis"] = False
    try:
        client = MilvusClient(uri=s.milvus_uri, token=s.milvus_token.get_secret_value(), timeout=5)
        client.list_collections(timeout=5)
        client.close()
        checks["Milvus"] = True
    except Exception:
        checks["Milvus"] = False
    if probe_models:
        from .llm import Coverage, get_llm
        from .retrieval import Embeddings

        try:
            result = (
                get_llm(s)
                .with_structured_output(Coverage, method="function_calling")
                .invoke("Return sufficient=false, reason='probe', queries=[].")
            )
            checks["LLM structured output"] = not result.sufficient and result.reason == "probe"
        except Exception:
            checks["LLM structured output"] = False
        try:
            e = Embeddings(s)
            checks["Embedding"] = e.dimension > 0
            checks["Tokenizer (no truncation)"] = len(e.tokenizer.encode("word " * 1000).ids) > 512
        except Exception:
            checks["Embedding"] = False
        # Also validate that the configured search endpoint is an HTTPS public endpoint.
        from .network import public_target

        try:
            public_target(s.search_endpoint)
            checks["Search endpoint public"] = s.search_endpoint.startswith("https://")
        except Exception:
            checks["Search endpoint public"] = False
    console.print_json(data=checks)
    required = [value for name, value in checks.items() if name != "GitHub token configured"]
    if not all(required):
        raise typer.Exit(1)
