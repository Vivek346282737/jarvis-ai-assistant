"""Pattern-lock support (add-on). JARVIS's own unlock types a PIN / password; a phone locked with a PATTERN needs a
finger gesture over a 3x3 grid instead. This add-on only takes over 'unlock phone' when the owner has said the phone
uses a pattern; for PIN / password phones the original unlock runs exactly as before.

The dots are numbered like a phone keypad:      1 2 3
                                                4 5 6
                                                7 8 9      so an "L" from top-left is 1 4 7 8 9.

    venv\\Scripts\\python.exe -m jarvis_addons.pattern_lock --set 14789     save your pattern (stays in this folder's database)
    venv\\Scripts\\python.exe -m jarvis_addons.pattern_lock --type pin      go back to PIN / password unlocking
    venv\\Scripts\\python.exe -m jarvis_addons.pattern_lock --status        what is saved, what the phone reports (never shows the pattern)
    venv\\Scripts\\python.exe -m jarvis_addons.pattern_lock --unlock        unlock the connected phone now
    venv\\Scripts\\python.exe -m jarvis_addons.pattern_lock --gesture-test  check that the finger gesture works on this phone (uses Settings, not the lock screen)

Safety: the grid is READ from the phone's lock screen, never guessed; one attempt per command; after 2 failed attempts it
stops trying until the pattern is saved again, so JARVIS can never run the phone into its "too many attempts" lock-out."""
import os
import re
import sqlite3
import sys
import time

MIN_SDK = 30                                        # `input motionevent` (a finger that stays down across several points) exists from Android 11
MAX_FAILS = 2
STEPS_BETWEEN_DOTS = 5
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_pattern(text):
    """'1 4 7 8 9' / '14789' -> [1, 4, 7, 8, 9]; None when it is not a valid Android pattern (4-9 dots, each used once)."""
    digits = re.findall(r"[1-9]", str(text or ""))
    if re.sub(r"[\s,\-]", "", str(text or "")) != "".join(digits):
        return None
    dots = [int(d) for d in digits]
    return dots if 4 <= len(dots) <= 9 and len(set(dots)) == len(dots) else None


def parse_bounds(bounds):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    return tuple(map(int, m.groups())) if m else None


def find_pattern_view(nodes):
    """The lock screen's pattern grid in a uiautomator dump -> (left, top, right, bottom) or None."""
    best = None
    for n in nodes or []:
        ident = (n.get("resource-id", "") + " " + n.get("class", "")).lower()
        if "lockpatternview" in ident or "lock_pattern_view" in ident or "patternview" in ident:
            b = parse_bounds(n.get("bounds"))
            if b and (b[2] - b[0]) > 100 and (b[3] - b[1]) > 100 and (best is None or (b[2] - b[0]) * (b[3] - b[1]) > (best[2] - best[0]) * (best[3] - best[1])):
                best = b
    return best


def grid_points(bounds):
    """Centre of each of the 9 dots of a pattern view."""
    left, top, right, bottom = bounds
    w, h = right - left, bottom - top
    return {r * 3 + c + 1: (left + int(w * (2 * c + 1) / 6), top + int(h * (2 * r + 1) / 6)) for r in range(3) for c in range(3)}


def path_points(dots, pts, steps=STEPS_BETWEEN_DOTS):
    """Every point the finger passes: each dot, with a few points in between so the phone sees a drag and not a jump."""
    out = [pts[dots[0]]]
    for a, b in zip(dots, dots[1:]):
        (x1, y1), (x2, y2) = pts[a], pts[b]
        out += [(x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps) for i in range(1, steps + 1)]
    return out


def gesture_command(points):
    """One shell line: finger down on the first point, moves through the rest, finger up. Sent as ONE adb call so the timing is the phone's own."""
    parts = [f"input motionevent DOWN {points[0][0]} {points[0][1]}"]
    parts += [f"input motionevent MOVE {x} {y}" for x, y in points[1:]]
    parts.append(f"input motionevent UP {points[-1][0]} {points[-1][1]}")
    return "; ".join(parts)


def credential_type(lock_settings_dump):
    """What `dumpsys lock_settings` says the phone is locked with -> 'pattern' | 'pin' | 'password' | 'none' | None (unknown)."""
    m = re.search(r"CredentialType:\s*([A-Za-z_]+)", lock_settings_dump or "")
    return {"PATTERN": "pattern", "PIN": "pin", "PASSWORD": "password", "NONE": "none"}.get(m.group(1).upper()) if m else None


def locked_state(window_dump):
    """True = the lock screen is showing, False = it is not, None = this phone does not say."""
    for rx in (r"mIsShowing=(true|false)", r"isKeyguardShowing=(true|false)", r"mShowingLockscreen=(true|false)", r"mKeyguardShowing=(true|false)", r"KeyguardShowing=(true|false)"):
        m = re.search(rx, window_dump or "")
        if m:
            return m.group(1) == "true"
    return None


