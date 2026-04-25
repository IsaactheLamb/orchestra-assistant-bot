"""
Live checklist feature — port of the legacy bot.py 'checklist' flow.

A coordinator posts a free-text checklist via /postchecklist <topic>, the bot
sends + pins it in the named topic of the group, then watches that topic for
emoji-prefixed lines (e.g. "✅Anna") and rewrites the leading emojis on the
matching line of the pinned message.

State:
  - In-memory map of message_id -> checklist data (loaded from config on start).
  - Persisted under config['active_checklists'][str(message_id)].

Wiring (in app.py):
  - CommandHandler('postchecklist', cmd_postchecklist, filters=ChatType.PRIVATE)
  - MessageHandler(group filter, handle_group_message) registered in a LOWER
    group number than the attendance handler, so checklist matches are
    consumed first via ApplicationHandlerStop. If no checklist matches, the
    attendance handler runs.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from telegram import ReactionTypeEmoji, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from helpers import is_admin
from storage import load_config, save_config

log = logging.getLogger(__name__)

# ── Emoji constants (mirror parser.py) ─────────────────────────────────────────
EMOJI_EXPECTED_YES = '☑️'
EMOJI_EXPECTED_NO = '⚫️'
EMOJI_EXPECTED_LATE = '🕐'
EMOJI_ACTUAL_ONTIME = '✅'
EMOJI_ACTUAL_LATE = '⚠️'
EMOJI_ACTUAL_ABSENT = '❌'
EMOJI_UNSET = '▫️'

# All emojis the checklist update prefix may consume.
UPDATE_EMOJIS: List[str] = [
    EMOJI_ACTUAL_ONTIME, EMOJI_ACTUAL_LATE, EMOJI_ACTUAL_ABSENT,
    EMOJI_EXPECTED_YES, EMOJI_EXPECTED_NO, EMOJI_EXPECTED_LATE,
    EMOJI_UNSET, '◻️', '◽️',
]

# General-topic sentinel: Telegram sends thread_id=None for the General topic.
GENERAL_TOPIC_ID = 1


def _normalize_vs(text: str) -> str:
    """Strip variation selector U+FE0F for reliable emoji matching."""
    return text.replace('\uFE0F', '')


_NORM_MAP: Dict[str, str] = {_normalize_vs(e): e for e in UPDATE_EMOJIS}
_NORM_SORTED = sorted(_NORM_MAP.keys(), key=len, reverse=True)


# ── In-memory state ───────────────────────────────────────────────────────────
# message_id -> {message_id, topic_id, chat_id, text, lines, type}
_checklists: Dict[int, Dict[str, Any]] = {}


def load_from_config() -> None:
    """Load active checklists from config.json into memory. Call on startup."""
    cfg = load_config()
    _checklists.clear()
    for key_str, data in cfg.get('active_checklists', {}).items():
        # Legacy format had message_id as a value; new format uses it as the key.
        if 'message_id' in data:
            mid = data['message_id']
            tid = int(key_str)
        else:
            mid = int(key_str)
            tid = data['topic_id']
        text = data.get('text', '')
        _checklists[mid] = {
            'message_id': mid,
            'topic_id': tid,
            'chat_id': data['chat_id'],
            'text': text,
            'lines': text.split('\n') if text else [],
            'type': data.get('type', 'checklist'),
        }
    log.info('Loaded %d active checklist(s).', len(_checklists))


def _persist(message_id: int) -> None:
    """Write a single checklist's state back to config.json."""
    cfg = load_config()
    cfg.setdefault('active_checklists', {})
    data = _checklists[message_id]
    entry: Dict[str, Any] = {
        'topic_id': data['topic_id'],
        'chat_id': data['chat_id'],
        'text': data['text'],
    }
    if data.get('type', 'checklist') != 'checklist':
        entry['type'] = data['type']
    cfg['active_checklists'][str(message_id)] = entry
    save_config(cfg)


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _extract_update_prefix(text: str) -> Tuple[str, str, str]:
    """Parse an update line into (emoji_prefix, clean_name, reason).
    Returns ('', '', '') if the line does not start with a known status emoji.
    """
    norm = _normalize_vs(text)
    i = 0
    canonical_prefix: List[str] = []
    while i < len(norm):
        if norm[i] == '|':
            canonical_prefix.append('|')
            i += 1
            continue
        found = False
        for emoji_norm in _NORM_SORTED:
            if norm[i:].startswith(emoji_norm):
                canonical_prefix.append(_NORM_MAP[emoji_norm])
                i += len(emoji_norm)
                found = True
                break
        if not found:
            break
    prefix = ''.join(canonical_prefix)
    if not prefix:
        return '', '', ''
    rest = norm[i:].strip()
    rest = re.sub(r'^\d+\.\s*', '', rest)  # strip "10. "
    reason_match = re.search(r'[\(\[](.*?)[\)\]]', rest)
    reason = reason_match.group(1).strip() if reason_match else ''
    rest = re.sub(r'[\(\[].*?[\)\]]', '', rest)
    rest = re.sub(r'[^\w\s]', '', rest)
    return prefix, rest.strip(), reason


