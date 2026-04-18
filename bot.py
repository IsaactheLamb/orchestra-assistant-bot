"""Orchestra Assistant Telegram Bot – main entry point.

Three tasks:
  1. Extract a single-session attendance report from a forwarded schedule (DM flow).
  2. Post a checklist to a group topic and keep it updated live from member messages.
  3. Post the attendance board and update it live from emoji+name messages.
"""

import json
import logging
import re
from datetime import datetime, date as date_type
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    ReplyKeyboardMarkup,
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
    EMOJI_EXPECTED_LATE,
    EMOJI_EXPECTED_NO,
    EMOJI_EXPECTED_YES,
    EMOJI_UNSET,
    NUMERAL_EMOJIS,
    Member,
    Session,
    format_session_report,
    parse_schedule,
)
from board import render_attendance_board
from storage import (
    load_sessions as load_sessions_data,
    load_members as load_members_data,
    load_attendance as load_attendance_data,
    save_attendance as save_attendance_data,
)
from helpers import get_week_monday, init_attendance_state, find_member, parse_time_input, parse_date_input

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s | %(levelname)-8s | %(name)s — %(message)s',
    level=logging.INFO,
)
logging.getLogger('httpx').setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ── Session input parser ─────────────────────────────────────────────────────

_DAY_ABBR_MAP = {
    'mon': 'Monday', 'tue': 'Tuesday', 'wed': 'Wednesday',
    'thu': 'Thursday', 'fri': 'Friday', 'sat': 'Saturday', 'sun': 'Sunday',
}


def _parse_session_lines(text: str) -> List[Dict[str, Any]]:
    """Parse session details from coordinator input.

    Each line: Date, Time[-EndTime], Location
           or: Date, Title, Time[-EndTime], Location
    Example:
        Wed 16 Apr, 9:15PM, NC Babyroom
        Wed 16 Apr, Youth Dept Gathering, 6:30AM, NC
        Sat 19 Apr, 4PM - 6PM, NC Babyroom
    """
    sessions: List[Dict[str, Any]] = []
    for i, line in enumerate(text.strip().split('\n'), 1):
        line = line.strip()
        if not line:
            continue
        # Strip leading number like "1. " or "1) "
        line = re.sub(r'^\d+[.)]\s*', '', line).strip()

        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 3:
            continue

        date_part = parts[0]

        # Detect whether parts[1] is a time or a title.
        # If parts[1] parses as a time → 3-part format (Date, Time, Location).
        # Otherwise → 4-part format (Date, Title, Time, Location).
        title = ''
        if len(parts) >= 4 and not parse_time_input(parts[1]):
            title = parts[1]
            time_part = parts[2]
            location = ', '.join(parts[3:])
        else:
            time_part = parts[1]
            location = ', '.join(parts[2:])

        # Extract day-of-week abbreviation if present
        day_name = None
        day_match = re.match(r'^(mon|tue|wed|thu|fri|sat|sun)\w*\s+(.+)', date_part, re.I)
        if day_match:
            day_name = _DAY_ABBR_MAP.get(day_match.group(1)[:3].lower())
            date_str_raw = day_match.group(2).strip()
        else:
            date_str_raw = date_part

        parsed_date = parse_date_input(date_str_raw) or parse_date_input(date_part)
        if not parsed_date:
            continue

        # Derive day name from date if not provided
        if not day_name:
            day_name = datetime.strptime(parsed_date, '%Y-%m-%d').strftime('%A')

        # Parse time (handle ranges like "4PM - 6PM" or "4:00PM-6:00PM")
        time_parts = re.split(r'\s*[-–]\s*', time_part)
        start_time = parse_time_input(time_parts[0])
        end_time = parse_time_input(time_parts[1]) if len(time_parts) > 1 else None
        if not start_time:
            continue

        entry: Dict[str, Any] = {
            'id': i,
            'day': day_name,
            'date': parsed_date,
            'time': start_time,
            'end_time': end_time,
            'location': location.strip(),
        }
        if title:
            entry['title'] = title
        sessions.append(entry)

    return sessions


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

# {user_id: True} — set when coordinator is entering session details
_pending_schedule: Dict[int, bool] = {}

