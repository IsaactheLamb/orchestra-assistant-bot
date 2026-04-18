"""
Settings menu handler.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from storage import load_config, save_config
from helpers import is_admin

# user_data key for pending new location input
_AWAITING_LOC = "settings_awaiting_loc"
_AWAITING_RM_LOC = "settings_rm_loc"


def _settings_text(cfg: dict) -> str:
    sat_mode = cfg.get("saturday_mode", "regular")
    sat_label = "🕗 7AM (Church event)" if sat_mode == "church" else "🕓 4PM (Regular)"
    return f"⚙️ SETTINGS\n\nSaturday mode: {sat_label}"


def _settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕗 Toggle Saturday Mode", callback_data="settings_sat_toggle")],
        [InlineKeyboardButton("📍 Manage Default Locations", callback_data="settings_locations")],
        [InlineKeyboardButton("👤 View Admin List", callback_data="settings_admins")],
        [InlineKeyboardButton("🔙 Back", callback_data="settings_back")],
    ])


async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    cfg = load_config()
    await q.edit_message_text(_settings_text(cfg), reply_markup=_settings_kb())


async def toggle_saturday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    cfg = load_config()
    current = cfg.get("saturday_mode", "regular")
    cfg["saturday_mode"] = "church" if current == "regular" else "regular"
    save_config(cfg)
    new_label = "🕗 7AM (Church event)" if cfg["saturday_mode"] == "church" else "🕓 4PM (Regular)"
    await q.edit_message_text(
        f"⚙️ SETTINGS\n\nSaturday mode set to: {new_label}",
        reply_markup=_settings_kb(),
    )


async def show_locations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    cfg = load_config()
    locs = cfg.get("default_locations", [])
    lines = ["📍 Default Locations\n"]
    for i, loc in enumerate(locs):
        lines.append(f"{i+1}. {loc}")

    rows = []
    for i, loc in enumerate(locs):
        rows.append([InlineKeyboardButton(f"🗑️ Remove: {loc}", callback_data=f"settings_rm_loc_{i}")])
    rows.append([InlineKeyboardButton("➕ Add Location", callback_data="settings_add_loc")])
    rows.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="settings_back_settings")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def add_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    context.user_data[_AWAITING_LOC] = True
    await context.bot.send_message(
        q.message.chat_id,
        "📍 Enter new location name (or /cancel to abort):"
    )


async def add_location_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    if not context.user_data.get(_AWAITING_LOC):
        return
    context.user_data.pop(_AWAITING_LOC)
    new_loc = update.message.text.strip()
    if not new_loc:
        await update.message.reply_text("⚠️ Empty input ignored.")
        return
    cfg = load_config()
    locs = cfg.setdefault("default_locations", [])
    if new_loc not in locs:
        locs.append(new_loc)
        save_config(cfg)
        await update.message.reply_text(f"✅ Added location: {new_loc}")
    else:
        await update.message.reply_text("⚠️ Location already exists.")


async def remove_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    idx = int(q.data.replace("settings_rm_loc_", ""))
    cfg = load_config()
    locs = cfg.get("default_locations", [])
    if 0 <= idx < len(locs):
        removed = locs.pop(idx)
        save_config(cfg)
        await q.answer(f"Removed: {removed}", show_alert=True)
    await show_locations(update, context)


async def show_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer(); return
    await q.answer()
    cfg = load_config()
    ids = cfg.get("coordinator_ids", [])
    lines = ["👤 Admin / Coordinator List\n"]
    for uid in ids:
        lines.append(f"• {uid}")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 Back to Settings", callback_data="settings_back_settings")
    ]])
    await q.edit_message_text("\n".join(lines), reply_markup=kb)


async def back_to_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    cfg = load_config()
    await q.edit_message_text(_settings_text(cfg), reply_markup=_settings_kb())


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers.menu import MAIN_MENU_TEXT, MAIN_MENU_KEYBOARD
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KEYBOARD)


def build_handlers() -> list:
    return [
        CallbackQueryHandler(handle_settings, pattern=r"^menu_settings$"),
        CallbackQueryHandler(toggle_saturday, pattern=r"^settings_sat_toggle$"),
        CallbackQueryHandler(show_locations, pattern=r"^settings_locations$"),
        CallbackQueryHandler(add_location_prompt, pattern=r"^settings_add_loc$"),
        CallbackQueryHandler(remove_location, pattern=r"^settings_rm_loc_\d+$"),
        CallbackQueryHandler(show_admins, pattern=r"^settings_admins$"),
        CallbackQueryHandler(back_to_settings, pattern=r"^settings_back_settings$"),
        CallbackQueryHandler(back_to_main_menu, pattern=r"^settings_back$"),
        # Text input for adding a new location
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            add_location_input,
        ),
    ]