def _extract_line_name(line: str) -> str:
    """Strip leading status emojis, ordinal, and (reason) from a checklist line."""
    s = _normalize_vs(line.strip())
    while True:
        consumed = False
        for emoji_norm in _NORM_SORTED:
            if s.startswith(emoji_norm):
                s = s[len(emoji_norm):]
                consumed = True
                break
        if not consumed:
            break
    s = s.strip()
    s = re.sub(r'^\d+\.\s*', '', s).strip()
    s = re.sub(r'[\(\[].*?[\)\]]', '', s).strip()
    return s


def _find_line_index(name: str, lines: List[str]) -> Optional[int]:
    """Case-insensitive exact, then unique-prefix match."""
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


def _count_status_emojis(prefix: str) -> int:
    norm = _normalize_vs(prefix)
    i = 0
    count = 0
    while i < len(norm):
        if norm[i] == '|':
            i += 1
            continue
        matched = False
        for emoji_norm in _NORM_SORTED:
            if norm[i:].startswith(emoji_norm):
                count += 1
                i += len(emoji_norm)
                matched = True
                break
        if not matched:
            break
    return count


def _apply_prefix(line: str, new_prefix: str) -> str:
    """Replace the leading emoji block on a line, padding with ◻️ if shorter."""
    stripped = line.lstrip()
    m = re.search(r'\d', stripped)
    body = stripped[m.start():] if m else stripped
    existing_prefix = stripped[:m.start()] if m else ''
    existing_cols = _count_status_emojis(existing_prefix)
    new_cols = _count_status_emojis(new_prefix)
    if new_cols < existing_cols:
        new_prefix = new_prefix + '◻️' * (existing_cols - new_cols)
    return new_prefix + body