# {topic_id: {'message_id': int, 'chat_id': int, 'text': str, 'lines': list}}
_checklists: Dict[int, Dict[str, Any]] = {}
# Keyed by message_id. Each entry: {message_id, topic_id, chat_id, text, lines, type}


def _load_checklists_from_config(cfg: Dict[str, Any]) -> None:
    for key_str, data in cfg.get('active_checklists', {}).items():
        # Migrate old format: keyed by topic_id, data has 'message_id' field.
        # New format: keyed by message_id, data has 'topic_id' field.
        if 'message_id' in data:
            # Old format — convert on load, will be re-saved in new format on next write.
            mid  = data['message_id']
            tid  = int(key_str)
        else:
            mid  = int(key_str)
            tid  = data['topic_id']
        text = data.get('text', '')
        _checklists[mid] = {
            'message_id': mid,
            'topic_id':   tid,
            'chat_id':    data['chat_id'],
            'text':       text,
            'lines':      text.split('\n') if text else [],
            'type':       data.get('type', 'checklist'),
        }
    log.info('Loaded %d active checklist(s).', len(_checklists))


def _persist_checklist(message_id: int) -> None:
    cfg = load_config()
    cfg.setdefault('active_checklists', {})
    # Remove any stale old-format entry for this message's topic before saving.
    data = _checklists[message_id]
    entry: Dict[str, Any] = {
        'topic_id': data['topic_id'],
        'chat_id':  data['chat_id'],
        'text':     data['text'],
    }
    checklist_type = data.get('type', 'checklist')
    if checklist_type != 'checklist':
        entry['type'] = checklist_type
    cfg['active_checklists'][str(message_id)] = entry
    save_config(cfg)


# ═════════════════════════════════════════════════════════════════════════════
# TASK 1 — Extract Single Session Report
# ═════════════════════════════════════════════════════════════════════════════

