"""
Session report generation and delivery.

Reports are sent as DMs to all coordinators after a session ends.
The report DM includes [📋 Copy Report] and [✏️ Edit Report] buttons.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, Application
from storage import load_sessions, load_attendance, save_attendance, load_members, load_config
from board import render_session_report
from helpers import is_admin

log = logging.getLogger(__name__)

# user_data key for pending prayer edit: "awaiting_prayer" -> session_idx (int)


async def send_report_to_coordinators(app: Application, session_idx: int) -> None:
    """
    Called automatically by APScheduler after a session ends,
    or manually via /report command.
    """
    cfg = load_config()
    coordinator_ids = cfg.get("coordinator_ids", [])
    sessions = load_sessions()

    if session_idx < 0 or session_idx >= len(sessions):
        log.warning("send_report: invalid session_idx %d", session_idx)
        return

    session = sessions[session_idx]
    members_data = load_members()
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])
    state = load_attendance()
    attendance = state.get("attendance", {})
    prayer = state.get("session_report_prayers", {}).get(str(session_idx + 1), "")

    report_text = render_session_report(session, session_idx, active, lta, attendance, prayer)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Copy Report", callback_data=f"report_copy_{session_idx}"),
        InlineKeyboardButton("✏️ Edit Report", callback_data=f"report_edit_{session_idx}"),
    ]])

    for uid in coordinator_ids:
        try:
            await app.bot.send_message(chat_id=uid, text=report_text, reply_markup=kb)
        except Exception as e:
            log.error("Failed to DM coordinator %d: %s", uid, e)


def _build_report_text(session_idx: int) -> str:
    sessions = load_sessions()
    if session_idx < 0 or session_idx >= len(sessions):
        return "⚠️ Session not found."
    session = sessions[session_idx]
    members_data = load_members()
    state = load_attendance()
    prayer = state.get("session_report_prayers", {}).get(str(session_idx + 1), "")
    return render_session_report(
        session, session_idx,
        members_data.get("active", []),
        members_data.get("long_term_absent", []),
        state.get("attendance", {}),
        prayer,
    )


# ── Callback handlers ──────────────────────────────────────────────────────────

async def handle_copy_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    session_idx = int(q.data.replace("report_copy_", ""))
    report_text = _build_report_text(session_idx)
    # Send as a new plain message (easy to copy)
    await context.bot.send_message(q.message.chat_id, report_text)


async def handle_edit_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    session_idx = int(q.data.replace("report_edit_", ""))
    context.user_data["awaiting_prayer"] = session_idx
    await context.bot.send_message(
        q.message.chat_id,
        f"✏️ Edit Report — Session {session_idx + 1}\n\n"
        "Enter the Representative Prayer (or any note to append):\n"
        "(Send /cancel to abort)"
    )


async def handle_prayer_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches coordinator text when awaiting prayer for a report edit."""
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    session_idx = context.user_data.get("awaiting_prayer")
    if session_idx is None:
        return

    prayer_text = update.message.text.strip()
    context.user_data.pop("awaiting_prayer", None)

    state = load_attendance()
    state.setdefault("session_report_prayers", {})[str(session_idx + 1)] = prayer_text
    save_attendance(state)

    report_text = _build_report_text(session_idx)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Copy Report", callback_data=f"report_copy_{session_idx}"),
        InlineKeyboardButton("✏️ Edit Report", callback_data=f"report_edit_{session_idx}"),
    ]])
    await update.message.reply_text("✅ Report updated:")
    await update.message.reply_text(report_text, reply_markup=kb)


async def handle_cancel_prayer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("awaiting_prayer", None) is not None:
        await update.message.reply_text("Prayer edit cancelled.")


# ── /report command – manual trigger ──────────────────────────────────────────

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /report [session_number] — with no arg, shows session-picker buttons."""
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return

    sessions = load_sessions()
    if not sessions:
        await update.message.reply_text("⚠️ No sessions found for the current week.")
        return

    args = context.args
    if not args:
        from helpers import format_date_display, format_time_display
        kb_rows = []
        for idx, s in enumerate(sessions):
            label = f"{idx+1}. {s['day'][:3]} {format_date_display(s['date'])} {format_time_display(s['time'])}"
            kb_rows.append([InlineKeyboardButton(label, callback_data=f"report_show_{idx}")])
        await update.message.reply_text(
            "📊 Which session?",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text("Usage: /report [session_number]")
        return

    if idx < 0 or idx >= len(sessions):
        await update.message.reply_text(f"⚠️ Session {idx+1} not found.")
        return

    await _send_session_report(update.message.chat_id, idx, context)


async def _send_session_report(chat_id: int, idx: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    sessions = load_sessions()
    members_data = load_members()
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])
    state = load_attendance()
    attendance = state.get("attendance", {})
    session = sessions[idx]
    prayer = state.get("session_report_prayers", {}).get(str(idx + 1), "")
    report_text = render_session_report(session, idx, active, lta, attendance, prayer)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Copy Report", callback_data=f"report_copy_{idx}"),
        InlineKeyboardButton("✏️ Edit Report", callback_data=f"report_edit_{idx}"),
    ]])
    await context.bot.send_message(chat_id, report_text, reply_markup=kb)


async def handle_report_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    idx = int(q.data.replace("report_show_", ""))
    await _send_session_report(q.message.chat_id, idx, context)


# ── Generate Report menu button ────────────────────────────────────────────────

async def handle_menu_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    sessions = load_sessions()
    if not sessions:
        await q.edit_message_text("⚠️ No sessions found. Set up the week first.")
        return

    members_data = load_members()
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])
    state = load_attendance()
    attendance = state.get("attendance", {})

    await q.edit_message_text("📊 Select session to generate report:")
    for idx, session in enumerate(sessions):
        from helpers import format_date_display, format_time_display
        date_disp = format_date_display(session["date"])
        time_disp = format_time_display(session["time"])
        prayer = state.get("session_report_prayers", {}).get(str(idx + 1), "")
        report_text = render_session_report(session, idx, active, lta, attendance, prayer)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Copy Report", callback_data=f"report_copy_{idx}"),
            InlineKeyboardButton("✏️ Edit Report", callback_data=f"report_edit_{idx}"),
        ]])
        header = f"Session {idx+1}: {session['day'][:3]} {date_disp} | {time_disp}"
        await context.bot.send_message(q.message.chat_id, header)
        await context.bot.send_message(q.message.chat_id, report_text, reply_markup=kb)


def build_handlers() -> list:
    return [
        CallbackQueryHandler(handle_copy_report, pattern=r"^report_copy_\d+$"),
        CallbackQueryHandler(handle_edit_report, pattern=r"^report_edit_\d+$"),
        CallbackQueryHandler(handle_report_show, pattern=r"^report_show_\d+$"),
        CallbackQueryHandler(handle_menu_report, pattern=r"^menu_report$"),
        # Prayer text input — must come BEFORE the generic cancel command handler
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_prayer_input,
        ),
    ]
