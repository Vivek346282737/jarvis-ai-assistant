"""Settings from a file (add-on): fill in config.json once instead of answering the setup questions.

    venv\\Scripts\\python.exe -m jarvis_addons.config_file --apply [FILE]    put the values of config.json into this installation
    venv\\Scripts\\python.exe -m jarvis_addons.config_file --check           which settings this installation has (names only)

JARVIS keeps its settings and keys in its own local database (jarvis_memory.db). Neither that file nor config.json is ever put
into git; config.example.json is the template that is. A value that is still a placeholder ("your-...") is skipped, and a
value is never printed - only the name of the setting."""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "jarvis_memory.db")
KEYS = ("owner_name", "city", "gemini_api_key", "groq_keys", "ollama_model", "telegram_token", "telegram_owner_id", "email_address", "email_app_password",
        "email_notify_number", "adb_target", "phone_password", "dry_run")
SECRET = ("gemini_api_key", "groq_keys", "telegram_token", "email_app_password", "phone_password")


def is_placeholder(value):
    v = str(value or "").strip().lower()
    return not v or v.startswith(("your-", "your_", "<", "xxx")) or v in ("changeme", "change-me", "none", "null")


def read(path):
    """-> ({setting: value} with only real values of known settings, [skipped names])"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    good, skipped = {}, []
    for k in KEYS:
        if k in raw and not is_placeholder(raw[k]):
            good[k] = str(raw[k]).strip()
        elif k in raw:
            skipped.append(k)
    return good, skipped


def apply(values, db=None):
    con = sqlite3.connect(db or DB)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
        for k, v in values.items():
            con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (k, v))
        con.commit()
    finally:
        con.close()
    return sorted(values)


def have(db=None):
    if not os.path.exists(db or DB):
        return []
    con = sqlite3.connect(f"file:{db or DB}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in con.execute("SELECT key FROM config WHERE val IS NOT NULL AND val != ''") if r[0] in KEYS)
    except sqlite3.Error:
        return []
    finally:
        con.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        got = have()
        for k in KEYS:
            print(f"  {'set    ' if k in got else 'not set'}  {k}" + ("  (secret)" if k in SECRET else ""))
        return 0
    if "--apply" in argv:
        rest = [a for a in argv[argv.index("--apply") + 1:] if not a.startswith("--")]
        path = rest[0] if rest else os.path.join(ROOT, "config.json")
        if not os.path.isfile(path):
            print(f"{path} does not exist. Copy config.example.json to config.json and fill it in first.")
            return 1
        values, skipped = read(path)
        done = apply(values)
        print("saved into this installation: " + (", ".join(done) or "nothing"))
        if skipped:
            print("left as they were (still placeholders): " + ", ".join(skipped))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
