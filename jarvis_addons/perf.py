"""Keeps JARVIS light on any laptop (add-on). Nothing JARVIS can DO changes - only WHEN the heavy background work runs.

Measured causes of the lag (2026-10-05) and what this file does about each:
  * the 4.7 GB local AI model was loaded at every start and then kept in RAM for 30 minutes after every use
        -> not loaded at start (it loads the first time it is needed) and unloaded after a few idle minutes
  * 'learning while you are away' ran the local model on the CPU every ~90 s, also on battery and while the PC was in use
        -> only when the PC is really unused, plugged in, and has CPU and RAM to spare
  * phone not reachable -> adb was started again every 3 s forever
        -> background retries back off (3 s ... 30/60 s); a command you give still tries at once
  * every start restarted the adb server for nothing (5 s) and started 11 background loops in the same second
        -> the useless restart is skipped; the loops start one after another over the first minute
  * auto-start together with Windows
        -> JARVIS waits until the desktop has been up for 45 s (low mode: 60 s)

  * laptop and JARVIS at the same time (the middle way)
        -> a 'pace' decided every 15 s on any laptop:  eco (on battery, or hot / under load for a minute), normal (you are working),
           full (plugged in and unused). A command you give is never slowed in any pace; only background work is.
        -> the local AI runs at low priority unless it is answering YOU; on a laptop with only a built-in GPU (Intel Arc / Iris Xe)
           it is moved to that GPU after a one-time speed test; without any usable GPU it gets half of the CPU cores

Modes: 'low' (RAM under 12 GB or 4 cores or fewer) and 'normal', chosen by itself at every start.
    venv\\Scripts\\python.exe -m jarvis_addons.perf --status        which mode and why, and what JARVIS uses right now
    venv\\Scripts\\python.exe -m jarvis_addons.perf --mode low      force a mode: low | normal | auto
    venv\\Scripts\\python.exe -m jarvis_addons.perf --gpu auto      test the built-in GPU for the local AI, keep the faster way: auto | off"""
import ctypes
import os
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "jarvis_memory.db")
LOW_RAM_GB, LOW_CORES = 12.0, 4
SETTINGS = {                 # mode -> numbers
    "normal": {"boot_wait": 45, "unload_after": 300, "adb_backoff_max": 30, "poll_extra_idle": 2.0, "poll_extra_active": 0.0, "learn_gap": 90, "below_normal": False},
    "low":    {"boot_wait": 60, "unload_after": 120, "adb_backoff_max": 60, "poll_extra_idle": 4.0, "poll_extra_active": 2.0, "learn_gap": 600, "below_normal": False},
}
STAGGER = {"_announcer_loop": 0, "_notification_loop": 3, "_call_loop": 6, "_adb_keeper_loop": 9, "_schedule_loop": 12, "_battery_loop": 20,
           "_birthday_loop": 30, "_email_poll_loop": 40, "_email_reminder_loop": 55, "_idle_learner_loop": 0}
POLLERS = ("_notification_loop", "_call_loop")
LEARN_USER_IDLE = 180        # seconds without keyboard/mouse before 'while you are away' counts as away
LEARN_MAX_CPU = 50           # percent of the whole PC
LEARN_MIN_FREE_GB = 3.0
HOT_C, COOL_C = 85, 78       # temperature: hot from HOT_C, normal again under COOL_C
BUSY_AVG, CALM_AVG = 75, 60  # whole-PC CPU averaged over the last minute: the same idea for laptops without a readable sensor
ECO_POLL = 4.0               # extra seconds between phone polls on battery / when hot
AI_PROCS = ("ollama.exe", "llama-server.exe", "ollama_llama_server.exe")
GPU_ENV = {"OLLAMA_VULKAN": "1", "OLLAMA_IGPU_ENABLE": "1"}          # what makes Ollama use a built-in GPU
GPU_MIN_RAM_GB = 12.0        # a built-in GPU borrows normal RAM; with less the model does not fit next to Windows
GPU_GAIN = 1.15              # the GPU is kept only when it is at least this much faster than the CPU
MDNS_BACKOFF_MAX = 60        # seconds between background mDNS searches while the phone cannot be found
MDNS_LINE = "[ADB] mDNS found no services"
MDNS_LOG_EVERY = 1800        # the same 'found nothing' line goes to the log at most this often


