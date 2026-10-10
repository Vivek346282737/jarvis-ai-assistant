"""Tests for the Telegram bridge against a LOCAL fake of Telegram's Bot API (a real HTTP server on 127.0.0.1), so the
whole path - long poll, parsing, owner check, running the command, sending the reply - is exercised without Telegram.
Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_telegram_bot -v"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import telegram_bot as tg  # noqa: E402

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
OWNER, STRANGER = 4242, 9999


class FakeTelegram:
    """Just enough of the Bot API: getMe, getUpdates (with offset), sendMessage."""

    def __init__(self):
        self.updates, self.sent, self.next_id, self.calls = [], [], 100, []
        self.files, self.voices = {}, []                     # file_id -> bytes the 'owner' sent; voice messages the bot sent
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):                                # file download: /file/bot<token>/voice/<file_id>.oga
                parts = self.path.strip("/").split("/")
                data = fake.files.get(parts[-1].rsplit(".", 1)[0]) if len(parts) >= 3 and parts[1][3:] == TOKEN else None
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                parts = self.path.strip("/").split("/")
                token, method = parts[0][3:], parts[1]
                fake.calls.append(method)
                if method == "sendVoice" and token == TOKEN:
                    fake.voices.append(raw)
                    return self._send(200, {"ok": True, "result": {"message_id": 1}})
                body = json.loads(raw or b"{}")
                if token != TOKEN:
                    return self._send(401, {"ok": False, "description": "Unauthorized"})
                if method == "getMe":
                    return self._send(200, {"ok": True, "result": {"id": 1, "username": "jarvis_test_bot", "first_name": "JARVIS"}})
                if method == "getUpdates":
                    off = int(body.get("offset") or 0)
                    return self._send(200, {"ok": True, "result": [u for u in fake.updates if u["update_id"] >= off]})
                if method == "getFile":
                    fid = body.get("file_id")
                    if fid not in fake.files:
                        return self._send(400, {"ok": False, "description": "Bad Request: file not found"})
                    return self._send(200, {"ok": True, "result": {"file_id": fid, "file_size": len(fake.files[fid]), "file_path": "voice/%s.oga" % fid}})
                if method == "sendMessage":
                    fake.sent.append((body["chat_id"], body["text"]))
                    return self._send(200, {"ok": True, "result": {"message_id": len(fake.sent)}})
                self._send(404, {"ok": False, "description": "no such method"})

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def message(self, text, sender=OWNER, age=0, chat_type="private", voice=False, audio=None, duration=3):
        self.next_id += 1
        m = {"message_id": self.next_id, "date": int(time.time()) - age, "from": {"id": sender, "first_name": "Riya"}, "chat": {"id": sender, "type": chat_type}}
        if audio is not None:
            fid = "f%d" % self.next_id
            self.files[fid] = audio
            m["voice"] = {"file_id": fid, "duration": duration}
        elif voice:
            m["voice"] = {"file_id": "abc", "duration": 3}
        else:
            m["text"] = text
        self.updates.append({"update_id": self.next_id, "message": m})

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeJarvis(dict):
    """route_and_execute answers through speak(), exactly how JARVIS does."""

    def __init__(self):
        super().__init__()
        self.ran, self.spoken_aloud, self.logs = [], [], []

        def route(text, speaker):
            self.ran.append((text, speaker))
            if "boom" in text:
                raise RuntimeError("kaboom")
            if "quiet" in text:
                return
            if "token" in text:
                self["speak"]("The secret is " + TOKEN)
            else:
                self["speak"]("*It's* 2:17 PM, Boss.")
        self.update(route_and_execute=route, CURRENT_ACTIVE_USER="Riya", speak=self.spoken_aloud.append, speak_urgent=self.spoken_aloud.append,
                    file_log=self.logs.append, log_debug=lambda c, e: self.logs.append("[DEBUG %s] %r" % (c, e)))


class Base(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        self.addCleanup(self.tg.close)
        self.tmp = tempfile.mkdtemp(prefix="jarvis_tg_test_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.db = os.path.join(self.tmp, "j.db")
        self.host = FakeJarvis()
        self.bridge = tg.Bridge(self.host, tg.Api(TOKEN, self.tg.base), OWNER)
        self.bridge.capture_speech()
        self.addCleanup(self.bridge.release_speech)

    def run_once(self):
        return tg.poll_loop(self.bridge, once=True, db=self.db)

    def texts(self):
        return [t for _, t in self.tg.sent]


class TestBridge(Base):
    def test_owner_message_runs_as_the_owner_and_the_answer_comes_back_not_spoken_aloud(self):
        self.tg.message("what time is it")
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(self.host.ran, [("what time is it", "Riya")])
        self.assertEqual(self.tg.sent, [(OWNER, "It's 2:17 PM, Boss.")])
        self.assertEqual(self.host.spoken_aloud, [])                                     # the laptop stays silent

    def test_anyone_else_is_ignored_completely(self):
        self.tg.message("unlock phone", sender=STRANGER)
        self.tg.message("unlock phone", sender=OWNER, chat_type="group")                 # even the owner, but in a group chat
        self.run_once()
        self.assertEqual((self.host.ran, self.tg.sent), ([], []))

    def test_old_messages_are_not_executed(self):
        self.tg.message("lock phone", age=tg.MAX_AGE + 60)
        self.run_once()
        self.assertEqual(self.host.ran, [])
        self.assertIn("too old", self.texts()[0])

    def test_destructive_requests_are_refused_from_telegram(self):
        for t in ("shutdown the laptop", "restart my pc", "delete all my files", "format the drive"):
            self.tg.message(t)
        self.run_once()
        self.assertEqual(self.host.ran, [])
        self.assertEqual(len(self.tg.sent), 4)

    def test_a_command_that_says_nothing_still_gets_an_answer_and_a_crash_is_reported(self):
        self.tg.message("quiet thing")
        self.tg.message("boom now")
        self.run_once()
        self.assertEqual(self.texts(), ["Done.", "Something went wrong with that one. I logged it."])

    def test_the_bot_token_never_leaves_in_a_reply(self):
        self.tg.message("tell me the token")
        self.run_once()
        self.assertEqual(self.texts(), ["The secret is ***"])

    def test_a_command_is_never_run_twice_even_after_a_restart(self):
        self.tg.message("what time is it")
        self.run_once()
        again = tg.Bridge(self.host, tg.Api(TOKEN, self.tg.base), OWNER)                  # a new process: only the saved offset is left
        tg.poll_loop(again, once=True, db=self.db)
        self.assertEqual(len(self.host.ran), 1)

    def test_slash_start_and_voice_are_answered_without_running_anything(self):
        self.tg.message("/start")
        self.tg.message("", voice=True)
        self.run_once()
        self.assertEqual(self.host.ran, [])
        self.assertEqual(len(self.tg.sent), 2)

    def test_speech_goes_back_to_the_speakers_when_the_bridge_is_released(self):
        self.bridge.release_speech()
        self.host["speak"]("hello")
        self.assertEqual((self.host.spoken_aloud, self.tg.sent), (["hello"], []))

    def test_telegram_being_down_never_crashes_the_loop(self):
        errors = []
        dead = tg.Bridge(self.host, tg.Api(TOKEN, "http://127.0.0.1:9"), OWNER)
        self.assertEqual(tg.poll_loop(dead, once=True, db=self.db, on_error=errors.append), 0)
        self.assertEqual(len(errors), 1)
        self.assertNotIn(TOKEN, str(errors[0]))

    def test_long_answers_are_split(self):
        self.assertEqual(tg.Api(TOKEN, self.tg.base).send(OWNER, "x" * (tg.MAX_LEN * 2 + 5)), 3)


class TestReachingTelegram(Base):
    """Found for real: the owner's IPv6 route to Telegram reset half of the connections (WinError 10054)."""

    def test_telegram_is_always_dialled_over_ipv4(self):
        import socket
        asked = []
        keep_info, keep_conn = socket.getaddrinfo, socket.create_connection

        def fake_info(host, port, family=0, *a, **k):
            asked.append(family)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.166.110", port))]

        dialled = []
        socket.getaddrinfo, socket.create_connection = fake_info, lambda addr, *a, **k: dialled.append(addr) or "SOCK"
        try:
            self.assertEqual(tg._ipv4_connection(("api.telegram.org", 443), 5), "SOCK")
        finally:
            socket.getaddrinfo, socket.create_connection = keep_info, keep_conn
        self.assertEqual((asked, dialled), ([socket.AF_INET], [("149.154.166.110", 443)]))

    def test_a_reset_connection_is_tried_again_and_a_real_refusal_is_not(self):
        import urllib.error
        api = tg.Api(TOKEN, self.tg.base)
        real_open, calls = tg._OPENER.open, []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.URLError(ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host"))
            return real_open(req, timeout=timeout)
        keep_sleep = tg.time.sleep
        tg._OPENER.open, tg.time.sleep = flaky, lambda s: None
        try:
            self.assertEqual(api.call("getMe")["username"], "jarvis_test_bot")            # 2 resets, then the answer
            self.assertEqual(len(calls), 3)
            calls.clear()
            with self.assertRaises(tg.TgError):
                api.call("sendMessage", chat_id=OWNER, text="x")                          # a send is retried only once: 2 resets = give up
            self.assertEqual(len(calls), 2)
        finally:
            tg._OPENER.open, tg.time.sleep = real_open, keep_sleep
        bad = tg.Api("111111111:AAWrongButWellFormedTokenValue_abcdefghi", self.tg.base)
        before = len(self.tg.calls)
        with self.assertRaises(tg.TgError):
            bad.call("getMe")
        self.assertEqual(len(self.tg.calls) - before, 1)                                 # HTTP 401 is an answer, not a broken connection


class TestSetup(Base):
    def test_setup_learns_the_owner_from_the_first_message_after_confirmation(self):
        said = []
        self.tg.message("hi")
        ok = tg.setup(db=self.db, token=TOKEN, base=self.tg.base, ask=lambda p: "y", say=said.append, wait=5)
        self.assertTrue(ok, said)
        self.assertEqual((tg.cfg_get("telegram_owner_id", self.db), tg.cfg_get("telegram_token", self.db)), (str(OWNER), TOKEN))
        self.assertFalse(any(TOKEN in s for s in said))
        self.assertIn("linked", self.texts()[-1])

    def test_setup_saves_nothing_when_the_owner_says_no_or_the_token_is_wrong(self):
        self.tg.message("hi", sender=STRANGER)
        self.assertFalse(tg.setup(db=self.db, token=TOKEN, base=self.tg.base, ask=lambda p: "n", say=lambda s: None, wait=5))
        self.assertIsNone(tg.cfg_get("telegram_token", self.db))
        self.assertFalse(tg.setup(db=self.db, token="not-a-token", base=self.tg.base, ask=lambda p: "y", say=lambda s: None))
        self.assertFalse(tg.setup(db=self.db, token="987654321:AAWrongButWellFormedTokenValue_abcdefgh", base=self.tg.base, ask=lambda p: "y", say=lambda s: None))
        self.assertIsNone(tg.cfg_get("telegram_token", self.db))

    def test_notify_reports_honestly(self):
        self.assertFalse(tg.notify("x", db=self.db))                                      # not set up
        tg.cfg_set("telegram_token", TOKEN, self.db)
        tg.cfg_set("telegram_owner_id", OWNER, self.db)
        tg.cfg_set("telegram_api", self.tg.base, self.db)
        self.assertTrue(tg.notify("Important email from your manager", db=self.db))
        self.assertEqual(self.texts()[-1], "Important email from your manager")


if __name__ == "__main__":
    unittest.main()