def display_on(display_dump):
    """True / False from `dumpsys display`, None when the phone does not say. A covered proximity sensor (phone face
    down, in a pocket) keeps a Samsung display OFF even after a wake-up key - no touch gesture can work then."""
    m = re.search(r"mScreenState=([A-Z_]+)", display_dump or "")
    return None if not m else m.group(1) == "ON"


def _shell(host, *args, timeout=20):
    res = host["run_silent_cmd"]([host["ADB_BIN"], "-s", host["TARGET"], "shell", *args], timeout=timeout)
    return (res.stdout or "") if getattr(res, "returncode", 1) == 0 else ""


def unlock_with_pattern(host, sleep=time.sleep):
    """-> (result, detail). result: 'unlocked' | 'already' | 'sent_unverified' | 'failed' | 'refused'. Never tries twice."""
    cfg, setc = host["get_config"], host["set_config"]
    dots = parse_pattern(cfg("phone_pattern"))
    if not dots:
        return "refused", "no pattern is saved"
    if int(cfg("pattern_fail_count", "0") or 0) >= MAX_FAILS:
        return "refused", "the saved pattern failed twice, so I stopped before the phone locks itself out. Save the pattern again"
    try:
        sdk = int(_shell(host, "getprop", "ro.build.version.sdk").strip() or 0)
    except ValueError:
        sdk = 0
    if sdk and sdk < MIN_SDK:
        return "refused", "this phone's Android is too old for a pattern gesture (Android 11 or newer is needed)"
    if locked_state(_shell(host, "dumpsys", "window")) is False and "mWakefulness=Awake" in _shell(host, "dumpsys", "power"):
        return "already", "the phone is already unlocked"
    _shell(host, "input", "keyevent", "224")                                   # wake
    sleep(0.6)
    if display_on(_shell(host, "dumpsys", "display")) is False:
        return "refused", "the phone's screen did not turn on - it may be face down, covered or in a pocket"
    _shell(host, "input", "keyevent", "82")                                    # ask for the unlock screen
    sleep(0.4)
    size = re.search(r"(\d+)x(\d+)", _shell(host, "wm", "size"))
    w, h = (int(size.group(1)), int(size.group(2))) if size else (1080, 2340)
    view = None
    for _ in range(2):
        view = find_pattern_view(host["get_ui_nodes"]())
        if view:
            break
        _shell(host, "input", "swipe", str(w // 2), str(int(h * 0.8)), str(w // 2), str(int(h * 0.3)), "200")          # some phones show the grid only after a swipe up
        sleep(0.7)
    if not view:
        return "refused", "I could not see the pattern grid on the lock screen, so I did not guess"
    _shell(host, gesture_command(path_points(dots, grid_points(view))), timeout=30)
    sleep(1.2)
    state = locked_state(_shell(host, "dumpsys", "window"))
    if state is True:
        setc("pattern_fail_count", str(int(cfg("pattern_fail_count", "0") or 0) + 1))
        return "failed", "the phone is still locked after the pattern"
    setc("pattern_fail_count", "0")
    _shell(host, "input", "keyevent", "3")
    return ("unlocked", "") if state is False else ("sent_unverified", "this phone does not report its lock state")


def gesture_selftest(host, sleep=time.sleep):
    """Does a multi-point finger path really move things on THIS phone? Measured by screenshots of the Settings list:
    first a plain swipe (control), then the same path as one finger-down gesture. Never touches the lock screen.
    -> dict(display_on, control_moved, gesture_moved)."""
    import hashlib

    def shot():
        res = host["run_silent_cmd"]([host["ADB_BIN"], "-s", host["TARGET"], "exec-out", "screencap", "-p"], timeout=30)
        return hashlib.md5((res.stdout or "").encode("utf-8", "replace")).hexdigest() if getattr(res, "returncode", 1) == 0 and res.stdout else None

    def settings():
        _shell(host, "am", "force-stop", "com.android.settings")
        _shell(host, "monkey", "-p", "com.android.settings", "-c", "android.intent.category.LAUNCHER", "1")
        sleep(2.5)
        return shot()

    _shell(host, "input", "keyevent", "224")
    sleep(0.8)
    out = {"display_on": display_on(_shell(host, "dumpsys", "display")), "control_moved": None, "gesture_moved": None}
    if out["display_on"] is False:
        return out
    size = re.search(r"(\d+)x(\d+)", _shell(host, "wm", "size"))
    w, h = (int(size.group(1)), int(size.group(2))) if size else (1080, 2340)
    a, b = (w // 2, int(h * 0.75)), (w // 2, int(h * 0.30))
    before = settings()
    _shell(host, "input", "swipe", str(a[0]), str(a[1]), str(b[0]), str(b[1]), "300")
    sleep(1.2)
    out["control_moved"] = before is not None and shot() != before
    before = settings()
    mid = [(a[0], a[1] + (b[1] - a[1]) * i // 6) for i in range(7)]
    _shell(host, gesture_command(mid + [(b[0] - w // 5, b[1])]), timeout=30)
    sleep(1.2)
    out["gesture_moved"] = before is not None and shot() != before
    _shell(host, "input", "keyevent", "3")
    return out


def install(host):
    need = ("unlock_phone", "get_config", "set_config", "run_silent_cmd", "get_ui_nodes", "speak", "ADB_BIN", "log_debug")
    if any(k not in host for k in need) or getattr(host["unlock_phone"], "_pattern_lock", False):
        return False
    orig = host["unlock_phone"]

    def unlock_phone(*a, **k):
        try:
            use_pattern = host["get_config"]("phone_lock_type") == "pattern"
        except Exception as e:
            host["log_debug"]("pattern_lock", e)
            use_pattern = False
        if not use_pattern:
            return orig(*a, **k)                                               # PIN / password phones: exactly the original unlock
        if host.get("require_phone_connection") and not host["require_phone_connection"]("unlock phone"):
            return
        try:
            result, detail = unlock_with_pattern(host)
        except Exception as e:
            host["log_debug"]("pattern_lock", e)
            result, detail = "failed", "something went wrong while drawing the pattern"
        audit = host.get("log_audit") or (lambda *x: None)
        if result in ("unlocked", "already"):
            host["speak"]("Phone unlocked successfully, Boss." if result == "unlocked" else "The phone is already unlocked, Boss.")
            audit("Phone Screen Unlock", "MEDIUM", "SUCCESS")
        elif result == "sent_unverified":
            host["speak"]("I drew the pattern, Boss, but this phone does not tell me whether it opened.")
            audit("Phone Screen Unlock", "MEDIUM", "UNVERIFIED")
        else:
            host["speak"](f"I could not unlock the phone, Boss: {detail}.")
            audit("Phone Screen Unlock", "MEDIUM", "FAIL")

    unlock_phone._pattern_lock = True
    host["unlock_phone"] = unlock_phone
    return True


def _config(db):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    return con


def main(argv=None, db=None):
    argv = sys.argv[1:] if argv is None else argv
    db = db or os.path.join(ROOT, "jarvis_memory.db")
    if "--set" in argv:
        raw = " ".join(argv[argv.index("--set") + 1:])
        dots = parse_pattern(raw)
        if not dots:
            print("That is not a valid pattern. Use the dot numbers 1-9 in order, each once, at least 4 dots - for example: --set 14789")
            return 2
        con = _config(db)
        for k, v in (("phone_pattern", "".join(map(str, dots))), ("phone_lock_type", "pattern"), ("pattern_fail_count", "0")):
            con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (k, v))
        con.commit()
        con.close()
        print(f"Pattern saved ({len(dots)} dots). 'unlock phone' will now draw it.")
        return 0
    if "--type" in argv:
        kind = (argv[argv.index("--type") + 1:] or [""])[0].lower()
        if kind not in ("pin", "password", "pattern", "none"):
            print("Use: --type pin | password | pattern | none")
            return 2
        con = _config(db)
        con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('phone_lock_type', ?)", (kind,))
        con.commit()
        con.close()
        print(f"Phone lock type: {kind}." + ("" if kind != "pattern" else " Save the pattern too:  --set 14789"))
        return 0
    from ._host import load
    L = load()
    host = L.__dict__
    if "--unlock" in argv:
        L.unlock_phone()
        return 0
    if "--gesture-test" in argv:
        r = gesture_selftest(host)
        if r["display_on"] is False:
            print("The phone's screen is OFF and did not turn on (face down, covered, or in a pocket). Put it face up and run this again.")
            return 1
        print(f"Plain swipe moved the screen      : {r['control_moved']}   (if this is False the test itself could not see the screen)")
        print(f"Finger-path gesture moved the screen: {r['gesture_moved']}   (True = pattern unlock can work on this phone)")
        return 0 if r["gesture_moved"] else 1
    saved = parse_pattern(L.get_config("phone_pattern"))
    print(f"Saved lock type      : {L.get_config('phone_lock_type') or 'not set (PIN / password unlock is used)'}")
    print(f"Pattern saved        : {'yes, ' + str(len(saved)) + ' dots' if saved else 'no'}   failed attempts: {L.get_config('pattern_fail_count', '0')}")
    sdk = _shell(host, "getprop", "ro.build.version.sdk").strip()
    if not sdk:
        print("Phone                : not connected")
        return 0
    print(f"Phone says it uses   : {credential_type(_shell(host, 'dumpsys', 'lock_settings')) or 'unknown'}")
    print(f"Android SDK          : {sdk}  ({'pattern gesture supported' if int(sdk) >= MIN_SDK else 'TOO OLD for a pattern gesture'})")
    print(f"Lock screen showing  : {locked_state(_shell(host, 'dumpsys', 'window'))}  (None = this phone does not say)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
