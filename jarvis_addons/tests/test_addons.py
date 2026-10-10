"""Offline tests for the add-ons. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_addons -v
Nothing here loads live_cmd_console.py or touches the real database, phone or microphone."""
import os
import sqlite3
import sys
import tempfile
import shutil
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import jarvis_setup  # noqa: E402
import make_clean_package  # noqa: E402
from jarvis_addons import voice_setup  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis_addons_test_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class FakeVoiceHost:
    """Just enough of JARVIS's voice API for voice_setup.status()."""

    def __init__(self, owner, prints, people):
        self.CURRENT_ACTIVE_USER, self._prints, self._people, self.cfg = owner, prints, people, {}

    def spk_load_all(self, include_cohort=False):
        return {n: v for n, v in self._prints.items() if include_cohort or not n.startswith("__")}

    def people_all(self):
        return self._people

    def spk_ready(self):
        return True

    def spk_threshold(self):
        return float(self.cfg.get("spk_thr", 0.45))

    def spk_margin(self):
        return float(self.cfg.get("spk_margin", 0.10))

    def get_config(self, k, d=None):
        return self.cfg.get(k, d)

    def set_config(self, k, v):
        self.cfg[k] = v

    def voice_lock_active(self):
        return True


class TestSetupOwner(Tmp):
    def _db(self, rows=(), contacts=0):
        db = os.path.join(self.tmp, "j.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.execute("CREATE TABLE contacts (name TEXT, phone TEXT)")
        con.executemany("INSERT INTO config VALUES (?, ?)", rows)
        con.executemany("INSERT INTO contacts VALUES (?, ?)", [(f"c{i}", "1") for i in range(contacts)])
        con.commit()
        con.close()
        return db

    def _owner(self, db):
        con = sqlite3.connect(db)
        try:
            return dict(con.execute("SELECT key, val FROM config WHERE key LIKE 'owner_%'").fetchall())
        finally:
            con.close()

    def test_new_folder_gets_its_own_owner(self):
        db = os.path.join(self.tmp, "new.db")
        self.assertEqual(jarvis_setup.main(["--name", "Riya", "--pronoun", "she", "--owner-only", "--db", db]), 0)
        self.assertEqual(self._owner(db), {"owner_name": "Riya", "owner_pronoun": "she", "owner_about": ""})

    def test_existing_installation_with_data_is_never_renamed(self):
        db = self._db(rows=[("gemini_api_key", "x")], contacts=3)          # the original owner's folder: data, but no owner_name
        self.assertEqual(jarvis_setup.existing_owner(db), ("legacy", None))
        self.assertEqual(jarvis_setup.main(["--name", "Riya", "--pronoun", "she", "--owner-only", "--db", db]), 2)
        self.assertEqual(self._owner(db), {})

    def test_owner_name_is_not_changed_once_set_but_the_placeholder_is(self):
        db = self._db(rows=[("owner_name", "Riya")])
        self.assertEqual(jarvis_setup.main(["--name", "Aman", "--owner-only", "--db", db]), 2)
        self.assertEqual(self._owner(db)["owner_name"], "Riya")
        db2 = os.path.join(self.tmp, "p.db")
        con = sqlite3.connect(db2)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.execute("INSERT INTO config VALUES ('owner_name', 'Owner')")          # JARVIS was started once before setup
        con.commit()
        con.close()
        self.assertEqual(jarvis_setup.main(["--name", "Aman", "--pronoun", "he", "--owner-only", "--db", db2]), 0)
        self.assertEqual(self._owner(db2)["owner_name"], "Aman")

    def test_odd_names_are_refused(self):
        self.assertIsNone(jarvis_setup.clean_name("x'; DROP TABLE config;--"))
        self.assertIsNone(jarvis_setup.clean_name("   "))
        self.assertEqual(jarvis_setup.clean_name("  Riya   Sharma "), "Riya Sharma")


class TestCleanPackage(Tmp):
    def _src(self, extra=None):
        src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(src, "jarvis_agent"))
        for f in make_clean_package.FILES:
            with open(os.path.join(src, f), "w", encoding="utf-8") as fh:
                fh.write("# code\n")
        with open(os.path.join(src, "jarvis_agent", "a.py"), "w") as fh:
            fh.write("x = 1\n")
        for rel, content in (extra or {}).items():
            os.makedirs(os.path.dirname(os.path.join(src, rel)), exist_ok=True)
            with open(os.path.join(src, rel), "w", encoding="utf-8") as fh:
                fh.write(content)
        db = os.path.join(src, "jarvis_memory.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val TEXT)")
        con.execute("CREATE TABLE contacts (name TEXT, phone TEXT, email TEXT)")
        con.executemany("INSERT INTO config VALUES (?, ?)", [("groq_keys", "gsk_AAAAAAAA1111,gsk_BBBBBBBB2222"), ("phone_password", "sunshine"), ("email_address", "me@mail.test")])
        con.execute("INSERT INTO contacts VALUES ('Mummy', '9876501234', 'mummy@mail.test')")
        con.commit()
        con.close()
        return src, db

    def test_personal_files_are_never_copied(self):
        src, db = self._src({"wa_chrome_profile/Default/Cookies": "session", "owner_voice.npy": "v", "jarvis.log": "l", "agent_tasks/t.json": "{}",
                             "jarvis_agent/__pycache__/a.pyc": "x", "live_cmd_console_backup.py": 'PW = "sunshine"\n'})
        dest = os.path.join(self.tmp, "out")
        self.assertTrue(make_clean_package.build(dest, with_models=False, db=db, src=src))
        got = sorted(os.path.relpath(os.path.join(dp, f), dest).replace("\\", "/") for dp, _, fn in os.walk(dest) for f in fn)
        self.assertEqual(got, sorted(make_clean_package.FILES + ["jarvis_agent/a.py", "requirements.txt"]))

    def test_a_secret_inside_shipped_code_rejects_and_deletes_the_package(self):
        for leak in ('KEY = "gsk_BBBBBBBB2222"\n', 'PHONE_PASSWORD = "sunshine"\n', "notify = '9876501234'\n", "to = 'mummy@mail.test'\n"):
            src, db = self._src({"jarvis_agent/leak.py": leak})
            dest = os.path.join(self.tmp, "out")
            self.assertFalse(make_clean_package.build(dest, with_models=False, db=db, src=src), leak)
            self.assertFalse(os.path.exists(dest))
            shutil.rmtree(src)

    def test_an_ordinary_word_password_does_not_cause_a_false_alarm(self):
        src, db = self._src({"jarvis_agent/ok.py": "# what a sunshine day\nx = 'sunshine_mode'\n"})
        self.assertTrue(make_clean_package.build(os.path.join(self.tmp, "out"), with_models=False, db=db, src=src))


