"""One-click off / on. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_power -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import power  # noqa: E402


class TestPower(unittest.TestCase):
    def test_which_sentences_switch_everything_off(self):
        for s in ("exam mode", "jarvis exam mode", "exam mode on", "exam mode chalu karo", "sab band karo", "sab kuch band kar do", "poora band ho jao"):
            self.assertTrue(power.wants_off(s), s)
        for s in ("so jao", "jarvis so jao", "sleep mode", "exam mode off", "exam mode band karo", "light band karo", "music band karo",
                  "my exam is tomorrow", "what time is it", "phone ki battery kitni hai", ""):
            self.assertFalse(power.wants_off(s), s)                              # 'so jao' stays JARVIS's own quiet mode

    def test_which_processes_belong_to_jarvis(self):
        k = power.kind_of
        self.assertEqual(k("python.exe", r'"C:\J\venv\Scripts\python.exe" "C:\J\live_cmd_console.py"'), "jarvis")
        self.assertEqual(k("pythonw.exe", r"C:\J\live_cmd_console.py"), "jarvis")
        self.assertEqual(k("python.exe", "-m jarvis_addons.telegram_bot --run"), "telegram")
        self.assertEqual(k("powershell.exe", r"-File C:\J\JARVIS_Autostart.ps1"), "autostart")
        self.assertEqual(k("ollama app.exe", ""), "helper")
        self.assertEqual(k("llama-server.exe", ""), "helper")
        self.assertEqual(k("adb.exe", "fork-server"), "helper")
        self.assertIsNone(k("pythonw.exe", "-m jarvis_addons.power --off"))      # the switch never stops itself
        self.assertIsNone(k("python.exe", "my_own_script.py"))
        self.assertIsNone(k("chrome.exe", "live_cmd_console.py"))
        self.assertIsNone(k("powershell.exe", "-Command Get-Date"))

    def test_voice_command_is_added_without_touching_other_commands(self):
        seen, said, stopped = [], [], []
        host = {"check_dynamic_rules": lambda cmd: seen.append(cmd) or False, "speak": said.append, "CLI_MODE": True,
                "require_owner": lambda what: False, "file_log": lambda s: None}
        keep = power.stop_all
        power.stop_all = lambda keep_pids=(): stopped.append(1)
        self.addCleanup(lambda: setattr(power, "stop_all", keep))
        self.assertTrue(power.install(host))
        self.assertFalse(power.install(host))
        self.assertFalse(host["check_dynamic_rules"]("what time is it"))
        self.assertEqual(seen, ["what time is it"])
        self.assertTrue(host["check_dynamic_rules"]("exam mode"))                # somebody who is not the owner: refused, nothing stops
        self.assertEqual((said, stopped, seen), ([], [], ["what time is it"]))
        self.assertTrue(host["check_dynamic_rules"]("exam mode off"))            # answered, never passed on to be guessed as another mode
        self.assertEqual((said, stopped, seen), (["Exam mode is not on, Boss. I am here and listening."], [], ["what time is it"]))
        self.assertFalse(power.install({}))

    def test_shortcuts_are_made_and_run_without_a_window(self):
        with tempfile.TemporaryDirectory() as d:
            made = power.make_shortcuts(d)
            self.assertEqual(sorted(os.path.basename(p) for p in made), ["JARVIS Band.lnk", "JARVIS Chalu.lnk"])
            raw = open(made[0], "rb").read()
            self.assertIn("pythonw.exe".encode("utf-16-le"), raw)
            self.assertIn("-m jarvis_addons.power --off".encode("utf-16-le"), raw)

if __name__ == "__main__":
    unittest.main()
