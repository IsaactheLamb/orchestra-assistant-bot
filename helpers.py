from __future__ import annotations
from datetime import datetime, timedelta, date as date_type
import pytz
from storage import load_config

AEST = pytz.timezone("Australia/Melbourne")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def is_admin(user_id: int) -> bool:
    cfg = load_config()
    return user_id in cfg.get("coordinator_ids", [])


def find_member(name: str, members: list) -> dict | None:
    name_lower = name.strip().lower()
    for m in members:
        if m["name"].lower() == name_lower:
            return m
    return None


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def format_date_display(date_str: str) -> str:
    """'2024-04-17' -> '17 Apr'"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.day} {d.strftime('%b')}"


def format_date_long(date_str: str) -> str:
    """'2024-04-17' -> '17th April'"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{ordinal(d.day)} {d.strftime('%B')}"


def format_time_display(time_str: str) -> str:
    """'21:15' -> '9:15PM', '07:00' -> '7:00AM', 'After Service' -> 'After Service'"""
    if not time_str or ":" not in time_str:
        return time_str or ""
    h, m = map(int, time_str.split(":"))
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{period}"


def parse_time_input(text: str) -> str | None:
    """Parse user time input -> 'HH:MM' 24h or None."""
    import re
    text = text.strip()
    if text.lower() == "after service":
        return "After Service"
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", text, re.IGNORECASE)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        period = (m.group(3) or "").lower()
        if period == "pm" and h != 12:
            h += 12
        elif period == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"
    # No colon, try e.g. "9PM"
    m = re.match(r"^(\d{1,2})\s*(am|pm)$", text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        period = m.group(2).lower()
        if period == "pm" and h != 12:
            h += 12
        elif period == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def parse_date_input(text: str) -> str | None:
    """Parse user date input -> 'YYYY-MM-DD' or None."""
    text = text.strip()
    now = datetime.now(AEST)
    for fmt in ["%d %b", "%d %B", "%b %d", "%B %d", "%d/%m", "%d-%m"]:
        try:
            parsed = datetime.strptime(text, fmt)
            parsed = parsed.replace(year=now.year)
            if parsed.date() < now.date():
                parsed = parsed.replace(year=now.year + 1)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get_week_monday(from_date=None) -> date_type:
    if from_date is None:
        from_date = datetime.now(AEST).date()
    elif isinstance(from_date, datetime):
        from_date = from_date.date()
    return from_date - timedelta(days=from_date.weekday())


def get_week_dates(monday=None) -> dict:
    if monday is None:
        monday = get_week_monday()
    return {name: monday + timedelta(days=i) for i, name in enumerate(DAY_NAMES)}


def build_default_sessions(monday: date_type, saturday_mode: str) -> list:
    """Build the default 3 sessions for the week starting monday."""
    cfg = load_config()
    defaults = cfg.get("default_sessions", [])
    week_dates = get_week_dates(monday)
    sessions = []
    for i, ds in enumerate(defaults, 1):
        day = ds["day"]
        session_date = week_dates.get(day)
        if not session_date:
            continue
        if day == "Saturday":
            if saturday_mode == "church":
                time_ = ds.get("time_church", "07:00")
                end_time = ds.get("end_time_church", "09:00")
            else:
                time_ = ds.get("time_regular", "16:00")
                end_time = ds.get("end_time_regular", "18:00")
        else:
            time_ = ds.get("time", "After Service")
            end_time = ds.get("end_time")
        sessions.append({
            "id": i,
            "day": day,
            "date": session_date.strftime("%Y-%m-%d"),
            "time": time_,
            "end_time": end_time,
            "location": ds.get("location", "TBC"),
        })
    return sessions


def init_attendance_state(sessions: list, monday: date_type) -> dict:
    """Create a fresh attendance state for the week."""
    members_data = __import__("storage").load_members()
    active = members_data.get("active", [])
    attendance = {}
    for member in active:
        name = member["name"]
        attendance[name] = {
            str(i + 1): {"expected": "▫️", "actual": "▫️"}
            for i in range(len(sessions))
        }
    return {
        "week_of": monday.strftime("%Y-%m-%d"),
        "board_message_id": None,
        "board_chat_id": None,
        "session_report_prayers": {},
        "attendance": attendance,
    }
