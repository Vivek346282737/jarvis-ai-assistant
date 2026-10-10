"""Dashboard backend. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_dashboard -v"""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import dashboard as dash, power  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "j.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.commit()
        con.close()
        dash._cfg_set("dashboard_pw", dash.hash_password("open-sesame"), self.db)
        self.history = dash.History(os.path.join(self.dir.name, "chat.db"))
        self.guard = dash.Guard(self.db)
        self.ran = []

        def route(text, user):
            self.ran.append(text)
            if "boom" in text:
                raise RuntimeError("inside JARVIS")
            if "quiet" not in text:
                self.host["speak"]("You said " + text + ". The key is sk-abcdefghijklmnopqrstuvwxyz123456")
        self.host = {"route_and_execute": route, "speak": lambda t: None, "speak_urgent": lambda t: None, "CURRENT_ACTIVE_USER": "owner", "log_debug": lambda c, e: self.ran.append(f"DEBUG {c}")}
        self.app = dash.make_app(self.host, history=self.history, guard=self.guard, stt=lambda audio, name: "what time is it" if len(audio) > 10 else None)
        self.runner = self.app.jarvis["runner"]
        self.runner.mask = lambda s: s.replace("sk-abcdefghijklmnopqrstuvwxyz123456", "***")
        self.c = self.app.test_client()

    def login(self):
        return self.c.post("/api/login", json={"password": "open-sesame"})

    def wait(self):
        end = time.time() + 10
        while self.runner.busy and time.time() < end:
            time.sleep(0.02)


