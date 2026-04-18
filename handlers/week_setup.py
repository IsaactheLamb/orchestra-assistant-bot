"""
Week setup ConversationHandler.

States:
  WS_MAIN          – setup menu shown, waiting for action button
  WS_ADD_DAY       – picking day for new session
  WS_ADD_DATE      – typing date for new session
  WS_ADD_TIME      – typing time for new session
  WS_ADD_LOCATION  – picking/typing location for new session
  WS_ADD_CONFIRM   – confirming new/edited session details
  WS_EDIT_FIELD    – picking which field to edit
  WS_EDIT_VALUE    – providing new value for the chosen field
  WS_REMOVE_CONF   – confirming session removal
  WS_POST_SAT_MODE – choosing Saturday time mode before posting
"""
import logging
from datetime import datetime, date as date_type, time as time_type
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)

from storage import load_sessions, save_sessions, load_config, load_attendance, save_attendance
from helpers import (
    is_admin, format_date_display, format_time_display,
    parse_date_input, parse_time_input, get_week_monday,
)
from board import render_attendance_board

log = logging.getLogger(__name__)

AEST = pytz.timezone("Australia/Melbourne")

# ── State constants ────────────────────────────────────────────────────────────
(
    WS_MAIN,
    WS_ADD_DAY,
    WS_ADD_DATE,
    WS_ADD_TIME,
    WS_ADD_LOCATION,
    WS_ADD_CONFIRM,
    WS_EDIT_FIELD,
    WS_EDIT_VALUE,
    WS_REMOVE_CONF,
    WS_POST_SAT_MODE,
) = range(10)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL = {
    "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday",
    "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
}

# ── Menu renderers ─────────────────────────────────────────────────────────────

def _setup_text(sessions: list) -> str:
    lines = ["📋 ORCHESTRA PRACTICE – Week Setup", ""]
    if sessions:
        lines.append("Sessions this week:")
        for i, s in enumerate(sessions):
            num = NUMBER_EMOJIS[i]
            day_abbr = s["day"][:3]
            date_disp = format_date_display(s["date"])
            time_disp = format_time_display(s["time"])
            lines.append(f"{num} {day_abbr} {date_disp} | {time_disp} | 📍 {s['location']}")
    else:
        lines.append("No sessions scheduled yet.")
    return "\n".join(lines)


def _setup_keyboard(sessions: list) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("➕ Add Session", callback_data="ws_add")]]
    if sessions:
        edit_row = [
            InlineKeyboardButton(f"✏️ Edit {i+1}", callback_data=f"ws_edit_{i+1}")
            for i in range(len(sessions))
        ]
        rm_row = [
            InlineKeyboardButton(f"🗑️ Remove {i+1}", callback_data=f"ws_rm_{i+1}")
            for i in range(len(sessions))
        ]
        keyboard.append(edit_row)
        keyboard.append(rm_row)
    keyboard.append([InlineKeyboardButton("✅ Post to Group", callback_data="ws_post")])
    return InlineKeyboardMarkup(keyboard)


def _loc_keyboard(extra_cancel=True) -> InlineKeyboardMarkup:
    cfg = load_config()
    locs = cfg.get("default_locations", [])
    rows = []
    for i in range(0, len(locs), 2):
        rows.append([
            InlineKeyboardButton(locs[j], callback_data=f"ws_loc_{j}")
            for j in range(i, min(i + 2, len(locs)))
        ])
    if extra_cancel:
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="ws_cancel")])
    return InlineKeyboardMarkup(rows)


def _day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(d, callback_data=f"ws_day_{d}") for d in DAYS_SHORT[:4]],
        [InlineKeyboardButton(d, callback_data=f"ws_day_{d}") for d in DAYS_SHORT[4:]],
        [InlineKeyboardButton("❌ Cancel", callback_data="ws_cancel")],
    ])


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _show_menu(update: Update, sessions: list, *, from_query=True):
    text = _setup_text(sessions)
    kb = _setup_keyboard(sessions)
    if from_query and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        msg = update.message or (update.callback_query and update.callback_query.message)
        chat_id = msg.chat_id if msg else update.effective_chat.id
        await update.get_bot().send_message(chat_id, text, reply_markup=kb)


