"""Expense tracker (add-on).  "spent 200 on lunch" / "kharcha 50 chai" / "aaj kitna kharch hua" / "this month expenses".
Stored locally in jarvis_addons/data/expenses.json (git-ignored). Amounts are plain numbers in the owner's own currency."""
import os
import re
from collections import defaultdict
from datetime import datetime

from ._rules import DATA, hook, load_json, save_json

FILE = os.path.join(DATA, "expenses.json")
NUM = r"(\d+(?:\.\d+)?)"
ADD_RX = [re.compile(rf"^(?:jarvis[,\s]+)?(?:i\s+)?(?:spent|paid)\s+(?:rs\.?\s*|₹\s*)?{NUM}\s*(?:rupees?|rs|inr)?\s*(?:on|for)?\s*(.*)$", re.I),
          re.compile(rf"^(?:jarvis[,\s]+)?(?:kharcha|kharch|expense)\s+(?:rs\.?\s*|₹\s*)?{NUM}\s*(?:rupees?|rs)?\s*(?:ka|ke liye|on|for)?\s*(.*)$", re.I),
          re.compile(rf"^(?:jarvis[,\s]+)?(?:rs\.?\s*|₹\s*)?{NUM}\s*(?:rupees?|rs|rupay[ea])?\s+(?:kharch|kharcha)\s+(?:kiye|kiya|hua|huye)?\s*(?:on|for|me|mein)?\s*(.*)$", re.I)]
TODAY_RX = re.compile(r"\b(?:aaj|today)\b.{0,20}\b(?:kharch\w*|spend\w*|spent|expenses?)\b|\b(?:kharch\w*|expenses?)\b.{0,12}\b(?:aaj|today)\b", re.I)
MONTH_RX = re.compile(r"\b(?:is\s+(?:mahine|month)|this\s+month|monthly)\b.{0,20}\b(?:kharch\w*|spend\w*|spent|expenses?)\b|\b(?:kharch\w*|expenses?)\b.{0,16}\b(?:is\s+(?:mahine|month)|this\s+month)\b", re.I)


def _rows():
    return load_json(FILE, [])


def add(amount, what="", now=None):
    rows = _rows()
    rows.append({"amount": float(amount), "what": (what or "").strip() or "misc", "ts": (now or datetime.now()).isoformat(timespec="minutes")})
    save_json(FILE, rows)


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def summary(prefix, label):
    rows = [r for r in _rows() if r["ts"].startswith(prefix)]
    if not rows:
        return f"No expenses recorded {label}, Boss."
    by = defaultdict(float)
    for r in rows:
        by[r["what"].lower()] += r["amount"]
    top = ", ".join(f"{k} {_fmt(v)}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:3])
    return f"{label.capitalize()} you spent {_fmt(sum(by.values()))} in total. Top: {top}."


def handle(cmd, now=None):
    c = cmd.strip()
    now = now or datetime.now()
    for rx in ADD_RX:
        m = rx.match(c)
        if m:
            add(m.group(1), m.group(2), now)
            return f"Logged {_fmt(float(m.group(1)))} for {m.group(2).strip() or 'misc'}, Boss."
    if MONTH_RX.search(c):
        return summary(now.strftime("%Y-%m"), "this month")
    if TODAY_RX.search(c):
        return summary(now.strftime("%Y-%m-%d"), "today")
    return None


def install(host):
    return hook(host, "_expenses_addon", handle, "expenses")
