"""Phone contacts stay fresh (add-on). JARVIS keeps its own copy of the phone's contacts; that copy was only refreshed when the
owner said "sync contacts", and a contact renamed on the phone (Rahul -> Rohan) kept its old name here for ever.

    venv\\Scripts\\python.exe -m jarvis_addons.contact_sync              show what a sync would change (changes nothing)
    venv\\Scripts\\python.exe -m jarvis_addons.contact_sync --apply      do it now
    venv\\Scripts\\python.exe -m jarvis_addons.contact_sync --retired    names that were replaced, and by what
    venv\\Scripts\\python.exe -m jarvis_addons.contact_sync --restore NAME   bring one back

What it adds - JARVIS's own contact functions are not changed, they are called:
  * the copy is refreshed by itself two minutes after JARVIS starts and then every six hours (when the phone is connected);
  * after every sync (also the spoken "sync contacts") renamed contacts are followed: a name that is gone from the phone while its
    number now belongs to exactly ONE other name there is retired - moved to the table contacts_retired (nothing is thrown away) -
    and its relation / notes / email move to the new name, so "message mummy" keeps working after a rename.
A contact that exists only in JARVIS (saved by voice) and whose number is not on the phone is never touched. A second name you gave
by hand to a number that IS on the phone would be taken for a rename: bring it back with --restore and it is kept from then on."""
import os
import re
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "jarvis_memory.db")
FIRST_AFTER = 120            # seconds after JARVIS starts
EVERY = 6 * 3600             # seconds between two automatic syncs
ROW_RX = re.compile(r"Row:\s*\d+\s+display_name=(.*), data1=(.*)$")


def key10(number):
    """The last 10 digits: the same person whether saved with +91, 0 or spaces."""
    d = re.sub(r"\D", "", str(number or ""))
    return d[-10:] if len(d) >= 10 else d


def parse_phone_rows(text):
    """Output of the phone's contact query -> [(name, number)], one per name (the first number, like JARVIS's own sync)."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        m = ROW_RX.match(line.strip())
        if not m:
            continue
        name, num = m.group(1).strip(), re.sub(r"[^0-9+]", "", m.group(2))
        if name and len(re.sub(r"\D", "", num)) >= 3 and name.lower() not in seen:
            seen.add(name.lower())
            out.append((name, num))
    return out


def plan(local, phone, keep=()):
    """local = [(name, phone, has_own_data)], phone = [(name, number)], keep = lower names the owner restored (never retired again).
    -> {"new": [names not here yet], "renamed": [(old name, new name)]}"""
    on_phone = {n.lower() for n, _ in phone}
    by_number = {}
    for n, num in phone:
        by_number.setdefault(key10(num), []).append(n)
    have = {n.lower() for n, _, _ in local}
    renamed = []
    for name, num, own_data in local:
        low = name.lower()
        if low in on_phone:
            continue                                          # still on the phone under this name
        now = by_number.get(key10(num)) or []
        if len(key10(num)) < 10 or len(now) != 1:
            continue                                          # number not on the phone (a JARVIS-only contact), or several names share it
        if low not in keep:
            renamed.append((name, now[0]))
    return {"new": [n for n, _ in phone if n.lower() not in have], "renamed": renamed}


# ------------------------------------------------------------------ database
def _prepare(con):
    con.execute("CREATE TABLE IF NOT EXISTS contacts_retired (name TEXT, phone TEXT, relation TEXT, notes TEXT, email TEXT, retired_ts TEXT, replaced_by TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS contacts_keep (name TEXT PRIMARY KEY, since TEXT)")


def _columns(con):
    return [r[1] for r in con.execute("PRAGMA table_info(contacts)")]


def local_rows(con):
    extra = [c for c in ("relation", "notes", "email") if c in _columns(con)]
    rows = con.execute(f"SELECT name, phone{''.join(', ' + c for c in extra)} FROM contacts").fetchall()
    return [(r[0], r[1], any((x or "").strip() for x in r[2:])) for r in rows if r[0]]


def follow_renames(phone, db=None, now=None):
    """After a sync: retire the names that were renamed on the phone and carry their data over. -> [(old, new)]"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    con = sqlite3.connect(db or DB, timeout=15)
    try:
        _prepare(con)
        keep = {r[0] for r in con.execute("SELECT name FROM contacts_keep")}
        todo = plan(local_rows(con), phone, keep)["renamed"]
        extra = [c for c in ("relation", "notes", "email") if c in _columns(con)]
        done = []
        for old, new in todo:
            row = con.execute(f"SELECT name, phone{''.join(', ' + c for c in extra)} FROM contacts WHERE LOWER(name)=?", (old.lower(),)).fetchone()
            if not row:
                continue
            data = dict(zip(["name", "phone"] + extra, row))
            target = con.execute("SELECT rowid FROM contacts WHERE LOWER(name)=?", (new.lower(),)).fetchone()
            if not target:                                    # JARVIS's own sync adds it; if it has not run yet, the row simply takes the new name
                con.execute("UPDATE contacts SET name=? WHERE LOWER(name)=?", (new, old.lower()))
            else:
                for c in extra:                               # relation / notes / email follow the person to the new name (never overwrite)
                    if (data.get(c) or "").strip():
                        con.execute(f"UPDATE contacts SET {c}=? WHERE rowid=? AND ({c} IS NULL OR TRIM({c})='')", (data[c], target[0]))
                con.execute("DELETE FROM contacts WHERE LOWER(name)=?", (old.lower(),))
            con.execute("INSERT INTO contacts_retired (name, phone, relation, notes, email, retired_ts, replaced_by) VALUES (?,?,?,?,?,?,?)",
                        (data["name"], data["phone"], data.get("relation"), data.get("notes"), data.get("email"), now, new))
            done.append((old, new))
        con.commit()
        return done
    finally:
        con.close()


