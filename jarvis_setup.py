"""First-time setup for a NEW owner of this copy of JARVIS.   Run:  venv\\Scripts\\python.exe jarvis_setup.py

It only writes the owner's name / pronoun into this folder's own database, then walks through JARVIS's existing setup
commands (AI keys, phone, voice). It never contacts anything and never copies data from anywhere.
It refuses to rename an installation that already has an owner, so it cannot damage an existing setup."""
import argparse
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
PERSONAL_TABLES = ("contacts", "inbox", "spk_embeddings", "people")


def _count(con, table):
    try:
        return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return 0


def existing_owner(db):
    """-> (state, name). state: 'fresh' (no database / empty), 'legacy' (an original install with data but no owner_name),
    'owned' (owner_name already set)."""
    if not os.path.exists(db):
        return "fresh", None
    con = sqlite3.connect(db)
    try:
        try:
            row = con.execute("SELECT val FROM config WHERE key='owner_name'").fetchone()
        except sqlite3.Error:
            return "fresh", None
        if row and row[0] and row[0] != "Owner":           # "Owner" is only the placeholder JARVIS writes when it is started before setup
            return "owned", row[0]
        if row and row[0] == "Owner":
            return "fresh", None
        has_data = any(_count(con, t) for t in PERSONAL_TABLES) or _count(con, "config") > 0
        return ("legacy", None) if has_data else ("fresh", None)
    finally:
        con.close()


def save_owner(db, name, pronoun, about):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    for k, v in (("owner_name", name), ("owner_pronoun", pronoun), ("owner_about", about)):
        con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (k, v))
    con.commit()
    con.close()


def clean_name(text):
    name = " ".join((text or "").split())[:30]
    return name if name and all(ch.isalnum() or ch in " .-'" for ch in name) else None