def _cfg(key, db=DB):
    try:
        con = sqlite3.connect(db)
        row = con.execute("SELECT val FROM config WHERE key=?", (key,)).fetchone()
        con.close()
        return row[0] if row and row[0] else None
    except sqlite3.Error:
        return None


def gpu_names():
    """Display adapters from the registry (instant; no PowerShell at start-up)."""
    out = []
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        for i in range(6):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{i:04d}") as k:
                    out.append(str(winreg.QueryValueEx(k, "DriverDesc")[0]))
            except OSError:
                continue
    except Exception:
        pass
    return out


def has_dedicated_gpu(names):
    import re
    return any(re.search(r"nvidia|geforce|rtx|gtx|quadro|radeon\s+(rx|pro)", n, re.I) for n in names)


def machine():
    import psutil
    names = gpu_names()
    return {"ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1), "cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "threads": psutil.cpu_count() or 1, "gpu": names, "dedicated_gpu": has_dedicated_gpu(names)}


def choose_mode(m, forced=None):
    """-> (mode, why)"""
    if forced in ("low", "normal"):
        return forced, "set by you"
    if m["ram_gb"] < LOW_RAM_GB:
        return "low", f"{m['ram_gb']} GB RAM (under {LOW_RAM_GB:.0f})"
    if m["cores"] <= LOW_CORES:
        return "low", f"{m['cores']} CPU cores"
    return "normal", f"{m['ram_gb']} GB RAM, {m['cores']} cores"


def user_idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:
        return 0.0


_TZ = {}


def temperature():
    """The hottest thermal zone Windows reports, in Celsius - or None when this laptop does not tell (then the load rule decides)."""
    try:
        import win32pdh
        if "q" not in _TZ:
            _TZ["q"] = win32pdh.OpenQuery()
            _TZ["c"] = win32pdh.AddEnglishCounter(_TZ["q"], r"\Thermal Zone Information(*)\Temperature")
        win32pdh.CollectQueryData(_TZ["q"])
        t = max(win32pdh.GetFormattedCounterArray(_TZ["c"], win32pdh.PDH_FMT_DOUBLE).values()) - 273.15
        return round(t, 1) if 5 < t < 130 else None
    except Exception:
        return None


def is_hot(was_hot, temp, avg_cpu):
    """Hot = the sensor says so, or the whole PC has been busy for a minute. It stays 'hot' until clearly cooler (no flapping)."""
    if (temp is not None and temp >= HOT_C) or avg_cpu >= BUSY_AVG:
        return True
    return bool(was_hot) and not ((temp is None or temp < COOL_C) and avg_cpu < CALM_AVG)


def choose_pace(on_battery, idle_s, hot):
    """-> (pace, why). Only BACKGROUND work follows the pace."""
    if hot:
        return "eco", "the laptop is hot or under heavy load"
    if on_battery:
        return "eco", "on battery"
    if idle_s >= LEARN_USER_IDLE:
        return "full", "plugged in and not in use"
    return "normal", "you are using the laptop"


