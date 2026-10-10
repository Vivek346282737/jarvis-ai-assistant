"""JARVIS dashboard (add-on): a local web page with chat, live status cards and the self-extension activity.

    venv\\Scripts\\python.exe -m jarvis_addons.dashboard --run              serve it (Ctrl+C stops)   -> http://localhost:8765
    venv\\Scripts\\python.exe -m jarvis_addons.dashboard --start            start it in the background (JARVIS does this by itself)
    venv\\Scripts\\python.exe -m jarvis_addons.dashboard --set-password X   choose the password (at least 6 characters)
    venv\\Scripts\\python.exe -m jarvis_addons.dashboard --status           is it running, which addresses

A sentence typed (or spoken) here goes through exactly the same command pipeline as `live_cmd_console.py --say`; what JARVIS
would have said comes back as the answer. The chat history is kept in dashboard.db in this folder, so it survives a refresh.

Security: the page is reachable from the same Wi-Fi, so nothing works without the password (stored only as a salted hash).
Five wrong passwords lock that device out for five minutes. The first start makes a random password and writes it to
dashboard_password.txt in this folder (delete that file after you have read it or set your own password)."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "jarvis_memory.db")
CHAT_DB = os.path.join(ROOT, "dashboard.db")
UI = os.path.join(ROOT, "jarvis_addons", "dashboard_ui", "index.html")
PW_FILE = os.path.join(ROOT, "dashboard_password.txt")
PORT = 8765
SESSION_HOURS = 12
MAX_FAILS, LOCK_SECONDS = 5, 300
LOG_KINDS = (("[POWER]", "power"), ("[CAPABILITY]", "capability"), ("[TELEGRAM]", "telegram"), ("[ADB] phone", "phone"), ("[PERF] pace", "laptop"), ("[EMAIL]", "email"))


# ------------------------------------------------------------------ password
def _cfg(key, db=None):
    con = sqlite3.connect(db or DB)
    try:
        row = con.execute("SELECT val FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[0] else None
    except sqlite3.Error:
        return None
    finally:
        con.close()                                           # also when the table is missing: an open handle locks the file on Windows


def _cfg_set(key, val, db=None):
    con = sqlite3.connect(db or DB)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (key, val))
    con.commit()
    con.close()


def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()


def password_ok(pw, stored):
    try:
        return bool(stored) and hmac.compare_digest(hash_password(pw, stored.split("$")[0]), stored)
    except (ValueError, AttributeError):
        return False


def ensure_password(db=None, pw_file=None):
    """The first start makes a random password. -> the plain password when one was just made, else None."""
    if _cfg("dashboard_pw", db):
        return None
    pw = secrets.token_urlsafe(8)
    _cfg_set("dashboard_pw", hash_password(pw), db)
    with open(pw_file or PW_FILE, "w", encoding="utf-8") as f:
        f.write(f"JARVIS dashboard password: {pw}\nOpen http://localhost:{PORT} on this laptop.\nChange it:  venv\\Scripts\\python.exe -m jarvis_addons.dashboard --set-password YOUR_PASSWORD\nDelete this file after reading it.\n")
    return pw


class Guard:
    """Sessions and the lock-out after wrong passwords."""

    def __init__(self, db=None):
        self.db, self.sessions, self.fails, self.lock = db, {}, {}, threading.Lock()

    def locked(self, ip, now=None):
        now = now or time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < LOCK_SECONDS]
            self.fails[ip] = f
            return len(f) >= MAX_FAILS

    def login(self, ip, pw, now=None):
        """-> session token | None"""
        now = now or time.time()
        if self.locked(ip, now):
            return None
        if not password_ok(pw or "", _cfg("dashboard_pw", self.db)):
            with self.lock:
                self.fails.setdefault(ip, []).append(now)
            return None
        tok = secrets.token_urlsafe(32)
        with self.lock:
            self.fails.pop(ip, None)
            self.sessions[tok] = now + SESSION_HOURS * 3600
        return tok

    def valid(self, tok, now=None):
        with self.lock:
            return bool(tok) and self.sessions.get(tok, 0) > (now or time.time())

    def logout(self, tok):
        with self.lock:
            self.sessions.pop(tok, None)


# ------------------------------------------------------------------ chat history (its own small database)
class History:
    def __init__(self, path=None):
        self.path, self.lock = path or CHAT_DB, threading.Lock()
        self._do("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, role TEXT, text TEXT)")

    def _do(self, sql, params=(), fetch=False):
        """One statement, and the file is closed again (an open handle would keep the database locked on Windows)."""
        with self.lock:
            con = sqlite3.connect(self.path, timeout=10)
            try:
                cur = con.execute(sql, params)
                out = cur.fetchall() if fetch else cur.lastrowid
                con.commit()
                return out
            finally:
                con.close()

    def add(self, role, text):
        text = (text or "").strip()
        return self._do("INSERT INTO messages (ts, role, text) VALUES (?, ?, ?)", (time.time(), role, text[:4000])) if text else None

    def since(self, after=0, limit=200):
        if after:
            rows = self._do("SELECT id, ts, role, text FROM messages WHERE id > ? ORDER BY id LIMIT ?", (after, limit), True)
        else:
            rows = self._do("SELECT id, ts, role, text FROM messages ORDER BY id DESC LIMIT ?", (limit,), True)[::-1]
        return [{"id": r[0], "ts": r[1], "role": r[2], "text": r[3]} for r in rows]

    def clear(self):
        self._do("DELETE FROM messages")


# ------------------------------------------------------------------ running a sentence through JARVIS
class Runner:
    """One sentence at a time through JARVIS's own router; everything JARVIS would say is collected as the answer."""

    def __init__(self, host, history, mask=None, deny=None):
        self.host, self.history, self.mask, self.deny = host, history, mask or (lambda s: s), deny
        self.lock, self.busy, self.started, self.before_say = threading.Lock(), False, 0.0, None
        for name in ("speak", "speak_urgent"):                 # in this process JARVIS's voice goes to the page, not the speakers
            if name in host:
                host[name] = self.say

    def say(self, text):
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if text:
            if self.before_say:
                self.before_say()                             # build steps that happened before this sentence go into the chat first
            self.history.add("jarvis", self.mask(text))

    def submit(self, text, source="typed"):
        """-> False when JARVIS is still busy with the previous sentence."""
        text = (text or "").strip()[:1000]
        if not text or self.busy:
            return False
        self.busy, self.started = True, time.time()
        self.history.add("user", text)
        threading.Thread(target=self._run, args=(text,), daemon=True, name="dashboard_command").start()
        return True

    def _run(self, text):
        try:
            with self.lock:
                before = self.history.since(0, 1)
                last = before[-1]["id"] if before else 0
                if self.deny and self.deny.search(text):
                    self.say("I do not do that from the dashboard. Say it to me at the laptop.")
                else:
                    self.host["route_and_execute"](text, self.host.get("CURRENT_ACTIVE_USER"))
                if not any(m["role"] == "jarvis" for m in self.history.since(last)):
                    self.say("Done.")
        except BaseException as e:
            if callable(self.host.get("log_debug")):
                self.host["log_debug"]("dashboard/command", e)
            self.history.add("system", "That command failed inside JARVIS; it was logged. JARVIS itself is still running.")
        finally:
            self.busy = False


