"""Offline tests for pattern-lock support (a scripted phone; no real device). Run:
venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_pattern_lock -v"""
import contextlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import pattern_lock as pl  # noqa: E402

GRID = {"resource-id": "com.android.systemui:id/lockPatternView", "class": "android.view.View", "bounds": "[90,1200][990,2100]"}


class Phone(dict):
    """Scripted phone. `locked` flips to False only when the RIGHT gesture arrives."""

    def __init__(self, cfg=None, sdk="34", nodes=(GRID,), right="14789", reports_state=True, screen="ON"):
        super().__init__()
        self.cfg, self.sent, self.said, self.audits, self.orig_calls = dict(cfg or {}), [], [], [], 0
        self.locked, self.sdk, self.nodes, self.reports_state = True, sdk, list(nodes), reports_state
        want = pl.gesture_command(pl.path_points(pl.parse_pattern(right), pl.grid_points(pl.parse_bounds(GRID["bounds"]))))

        def run(cmd_list, timeout=None):
            args = cmd_list[4:]
            self.sent.append(args)
            out = ""
            if args[:2] == ["getprop", "ro.build.version.sdk"]:
                out = self.sdk + "\n"
            elif args[:2] == ["dumpsys", "window"]:
                out = ("KeyguardStateMonitor\n  mIsShowing=" + ("true" if self.locked else "false") + "\n") if self.reports_state else "nothing useful\n"
            elif args[:2] == ["dumpsys", "power"]:
                out = "mWakefulness=Awake\n"
            elif args[:2] == ["dumpsys", "display"]:
                out = "  mScreenState=" + screen + "\n"
            elif args[:2] == ["wm", "size"]:
                out = "Physical size: 1080x2340\n"
            elif args and args[0].startswith("input motionevent"):
                if args[0] == want:
                    self.locked = False
            return subprocess.CompletedProcess(cmd_list, 0, stdout=out, stderr="")

        def orig_unlock():
            self.orig_calls += 1
        self.update(run_silent_cmd=run, ADB_BIN="adb", TARGET="t", get_config=lambda k, d=None: self.cfg.get(k, d), set_config=self.cfg.__setitem__,
                    get_ui_nodes=lambda: list(self.nodes), speak=self.said.append, log_debug=lambda c, e: self.said.append("[LOG %s] %r" % (c, e)),
                    log_audit=lambda a, r, s: self.audits.append((a, s)), unlock_phone=orig_unlock, require_phone_connection=lambda what: True)

    def gestures(self):
        return [a[0] for a in self.sent if a and a[0].startswith("input motionevent")]


class TestPatternMaths(unittest.TestCase):
    def test_pattern_parsing(self):
        self.assertEqual(pl.parse_pattern("14789"), [1, 4, 7, 8, 9])
        self.assertEqual(pl.parse_pattern("1 4 7 8 9"), [1, 4, 7, 8, 9])
        for bad in ("123", "1123", "1230", "12a4", "", None, "1234567891"):
            self.assertIsNone(pl.parse_pattern(bad), bad)

    def test_dots_are_the_centres_of_a_3x3_grid_read_from_the_screen(self):
        pts = pl.grid_points((90, 1200, 990, 2100))
        self.assertEqual((pts[1], pts[5], pts[9], pts[3], pts[7]), ((240, 1350), (540, 1650), (840, 1950), (840, 1350), (240, 1950)))

    def test_gesture_is_one_finger_down_moves_then_up_through_every_dot(self):
        pts = pl.grid_points((90, 1200, 990, 2100))
        path = pl.path_points([1, 4, 7, 8], pts, steps=3)
        self.assertEqual((path[0], path[3], path[6], path[9]), (pts[1], pts[4], pts[7], pts[8]))
        cmd = pl.gesture_command(path)
        self.assertTrue(cmd.startswith("input motionevent DOWN 240 1350; input motionevent MOVE 240 1450;"), cmd[:80])
        self.assertTrue(cmd.endswith("input motionevent UP 540 1950"), cmd[-40:])
        self.assertEqual((cmd.count("DOWN"), cmd.count("UP"), cmd.count("MOVE")), (1, 1, 9))

    def test_reading_the_phone(self):
        self.assertEqual(pl.credential_type("  CredentialType: PATTERN\n"), "pattern")
        self.assertEqual(pl.credential_type("  CredentialType: PASSWORD\n"), "password")
        self.assertIsNone(pl.credential_type("nothing"))
        self.assertEqual((pl.locked_state("mIsShowing=true"), pl.locked_state("x isKeyguardShowing=false"), pl.locked_state("")), (True, False, None))
        self.assertEqual(pl.find_pattern_view([{"class": "android.widget.TextView", "bounds": "[0,0][100,100]"}, GRID]), (90, 1200, 990, 2100))
        self.assertIsNone(pl.find_pattern_view([{"class": "android.widget.TextView", "bounds": "[0,0][500,500]"}]))


