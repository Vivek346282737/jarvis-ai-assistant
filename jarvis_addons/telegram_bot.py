"""Control JARVIS from Telegram (add-on). The owner writes to their own bot from anywhere; JARVIS runs the sentence exactly
as if it had been spoken and sends back what it would have said. Nothing here starts the voice loop.

    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --setup     save the bot token and learn who the owner is
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --status    what is saved, is the bot reachable
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --send "hi" send one message to the owner (alert test)
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --once      answer the messages waiting now, then stop
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --run       keep answering until Ctrl+C
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --start     start the bot in the background now (JARVIS does this by itself)

Always on: the JARVIS that Windows starts (the "JARVIS Assistant" task) starts this bot as its own background process and
starts it again if it ever stops. "JARVIS Band" / exam mode stops both; "JARVIS Chalu" brings both back.
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --set-username NAME   your Telegram username, for calls
    venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --call "text"         JARVIS calls you now and says it

Voice messages: the audio is turned into text by Groq (so it is sent to Groq), the answer comes back as text AND as a
voice message in JARVIS's own voice. "call me" / "call kar" makes JARVIS ring you through CallMeBot (see callmebot.py).

Safety: only ONE Telegram account (the owner's numeric id, learned in --setup) is ever obeyed; everybody else is ignored.
A message older than a few minutes is not executed (so commands that piled up while the laptop was off do not all fire).
A short list of destructive requests is refused from Telegram. The bot token is a secret: it lives in this folder's
database and is masked wherever text is printed or sent."""
import json
import os
import http.client
import re
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "jarvis_memory.db")
API_BASE = "https://api.telegram.org"
MAX_AGE = 300                       # seconds: an older message is answered with "too old", never executed
POLL = 25                           # long-poll seconds
MAX_LEN = 3900                      # Telegram's limit for one message is 4096
MAX_VOICE_SECONDS = 120             # a longer voice message is refused, not half-heard
MAX_VOICE_BYTES = 8_000_000
MAX_SPOKEN_REPLY = 700              # characters turned into a voice answer (the full text always goes out as text)
CALL_ALERT_EVERY = 900              # seconds: JARVIS places at most one alert CALL in this time (texts are not limited)
STT_MODEL = "whisper-large-v3-turbo"
CALL_ME = re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:call\s+me(?:\s+now)?|give\s+me\s+a\s+call|mujhe\s+call\s+kar\w*(?:\s+\w+)?|call\s+kar(?:o|\s+do|\s+de)?|phone\s+kar(?:o|\s+do)?)[.!\s]*$", re.I)
_DEV_CALL = (("जार्विस", "jarvis"), ("मुझे", "mujhe"), ("कॉल", "call"), ("काल", "call"), ("करो", "karo"), ("कर", "kar"), ("दो", "do"), ("मी", "me"), ("फोन", "phone"))
REMOTE_DENY = re.compile(r"\b(shut\s*down|power\s*off|restart|reboot|format|factory\s*reset|wipe|delete\s+(all|every)|erase)\b", re.I)


class TgError(Exception):
    pass


# ---- reaching Telegram over IPv4 --------------------------------------------------------------------------------------
# Measured on the owner's connection (2026-10-02): over IPv6 half of the requests to api.telegram.org were reset
# ("WinError 10054") or timed out, over IPv4 none were - and Windows/Python try IPv6 first. So Telegram is always reached
# over IPv4, and a connection that breaks before an answer arrived is tried again.
RETRIES = {"sendMessage": 1}        # extra attempts; everything else (reads, long poll) gets 2
RETRY_DEFAULT = 2


def _ipv4_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kw):
    host, port = address
    try:
        host = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        pass                                        # no IPv4 address known: let the normal lookup decide
    return socket.create_connection((host, port), timeout, source_address)


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _ipv4_connection          # the TLS server name stays the real host name


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req, context=ssl.create_default_context())


_OPENER = urllib.request.build_opener(_IPv4HTTPSHandler())
_BROKEN = (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError, http.client.RemoteDisconnected, http.client.IncompleteRead)