async def handle_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command DM from a coordinator (schedule text or keyboard button)."""
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

    # ── Keyboard button shortcuts ─────────────────────────────────────────────
    if text == BTN_EXTRACT:
        await cmd_extract(update, context)
        return

    if text == BTN_HELP:
        await cmd_help(update, context)
        return

    if text == BTN_CHECKLIST:
        topics = cfg.get('topics', {})
        topic_list = '\n'.join(f'  • {name}' for name in topics) if topics else '  (none configured)'
        await update.message.reply_text(
            '📋 Post a checklist by sending:\n\n'
            '/postchecklist [Topic Name]\n'
            '[checklist text]\n\n'
            f'Available topics:\n{topic_list}\n\n'
            'Example:\n'
            '/postchecklist Attendance\n'
            '◻️1. Anna\n'
            '◻️2. Ben\n'
            '◻️3. Chris'
        )
        return

    if text == BTN_SCHEDULE:
        await cmd_postschedule(update, context)
        return
    # ─────────────────────────────────────────────────────────────────────────

    # ── Pending schedule input ────────────────────────────────────────────────
    if uid in _pending_schedule:
        del _pending_schedule[uid]
        if text.lower() == '/cancel':
            await update.message.reply_text('Cancelled.')
            return
        await _process_schedule_input(update, context, text)
        return
    # ─────────────────────────────────────────────────────────────────────────

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
    EMOJI_EXPECTED_NO,     # ⚫️
    EMOJI_EXPECTED_LATE,   # 🕐
    EMOJI_UNSET,           # ▫️ (board-copy format / reset slot)
    '◻️',                  # reset to blank (alias)
]
_UPDATE_EMOJIS_SORTED = sorted(UPDATE_EMOJIS, key=len, reverse=True)

EXPECTED_EMOJI_SET = {EMOJI_EXPECTED_YES, EMOJI_EXPECTED_NO, EMOJI_EXPECTED_LATE}
ACTUAL_EMOJI_SET = {EMOJI_ACTUAL_ONTIME, EMOJI_ACTUAL_LATE, EMOJI_ACTUAL_ABSENT}


def _get_nearest_session_idx(sessions: list) -> int:
    """Find the session index whose date is closest to today."""
    today = date_type.today()
    best_idx = 0
    best_diff = float('inf')
    for i, s in enumerate(sessions):
        d = datetime.strptime(s['date'], '%Y-%m-%d').date()
        diff = abs((d - today).days)
        if diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


def _extract_all_emojis(prefix: str) -> List[str]:
    """Extract all status emojis from a prefix string, in order.
    Returns canonical (with variation selector) emoji strings."""
    norm = _normalize_vs(prefix)
    emojis: List[str] = []
    i = 0
    while i < len(norm):
        found = False
        for emoji_norm in _NORM_EMOJIS_SORTED:
            if norm[i:].startswith(emoji_norm):
                emojis.append(_EMOJI_NORM_MAP[emoji_norm])
                i += len(emoji_norm)
                found = True
                break
        if not found:
            i += 1
    return emojis


def _apply_single_emoji(emoji: str, sid: str, attendance: Dict[str, Any], matched_name: str) -> None:
    """Apply a single emoji to one session slot."""
    if sid not in attendance[matched_name]:
        attendance[matched_name][sid] = {'expected': '▫️', 'actual': '▫️'}

    if emoji == EMOJI_EXPECTED_NO:
        # ⚫️ = expected absent → fill both slots
        attendance[matched_name][sid]['expected'] = emoji
        attendance[matched_name][sid]['actual'] = emoji
    elif emoji in EXPECTED_EMOJI_SET:
        attendance[matched_name][sid]['expected'] = emoji
    elif emoji in ACTUAL_EMOJI_SET:
        attendance[matched_name][sid]['actual'] = emoji
    elif emoji in (EMOJI_UNSET, '◻️'):
        attendance[matched_name][sid] = {'expected': '▫️', 'actual': '▫️'}


def _apply_attendance_board_copy(
    prefix: str, sessions: list,
    attendance: Dict[str, Any], matched_name: str,
) -> None:
    """Apply board-copy format: expected_actual|expected_actual per session."""
    num_sessions = len(sessions)
    columns = prefix.split('|')

    for idx, col in enumerate(columns):
        if idx >= num_sessions:
            break
        sid = str(idx + 1)
        if sid not in attendance[matched_name]:
            attendance[matched_name][sid] = {'expected': '▫️', 'actual': '▫️'}

        emojis = _extract_all_emojis(col)
        if len(emojis) >= 1:
            exp = emojis[0]
            if exp == EMOJI_EXPECTED_NO:
                # ⚫️ = expected absent → fill both slots
                attendance[matched_name][sid] = {'expected': exp, 'actual': exp}
            elif exp in EXPECTED_EMOJI_SET or exp == EMOJI_UNSET or exp == '◻️':
                attendance[matched_name][sid]['expected'] = exp if exp not in (EMOJI_UNSET, '◻️') else '▫️'
            else:
                attendance[matched_name][sid]['expected'] = exp
        if len(emojis) >= 2:
            act = emojis[1]
            attendance[matched_name][sid]['actual'] = act if act not in (EMOJI_UNSET, '◻️') else '▫️'


def _apply_attendance_emojis(
    prefix: str, emojis: List[str], name: str, sessions: list,
    attendance: Dict[str, Any], matched_name: str,
) -> None:
    """Apply emojis to attendance.
    If prefix contains |  → board-copy (column pairs per session).
    Single emoji           → one session.
    Multiple emojis        → positional (one per session).
    """
    num_sessions = len(sessions)

    # Ensure member entry exists
    if matched_name not in attendance:
        attendance[matched_name] = {
            str(i + 1): {'expected': '▫️', 'actual': '▫️'}
            for i in range(num_sessions)
        }

    # Board-copy format: emoji_pair|emoji_pair
    if '|' in prefix:
        _apply_attendance_board_copy(prefix, sessions, attendance, matched_name)
        return

    if len(emojis) == 1:
        # Single emoji — apply to specified or nearest session
        clean_name, session_idx = _parse_schedule_name(name, sessions)
        sid = str(session_idx + 1)
        _apply_single_emoji(emojis[0], sid, attendance, matched_name)
    else:
        # Positional mode — each emoji maps to session 1, 2, 3…
        for idx, emoji in enumerate(emojis):
            if idx >= num_sessions:
                break
            sid = str(idx + 1)
            _apply_single_emoji(emoji, sid, attendance, matched_name)


def _parse_schedule_name(name: str, sessions: list) -> Tuple[str, int]:
    """Extract session number from name if present.
    Returns (clean_name, session_idx_0based).

    Supported formats:
      '1️⃣Isaac'  → session 1
      'Isaac 1'  → session 1
      'Isaac'    → nearest session (auto-detect)
    """
    # Leading numeral emoji: "1️⃣Isaac"
    norm_name = _normalize_vs(name)
    for idx, emoji in enumerate(NUMERAL_EMOJIS):
        norm_emoji = _normalize_vs(emoji)
        if norm_name.startswith(norm_emoji):
            return norm_name[len(norm_emoji):].strip(), idx

    # Trailing session number: "Isaac 1"
    m = re.match(r'^(.+?)\s+(\d+)$', name)
    if m:
        session_num = int(m.group(2))
        if 1 <= session_num <= len(sessions):
            return m.group(1).strip(), session_num - 1

    # Auto-detect
    return name, _get_nearest_session_idx(sessions)


def _normalize_vs(text: str) -> str:
    """Strip variation selector U+FE0F for reliable emoji matching."""
    return text.replace('\uFE0F', '')


# Build a lookup: normalized emoji → canonical emoji (with variation selector)
_EMOJI_NORM_MAP = {_normalize_vs(e): e for e in UPDATE_EMOJIS}
_NORM_EMOJIS_SORTED = sorted(_EMOJI_NORM_MAP.keys(), key=len, reverse=True)


def _extract_update_prefix(text: str) -> Tuple[str, str, str]:
    """
    Parse a member update message into (emoji_prefix, clean_name, reason).
    Consumes status emojis and | separators (board-copy format).
    e.g. '✅◻️Isaac (sick)'          → ('✅◻️', 'Isaac', 'sick')
         '⚫️⚫️|☑️▫️  2. Cardin'     → ('⚫️⚫️|☑️▫️', 'Cardin', '')
    Returns ('', '', '') if the message doesn't start with a status emoji.
    """
    norm = _normalize_vs(text)
    i = 0
    canonical_prefix = []
    while i < len(norm):
        # Allow | as column separator (board-copy format)
        if norm[i] == '|':
            canonical_prefix.append('|')
            i += 1
            continue
        found = False
        for emoji_norm in _NORM_EMOJIS_SORTED:
            if norm[i:].startswith(emoji_norm):
                canonical_prefix.append(_EMOJI_NORM_MAP[emoji_norm])
                i += len(emoji_norm)
                found = True
                break
        if not found:
            break
    prefix = ''.join(canonical_prefix)
    if not prefix:
        return '', '', ''
    rest = norm[i:].strip()
    rest = re.sub(r'^\d+\.\s*', '', rest)         # strip leading ordinal like "10. "
    # Extract reason from (reason) or [reason] before removing it
    reason_match = re.search(r'[\(\[](.*?)[\)\]]', rest)
    reason = reason_match.group(1).strip() if reason_match else ''
    rest = re.sub(r'[\(\[].*?[\)\]]', '', rest)  # remove (reason)
    rest = re.sub(r'[^\w\s]', '', rest)           # remove punctuation
    return prefix, rest.strip(), reason


def _extract_line_name(line: str) -> str:
    """Strip ALL leading status emojis, ordinal, and reason from a checklist line."""
    s = _normalize_vs(line.strip())
    # Consume all leading status emojis
    while True:
        consumed = False
        for emoji_norm in _NORM_EMOJIS_SORTED:
            if s.startswith(emoji_norm):
                s = s[len(emoji_norm):]
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


def _count_status_emojis(prefix: str) -> int:
    """Count status emojis in a prefix (ignores '|' separators)."""
    norm = _normalize_vs(prefix)
    i = 0
    count = 0
    while i < len(norm):
        if norm[i] == '|':
            i += 1
            continue
        matched = False
        for emoji_norm in _NORM_EMOJIS_SORTED:
            if norm[i:].startswith(emoji_norm):
                count += 1
                i += len(emoji_norm)
                matched = True
                break
        if not matched:
            break
    return count


def _apply_prefix(line: str, new_prefix: str) -> str:
    """
    Replace the leading emoji block on a checklist line with new_prefix.
    If the existing prefix has more columns than new_prefix, pad with ◻️
    so unspecified columns become blank instead of being dropped.
    """
    stripped = line.lstrip()
    m = re.search(r'\d', stripped)
    body = stripped[m.start():] if m else stripped
    existing_prefix = stripped[:m.start()] if m else stripped[:0]

    existing_cols = _count_status_emojis(existing_prefix)
    new_cols = _count_status_emojis(new_prefix)
    if new_cols < existing_cols:
        new_prefix = new_prefix + '◻️' * (existing_cols - new_cols)

    return new_prefix + body


async def _react(update: Update, context: ContextTypes.DEFAULT_TYPE, emoji: str) -> None:
    """Helper to react to the triggering message, silently ignoring failures."""
    try:
        await context.bot.set_message_reaction(
            chat_id=update.message.chat_id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass


async def _handle_schedule_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    data: Dict[str, Any],
    updates: List[Tuple[str, str, str]],
) -> None:
    """Handle emoji+name updates on a schedule board (attendance)."""
    sessions = load_sessions_data()
    members_data = load_members_data()
    active = members_data.get('active', [])
    lta = members_data.get('long_term_absent', [])
    all_members = active + lta

    state = load_attendance_data()
    attendance = state.get('attendance', {})

    all_found = True

    for prefix, name, reason in updates:
        if not name:
            all_found = False
            continue

        # Extract all emojis from prefix for positional mode support
        emojis = _extract_all_emojis(prefix)
        log.info('SCHEDULE | prefix=%r name=%r reason=%r emojis=%d %s',
                 prefix, name, reason, len(emojis), emojis)
        if not emojis:
            all_found = False
            continue

        # For single emoji, _parse_schedule_name handles session detection.
        # For multi emoji, we strip any session specifier from name for member lookup.
        clean_name = name
        for num_emoji in NUMERAL_EMOJIS:
            if clean_name.startswith(_normalize_vs(num_emoji)):
                clean_name = clean_name[len(_normalize_vs(num_emoji)):].strip()
                break
        clean_name = re.sub(r'\s+\d+$', '', clean_name).strip()

        member = find_member(clean_name, all_members)
        if not member:
            log.warning('SCHEDULE | member not found: %r (cleaned: %r)', name, clean_name)
            all_found = False
            continue

        _apply_attendance_emojis(prefix, emojis, name, sessions, attendance, member['name'])

        # Store or clear reason
        if member['name'] in attendance:
            if reason:
                attendance[member['name']]['reason'] = reason
            else:
                attendance[member['name']].pop('reason', None)

    # Save attendance state
    state['attendance'] = attendance
    save_attendance_data(state)

    # Re-render board
    board_text = render_attendance_board(sessions, active, lta, attendance)

    try:
        await context.bot.edit_message_text(
            chat_id=data['chat_id'],
            message_id=data['message_id'],
            text=board_text,
            parse_mode='HTML',
        )
    except BadRequest as exc:
        if 'not modified' not in str(exc).lower():
            log.warning('SCHEDULE | edit failed: %s', exc)
            return
    except Exception as exc:
        log.warning('SCHEDULE | edit failed: %s', exc)
        return

    data['text'] = board_text
    data['lines'] = board_text.split('\n')
    _persist_checklist(data['message_id'])

    await _react(update, context, '👍' if all_found else '🤔')


async def _handle_standalone_attendance(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    updates: List[Tuple[str, str, str]],
) -> None:
    """Handle attendance updates as standalone messages (not replying to anything)."""
    sessions = load_sessions_data()
    if not sessions:
        return

    members_data = load_members_data()
    active = members_data.get('active', [])
    lta = members_data.get('long_term_absent', [])
    all_members = active + lta

    state = load_attendance_data()
    attendance = state.get('attendance', {})
    board_msg_id = state.get('board_message_id')
    board_chat_id = state.get('board_chat_id')

    any_valid = False

    for prefix, name, reason in updates:
        if not name:
            continue

        emojis = _extract_all_emojis(prefix)
        if not emojis:
            continue

        clean_name = name
        for num_emoji in NUMERAL_EMOJIS:
            if clean_name.startswith(_normalize_vs(num_emoji)):
                clean_name = clean_name[len(_normalize_vs(num_emoji)):].strip()
                break
        clean_name = re.sub(r'\s+\d+$', '', clean_name).strip()

        member = find_member(clean_name, all_members)
        if not member:
            continue

        _apply_attendance_emojis(prefix, emojis, name, sessions, attendance, member['name'])

        # Store or clear reason
        if member['name'] in attendance:
            if reason:
                attendance[member['name']]['reason'] = reason
            else:
                attendance[member['name']].pop('reason', None)

        any_valid = True

    if not any_valid:
        return

    state['attendance'] = attendance
    save_attendance_data(state)

    # Edit the board if we know where it is
    if board_msg_id and board_chat_id:
        board_text = render_attendance_board(sessions, active, lta, attendance)
        try:
            await context.bot.edit_message_text(
                chat_id=board_chat_id,
                message_id=board_msg_id,
                text=board_text,
                parse_mode='HTML',
            )
        except Exception as exc:
            log.warning('SCHEDULE | standalone edit failed: %s', exc)

    await _react(update, context, '👍')


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

    # Collect every line that starts with a status emoji.
    updates: List[Tuple[str, str, str]] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        prefix, name, reason = _extract_update_prefix(raw_line)
        if prefix:
            updates.append((prefix, name, reason))

    if not updates:
        return  # no status emoji lines — ignore silently

    reply_to = update.message.reply_to_message
    thread_id = getattr(update.message, 'message_thread_id', None)

    # In forum topics, Telegram auto-sets reply_to to the topic header message.
    # Treat that as a non-reply (topic pointer only).
    is_explicit_reply = (
        reply_to is not None
        and reply_to.message_id != thread_id
    )

    log.info('GROUP | updates=%r reply_to=%s thread_id=%s explicit_reply=%s checklists_keys=%s',
             updates, reply_to.message_id if reply_to else None,
             thread_id, is_explicit_reply, list(_checklists.keys()))

    # 1) If explicitly replying to a specific post, update that post (even old/previous lists).
    if is_explicit_reply:
        data = _checklists.get(reply_to.message_id)
        if data is not None:
            if data.get('type') == 'schedule':
                await _handle_schedule_update(update, context, data, updates)
            else:
                await _handle_checklist_update(update, context, data, updates)
        else:
            await _react(update, context, '🤨')
        return

    # 2) No reply — find the latest checklist in this topic (highest message_id).
    # In the General topic, Telegram sends thread_id=None; normalize to 1 (our General sentinel).
    effective_thread_id = thread_id if thread_id is not None else 1
    topic_matches = [d for d in _checklists.values() if d['topic_id'] == effective_thread_id]
    log.info('GROUP | no-reply path: thread_id=%s topic_matches=%s',
             effective_thread_id, [d['message_id'] for d in topic_matches])
    if topic_matches:
        data = max(topic_matches, key=lambda d: d['message_id'])
        if data.get('type') == 'schedule':
            await _handle_schedule_update(update, context, data, updates)
        else:
            await _handle_checklist_update(update, context, data, updates)
        return

    # 3) Not in a known topic and not a reply — try standalone attendance update.
    await _handle_standalone_attendance(update, context, updates)


async def _handle_checklist_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    data: Dict[str, Any],
    updates: List[Tuple[str, str, str]],
) -> None:
    """Handle emoji+name updates on a regular checklist."""
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
        if 'not modified' in str(exc).lower():
            pass
        else:
            log.warning('CHECKLIST | edit failed: %s', exc)
            return
    except Exception as exc:
        log.warning('CHECKLIST | edit failed: %s', exc)
        return

    _persist_checklist(data['message_id'])
    await _react(update, context, '👍' if all_found else '🤔')


# ═════════════════════════════════════════════════════════════════════════════
# Coordinator keyboard
# ═════════════════════════════════════════════════════════════════════════════

BTN_EXTRACT   = '📊 Extract Session Report'
BTN_CHECKLIST = '📋 Post Checklist'
BTN_SCHEDULE  = '📅 Post Schedule'
BTN_HELP      = '❓ Help'

COORD_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_EXTRACT], [BTN_CHECKLIST], [BTN_SCHEDULE], [BTN_HELP]],
    resize_keyboard=True,
    is_persistent=True,
)


# ═════════════════════════════════════════════════════════════════════════════
# Commands
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = load_config()
    uid = update.effective_user.id
    if is_coordinator(uid, cfg):
        await update.message.reply_text(
            '🎵 Orchestra Assistant Bot\n\n'
            'Use the buttons below or forward me the weekly schedule to get started.\n\n'
            'In the group, members send e.g. ✅Name or ⚠️Name to update the checklist.',
            reply_markup=COORD_KEYBOARD,
        )
    else:
        await update.message.reply_text(
            '🎵 Orchestra Assistant Bot\n\n'
            'In the group, send e.g. ✅Name or ⚠️Name to update the attendance checklist.'
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
        '- Status emojis: ▫️ ☑️ ⚫️ 🕐 ✅ ⚠️ ❌ must stay consistent\n'
        '- Location prefix: keep 📍 before location names\n'
        '- Reasons: keep them in () or [] after the name\n'
        '  · No prefix → applies to all sessions, e.g. (Overseas)\n'
        '  · 🔁 prefix → all sessions, e.g. (🔁Overseas)\n'
        '  · Session emoji → specific session, e.g. (1️⃣Family)\n'
        '  · Multiple: (1️⃣Family. 2️⃣Work)\n'
        '  · Late with time: (1️⃣6:30PM, work)\n'
        '- Slot order: left slot = expected, right slot = actual\n\n'
        '❓ If extraction fails, the bot will tell you. '
        'Use /extract to try again after fixing the format.'
    )


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-show session buttons for an already-loaded schedule."""
    cfg = load_config()
    uid = update.effective_user.id

    if not is_coordinator(uid, cfg):
        await update.message.reply_text('Sorry, this command is for coordinators only.')
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
        await update.message.reply_text('Sorry, this command is for coordinators only.')
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
        kwargs: Dict[str, Any] = {'chat_id': group_chat_id, 'text': checklist_body}
        # General topic has no thread id — skip the param (topic_id==1 is the General sentinel).
        if topic_id and topic_id > 1:
            kwargs['message_thread_id'] = topic_id
        sent = await context.bot.send_message(**kwargs)
        await context.bot.pin_chat_message(
            chat_id=group_chat_id,
            message_id=sent.message_id,
        )
    except Exception as exc:
        log.error('Failed to post checklist: %s', exc)
        await update.message.reply_text(f'⚠️ Failed to post checklist: {exc}')
        return

    _checklists[sent.message_id] = {
        'message_id': sent.message_id,
        'topic_id':   topic_id,
        'chat_id':    group_chat_id,
        'text':       checklist_body,
        'lines':      checklist_body.split('\n'),
        'type':       'checklist',
    }
    _persist_checklist(sent.message_id)

    await update.message.reply_text(f'✅ Checklist posted and pinned in "{topic_name}".')