async def send_setup_menu(bot, chat_id: int) -> None:
    """Called by scheduler to DM a coordinator the weekly setup menu."""
    sessions = load_sessions()
    text = _setup_text(sessions)
    kb = _setup_keyboard(sessions)
    await bot.send_message(chat_id, text, reply_markup=kb)


# ── Entry / WS_MAIN ────────────────────────────────────────────────────────────

async def enter_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry from main menu button."""
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer("Not authorised.")
        return ConversationHandler.END
    await q.answer()
    sessions = load_sessions()
    await _show_menu(update, sessions)
    return WS_MAIN


async def refresh_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh setup menu (ws_main callback)."""
    q = update.callback_query
    await q.answer()
    sessions = load_sessions()
    await _show_menu(update, sessions)
    return WS_MAIN


# ── Add session flow ───────────────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    context.user_data["ws_temp"] = {}
    context.user_data["ws_mode"] = "add"
    await q.edit_message_text(
        "➕ Add Session\n\nStep 1/4: Select day",
        reply_markup=_day_keyboard(),
    )
    return WS_ADD_DAY


async def add_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    day_abbr = q.data.replace("ws_day_", "")
    context.user_data["ws_temp"]["day"] = DAY_FULL.get(day_abbr, day_abbr)
    await q.edit_message_text(
        f"➕ Add Session\n\nDay: {context.user_data['ws_temp']['day']}\n\n"
        "Step 2/4: Enter date (e.g. 16 Apr)"
    )
    return WS_ADD_DATE


async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = parse_date_input(update.message.text)
    if not date_str:
        await update.message.reply_text("⚠️ Couldn't parse date. Try: 16 Apr or 19 April")
        return WS_ADD_DATE
    temp = context.user_data["ws_temp"]
    temp["date"] = date_str
    temp["date_input"] = update.message.text.strip()
    await update.message.reply_text(
        f"➕ Add Session\n\nDay: {temp['day']} | Date: {temp['date_input']}\n\n"
        "Step 3/4: Enter time (e.g. 9:15PM, 4:00PM, 7:00AM, After Service)"
    )
    return WS_ADD_TIME


async def add_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_time_input(update.message.text)
    if not parsed:
        await update.message.reply_text("⚠️ Couldn't parse time. Try: 9:15PM, 4:00PM or After Service")
        return WS_ADD_TIME
    temp = context.user_data["ws_temp"]
    temp["time"] = parsed
    await update.message.reply_text(
        f"➕ Add Session\n\nDay: {temp['day']} | {temp['date_input']} | "
        f"{format_time_display(parsed)}\n\n"
        "Step 4/4: Select or type location",
        reply_markup=_loc_keyboard(),
    )
    return WS_ADD_LOCATION


async def add_loc_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.replace("ws_loc_", ""))
    cfg = load_config()
    locs = cfg.get("default_locations", [])
    context.user_data["ws_temp"]["location"] = locs[idx] if idx < len(locs) else "TBC"
    return await _show_confirm(update, context, from_query=True)


async def add_loc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ws_temp"]["location"] = update.message.text.strip()
    return await _show_confirm(update, context, from_query=False)


async def _show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, *, from_query: bool):
    temp = context.user_data["ws_temp"]
    mode = context.user_data.get("ws_mode", "add")
    label = "➕ Add" if mode == "add" else "✏️ Edit"
    text = (
        f"{label} Session – Confirm?\n\n"
        f"Day: {temp.get('day', '')}\n"
        f"Date: {temp.get('date_input') or format_date_display(temp.get('date', ''))}\n"
        f"Time: {format_time_display(temp.get('time', ''))}\n"
        f"Location: {temp.get('location', '')}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Save", callback_data="ws_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="ws_cancel"),
    ]])
    if from_query and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)
    return WS_ADD_CONFIRM


async def add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    temp = context.user_data["ws_temp"]
    mode = context.user_data.get("ws_mode", "add")
    sessions = load_sessions()

    if mode == "edit":
        edit_idx = context.user_data.get("ws_edit_idx", 0)
        if 0 <= edit_idx < len(sessions):
            sessions[edit_idx].update({
                "day": temp.get("day", sessions[edit_idx]["day"]),
                "date": temp.get("date", sessions[edit_idx]["date"]),
                "time": temp.get("time", sessions[edit_idx]["time"]),
                "location": temp.get("location", sessions[edit_idx]["location"]),
            })
    else:
        sessions.append({
            "id": len(sessions) + 1,
            "day": temp["day"],
            "date": temp["date"],
            "time": temp["time"],
            "end_time": None,
            "location": temp["location"],
        })

    save_sessions(sessions)
    context.user_data.pop("ws_temp", None)
    await q.edit_message_text("✅ Session saved.")
    await send_setup_menu(q.get_bot(), q.message.chat_id)
    return WS_MAIN