class TestVoiceSetup(unittest.TestCase):
    def test_status_flags_voice_data_from_another_installation(self):
        L = FakeVoiceHost("Riya", {"Riya": [[1.0]] * 8, "Vivek": [[1.0]] * 51, "Papa": [[1.0]] * 5, "__cohort__": [[1.0]] * 16},
                          [{"name": "Riya", "role": "owner", "vectors": []}, {"name": "Papa", "role": "family", "vectors": []}])
        s = voice_setup.status(L)
        self.assertEqual((s["owner_prints"], s["cohort"], s["foreign_prints"], s["others"]), (8, 16, ["Vivek"], {"Vivek": 51, "Papa": 5}))
        L2 = FakeVoiceHost("Riya", {}, [{"name": "Vivek", "role": "owner", "vectors": [[1]]}])
        s2 = voice_setup.status(L2)
        self.assertEqual((s2["owner_prints"], s2["legacy_vectors"], s2["foreign_owner_rows"]), (0, 0, ["Vivek"]))

    def test_limits_are_applied_only_on_a_clean_split(self):
        L = FakeVoiceHost("Riya", {}, [])
        self.assertEqual(voice_setup.apply_limits(L, "\n=> Clean split. Set the best limits with these two lines:\n   --spk-threshold 0.62\n   --spk-margin 0.14\n"), (True, 0.62, 0.14))
        self.assertEqual(L.cfg, {"spk_thr": "0.62", "spk_margin": "0.14"})
        L2 = FakeVoiceHost("Riya", {}, [])
        self.assertFalse(voice_setup.apply_limits(L2, "=> Not a clean split (best room -0.03). Closest setting:  --spk-threshold 0.58  --spk-margin 0.10. Teach ...")[0])
        self.assertEqual(L2.cfg, {})


if __name__ == "__main__":
    unittest.main()


