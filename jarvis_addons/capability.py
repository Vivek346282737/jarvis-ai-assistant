"""Self-extending capabilities (add-on): "Jarvis, add a feature that flips a coin" -> JARVIS writes the code, tests it, registers
it and runs it. Like skills for a smart speaker, written by JARVIS itself.

    venv\\Scripts\\python.exe -m jarvis_addons.capability --list              what JARVIS has built for itself
    venv\\Scripts\\python.exe -m jarvis_addons.capability --remove NAME       take one out again (the file is deleted)
    venv\\Scripts\\python.exe -m jarvis_addons.capability --check             re-test every capability file

By voice / chat:  "add a feature that ..." / "... wala feature add karo" / "naya feature banao: ..."
                  "list capabilities" / "remove capability NAME"

What keeps JARVIS safe:
  * generated code goes ONLY into jarvis_addons/capabilities/NAME.py - live_cmd_console.py and every other file are never touched
  * a capability is asked only AFTER all of JARVIS's own rules said "not mine", so it can never take over an existing command
  * the code is checked before it ever runs here: no file writing, no programs, no network sockets, no secrets (see check_code);
    then it is tested in a SEPARATE process with a time limit; three attempts, and a failure deletes the file again
  * requests about deleting, money or passwords are never built: they are written to PENDING_FROM_VIVEK.md for the owner
  * at run time a capability gets a time limit and its errors are caught - JARVIS itself keeps running

A capability file:   NAME, DESCRIPTION, TRIGGERS (phrases), EXAMPLE (one command),  run(text, ctx) -> the sentence to say,
                     self_test().   ctx offers: ask_ai(prompt), http_get(url), read_text(path), list_dir(path)."""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP_DIR = os.path.join(ROOT, "jarvis_addons", "capabilities")
REGISTRY = os.path.join(CAP_DIR, "registry.json")
ACTIVITY = os.path.join(CAP_DIR, "activity.jsonl")
PENDING = os.path.join(ROOT, "PENDING_FROM_VIVEK.md")
MAX_TRIES = 3
TEST_TIMEOUT = 25            # seconds for the separate test process
RUN_TIMEOUT = 20             # seconds one capability may take when it is used
ALLOWED_IMPORTS = {"random", "math", "datetime", "time", "re", "json", "statistics", "string", "itertools", "collections", "functools", "textwrap",
                   "calendar", "decimal", "fractions", "uuid", "hashlib", "base64", "html", "urllib.parse", "unicodedata", "difflib", "operator"}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open", "globals", "locals", "vars", "input", "breakpoint", "exit", "quit", "getattr", "setattr", "delattr"}
BUILD_RX = re.compile(r"\b(?:add|create|make|build|write|develop)\b.{0,40}\b(?:feature|capability)\b"
                      r"|\b(?:feature|capability)\b.{0,60}\b(?:add|bana|jod|daal)\w*"
                      r"|\b(?:naya|nayi|new)\s+(?:feature|capability)\b", re.I)
LIST_RX = re.compile(r"^(?:list|show|what are)\s+(?:my\s+|your\s+|all\s+)?capabilit(?:y|ies)$|^capabilit(?:y|ies)\s+(?:list|dikhao|batao)$", re.I)
REMOVE_RX = re.compile(r"^(?:remove|delete|forget)\s+(?:the\s+)?capability\s+([a-z0-9_ ]{2,40})$|^capability\s+([a-z0-9_ ]{2,40})\s+(?:hatao|hata do|remove)$", re.I)
DENY_RX = re.compile(r"\b(delet\w*|remov\w*|eras\w*|wip(?:e|es|ed|ing)|format\w*|shut\s*down|restart\w*|reboot\w*|kill\w*|uninstall\w*|factory\s*reset|registry|"
                     r"pay|pays|paying|payment\w*|upi|bank\w*|money|paisa|paise|rupee\w*|transfer\w*|buy\w*|purchas\w*|order\w*|shopping|wallet\w*|card\w*|"
                     r"password\w*|passcode\w*|otp|pin|credential\w*|token\w*|api\s*key\w*|login\w*|log\s*in|sign\s*in|hack\w*|keylog\w*|spy\w*|steal\w*)\b", re.I)
_LOCK = threading.RLock()
_LOADED = {}                 # name -> (mtime, module)

