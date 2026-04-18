"""
Member management ConversationHandler.

States:
  MM_MAIN           – members menu
  MM_ADD_NAME       – typing new member name
  MM_ADD_SECTION    – selecting instrument section
  MM_ABSENT_SELECT  – selecting member to mark long-term absent
  MM_ABSENT_REASON  – typing absence reason
  MM_CLEAR_SELECT   – selecting member to clear from long-term absent
  MM_REMOVE_SELECT  – selecting member to remove
  MM_REMOVE_CONF    – confirming member removal
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    MessageHandler, CommandHandler, filters,
)
from storage import load_members, save_members
from helpers import is_admin

(
    MM_MAIN,
    MM_ADD_NAME,
    MM_ADD_SECTION,
    MM_ABSENT_SELECT,
    MM_ABSENT_REASON,
    MM_CLEAR_SELECT,
    MM_REMOVE_SELECT,
    MM_REMOVE_CONF,
) = range(8)

SECTIONS = ["Strings", "Winds", "Brass", "Percussion"]
SECTION_ICONS = {"Strings": "🎻", "Winds": "🎵", "Brass": "🎺", "Percussion": "🥁"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _members_menu_text(members_data: dict) -> str:
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])
    lines = ["👥 MEMBERS", ""]
    lines.append(f"Active: {len(active)}  |  Long-term absent: {len(lta)}")
    return "\n".join(lines)


def _members_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Member", callback_data="mm_add")],
        [InlineKeyboardButton("🔴 Mark Long-term Absent", callback_data="mm_mark_absent")],
        [InlineKeyboardButton("🟢 Clear Long-term Absent", callback_data="mm_clear_absent")],
        [InlineKeyboardButton("🗑️ Remove Member", callback_data="mm_remove")],
        [InlineKeyboardButton("🔙 Back", callback_data="mm_back")],
    ])


def _member_list_kb(members: list, cb_prefix: str, add_cancel=True) -> InlineKeyboardMarkup:
    rows = []
    for m in members:
        name = m["name"]
        section = m.get("section", "")
        icon = SECTION_ICONS.get(section, "")
        rows.append([InlineKeyboardButton(
            f"{icon} {name}", callback_data=f"{cb_prefix}{name}"
        )])
    if add_cancel:
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="mm_cancel")])
    return InlineKeyboardMarkup(rows)


async def _go_menu(update: Update, from_query=True):
    md = load_members()
    text = _members_menu_text(md)
    kb = _members_menu_kb()
    if from_query and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        chat_id = update.effective_chat.id
        await update.get_bot().send_message(chat_id, text, reply_markup=kb)


# ── Entry ──────────────────────────────────────────────────────────────────────

async def enter_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update.effective_user.id):
        await q.answer("Not authorised."); return ConversationHandler.END
    await q.answer()
    await _go_menu(update)
    return MM_MAIN


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    md = load_members()
    await update.message.reply_text(_members_menu_text(md), reply_markup=_members_menu_kb())
    return MM_MAIN


# ── Add member ─────────────────────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("➕ Add Member\n\nEnter the member's name:")
    return MM_ADD_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("⚠️ Name cannot be empty.")
        return MM_ADD_NAME
    # Check duplicate
    md = load_members()
    all_names = [m["name"].lower() for m in md["active"]]
    all_names += [m["name"].lower() for m in md["long_term_absent"]]
    if name.lower() in all_names:
        await update.message.reply_text(f"⚠️ '{name}' already exists.")
        return MM_ADD_NAME
    context.user_data["mm_new_name"] = name
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{SECTION_ICONS[s]} {s}", callback_data=f"mm_section_{s}")]
        for s in SECTIONS
    ] + [[InlineKeyboardButton("❌ Cancel", callback_data="mm_cancel")]])
    await update.message.reply_text(
        f"➕ Add Member: {name}\n\nSelect instrument section:",
        reply_markup=kb,
    )
    return MM_ADD_SECTION


async def add_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    section = q.data.replace("mm_section_", "")
    name = context.user_data.pop("mm_new_name", "")
    if not name:
        await q.edit_message_text("⚠️ Name lost. Please start again.")
        return ConversationHandler.END
    md = load_members()
    md["active"].append({"name": name, "section": section})
    save_members(md)
    await q.edit_message_text(f"✅ Added {name} ({SECTION_ICONS.get(section,'')} {section})")
    await _go_menu(update)
    return MM_MAIN


# ── Mark long-term absent ──────────────────────────────────────────────────────

async def absent_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    md = load_members()
    active = md.get("active", [])
    if not active:
        await q.edit_message_text("No active members.")
        await _go_menu(update)
        return MM_MAIN
    await q.edit_message_text(
        "🔴 Mark Long-term Absent\n\nSelect member:",
        reply_markup=_member_list_kb(active, "mm_absent_sel_"),
    )
    return MM_ABSENT_SELECT


async def absent_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    name = q.data.replace("mm_absent_sel_", "")
    context.user_data["mm_absent_name"] = name
    await q.edit_message_text(
        f"🔴 Mark {name} as long-term absent\n\nEnter reason (e.g. Overseas, Family, Injury):"
    )
    return MM_ABSENT_REASON


async def absent_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    name = context.user_data.pop("mm_absent_name", "")
    if not name:
        await update.message.reply_text("⚠️ Error. Please start again.")
        return ConversationHandler.END
    md = load_members()
    # Move from active to long_term_absent
    md["active"] = [m for m in md["active"] if m["name"] != name]
    md["long_term_absent"].append({"name": name, "reason": reason})
    save_members(md)
    await update.message.reply_text(f"✅ {name} marked as long-term absent ({reason}).")
    await _go_menu(update, from_query=False)
    return MM_MAIN


# ── Clear long-term absent ─────────────────────────────────────────────────────

async def clear_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    md = load_members()
    lta = md.get("long_term_absent", [])
    if not lta:
        await q.edit_message_text("No long-term absent members.")
        await _go_menu(update)
        return MM_MAIN
    await q.edit_message_text(
        "🟢 Clear Long-term Absent\n\nSelect member to reinstate:",
        reply_markup=_member_list_kb(lta, "mm_clear_sel_"),
    )
    return MM_CLEAR_SELECT


async def clear_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    name = q.data.replace("mm_clear_sel_", "")
    md = load_members()
    # Find the member's previous section (default to Strings if unknown)
    member = next((m for m in md["long_term_absent"] if m["name"] == name), None)
    if member:
        md["long_term_absent"] = [m for m in md["long_term_absent"] if m["name"] != name]
        # Ask which section to restore them to
        context.user_data["mm_clear_name"] = name
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{SECTION_ICONS[s]} {s}", callback_data=f"mm_restore_section_{s}")]
            for s in SECTIONS
        ] + [[InlineKeyboardButton("❌ Cancel", callback_data="mm_cancel")]])
        await q.edit_message_text(
            f"🟢 Restore {name}\n\nSelect instrument section:",
            reply_markup=kb,
        )
        return MM_CLEAR_SELECT
    await q.edit_message_text("⚠️ Member not found.")
    await _go_menu(update)
    return MM_MAIN


async def clear_section_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    section = q.data.replace("mm_restore_section_", "")
    name = context.user_data.pop("mm_clear_name", "")
    if not name:
        await q.edit_message_text("⚠️ Error."); return ConversationHandler.END
    md = load_members()
    md["long_term_absent"] = [m for m in md["long_term_absent"] if m["name"] != name]
    md["active"].append({"name": name, "section": section})
    save_members(md)
    await q.edit_message_text(f"✅ {name} restored to active ({SECTION_ICONS.get(section,'')} {section}).")
    await _go_menu(update)
    return MM_MAIN


# ── Remove member ──────────────────────────────────────────────────────────────

async def remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    md = load_members()
    all_members = md.get("active", []) + [
        {"name": m["name"], "section": "—"} for m in md.get("long_term_absent", [])
    ]
    if not all_members:
        await q.edit_message_text("No members.")
        await _go_menu(update)
        return MM_MAIN
    await q.edit_message_text(
        "🗑️ Remove Member\n\nSelect member to permanently remove:",
        reply_markup=_member_list_kb(all_members, "mm_rm_sel_"),
    )
    return MM_REMOVE_SELECT


async def remove_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    name = q.data.replace("mm_rm_sel_", "")
    context.user_data["mm_rm_name"] = name
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, remove", callback_data=f"mm_rm_confirm_{name}"),
        InlineKeyboardButton("❌ No, keep", callback_data="mm_cancel"),
    ]])
    await q.edit_message_text(f"🗑️ Permanently remove {name}?", reply_markup=kb)
    return MM_REMOVE_CONF


async def remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    name = q.data.replace("mm_rm_confirm_", "")
    md = load_members()
    md["active"] = [m for m in md["active"] if m["name"] != name]
    md["long_term_absent"] = [m for m in md["long_term_absent"] if m["name"] != name]
    save_members(md)
    context.user_data.pop("mm_rm_name", None)
    await q.edit_message_text(f"✅ {name} removed.")
    await _go_menu(update)
    return MM_MAIN


# ── Cancel / back ──────────────────────────────────────────────────────────────

async def cancel_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("mm_new_name", None)
    context.user_data.pop("mm_absent_name", None)
    context.user_data.pop("mm_rm_name", None)
    await _go_menu(update)
    return MM_MAIN


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.menu import MAIN_MENU_TEXT, MAIN_MENU_KEYBOARD
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KEYBOARD)
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── ConversationHandler factory ────────────────────────────────────────────────

def build_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(enter_members, pattern=r"^menu_members$"),
            CommandHandler("members", cmd_members),
        ],
        states={
            MM_MAIN: [
                CallbackQueryHandler(add_start, pattern=r"^mm_add$"),
                CallbackQueryHandler(absent_start, pattern=r"^mm_mark_absent$"),
                CallbackQueryHandler(clear_start, pattern=r"^mm_clear_absent$"),
                CallbackQueryHandler(remove_start, pattern=r"^mm_remove$"),
                CallbackQueryHandler(back_to_main_menu, pattern=r"^mm_back$"),
            ],
            MM_ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_name),
            ],
            MM_ADD_SECTION: [
                CallbackQueryHandler(add_section, pattern=r"^mm_section_"),
            ],
            MM_ABSENT_SELECT: [
                CallbackQueryHandler(absent_selected, pattern=r"^mm_absent_sel_"),
            ],
            MM_ABSENT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, absent_reason),
            ],
            MM_CLEAR_SELECT: [
                CallbackQueryHandler(clear_selected, pattern=r"^mm_clear_sel_"),
                CallbackQueryHandler(clear_section_selected, pattern=r"^mm_restore_section_"),
            ],
            MM_REMOVE_SELECT: [
                CallbackQueryHandler(remove_selected, pattern=r"^mm_rm_sel_"),
            ],
            MM_REMOVE_CONF: [
                CallbackQueryHandler(remove_confirm, pattern=r"^mm_rm_confirm_"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_menu, pattern=r"^mm_cancel$"),
            CommandHandler("cancel", cancel_command),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
        allow_reentry=True,
    )
