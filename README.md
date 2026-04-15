# Orchestra Assistant Bot

A Telegram bot for orchestra coordinators. Two tasks:

1. **Session Report** — forward the weekly schedule to the bot in DM; tap a session to get a formatted attendance report.
2. **Live Checklist** — post a pinned checklist to a group topic; members update it by sending emoji-prefixed messages.

---

## Table of Contents

- [Setup](#setup)
  - [1. Create the bot via BotFather](#1-create-the-bot-via-botfather)
  - [2. Add bot to the group](#2-add-bot-to-the-group)
  - [3. Get required IDs](#3-get-required-ids)
  - [4. Configure config.json](#4-configure-configjson)
  - [5. Install and run](#5-install-and-run)
- [User Guide — Session Report](#user-guide--session-report)
- [User Guide — Live Checklist](#user-guide--live-checklist)
  - [Posting a checklist](#posting-a-checklist)
  - [Updating the checklist](#updating-the-checklist-members)
  - [Multi-column checklists](#multi-column-checklists)
- [Schedule Format Reference](#schedule-format-reference)
- [Commands](#commands)
- [File Structure](#file-structure)

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

**Coordinator Telegram user IDs**

Send a message to **@userinfobot** on Telegram — it replies with your numeric user ID.

### 4. Configure config.json

```json
{
  "bot_token": "PASTE_YOUR_TOKEN_HERE",
  "group_chat_id": -1001234567890,
  "coordinator_ids": [123456789, 987654321],
  "topics": {
    "Attendance": 12345,
    "General": 1
  },
  "active_checklists": {}
}
```

| Field | Description |
|---|---|
| `bot_token` | Token from BotFather |
| `group_chat_id` | Negative integer ID of your group |
| `coordinator_ids` | Telegram user IDs of coordinators (can DM the bot) |
| `topics` | Map of topic name → thread ID |
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

## User Guide — Session Report

This flow is for **coordinators only**.

### Step 1 — Forward the schedule

Open a **private chat** with the bot and forward (or paste) the weekly schedule message.

### Step 2 — Pick a session

The bot replies with one button per session:

```
Which session to extract?
[1️⃣ Wed 16 Apr – Orchestra Rehearsal]
[2️⃣ Sat 18 Apr – Choir Rehearsal    ]
[3️⃣ Sun 19 Apr – Orchestra Bonding  ]
```

Tap the session you want. If there is only one session, the bot skips this step.

### Step 3 — Receive the report

```
┌ ORCHESTRA REHEARSAL
📆 Wednesday, 16th April | 9:15PM
📍 NC, Babyroom
🙏 Representative Prayer:

Total: 13

✅ Attending (11/13)
◽️1. Isaac
✅2. Cardin
...

🕐 Late (0/13)
—

❌ Absent (4/15)
❌1. Karina
❌2. Jet (Overseas)
❌3. Lolo (Family)
```

**Status key:**

| Symbol | Meaning |
|---|---|
| ✅ | Confirmed attending (on time) |
| ◽️ | Unconfirmed — no response yet |
| ⚠️ | Late |
| ❌ | Absent |

Members with ⛔️ in the expected slot for every session (long-term absent) always appear in ❌ Absent with their reason.

Use `/extract` to re-show session buttons without re-forwarding the schedule.

---

## User Guide — Live Checklist

### Posting a checklist

Send the bot a private message:

```
/postchecklist [Topic Name]
[checklist text]
```

**Example:**

```
/postchecklist Attendance
◻️◻️1. Isaac
◻️◻️2. Karina
◻️◻️3. Katherine
◻️◻️4. Alice
◻️◻️5. Joseph
```

The bot posts the checklist to the named topic, pins it, and starts monitoring that topic for updates.

> The topic name must match an entry in `config.json → "topics"` (case-insensitive).

---

### Updating the checklist (members)

Members send a message in the group topic:

```
[emoji][Name]
```

**Examples:**

| Message sent | Effect on checklist |
|---|---|
| `✅Isaac` | Marks Isaac as attending |
| `⚠️Karina` | Marks Karina as late |
| `❌James` | Marks James as absent |
| `◻️Isaac` | Resets Isaac back to blank |

**Matching rules:**
- Case-insensitive — `✅isaac` works the same as `✅Isaac`
- Partial names are matched if unique — `✅Isa` will match Isaac if no one else starts with "Isa"
- Ambiguous partial matches are ignored silently

**Bot reactions:**
- 👍 — name found, checklist updated
- 🤔 — name not found or ambiguous

---

### Multi-column checklists

If your checklist has multiple columns (e.g. two tasks per person), members send the **full new column state** as the emoji prefix:

**Checklist:**
```
◻️◻️1. Isaac
◻️◻️2. Karina
```

**Updates:**

| Message | Resulting line | Meaning |
|---|---|---|
| `✅◻️Isaac` | `✅◻️1. Isaac` | Task 1 done, task 2 pending |
| `◻️✅Isaac` | `◻️✅1. Isaac` | Task 1 pending, task 2 done |
| `✅✅Isaac` | `✅✅1. Isaac` | Both tasks done |
| `◻️◻️Isaac` | `◻️◻️1. Isaac` | Reset both |

The bot replaces the entire emoji prefix with exactly what you sent. The number of emojis should match the number of columns.

---

## Schedule Format Reference

The coordinator maintains the weekly schedule message manually and forwards it to the bot in DM.

**Example:**

```
🎵 Orchestra Schedule
- ••••••••••••••••••••
1️⃣ Wed 16 Apr | Orchestra Rehearsal | 9:15PM (📍 NC, Babyroom)
2️⃣ Sat 18 Apr | Choir Rehearsal | 4:00PM (📍 NC, Babyroom)
3️⃣ Sun 19 Apr | Orchestra Bonding | 3:00PM (📍 TBC)
1️⃣ Exp: ☑️ Yes · ⛔️ No
*️⃣ Act:  ✅ On time · ⚠️ Late · ❌ Absent
- ••••••••••••••••••••
1️⃣*️⃣|2️⃣*️⃣|3️⃣*️⃣
━━━━━━━━━━━━━━━━━━━━━
🎻 STRINGS
☑️▫️|▫️▫️|▫️▫️ 1. Isaac
▫️▫️|▫️▫️|▫️▫️ 2. Cardin
🎵 WINDS
▫️▫️|▫️▫️|▫️▫️ 6. Karina
🎺 BRASS
▫️▫️|▫️▫️|▫️▫️ 10. Luis
🥁 PERCUSSION
▫️▫️|▫️▫️|▫️▫️ 13. Joseph
⛔️⛔️|⛔️⛔️|⛔️⛔️ 14. Jet (Overseas)
⛔️⛔️|⛔️⛔️|⛔️⛔️ 15. Lolo (Family)
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
| Absence reason | In `()` or `[]` after the name |
| Long-term absent | Member has `⛔️` in expected slot for all sessions |

**You can freely change:** session titles, dates, times, locations, number of sessions, number of members, section headers (`🎻 STRINGS` etc.), decorative lines.

**Do not change:** session numeral emojis, `|` separator, status emojis, `📍` prefix.

---

## Commands

All commands work in **private DM** with the bot (coordinators only).

| Command | Description |
|---|---|
| `/start` | Brief instructions |
| `/help` | Schedule formatting rules |
| `/extract` | Re-show session buttons for the last forwarded schedule |
| `/postchecklist [topic]` | Post and pin a checklist to a group topic |

---

## File Structure

```
orchestra-assistant-bot/
├── bot.py           — Handlers, commands, entry point
├── parser.py        — Schedule parser and report formatter
├── config.json      — Token, IDs, topics, active checklists
├── requirements.txt
└── README.md
```

---

## Timezone

Designed for **Australia/Melbourne** (AEST/AEDT — UTC+10/+11).
