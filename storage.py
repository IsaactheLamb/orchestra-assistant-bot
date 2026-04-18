import json
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _load(filename: str) -> dict:
    path = BASE_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(filename: str, data: dict) -> None:
    path = BASE_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    return _load("config.json")


def save_config(cfg: dict) -> None:
    _save("config.json", cfg)


def load_members() -> dict:
    """Returns {'active': [...], 'long_term_absent': [...]}"""
    data = _load("members.json")
    data.setdefault("active", [])
    data.setdefault("long_term_absent", [])
    return data


def save_members(data: dict) -> None:
    _save("members.json", data)


def load_sessions() -> list:
    """Returns list of session dicts."""
    data = _load("sessions.json")
    return data.get("sessions", [])


def save_sessions(sessions: list) -> None:
    _save("sessions.json", {"sessions": sessions})


def load_attendance() -> dict:
    data = _load("attendance_state.json")
    if not data:
        return {
            "week_of": None,
            "board_message_id": None,
            "board_chat_id": None,
            "session_report_prayers": {},
            "attendance": {},
        }
    data.setdefault("week_of", None)
    data.setdefault("board_message_id", None)
    data.setdefault("board_chat_id", None)
    data.setdefault("session_report_prayers", {})
    data.setdefault("attendance", {})
    return data


def save_attendance(state: dict) -> None:
    _save("attendance_state.json", state)