def cpu_threads(m):
    """Threads for the local AI when it must run on the CPU: half of the real cores, so the laptop stays usable and cooler."""
    return max(1, min(m["cores"], max(2, m["cores"] // 2)))


def may_learn(idle_s, cpu, free_gb, on_battery, allow_battery=False, hot=False):
    """Is this a good moment for background study? -> (ok, why not)"""
    if hot:
        return False, "the laptop is hot or under heavy load"
    if on_battery and not allow_battery:
        return False, "on battery"
    if idle_s < LEARN_USER_IDLE:
        return False, "the PC is being used"
    if cpu >= LEARN_MAX_CPU:
        return False, "the CPU is busy"
    if free_gb < LEARN_MIN_FREE_GB:
        return False, "little free RAM"
    return True, ""


def boot_wait_left(now, desktop_started, wait):
    """Seconds to wait so that Windows finishes starting first (0 when the desktop has been up long enough)."""
    return max(0.0, min(wait, desktop_started + wait - now)) if desktop_started else 0.0


def _desktop_started():
    try:
        import psutil
        me = psutil.Process().username()
        times = [p.info["create_time"] for p in psutil.process_iter(["name", "create_time", "username"]) if (p.info["name"] or "").lower() == "explorer.exe" and p.info["username"] == me]
        return min(times) if times else None
    except Exception:
        return None


class Backoff:
    """Background reconnect attempts: 3 s, 6 s, 12 s ... up to `cap`. Any success resets it."""

    def __init__(self, cap):
        self.cap, self.fails, self.next_ts = cap, 0, 0.0

    def skip(self, now):
        return now < self.next_ts

    def result(self, ok, now):
        if ok:
            self.fails, self.next_ts = 0, 0.0
        else:
            self.fails += 1
            self.next_ts = now + min(self.cap, 3 * 2 ** min(self.fails, 6))


def install(host):
    need = ("get_config", "ADDON", "log_debug")
    if any(k not in host for k in need) or host.get("_perf"):
        return False
    m = machine()
    mode, why = choose_mode(m, host["get_config"]("perf_mode"))
    S = SETTINGS[mode]
    cli = bool(host.get("CLI_MODE"))
    host["_perf"] = {"mode": mode, "why": why, "machine": m, "last_local_ai": 0.0, "learn_blocked": "", "pace": "normal", "hot": False, "temp": None,
                     "fg_ai": 0, "ai_gpu": bool(m["dedicated_gpu"]) or host["get_config"]("perf_gpu") == "on"}
    log = host.get("file_log") or (lambda s: None)
    names = {t for t in STAGGER}

    def background():
        return threading.current_thread().name in names

    # 1. auto-start with Windows: let the desktop finish first (only the real assistant, never a terminal command)
    if not cli:
        left = boot_wait_left(time.time(), _desktop_started(), S["boot_wait"])
        if left > 1:
            log(f"[PERF] Windows has just started: waiting {left:.0f}s before JARVIS starts")
            time.sleep(left)
        if S["below_normal"]:
            try:
                import psutil
                psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)          # the laptop's own apps come first
            except Exception as e:
                host["log_debug"]("perf/priority", e)

    # 2. the local AI model is not loaded at start any more: it loads the first time something really asks for it
    if "_ollama_warm" in host:
        host["_ollama_warm"] = _named(lambda: None, "_ollama_warm")

    # 3. the useless adb-server restart at every start (it looks for a backend name this adb never prints)
    if isinstance(host.get("_DISC"), dict):
        host["_DISC"]["switched"] = True

    # 4. background loops start one after another instead of all in the same second
    for name, delay in STAGGER.items():
        fn = host.get(name)
        if callable(fn) and delay and not getattr(fn, "_perf", False):
            host[name] = _delayed(fn, delay, name)

    # 5. phone unreachable: background retries back off; a command the owner gives is never delayed
    if callable(host.get("ensure_adb_connected")):
        orig_ensure, back = host["ensure_adb_connected"], Backoff(S["adb_backoff_max"])

        def ensure_adb_connected(*a, **k):
            if not background():
                ok = orig_ensure(*a, **k)
                back.result(ok, time.time())
                return ok
            if back.skip(time.time()):
                return False
            ok = orig_ensure(*a, **k)
            back.result(ok, time.time())
            return ok
        host["ensure_adb_connected"] = ensure_adb_connected

    # 6. the phone is asked for its notifications a little less often when nothing is going on
    if callable(host.get("_adb_dump")):
        orig_dump = host["_adb_dump"]

        def _adb_dump(*a, **k):
            if threading.current_thread().name in POLLERS:
                A = host["ADDON"]
                active = time.time() - max(A.get("last_activity", 0) or 0, A.get("msg_ts", 0) or 0) < 120
                extra, pace = (S["poll_extra_active"] if active else S["poll_extra_idle"]), host["_perf"].get("pace")
                extra = 0 if pace == "full" else max(extra, ECO_POLL) if pace == "eco" else extra
                if extra:
                    time.sleep(extra)
            return orig_dump(*a, **k)
        host["_adb_dump"] = _adb_dump

    # 7. remember when the local model was last used, so it can be put away afterwards
    if callable(host.get("_ollama_text")):
        orig_ollama, ai_lock = host["_ollama_text"], threading.Lock()

        def _ollama_text(*a, **k):
            P, real = host["_perf"], "OLLAMA_URL" in host
            mine = real and not (k.get("background") or background())                 # an answer somebody is waiting for
            if real:
                with ai_lock:
                    P["fg_ai"] += mine
                set_ai_priority(P["fg_ai"] > 0 or P["pace"] == "full")
            try:
                return orig_ollama(*a, **k)
            finally:
                P["last_local_ai"] = time.time()
                if mine:
                    with ai_lock:
                        P["fg_ai"] -= 1
                    if not P["fg_ai"] and P["pace"] != "full":
                        set_ai_priority(False)
        host["_ollama_text"] = _ollama_text

    # 7b. no usable GPU: the local AI gets half of the cores (one fixed number, so the model is never reloaded because of it)
    if callable(host.get("_post_json")) and host.get("OLLAMA_URL"):
        orig_post, n_thr = host["_post_json"], cpu_threads(m)

        def _post_json(url, data, *a, **k):
            if (not host["_perf"]["ai_gpu"] and isinstance(data, dict) and isinstance(data.get("options"), dict)
                    and str(url).startswith(str(host["OLLAMA_URL"])) and "num_thread" not in data["options"]):
                data = dict(data, options=dict(data["options"], num_thread=n_thr))
            return orig_post(url, data, *a, **k)
        host["_post_json"] = _post_json

    # 8. phone not found: the mDNS search backs off as well (up to 60 s; a command is never delayed) ...
    if callable(host.get("adb_discover")):
        orig_disc, dback = host["adb_discover"], Backoff(MDNS_BACKOFF_MAX)

        def adb_discover(*a, **k):
            if background() and dback.skip(time.time()):
                return False
            ok = orig_disc(*a, **k)
            dback.result(ok, time.time())
            return ok
        host["adb_discover"] = adb_discover

    # ... and its 'found nothing' line is written once, then only every half hour with a count, instead of filling the log
    if callable(host.get("file_log")):
        orig_log, seen = host["file_log"], {"ts": 0.0, "n": 0}

        def file_log(msg, *a, **k):
            if isinstance(msg, str) and msg.startswith(MDNS_LINE):
                now = time.time()
                if now - seen["ts"] < MDNS_LOG_EVERY:
                    seen["n"] += 1
                    return None
                if seen["n"]:
                    msg += f" [{seen['n']} more times since the last such line]"
                seen.update(ts=now, n=0)
            return orig_log(msg, *a, **k)
        host["file_log"] = file_log

    if not cli:
        threading.Thread(target=_governor, args=(host, S), daemon=True, name="perf_governor").start()
    log(f"[PERF] mode {mode} ({why}); GPU: {', '.join(m['gpu']) or 'unknown'}; local AI on " + ("the GPU" if host["_perf"]["ai_gpu"] else f"the CPU, {cpu_threads(m)} threads"))
    return True


def _named(fn, name):
    fn.__name__ = name
    fn._perf = True
    return fn


def _delayed(fn, delay, name):
    def starter(*a, **k):
        time.sleep(delay)
        return fn(*a, **k)
    return _named(starter, name)


KEEP_ALIVE = 1800            # JARVIS asks Ollama to keep a model for 30 minutes after each use


def idle_seconds_of(expires_at, now):
    """How long ago a loaded model was last used, from Ollama's own 'expires_at' (= last use + 30 minutes). None if unreadable."""
    import datetime
    import re
    try:
        s = re.sub(r"(\.\d{6})\d+", r"\1", str(expires_at).replace("Z", "+00:00"))
        return now - (datetime.datetime.fromisoformat(s).timestamp() - KEEP_ALIVE)
    except Exception:
        return None


def unload_local_models(host, older_than=0, now=None):
    """Asks Ollama to free the RAM of loaded models that have not been used for `older_than` seconds - whichever JARVIS
    process used them (the assistant, the Telegram bot, a terminal command). They load again by themselves when needed.
    -> names unloaded"""
    done = []
    try:
        url, now = host["OLLAMA_URL"], (now or time.time())
        for mdl in (host["_http_json"](url + "/api/ps", timeout=2).get("models") or []):
            name = mdl.get("name") or mdl.get("model")
            idle = idle_seconds_of(mdl.get("expires_at"), now) if older_than else older_than
            if name and idle is not None and idle >= older_than:
                host["_post_json"](url + "/api/generate", {"model": name, "keep_alive": 0}, timeout=10)
                done.append(name)
    except Exception:
        pass
    return done


def _ollama_busy():
    """Is Ollama computing right now (any of its processes using real CPU)?"""
    try:
        import psutil
        ps = _ai_processes()
        for p in ps:
            p.cpu_percent(None)
        time.sleep(0.5)
        return sum(p.cpu_percent(None) for p in ps) > 20
    except Exception:
        return False


def _ai_processes():
    import psutil
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in AI_PROCS]


def set_ai_priority(foreground):
    """The local AI works at normal priority only while it answers the owner (or the laptop is free); otherwise below the laptop's apps."""
    try:
        import psutil
        want = psutil.NORMAL_PRIORITY_CLASS if foreground else psutil.BELOW_NORMAL_PRIORITY_CLASS
        for p in _ai_processes():
            try:
                if p.nice() != want:
                    p.nice(want)
            except psutil.Error:
                pass
    except Exception:
        pass


# ------------------------------------------------------------------ the built-in GPU for the local AI
def _save(key, val, db=None):
    con = sqlite3.connect(db or DB)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    if val is None:
        con.execute("DELETE FROM config WHERE key=?", (key,))
    else:
        con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (key, str(val)))
    con.commit()
    con.close()


