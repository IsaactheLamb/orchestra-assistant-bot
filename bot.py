"""Orchestra Assistant Telegram Bot – main entry point.

Two tasks:
  1. Extract a single-session attendance report from a forwarded schedule (DM flow).
  2. Post a checklist to a group topic and keep it updated live from member messages.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from parser import (
    EMOJI_ACTUAL_ABSENT,
    EMOJI_ACTUAL_LATE,
    EMOJI_ACTUAL_ONTIME,
    EMOJI_EXPECTED_NO,
    EMOJI_EXPECTED_YES,
    EMOJI_UNSET,
    Member,
    Session,
    format_session_report,
    parse_schedule,
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s | %(levelname)-8s | %(name)s — %(message)s',
    level=logging.INFO,
)
logging.getLogger('httpx').setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / 'config.json'


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def is_coordinator(user_id: int, cfg: Dict[str, Any]) -> bool:
    return user_id in cfg.get('coordinator_ids', [])


# ── In-memory state ───────────────────────────────────────────────────────────

# {user_id: (sessions, members)} — set when coordinator forwards a schedule
_pending: Dict[int, Tuple[List[Session], List[Member]]] = {}

# {topic_id: {'message_id': int, 'chat_id': int, 'text': str, 'lines': list}}
_checklists: Dict[int, Dict[str, Any]] = {}


def _load_checklists_from_config(cfg: Dict[str, Any]) -> None:
    for tid_str, data in cfg.get('active_checklists', {}).items():
        tid = int(tid_str)
        text = data.get('text', '')
        _checklists[tid] = {
            'message_id': data['message_id'],
            'chat_id':    data['chat_id'],
            'text':       text,
            'lines':      text.split('\n') if text else [],
        }
    log.info('Loaded %d active checklist(s).', len(_checklists))


def _persist_checklist(topic_id: int) -> None:
    cfg = load_config()
    cfg.setdefault('active_checklists', {})
    data = _checklists[topic_id]
    cfg['active_checklists'][str(topic_id)] = {
        'message_id': data['message_id'],
        'chat_id':    data['chat_id'],
        'text':       data['text'],
    }
    save_config(cfg)


# ═════════════════════════════════════════════════════════════════════════════
# TASK 1 — Extract Single Session Report
# ═════════════════════════════════════════════════════════════════════════════

async def handle_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command DM from a coordinator (schedule text)."""
    if not update.message or not update.effective_user:
        return

    cfg = load_config()
    uid = update.effective_user.id

    if not is_coordinator(uid, cfg):
        await update.message.reply_text('Sorry, this bot is for coordinators only.')
        return

    text = (update.message.text or update.message.caption or '').strip()
    if not text:
        return

    try:
        sessions, members = parse_schedule(text)
    except ValueError:
        await update.message.reply_text(
            '⚠️ Could not read the schedule. '
            'Please check the format with /help and try again.'
        )
        return

    if not sessions:
        await update.message.reply_text(
            '⚠️ No sessions found in the schedule. '
            'Please check the format with /help and try again.'
        )
        return

    _pending[uid] = (sessions, members)

    # Only one session → extract immediately, no button needed
    if len(sessions) == 1:
        report = format_session_report(sessions[0], members, len(sessions))
        await update.message.reply_text(report)
        return

    keyboard = [
        [InlineKeyboardButton(
            f'{s.emoji} {s.date_str} – {s.title}',
            callback_data=f'extract_{s.number}',
        )]
        for s in sessions
    ]
    await update.message.reply_text(
        'Which session to extract?',
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_extract_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a session selection button."""
    query = update.callback_query
    await query.answer()

    if not query.data or not query.from_user:
        return

    uid = query.from_user.id
    cfg = load_config()
    if not is_coordinator(uid, cfg):
        return

    if uid not in _pending:
        await query.edit_message_text(
            '⚠️ Session data has expired. Please forward the schedule again.'
        )
        return

    session_number = int(query.data.split('_')[1])
    sessions, members = _pending[uid]
    session = next((s for s in sessions if s.number == session_number), None)

    if not session:
        await query.edit_message_text(
            '⚠️ Session not found. Please forward the schedule again.'
        )
        return

    report = format_session_report(session, members, len(sessions))
    await query.edit_message_text(report)


# ═════════════════════════════════════════════════════════════════════════════
# TASK 2 — Live Checklist Updates
# ═════════════════════════════════════════════════════════════════════════════

UPDATE_EMOJIS: List[str] = [
    EMOJI_ACTUAL_ONTIME,   # ✅
    EMOJI_ACTUAL_LATE,     # ⚠️
    EMOJI_ACTUAL_ABSENT,   # ❌
    EMOJI_EXPECTED_YES,    # ☑️
    EMOJI_EXPECTED_NO,     # ⛔️
    '◻️',                  # reset to blank
]
_UPDATE_EMOJIS_SORTED = sorted(UPDATE_EMOJIS, key=len, reverse=True)


def _extract_update_prefix(text: str) -> Tuple[str, str]:
    """
    Parse a member update message into (emoji_prefix, clean_name).
    Consumes as many consecutive status emojis as appear before the name.
    e.g. '✅◻️Isaac (sick)' → ('✅◻️', 'Isaac')
         '◻️◻️Karina'       → ('◻️◻️', 'Karina')
    Returns ('', '') if the message doesn't start with a status emoji.
    """
    i = 0
    while i < len(text):
        found = False
        for emoji in _UPDATE_EMOJIS_SORTED:
            if text[i:].startswith(emoji):
                i += len(emoji)
                found = True
                break
        if not found:
            break
    prefix = text[:i]
    if not prefix:
        return '', ''
    rest = text[i:].strip()
    rest = re.sub(r'[\(\[].*?[\)\]]', '', rest)  # remove (reason)
    rest = re.sub(r'[^\w\s]', '', rest)           # remove punctuation
    return prefix, rest.strip()


def _extract_line_name(line: str) -> str:
    """Strip ALL leading status emojis, ordinal, and reason from a checklist line."""
    s = line.strip()
    # Consume all leading status emojis
    while True:
        consumed = False
        for emoji in _UPDATE_EMOJIS_SORTED:
            if s.startswith(emoji):
                s = s[len(emoji):]
                consumed = True
                break
        if not consumed:
            break
    s = s.strip()
    s = re.sub(r'^\d+\.\s*', '', s).strip()       # remove "1. "
    s = re.sub(r'[\(\[].*?[\)\]]', '', s).strip()  # remove (reason)
    return s


def _find_line_index(name: str, lines: List[str]) -> Optional[int]:
    """
    Case-insensitive match; partial (prefix) match accepted when unique.
    Returns the matching line index or None.
    """
    name_lc = name.lower()
    exact: List[int] = []
    partial: List[int] = []

    for i, line in enumerate(lines):
        line_name = _extract_line_name(line).lower()
        if not line_name:
            continue
        if line_name == name_lc:
            exact.append(i)
        elif name_lc in line_name or line_name.startswith(name_lc):
            partial.append(i)

    if len(exact) == 1:
        return exact[0]
    if not exact and len(partial) == 1:
        return partial[0]
    return None


def _apply_prefix(line: str, new_prefix: str) -> str:
    """
    Replace the leading emoji block on a checklist line with new_prefix.
    Finds the first digit (start of ordinal like '1.') and keeps everything from there.
    """
    stripped = line.lstrip()
    m = re.search(r'\d', stripped)
    if m:
        return new_prefix + stripped[m.start():]
    return new_prefix + stripped


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Watch group messages for emoji-name status updates (e.g. ✅Isaac)."""
    if not update.message or not update.effective_chat:
        return

    cfg = load_config()
    group_chat_id = cfg.get('group_chat_id')
    if not group_chat_id or update.effective_chat.id != group_chat_id:
        return

    text = (update.message.text or '').strip()
    if not text:
        return

    prefix, name = _extract_update_prefix(text)
    if not prefix:
        return

    # Telegram sends message_thread_id=None for the General topic (thread 1).
    # All other topics have their actual thread ID set.
    raw_tid = update.message.message_thread_id
    topic_id = raw_tid if raw_tid is not None else 1

    if topic_id not in _checklists:
        return

    if not name:
        return

    data = _checklists[topic_id]
    lines: List[str] = data['lines']

    idx = _find_line_index(name, lines)
    if idx is None:
        # Name not recognised — react with 🤔
        try:
            await context.bot.set_message_reaction(
                chat_id=update.message.chat_id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji(emoji='🤔')],
            )
        except Exception:
            pass
        return

    lines[idx] = _apply_prefix(lines[idx], prefix)
    new_text = '\n'.join(lines)
    data['text'] = new_text
    data['lines'] = lines

    try:
        await context.bot.edit_message_text(
            chat_id=data['chat_id'],
            message_id=data['message_id'],
            text=new_text,
        )
    except BadRequest as exc:
        if 'not modified' in str(exc).lower():
            pass  # already up to date — not an error
        else:
            log.warning('CHECKLIST | edit failed: %s', exc)
            return
    except Exception as exc:
        log.warning('CHECKLIST | edit failed: %s', exc)
        return

    _persist_checklist(topic_id)

    try:
        await context.bot.set_message_reaction(
            chat_id=update.message.chat_id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji='👍')],
        )
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Commands
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        '🎵 Orchestra Assistant Bot\n\n'
        'Commands for coordinators:\n'
        '• Forward me the weekly schedule → I parse it and offer session buttons\n'
        '• /extract — Re-show session buttons for a schedule you already sent\n'
        '• /postchecklist [topic] — Post and pin a checklist in a group topic\n'
        '• /help — Schedule formatting rules\n\n'
        'In the group, members send e.g. ✅Isaac or ⚠️Karina to update the checklist.'
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        '📋 ORCHESTRA ASSISTANT BOT – Schedule Formatting Rules\n\n'
        'To keep the bot working correctly, follow these rules when updating the schedule:\n\n'
        '✅ You CAN freely change:\n'
        '- Session titles, dates, times, locations\n'
        '- Number of sessions\n'
        '- Number of members\n'
        '- Section headers (🎻 WINDS etc.)\n'
        '- Decorative lines (•••, ━━━)\n\n'
        '⚠️ Please DO NOT change:\n'
        '- Session emojis: must use 1️⃣2️⃣3️⃣ (not plain numbers)\n'
        '- Column separator: must use | between columns\n'
        '- Status emojis: ▫️ ☑️ ⛔️ ✅ ⚠️ ❌ must stay consistent\n'
        '- Location prefix: keep 📍 before location names\n'
        '- Absence reasons: keep them in () or [] brackets after the name\n'
        '- Slot order: left slot = expected, right slot = actual\n\n'
        '❓ If extraction fails, the bot will tell you. '
        'Use /extract to try again after fixing the format.'
    )


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-show session buttons for an already-loaded schedule."""
    cfg = load_config()
    uid = update.effective_user.id

    if not is_coordinator(uid, cfg):
        return

    if uid not in _pending:
        await update.message.reply_text(
            'No schedule loaded yet. Please forward me the weekly schedule message.'
        )
        return

    sessions, _ = _pending[uid]
    keyboard = [
        [InlineKeyboardButton(
            f'{s.emoji} {s.date_str} – {s.title}',
            callback_data=f'extract_{s.number}',
        )]
        for s in sessions
    ]
    await update.message.reply_text(
        'Which session to extract?',
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_postchecklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /postchecklist [topic name]
    [checklist body lines]

    Posts the checklist to the named topic in the configured group and pins it.
    The bot then monitors that topic for emoji-name updates.
    """
    cfg = load_config()
    uid = update.effective_user.id

    if not is_coordinator(uid, cfg):
        return

    full_text = (update.message.text or '').strip()

    newline_pos = full_text.find('\n')
    if newline_pos == -1:
        await update.message.reply_text(
            'Usage:\n'
            '/postchecklist [topic name]\n'
            '[checklist text]'
        )
        return

    command_line   = full_text[:newline_pos].strip()
    checklist_body = full_text[newline_pos + 1:].strip()

    topic_name = re.sub(r'^/postchecklist\s*', '', command_line, flags=re.IGNORECASE).strip()

    if not topic_name:
        await update.message.reply_text('Please specify a topic name after /postchecklist.')
        return

    if not checklist_body:
        await update.message.reply_text('Please include the checklist text below the command.')
        return

    group_chat_id = cfg.get('group_chat_id')
    if not group_chat_id:
        await update.message.reply_text('⚠️ group_chat_id is not set in config.json.')
        return

    # Resolve topic name → topic thread ID
    topics: Dict[str, int] = cfg.get('topics', {})
    topic_id: Optional[int] = None
    for t_name, t_id in topics.items():
        if t_name.lower() == topic_name.lower():
            topic_id = t_id
            break

    if topic_id is None:
        available = ', '.join(f'"{k}"' for k in topics) if topics else 'none configured'
        await update.message.reply_text(
            f'⚠️ Topic "{topic_name}" not found.\n'
            f'Configured topics: {available}\n\n'
            'Add topic thread IDs to config.json under "topics": {"TopicName": thread_id}'
        )
        return

    try:
        sent = await context.bot.send_message(
            chat_id=group_chat_id,
            text=checklist_body,
            message_thread_id=topic_id,
        )
        await context.bot.pin_chat_message(
            chat_id=group_chat_id,
            message_id=sent.message_id,
        )
    except Exception as exc:
        log.error('Failed to post checklist: %s', exc)
        await update.message.reply_text(f'⚠️ Failed to post checklist: {exc}')
        return

    _checklists[topic_id] = {
        'message_id': sent.message_id,
        'chat_id':    group_chat_id,
        'text':       checklist_body,
        'lines':      checklist_body.split('\n'),
    }
    _persist_checklist(topic_id)

    await update.message.reply_text(f'✅ Checklist posted and pinned in "{topic_name}".')


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    cfg = load_config()
    token = cfg.get('bot_token', '')
    if not token or token == 'YOUR_BOT_TOKEN_HERE':
        raise SystemExit(
            '❌  No bot token found.\n'
            '    Edit config.json and set "bot_token" to your BotFather token.'
        )

    _load_checklists_from_config(cfg)

    app = Application.builder().token(token).build()

    # Commands (private DM only for coordinator commands)
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('extract', cmd_extract, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler('postchecklist', cmd_postchecklist, filters=filters.ChatType.PRIVATE))

    # Inline button — session selection
    app.add_handler(CallbackQueryHandler(handle_extract_callback, pattern=r'^extract_\d+$'))

    # DM — schedule text (forwarded or typed)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_dm_message,
    ))

    # Group — live attendance emoji updates
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND,
        handle_group_message,
    ))

    log.info('Orchestra Assistant Bot starting…')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
