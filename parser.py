"""Schedule message parser for Orchestra Bot."""

import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

# ── Emoji constants ────────────────────────────────────────────────────────────

NUMERAL_EMOJIS: List[str] = [
    '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣',
    '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟',
]

EMOJI_EXPECTED_YES  = '☑️'   # attending (expected)
EMOJI_EXPECTED_NO   = '⛔️'  # not attending (expected)
EMOJI_ACTUAL_ONTIME = '✅'   # on time
EMOJI_ACTUAL_LATE   = '⚠️'  # late
EMOJI_ACTUAL_ABSENT = '❌'   # absent
EMOJI_UNSET         = '▫️'   # not yet set

STATUS_EMOJIS: List[str] = [
    EMOJI_EXPECTED_YES, EMOJI_EXPECTED_NO,
    EMOJI_ACTUAL_ONTIME, EMOJI_ACTUAL_LATE,
    EMOJI_ACTUAL_ABSENT, EMOJI_UNSET,
]
# Sort longest-first so prefix matching always picks the most specific emoji.
STATUS_EMOJIS_SORTED = sorted(STATUS_EMOJIS, key=len, reverse=True)

SECTION_EMOJIS: List[str] = ['🎻', '🎵', '🎺', '🥁', '🎸', '🎹', '🪘', '🎷', '🎼', '🪗']

# Words / phrases that identify legend / key lines – skip these.
LEGEND_KEYWORDS: List[str] = [
    'Exp:', 'Act:', 'On time', 'Expected', 'Actual',
]

# ── Regexes ───────────────────────────────────────────────────────────────────

TIME_RE     = re.compile(r'\b(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)\b')
LOCATION_RE = re.compile(r'📍\s*([^\n\)\]]+)')
REASON_RE   = re.compile(r'[\(\[]([^\)\]]+)[\)\]]')

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Session:
    number:   int    # 1-based
    emoji:    str    # e.g. '1️⃣'
    date_str: str    # e.g. 'Wed 16 Apr'
    title:    str    # e.g. 'Orchestra Rehearsal'
    time_str: str    # e.g. '9:15PM'
    location: str    # e.g. 'NC, Babyroom'


@dataclass
class Member:
    name:    str                          # clean name
    reason:  str                          # absence reason or ''
    section: str                          # section label
    columns: List[Tuple[str, str]] = field(default_factory=list)
    # columns[i] = (expected_emoji, actual_emoji) for session i


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_decorative(line: str) -> bool:
    """True if line consists only of repeated non-alphanumeric decoration."""
    cleaned = re.sub(r'[\s\u200b\u200c\u200d\ufe0f]', '', line)
    if not cleaned:
        return True
    alnum = re.sub(r'\w', '', cleaned, flags=re.UNICODE)
    # If stripping word chars leaves nothing extra it's all word-chars → not decorative.
    # If original has zero word chars → purely decorative.
    word_chars = re.findall(r'\w', cleaned, flags=re.UNICODE)
    if not word_chars:
        return True
    return False


def starts_with_numeral_emoji(line: str) -> Optional[Tuple[str, int, str]]:
    """Returns (emoji, 1-based-index, remainder) or None."""
    for idx, emoji in enumerate(NUMERAL_EMOJIS):
        if line.startswith(emoji):
            return emoji, idx + 1, line[len(emoji):]
    return None


def is_section_header(line: str) -> bool:
    """True for lines like '🎻 STRINGS' (section emoji, no pipe)."""
    for emoji in SECTION_EMOJIS:
        if line.startswith(emoji) and '|' not in line:
            return True
    return False


def extract_section_name(line: str) -> str:
    for emoji in SECTION_EMOJIS:
        if line.startswith(emoji):
            return line[len(emoji):].strip()
    return line.strip()


def extract_emojis_from_str(s: str) -> List[str]:
    """Return all STATUS_EMOJIS found in order, left-to-right."""
    emojis: List[str] = []
    i = 0
    while i < len(s):
        matched = False
        for emoji in STATUS_EMOJIS_SORTED:
            if s[i:].startswith(emoji):
                emojis.append(emoji)
                i += len(emoji)
                matched = True
                break
        if not matched:
            i += 1
    return emojis


def parse_status_columns(prefix: str) -> List[Tuple[str, str]]:
    """
    Parse '☑️▫️|▫️▫️|▫️▫️' into [(expected, actual), ...] per session column.
    Each column group is two status emojis; missing ones default to EMOJI_UNSET.
    """
    columns: List[Tuple[str, str]] = []
    for part in prefix.split('|'):
        emojis = extract_emojis_from_str(part)
        expected = emojis[0] if len(emojis) > 0 else EMOJI_UNSET
        actual   = emojis[1] if len(emojis) > 1 else EMOJI_UNSET
        columns.append((expected, actual))
    return columns