STEPS = [   # (what it is, the existing JARVIS command, needs a typed value?)
    ("Gemini API key (aistudio.google.com) - optional if you use Groq", "--set-key", "Paste your Gemini key (blank to skip): "),
    ("Groq API keys (console.groq.com) - asked one by one, hidden", "--setup-groq", None),
    ("Your phone: JARVIS finds it on the Wi-Fi by itself - no IP address to type", "phone", None),
    ("Your phone's lock, used only to unlock it on your command (stays in this folder's database)", "lock", None),
    ("YOUR voice: model download, 8 short sentences, comparison voices, limits - about 5 minutes in a quiet room", "voice", None),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="First-time setup for a new JARVIS owner")
    ap.add_argument("--name")
    ap.add_argument("--pronoun", choices=["he", "she", "they"])
    ap.add_argument("--about", default=None, help='optional, e.g. "a college student from Pune" (used only in auto-reply prompts)')
    ap.add_argument("--db", default=os.path.join(HERE, "jarvis_memory.db"), help=argparse.SUPPRESS)
    ap.add_argument("--usage-log", choices=["on", "off"], default=None, help="keep the local privacy-safe usage log (asked when not given)")
    ap.add_argument("--phone", choices=["auto"], default=None, help="'auto': find and link the phone without asking anything (it must already allow this PC)")
    ap.add_argument("--owner-only", action="store_true", help="save the owner and stop (no keys / voice steps; the phone only with --phone auto)")
    a = ap.parse_args(argv)

    state, owner = existing_owner(a.db)
    if state == "legacy":
        print("STOP: this folder already holds an existing JARVIS installation with its owner's data (contacts, messages, voice).\n"
              "Setup will not touch it. A new owner must start from a CLEAN package (see SHARING_GUIDE.md), never from a copy of this folder.")
        return 2
    if state == "owned" and (a.name is None or clean_name(a.name) != owner):
        print(f"This copy of JARVIS already belongs to: {owner}")
        if a.name is not None:
            print("STOP: the owner's name is not changed once it is set (the voice print and settings are saved under it).")
            return 2
    name = owner
    if state == "fresh":
        name = clean_name(a.name if a.name is not None else input("Your name (JARVIS will call you this): "))
        if not name:
            print("STOP: please give a simple name (letters, numbers, spaces).")
            return 2
        pronoun = a.pronoun or (input("How should JARVIS refer to you - he / she / they [they]: ").strip().lower() or "they")
        if pronoun not in ("he", "she", "they"):
            pronoun = "they"
        about = a.about if a.about is not None else ("" if a.name is not None else input("One line about you for auto-replies (optional, blank to skip): ").strip())
        save_owner(a.db, name, pronoun, about[:80])
        print(f"Saved. This copy of JARVIS now belongs to: {name}")
        usage = a.usage_log
        if usage is None:
            print("\nUsage log: JARVIS can keep a small local file (usage_log.jsonl) of WHICH features you use and whether they worked,\n"
                  "so the developer can fix problems. It never contains your words, names, numbers, messages, keys or passwords, and it is\n"
                  "not sent anywhere by JARVIS. You can read it any time:  venv\\Scripts\\python.exe -m jarvis_addons.usage_log")
            usage = "off" if (a.name is None and input("Keep this usage log? [Y/n]: ").strip().lower() == "n") else "on"
        con = sqlite3.connect(a.db)
        con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('usage_log', ?)", (usage,))
        con.commit()
        con.close()
    if a.phone == "auto":
        from jarvis_addons import phone_link
        print()
        phone_link.link(a.db, assume_yes=True)
    if a.owner_only:
        return 0

    py = sys.executable
    script = os.path.join(HERE, "live_cmd_console.py")
    print("\nNow the one-time steps. You can skip any step and run it later by hand.\n")
    for what, flag, ask in STEPS:
        print(f"--- {what}")
        cmd = [py, script, flag]
        if flag == "phone":
            from jarvis_addons import phone_link
            print(phone_link.HELP)
            try:
                go = input("    Press Enter when the phone is ready (or type 'skip'): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                go = "skip"
            if go == "skip":
                print("    skipped. Later:  venv\\Scripts\\python.exe -m jarvis_addons.phone_link\n")
            else:
                phone_link.link(a.db)
                print()
            continue
        if flag == "lock":
            try:
                kind = input("    Phone lock type - pin / password / pattern / none (blank to skip): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                kind = ""
            if kind in ("pin", "password"):
                val = input("    Your phone PIN/password: ").strip()
                if val:
                    subprocess.run([py, script, "--set-phone-password", val], cwd=HERE)
                    subprocess.run([py, "-m", "jarvis_addons.pattern_lock", "--type", kind], cwd=HERE)
            elif kind == "pattern":
                print("    Dots are numbered like a keypad:  1 2 3 / 4 5 6 / 7 8 9.  An L from the top-left is 14789.")
                val = input("    Your pattern as dot numbers: ").strip()
                if val:
                    subprocess.run([py, "-m", "jarvis_addons.pattern_lock", "--set", val], cwd=HERE)
            elif kind == "none":
                subprocess.run([py, "-m", "jarvis_addons.pattern_lock", "--type", "none"], cwd=HERE)
            else:
                print("    skipped. Later:  live_cmd_console.py --set-phone-password VALUE   or   -m jarvis_addons.pattern_lock --set 14789")
            print()
            continue
        if flag == "voice":
            cmd = [py, "-m", "jarvis_addons.voice_setup"]
            flag = "(or run:  venv\\Scripts\\python.exe -m jarvis_addons.voice_setup)"
        if ask:
            try:
                val = input(ask).strip()
            except (EOFError, KeyboardInterrupt):
                val = ""
            if not val:
                print(f"    skipped. Later:  venv\\Scripts\\python.exe live_cmd_console.py {flag} VALUE\n")
                continue
            cmd.append(val)
        else:
            try:
                go = input("    run this now? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                go = "n"
            if go == "n":
                print(f"    skipped. Later:  venv\\Scripts\\python.exe live_cmd_console.py {flag}\n")
                continue
        subprocess.run(cmd, cwd=HERE)
        print()
    print("Setup finished. Start JARVIS with:  .\\JARVIS_Launcher.ps1 -Console")
    return 0


if __name__ == "__main__":
    sys.exit(main())
