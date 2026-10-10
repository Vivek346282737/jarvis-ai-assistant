"""Builds a CLEAN copy of JARVIS to give to someone else.   Run:  venv\\Scripts\\python.exe make_clean_package.py

ALLOWLIST only: nothing is copied unless it is named below, so the owner's database, WhatsApp session, voice print,
logs, screenshots and generated projects can never slip in by accident. After copying, the package is SCANNED for the
owner's secrets and personal data; if anything is found the package is deleted and the script fails."""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["live_cmd_console.py", "jarvis_setup.py", "JARVIS_Launcher.ps1", "Install_JARVIS.ps1", "Install_JARVIS.bat", "README_SETUP.md",
         "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"]
DIRS = ["jarvis_agent", "jarvis_addons"]                  # code only
MODEL_DIR = "models"                                      # public speech models (no personal data); skipped with --no-models
SKIP_DIR_NAMES = {"__pycache__", ".git", "venv", ".venv", "tests"}          # the developer's test suites are not shipped (one of them needs this very script)
# never allowed inside a package, whatever the allowlist says (second line of defence)
FORBIDDEN_NAMES = {"jarvis_memory.db", "owner_voice.npy", "wa_chrome_profile", "agent_tasks", "screenshots", "downloads", "jarvis.log",
                   "jarvis.log.old", "test_report.txt", "startup_backup", "usage_log.jsonl"}
FORBIDDEN_EXT = {".db", ".sqlite", ".npy", ".log", ".mp3", ".wav", ".pkl", ".jsonl"}
TEXT_EXT = {".py", ".ps1", ".bat", ".md", ".txt", ".json", ".html", ".css", ".js", ".cfg", ".ini"}
SECRET_KEYS = ("gemini_api_key", "groq_keys", "email_app_password", "phone_password", "email_address", "email_notify_number", "telegram_token")


def owner_secrets(db):
    """Values that must never appear in a package. Returned for scanning only - never printed."""
    out = {os.environ.get("USERNAME") or "", os.path.basename(os.path.expanduser("~"))}
    if os.path.exists(db):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for k in SECRET_KEYS:
                row = con.execute("SELECT val FROM config WHERE key=?", (k,)).fetchone()
                if row and row[0]:
                    out.update(p.strip() for p in str(row[0]).split(","))
            for table, col in (("contacts", "phone"), ("contacts", "email")):
                try:
                    out.update(str(r[0]).strip() for r in con.execute(f"SELECT {col} FROM {table}") if r[0])
                except sqlite3.Error:
                    pass
        finally:
            con.close()
    try:                                                   # keys kept in the Windows user environment, not in the database (Pexels)
        sys.path.insert(0, HERE)
        from jarvis_agent.util import secret_values
        out.update(secret_values())
    except Exception:
        pass
    return {s for s in out if len(s) >= 5}


def scan(dest, secrets):
    problems = []
    for dp, dn, fn in os.walk(dest):
        for name in dn + fn:
            ext = os.path.splitext(name)[1].lower()
            if name in FORBIDDEN_NAMES or (name in fn and ext in FORBIDDEN_EXT):
                problems.append(f"forbidden item: {os.path.relpath(os.path.join(dp, name), dest)}")
        for name in fn:
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            path = os.path.join(dp, name)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # a secret that is an ordinary word (a password like "sunshine") also occurs innocently inside code, so it only
            # counts when it stands alone as a quoted value - exactly how a hardcoded secret would look
            hits = sum(1 for s in secrets if ((f'"{s}"' in text or f"'{s}'" in text) if s.isalpha() else s in text))
            if hits:
                problems.append(f"{os.path.relpath(path, dest)}: contains {hits} of the owner's private values (not shown)")
    return problems


def build(dest, with_models=True, db=None, src=HERE):
    if os.path.exists(dest):
        raise SystemExit(f"STOP: {dest} already exists. Delete it or choose another --out.")
    missing = [f for f in FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        raise SystemExit(f"STOP: these files are missing here, the package would not work: {missing}")
    os.makedirs(dest)
    for f in FILES:
        shutil.copy2(os.path.join(src, f), os.path.join(dest, f))
    ignore = shutil.ignore_patterns(*SKIP_DIR_NAMES, "*.pyc")
    for d in DIRS + ([MODEL_DIR] if with_models else []):
        if os.path.isdir(os.path.join(src, d)):
            shutil.copytree(os.path.join(src, d), os.path.join(dest, d), ignore=ignore)
    try:
        req = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        req = ""
    with open(os.path.join(dest, "requirements.txt"), "w", encoding="utf-8") as fh:
        fh.write(req)
    problems = scan(dest, owner_secrets(db or os.path.join(src, "jarvis_memory.db")))
    if not req.strip():
        problems.append("requirements.txt is empty (pip freeze failed)")
    if problems:
        shutil.rmtree(dest, ignore_errors=True)
        print("PACKAGE REJECTED AND DELETED - it was not safe to share:")
        for p in problems:
            print("  -", p)
        return False
    n = sum(len(fn) for _, _, fn in os.walk(dest))
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(dest) for f in fn) / 1e6
    print(f"CLEAN PACKAGE READY: {dest}\n  {n} files, {size:.0f} MB. Scanned: no database, no voice print, no WhatsApp session, no logs, none of your keys/passwords/contacts.")
    print("  Zip that folder and send it. The receiver follows README_SETUP.md inside it.")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "JARVIS_Clean_Package"))
    ap.add_argument("--no-models", action="store_true", help="leave out the 80 MB speech models (the receiver downloads them during setup)")
    a = ap.parse_args()
    sys.exit(0 if build(a.out, with_models=not a.no_models) else 1)