# ── Session line parsing ──────────────────────────────────────────────────────

def try_parse_session_line(line: str) -> Optional[Session]:
    """
    Try to parse a session definition line.
    Format (flexible): 1️⃣ [date] | [title] | [time] (📍 [location])
    Returns None if line is not a session definition.
    """
    result = starts_with_numeral_emoji(line)
    if not result:
        return None

    emoji, number, rest = result
    rest = rest.strip()

    # Must contain at least one letter to be a real session line
    # (column header lines like '1️⃣*️⃣|2️⃣*️⃣|3️⃣*️⃣' have no letters).
    if not re.search(r'[a-zA-Z]', rest):
        return None

    # Skip legend lines embedded in session-style lines
    if any(kw in rest for kw in LEGEND_KEYWORDS):
        return None

    parts = [p.strip() for p in rest.split('|')]

    date_str = parts[0] if parts else 'TBC'
    title    = parts[1].strip() if len(parts) > 1 else 'Unknown'

    # Extract location from full rest
    location = 'TBC'
    loc_match = LOCATION_RE.search(rest)
    if loc_match:
        location = loc_match.group(1).strip().rstrip(')').strip()

    # Extract time from full rest
    time_str = 'TBC'
    time_match = TIME_RE.search(rest)
    if time_match:
        time_str = time_match.group(1).strip()

    # Clean title: remove time, location bleed, stray parentheses
    title = re.sub(r'\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'📍.*', '', title)
    title = re.sub(r'\(.*?\)', '', title)
    title = title.strip()
    if not title:
        title = 'Unknown'

    return Session(
        number=number,
        emoji=emoji,
        date_str=date_str.strip(),
        title=title,
        time_str=time_str,
        location=location,
    )


# ── Member line parsing ───────────────────────────────────────────────────────

def try_parse_member_line(line: str, section: str) -> Optional[Member]:
    """
    Try to parse an attendance member line.
    Format: [status_columns] [number.] name [(reason)]
    The status_columns block is a sequence of status emojis separated by |.
    """
    if '|' not in line:
        return None

    # Walk character-by-character to find where the emoji/pipe prefix ends.
    i = 0
    last_boundary = 0
    while i < len(line):
        if line[i] == '|':
            i += 1
            last_boundary = i
            continue
        found = False
        for emoji in STATUS_EMOJIS_SORTED:
            if line[i:].startswith(emoji):
                i += len(emoji)
                last_boundary = i
                found = True
                break
        if not found:
            break

    status_prefix = line[:last_boundary]
    name_part = line[last_boundary:].strip()

    if not status_prefix or not name_part:
        return None

    # Reject lines where "name_part" looks like session content (contains 📍 or numeral emoji)
    if '📍' in name_part:
        return None

    columns = parse_status_columns(status_prefix)

    # Remove leading ordinal like "1. " or "13. "
    name_part = re.sub(r'^\d+\.\s*', '', name_part).strip()

    # Extract reason in () or []
    reason = ''
    reason_match = REASON_RE.search(name_part)
    if reason_match:
        reason = reason_match.group(1).strip()
        name_part = name_part[:reason_match.start()].strip()

    name = name_part.strip()
    if not name:
        return None

    return Member(name=name, reason=reason, section=section, columns=columns)


# ── Top-level parser ──────────────────────────────────────────────────────────

def parse_schedule(text: str) -> Tuple[List[Session], List[Member]]:
    """
    Parse the full schedule message text.
    Returns (sessions, members).
    Raises ValueError if nothing useful could be extracted.
    """
    sessions: List[Session] = []
    members:  List[Member]  = []
    current_section = ''

    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if not line:
            continue

        if is_decorative(line):
            continue

        if any(kw in line for kw in LEGEND_KEYWORDS):
            continue

        session = try_parse_session_line(line)
        if session:
            sessions.append(session)
            continue

        if is_section_header(line):
            current_section = extract_section_name(line)
            continue

        if '|' in line:
            member = try_parse_member_line(line, current_section)
            if member:
                members.append(member)

    if not sessions and not members:
        raise ValueError('Could not parse schedule – no sessions or members found')

    return sessions, members


# ── Status helpers ────────────────────────────────────────────────────────────

def is_long_term_absent(member: Member, num_sessions: int) -> bool:
    """True if member has ⛔️ in the *expected* slot for every session column."""
    check = min(num_sessions, len(member.columns))
    if check == 0:
        return False
    return all(member.columns[i][0] == EMOJI_EXPECTED_NO for i in range(check))


def get_member_status(member: Member, session_idx: int) -> str:
    """
    Returns one of 'attending' | 'late' | 'absent' | 'unset'
    based on the *actual* emoji for the given session (0-based).
    """
    if session_idx >= len(member.columns):
        return 'unset'
    actual = member.columns[session_idx][1]
    if actual == EMOJI_ACTUAL_ONTIME:
        return 'attending'
    if actual == EMOJI_ACTUAL_LATE:
        return 'late'
    if actual == EMOJI_ACTUAL_ABSENT:
        return 'absent'
    return 'unset'


# ── Date formatting ───────────────────────────────────────────────────────────

_DAY_MAP = {
    'Mon': 'Monday', 'Tue': 'Tuesday', 'Wed': 'Wednesday',
    'Thu': 'Thursday', 'Fri': 'Friday', 'Sat': 'Saturday', 'Sun': 'Sunday',
}
_MONTH_MAP = {
    'Jan': 'January', 'Feb': 'February', 'Mar': 'March', 'Apr': 'April',
    'May': 'May',     'Jun': 'June',     'Jul': 'July',  'Aug': 'August',
    'Sep': 'September', 'Oct': 'October', 'Nov': 'November', 'Dec': 'December',
}


def _ordinal(n: int) -> str:
    suffix = 'th' if 11 <= n <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def format_date(date_str: str) -> str:
    """'Wed 16 Apr' → 'Wednesday, 16th April'.  Falls back to original on failure."""
    parts = date_str.split()
    result: List[str] = []
    for p in parts:
        if p in _DAY_MAP:
            result.append(_DAY_MAP[p])
        elif p in _MONTH_MAP:
            result.append(_MONTH_MAP[p])
        elif p.isdigit():
            result.append(_ordinal(int(p)))
        else:
            result.append(p)
    if not result:
        return date_str
    if len(result) == 1:
        return result[0]
    return f'{result[0]}, {" ".join(result[1:])}'


# ── Report formatter ──────────────────────────────────────────────────────────

def format_session_report(
    session: Session,
    members: List[Member],
    num_sessions: int,
) -> str:
    """Generate the flat attendance report for a single session."""
    session_idx = session.number - 1

    active   = [m for m in members if not is_long_term_absent(m, num_sessions)]
    lt_absent = [m for m in members if is_long_term_absent(m, num_sessions)]
    total    = len(active)

    attending: List[Tuple[str, Member]] = []
    late:      List[Member] = []
    absent:    List[Member] = []

    for m in active:
        status = get_member_status(m, session_idx)
        if status == 'attending':
            attending.append(('✅', m))
        elif status == 'late':
            late.append(m)
        elif status == 'absent':
            absent.append(m)
        else:  # unset → listed under attending as unconfirmed
            attending.append(('◽️', m))

    lines: List[str] = []
    lines.append(f'┌ {session.title.upper()}')
    lines.append(f'📆 {format_date(session.date_str)} | {session.time_str}')
    lines.append(f'📍 {session.location}')
    lines.append('🙏 Representative Prayer:')
    lines.append('')
    lines.append(f'Total: {total}')
    lines.append('')

    # Attending
    lines.append(f'✅ Attending ({len(attending)}/{total})')
    if attending:
        for i, (prefix, m) in enumerate(attending, 1):
            lines.append(f'{prefix}{i}. {m.name}')
    else:
        lines.append('—')
    lines.append('')

    # Late
    lines.append(f'🕐 Late ({len(late)}/{total})')
    if late:
        for i, m in enumerate(late, 1):
            lines.append(f'⚠️{i}. {m.name}')
    else:
        lines.append('—')
    lines.append('')

    # Absent (session-absent active members + long-term absent)
    absent_display: List[Tuple[str, str]] = (
        [(m.name, '') for m in absent] +
        [(m.name, m.reason) for m in lt_absent]
    )
    lines.append(f'❌ Absent ({len(absent_display)}/{total + len(lt_absent)})')
    if absent_display:
        for i, (name, reason) in enumerate(absent_display, 1):
            suffix = f' ({reason})' if reason else ''
            lines.append(f'❌{i}. {name}{suffix}')
    else:
        lines.append('—')

    return '\n'.join(lines)
