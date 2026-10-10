"""Quick notes (add-on).  "note kar: milk lena" / "take a note call mom" / "show my notes" / "last note" / "clear notes".
Stored locally in jarvis_addons/data/notes.json (git-ignored)."""
import os
import re
from datetime import datetime

from ._rules import DATA, hook, load_json, save_json

FILE = os.path.join(DATA, "notes.json")
ADD_RX = [re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:take|make|add|save)\s+(?:a\s+)?note\s*[:,-]?\s*(.+)$", re.I),
          re.compile(r"^(?:jarvis[,\s]+)?note\s+(?:kar|karo|kar do|likh|likho|likh do)\s*[:,-]?\s*(.+)$", re.I),
          re.compile(r"^(?:jarvis[,\s]+)?note\s*[:,-]\s*(.+)$", re.I),
          re.compile(r"^(?:jarvis[,\s]+)?(?:yaad rakh|yaad rakhna|remember that)\s*[:,-]?\s*(.+)$", re.I)]
LIST_RX = re.compile(r"\b(?:show|read|list|meri|my|saare|sab)\b.{0,12}\bnotes?\b|\bnotes?\s+(?:dikhao|sunao|padho|batao)\b", re.I)
LAST_RX = re.compile(r"\b(?:last|latest|pichla|aakhri)\s+note\b", re.I)
CLEAR_RX = re.compile(r"\b(?:clear|delete|remove|saaf|hata)\b.{0,10}\b(?:all\s+)?notes\b", re.I)


def _notes():
    return load_json(FILE, [])


def add(text, now=None):
    items = _notes()
    items.append({"text": text.strip(), "ts": (now or datetime.now()).isoformat(timespec="minutes")})
    save_json(FILE, items)
    return len(items)


def handle(cmd):
    c = cmd.strip()
    for rx in ADD_RX:
        m = rx.match(c)
        if m and m.group(1).strip():
            n = add(m.group(1))
            return f"Noted, Boss. You have {n} note{'s' if n != 1 else ''}."
    if CLEAR_RX.search(c):
        save_json(FILE, [])
        return "All notes cleared, Boss."
    if LAST_RX.search(c):
        items = _notes()
        return f"Your last note: {items[-1]['text']}." if items else "You have no notes yet, Boss."
    if LIST_RX.search(c):
        items = _notes()
        if not items:
            return "You have no notes yet, Boss."
        return "Your notes: " + "; ".join(f"{i}. {n['text']}" for i, n in enumerate(items[-10:], 1)) + "."
    return None


def install(host):
    return hook(host, "_notes_addon", handle, "notes")