PROMPT = """You write ONE small Python module that gives a voice assistant a new ability. Reply in EXACTLY this layout and nothing else:

NAME: short_snake_case_name
DESCRIPTION: one sentence
TRIGGERS: phrase one | phrase two | phrase three | a Hinglish phrase
EXAMPLE: one command a user would say
```python
NAME = "short_snake_case_name"
DESCRIPTION = "one sentence"
TRIGGERS = ["phrase one", "phrase two"]
EXAMPLE = "one command a user would say"

def run(text, ctx):
    # text = what the user said. Return ONE short sentence to be spoken.
    return "..."

def self_test():
    # raise AssertionError if run() misbehaves. Use a tiny fake ctx if you need one.
    assert isinstance(run(EXAMPLE, None), str)
```

Rules for the code:
- Only these imports: random, math, datetime, time, re, json, statistics, string, itertools, collections, functools, textwrap, calendar, decimal, fractions, uuid, hashlib, base64, html, urllib.parse, unicodedata, difflib, operator.
- No files, no open(), no os, no subprocess, no sockets, no eval/exec, no getattr, no names starting with two underscores.
- For anything outside pure Python use ctx (it may be None in self_test, so guard it):
    ctx.ask_ai(prompt) -> text from an AI        ctx.http_get(url) -> page text (http/https)
    ctx.read_text(path) -> text of a file         ctx.list_dir(path) -> list of file names
- TRIGGERS: 3 to 6 phrases of 2 to 5 lowercase words that clearly mean THIS ability and nothing else (no single common words).
- run() must always return a non-empty string and never raise; keep the whole module under 80 lines."""


# ------------------------------------------------------------------ small helpers
def emit(state, text):
    """One line for the live activity panel (and the orb): state = THINKING | CODING | TESTING | SUCCESS | ERROR | IDLE."""
    try:
        os.makedirs(CAP_DIR, exist_ok=True)
        with open(ACTIVITY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "state": state, "text": text}) + "\n")
    except OSError:
        pass


def recent_activity(n=40):
    try:
        with open(ACTIVITY, encoding="utf-8") as f:
            return [json.loads(x) for x in f.readlines()[-n:] if x.strip()]
    except (OSError, ValueError):
        return []


def registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(reg):
    os.makedirs(CAP_DIR, exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=1)
    os.replace(tmp, REGISTRY)


def tokens(text):
    words = re.findall(r"[a-z0-9ऀ-ॿ]+", (text or "").lower())
    return [w for w in words if w not in ("jarvis", "please", "hey", "ok", "okay")]


def matches(text, triggers):
    """Every word of one trigger phrase (2+ words) is in the sentence, and the sentence is not much longer than the phrase."""
    said = tokens(text)
    for t in triggers or []:
        want = tokens(t)
        if len(want) >= 2 and all(w in said for w in want) and len(said) <= len(want) + 8:
            return True
    return False


def find(text, reg=None):
    for name, e in (registry() if reg is None else reg).items():
        if e.get("status") == "active" and matches(text, e.get("triggers")):
            return name
    return None


def denied(text):
    m = DENY_RX.search(text or "")
    return m.group(0).lower() if m else None


def note_pending(request, why):
    try:
        with open(PENDING, "a", encoding="utf-8") as f:
            f.write(f"\n## Capability request that needs your approval ({time.strftime('%Y-%m-%d %H:%M')})\n"
                    f"Asked: \"{request}\"\nNot built by JARVIS itself because it mentions: {why} (deleting, money and passwords are never self-built).\n"
                    "If you want it: tell Claude to build it by hand, or ask again in words that do not touch those areas.\n")
    except OSError:
        pass