def restore(name, db=None):
    con = sqlite3.connect(db or DB, timeout=15)
    try:
        _prepare(con)
        row = con.execute("SELECT name, phone, relation, notes, email, rowid FROM contacts_retired WHERE LOWER(name)=? ORDER BY retired_ts DESC", (name.lower(),)).fetchone()
        if not row or con.execute("SELECT 1 FROM contacts WHERE LOWER(name)=?", (name.lower(),)).fetchone():
            return False
        cols = _columns(con)
        vals = {"name": row[0], "phone": row[1], "relation": row[2], "notes": row[3], "email": row[4]}
        use = [c for c in vals if c in cols]
        con.execute(f"INSERT INTO contacts ({', '.join(use)}) VALUES ({', '.join('?' for _ in use)})", [vals[c] for c in use])
        con.execute("DELETE FROM contacts_retired WHERE rowid=?", (row[5],))
        con.execute("INSERT OR REPLACE INTO contacts_keep (name, since) VALUES (?, ?)", (name.lower(), time.strftime("%Y-%m-%dT%H:%M:%S")))   # the owner's own name for that number: kept from now on
        con.commit()
        return True
    finally:
        con.close()


# ------------------------------------------------------------------ inside a running JARVIS
def read_phone(host):
    """The phone's contacts right now, or None when the phone cannot be read (then nothing at all is changed)."""
    target = host.get("TARGET") or (host["get_config"]("adb_target") if callable(host.get("get_config")) else "")
    if not target or (host["_adb_state"]() or {}).get(target) != "device":
        return None
    r = host["run_silent_cmd"]([host["ADB_BIN"], "-s", target, "shell", "content", "query", "--uri", "content://com.android.contacts/data/phones",
                                "--projection", "display_name:data1"], timeout=90)
    rows = parse_phone_rows(r.stdout or "")
    return rows or None


def install(host):
    need = ("sync_contacts_from_phone", "_adb_state", "run_silent_cmd", "ADB_BIN", "log_debug")
    if any(k not in host for k in need) or getattr(host["sync_contacts_from_phone"], "_contact_sync", False):
        return False
    orig, log, lock = host["sync_contacts_from_phone"], host.get("file_log") or (lambda s: None), threading.Lock()

    def sync_contacts_from_phone(*a, **k):
        n = orig(*a, **k)                                     # JARVIS's own sync: new names and changed numbers
        try:
            with lock:
                phone = read_phone(host)
                if phone:
                    for old, new in follow_renames(phone):
                        log(f"[SYNC] contact renamed on the phone: '{old}' is now '{new}' (old name retired, its data kept)")
        except Exception as e:
            host["log_debug"]("contact_sync/renames", e)
        return n

    sync_contacts_from_phone._contact_sync = True
    host["sync_contacts_from_phone"] = sync_contacts_from_phone

    def keeper():
        time.sleep(FIRST_AFTER)
        while True:
            try:
                target = host.get("TARGET") or ""
                if (host["_adb_state"]() or {}).get(target) == "device":       # only looks; never makes JARVIS speak or reconnect
                    host["sync_contacts_from_phone"](announce=False)
                    log("[SYNC] automatic contact sync done")
            except Exception as e:
                host["log_debug"]("contact_sync/keeper", e)
            time.sleep(EVERY)
    if not host.get("CLI_MODE"):
        threading.Thread(target=keeper, daemon=True, name="contact_sync").start()
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--retired" in argv:
        con = sqlite3.connect(DB)
        _prepare(con)
        rows = con.execute("SELECT name, replaced_by, retired_ts FROM contacts_retired ORDER BY retired_ts DESC").fetchall()
        con.close()
        for name, new, ts in rows:
            print(f"  {ts}  '{name}' -> '{new}'")
        print(f"{len(rows)} retired name(s)")
        return 0
    if "--restore" in argv:
        name = " ".join(argv[argv.index("--restore") + 1:])
        print("restored" if restore(name) else "nothing to restore under that name (or the name is in use)")
        return 0
    from ._host import load
    H = load().__dict__
    phone = read_phone(H)
    if not phone:
        print("The phone is not connected (or gave no contacts), so nothing can be compared.")
        return 1
    con = sqlite3.connect(DB)
    _prepare(con)
    p = plan(local_rows(con), phone, {r[0] for r in con.execute("SELECT name FROM contacts_keep")})
    con.close()
    print(f"phone: {len(phone)} contacts | new for JARVIS: {len(p['new'])} | renamed on the phone: {len(p['renamed'])}")
    for old, new in p["renamed"]:
        print(f"  renamed: '{old}' -> '{new}'")
    for n in p["new"][:15]:
        print(f"  new: {n}")
    if "--apply" in argv:
        H["sync_contacts_from_phone"](announce=False)
        print("applied (JARVIS's own sync, then the renames)")
    else:
        print("nothing was changed; add --apply to do it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