# ── Edit session flow ──────────────────────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    n = int(q.data.replace("ws_edit_", ""))
    sessions = load_sessions()
    if n < 1 or n > len(sessions):
        await q.answer("Session not found."); return WS_MAIN
    idx = n - 1
    s = sessions[idx]
    context.user_data["ws_edit_idx"] = idx
    context.user_data["ws_mode"] = "edit"
    # Pre-populate temp with existing values
    context.user_data["ws_temp"] = {
        "day": s["day"],
        "date": s["date"],
        "date_input": format_date_display(s["date"]),
        "time": s["time"],
        "location": s["location"],
    }
    time_disp = format_time_display(s["time"])
    date_disp = format_date_display(s["date"])
    text = (
        f"✏️ Edit Session {n}\n\n"
        f"Current: {s['day'][:3]} {date_disp} | {time_disp} | 📍 {s['location']}\n\n"
        "What would you like to change?"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Day", callback_data="ws_field_day"),
            InlineKeyboardButton("🗓 Date", callback_data="ws_field_date"),
        ],
        [
            InlineKeyboardButton("⏰ Time", callback_data="ws_field_time"),
            InlineKeyboardButton("📍 Location", callback_data="ws_field_location"),
        ],
        [InlineKeyboardButton("✅ Save as-is", callback_data="ws_confirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="ws_cancel")],
    ])
    await q.edit_message_text(text, reply_markup=kb)
    return WS_EDIT_FIELD


async def edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.replace("ws_field_", "")
    context.user_data["ws_edit_field"] = field
    temp = context.user_data.get("ws_temp", {})

    if field == "day":
        await q.edit_message_text("Select new day:", reply_markup=_day_keyboard())
        return WS_EDIT_VALUE
    elif field == "date":
        await q.edit_message_text(
            f"Current date: {temp.get('date_input', '')}\n\nEnter new date (e.g. 16 Apr):"
        )
        return WS_EDIT_VALUE
    elif field == "time":
        await q.edit_message_text(
            f"Current time: {format_time_display(temp.get('time', ''))}\n\n"
            "Enter new time (e.g. 9:15PM or After Service):"
        )
        return WS_EDIT_VALUE
    elif field == "location":
        await q.edit_message_text(
            f"Current location: {temp.get('location', '')}\n\nSelect or type new location:",
            reply_markup=_loc_keyboard(),
        )
        return WS_EDIT_VALUE
    return WS_EDIT_FIELD


async def edit_value_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button input during WS_EDIT_VALUE (day or location quick-select)."""
    q = update.callback_query
    await q.answer()
    data = q.data
    field = context.user_data.get("ws_edit_field")
    temp = context.user_data.setdefault("ws_temp", {})

    if data.startswith("ws_day_"):
        day_abbr = data.replace("ws_day_", "")
        temp["day"] = DAY_FULL.get(day_abbr, day_abbr)
    elif data.startswith("ws_loc_"):
        idx = int(data.replace("ws_loc_", ""))
        cfg = load_config()
        locs = cfg.get("default_locations", [])
        temp["location"] = locs[idx] if idx < len(locs) else "TBC"

    return await _show_confirm(update, context, from_query=True)


async def edit_value_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text input during WS_EDIT_VALUE (date or time)."""
    field = context.user_data.get("ws_edit_field")
    temp = context.user_data.setdefault("ws_temp", {})

    if field == "date":
        parsed = parse_date_input(update.message.text)
        if not parsed:
            await update.message.reply_text("⚠️ Couldn't parse date. Try: 16 Apr")
            return WS_EDIT_VALUE
        temp["date"] = parsed
        temp["date_input"] = update.message.text.strip()
    elif field == "time":
        parsed = parse_time_input(update.message.text)
        if not parsed:
            await update.message.reply_text("⚠️ Couldn't parse time. Try: 9:15PM")
            return WS_EDIT_VALUE
        temp["time"] = parsed
    elif field == "location":
        temp["location"] = update.message.text.strip()

    return await _show_confirm(update, context, from_query=False)


