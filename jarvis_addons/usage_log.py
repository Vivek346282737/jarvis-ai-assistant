"""Privacy-safe usage log (add-on). Writes ONE local file, usage_log.jsonl, in this JARVIS folder. Nothing is sent anywhere.

What a line contains:  when, an anonymous installation id, how the command came in, a masked command SHAPE, which
feature group handled it, what kind of action ran and whether it worked, error TYPES, how long it took.

What it can never contain: the words of a command (only the few public command words in COMMAND_WORDS survive, every
other word becomes "_"), names, numbers, message text, file paths, keys or passwords, or the text of an error.

    venv\\Scripts\\python.exe -m jarvis_addons.usage_log            show what has been recorded (the owner can always look)
    venv\\Scripts\\python.exe -m jarvis_addons.usage_log --off      stop recording        (--on to start again)
    venv\\Scripts\\python.exe -m jarvis_addons.usage_log --summary  counts per feature / error"""
import atexit
import functools
import json
import os
import re
import sys
import threading
import time
import uuid

FILE_NAME = "usage_log.jsonl"
MAX_BYTES = 2_000_000
SHAPE_WORDS = 8                                     # only the head of a command is shaped; whatever follows (the message itself) is dropped
BG_REPEAT_SECS = 600                                # the same background error is recorded at most this often

# Public command vocabulary. A word that is not here is never written, so a name or a piece of a message cannot get through.
COMMAND_WORDS = frozenset("""
open close start stop play pause resume next previous search find show tell read check what when where how is it the a an my on off of in to for and
call dial redial message msg send reply text whatsapp email mail gmail inbox phone mobile laptop pc computer screen lock unlock mute unmute volume
up down battery status time date day weather temperature remind reminder alarm timer todo list task note birthday schedule cancel delete remove
add set turn switch enable disable dnd disturb screenshot photo camera gallery calculator settings chrome youtube spotify maps instagram telegram
music song video website site app application build make create fix debug analyze analyse clean research learn teach skill voice sleep wake
shutdown restart brightness wifi bluetooth news joke quiz briefing daily translate
kholo khol band karo kar do de bhejo bhej batao bata sunao suna padho padh lagao laga chalu shuru ruko ruk hatao hata banao bana dikhao dikha
ko ki ka ke se pe par mein me hai kya kitna kitni kitne aaj kal abhi
""".split())

AUDIT_CATEGORIES = sorted([
    "WhatsApp sent (unverified)", "WhatsApp sent successfully", "WhatsApp sent", "WhatsApp FAILED", "WhatsApp aborted", "Duplicate WhatsApp blocked",
    "Autonomous WhatsApp sent", "Auto-replied", "Answered WhatsApp call", "Declined WhatsApp call", "Phone Screen Unlock with Password",
    "Phone Screen Unlock", "Phone Screen Lock", "Phone Volume Force-Muted", "Phone Volume Muted", "Phone DND Enabled", "Phone DND Disabled",
    "Phone Call", "Started Relentless Redial Loop", "Phone screenshot", "Laptop screenshot", "Opened", "Open Website", "Open", "Workstation Lock",
    "Email trashed", "Email reply", "Email", "Screen Vision Analysis", "Screen Analysis via Gemini Vision", "Authorized voice profile",
    "Revoked guest access", "Guest", "ADB connection failed", "Contacts synced from phone", "Autonomous Gmail opened", "Set Alarm",
    "Created Proactive Reminder", "Introduced", "Allowed", "Muted", "Scheduled message", "Pushed file to phone", "Skill refused", "Skill failed",
    "Skill done", "Learned skill", "Planned by local AI", "Researched", "Agent done", "Project built with issues", "Project built",
], key=len, reverse=True)
_STATUS_OK = {"SUCCESS", "FAIL", "DENIED", "BLOCKED", "UNVERIFIED", "PARTIAL"}


def shape(text):
    """'send message to Rahul saying call me' -> 'send message to _ _ _ _' (+ the total word count). No other word survives."""
    words = re.findall(r"[^\W\d_]+|\d+", (text or "").lower())
    head, masked = [], False
    for w in words[:SHAPE_WORDS]:
        masked = masked or w not in COMMAND_WORDS          # after the first unknown word (a name, the start of a message) NOTHING more is kept:
        head.append("_" if masked else w)                  # command words that happen to occur inside a message must not leak its outline
    return " ".join(head), len(words)


def script_of(text):
    dev = bool(re.search(r"[ऀ-ॿ]", text or ""))
    lat = bool(re.search(r"[A-Za-z]", text or ""))
    return "mixed" if dev and lat else "devanagari" if dev else "latin" if lat else "none"