def _is_broken_connection(exc):
    """A connection that died (reset, timeout) - worth another try. A real answer from Telegram (HTTP error) is not."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    inner = getattr(exc, "reason", None)
    return isinstance(exc, _BROKEN) or isinstance(inner, _BROKEN) or isinstance(exc, urllib.error.URLError)


def cfg_get(key, db=DB):
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT val FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[0] not in (None, "") else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def cfg_set(key, val, db=DB):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    con.execute("INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)", (key, str(val)))
    con.commit()
    con.close()


class Api:
    def __init__(self, token, base=None):
        self.token, self.base = token, (base or API_BASE).rstrip("/")

    def mask(self, text):
        return str(text).replace(self.token, "***") if self.token else str(text)

    def call(self, method, http_timeout=20, **params):
        """-> the 'result' of a Bot API method. Raises TgError with a message that never contains the token."""
        body = json.dumps(params).encode("utf-8")
        attempts = 1 + RETRIES.get(method, RETRY_DEFAULT)
        data = None
        for attempt in range(attempts):
            req = urllib.request.Request(f"{self.base}/bot{self.token}/{method}", data=body, headers={"Content-Type": "application/json"})
            try:
                with _OPENER.open(req, timeout=http_timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as e:
                try:
                    detail = json.loads(e.read().decode("utf-8", "replace")).get("description", "")
                except Exception:
                    detail = ""
                raise TgError(self.mask(f"{method}: HTTP {e.code} {detail}".strip()))
            except Exception as e:
                if attempt + 1 < attempts and _is_broken_connection(e):
                    time.sleep(0.6 * (attempt + 1))
                    continue
                raise TgError(self.mask(f"{method}: {type(e).__name__}: {e}"))
        if not data.get("ok"):
            raise TgError(self.mask(f"{method}: {data.get('description', 'not ok')}"))
        return data.get("result")

    def send(self, chat_id, text):
        text = self.mask(text).strip()
        sent = 0
        while text:
            part, text = text[:MAX_LEN], text[MAX_LEN:]
            self.call("sendMessage", chat_id=chat_id, text=part)
            sent += 1
        return sent

    def download(self, file_id, max_bytes=MAX_VOICE_BYTES):
        """A file the owner sent (a voice message). -> (bytes, file name). Raises TgError."""
        info = self.call("getFile", file_id=file_id)
        path = info.get("file_path") or ""
        if int(info.get("file_size") or 0) > max_bytes:
            raise TgError("getFile: that file is too big")
        try:
            with _OPENER.open(urllib.request.Request(f"{self.base}/file/bot{self.token}/{path}"), timeout=40) as r:
                data = r.read(max_bytes + 1)
        except Exception as e:
            raise TgError(self.mask(f"download: {type(e).__name__}: {e}"))
        if len(data) > max_bytes:
            raise TgError("download: that file is too big")
        return data, os.path.basename(path) or "voice.ogg"

    def send_voice(self, chat_id, audio, filename="jarvis.mp3"):
        """Sends audio as a playable voice message (Telegram accepts OGG/Opus, MP3 and M4A here). Raises TgError."""
        boundary = "----jarvis" + uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"{filename}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode(),
            audio, f"\r\n--{boundary}--\r\n".encode()])
        req = urllib.request.Request(f"{self.base}/bot{self.token}/sendVoice", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with _OPENER.open(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("description", "")
            except Exception:
                detail = ""
            raise TgError(self.mask(f"sendVoice: HTTP {e.code} {detail}".strip()))
        except Exception as e:
            raise TgError(self.mask(f"sendVoice: {type(e).__name__}: {e}"))
        if not data.get("ok"):
            raise TgError(self.mask(f"sendVoice: {data.get('description', 'not ok')}"))
        return True


def normalize(text):
    """Lower case, punctuation dropped, and the few Devanagari words speech-to-text writes for 'call me' turned into Roman."""
    t = str(text or "").lower()
    for dev, rom in _DEV_CALL:
        t = t.replace(dev, rom)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def clean_spoken(text):
    return str(text or "").replace("*", "").replace("#", "").replace("`", "").strip()


class Bridge:
    """Runs owner messages through JARVIS and turns JARVIS's speech into Telegram replies."""

    def __init__(self, host, api, owner_id, mask=None, log=None, stt=None, tts=None, caller=None):
        """stt(audio bytes, file name) -> text or None; tts(text) -> mp3 bytes or None; caller(text) -> (ok, detail)."""
        self.host, self.api, self.owner_id = host, api, int(owner_id)
        self.mask = mask or (lambda s: s)
        self.log = log or host.get("file_log") or (lambda s: None)
        self.stt, self.tts, self.caller = stt, tts, caller
        self.lock = threading.Lock()
        self.replies = 0
        self.ignored = set()
        self._orig = {}
        self._voice_buffer = None                   # a list while the answer to a VOICE message is being collected

    # -- speech -> Telegram
    def say(self, text):
        text = clean_spoken(text)
        if not text:
            return
        if self._voice_buffer is not None:
            self._voice_buffer.append(text)
        try:
            self.api.send(self.owner_id, self.mask(text))
            self.replies += 1
        except TgError as e:
            self.log(f"[TELEGRAM] could not send a reply: {e}")

    def capture_speech(self):
        """In this (bot) process everything JARVIS would say goes to Telegram instead of the laptop speakers."""
        for name in ("speak", "speak_urgent"):
            if name in self.host and name not in self._orig:
                self._orig[name] = self.host[name]
                self.host[name] = self.say

    def release_speech(self):
        for name, fn in self._orig.items():
            self.host[name] = fn
        self._orig.clear()

    # -- Telegram -> JARVIS
    def handle(self, update, now=None):
        """-> what happened: 'ran' | 'denied' | 'old' | 'ignored' | 'unsupported' | 'empty'"""
        msg = update.get("message") or {}
        sender = (msg.get("from") or {}).get("id")
        if sender != self.owner_id or (msg.get("chat") or {}).get("type") not in (None, "private"):
            if sender not in self.ignored:
                self.ignored.add(sender)
                self.log("[TELEGRAM] a message from someone who is not the owner was ignored")
            return "ignored"
        if (now or time.time()) - int(msg.get("date") or 0) > MAX_AGE:
            self.say("That message is too old, so I did not run it. Send it again if you still want it.")
            return "old"
        text = (msg.get("text") or "").strip()
        spoken = msg.get("voice") or msg.get("audio")
        if not text and spoken:
            text = self._hear(spoken)
            if text is None:
                return "unsupported"
        elif not text:
            self.say("Send me text or a voice message.")
            return "unsupported"
        if text.startswith("/"):
            self.say("Hello. Just write what you would say to me, for example: what time is it" if text.split()[0] in ("/start", "/help") else "I do not use slash commands - just write the sentence.")
            return "empty"
        if REMOTE_DENY.search(text):
            self.say("I do not do that from Telegram. Say it to me at the laptop.")
            self.log("[TELEGRAM] a destructive request was refused")
            return "denied"
        with self.lock:
            before = self.replies
            self._voice_buffer = [] if spoken else None
            try:
                if CALL_ME.search(normalize(text)):
                    self._call_me()
                else:
                    self.host["route_and_execute"](text, self.host["CURRENT_ACTIVE_USER"])
                if self.replies == before:
                    self.say("Done.")
            except Exception as e:
                if self.host.get("log_debug"):
                    self.host["log_debug"]("telegram_bot/command", e)
                self.say("Something went wrong with that one. I logged it.")
            finally:
                answer, self._voice_buffer = self._voice_buffer, None
            if answer:
                self._answer_aloud(" ".join(answer))
        return "ran"

    def _hear(self, spoken):
        """A voice message -> the sentence in it (also shown back to the owner), or None when it could not be used."""
        if not self.stt:
            self.say("I cannot listen to voice messages on this PC yet. Please write it.")
            return None
        if int(spoken.get("duration") or 0) > MAX_VOICE_SECONDS:
            self.say(f"That voice message is longer than {MAX_VOICE_SECONDS} seconds. Please keep it short.")
            return None
        try:
            audio, name = self.api.download(spoken["file_id"])
            heard = (self.stt(audio, name) or "").strip()
        except Exception as e:
            self.log(f"[TELEGRAM] voice message could not be heard: {self.api.mask(e)}")
            heard = ""
        if not heard:
            self.say("I could not make out that voice message. Please say it again or write it.")
            return None
        try:
            self.api.send(self.owner_id, "🎙 " + self.mask(heard))          # what was understood: a wrong hearing is visible at once
        except TgError:
            pass
        return heard

    def _answer_aloud(self, text):
        """The answer to a voice message is ALSO sent as a voice message (the text already went out)."""
        if not self.tts:
            return
        try:
            audio = self.tts(self.mask(text)[:MAX_SPOKEN_REPLY])
            if audio:
                self.api.send_voice(self.owner_id, audio)
        except Exception as e:
            self.log(f"[TELEGRAM] the spoken answer could not be sent: {self.api.mask(e)}")

    def _call_me(self):
        if not self.caller:
            self.say("Calling is not set up yet. Save your Telegram username first.")
            return
        self.say("Calling you now.")
        ok, detail = self.caller("This is JARVIS. You asked me to call you. Send me a voice message in Telegram to talk.")
        if not ok:
            self.say(f"The call did not go through: {detail}")


