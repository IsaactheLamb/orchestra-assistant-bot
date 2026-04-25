"""Orchestra Assistant Bot — state-driven entry point.

Wires up the modular `handlers/` package. Run with `python3 app.py`.

Replaces the legacy `bot.py` (paste-and-parse model). Both cannot run at
the same time — Telegram allows only one polling client per bot token.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, CommandHandler

from storage import load_config
from helpers import AEST

from handlers import menu as menu_h
from handlers import week_setup as week_setup_h
from handlers import members as members_h
from handlers import reports as reports_h
from handlers import settings as settings_h
from handlers import attendance as attendance_h


logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    scheduler = AsyncIOScheduler(timezone=AEST)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    log.info("APScheduler started.")


async def _post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def main() -> None:
    cfg = load_config()
    token = cfg.get("bot_token")
    if not token:
        raise SystemExit("bot_token missing from config.json")

    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", menu_h.cmd_start))
    app.add_handler(CommandHandler("menu", menu_h.cmd_menu))
    app.add_handler(CommandHandler("report", reports_h.cmd_report))

    # ── Conversation handlers (must register before other CallbackQueryHandlers
    # that share callback patterns, so the conversation gets first dibs) ──────
    app.add_handler(week_setup_h.build_conv_handler())
    app.add_handler(members_h.build_conv_handler())

    # ── Standalone handlers ───────────────────────────────────────────────────
    for h in reports_h.build_handlers():
        app.add_handler(h)
    for h in settings_h.build_handlers():
        app.add_handler(h)

    # Group attendance listener (group chat messages)
    app.add_handler(attendance_h.build_handler())

    log.info("Orchestra Bot starting (state-driven mode)…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
