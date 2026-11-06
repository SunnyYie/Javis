import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated

import typer
from filelock import FileLock, Timeout
from javis_tools.adapters import Assistants
from javis_tools.feishu import FeishuBot, FeishuError
from langsmith import tracing_context
from rich.console import Console

from .config import Settings
from .daily import TZ, Daily, DailySettings, DailyStore, enhance
from .daily_topics import TOPICS, WeeklyTopics
from .llm import Models
from .models import BrainError

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
console = Console(markup=False, highlight=False, soft_wrap=True)


@contextmanager
def context():
    settings = Settings()
    config = DailySettings()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(settings.data_dir / ".leader.lock")
    adapters = store = bot = None
    try:
        lock.acquire(timeout=0)
        # Do not construct Service: daily questions cannot access a career session.
        with tracing_context(enabled=False):
            store = DailyStore(settings.data_dir / "daily")
            adapters = Assistants(settings, "daily-technical-only")
            bot = FeishuBot(
                config.app_id, config.app_secret.get_secret_value(), config.recipient_email,
            )
            yield Daily(store, config, adapters, Models(settings), bot)
    except Timeout:
        raise BrainError("runtime_busy", "主应用正在使用知识服务，请稍后重试") from None
    finally:
        try:
            if adapters:
                adapters.close()
        finally:
            if bot:
                bot.close()
            if store:
                store.close()
            if lock.is_locked:
                lock.release()


def execute(fn):
    try:
        with context() as daily:
            result = fn(daily)
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
    except (BrainError, FeishuError) as exc:
        code = exc.code if isinstance(exc, BrainError) else exc.category
        result = {"status": "failed", "error": code}
        if isinstance(exc, FeishuError) and exc.code is not None:
            result["vendor_code"] = exc.code
        console.print(json.dumps(result, ensure_ascii=False))
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print('{"status":"cancelled","error":"daily_cancelled"}')
        raise typer.Exit(130) from None
    except Exception:
        # Validation and transport exceptions can embed secrets or external text.
        console.print('{"status":"failed","error":"daily_operation_failed"}')
        raise typer.Exit(1) from None


@app.command()
def status():
    """本地配置状态与最近发送结果；不调用模型或飞书。"""
    execute(lambda daily: {
        "enabled": daily.config.enabled,
        "app_id": daily.config.app_id,
        "secret_configured": bool(daily.config.app_secret.get_secret_value()),
        "recipient_email": daily.config.recipient_email,
        "configured": daily.config.ready(),
        "schedule": "Monday-Friday 11:00 Asia/Shanghai",
        "weekly_categories": [topic[0] for topic in TOPICS.values()],
        "weekly_policy": "persistent_random_permutation_without_replacement",
        "history": daily.store.history(),
        "card_v2_history": daily.store.history(table="daily_card_v2"),
    })


@app.command()
def schedule(day: Annotated[str | None, typer.Option("--day")] = None):
    """查看并固定指定日期所在周的五类随机顺序；不调用模型、不发送。"""
    def invoke(daily):
        target = date.fromisoformat(day) if day else datetime.now(TZ).date()
        return {"status": "scheduled", "plan": WeeklyTopics(daily.store.db).plan(target)}

    execute(invoke)


@app.command()
def preview(
    allow_remote: bool = False,
    day: Annotated[str | None, typer.Option("--day")] = None,
):
    """生成并保存一道技术题，不发送；首次生成需 --allow-remote。"""
    def invoke(daily):
        target = date.fromisoformat(day) if day else datetime.now(TZ).date()
        if not daily.store.get(target) and not allow_remote:
            raise BrainError("daily_remote_consent_required")
        artifact = daily.prepare(target)
        return {
            "status": "prepared", "sent": False, "day": target.isoformat(),
            "content": artifact["content"], "corpus": artifact["corpus"],
        }

    execute(invoke)


@app.command()
def restyle(allow_remote: bool = False, send: bool = False, rebuild: bool = False):
    """当天已有题的 Card 2.0 修订；--send 显式发送，修订回执独立幂等。"""
    def invoke(daily):
        if not allow_remote:
            raise BrainError("daily_remote_consent_required")
        day = datetime.now(TZ).date()
        original = daily.store.get(day)
        if not original:
            raise BrainError("daily_original_required")
        edition = DailyStore(daily.store.root, table="daily_card_v2")
        try:
            row = edition.get(day)
            if rebuild and row and row["status"] != "prepared":
                raise BrainError("daily_revision_not_editable")
            if not row or rebuild:
                artifact = enhance(
                    daily.adapters, daily.models, json.loads(original["artifact"]),
                    web_search=daily.config.web_search,
                )
                if row:
                    edition.revise_prepared(day, artifact)
                else:
                    edition.prepare(artifact)
            if send:
                return Daily(
                    edition, daily.config, daily.adapters, daily.models, daily.bot,
                ).run()
            artifact = json.loads(edition.get(day)["artifact"])
            return {
                "status": "prepared", "edition": "card_v2", "content": artifact["content"],
                "reading_warnings": artifact["reading_warnings"],
            }
        finally:
            edition.close()

    execute(invoke)


@app.command()
def refresh(allow_remote: bool = False):
    """仅用当前排版更新当天已发送的 Card 2.0，不生成题目、不新增消息。"""
    def invoke(daily):
        if not allow_remote:
            raise BrainError("daily_remote_consent_required")
        day = datetime.now(TZ).date()
        edition = DailyStore(daily.store.root, table="daily_card_v2")
        try:
            row = edition.get(day)
            target = edition if row and row["status"] == "sent" else daily.store
            return Daily(
                target, daily.config, daily.adapters, daily.models, daily.bot,
            ).refresh_layout(day)
        finally:
            edition.close()

    execute(invoke)


@app.command()
def run(allow_remote: bool = False, retry_rejected: bool = False):
    """仅周一至周五11点后发送当日题；有持久幂等保护，不补发历史日期。"""
    def invoke(daily):
        if not allow_remote:
            raise BrainError("daily_remote_consent_required")
        return daily.run(retry_rejected=retry_rejected)

    execute(invoke)


@app.command()
def probe():
    """显式验证机器人凭证，不发送消息、不调用模型。"""
    def invoke(daily):
        if not daily.config.ready():
            raise BrainError("daily_not_configured")
        daily.bot.authenticate()
        return {
            "status": "authenticated", "message_sent": False,
            "delivery_permissions_verified": False,
        }

    execute(invoke)


@app.command()
def acknowledge(
    day: str, message_id: str,
    confirm_received: Annotated[bool, typer.Option("--confirm-received")] = False,
):
    """已人工确认收到时，登记不确定结果的回执；不重新发送。"""
    def invoke(daily):
        target = date.fromisoformat(day)
        row = daily.store.get(target)
        if not confirm_received or not message_id.startswith("om_") or not row:
            raise BrainError("daily_receipt_confirmation_required")
        if row["status"] not in ("sending", "unknown"):
            raise BrainError("daily_not_uncertain")
        daily.store.update(target, "sent", message_id=message_id, error="human_confirmed_receipt")
        return {"status": "sent", "basis": "human_confirmed_receipt", "day": day}

    execute(invoke)