# ------------------------------------------------------------------ real data for the cards
class Data:
    def __init__(self, host):
        self.host, self.cache, self.lock = host, {}, threading.Lock()

    def cached(self, key, seconds, fn):
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
        try:
            val = fn()
        except Exception as e:
            val = {"error": f"{type(e).__name__}: {e}"[:160]}
        with self.lock:
            self.cache[key] = (now, val)
        return val

    def system(self):
        import psutil
        from . import perf
        b = psutil.sensors_battery()
        vm = psutil.virtual_memory()
        return {"cpu": psutil.cpu_percent(0.2), "ram": vm.percent, "ram_used_gb": round((vm.total - vm.available) / 2 ** 30, 1), "ram_total_gb": round(vm.total / 2 ** 30, 1),
                "battery": None if b is None else {"percent": round(b.percent), "plugged": bool(b.power_plugged)}, "temp": perf.temperature(),
                "host": os.environ.get("COMPUTERNAME", "this PC")}

    def weather(self):
        import urllib.parse
        H = self.host
        city = H["get_config"]("city", "New Delhi")
        g = H["_http_json"]("https://geocoding-api.open-meteo.com/v1/search?name=" + urllib.parse.quote(city) + "&count=1&language=en&format=json")
        r = (g.get("results") or [None])[0]
        if not r:
            return {"error": f"no place called {city}"}
        w = H["_http_json"](f"https://api.open-meteo.com/v1/forecast?latitude={r['latitude']}&longitude={r['longitude']}"
                            "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                            "&timezone=auto&forecast_days=3")
        codes, d = H.get("_WX_CODES", {}), w["daily"]
        days = [{"date": d["time"][i], "desc": codes.get(d["weather_code"][i], "mixed weather"), "hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
                 "rain": d.get("precipitation_probability_max", [None] * 3)[i]} for i in range(len(d["time"]))]
        c = w["current"]
        return {"city": r.get("name", city), "now": round(c["temperature_2m"]), "desc": codes.get(c["weather_code"], "mixed weather"), "humidity": c.get("relative_humidity_2m"),
                "wind": c.get("wind_speed_10m"), "days": days, "source": "open-meteo.com", "fetched": time.time()}

    def phone(self):
        H = self.host
        target = H["get_config"]("adb_target") or ""
        state = (H["_adb_state"]() or {}).get(target)
        out = {"address": target, "connected": state == "device", "state": state or "not reachable"}
        if out["connected"]:
            run = H["run_silent_cmd"]
            sh = lambda *a: (run([H["ADB_BIN"], "-s", target, "shell", *a], timeout=8).stdout or "").strip()
            bat = sh("dumpsys", "battery")
            m = re.search(r"\blevel:\s*(\d+)", bat)
            out.update(battery=int(m.group(1)) if m else None, charging=bool(re.search(r"(AC|USB|Wireless) powered:\s*true", bat)),
                       model=(sh("getprop", "ro.product.manufacturer") + " " + sh("getprop", "ro.product.model")).strip(), android=sh("getprop", "ro.build.version.release"))
        return out

    def connectors(self):
        """Only what this JARVIS really has; each with whether it works right now."""
        H = self.host
        from . import power, telegram_bot
        phone = self.cached("phone", 45, self.phone)
        kinds = {k for k, p in power.running()}
        con = [{"id": "computer", "name": "Computer", "ok": True, "detail": f"{os.environ.get('COMPUTERNAME', 'this PC')}: apps, files, screen, keyboard"},
               {"id": "phone", "name": "Phone (ADB)", "ok": bool(phone.get("connected")), "detail": (f"{phone.get('model') or 'phone'} at {phone.get('address')}" if phone.get("connected") else f"not connected ({phone.get('address') or 'no phone linked'})")}]
        wa_ok = bool(phone.get("connected"))
        con.append({"id": "whatsapp", "name": "WhatsApp", "ok": wa_ok, "detail": ("through the phone; " if wa_ok else "needs the phone; ") + ("dry run ON (types, does not send)" if H["get_config"]("dry_run", "1") == "1" else "live (messages are really sent)")})
        try:
            mail = bool(H["email_configured"]())
        except Exception:
            mail = False
        addr = H["get_config"]("email_address") or ""
        con.append({"id": "email", "name": "Email", "ok": mail, "detail": (re.sub(r"^(.).*(@.*)$", r"\1***\2", addr) + " (Gmail)") if mail else "not set up"})
        tg_set = telegram_bot.configured()[0] is not None
        con.append({"id": "telegram", "name": "Telegram", "ok": tg_set and "telegram" in kinds, "detail": "bot is answering in the background" if tg_set and "telegram" in kinds else ("set up, but the bot is not running" if tg_set else "not set up")})
        try:
            ai_local = bool(H["ollama_up"]())
        except Exception:
            ai_local = False
        con.append({"id": "local_ai", "name": "Local AI", "ok": ai_local, "detail": (f"Ollama, model {H['get_config']('ollama_model') or '?'}" if ai_local else "Ollama is not running")})
        online = bool(H.get("client")) or bool(H["get_config"]("groq_keys"))
        con.append({"id": "online_ai", "name": "Online AI", "ok": online, "detail": ", ".join(n for n, ok in (("Gemini", bool(H.get("client"))), ("Groq", bool(H["get_config"]("groq_keys")))) if ok) or "no key saved"})
        con.append({"id": "voice", "name": "Voice", "ok": "jarvis" in kinds, "detail": "the always-on JARVIS is listening on this laptop" if "jarvis" in kinds else "the always-on JARVIS is not running (JARVIS Chalu starts it)"})
        return con

    def notifications(self, limit=40):
        """Recent real things: phone messages JARVIS saw, email follow-ups, reminders, and what JARVIS itself did."""
        items = []
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            for i, sender, text, ts, read in con.execute("SELECT id, sender, text, ts, read FROM inbox ORDER BY id DESC LIMIT 15"):
                items.append({"id": f"inbox-{i}", "kind": "message", "title": f"Message from {sender}", "text": text or "", "ts": ts, "unread": not read})
            for uid, name, subj, ts, tries, res in con.execute("SELECT uid, from_name, subject, last_ts, tries, resolved FROM email_reminders ORDER BY last_ts DESC LIMIT 8"):
                items.append({"id": f"mail-{uid}", "kind": "email", "title": f"Important email from {name}", "text": f"{subj}\nReminders sent: {tries}. " + ("Closed (answered or given up)." if res else "Still waiting for your reply."), "ts": ts, "unread": not res})
            for i, text, due, done in con.execute("SELECT id, reminder_text, due_time, is_done FROM reminders ORDER BY id DESC LIMIT 8"):
                items.append({"id": f"rem-{i}", "kind": "reminder", "title": "Reminder", "text": f"{text}\nDue: {due}" + (" (done)" if done else ""), "ts": str(due), "unread": not done})
            con.close()
        except sqlite3.Error:
            pass
        try:
            with open(os.path.join(ROOT, "jarvis.log"), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 400_000))
                lines = f.read().decode("utf-8", errors="replace").splitlines()[1:]
            n = 0
            for line in reversed(lines):
                kind = next((k for tag, k in LOG_KINDS if tag in line), None)
                if kind and re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", line):
                    text = re.sub(r"^\S+ \S+ \[pid \d+\] ", "", line)
                    items.append({"id": f"log-{hash(line) & 0xffffffff}", "kind": kind, "title": text[:70], "text": text, "ts": line[:19].replace(" ", "T"), "unread": False})
                    n += 1
                    if n >= 20:
                        break
        except OSError:
            pass
        items.sort(key=lambda x: str(x["ts"]), reverse=True)
        return items[:limit]

    def detail(self, card):
        H = self.host
        if card == "system":
            from . import perf
            m = perf.machine()
            mode, why = perf.choose_mode(m, H["get_config"]("perf_mode"))
            return {"title": "System", "rows": [["PC", f"{m['ram_gb']} GB RAM, {m['cores']} cores"], ["GPU", ", ".join(m["gpu"]) or "unknown"], ["Performance mode", f"{mode} ({why})"],
                                                 ["Local AI runs on", "the GPU" if (m["dedicated_gpu"] or H["get_config"]("perf_gpu") == "on") else "the CPU"]]
                    + [[tag, f"CPU {cpu:.1f}%  RAM {ram:.0f} MB"] for tag, cpu, ram in perf.usage(1.0)]}
        if card == "weather":
            w = self.cached("weather", 600, self.weather)
            if "error" in w:
                return {"title": "Weather", "rows": [["Problem", w["error"]]]}
            return {"title": f"Weather in {w['city']}", "rows": [["Now", f"{w['now']} C, {w['desc']}"], ["Humidity", f"{w['humidity']} %"], ["Wind", f"{w['wind']} km/h"]]
                    + [[d["date"], f"{d['desc']}, {d['lo']} to {d['hi']} C" + (f", rain {d['rain']} %" if d["rain"] is not None else "")] for d in w["days"]] + [["Source", w["source"]]]}
        if card == "devices":
            p, s = self.cached("phone", 45, self.phone), self.system()
            rows = [["Laptop", s["host"]], ["Laptop battery", "no battery" if not s["battery"] else f"{s['battery']['percent']} %" + (" (charging)" if s["battery"]["plugged"] else " (on battery)")],
                    ["Phone", p.get("model") or "not connected"], ["Phone address", p.get("address") or "-"], ["Phone link", p.get("state")]]
            if p.get("connected"):
                rows += [["Phone battery", f"{p.get('battery')} %" + (" (charging)" if p.get("charging") else "")], ["Android", p.get("android")]]
            return {"title": "Devices", "rows": rows}
        for c in self.cached("connectors", 30, self.connectors):
            if card == c["id"]:
                return {"title": c["name"], "rows": [["Status", "working" if c["ok"] else "not available"], ["Detail", c["detail"]]]}
        return {"title": card, "rows": [["", "nothing known about this"]]}