def poll_loop(bridge, once=False, db=DB, poll=POLL, stop=None, on_error=None):
    """Long-polls for updates and handles them one by one. Never raises for a network problem; `once` stops when nothing waits."""
    offset = int(cfg_get("telegram_offset", db) or 0)
    handled, backoff = 0, 2
    while not (stop and stop.is_set()):
        try:
            updates = bridge.api.call("getUpdates", http_timeout=(8 if once else poll + 10), offset=offset, timeout=(0 if once else poll))
            backoff = 2
        except TgError as e:
            if on_error:
                on_error(e)
            if once:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        for u in updates or []:
            offset = int(u["update_id"]) + 1
            cfg_set("telegram_offset", offset, db)                             # saved BEFORE running: a crash never re-runs a command
            bridge.handle(u)
            handled += 1
        if once and not updates:
            break
    return handled


def setup(db=DB, token=None, owner_id=None, base=None, ask=input, say=print, wait=90):
    """Saves the token, then learns the owner's id from the first message sent to the bot. -> True when ready."""
    if token is None:
        import getpass
        try:
            token = getpass.getpass("Paste the bot token from @BotFather (hidden): ").strip()
        except (KeyboardInterrupt, EOFError, OSError):
            token = ask("Paste the bot token from @BotFather: ").strip()
    if not re.fullmatch(r"\d{6,}:[\w-]{30,}", token or ""):
        say("That does not look like a bot token (it looks like 123456789:AA...). Nothing was saved.")
        return False
    api = Api(token, base)
    try:
        me = api.call("getMe")
    except TgError as e:
        say(f"Telegram did not accept that token: {e}")
        return False
    say(f"Bot found: @{me.get('username')} ({me.get('first_name')})")
    if owner_id is None:
        say(f"Now open Telegram, find @{me.get('username')}, press START and send it any message. Waiting up to {wait} seconds ...")
        t0, offset, seen = time.time(), 0, None
        while time.time() - t0 < wait and seen is None:
            try:
                for u in api.call("getUpdates", http_timeout=20, offset=offset, timeout=10) or []:
                    offset = int(u["update_id"]) + 1
                    m = u.get("message") or {}
                    if (m.get("chat") or {}).get("type") == "private" and m.get("from"):
                        seen = m["from"]
            except TgError as e:
                say(f"  (waiting: {e})")
                time.sleep(3)
        if seen is None:
            say("No message arrived. Run --setup again after sending the bot a message.")
            return False
        who = " ".join(x for x in (seen.get("first_name"), seen.get("last_name")) if x) + (f" (@{seen['username']})" if seen.get("username") else "")
        if ask(f"A message came from: {who}. Is that YOU? Only this account will be obeyed. [y/N]: ").strip().lower() != "y":
            say("Nothing was saved.")
            return False
        owner_id = seen["id"]
        cfg_set("telegram_offset", offset, db)
    cfg_set("telegram_token", token, db)
    cfg_set("telegram_owner_id", int(owner_id), db)
    if base:
        cfg_set("telegram_api", base, db)
    try:
        api.send(int(owner_id), "JARVIS is linked to this chat. Write to me here from anywhere.")
    except TgError as e:
        say(f"Saved, but the first message could not be sent: {e}")
    say("Saved. Only your Telegram account is obeyed.")
    return True