async def _react(update: Update, context: ContextTypes.DEFAULT_TYPE, emoji: str) -> None:
    try:
        await context.bot.set_message_reaction(
            chat_id=update.message.chat_id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass


def _get_group_chat_id(cfg: Dict[str, Any]) -> Optional[int]:
    """Mirror bot.py's profile-aware getter so checklist works on either chat."""
    profiles = cfg.get('profiles')
    name = cfg.get('active_profile')
    if profiles and name and name in profiles:
        return profiles[name].get('group_chat_id')
    return cfg.get('group_chat_id')


def _get_topics(cfg: Dict[str, Any]) -> Dict[str, int]:
    profiles = cfg.get('profiles')
    name = cfg.get('active_profile')
    if profiles and name and name in profiles:
        return profiles[name].get('topics', {})
    return cfg.get('topics', {})


# ── /postchecklist ────────────────────────────────────────────────────────────

async def cmd_postchecklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/postchecklist <topic>\n<body…> — post + pin a checklist in a group topic."""
    cfg = load_config()
    if not is_admin(update.effective_user.id):
        await update.message.reply_text('Sorry, this command is for coordinators only.')
        return

    full_text = (update.message.text or '').strip()
    newline_pos = full_text.find('\n')
    if newline_pos == -1:
        await update.message.reply_text(
            'Usage:\n/postchecklist [topic name]\n[checklist text]'
        )
        return

    command_line = full_text[:newline_pos].strip()
    body = full_text[newline_pos + 1:].strip()
    topic_name = re.sub(r'^/postchecklist\s*', '', command_line, flags=re.IGNORECASE).strip()

    if not topic_name:
        await update.message.reply_text('Please specify a topic name after /postchecklist.')
        return
    if not body:
        await update.message.reply_text('Please include the checklist text below the command.')
        return

    group_chat_id = _get_group_chat_id(cfg)
    if not group_chat_id:
        await update.message.reply_text('⚠️ group_chat_id is not set in config.json.')
        return

    topics = _get_topics(cfg)
    topic_id: Optional[int] = next(
        (tid for name, tid in topics.items() if name.lower() == topic_name.lower()),
        None,
    )
    if topic_id is None:
        available = ', '.join(f'"{k}"' for k in topics) if topics else 'none configured'
        await update.message.reply_text(
            f'⚠️ Topic "{topic_name}" not found.\nConfigured topics: {available}'
        )
        return

    try:
        kwargs: Dict[str, Any] = {'chat_id': group_chat_id, 'text': body}
        if topic_id and topic_id > 1:  # General topic has no thread param
            kwargs['message_thread_id'] = topic_id
        sent = await context.bot.send_message(**kwargs)
        await context.bot.pin_chat_message(chat_id=group_chat_id, message_id=sent.message_id)
    except Exception as exc:
        log.error('Failed to post checklist: %s', exc)
        await update.message.reply_text(f'⚠️ Failed to post checklist: {exc}')
        return

    _checklists[sent.message_id] = {
        'message_id': sent.message_id,
        'topic_id': topic_id,
        'chat_id': group_chat_id,
        'text': body,
        'lines': body.split('\n'),
        'type': 'checklist',
    }
    _persist(sent.message_id)
    await update.message.reply_text(f'✅ Checklist posted and pinned in "{topic_name}".')


# ── Group listener ────────────────────────────────────────────────────────────

async def _handle_checklist_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    data: Dict[str, Any], updates: List[Tuple[str, str, str]],
) -> None:
    lines: List[str] = data['lines']
    all_found = True
    for prefix, name, _reason in updates:
        if not name:
            all_found = False
            continue
        idx = _find_line_index(name, lines)
        if idx is None:
            all_found = False
        else:
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
        if 'not modified' not in str(exc).lower():
            log.warning('CHECKLIST | edit failed: %s', exc)
            return
    except Exception as exc:
        log.warning('CHECKLIST | edit failed: %s', exc)
        return

    _persist(data['message_id'])
    await _react(update, context, '👍' if all_found else '🤔')


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """If the message updates a known checklist, handle it and stop dispatch.
    Otherwise return — letting the attendance handler (next group) run.
    """
    if not update.message or not update.effective_chat:
        return

    cfg = load_config()
    group_chat_id = _get_group_chat_id(cfg)
    if not group_chat_id or update.effective_chat.id != group_chat_id:
        return

    text = (update.message.text or '').strip()
    if not text:
        return

    updates: List[Tuple[str, str, str]] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        prefix, name, reason = _extract_update_prefix(raw_line)
        if prefix:
            updates.append((prefix, name, reason))
    if not updates:
        return

    reply_to = update.message.reply_to_message
    thread_id = getattr(update.message, 'message_thread_id', None)
    is_explicit_reply = reply_to is not None and reply_to.message_id != thread_id

    # 1) Explicit reply to a known checklist post
    if is_explicit_reply:
        data = _checklists.get(reply_to.message_id)
        if data is not None and data.get('type') == 'checklist':
            await _handle_checklist_update(update, context, data, updates)
            raise ApplicationHandlerStop
        # Reply to something we don't track → let attendance handler decide.
        return

    # 2) No reply — find latest checklist in this topic
    effective_thread_id = thread_id if thread_id is not None else GENERAL_TOPIC_ID
    topic_matches = [
        d for d in _checklists.values()
        if d['topic_id'] == effective_thread_id and d.get('type') == 'checklist'
    ]
    if topic_matches:
        data = max(topic_matches, key=lambda d: d['message_id'])
        await _handle_checklist_update(update, context, data, updates)
        raise ApplicationHandlerStop


# ── Wiring factory ────────────────────────────────────────────────────────────

def build_handlers() -> list:
    """Return the handlers to register. `handle_group_message` MUST be added
    in a lower group number than handlers.attendance.build_handler() so it
    runs first and can short-circuit via ApplicationHandlerStop."""
    return [
        CommandHandler(
            'postchecklist',
            cmd_postchecklist,
            filters=filters.ChatType.PRIVATE,
        ),
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_group_message,
        ),
    ]