def _ollama_env(on):
    """Ollama reads these when it starts. Saved for this Windows user (so they hold after a restart) and for this process."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        for name, val in GPU_ENV.items():
            if on:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
                os.environ[name] = val
            else:
                os.environ.pop(name, None)
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 2000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass


def _api(url, path, data=None, timeout=240):
    import json
    import urllib.request
    req = urllib.request.Request(url + path, json.dumps(data).encode() if data is not None else None, {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def ai_bench(url, model):
    """One short answer -> (tokens per second, GB of the model on the GPU), or None when the local AI did not answer."""
    try:
        opts = {"num_predict": 48, "temperature": 0.2, "num_ctx": 4096}
        _api(url, "/api/generate", {"model": model, "prompt": "hi", "stream": False, "options": dict(opts, num_predict=1)})
        vram = sum(x.get("size_vram", 0) for x in _api(url, "/api/ps", timeout=5).get("models") or []) / 2 ** 30
        r = _api(url, "/api/generate", {"model": model, "prompt": "Count from one to thirty in words.", "stream": False, "options": opts})
        return r["eval_count"] / (r["eval_duration"] / 1e9), vram
    except Exception:
        return None


def restart_ollama(url, wait=40):
    """Stops Ollama and starts it the way it was running (tray app or plain server), with this process's environment. -> is it answering?"""
    import psutil
    import subprocess
    procs = [p for p in psutil.process_iter(["name", "exe"]) if (p.info["name"] or "").lower() in AI_PROCS + ("ollama app.exe",)]
    app = next((p.info["exe"] for p in procs if (p.info["name"] or "").lower() == "ollama app.exe" and p.info["exe"]), None)
    srv = next((p.info["exe"] for p in procs if (p.info["name"] or "").lower() == "ollama.exe" and p.info["exe"]), None)
    if not (app or srv):
        return False
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=8)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([app] if app else [srv, "serve"], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + wait
    while time.time() < end:
        try:
            _api(url, "/api/version", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def gpu_decision(cpu_run, gpu_run):
    """-> ('on' | 'cpu', why). Runs are (tokens/s, GB on GPU) or None."""
    if not gpu_run or gpu_run[1] <= 0.1:
        return "cpu", "the built-in GPU could not take the model"
    if cpu_run and gpu_run[0] < cpu_run[0] * GPU_GAIN:
        return "cpu", f"the GPU was not faster ({gpu_run[0]:.1f} against {cpu_run[0]:.1f} tokens/s)"
    return "on", f"{gpu_run[0]:.1f} tokens/s on the GPU" + (f" against {cpu_run[0]:.1f} on the CPU" if cpu_run else "")


def gpu_setup(want="auto", say=print, url="http://127.0.0.1:11434", db=None):
    """Decides once, by measuring, where this laptop's local AI runs. -> 'on' | 'cpu' | 'dedicated' | 'off' | None (could not test)"""
    m = machine()
    if want == "off":
        _ollama_env(False)
        restart_ollama(url)
        _save("perf_gpu", "off", db)
        say("local AI: built-in GPU switched off, it runs on the CPU.")
        return "off"
    if m["dedicated_gpu"]:
        say("local AI: this PC has a dedicated GPU; Ollama uses it by itself, nothing to set.")
        return "dedicated"
    if not m["gpu"] or m["ram_gb"] < GPU_MIN_RAM_GB:
        _save("perf_gpu", "cpu", db)
        say(f"local AI: stays on the CPU with {cpu_threads(m)} threads (no built-in GPU with enough memory: {m['ram_gb']} GB RAM).")
        return "cpu"
    try:
        model = _cfg("ollama_model", db or DB) or (_api(url, "/api/tags", timeout=5).get("models") or [{}])[0].get("name")
    except Exception:
        model = None
    if not model:
        say("local AI: Ollama is not running or has no model, so the GPU could not be tested.")
        return None
    cpu_run = ai_bench(url, model)
    if cpu_run and cpu_run[1] > 0.1:
        _save("perf_gpu", "on", db)
        say(f"local AI: already on the GPU ({cpu_run[0]:.1f} tokens/s).")
        return "on"
    _ollama_env(True)
    gpu_run = ai_bench(url, model) if restart_ollama(url) else None
    res, why = gpu_decision(cpu_run, gpu_run)
    if res != "on":
        _ollama_env(False)
        restart_ollama(url)
    else:
        try:
            _api(url, "/api/generate", {"model": model, "keep_alive": 0}, timeout=20)
        except Exception:
            pass
    _save("perf_gpu", res, db)
    say(f"local AI: {'moved to the built-in GPU' if res == 'on' else 'stays on the CPU'} - {why}.")
    return res


def _governor(host, S):
    """One light check every 15 s: holds back background study when the PC is busy / on battery, and frees the local model's RAM."""
    import psutil
    psutil.cpu_percent(None)
    last_session, load = 0.0, []
    while True:
        time.sleep(15)
        try:
            P, A, now = host["_perf"], host["ADDON"], time.time()
            batt = psutil.sensors_battery()
            cpu, idle_s, on_batt = psutil.cpu_percent(None), user_idle_seconds(), bool(batt and not batt.power_plugged)
            load = (load + [cpu])[-4:]                                 # the last minute
            P["temp"] = temperature()
            P["hot"] = is_hot(P["hot"], P["temp"], sum(load) / 4.0)
            pace, pwhy = choose_pace(on_batt, idle_s, P["hot"])
            if pace != P["pace"]:
                P["pace"] = pace
                (host.get("file_log") or (lambda s: None))(f"[PERF] pace {pace}: {pwhy}" + (f" ({P['temp']:.0f} C, CPU {cpu:.0f}%)" if P["temp"] else f" (CPU {cpu:.0f}%)"))
                if "OLLAMA_URL" in host:
                    set_ai_priority(P["fg_ai"] > 0 or pace == "full")
            ok, why = may_learn(idle_s, cpu, psutil.virtual_memory().available / 2 ** 30,
                                on_batt, host["get_config"]("perf_learn_on_battery") == "on", hot=P["hot"])
            seen = A.get("last_idle_learn", 0) or 0
            if seen > last_session and seen <= now - 1 and P.get("_ours") != seen:
                last_session = seen                                    # a real study session started at `seen`
            if ok and now - last_session < S["learn_gap"]:
                ok, why = False, "paced"
            if not ok:
                A["last_idle_learn"] = P["_ours"] = now                # JARVIS's own rule ("not within 90 s of the last one") then skips this turn
            if why != P["learn_blocked"]:
                P["learn_blocked"] = why
                (host.get("file_log") or (lambda s: None))("[PERF] background study " + (f"paused: {why}" if why else "allowed"))
            lock = host.get("_OLLAMA_LOCK")
            ticks = P["ticks"] = P.get("ticks", 0) + 1
            if pace == "full" and ticks % 8 == 0 and not P.get("gpu_tried") and lock and not lock.locked():
                P["gpu_tried"] = True                                  # once: is the built-in GPU the better place for the local AI?
                if _cfg("perf_gpu") is None and not P["machine"]["dedicated_gpu"] and host.get("ollama_up", lambda: False)():
                    with lock:
                        res = gpu_setup("auto", say=lambda s: (host.get("file_log") or (lambda x: None))("[PERF] " + s))
                    P["ai_gpu"] = res == "on"
            if ticks % 4 == 0 and not (lock and lock.locked()) and now - P["last_local_ai"] > S["unload_after"] and not _ollama_busy():
                freed = unload_local_models(host, older_than=S["unload_after"])          # once a minute
                if freed:
                    (host.get("file_log") or (lambda s: None))(f"[PERF] local AI model put away after {S['unload_after']}s unused: {', '.join(freed)}")
        except Exception as e:
            host["log_debug"]("perf/governor", e)


def usage(seconds=6):
    """CPU (percent of the whole PC) and RAM of everything JARVIS runs right now."""
    import psutil
    groups = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            n, cl = (p.info["name"] or "").lower(), " ".join(p.info["cmdline"] or [])
            tag = ("JARVIS" if "live_cmd_console.py" in cl and n.startswith("python") else "Telegram bot" if "telegram_bot" in cl and n.startswith("python")
                   else "local AI (Ollama)" if n in AI_PROCS else "adb (phone link)" if n == "adb.exe" else None)
            if tag:
                p.cpu_percent(None)
                groups.setdefault(tag, []).append(p)
        except Exception:
            pass
    time.sleep(seconds)
    rows = []
    for tag, ps in groups.items():
        cpu = ram = 0.0
        for p in ps:
            try:
                cpu += p.cpu_percent(None) / (psutil.cpu_count() or 1)
                ram += p.memory_info().rss / 2 ** 20
            except Exception:
                pass
        rows.append((tag, cpu, ram))
    return rows


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--mode" in argv:
        val = (argv[argv.index("--mode") + 1:] or [""])[0].lower()
        if val not in ("low", "normal", "auto"):
            print("Use: --mode low | normal | auto")
            return 2
        con = sqlite3.connect(DB)
        con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
        if val == "auto":
            con.execute("DELETE FROM config WHERE key='perf_mode'")
        else:
            con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('perf_mode', ?)", (val,))
        con.commit()
        con.close()
        print(f"Performance mode: {val}. Restart JARVIS to apply.")
        return 0
    if "--gpu" in argv:
        val = (argv[argv.index("--gpu") + 1:] or [""])[0].lower()
        if val not in ("auto", "off"):
            print("Use: --gpu auto | off")
            return 2
        print("Testing the local AI (this loads the model, about a minute) ...")
        return 0 if gpu_setup(val) else 1
    m = machine()
    mode, why = choose_mode(m, _cfg("perf_mode"))
    print(f"This PC   : {m['ram_gb']} GB RAM, {m['cores']} cores / {m['threads']} threads, GPU: {', '.join(m['gpu']) or 'unknown'}" + ("" if m["dedicated_gpu"] else " (no dedicated GPU)"))
    print(f"Mode      : {mode}  ({why})")
    S = SETTINGS[mode]
    print(f"            waits {S['boot_wait']}s after Windows starts | local AI put away after {S['unload_after']}s unused | background study at most every {S['learn_gap']}s")
    import psutil
    batt, t = psutil.sensors_battery(), temperature()
    on_batt = bool(batt and not batt.power_plugged)
    gpu = "the GPU (by itself)" if m["dedicated_gpu"] else {"on": "the built-in GPU", "off": "the CPU (GPU switched off by you)"}.get(_cfg("perf_gpu") or "", f"the CPU, {cpu_threads(m)} threads")
    print(f"Local AI  : runs on {gpu}; low priority unless it is answering you")
    print(f"Right now : {'on battery' if on_batt else 'plugged in'}, temperature {f'{t:.0f} C' if t else 'not readable (load is used instead)'}, "
          f"pace would be '{choose_pace(on_batt, user_idle_seconds(), is_hot(False, t, psutil.cpu_percent(0.5)))[0]}'")
    print("Measuring for 6 seconds ...")
    rows = usage()
    if not rows:
        print("JARVIS is not running right now.")
    for tag, cpu, ram in rows:
        print(f"  {tag:18} CPU {cpu:5.1f}%   RAM {ram:6.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