def configured(db=DB):
    token, owner = cfg_get("telegram_token", db), cfg_get("telegram_owner_id", db)
    return (Api(token, cfg_get("telegram_api", db)), int(owner)) if token and owner else (None, None)


def notify(text, db=DB):
    """One message to the owner (alerts). -> True when Telegram accepted it. Never raises."""
    api, owner = configured(db)
    if not api:
        return False
    try:
        return api.send(owner, text) > 0
    except TgError:
        return False


def call_owner(text, db=DB, caller=None):
    """A Telegram voice CALL to the owner that speaks `text` (free CallMeBot service; one-way). -> (accepted, detail)."""
    from . import callmebot
    user = cfg_get("telegram_username", db)
    if not user:
        return False, "no Telegram username is saved (run: -m jarvis_addons.telegram_bot --set-username YOURNAME)"
    return (caller or callmebot.call)(user, text, lang=cfg_get("telegram_call_lang", db))


def make_stt(host):
    """Speech-to-text for voice messages through Groq (the audio is sent to Groq). Tries the saved keys one after another."""
    def stt(audio, name):
        from groq import Groq
        name = re.sub(r"\.oga$", ".ogg", name or "voice.ogg")               # Telegram calls its voice files .oga; Groq wants a known ending
        last = None
        for key in list(host["groq_keys"]())[:3]:
            try:
                return Groq(api_key=key).audio.transcriptions.create(file=(name, audio), model=STT_MODEL).text
            except Exception as e:
                last = e
        if last is not None and host.get("log_debug"):
            host["log_debug"]("telegram_bot/stt", type(last).__name__)
        return None
    return stt if host.get("groq_keys") else None


