"""The Pexels key (kept in the Windows user environment) must never appear in output, logs, reports, task state, speech
or a package. Uses a FAKE key. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_secret_masking -v"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import make_clean_package  # noqa: E402
from jarvis_agent import core, polish, runner, util  # noqa: E402
from jarvis_agent.state import TaskState  # noqa: E402

FAKE = "FAKEpexelsKEY0123456789abcdefghijklmnopqrstuvwxyzABCDEF"


class Base(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PEXELS_API_KEY")
        os.environ["PEXELS_API_KEY"] = FAKE
        self.tmp = tempfile.mkdtemp(prefix="jarvis_mask_test_")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.old is None:
            os.environ.pop("PEXELS_API_KEY", None)
        else:
            os.environ["PEXELS_API_KEY"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMasking(Base):
    def test_value_is_masked_wherever_it_appears_and_other_text_is_untouched(self):
        self.assertIn(FAKE, util.secret_values())
        self.assertEqual(util.mask_secrets(f"401 for Authorization: {FAKE} (bad key)"), "401 for Authorization: *** (bad key)")
        self.assertEqual(util.mask_secrets(f"{FAKE}{FAKE}"), "******")
        plain = "GET /menu returned 500: Internal Server Error, token missing"
        self.assertEqual(util.mask_secrets(plain), plain)
        self.assertEqual(util.mask_secrets(None), "")
        self.assertNotIn(FAKE, util.redact(f"PEXELS_API_KEY={FAKE}"))
        self.assertNotIn(FAKE, util.redact(f"environ({{'X': '{FAKE}'}})"))

    def test_the_key_still_reaches_the_feature_that_needs_it(self):
        self.assertEqual(polish.pexels_key(), FAKE)                                          # the photo search itself is unchanged

    def test_task_state_file_never_holds_the_value(self):
        st = TaskState("build a site")
        st.error(f"server said {FAKE}")
        st.test("smoke", False, f"body: {FAKE}")
        st["plan"] = {"note": FAKE}
        st.save()
        from jarvis_agent import state as state_mod
        path = os.path.join(state_mod.STATE_DIR, st["id"] + ".json")
        try:
            self.assertNotIn(FAKE, open(path, encoding="utf-8").read())
        finally:
            os.remove(path)

    def test_spoken_text_is_masked(self):
        said = []
        ctx = core.Ctx({"speak": said.append, "log_debug": lambda c, e: None}, None, {})
        ctx.say(f"I found a problem: KeyError near {FAKE}. Fixing it, Boss.")
        ctx.say("Done, Boss. It is built, tested and verified.")
        self.assertEqual(said, ["I found a problem: KeyError near ***. Fixing it, Boss.", "Done, Boss. It is built, tested and verified."])


class TestStartedPrograms(Base):
    def test_a_program_jarvis_runs_does_not_get_the_key_and_its_output_is_masked(self):
        self.assertNotIn("PEXELS_API_KEY", util.child_env())
        self.assertEqual(util.child_env(PORT="1234")["PORT"], "1234")
        self.assertEqual(util.child_env().get("PATH"), os.environ.get("PATH"))               # everything else is passed on as before
        with open(os.path.join(self.tmp, "show.py"), "w") as fh:
            fh.write("import os\nprint('key in my environment:', os.environ.get('PEXELS_API_KEY'))\nprint('path present:', bool(os.environ.get('PATH')))\n")
        rc, out = util.run_cmd(["python", "show.py"], self.tmp)
        self.assertEqual(rc, 0, out)
        self.assertIn("key in my environment: None", out)
        self.assertIn("path present: True", out)
        with open(os.path.join(self.tmp, "leak.py"), "w") as fh:                             # a program that got hold of the value some other way
            fh.write(f"print('oops {FAKE}')\n")
        self.assertEqual(util.run_cmd(["python", "leak.py"], self.tmp)[1].strip(), "oops ***")

    def test_a_started_server_does_not_get_the_key_and_its_log_is_masked_when_read(self):
        with open(os.path.join(self.tmp, "srv.py"), "w") as fh:
            fh.write("import os, sys, time\nprint('Error: env key =', os.environ.get('PEXELS_API_KEY'), 'port', os.environ.get('PORT'))\n"
                     f"print('Error: leaked {FAKE}')\nsys.stdout.flush()\ntime.sleep(20)\n")
        s = runner.Server(self.tmp, ["python", "srv.py"])
        s.start()
        try:
            import time
            for _ in range(40):
                if "leaked" in util.read_text(s.log_path, 10 ** 6):
                    break
                time.sleep(0.25)
            tail, tb = s.log_tail(), s.new_traceback()
        finally:
            s.stop()
        self.assertIn("env key = None", tail)
        self.assertIn(f"port {s.port}", tail)
        self.assertIn("leaked ***", tail)
        self.assertNotIn(FAKE, tail)
        self.assertNotIn(FAKE, tb or "")


class TestPackageScanner(Base):
    def test_a_pexels_key_inside_shipped_code_rejects_the_package(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(src, "jarvis_agent"))
        for f in make_clean_package.FILES:
            with open(os.path.join(src, f), "w") as fh:
                fh.write("# code\n")
        db = os.path.join(src, "jarvis_memory.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.commit()
        con.close()
        self.assertIn(FAKE, make_clean_package.owner_secrets(db))
        with open(os.path.join(src, "jarvis_agent", "ok.py"), "w") as fh:
            fh.write("KEY_NAME = 'PEXELS_API_KEY'\n")                                        # the NAME of the variable is fine
        self.assertTrue(make_clean_package.build(os.path.join(self.tmp, "out1"), with_models=False, db=db, src=src))
        with open(os.path.join(src, "jarvis_agent", "leak.py"), "w") as fh:
            fh.write(f"headers = {{'Authorization': '{FAKE}'}}\n")
        dest = os.path.join(self.tmp, "out2")
        self.assertFalse(make_clean_package.build(dest, with_models=False, db=db, src=src))
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