def audit_category(action):
    a = str(action or "")
    return next((c for c in AUDIT_CATEGORIES if a.startswith(c)), "other")


def error_where(context, known=None):
    """Only the leading identifier of the context, and only when it is the name of something in JARVIS's own code
    ('send_whatsapp/typing' -> 'send_whatsapp'). Anything else - a path, an IP, a person's name used as context - becomes 'other'."""
    m = re.match(r"[A-Za-z_]\w{0,48}", str(context or ""))
    name = m.group(0) if m else ""
    return name if name and known is not None and known(name) else "other"


def error_type(exc):
    return type(exc).__name__ if isinstance(exc, BaseException) else "message"


class Recorder:
    def __init__(self, path, install_id, enabled=lambda: True, source="voice", known=None):
        self.path, self.install_id, self.enabled, self.source, self.known = path, install_id, enabled, source, known
        self.lock = threading.RLock()
        self.open = None                            # the command being handled right now
        self._bg_seen = {}

    def _write(self, rec):
        try:
            if not self.enabled():
                return
            rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": self.install_id, **rec}
            with self.lock:
                if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".old")
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
        except Exception:
            pass                                    # a usage log must never be the reason a command fails

    def start(self, text):
        with self.lock:
            if self.open is not None:               # the previous command never reached its end: it crashed or was cut off
                self.finish(status="no_end")
            sh, n = shape(text)
            self.open = {"t0": time.time(), "shape": sh, "words": n, "script": script_of(text), "handlers": [], "actions": [], "errors": []}

    def handler(self, name, handled):
        with self.lock:
            if self.open is not None and handled and name not in self.open["handlers"]:
                self.open["handlers"].append(name)

    def action(self, action, status):
        item = {"what": audit_category(action), "status": status if status in _STATUS_OK else "other"}
        with self.lock:
            if self.open is not None:
                self.open["actions"].append(item)
                return
        self._write({"kind": "background", **item})

    def error(self, context, exc):
        item = {"where": error_where(context, self.known), "type": error_type(exc)}
        with self.lock:
            if self.open is not None:
                if item not in self.open["errors"]:
                    self.open["errors"].append(item)
                return
            key = (item["where"], item["type"])
            if time.time() - self._bg_seen.get(key, 0) < BG_REPEAT_SECS:
                return
            self._bg_seen[key] = time.time()
        self._write({"kind": "background_error", **item})

    def finish(self, status=None):
        with self.lock:
            c, self.open = self.open, None
        if c is None:
            return
        if status is None:
            bad = c["errors"] or any(a["status"] in ("FAIL", "DENIED", "BLOCKED") for a in c["actions"])
            status = "problem" if bad else "ok"
        self._write({"kind": "command", "src": self.source, "shape": c["shape"], "words": c["words"], "script": c["script"],
                     "handlers": c["handlers"], "actions": c["actions"], "errors": c["errors"], "ms": int((time.time() - c["t0"]) * 1000), "status": status})


NOTICE = "\n".join([
    "NOTICE: JARVIS keeps a small LOCAL usage log (usage_log.jsonl in this folder): which features you use and whether they worked.",
    "        It never contains your words, names, numbers, messages, keys or passwords, and JARVIS does not send it anywhere.",
    r"        See it:        venv\Scripts\python.exe -m jarvis_addons.usage_log",
    r"        Turn it off:   venv\Scripts\python.exe -m jarvis_addons.usage_log --off",
])
NOTICE_SPOKEN = ("One thing you should know: I keep a small local usage log of which features you use and whether they worked. "
                 "It never contains your words, names or messages, and I do not send it anywhere. "
                 "The command to switch it off is on the screen and in the read me file.")


def needs_notice(host):
    """True when nobody has chosen yet: setup (which explains the log and asks) was skipped, and the notice was never shown."""
    if os.environ.get("JARVIS_ADDON_TOOL"):          # a helper tool loading JARVIS quietly (its output is hidden): the notice would be
        return False                                 # swallowed and then never shown for real - leave it for the next real start
    get = host["get_config"]
    return get("usage_log") is None and get("usage_log_notice") is None


def _install_id(host):
    iid = host["get_config"]("usage_install_id")
    if not iid:
        iid = uuid.uuid4().hex[:12]                 # random: says nothing about the person or the PC
        host["set_config"]("usage_install_id", iid)
    return iid


def _wrap(host, name, make):
    fn = host.get(name)
    if callable(fn) and not getattr(fn, "_usage_log", False):
        new = functools.wraps(fn)(make(fn))
        new._usage_log = True
        host[name] = new