class TestUnlock(unittest.TestCase):
    def test_pin_and_password_phones_use_the_original_unlock_untouched(self):
        for cfg in ({}, {"phone_lock_type": "pin"}, {"phone_lock_type": "password", "phone_pattern": "14789"}):
            p = Phone(cfg)
            self.assertTrue(pl.install(p))
            self.assertFalse(pl.install(p))
            p["unlock_phone"]()
            self.assertEqual((p.orig_calls, p.gestures()), (1, []), cfg)

    def test_right_pattern_unlocks_and_is_verified(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "14789"})
        pl.install(p)
        p["unlock_phone"]()
        self.assertEqual((p.orig_calls, len(p.gestures()), p.locked), (0, 1, False))
        self.assertEqual((p.said, p.audits, p.cfg["pattern_fail_count"]), (["Phone unlocked successfully, Boss."], [("Phone Screen Unlock", "SUCCESS")], "0"))

    def test_wrong_pattern_is_reported_and_stops_after_two_failures(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "12369"})
        pl.install(p)
        for _ in range(4):
            p["unlock_phone"]()
        self.assertEqual(len(p.gestures()), 2)                                               # the 3rd and 4th command never touched the lock screen
        self.assertTrue(p.locked)
        self.assertIn("still locked", p.said[0])
        self.assertIn("stopped before the phone locks itself out", p.said[-1])
        self.assertEqual([s for _, s in p.audits], ["FAIL"] * 4)

    def test_grid_not_visible_means_no_guessing(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "14789"}, nodes=({"class": "android.widget.TextView", "bounds": "[0,0][900,900]"},))
        pl.install(p)
        p["unlock_phone"]()
        self.assertEqual(p.gestures(), [])
        self.assertIn("did not guess", p.said[0])
        self.assertEqual(p.cfg.get("pattern_fail_count", "0"), "0")                          # not seeing the grid is not a failed attempt

    def test_old_android_and_missing_pattern_are_refused_without_touching_the_phone(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "14789"}, sdk="28")
        pl.install(p)
        p["unlock_phone"]()
        self.assertEqual(p.gestures(), [])
        self.assertIn("too old", p.said[0])
        p2 = Phone({"phone_lock_type": "pattern"})
        pl.install(p2)
        p2["unlock_phone"]()
        self.assertEqual((p2.gestures(), p2.sent), ([], []))

    def test_screen_that_stays_off_is_reported_and_is_not_a_failed_attempt(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "14789"}, screen="OFF")          # seen for real: proximity sensor covered
        pl.install(p)
        p["unlock_phone"]()
        self.assertEqual(p.gestures(), [])
        self.assertIn("screen did not turn on", p.said[0])
        self.assertEqual(p.cfg.get("pattern_fail_count", "0"), "0")
        self.assertEqual((pl.display_on("mScreenState=ON"), pl.display_on("mScreenState=OFF"), pl.display_on("")), (True, False, None))

    def test_phone_that_does_not_report_its_state_is_said_so_not_faked(self):
        p = Phone({"phone_lock_type": "pattern", "phone_pattern": "14789"}, reports_state=False)
        pl.install(p)
        p["unlock_phone"]()
        self.assertEqual(len(p.gestures()), 1)
        self.assertIn("does not tell me whether it opened", p.said[0])
        self.assertEqual(p.audits, [("Phone Screen Unlock", "UNVERIFIED")])


class TestCli(unittest.TestCase):
    def test_set_and_type_write_only_this_folders_database_and_never_print_the_pattern(self):
        tmp = tempfile.mkdtemp(prefix="jarvis_pattern_test_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        db = os.path.join(tmp, "j.db")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(pl.main(["--set", "3", "6", "9", "8", "7"], db=db), 0)
            self.assertEqual(pl.main(["--set", "112"], db=db), 2)
        self.assertNotIn("36987", buf.getvalue())
        con = sqlite3.connect(db)
        cfg = dict(con.execute("SELECT key, val FROM config").fetchall())
        con.close()
        self.assertEqual(cfg, {"phone_pattern": "36987", "phone_lock_type": "pattern", "pattern_fail_count": "0"})
        with contextlib.redirect_stdout(buf):
            self.assertEqual(pl.main(["--type", "pin"], db=db), 0)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT val FROM config WHERE key='phone_lock_type'").fetchone()[0], "pin")
        con.close()


if __name__ == "__main__":
    unittest.main()
