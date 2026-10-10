"""Makes the PUBLIC copy of this project: a separate folder with a clean history, checked for anything private.

    venv\\Scripts\\python.exe make_public_repo.py                 build ..\\JARVIS-public and scan it
    venv\\Scripts\\python.exe make_public_repo.py --out D:\\x      another folder
    venv\\Scripts\\python.exe make_public_repo.py --scan-only     scan an existing public folder again

Why a separate folder: this working folder's git history contains files that were removed from tracking later (run-time
logs, hand-over notes). Pushing this repository would publish that history. The public folder gets ONLY the files listed as
tracked today, minus the list below, and starts its own history with `git init`.

What is never copied: databases, config.json, passwords, logs, voice prints, key stores (they are git-ignored and therefore
not tracked), and the old helper scripts in NOT_PUBLISHED. The copy is then scanned against this PC's REAL private values
(API keys, tokens, phone numbers, e-mail addresses from the local database - read only, never printed) and against patterns
for keys, Windows user paths and this home network's real addresses. Any finding makes the script exit with an error."""
import os
import re
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "jarvis_memory.db")
NOT_PUBLISHED = {                                              # tracked here, but personal or machine-bound helpers - kept local
    "NOTICE.txt", "mke2fs.conf", "source.properties",          # leftovers of Android's platform-tools download
    "SHARING_GUIDE.md", "JARVIS_Reconnect.ps1", "run_all_agent_tests.ps1", "run_jarvis_console.bat", "run_speed_test.ps1",
    "audit_jarvis.py", "enroll_voice.py", "init_profiles.py", "jarvis_service.pyw", "live_notification_radar.py", "msg_brain_live.py",
    "setup_relations.py", "update_db_relations.py", "test_cli.py", "test_jarvis.py", "test_mic.py", "test_msg_sniffer.py", "test_smart_dialer.py",
}
MUST_NOT_EXIST = re.compile(r"\.(db|sqlite|npy|log|keystore|pass|mp3|wav|pkl|jsonl)$|(^|/)(config\.json|\.env|dashboard_password\.txt|AUTONOMOUS_LOG\.md|PENDING_FROM_VIVEK\.md|registry\.json)$", re.I)
TEXT_EXT = {".py", ".pyw", ".ps1", ".bat", ".md", ".txt", ".json", ".html", ".xml", ".java", ".cfg", ".ini", ".gitignore", ""}
KEY_RX = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|gsk_[0-9A-Za-z]{30,}|\bsk-[0-9A-Za-z]{24,}|\b\d{8,10}:[A-Za-z0-9_\-]{35}\b|ghp_[0-9A-Za-z]{30,}")
TEST_KEY = re.compile(r"sk-abcdefghijklmnop|A{35}|TESTONLY")
# a Windows profile path of a real user (the test fixtures' made-up 'riya' is fine); written in pieces so this line does not match itself
PC_PATH = re.compile(r"[a-z]:\\+" + "users" + r"\\+(?!riya\b|you\b|name\b|<)[^\\\s\"'<>]+|/c/" + "users/")


def private_values():
    """Values of THIS installation that must never be published. Read-only; nothing here is ever printed."""
    vals, names = set(), set()
    user = {os.environ.get("USERNAME") or "", os.path.basename(os.path.expanduser("~")), os.environ.get("COMPUTERNAME") or ""}
    if os.path.exists(DB):
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            cfg = dict(con.execute("SELECT key, val FROM config").fetchall())
            for k in ("gemini_api_key", "groq_keys", "email_app_password", "phone_password", "email_address", "email_notify_number", "telegram_token",
                      "telegram_owner_id", "adb_last_ip", "adb_phone_serial", "dashboard_pw"):
                vals.update(p.strip() for p in str(cfg.get(k) or "").split(",") if p.strip())
            vals.add(str(cfg.get("adb_target") or "").split(":")[0])
            for table, col in (("contacts", "phone"), ("contacts", "email")):
                try:
                    vals.update(str(r[0]).strip() for r in con.execute(f"SELECT {col} FROM {table}") if r[0])
                except sqlite3.Error:
                    pass
            try:
                names = {str(r[0]).strip().lower() for r in con.execute("SELECT name FROM contacts") if r[0]}
            except sqlite3.Error:
                pass
        finally:
            con.close()
    try:                                                      # the author's own name (git user.name) is his to publish
        me = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, cwd=HERE).stdout.strip().lower()
    except OSError:
        me = ""
    names.discard(me)
    full_names = {n for n in names if len(n.split()) >= 2 and re.fullmatch(r"[a-z .'-]+", n) and len(n) >= 9 and not n.startswith("jarvis")}       # 'first last': distinctive enough to search for
    return {v for v in vals if len(v) >= 5}, {u.lower() for u in user if len(u) >= 4}, full_names