async def cmd_postschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt the coordinator to enter session details for the attendance board."""
    cfg = load_config()
    uid = update.effective_user.id

    if not is_coordinator(uid, cfg):
        await update.message.reply_text('Sorry, this command is for coordinators only.')
        return

    _pending_schedule[uid] = True

    await update.message.reply_text(
        '📅 Enter session details (one per line):\n\n'
        'Format:  Date, Time, Location\n'
        '    or:  Date, Title, Time, Location\n\n'
        'Example:\n'
        'Wed 16 Apr, 9:15PM, NC Babyroom\n'
        'Wed 16 Apr, Youth Dept Gathering, 6:30AM, NC\n'
        'Sat 19 Apr, 4PM - 6PM, NC Babyroom\n\n'
        'Send /cancel to cancel.'
    )


async def _process_schedule_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
) -> None:
    """Parse session details, render board, and post to group."""
    from storage import save_sessions as save_sessions_data

    sessions = _parse_session_lines(text)
    if not sessions:
        await update.message.reply_text(
            '⚠️ Could not parse session details.\n\n'
            'Please use this format (one session per line):\n'
            'Date, Time, Location\n'
            '  or: Date, Title, Time, Location\n\n'
            'Example:\n'
            'Wed 16 Apr, 9:15PM, NC Babyroom\n'
            'Wed 16 Apr, Youth Dept Gathering, 6:30AM, NC\n'
            'Sat 19 Apr, 4PM - 6PM, NC Babyroom'
        )
        return

    # Show parsed sessions for confirmation
    preview_lines = []
    for i, s in enumerate(sessions, 1):
        time_str = s['time']
        if s.get('end_time'):
            time_str += f' - {s["end_time"]}'
        title_part = f' | {s["title"]}' if s.get('title') else ''
        preview_lines.append(f'  {i}. {s["day"]} {s["date"]}{title_part} | {time_str} | {s["location"]}')
    preview = '\n'.join(preview_lines)
    log.info('Parsed %d session(s):\n%s', len(sessions), preview)

    # Save sessions
    save_sessions_data(sessions)

    # Load members
    members_data = load_members_data()
    active = members_data.get('active', [])
    lta = members_data.get('long_term_absent', [])

    # Initialize fresh attendance state
    d = datetime.strptime(sessions[0]['date'], '%Y-%m-%d').date()
    current_monday = get_week_monday(d)
    state = init_attendance_state(sessions, current_monday)
    attendance = state.get('attendance', {})

    # Render board
    board_text = render_attendance_board(sessions, active, lta, attendance)

    # Post to group
    cfg = load_config()
    group_chat_id = cfg.get('group_chat_id')
    if not group_chat_id:
        await update.message.reply_text('⚠️ group_chat_id not configured.')
        return

    topics: Dict[str, int] = cfg.get('topics', {})
    topic_id: Optional[int] = None
    for t_name, t_id in topics.items():
        if t_name.lower() == 'attendance':
            topic_id = t_id
            break

    try:
        kwargs: Dict[str, Any] = {'chat_id': group_chat_id, 'text': board_text, 'parse_mode': 'HTML'}
        if topic_id:
            kwargs['message_thread_id'] = topic_id
        sent = await context.bot.send_message(**kwargs)
        await context.bot.pin_chat_message(
            chat_id=group_chat_id,
            message_id=sent.message_id,
        )
    except Exception as exc:
        log.error('Failed to post schedule: %s', exc)
        await update.message.reply_text(f'⚠️ Failed to post schedule: {exc}')
        return

    # Save board message ID to attendance state
    state['board_message_id'] = sent.message_id
    state['board_chat_id'] = group_chat_id
    save_attendance_data(state)

    # Register as schedule-type checklist for live updates
    effective_topic = topic_id or 1
    _checklists[sent.message_id] = {
        'message_id': sent.message_id,
        'topic_id':   effective_topic,
        'chat_id':    group_chat_id,
        'text':       board_text,
        'lines':      board_text.split('\n'),
        'type':       'schedule',
    }
    _persist_checklist(sent.message_id)

    await update.message.reply_text(
        f'✅ Schedule posted and pinned ({len(sessions)} session{"s" if len(sessions) != 1 else ""}).\n\n'
        'Members can update by:\n'
        '• Replying to the board: ✅Isaac\n'
        '• Standalone: ☑️Isaac 1\n'
        '• Positional: ☑️☑️⚫️Isaac (sessions 1,2,3)\n'
        '• Board-copy: ☑️▫️|⚫️⚫️  2. Name\n\n'
        '☑️ attending · ⚫️ absent · 🕐 late\n'
        '✅ on time · ⚠️ late · ❌ MIA'
    )


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application) -> None:
    """Register bot commands with Telegram on startup."""
    await app.bot.set_my_commands([
        BotCommand('start',          'Show main menu'),
        BotCommand('extract',        'Extract a session report'),
        BotCommand('postchecklist',  'Post a checklist to a group topic'),
        BotCommand('postschedule',   'Post attendance board to group'),
        BotCommand('help',           'Schedule formatting rules'),
    ])


def main() -> None:
    cfg = load_config()
    token = cfg.get('bot_token', '')
    if not token or token == 'YOUR_BOT_TOKEN_HERE':
        raise SystemExit(
            '❌  No bot token found.\n'
            '    Edit config.json and set "bot_token" to your BotFather token.'
        )

    _load_checklists_from_config(cfg)

    app = Application.builder().token(token).post_init(post_init).build()

    # Commands (private DM only for coordinator commands)
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('extract', cmd_extract, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler('postchecklist', cmd_postchecklist, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler('postschedule', cmd_postschedule, filters=filters.ChatType.PRIVATE))

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
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == '__main__':
    main()
