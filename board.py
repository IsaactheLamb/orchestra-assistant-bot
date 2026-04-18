from helpers import format_date_display, format_time_display, format_date_long, get_week_monday
from datetime import datetime

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
SECTION_ORDER = [
    ("🎻 STRINGS", "Strings"),
    ("🎵 WINDS", "Winds"),
    ("🎺 BRASS", "Brass"),
    ("🥁 PERCUSSION", "Percussion"),
]
SEP = "•••••••••••••••••••••"


def _html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_attendance_board(sessions: list, active_members: list,
                             long_term_absent: list, attendance: dict) -> str:
    """Render the attendance board as HTML-formatted text for Telegram."""
    lines = ["🎵 Orchestra Schedule"]
    lines.append(SEP)
    lines.append("")
    lines.append("Expected Schedule")

    for i, s in enumerate(sessions):
        num = NUMBER_EMOJIS[i]
        day_abbr = s["day"][:3]
        date_disp = format_date_display(s["date"])
        time_disp = format_time_display(s["time"])
        loc = _html(s["location"])
        title = s.get("title", "")
        if title:
            lines.append(f"{num} <b>{day_abbr} {date_disp} | {_html(title)} | {time_disp}</b> (📍 {loc})")
        else:
            lines.append(f"{num} <b>{day_abbr} {date_disp} | {time_disp}</b> (📍 {loc})")

    lines.append("")
    lines.append("1️⃣ Exp: ☑️ Attending | ⚫️ Absent | 🕐 Late")
    lines.append("*️⃣ Act: ✅ On time | ⚠️ Late | ❌ MIA")
    lines.append("")
    lines.append(SEP)
    lines.append("")

    # Header row: 1️⃣*️⃣|2️⃣*️⃣|…
    if sessions:
        header_parts = [f"{NUMBER_EMOJIS[i]}*️⃣" for i in range(len(sessions))]
        lines.append("|".join(header_parts))
    lines.append("━━━━━━━━━━━━━━━━━━━━━")

    lines.append("")

    member_num = 1
    first_section = True
    for section_label, section_key in SECTION_ORDER:
        section_members = [m for m in active_members if m["section"] == section_key]
        if not section_members:
            continue
        if not first_section:
            lines.append("")
        first_section = False
        lines.append(section_label)
        for member in section_members:
            name = member["name"]
            member_att = attendance.get(name, {})
            slots = []
            for i in range(len(sessions)):
                sid = str(i + 1)
                att = member_att.get(sid, {"expected": "▫️", "actual": "▫️"})
                slots.append(f"{att['expected']}{att['actual']}")
            reason = member_att.get("reason", "")
            name_display = f"{_html(name)} ({_html(reason)})" if reason else _html(name)
            lines.append(f"{'|'.join(slots)}  {member_num}. {name_display}")
            member_num += 1

    if long_term_absent:
        lines.append("")
        lines.append("⚫️ LONG-TERM ABSENT")
        for member in long_term_absent:
            name = member["name"]
            reason = member.get("reason", "")
            slots = "|".join(["⚫️⚫️"] * len(sessions))
            lines.append(f"{slots}  {_html(name)} ({_html(reason)})")

    lines.append(SEP)
    total_active = len(active_members)
    total_absent = len(long_term_absent)
    if total_absent > 0:
        lines.append(f"Total: {total_active} (+{total_absent} long-term absent)")
    else:
        lines.append(f"Total: {total_active}")

    return "\n".join(lines)


def render_session_report(session: dict, session_idx: int,
                          active_members: list, long_term_absent: list,
                          attendance: dict, prayer: str = "") -> str:
    title = session.get("title", "").strip() or "Session"
    lines = [f"┌ {title.upper()}"]

    day_name = session["day"]
    date_long = format_date_long(session["date"])
    time_disp = format_time_display(session["time"])

    if session.get("end_time"):
        end_disp = format_time_display(session["end_time"])
        lines.append(f"📆 {day_name}, {date_long} | {time_disp} – {end_disp}")
    else:
        lines.append(f"📆 {day_name}, {date_long} | {time_disp}")

    lines.append(f"📍 {session['location']}")
    lines.append("")

    total = len(active_members)
    lines.append(f"Total: {total}")
    lines.append("")

    sid = str(session_idx + 1)
    attending, late, unconfirmed, absent = [], [], [], []

    for i, member in enumerate(active_members, 1):
        name = member["name"]
        att = attendance.get(name, {}).get(sid, {"expected": "▫️", "actual": "▫️"})
        exp, act = att["expected"], att["actual"]
        if act == "⚠️":
            late.append((i, name))
        elif exp in ("⚫️", "⛔️") or act == "❌":
            absent.append((i, name))
        elif act == "✅":
            attending.append((i, name))
        else:
            unconfirmed.append((i, name))

    lines.append(f"✅ Attending ({len(attending)}/{total})")
    if attending:
        for i, name in attending:
            lines.append(f"✅{i}. {name}")
    else:
        lines.append("—")

    lines.append("")
    lines.append(f"🕐 Late ({len(late)}/{total})")
    if late:
        for i, name in late:
            lines.append(f"⚠️{i}. {name}")
    else:
        lines.append("—")

    if unconfirmed:
        lines.append("")
        lines.append(f"◽️ Unconfirmed ({len(unconfirmed)}/{total})")
        for i, name in unconfirmed:
            lines.append(f"◽️{i}. {name}")

    lines.append("")
    lines.append(f"❌ Absent ({len(absent)}/{total})")
    if absent:
        for seq, (i, name) in enumerate(absent, 1):
            lines.append(f"❌{seq}. {name}")
    else:
        lines.append("—")

    return "\n".join(lines)