# ── Remove session flow ────────────────────────────────────────────────────────

async def remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    n = int(q.data.replace("ws_rm_", ""))
    sessions = load_sessions()
    if n < 1 or n > len(sessions):
        await q.answer("Session not found."); return WS_MAIN
    s = sessions[n - 1]
    date_disp = format_date_display(s["date"])
    time_disp = format_time_display(s["time"])
    context.user_data["ws_rm_idx"] = n - 1
    text = (
        f"🗑️ Remove Session {n}?\n\n"
        f"{s['day'][:3]} {date_disp} | {time_disp} | 📍 {s['location']}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, remove", callback_data=f"ws_rm_yes_{n}"),
        InlineKeyboardButton("❌ No, keep", callback_data="ws_cancel"),
    ]])
    await q.edit_message_text(text, reply_markup=kb)
    return WS_REMOVE_CONF


async def remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    n = int(q.data.replace("ws_rm_yes_", ""))
    sessions = load_sessions()
    idx = n - 1
    if 0 <= idx < len(sessions):
        sessions.pop(idx)
        # Re-number IDs
        for i, s in enumerate(sessions):
            s["id"] = i + 1
        save_sessions(sessions)
    await q.edit_message_text("✅ Session removed.")
    await send_setup_menu(q.get_bot(), q.message.chat_id)
    return WS_MAIN


# ── Post to group flow ─────────────────────────────────────────────────────────

async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    sessions = load_sessions()
    if not sessions:
        await q.edit_message_text("⚠️ No sessions to post. Add sessions first.")
        await send_setup_menu(q.get_bot(), q.message.chat_id)
        return WS_MAIN

    # Check if Saturday session exists
    has_saturday = any(s["day"] == "Saturday" for s in sessions)
    if has_saturday:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🕗 7AM (Church event)", callback_data="ws_sat_church"),
            InlineKeyboardButton("🕓 4PM (Regular)", callback_data="ws_sat_regular"),
        ]])
        await q.edit_message_text(
            "Saturday mode: which time should be used for Saturday?",
            reply_markup=kb,
        )
        return WS_POST_SAT_MODE
    else:
        return await _do_post(update, context)


async def post_saturday_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sessions = load_sessions()
    mode = "church" if q.data == "ws_sat_church" else "regular"
    cfg = load_config()

    for i, s in enumerate(sessions):
        if s["day"] == "Saturday":
            defaults = cfg.get("default_sessions", [])
            sat_default = next((d for d in defaults if d["day"] == "Saturday"), {})
            if mode == "church":
                sessions[i]["time"] = sat_default.get("time_church", "07:00")
                sessions[i]["end_time"] = sat_default.get("end_time_church", "09:00")
            else:
                sessions[i]["time"] = sat_default.get("time_regular", "16:00")
                sessions[i]["end_time"] = sat_default.get("end_time_regular", "18:00")

    save_sessions(sessions)
    # Save saturday mode to config
    cfg["saturday_mode"] = mode
    __import__("storage").save_config(cfg)
    return await _do_post(update, context)


