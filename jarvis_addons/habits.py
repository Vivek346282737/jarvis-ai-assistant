"""Water and habit tracker (add-on).  "paani piya" / "drank 2 glasses of water" / "water kitna piya" / "habit done gym" / "habit streak gym".
Stored locally in jarvis_addons/data/habits.json (git-ignored)."""
import os
import re
from datetime import date, datetime, timedelta

from ._rules import DATA, hook, load_json, save_json

FILE = os.path.join(DATA, "habits.json")
GOAL_GLASSES = 8
WATER_ADD_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:i\s+)?(?:drank|had)\s+(\d+|a|one|two|three|four)?\s*(?:glass(?:es)?\s+(?:of\s+)?)?water$"
                          r"|^(?:jarvis[,\s]+)?(?:(\d+|ek|do|teen|char)\s+)?(?:glass\s+)?(?:paani|pani)\s+(?:piya|pi liya|pee liya)$", re.I)
WATER_Q_RX = re.compile(r"\b(?:how much water|water kitna|kitna (?:paani|pani)|water intake)\b", re.I)
DONE_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:habit\s+(?:done|complete|completed)|mark habit)\s+(.+)$|^(?:jarvis[,\s]+)?(.+?)\s+(?:habit\s+)?(?:kar liya|done for today)$", re.I)
STREAK_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:habit\s+streak|streak)\s+(?:of\s+|for\s+)?(.+)$", re.I)
WORDS = {"a": 1, "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3, "four": 4, "char": 4}


def _data():
    return load_json(FILE, {"water": {}, "habits": {}})


def _glasses(tok):
    if not tok:
        return 1
    return int(tok) if tok.isdigit() else WORDS.get(tok.lower(), 1)


def add_water(n, today=None):
    d = _data()
    k = (today or date.today()).isoformat()
    d["water"][k] = d["water"].get(k, 0) + n
    save_json(FILE, d)
    return d["water"][k]


def mark(habit, today=None):
    d = _data()
    days = d["habits"].setdefault(habit.lower().strip(), [])
    k = (today or date.today()).isoformat()
    if k not in days:
        days.append(k)
    save_json(FILE, d)
    return streak(habit, today)


def streak(habit, today=None):
    days = set(_data()["habits"].get(habit.lower().strip(), []))
    day = today or date.today()
    if day.isoformat() not in days:
        day -= timedelta(days=1)
    n = 0
    while day.isoformat() in days:
        n += 1
        day -= timedelta(days=1)
    return n


def handle(cmd, today=None):
    c = cmd.strip().rstrip("?.! ")
    m = WATER_ADD_RX.match(c)
    if m:
        total = add_water(_glasses(m.group(1) or m.group(2)), today)
        left = GOAL_GLASSES - total
        return f"{total} glass{'es' if total != 1 else ''} today, Boss." + (f" {left} more to reach your goal." if left > 0 else " Daily goal reached!")
    if WATER_Q_RX.search(c):
        total = _data()["water"].get((today or date.today()).isoformat(), 0)
        return f"You have had {total} of {GOAL_GLASSES} glasses today, Boss."
    m = STREAK_RX.match(c)
    if m:
        n = streak(m.group(1), today)
        return f"{m.group(1).strip()}: {n} day streak, Boss." if n else f"No streak for {m.group(1).strip()} yet, Boss."
    m = DONE_RX.match(c)
    if m:
        h = (m.group(1) or m.group(2)).strip()
        n = mark(h, today)
        return f"{h} done. {n} day streak, Boss."
    return None


def install(host):
    return hook(host, "_habits_addon", handle, "habits")