# ------------------------------------------------------------------ the web app
def make_app(host, history=None, guard=None, runner=None, stt=None):
    from flask import Flask, jsonify, request, send_file, make_response
    from . import capability
    try:
        from jarvis_agent.util import mask_secrets
    except Exception:
        mask_secrets = None
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    history, guard = history or History(), guard or Guard()
    try:
        from .telegram_bot import REMOTE_DENY
    except Exception:
        REMOTE_DENY = None
    runner = runner or Runner(host, history, mask=mask_secrets, deny=REMOTE_DENY)
    data, seen = Data(host), {"activity": time.time()}

    def pump_activity():
        """Self-extension steps also appear in the chat as SYSTEM lines (and so survive a refresh)."""
        new = [a for a in capability.recent_activity(60) if a["ts"] > seen["activity"]]
        for a in new:
            history.add("system", f"{a['state']}: {a['text']}")
        if new:
            seen["activity"] = new[-1]["ts"]

    runner.before_say = pump_activity

    def orb_state():
        acts = capability.recent_activity(1)
        if acts and time.time() - acts[-1]["ts"] < 6 and acts[-1]["state"] != "IDLE":
            return acts[-1]["state"]
        return "THINKING" if runner.busy else "IDLE"

    def authed():
        return guard.valid(request.cookies.get("jv"))

    @app.before_request
    def gate():
        if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/ping") and not authed():
            return jsonify(error="login"), 401

    @app.errorhandler(Exception)
    def failed(e):
        code = getattr(e, "code", 500)
        if code == 500 and callable(host.get("log_debug")):
            host["log_debug"]("dashboard/" + request.path, e)
        return jsonify(error=("too large" if code == 413 else "not found" if code == 404 else "something failed in the dashboard; it was logged")), code if isinstance(code, int) else 500

    @app.get("/")
    def index():
        r = make_response(send_file(UI))
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.get("/api/ping")
    def ping():
        return jsonify(ok=True, authed=authed())

    @app.post("/api/login")
    def login():
        ip = request.remote_addr or "?"
        if guard.locked(ip):
            return jsonify(error="Too many wrong passwords. Try again in five minutes."), 429
        tok = guard.login(ip, (request.get_json(silent=True) or {}).get("password"))
        if not tok:
            return jsonify(error="Wrong password."), 403
        r = jsonify(ok=True)
        r.set_cookie("jv", tok, max_age=SESSION_HOURS * 3600, httponly=True, samesite="Strict")
        return r

    @app.post("/api/logout")
    def logout():
        guard.logout(request.cookies.get("jv"))
        r = jsonify(ok=True)
        r.delete_cookie("jv")
        return r

    @app.get("/api/state")
    def state():
        pump_activity()
        return jsonify(online=True, busy=runner.busy, orb=orb_state(), time=time.time())

    @app.get("/api/history")
    def hist():
        pump_activity()
        return jsonify(messages=history.since(int(request.args.get("after", 0) or 0)), busy=runner.busy, orb=orb_state())

    @app.post("/api/chat")
    def chat():
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
        if not text:
            return jsonify(error="Type something first."), 400
        if not runner.submit(text):
            return jsonify(error="JARVIS is still working on the previous sentence."), 409
        return jsonify(ok=True)

    @app.post("/api/voice")
    def voice():
        f = request.files.get("audio")
        if not f:
            return jsonify(error="No recording arrived."), 400
        if not stt:
            return jsonify(error="Speech to text is not available on this PC (no Groq key)."), 503
        text = stt(f.read(), f.filename or "voice.webm")
        if not text:
            return jsonify(error="I could not understand the recording."), 422
        if not runner.submit(text, "spoken"):
            return jsonify(error="JARVIS is still working on the previous sentence.", heard=text), 409
        return jsonify(ok=True, heard=text)

    @app.get("/api/overview")
    def overview():
        return jsonify(system=data.system(), weather=data.cached("weather", 600, data.weather), phone=data.cached("phone", 45, data.phone),
                       connectors=data.cached("connectors", 30, data.connectors))

    @app.get("/api/notifications")
    def notifications():
        return jsonify(items=data.cached("notifications", 10, data.notifications))

    @app.get("/api/capabilities")
    def capabilities():
        return jsonify(items=[dict(e, name=n) for n, e in capability.registry().items()])

    @app.get("/api/activity")
    def activity():
        after = float(request.args.get("after", 0) or 0)
        return jsonify(items=[a for a in capability.recent_activity(60) if a["ts"] > after])

    @app.get("/api/detail/<card>")
    def detail(card):
        return jsonify(data.detail(re.sub(r"[^a-z_]", "", card.lower())[:30]))

    @app.post("/api/history/clear")
    def clear():
        history.clear()
        return jsonify(ok=True)

    app.jarvis = {"history": history, "guard": guard, "runner": runner, "data": data}
    return app


