from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler
from helpers import is_admin


MAIN_MENU_TEXT = "🎻 ORCHESTRA BOT"

MAIN_MENU_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📋 Week Setup", callback_data="menu_week_setup"),
        InlineKeyboardButton("👥 Members", callback_data="menu_members"),
    ],
    [
        InlineKeyboardButton("📊 Generate Report", callback_data="menu_report"),
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
    ],
])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You are not authorised to use this bot.")
        return
    # Only respond in private chat
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KEYBOARD)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KEYBOARD)


async def send_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Helper to send the main menu to a coordinator."""
    await context.bot.send_message(
        chat_id=chat_id,
        text=MAIN_MENU_TEXT,
        reply_markup=MAIN_MENU_KEYBOARD,
    )