def scan(dest):
    secrets, user, full_names = private_values()
    problems = []
    for dp, dn, fn in os.walk(dest):
        dn[:] = [d for d in dn if d != ".git"]
        for name in fn:
            path = os.path.join(dp, name)
            rel = os.path.relpath(path, dest).replace("\\", "/")
            if MUST_NOT_EXIST.search(rel):
                problems.append(f"{rel}: this kind of file must not be published")
                continue
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                low = line.lower()
                if any(((f'"{s}"' in line or f"'{s}'" in line) if s.isalpha() else s in line) for s in secrets):
                    problems.append(f"{rel}:{i}: contains one of this installation's private values (not shown)")
                m = KEY_RX.search(line)
                if m and not TEST_KEY.search(line):
                    problems.append(f"{rel}:{i}: looks like an API key or token ({m.group(0)[:4]}...)")
                if PC_PATH.search(low) or any(re.search(r"\b" + re.escape(u) + r"\b", low) for u in user if u not in ("vivek",)):
                    problems.append(f"{rel}:{i}: a path or user name of this PC")
                if any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in full_names):
                    problems.append(f"{rel}:{i}: the full name of a real contact")
    return problems


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, cwd=HERE)
    if out.returncode:
        raise SystemExit("git ls-files failed - run this inside the JARVIS folder")
    return [f for f in out.stdout.decode("utf-8").split("\0") if f]


def build(dest):
    if os.path.exists(dest) and any(n != ".git" for n in os.listdir(dest)):
        raise SystemExit(f"STOP: {dest} already has files. Delete its contents (keep .git if you want) or choose another --out.")
    files = [f for f in tracked() if f not in NOT_PUBLISHED]
    for f in files:
        target = os.path.join(dest, f)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(HERE, f), target)
    return files


def local_rewrites():
    """[(compiled pattern, replacement)] from public_rewrites.local.json - names of real people that are part of how the owner's
    own installation works (a friend's name as a spoken alias, for example) and are replaced by made-up names in the public copy.
    The list itself is git-ignored, so the real names are not in this script or anywhere in the published files."""
    path = os.path.join(HERE, "public_rewrites.local.json")
    if not os.path.exists(path):
        return []
    import json
    with open(path, encoding="utf-8") as f:
        return [(re.compile(a), b) for a, b in json.load(f).get("rewrites", [])]


def rewrite(dest, rules):
    """Applies the replacements to every text file of the public copy. -> (files changed, replacements made)"""
    changed = total = 0
    for dp, dn, fn in os.walk(dest):
        dn[:] = [d for d in dn if d != ".git"]
        for name in fn:
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            path = os.path.join(dp, name)
            raw = open(path, "rb").read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            n = 0
            for rx, new in rules:
                text, k = rx.subn(new, text)
                n += k
            if n:
                open(path, "wb").write(text.encode("utf-8"))
                changed, total = changed + 1, total + n
    return changed, total


def leftovers(dest, rules):
    """A name from the rewrite list that is still somewhere in the public copy (must be empty)."""
    out = []
    for dp, dn, fn in os.walk(dest):
        dn[:] = [d for d in dn if d != ".git"]
        for name in fn:
            if os.path.splitext(name)[1].lower() in TEXT_EXT:
                path = os.path.join(dp, name)
                for i, line in enumerate(open(path, encoding="utf-8", errors="replace").read().splitlines(), 1):
                    if any(rx.search(line) for rx, _ in rules):
                        out.append(f"{os.path.relpath(path, dest).replace(os.sep, '/')}:{i}: a real name from the local rewrite list is still here")
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dest = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv else os.path.join(os.path.dirname(HERE), "JARVIS-public")
    rules = local_rewrites()
    if "--scan-only" not in argv:
        files = build(dest)
        print(f"copied {len(files)} files to {dest}  ({len(NOT_PUBLISHED)} local-only helpers left out)")
        changed, total = rewrite(dest, rules)
        print(f"real names replaced by made-up ones: {total} place(s) in {changed} file(s)  ({len(rules)} local rule(s))")
    problems = scan(dest) + leftovers(dest, rules)
    if problems:
        print(f"\nNOT SAFE TO PUBLISH - {len(problems)} finding(s):")
        for p in problems[:60]:
            print("  " + p)
        return 1
    print("scan: nothing private found (real keys, tokens, numbers, e-mail addresses, contact names, PC paths, phone address)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