class TestSecurity(Base):
    def test_password_is_stored_only_as_a_salted_hash(self):
        h = dash.hash_password("open-sesame")
        self.assertNotIn("open-sesame", h)
        self.assertNotEqual(h, dash.hash_password("open-sesame"))                # a new salt every time
        self.assertTrue(dash.password_ok("open-sesame", h))
        self.assertFalse(dash.password_ok("open-sesam", h))
        self.assertFalse(dash.password_ok("x", None))
        self.assertFalse(dash.password_ok("x", "garbage"))

    def test_first_start_makes_a_password_once(self):
        db2, f = os.path.join(self.dir.name, "new.db"), os.path.join(self.dir.name, "pw.txt")
        pw = dash.ensure_password(db2, f)
        with open(f, encoding="utf-8") as fh:                                # closed before the temp folder is removed (Windows)
            self.assertTrue(pw and pw in fh.read())
        self.assertTrue(dash.password_ok(pw, dash._cfg("dashboard_pw", db2)))
        self.assertIsNone(dash.ensure_password(db2, f))

    def test_nothing_works_without_login(self):
        for path in ("/api/history", "/api/overview", "/api/notifications", "/api/capabilities", "/api/activity", "/api/detail/system", "/api/state"):
            self.assertEqual(self.c.get(path).status_code, 401, path)
        self.assertEqual(self.c.post("/api/chat", json={"text": "what time is it"}).status_code, 401)
        self.assertEqual(self.c.post("/api/voice").status_code, 401)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.c.get("/api/ping").get_json(), {"ok": True, "authed": False})

    def test_wrong_passwords_lock_that_device_out_and_logout_ends_the_session(self):
        for _ in range(dash.MAX_FAILS):
            self.assertEqual(self.c.post("/api/login", json={"password": "nope"}).status_code, 403)
        self.assertEqual(self.login().status_code, 429)                           # even the right one, for five minutes
        self.assertFalse(self.guard.locked("127.0.0.1", time.time() + dash.LOCK_SECONDS + 1))
        self.guard.fails.clear()
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertIn("HttpOnly", r.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", r.headers["Set-Cookie"])
        self.assertEqual(self.c.get("/api/history").status_code, 200)
        self.c.post("/api/logout")
        self.assertEqual(self.c.get("/api/history").status_code, 401)
        tok = self.guard.login("1.2.3.4", "open-sesame")
        self.assertFalse(self.guard.valid(tok, time.time() + dash.SESSION_HOURS * 3600 + 1))      # sessions end by themselves


class TestChat(Base):
    def test_a_sentence_goes_through_jarvis_and_the_answer_is_kept(self):
        self.login()
        self.assertEqual(self.c.post("/api/chat", json={"text": "  "}).status_code, 400)
        self.assertEqual(self.c.post("/api/chat", json={"text": "hello there"}).status_code, 200)
        self.wait()
        msgs = self.c.get("/api/history").get_json()["messages"]
        self.assertEqual([(m["role"], m["text"]) for m in msgs], [("user", "hello there"), ("jarvis", "You said hello there. The key is ***")])   # secrets are masked
        self.assertEqual(self.ran, ["hello there"])
        again = dash.History(os.path.join(self.dir.name, "chat.db"))             # a restart of the server: the history is still there
        self.assertEqual(len(again.since()), 2)
        self.assertEqual(self.c.get(f"/api/history?after={msgs[-1]['id']}").get_json()["messages"], [])

    def test_silent_and_failing_commands_still_give_an_answer_and_never_kill_the_server(self):
        self.login()
        self.c.post("/api/chat", json={"text": "quiet one"})
        self.wait()
        self.c.post("/api/chat", json={"text": "boom"})
        self.wait()
        texts = [m["text"] for m in self.history.since()]
        self.assertIn("Done.", texts)
        self.assertTrue(any("failed inside JARVIS" in t for t in texts))
        self.assertFalse(self.runner.busy)
        self.assertEqual(self.c.post("/api/chat", json={"text": "after the crash"}).status_code, 200)
        self.wait()

    def test_one_sentence_at_a_time_and_dangerous_ones_are_refused(self):
        self.login()
        self.runner.busy = True
        self.assertEqual(self.c.post("/api/chat", json={"text": "second"}).status_code, 409)
        self.runner.busy = False
        self.c.post("/api/chat", json={"text": "shut down the laptop"})
        self.wait()
        self.assertNotIn("shut down the laptop", self.ran)
        self.assertIn("I do not do that from the dashboard", self.history.since()[-1]["text"])

    def test_voice_recording_becomes_text_then_a_command(self):
        import io
        self.login()
        r = self.c.post("/api/voice", data={"audio": (io.BytesIO(b"x" * 100), "voice.webm")}, content_type="multipart/form-data")
        self.assertEqual((r.status_code, r.get_json()["heard"]), (200, "what time is it"))
        self.wait()
        self.assertEqual(self.ran, ["what time is it"])
        r = self.c.post("/api/voice", data={"audio": (io.BytesIO(b"x"), "voice.webm")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.post("/api/voice").status_code, 400)

    def test_errors_come_back_as_small_json_messages(self):
        self.login()
        r = self.c.get("/api/overview")                                          # this fake host has none of JARVIS's data functions:
        self.assertEqual(r.status_code, 200)                                     # each card that cannot be filled says so, the rest still arrives
        o = r.get_json()
        self.assertIn("error", o["weather"])
        self.assertIn("error", o["phone"])
        self.assertIn("cpu", o["system"])
        self.assertEqual(self.c.get("/api/nothing-here").status_code, 404)
        self.assertEqual(self.c.get("/api/history").status_code, 200)            # and the server goes on


class TestSwitch(unittest.TestCase):
    def test_band_chalu_know_the_dashboard(self):
        self.assertEqual(power.kind_of("pythonw.exe", r"C:\J\venv\Scripts\pythonw.exe -m jarvis_addons.dashboard --run"), "dashboard")
        self.assertNotIn(os.getpid(), [p.pid for p in dash.dashboard_processes()])
        self.assertFalse(dash.install({"CLI_MODE": True, "get_config": lambda k, d=None: d}))      # a terminal command never starts it

    def test_not_started_while_switched_off(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "jarvis_off.flag"), "w").close()
            self.assertEqual(dash.start_background(d), "switched off")


if __name__ == "__main__":
    unittest.main()