# ------------------------------------------------------------------ what generated code may contain
def check_code(code):
    """-> None when the code is acceptable, otherwise the reason (given back to the AI so it can fix it)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"syntax error line {e.lineno}: {e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m not in ALLOWED_IMPORTS and m.split(".")[0] not in {a for a in ALLOWED_IMPORTS if "." not in a}:
                    return f"import of '{m}' is not allowed"
                if m.split(".")[0] == "urllib" and m != "urllib.parse":
                    return f"import of '{m}' is not allowed (use ctx.http_get)"
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return f"'{node.id}' is not allowed"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"attribute '{node.attr}' is not allowed"
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await)):
            return f"{type(node).__name__} is not allowed"
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} | {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    missing = [x for x in ("NAME", "DESCRIPTION", "TRIGGERS", "EXAMPLE", "run", "self_test") if x not in names]
    if missing:
        return "missing in the module: " + ", ".join(missing)
    if len(code.splitlines()) > 140:
        return "the module is too long"
    return None


def parse_reply(reply):
    """-> {"name", "description", "triggers", "example", "code"} or None"""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.S)
    if not m:
        return None
    code, head = m.group(1).strip() + "\n", (reply or "")[:m.start()]

    def field(key):
        f = re.search(rf"^{key}\s*:\s*(.+)$", head, re.M | re.I)
        return f.group(1).strip() if f else ""
    name = re.sub(r"[^a-z0-9_]", "_", field("NAME").lower()).strip("_")[:40]
    trig = [t.strip().lower() for t in field("TRIGGERS").split("|") if len(tokens(t)) >= 2][:6]
    if not name or not trig:
        return None
    return {"name": name, "description": field("DESCRIPTION")[:200], "triggers": trig, "example": field("EXAMPLE")[:120] or trig[0], "code": code}


# ------------------------------------------------------------------ testing in a separate process
_HARNESS = r'''
import importlib.util, json, sys
class Ctx:
    def ask_ai(self, prompt, system=""): return "test answer"
    def http_get(self, url, timeout=10): return "test page text"
    def read_text(self, path): return "test file text"
    def list_dir(self, path): return ["a.txt", "b.txt"]
try:
    spec = importlib.util.spec_from_file_location("cap_under_test", sys.argv[1])
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.self_test()
    out = mod.run(sys.argv[2], Ctx())
    assert isinstance(out, str) and out.strip(), "run() must return a non-empty string, it returned %r" % (out,)
    print("CAP_OK " + json.dumps(out[:300]))
except BaseException as e:
    print("CAP_FAIL %s: %s" % (type(e).__name__, str(e)[:300]))
'''


def test_file(path, example, timeout=TEST_TIMEOUT):
    """-> (ok, the sentence it said | why it failed). The file runs in its own process: a crash or an endless loop cannot hurt JARVIS."""
    try:
        from jarvis_agent.util import child_env
        env = child_env()
    except Exception:
        env = dict(os.environ)
    try:
        r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-I", "-c", _HARNESS, path, example], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=CAP_DIR, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, f"the test did not finish in {timeout} seconds (an endless loop?)"
    out = (r.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if last.startswith("CAP_OK "):
        return True, json.loads(last[7:])
    return False, (last[9:] if last.startswith("CAP_FAIL ") else (r.stderr or "no output").strip()[-300:])


# ------------------------------------------------------------------ building
def build(request, ask, say=lambda s: None, tries=MAX_TRIES):
    """Writes, checks and tests a capability for `request`. ask(system, prompt) -> the AI's text.
    -> (name, registry entry) on success, (None, why) when it could not be built (nothing is left behind)."""
    why = denied(request)
    if why:
        note_pending(request, why)
        emit("ERROR", f"Not building: the request mentions '{why}' - written to PENDING_FROM_VIVEK.md")
        return None, f"denied:{why}"
    emit("THINKING", f"New capability requested: {request[:80]}")
    feedback, last_error, path = "", "the AI gave no usable answer", None
    for attempt in range(1, tries + 1):
        say(f"Writing the code, attempt {attempt}." if attempt > 1 else "I do not have that yet. Writing it now.")
        emit("CODING", f"Asking the AI for the code (attempt {attempt} of {tries})")
        reply = ask(PROMPT, f"The new ability the user asked for: {request}{feedback}")
        got = parse_reply(reply)
        if not got:
            last_error = "the answer was not in the required layout (NAME / TRIGGERS / one python block)"
        else:
            last_error = check_code(got["code"])
        if got and not last_error:
            with _LOCK:
                reg = registry()
                name = got["name"]
                if name in reg and reg[name].get("status") == "active":
                    name = f"{name}_{int(time.time()) % 10000}"
                path = os.path.join(CAP_DIR, name + ".py")
                os.makedirs(CAP_DIR, exist_ok=True)
                emit("CODING", f"Creating capabilities/{name}.py")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(got["code"])
            emit("TESTING", f"Running the self-test of {name} in a separate process")
            ok, detail = test_file(path, got["example"])
            if ok:
                entry = {"file": name + ".py", "description": got["description"], "triggers": got["triggers"], "example": got["example"], "request": request[:200],
                         "created": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "active", "tries": attempt, "uses": 0, "last_used": "", "errors": 0}
                with _LOCK:
                    reg = registry()
                    reg[name] = entry
                    _save(reg)
                emit("SUCCESS", f"Capability '{name}' registered after {attempt} attempt(s); test said: {detail[:80]}")
                return name, entry
            last_error = "the test failed: " + detail
            try:
                os.remove(path)                               # rollback: a file that does not pass is never left behind
            except OSError:
                pass
            emit("ERROR", f"Test failed, removed capabilities/{name}.py: {detail[:100]}")
        else:
            emit("ERROR", f"Attempt {attempt} rejected: {last_error[:120]}")
        feedback = f"\n\nYour previous answer was rejected: {last_error}\nWrite it again, fixing exactly that."
    emit("ERROR", f"Gave up after {tries} attempts: {last_error[:120]}")
    return None, last_error


def remove(name):
    with _LOCK:
        reg = registry()
        key = name if name in reg else next((k for k in reg if k.replace("_", " ") == name.replace("_", " ").strip().lower()), None)
        if not key:
            return False
        try:
            os.remove(os.path.join(CAP_DIR, reg[key]["file"]))
        except OSError:
            pass
        reg[key]["status"] = "removed"
        reg[key]["removed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(reg)
        _LOADED.pop(key, None)
    emit("IDLE", f"Capability '{key}' removed")
    return True


# ------------------------------------------------------------------ using a capability inside JARVIS
class Ctx:
    """What a capability may use of JARVIS: the AI, reading a web page, reading a file. Nothing that changes anything."""

    def __init__(self, host):
        self._host = host

    def ask_ai(self, prompt, system="You answer briefly and clearly."):
        try:
            return self._host["gemini_text"]([str(prompt)[:6000]], system, max_tokens=500) or ""
        except Exception:
            return ""

    def http_get(self, url, timeout=10):
        import urllib.request
        if not re.match(r"https?://", str(url)):
            return ""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=min(timeout, 15)) as r:
                return r.read(200_000).decode("utf-8", errors="replace")
        except Exception:
            return ""

    def read_text(self, path):
        p = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
        if re.search(r"\.(db|sqlite|npy|keystore|pem|key|env)$", p, re.I) or "jarvis_memory" in p.lower():
            return ""                                         # never JARVIS's own data or keys
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read(200_000)
        except OSError:
            return ""

    def list_dir(self, path):
        try:
            return sorted(os.listdir(os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))))[:200]
        except OSError:
            return []


def load(name, entry):
    path = os.path.join(CAP_DIR, entry["file"])
    mtime = os.path.getmtime(path)
    if name in _LOADED and _LOADED[name][0] == mtime:
        return _LOADED[name][1]
    with open(path, encoding="utf-8") as f:
        problem = check_code(f.read())                        # also a file somebody edited by hand must pass the same check
    if problem:
        raise RuntimeError("the capability file does not pass the safety check: " + problem)
    spec = importlib.util.spec_from_file_location("jarvis_cap_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LOADED[name] = (mtime, mod)
    return mod


def run(name, text, host, timeout=RUN_TIMEOUT):
    """-> (ok, sentence). Never raises; a capability that hangs is given up on after `timeout` seconds."""
    reg = registry()
    entry = reg.get(name)
    if not entry or entry.get("status") != "active":
        return False, "I do not have that capability."
    box = {}

    def work():
        try:
            box["out"] = load(name, entry).run(text, Ctx(host))
        except BaseException as e:
            box["err"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=work, daemon=True, name="capability_" + name)
    t.start()
    t.join(timeout)
    ok = isinstance(box.get("out"), str) and bool(box["out"].strip())
    with _LOCK:
        reg = registry()
        if name in reg:
            reg[name]["uses"] = reg[name].get("uses", 0) + 1
            reg[name]["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if not ok:
                reg[name]["errors"] = reg[name].get("errors", 0) + 1
            _save(reg)
    if ok:
        return True, box["out"].strip()[:1500]
    why = box.get("err") or ("it did not answer in time" if t.is_alive() else "it returned nothing")
    emit("ERROR", f"Capability '{name}' failed: {why[:120]}")
    return False, f"My {name.replace('_', ' ')} capability failed, so I did not use it."


def describe(reg=None):
    rows = [(n, e) for n, e in (registry() if reg is None else reg).items() if e.get("status") == "active"]
    if not rows:
        return "I have not built any capability for myself yet. Say: add a feature that flips a coin."
    return f"I have built {len(rows)} capabilit{'y' if len(rows) == 1 else 'ies'} for myself: " + "; ".join(f"{n.replace('_', ' ')} - say {e['example']}" for n, e in rows[:8]) + "."


def install(host):
    """Hooks for a running JARVIS (also in terminal / Telegram / dashboard runs, which use the same command pipeline)."""
    if host.get("_capability") or not callable(host.get("check_dynamic_rules")) or not callable(host.get("nlu_handle")):
        return False
    host["_capability"] = True
    speak = lambda s: host["speak"](s) if callable(host.get("speak")) else None
    log = host.get("file_log") or (lambda s: None)

    def ask(system, prompt):
        return host["gemini_text"]([prompt], system, max_tokens=1800, temperature=0.3, groq_task="smart")

    orig_rules = host["check_dynamic_rules"]

    def check_dynamic_rules(cmd, *a, **k):
        text = (cmd or "").strip()
        try:
            if LIST_RX.match(text):
                speak(describe())
                return True
            m = REMOVE_RX.match(text)
            if m:
                name = (m.group(1) or m.group(2)).strip()
                speak(f"Removed the capability {name}." if remove(name) else f"I have no capability called {name}.")
                return True
            if BUILD_RX.search(text) and not re.search(r"\.[a-z]{2,4}\b|[\\/]", text):          # a file / path = the coding agent's job, not this
                owner = host.get("require_owner")
                if callable(owner) and not owner("add a capability"):
                    return True
                why = denied(text)
                if why:
                    build(text, ask)                           # only writes the request into PENDING_FROM_VIVEK.md
                    speak(f"I will not build that by myself, Boss, because it is about {why}. I wrote it down for your approval.")
                    return True
                log("[CAPABILITY] build requested: " + text[:100])
                name, info = build(text, ask, say=speak)
                if not name:
                    speak("I could not build that safely after three attempts, so I removed it again. Nothing else was changed.")
                    log("[CAPABILITY] build failed: " + str(info)[:150])
                    return True
                log(f"[CAPABILITY] built '{name}' in {info['tries']} attempt(s)")
                ok, out = run(name, info["example"], host)
                speak(f"Done. I can now do {name.replace('_', ' ')}. For example, say: {info['example']}.")
                speak(out)
                emit("IDLE", f"Ran '{info['example']}': {out[:80]}")
                return True
        except Exception as e:
            host["log_debug"]("capability/command", e)
        return orig_rules(cmd, *a, **k)
    host["check_dynamic_rules"] = check_dynamic_rules

    orig_nlu = host["nlu_handle"]

    def nlu_handle(raw_text, *a, **k):
        try:
            name = find(raw_text)                              # only reached when none of JARVIS's own rules took the sentence
            if name:
                ok, out = run(name, raw_text, host)
                speak(out)
                log(f"[CAPABILITY] used '{name}' ({'ok' if ok else 'failed'})")
                return True
        except Exception as e:
            host["log_debug"]("capability/use", e)
        return orig_nlu(raw_text, *a, **k)
    host["nlu_handle"] = nlu_handle
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    reg = registry()
    if "--remove" in argv:
        name = (argv[argv.index("--remove") + 1:] or [""])[0]
        print("removed" if remove(name) else "no such capability")
        return 0
    if "--adopt" in argv:                                    # a capability written by hand goes through exactly the same checks
        src = open((argv[argv.index("--adopt") + 1:] or [""])[0], encoding="utf-8").read()
        name, info = build("adopted from a file written by hand", lambda system, prompt: src, tries=1)
        print(f"registered {name}: say '{info['example']}'" if name else f"NOT registered: {info}")
        return 0 if name else 1
    if "--check" in argv:
        bad = 0
        for name, e in reg.items():
            if e.get("status") != "active":
                continue
            ok, detail = test_file(os.path.join(CAP_DIR, e["file"]), e["example"])
            bad += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail[:100]}")
        return 1 if bad else 0
    for name, e in reg.items():
        print(f"  {e.get('status', '?'):8} {name:24} made {e.get('created', '')}  used {e.get('uses', 0)}x  say: {e.get('example', '')}")
    if not reg:
        print("  no capabilities yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