# ------------------------------------------------------------------ running it
def addresses():
    try:
        from .phone_link import local_ipv4
        ips = local_ipv4()
    except Exception:
        ips = []
    return [f"http://localhost:{PORT}"] + [f"http://{ip}:{PORT}" for ip in ips]


def dashboard_processes():
    import psutil
    try:
        mine = {os.getpid()} | {q.pid for q in psutil.Process().parents()}
    except Exception:
        mine = {os.getpid()}
    out = []
    for q in psutil.process_iter(["name", "cmdline"]):
        try:
            cl = " ".join(q.info["cmdline"] or [])
            if (q.info["name"] or "").lower().startswith("python") and "jarvis_addons.dashboard" in cl and "--run" in cl and q.pid not in mine:
                out.append(q)
        except Exception:
            pass
    return out


def start_background(root=ROOT):
    """-> 'started' | 'running' | 'switched off'"""
    import subprocess
    if os.path.exists(os.path.join(root, "jarvis_off.flag")):
        return "switched off"
    if dashboard_processes():
        return "running"
    pyw = os.path.join(root, "venv", "Scripts", "pythonw.exe")
    subprocess.Popen([pyw if os.path.isfile(pyw) else sys.executable, "-m", "jarvis_addons.dashboard", "--run"], cwd=root, close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "started"


def install(host):
    """The always-on JARVIS keeps the dashboard alive (never a terminal command). Config dashboard_autostart=off switches that off."""
    if host.get("_dashboard") or host.get("CLI_MODE") or "get_config" not in host:
        return False
    host["_dashboard"] = True

    def keeper():
        time.sleep(30)
        while True:
            try:
                if host["get_config"]("dashboard_autostart", "on") != "off" and start_background() == "started" and callable(host.get("file_log")):
                    host["file_log"](f"[DASHBOARD] started in the background: {addresses()[0]}")
            except Exception as e:
                host["log_debug"]("dashboard/keeper", e)
            time.sleep(60)
    threading.Thread(target=keeper, daemon=True, name="dashboard_keeper").start()
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    say = print if sys.stdout else (lambda *a: None)
    if "--set-password" in argv:
        pw = (argv[argv.index("--set-password") + 1:] or [""])[0]
        if len(pw) < 6:
            say("The password needs at least 6 characters.")
            return 2
        _cfg_set("dashboard_pw", hash_password(pw))
        if os.path.exists(PW_FILE):
            os.remove(PW_FILE)
        say("Password saved (only as a hash). Restart the dashboard if it is running.")
        return 0
    if "--status" in argv:
        say("Dashboard: " + ("running" if dashboard_processes() else "not running") + " | " + "  ".join(addresses()))
        return 0
    if "--start" in argv:
        say("Dashboard: " + start_background())
        return 0
    if "--run" in argv:
        if dashboard_processes():
            say("The dashboard is already running: " + addresses()[0])
            return 0
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        made = ensure_password()
        from ._host import load
        L = load()
        H = L.__dict__
        try:
            from .telegram_bot import make_stt
            stt = make_stt(H)
        except Exception:
            stt = None
        app = make_app(H, stt=stt)
        (H.get("file_log") or say)("[DASHBOARD] serving on " + ", ".join(addresses()) + (" - a first password was written to dashboard_password.txt" if made else ""))
        say("JARVIS dashboard: " + "  ".join(addresses()) + (f"\nFirst password: {made}  (also in dashboard_password.txt)" if made else ""))
        app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)
        return 0
    say(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