def install(host):
    need = ("note_mood", "care_after", "log_audit", "log_debug", "get_config", "set_config", "JARVIS_DIR")
    if any(k not in host for k in need) or host.get("_usage_recorder") is not None:
        return False
    rec = Recorder(os.path.join(host["JARVIS_DIR"], FILE_NAME), _install_id(host),
                   enabled=lambda: host["get_config"]("usage_log", "on") == "on", source="cli" if host.get("CLI_MODE") else "voice",
                   known=lambda name: name in host and not name.startswith("__"))
    host["_usage_recorder"] = rec
    late = {"done": False}
    notice = {"speak": False}
    try:
        if needs_notice(host):                      # the setup wizard was skipped: never record silently - say so, once
            (host.get("safe_print") or print)(NOTICE)
            if host.get("file_log"):
                host["file_log"]("[USAGE LOG] first-run notice shown (setup was skipped)")
            host["set_config"]("usage_log_notice", "shown")
            notice["speak"] = not host.get("CLI_MODE")          # in the voice loop it is also SAID once, after the first command
    except Exception:
        pass

    def wrap_handlers():
        """The *_commands functions are the feature groups. Some are defined after the add-ons are installed, so they are
        wrapped on the first command, when the whole of JARVIS is loaded."""
        if late["done"]:
            return
        late["done"] = True
        for name in [n for n in list(host) if re.fullmatch(r"[a-z_]+_commands?", n) and n not in ("addon_commands", "transcribe_command", "looks_like_command", "nlu_command")]:
            def make(fn, name=name):
                def wrapper(*a, **k):
                    try:
                        out = fn(*a, **k)
                    except Exception as e:
                        rec.error(name, e)
                        raise
                    rec.handler(name, bool(out))
                    return out
                return wrapper
            _wrap(host, name, make)

    def mk_note_mood(fn):
        def note_mood(text, *a, **k):               # called by the router at the very start of every command of the owner
            try:
                wrap_handlers()
                rec.start(text)
            except Exception:
                pass
            return fn(text, *a, **k)
        return note_mood

    def mk_care_after(fn):
        def care_after(*a, **k):                    # called by the router when the command is finished
            try:
                rec.finish()
            except Exception:
                pass
            out = fn(*a, **k)
            if notice["speak"]:
                notice["speak"] = False
                try:
                    host["speak"](NOTICE_SPOKEN)
                except Exception:
                    pass
            return out
        return care_after

    def mk_log_audit(fn):
        def log_audit(action, risk_level="LOW", status="SUCCESS", *a, **k):
            try:
                rec.action(action, status)
            except Exception:
                pass
            return fn(action, risk_level, status, *a, **k)
        return log_audit

    def mk_log_debug(fn):
        def log_debug(context, exc, *a, **k):
            try:
                rec.error(context, exc)
            except Exception:
                pass
            return fn(context, exc, *a, **k)
        return log_debug

    _wrap(host, "note_mood", mk_note_mood)
    _wrap(host, "care_after", mk_care_after)
    _wrap(host, "log_audit", mk_log_audit)
    _wrap(host, "log_debug", mk_log_debug)
    atexit.register(lambda: rec.finish(status="interrupted"))          # a command still open when JARVIS exits (crash, window closed)
    return True


def _read(path):
    out = []
    for p in (path + ".old", path):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


def summary(rows):
    from collections import Counter
    cmds = [r for r in rows if r.get("kind") == "command"]
    return {
        "commands": len(cmds),
        "by_status": dict(Counter(r["status"] for r in cmds)),
        "by_handler": dict(Counter(h for r in cmds for h in (r["handlers"] or ["(core router)"])).most_common()),
        "actions": dict(Counter(f"{a['what']} / {a['status']}" for r in rows for a in (r.get("actions") or ([r] if r.get("kind") == "background" else []))).most_common()),
        "errors": dict(Counter(f"{e['where']} / {e['type']}" for r in rows for e in (r.get("errors") or ([r] if r.get("kind") == "background_error" else []))).most_common()),
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, FILE_NAME)
    if "--on" in argv or "--off" in argv:
        import sqlite3
        con = sqlite3.connect(os.path.join(root, "jarvis_memory.db"))
        con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
        con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('usage_log', ?)", ("on" if "--on" in argv else "off",))
        con.commit()
        con.close()
        print("Usage log is now " + ("ON" if "--on" in argv else "OFF") + ". (A running JARVIS picks this up at the next command.)")
        return 0
    rows = _read(path)
    if "--summary" in argv:
        print(json.dumps(summary(rows), indent=1))
        return 0
    print(f"{len(rows)} line(s) in {path}\nThis is everything that is recorded - no words of your commands except the public command words, no names, no messages.\n")
    for r in rows[-40:]:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
