"""
Group chat attendance update handler.

Supported syntax (auto-detected):
  Single:     {emoji}{Name} {N} (optional reason)
              e.g.  ☑️Isaac 1   ⛔️Isaac 2 (work)   ✅Isaac 1
  Positional: {emoji}{emoji}…{Name} (optional reason)
              e.g.  ☑️☑️⛔️Isaac  →  sessions 1,2,3
              emojis must all be attendance emojis, immediately before the name
"""
from __future__ import annotations

import re
import logging
from telegram import Update, ReactionTypeEmoji
from telegram.ext import ContextTypes, MessageHandler, filters
from storage import load_sessions, load_attendance, save_attendance, load_members, load_config
from helpers import find_member
from board import render_attendance_board

log = logging.getLogger(__name__)

# Base codepoints for emoji matching (strip \ufe0f variation selectors before matching)
EXPECTED_EMOJIS = {"\u2611": "☑️", "\u26d4": "⛔️"}        # ☑ ⛔
ACTUAL_EMOJIS   = {"\u2705": "✅", "\u26a0": "⚠️", "\u274c": "❌"}  # ✅ ⚠ ❌
ALL_EMOJIS = {**EXPECTED_EMOJIS, **ACTUAL_EMOJIS}


def _strip_vs(text: str) -> str:
    """Remove variation selectors (U+FE0F) from text."""
    return text.replace("\ufe0f", "")


def parse_attendance_message(text: str) -> list[dict] | None:
    """
    Parse an attendance update message.
    Returns a list of update dicts or None if not a valid attendance message.
    Each dict has: name, session (1-indexed int), type ('expected'|'actual'),
                   value (emoji str), reason (str|None)
    """
    clean = _strip_vs(text.strip())
    if not clean:
        return None

    # Extract leading attendance emojis
    emojis: list[tuple[str, str]] = []  # (type, value)
    i = 0
    while i < len(clean):
        found = False
        for base, value in ALL_EMOJIS.items():
            if clean[i:].startswith(base):
                att_type = "expected" if base in EXPECTED_EMOJIS else "actual"
                emojis.append((att_type, value))
                i += len(base)
                found = True
                break
        if not found:
            break

    if not emojis:
        return None

    remainder = clean[i:].strip()

    # ── Try SINGLE mode first if exactly one emoji ────────────────────────────
    if len(emojis) == 1:
        # Pattern: Name SessionNumber (optional reason)
        m = re.match(r"^([A-Za-z][A-Za-z\s]*)(\d+)(?:\s+\(([^)]+)\))?$", remainder)
        if m:
            name = m.group(1).strip()
            session_num = int(m.group(2))
            reason = m.group(3)
            att_type, att_value = emojis[0]
            return [{"name": name, "session": session_num, "type": att_type,
                     "value": att_value, "reason": reason}]

    # ── Positional mode: emojis map to sessions 1, 2, 3… ─────────────────────
    # remainder should be: Name (optional reason)
    m = re.match(r"^([A-Za-z][A-Za-z\s]*)(?:\s+\(([^)]+)\))?$", remainder.strip())
    if m:
        name = m.group(1).strip()
        reason = m.group(2)
        updates = []
        for idx, (att_type, att_value) in enumerate(emojis):
            updates.append({
                "name": name,
                "session": idx + 1,
                "type": att_type,
                "value": att_value,
                "reason": reason,
            })
        return updates

    return None


async def handle_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle attendance messages sent in the group chat."""
    cfg = load_config()
    group_chat_id = cfg.get("group_chat_id")
    if not group_chat_id:
        return

    # Only process messages in the configured group chat
    if update.effective_chat.id != group_chat_id:
        return

    msg_text = update.message.text or ""
    updates = parse_attendance_message(msg_text)
    if not updates:
        return

    sessions = load_sessions()
    members_data = load_members()
    active = members_data.get("active", [])
    lta = members_data.get("long_term_absent", [])
    all_members = active + lta

    state = load_attendance()
    attendance = state.get("attendance", {})
    any_valid = False

    for upd in updates:
        name = upd["name"]
        session_num = upd["session"]
        att_type = upd["type"]
        att_value = upd["value"]

        # Validate session number
        if session_num < 1 or session_num > len(sessions):
            log.debug("Session %d out of range (have %d)", session_num, len(sessions))
            continue

        # Find member (case-insensitive, active + long-term absent)
        member = find_member(name, all_members)
        if not member:
            log.debug("Member '%s' not found", name)
            continue

        matched_name = member["name"]
        sid = str(session_num)

        # Ensure member entry exists
        if matched_name not in attendance:
            attendance[matched_name] = {
                str(i + 1): {"expected": "▫️", "actual": "▫️"}
                for i in range(len(sessions))
            }

        # Ensure session slot exists
        if sid not in attendance[matched_name]:
            attendance[matched_name][sid] = {"expected": "▫️", "actual": "▫️"}

        # Apply update
        if att_type == "expected":
            attendance[matched_name][sid]["expected"] = att_value
        else:
            attendance[matched_name][sid]["actual"] = att_value

        any_valid = True

    if not any_valid:
        return

    state["attendance"] = attendance

    # Auto-detect pinned message if board_message_id was never saved
    # (e.g. the board was posted manually or the state file was reset).
    board_msg_id = state.get("board_message_id")
    board_chat_id = state.get("board_chat_id")
    if not board_msg_id:
        try:
            chat = await context.bot.get_chat(group_chat_id)
            if chat.pinned_message:
                board_msg_id = chat.pinned_message.message_id
                board_chat_id = group_chat_id
                state["board_message_id"] = board_msg_id
                state["board_chat_id"] = board_chat_id
                log.info("Auto-detected pinned message id=%s", board_msg_id)
        except Exception as e:
            log.warning("Could not auto-detect pinned message: %s", e)

    save_attendance(state)

    # Edit the pinned board message
    if board_msg_id and board_chat_id:
        try:
            new_text = render_attendance_board(sessions, active, lta, attendance)
            await context.bot.edit_message_text(
                chat_id=board_chat_id,
                message_id=board_msg_id,
                text=new_text,
            )
        except Exception as e:
            log.warning("Could not edit board message: %s", e)

    # React with 👍 to confirm the update.
    # Telegram reactions only accept a fixed emoji set (no variation selectors).
    # ✔️/✅ are not in the allowed list; 👍 is universally supported.
    try:
        await update.message.set_reaction([ReactionTypeEmoji("\U0001F44D")])
    except Exception as e:
        log.debug("Reaction not set: %s", e)


def build_handler() -> MessageHandler:
    # Filter: text messages in group/supergroup chats starting with an attendance emoji.
    # We use a broad filter and let parse_attendance_message do the detailed check.
    att_filter = (
        filters.TEXT
        & ~filters.COMMAND
        & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
    )
    return MessageHandler(att_filter, handle_attendance)