def make_tts(host):
    """JARVIS's own voice (edge-tts) as mp3 bytes."""
    def tts(text):
        path = os.path.join(host["JARVIS_DIR"], f"_tg_reply_{uuid.uuid4().hex[:8]}.mp3")
        voices = [host.get("ACTIVE_VOICE")] + [v for v in (host.get("VOICE_SETS") or {}).get(host.get("VOICE_GENDER"), []) if v != host.get("ACTIVE_VOICE")]
        try:
            for v in [v for v in voices if v][:3]:
                try:
                    host["_tts_generate"](text, v, path)
                    if os.path.isfile(path) and os.path.getsize(path) > 500:
                        with open(path, "rb") as fh:
                            return fh.read()
                except Exception:
                    continue
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return tts if host.get("_tts_generate") else None


# ---- alerts from the always-on JARVIS -------------------------------------------------------------------------------
_last_alert_call = {"ts": 0.0}


def alert(text, db=DB, now=None, caller=None, sender=None):
    """Something important happened: the details go to the owner's own Telegram chat; then a CALL makes the phone ring.
    The call never carries the details (it passes through a third party) and is placed at most once per CALL_ALERT_EVERY.
    -> (text sent, call accepted or None when no call was attempted)"""
    sent = (sender or notify)(text, db)
    if cfg_get("telegram_alert_call", db) == "off" or not cfg_get("telegram_username", db):
        return sent, None
    t = now if now is not None else time.time()
    if t - _last_alert_call["ts"] < CALL_ALERT_EVERY:
        return sent, None
    _last_alert_call["ts"] = t
    ok, _ = call_owner("This is JARVIS. Something important needs your attention. The details are in your Telegram chat.", db, caller)
    return sent, ok


KEEP_EVERY = 60                     # seconds between two looks whether the background bot is still alive


def bot_processes():
    """Other processes that are running this bot with --run (not this process and not its own starter)."""
    import psutil
    try:
        mine = {os.getpid()} | {q.pid for q in psutil.Process().parents()}
    except Exception:
        mine = {os.getpid()}
    out = []
    for q in psutil.process_iter(["name", "cmdline"]):
        try:
            cl = " ".join(q.info["cmdline"] or [])
            if (q.info["name"] or "").lower().startswith("python") and "jarvis_addons.telegram_bot" in cl and "--run" in cl and q.pid not in mine:
                out.append(q)
        except Exception:
            pass
    return out