class FakePhone(dict):
    """A host dict with a scripted phone: `answers` maps the shell command (joined) to its stdout."""

    def __init__(self, answers, apps=None, call_pkgs=("com.samsung.android.incallui",)):
        super().__init__()
        self.ran, self.said, self.opened = [], [], []
        self.answers = answers

        def run(cmd_list, timeout=None):
            self.ran.append(cmd_list[4:])
            import subprocess
            key = " ".join(cmd_list[4:])
            out = next((v for k, v in self.answers.items() if key.startswith(k)), None)
            return subprocess.CompletedProcess(cmd_list, 0 if out is not None else 1, stdout=out or "", stderr="")
        self.update(run_silent_cmd=run, ADB_BIN="adb", TARGET="1.2.3.4:5555", speak=self.said.append, log_debug=lambda c, e: self.said.append(f"[LOG {c}] {e!r}"),
                    log_audit=lambda *a: None, open_phone_app=lambda k: self.opened.append((k, self["PHONE_APPS"].get(k))), _CALL_PKGS=tuple(call_pkgs),
                    PHONE_APPS=dict(apps or {"camera": "com.sec.android.app.camera", "calculator": "com.sec.android.app.popupcalculator", "whatsapp": "com.whatsapp"}))


def _pkgs(*names):
    return "".join(f"package:{n}\n" for n in names)


class TestPhoneCompat(unittest.TestCase):
    def setUp(self):
        from jarvis_addons import phone_compat
        phone_compat._cache.clear()
        self.pc = phone_compat

    def test_samsung_phone_is_left_exactly_as_it_was(self):
        h = FakePhone({"pm list packages": _pkgs("com.sec.android.app.camera", "com.sec.android.app.popupcalculator", "com.whatsapp"),
                       "wm size": "Physical size: 1080x2340\n", "getprop ro.product.manufacturer": "samsung\n"})
        self.assertTrue(self.pc.install(h))
        self.assertFalse(self.pc.install(h))                                                 # idempotent
        h["open_phone_app"]("camera")
        h["open_phone_app"]("whatsapp")
        self.assertEqual(h.opened, [("camera", "com.sec.android.app.camera"), ("whatsapp", "com.whatsapp")])
        h["run_silent_cmd"](["adb", "-s", "t", "shell"] + self.pc.UNLOCK_SWIPE)
        self.assertEqual(h.ran[-1], self.pc.UNLOCK_SWIPE)                                    # the unlock swipe is not touched on a normal screen
        self.assertEqual(h["_CALL_PKGS"], ("com.samsung.android.incallui",))

    def test_other_brand_uses_its_own_camera_and_calculator(self):
        h = FakePhone({"pm list packages": _pkgs("com.google.android.GoogleCamera", "com.miui.calculator", "com.whatsapp"),
                       "wm size": "Physical size: 1080x2400\n", "getprop ro.product.manufacturer": "Xiaomi\n"})
        self.pc.install(h)
        h["open_phone_app"]("camera")
        h["open_phone_app"]("calculator")
        self.assertEqual(h.opened, [("camera", "com.google.android.GoogleCamera"), ("calculator", "com.miui.calculator")])
        h["run_silent_cmd"](["adb", "-s", "t", "shell", "dumpsys", "notification"])
        self.assertIn("com.oneplus.dialer", h["_CALL_PKGS"])                                 # more dialer apps are recognised on a non-Samsung phone

    def test_unknown_brand_falls_back_to_the_standard_android_intent(self):
        h = FakePhone({"pm list packages": _pkgs("com.whatsapp", "com.brandx.cam"), "am start -a android.media.action.STILL_IMAGE_CAMERA": "Starting: Intent { act=... }\n"})
        self.pc.install(h)
        h["open_phone_app"]("camera")
        self.assertEqual(h.opened, [])                                                       # the original (Samsung package) launch was not attempted
        self.assertEqual(h.said, ["Camera opened on your phone, Boss."])

    def test_unreachable_phone_changes_nothing(self):
        h = FakePhone({})
        self.pc.install(h)
        h["open_phone_app"]("camera")
        self.assertEqual(h.opened, [("camera", "com.sec.android.app.camera")])               # exactly the old behaviour (it reports the failure itself)

    def test_unlock_swipe_is_scaled_only_on_a_small_screen(self):
        self.assertIsNone(self.pc.scaled_unlock_swipe((1080, 2340)))
        self.assertIsNone(self.pc.scaled_unlock_swipe((720, 1600)))
        self.assertIsNone(self.pc.scaled_unlock_swipe(None))
        self.assertEqual(self.pc.scaled_unlock_swipe((480, 854)), ["input", "swipe", "240", "640", "240", "213", "200"])
        h = FakePhone({"wm size": "Physical size: 480x854\n", "getprop ro.product.manufacturer": "samsung\n"})
        self.pc.install(h)
        h["run_silent_cmd"](["adb", "-s", "t", "shell"] + self.pc.UNLOCK_SWIPE)
        self.assertEqual(h.ran[-1], ["input", "swipe", "240", "640", "240", "213", "200"])

    def test_missing_host_functions_never_break_install(self):
        import jarvis_addons
        self.assertEqual(jarvis_addons.install({"log_debug": lambda c, e: None}), [])
