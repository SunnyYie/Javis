from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(help="按技术栈配额整理、补题、检索与导出结构化题库。")
console = Console()


@app.command()
def build(
    output: Annotated[Path, typer.Option("--output")],
    target: Annotated[int, typer.Option(min=1, max=10000)] = 1000,
    workers: Annotated[int, typer.Option(min=1, max=4)] = 3,
    rounds: Annotated[int, typer.Option(min=0, max=100)] = 30,
):
    """先整理有答案的来源原题，按缺口调用配置的 GLM 补题并核验；重复执行可续跑。"""
    from .bank import build_bank
    from .cli import fail
    from .config import get_settings

    try:
        report = build_bank(get_settings(), output, target=target, workers=workers, rounds=rounds)
        console.print_json(data=report["stats"])
        if not report["complete"]:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(exc)


@app.command()
def stats():
    """统计实际落表题量、类型、热点、来源标记与证据有效性。"""
    from .bank import QuestionBank
    from .cli import fail
    from .config import get_settings

    try:
        console.print_json(data=QuestionBank(get_settings()).stats())
    except Exception as exc:
        fail(exc)


@app.command()
def search(
    question: Annotated[str, typer.Option("-q", "--question")],
    limit: Annotated[int, typer.Option(min=1, max=20)] = 5,
    topic: Annotated[str | None, typer.Option()] = None,
):
    """从独立 Milvus 题目向量集合检索已存题目；答案引用原始资料。"""
    from .bank import QuestionBank
    from .cli import fail
    from .config import get_settings

    try:
        console.print_json(data=QuestionBank(get_settings()).search(question, limit, topic))
    except Exception as exc:
        fail(exc)


@app.command()
def export(
    output: Annotated[Path, typer.Option("--output")],
):
    """导出 .jsonl 或 .md，含每题答案、分类、难度、来源与核验类型。"""
    from .bank import QuestionBank
    from .cli import fail
    from .config import get_settings

    try:
        total = QuestionBank(get_settings()).export(output)
        console.print_json(data={"exported": total, "path": str(output.resolve())})
    except Exception as exc:
        fail(exc)