def start_background(db=DB, root=ROOT):
    """Starts the bot as its own windowless process. -> 'started' | 'running' | 'not set up' | 'switched off'"""
    import subprocess
    if os.path.exists(os.path.join(root, "jarvis_off.flag")):
        return "switched off"
    if configured(db)[0] is None:
        return "not set up"
    if bot_processes():
        return "running"
    pyw = os.path.join(root, "venv", "Scripts", "pythonw.exe")
    subprocess.Popen([pyw if os.path.isfile(pyw) else sys.executable, "-m", "jarvis_addons.telegram_bot", "--run"], cwd=root, close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "started"


def _keeper(host):
    log = host.get("file_log") or (lambda s: None)
    time.sleep(20)
    while True:
        try:
            if start_background() == "started":
                log("[TELEGRAM] the bot was not running: started it in the background")
        except Exception as e:
            if host.get("log_debug"):
                host["log_debug"]("telegram_bot/keeper", e)
        time.sleep(KEEP_EVERY)


def install(host):
    """Hook for the always-on JARVIS: whatever it already decides is important enough to alert the owner on WhatsApp
    (an important email, its reminders) is ALSO sent to Telegram and followed by a call. JARVIS's own behaviour and the
    result it gets back are unchanged; the Telegram part runs in the background and can never block or break it."""
    if "notify_whatsapp" not in host or getattr(host["notify_whatsapp"], "_telegram", False):
        return False
    orig = host["notify_whatsapp"]

    def notify_whatsapp(text, *a, **k):
        try:
            # never from a terminal run (--say, --full-test, the bot process itself): only the always-on JARVIS alerts the owner
            if not host.get("CLI_MODE") and configured()[0] is not None:
                threading.Thread(target=alert, args=(str(text),), daemon=True, name="telegram_alert").start()
        except Exception as e:
            if host.get("log_debug"):
                host["log_debug"]("telegram_bot/alert", e)
        return orig(text, *a, **k)

    notify_whatsapp._telegram = True
    host["notify_whatsapp"] = notify_whatsapp
    if not host.get("CLI_MODE"):                              # only the always-on JARVIS keeps the bot alive, never a terminal command
        threading.Thread(target=_keeper, args=(host,), daemon=True, name="telegram_keeper").start()
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--set-username" in argv:
        from . import callmebot
        raw = (argv[argv.index("--set-username") + 1:] or [""])[0]
        user = callmebot.clean_user(raw)
        if not user:
            print("That is not a Telegram username (5-32 letters, digits or _), nor a +number.")
            return 2
        cfg_set("telegram_username", user)
        print(f"Saved: {user}. Test a call with:  venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --call \"hello\"")
        return 0
    if "--call" in argv:
        text = " ".join(argv[argv.index("--call") + 1:]) or "This is JARVIS. This is a test call."
        ok, detail = call_owner(text)
        print(("CallMeBot accepted the call: " if ok else "The call was NOT placed: ") + detail)
        return 0 if ok else 1
    if "--setup" in argv:
        tok = argv[argv.index("--token") + 1] if "--token" in argv else None
        own = argv[argv.index("--owner-id") + 1] if "--owner-id" in argv else None
        return 0 if setup(token=tok, owner_id=own) else 1
    api, owner = configured()
    if "--status" in argv:
        if not api:
            print("Telegram is not set up. Run:  venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --setup")
            return 1
        try:
            me = api.call("getMe")
            print(f"Bot: @{me.get('username')} - reachable. Owner id saved: yes. Token: saved (hidden).")
            return 0
        except TgError as e:
            print(f"Bot token is saved but Telegram did not answer: {e}")
            return 1
    if not api:
        print("Telegram is not set up. Run:  venv\\Scripts\\python.exe -m jarvis_addons.telegram_bot --setup")
        return 1
    if "--send" in argv:
        text = " ".join(argv[argv.index("--send") + 1:]) or "Test message from JARVIS."
        ok = notify(text)
        print("Sent." if ok else "Could not send (see the bot token / internet).")
        return 0 if ok else 1
    if "--start" in argv:
        print("Telegram bot: " + start_background())
        return 0
    if "--run" in argv and bot_processes():
        print("The Telegram bot is already running in the background (two of them would fight over the same messages).")
        return 0
    if "--run" in argv or "--once" in argv:
        from ._host import load
        L = load()
        try:
            from jarvis_agent.util import mask_secrets
        except Exception:
            mask_secrets = None
        bridge = Bridge(L.__dict__, api, owner, mask=mask_secrets, stt=make_stt(L.__dict__), tts=make_tts(L.__dict__), caller=call_owner)
        bridge.capture_speech()
        once = "--once" in argv
        print("JARVIS is answering on Telegram" + (" (only what is waiting now)." if once else ". Press Ctrl+C to stop."))
        try:
            (L.__dict__.get("file_log") or print)("[TELEGRAM] bot " + ("answered what was waiting" if once else "is answering in the background"))
            n = poll_loop(bridge, once=once, on_error=lambda e: print(f"  (Telegram: {e})"))
        except KeyboardInterrupt:
            n = 0
            print("\nStopped.")
        finally:
            bridge.release_speech()
        if once:
            print(f"Handled {n} message(s).")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