async def _do_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post and pin the attendance board in the group chat."""
    q = update.callback_query
    cfg = load_config()
    group_chat_id = cfg.get("group_chat_id")
    if not group_chat_id:
        await q.edit_message_text(
            "⚠️ group_chat_id not set in config.json. Please configure it first."
        )
        return ConversationHandler.END

    sessions = load_sessions()
    members_data = __import__("storage").load_members()
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])

    # Initialise fresh attendance state
    from helpers import get_week_monday, init_attendance_state
    from datetime import datetime
    first_date = datetime.strptime(sessions[0]["date"], "%Y-%m-%d").date()
    monday = get_week_monday(first_date)
    state = init_attendance_state(sessions, monday)
    save_attendance(state)

    board_text = render_attendance_board(sessions, active, lta, state["attendance"])

    try:
        msg = await context.bot.send_message(group_chat_id, board_text)
        await context.bot.pin_chat_message(group_chat_id, msg.message_id, disable_notification=True)
    except Exception as e:
        log.error("Failed to post/pin board: %s", e)
        await q.edit_message_text(f"❌ Failed to post board: {e}")
        return ConversationHandler.END

    # Save message reference
    state["board_message_id"] = msg.message_id
    state["board_chat_id"] = group_chat_id
    save_attendance(state)

    # Schedule post-session report jobs
    scheduler = context.bot_data.get("scheduler")
    if scheduler:
        _schedule_reports(scheduler, sessions, context.application)

    await q.edit_message_text("✅ Board posted and pinned in the group!")
    return ConversationHandler.END


def _schedule_reports(scheduler, sessions: list, app):
    """Add APScheduler one-shot jobs for each session with an end time."""
    from handlers.reports import send_report_to_coordinators
    now = datetime.now(AEST)
    for i, session in enumerate(sessions):
        if not session.get("end_time"):
            continue
        try:
            end_h, end_m = map(int, session["end_time"].split(":"))
            session_date = datetime.strptime(session["date"], "%Y-%m-%d").date()
            run_dt = AEST.localize(
                datetime.combine(session_date, time_type(end_h, end_m))
            )
            if run_dt <= now:
                continue
            job_id = f"report_session_{i + 1}"
            # Remove any existing job with same ID
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            scheduler.add_job(
                send_report_to_coordinators,
                "date",
                run_date=run_dt,
                args=[app, i],
                id=job_id,
            )
            log.info("Scheduled report job %s at %s", job_id, run_dt)
        except Exception as e:
            log.error("Error scheduling report for session %d: %s", i + 1, e)


# ── Cancel / fallback ──────────────────────────────────────────────────────────

async def cancel_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ws_temp", None)
    q = update.callback_query
    await q.answer()
    sessions = load_sessions()
    await q.edit_message_text(_setup_text(sessions), reply_markup=_setup_keyboard(sessions))
    return WS_MAIN


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ws_temp", None)
    await update.message.reply_text("Cancelled.")
    sessions = load_sessions()
    text = _setup_text(sessions)
    kb = _setup_keyboard(sessions)
    await update.message.reply_text(text, reply_markup=kb)
    return ConversationHandler.END


# ── ConversationHandler factory ────────────────────────────────────────────────

_WS_MAIN_CBQ = [
    CallbackQueryHandler(add_start, pattern=r"^ws_add$"),
    CallbackQueryHandler(edit_start, pattern=r"^ws_edit_\d+$"),
    CallbackQueryHandler(remove_start, pattern=r"^ws_rm_\d+$"),
    CallbackQueryHandler(post_start, pattern=r"^ws_post$"),
    CallbackQueryHandler(refresh_menu, pattern=r"^ws_main$"),
]


def build_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(enter_setup, pattern=r"^menu_week_setup$"),
            *_WS_MAIN_CBQ,  # Also entry if Monday scheduler sent the menu directly
        ],
        states={
            WS_MAIN: _WS_MAIN_CBQ,
            WS_ADD_DAY: [
                CallbackQueryHandler(add_day, pattern=r"^ws_day_"),
            ],
            WS_ADD_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_date),
            ],
            WS_ADD_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_time),
            ],
            WS_ADD_LOCATION: [
                CallbackQueryHandler(add_loc_button, pattern=r"^ws_loc_\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_loc_text),
            ],
            WS_ADD_CONFIRM: [
                CallbackQueryHandler(add_confirm, pattern=r"^ws_confirm$"),
            ],
            WS_EDIT_FIELD: [
                CallbackQueryHandler(edit_field_selected, pattern=r"^ws_field_"),
                CallbackQueryHandler(add_confirm, pattern=r"^ws_confirm$"),
            ],
            WS_EDIT_VALUE: [
                CallbackQueryHandler(edit_value_button, pattern=r"^ws_day_|^ws_loc_\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_text),
            ],
            WS_REMOVE_CONF: [
                CallbackQueryHandler(remove_confirm, pattern=r"^ws_rm_yes_\d+$"),
            ],
            WS_POST_SAT_MODE: [
                CallbackQueryHandler(post_saturday_mode, pattern=r"^ws_sat_"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_menu, pattern=r"^ws_cancel$"),
            CommandHandler("cancel", cancel_command),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
        allow_reentry=True,
    )
