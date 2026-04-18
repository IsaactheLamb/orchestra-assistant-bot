# Orchestra Assistant Bot

A Telegram bot for orchestra coordinators. It does three things:

1. **Session Report** — forward the weekly schedule in DM, tap a session, get a formatted attendance report.
2. **Live Checklist** — post a pinned checklist to a group topic; members update it by sending emoji-prefixed messages.
3. **Live Schedule Board** — post a pinned attendance board; members update their own row live.

---

## Table of Contents

- [User Guide](#user-guide)
  - [For Members — updating checklists and boards](#for-members--updating-checklists-and-boards)
  - [For Coordinators — session report](#for-coordinators--session-report)
  - [For Coordinators — posting a checklist](#for-coordinators--posting-a-checklist)
  - [For Coordinators — posting a schedule board](#for-coordinators--posting-a-schedule-board)
- [Commands](#commands)
- [Schedule Format Reference](#schedule-format-reference)
- [Setup](#setup)
- [File Structure](#file-structure)
- [Timezone](#timezone)

---

## User Guide

### For Members — updating checklists and boards

Send a message in the group topic where the checklist was pinned:

```
[emoji][Name]
```

**Examples:**

| Message sent | Effect on checklist |
|---|---|
| `✅Anna` | Marks Anna as attending |
| `⚠️Ben` | Marks Ben as late |
| `❌Chris` | Marks Chris as absent |
| `◻️Anna` | Resets Anna back to blank |

**Reply rules:**
- If you **reply to the pinned checklist message**, that specific checklist is updated — useful when a topic has more than one active checklist.
- If you **don't reply**, the bot updates the **latest** checklist/board in that topic.
- If you reply to a different message (not a checklist), the bot reacts 🤨 and does nothing.

**Matching rules:**
- Case-insensitive — `✅anna` works the same as `✅Anna`
- Partial names are matched if unique — `✅Ann` will match Anna if no one else starts with "Ann"
- Ambiguous partial matches are ignored silently

**Bot reactions:**
- 👍 — name found, checklist updated
- 🤔 — name not found or ambiguous
- 🤨 — replying to something that isn't a checklist

**Multi-column checklists**

Some checklists have more than one column per person (e.g. two tasks). Send the **full column state** as the prefix:

| Message | Resulting line | Meaning |
|---|---|---|
| `✅◻️Anna` | `✅◻️1. Anna` | Task 1 done, task 2 pending |
| `◻️✅Anna` | `◻️✅1. Anna` | Task 1 pending, task 2 done |
| `✅✅Anna` | `✅✅1. Anna` | Both tasks done |

If you only send one emoji for a multi-column line, the missing columns are **filled with blank squares**:

| Message | Existing line | Resulting line |
|---|---|---|
| `✅Anna` | `◻️◻️1. Anna` | `✅◻️1. Anna` |

---

### For Coordinators — session report

Open a **private chat** with the bot and forward (or paste) the weekly schedule message.

The bot replies with one button per session:

```
Which session to extract?
[1️⃣ Wed 16 Apr – Orchestra Rehearsal]
[2️⃣ Sat 18 Apr – Choir Rehearsal    ]
[3️⃣ Sun 19 Apr – Orchestra Bonding  ]
```

Tap a session. If there is only one session, the bot skips this step. You receive a report like:

```
┌ ORCHESTRA REHEARSAL
📆 Wednesday, 16th April | 9:15PM
📍 Rehearsal Venue

Total: 13

✅ Attending (11/13)
✅1. Member A
✅2. Member B
...

🕐 Late (1/13)
⚠️1. Member C (6:30PM, work)

◽️ Unconfirmed (1/13)
◽️1. Member D

❌ Absent (0/13)
—
```

**Status key:**

| Symbol | Meaning |
|---|---|
| ✅ | Confirmed attending (on time) |
| ⚠️ | Late |
| ◽️ | Unconfirmed — no response yet |
| ❌ | Absent |

The `◽️ Unconfirmed` section is hidden if everyone has confirmed.

Use `/extract` to re-show session buttons without re-forwarding the schedule.

---

### For Coordinators — posting a checklist

Send the bot a private message:

```
/postchecklist [Topic Name]
[checklist text]
```

**Example:**

```
/postchecklist Attendance
◻️◻️1. Member A
◻️◻️2. Member B
◻️◻️3. Member C
◻️◻️4. Member D
◻️◻️5. Member E
```

The bot posts the checklist to the named topic, pins it, and starts monitoring that topic for updates.

> The topic name must match an entry in `config.json → "topics"` (case-insensitive).

Multiple checklists can be active in the same topic. Members should reply to the specific checklist they want to update; otherwise the bot updates the most recent.

---

### For Coordinators — posting a schedule board

`/postschedule` prompts you to enter session details. Then:

```
📅 Enter session details (one per line):

Format:  Date, Time, Location
    or:  Date, Title, Time, Location

Example:
Wed 16 Apr, 9:15PM, Rehearsal Venue
Sat 18 Apr, Youth Gathering, 6:30AM, NC
```

The bot posts an attendance board to the **Attendance** topic (as configured in `topics`), pins it, and starts receiving emoji-prefix updates from members.

---

## Commands

All commands are **coordinator-only** and used in DM with the bot.

| Command | Description |
|---|---|
| `/start` | Show the coordinator keyboard |
| `/help` | Schedule formatting rules |
| `/extract` | Re-show session buttons for the last forwarded schedule |
| `/postchecklist [topic]` | Post and pin a checklist to a group topic |
| `/postschedule` | Enter session details and post a live attendance board |

Non-coordinators receive "Sorry, this command is for coordinators only."

---

## Schedule Format Reference

The weekly schedule message (used for `/extract`) is maintained manually by a coordinator and forwarded to the bot.

**Example:**

```
🎵 Orchestra Schedule
- ••••••••••••••••••••
1️⃣ Wed 16 Apr | Orchestra Rehearsal | 9:15PM (📍 Venue A)
2️⃣ Sat 18 Apr | Choir Rehearsal | 4:00PM (📍 Venue A)
3️⃣ Sun 19 Apr | Orchestra Bonding | 3:00PM (📍 TBC)
1️⃣ Exp: ☑️ Yes · ⛔️ No
*️⃣ Act:  ✅ On time · ⚠️ Late · ❌ Absent
- ••••••••••••••••••••
1️⃣*️⃣|2️⃣*️⃣|3️⃣*️⃣
━━━━━━━━━━━━━━━━━━━━━
🎻 STRINGS
☑️▫️|▫️▫️|▫️▫️ 1. Member A
▫️▫️|▫️▫️|▫️▫️ 2. Member B
🎵 WINDS
☑️⚠️|▫️▫️|▫️▫️ 6. Member C (1️⃣6:30PM, work)
🎺 BRASS
☑️❌|▫️▫️|▫️▫️ 10. Member D (1️⃣Family)
🥁 PERCUSSION
▫️▫️|▫️▫️|▫️▫️ 13. Member E
⛔️⛔️|⛔️⛔️|⛔️⛔️ 14. Member F (🔁Overseas)
⛔️⛔️|⛔️⛔️|⛔️⛔️ 15. Member G (1️⃣Family. 2️⃣Work)
- ••••••••••••••••••••
```

**Rules:**

| Element | Rule |
|---|---|
| Session lines | Must start with a numeral emoji (`1️⃣` `2️⃣` `3️⃣` …) |
| Column separator | `\|` between session columns per member row |
| Status emojis | `▫️` `☑️` `⛔️` `✅` `⚠️` `❌` — left slot = expected, right = actual |
| Location | Follows `📍` |
| Time | Any `H:MM AM/PM` or `HH:MM` token |
| Reason | In `()` or `[]` after the name — see reason format below |
| Long-term absent | Member has `⛔️` in expected slot for all sessions |

**You can freely change:** session titles, dates, times, locations, number of sessions, number of members, section headers (`🎻 STRINGS` etc.), decorative lines.

**Do not change:** session numeral emojis, `|` separator, status emojis, `📍` prefix.

### Reason format

Reasons are written in `()` after the member's name. Use a session emoji prefix to specify which session the reason applies to.

| Format | Meaning | Example |
|---|---|---|
| `(Text)` | Applies to all sessions | `(Overseas)` |
| `(🔁Text)` | Applies to all sessions (explicit) | `(🔁Overseas)` |
| `(1️⃣Text)` | Applies to session 1 only | `(1️⃣Family)` |
| `(1️⃣Text. 2️⃣Text)` | Different reasons per session | `(1️⃣Family. 2️⃣Work)` |

The reason (and time for late members) is shown in the session report:

```
⚠️1. Member C (6:30PM, work)
❌2. Member D (Family)
```

---

## Setup

### 1. Create the bot via BotFather

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and username).
3. Copy the **HTTP API token** — you'll need it for `config.json`.

### 2. Add bot to the group

1. Add the bot to your group.
2. Promote it to **admin** with at least these permissions:
   - Pin Messages
   - Read Messages (so it receives all group messages, not just commands)

> Also go to BotFather → `/mybots` → select your bot → **Bot Settings → Group Privacy → Turn off** so the bot receives every message in the group.

### 3. Get required IDs

**Group chat ID**

1. Send any message in the group.
2. Visit this URL in a browser (replace `TOKEN` with your actual bot token):
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```
3. Find `"chat": {"id": -100xxxxxxxxxx}` — that negative number is your `group_chat_id`.

**Topic thread IDs** (for groups with Topics enabled)

1. Open your group in **Telegram Web** (`web.telegram.org`).
2. Click on a topic. The URL will contain `?thread=NNNNN`.
3. That number is the topic's thread ID. Repeat for each topic you want to use.

Use `1` for the General topic.

**Coordinator Telegram user IDs**

Send a message to **@userinfobot** on Telegram — it replies with your numeric user ID.

### 4. Configure config.json

```json
{
  "bot_token": "PASTE_YOUR_TOKEN_HERE",
  "group_chat_id": -1001234567890,
  "coordinator_ids": [123456789, 987654321],
  "topics": {
    "General": 1,
    "Attendance": 12345
  },
  "active_checklists": {}
}
```

| Field | Description |
|---|---|
| `bot_token` | Token from BotFather |
| `group_chat_id` | Negative integer ID of your group |
| `coordinator_ids` | Telegram user IDs of coordinators (can DM the bot) |
| `topics` | Map of topic name → thread ID (use `1` for General) |
| `active_checklists` | Auto-managed by the bot — do not edit manually |

### 5. Install and run

Python 3.11+ required.

```bash
cd orchestra-assistant-bot
pip install -r requirements.txt
python3 bot.py
```

You should see `Orchestra Assistant Bot starting…` in the console.

---

## File Structure

```
orchestra-assistant-bot/
├── bot.py           — Handlers, commands, entry point
├── parser.py        — Schedule parser and report formatter
├── board.py         — Attendance board renderer
├── storage.py       — JSON persistence
├── helpers.py       — Shared utilities (date/time, member lookup)
├── handlers/        — Handler modules for board flows
├── config.json      — Token, IDs, topics, active checklists
├── members.json     — Roster (active + long-term absent)
├── sessions.json    — Current week's sessions (posted via /postschedule)
├── attendance_state.json  — Live attendance state for the board
├── requirements.txt
└── README.md
```

---

## Timezone

Designed for **Australia/Melbourne** (AEST/AEDT — UTC+10/+11).
