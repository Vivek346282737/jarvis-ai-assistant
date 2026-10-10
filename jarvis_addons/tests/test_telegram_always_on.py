"""The Telegram bot as a background process of the always-on JARVIS. Run:
venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_telegram_always_on -v"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import power, telegram_bot as tg  # noqa: E402


class TestAlwaysOn(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "j.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.commit()
        con.close()

    def _setup_bot(self):
        con = sqlite3.connect(self.db)
        con.executemany("INSERT INTO config VALUES (?, ?)", [("telegram_token", "123456789:" + "A" * 35), ("telegram_owner_id", "42")])
        con.commit()
        con.close()

    def test_not_started_when_not_set_up_or_switched_off(self):
        self.assertEqual(tg.start_background(self.db, self.dir.name), "not set up")
        self._setup_bot()
        open(os.path.join(self.dir.name, "jarvis_off.flag"), "w").close()
        self.assertEqual(tg.start_background(self.db, self.dir.name), "switched off")      # JARVIS Band: the bot stays off too

    def test_only_one_bot_and_it_is_started_without_a_window(self):
        self._setup_bot()
        import subprocess
        keep_popen, keep_procs, started = subprocess.Popen, tg.bot_processes, []
        subprocess.Popen = lambda args, **k: started.append((args, k))
        self.addCleanup(lambda: (setattr(subprocess, "Popen", keep_popen), setattr(tg, "bot_processes", keep_procs)))
        tg.bot_processes = lambda: ["a running bot"]
        self.assertEqual(tg.start_background(self.db, self.dir.name), "running")
        self.assertEqual(started, [])
        tg.bot_processes = lambda: []
        self.assertEqual(tg.start_background(self.db, self.dir.name), "started")
        args, kw = started[0]
        self.assertEqual(args[1:], ["-m", "jarvis_addons.telegram_bot", "--run"])
        self.assertTrue(kw["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_a_terminal_command_never_starts_the_keeper_and_the_switch_knows_the_bot(self):
        import threading
        host = {"notify_whatsapp": lambda t: True, "CLI_MODE": True}
        self.assertTrue(tg.install(host))
        self.assertNotIn("telegram_keeper", [t.name for t in threading.enumerate()])
        self.assertEqual(power.kind_of("pythonw.exe", r"C:\J\venv\Scripts\pythonw.exe -m jarvis_addons.telegram_bot --run"), "telegram")
        self.assertNotIn(os.getpid(), [p.pid for p in tg.bot_processes()])


if __name__ == "__main__":
    unittest.main()
