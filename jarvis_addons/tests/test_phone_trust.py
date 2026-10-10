"""USB-cable linking: is the trust permanent, and does the Wi-Fi link come back after a phone restart? (scripted phone)
Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_phone_trust -v"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import phone_link as pl  # noqa: E402
from jarvis_addons.tests.test_phone_link import FakeAdb, TestLink  # noqa: E402

KEY, NAME = "PCKEY" * 11, "pc@host"


class TestTrustReading(unittest.TestCase):
    def test_permanent_trust_is_read_from_the_phones_own_list(self):
        saved = f"ADB MANAGER STATE\n  connected_to_adb=true\n  user_keys={KEY} {NAME}\n  keystore=...\n"
        self.assertTrue(pl.trust_in_dump(saved, KEY, NAME))
        other = "ADB MANAGER STATE\n  connected_to_adb=true\n  user_keys=OTHERKEYOTHERKEY someone@else\n"
        self.assertFalse(pl.trust_in_dump(other, KEY, NAME))                             # allowed for now, but 'Always allow' not ticked
        self.assertFalse(pl.trust_in_dump("ADB MANAGER STATE\n  user_keys=\n", KEY, NAME))
        self.assertIsNone(pl.trust_in_dump("", KEY, NAME))                               # an old phone that does not report it
        self.assertIsNone(pl.trust_in_dump("Can't find service: adb", KEY, NAME))


class TestLinkTellsTheOwner(TestLink):
    def _usb(self, **kw):
        fake = FakeAdb(devices={"R5CT123ABC": "device"}, models={"R5CT123ABC": ("samsung", "SM-A546E")})
        for k, v in kw.items():
            setattr(fake, k, v)
        return self._use(fake)

    def test_always_allow_ticked_and_no_expiry_needs_no_extra_question(self):
        self._usb()
        self.assertEqual(pl.link(self.db, ask=self._answers("y"), say=self.said.append), "192.168.1.77:5555")
        self.assertEqual(len(self.asked), 1)
        text = "\n".join(self.said)
        self.assertIn("trusts this PC permanently", text)
        self.assertIn("unplug the cable", text)
        self.assertIn("After the PHONE is restarted", text)

    def test_always_allow_not_ticked_is_spotted_and_explained(self):
        self._usb(dump="ADB MANAGER STATE\n  connected_to_adb=true\n  user_keys=\n")
        pl.link(self.db, ask=self._answers("y"), say=self.said.append)
        text = "\n".join(self.said)
        self.assertIn("only for NOW", text)
        self.assertIn("Always allow from this computer", text)

    def test_seven_day_expiry_is_explained_and_switched_off_only_when_the_owner_agrees(self):
        fake = self._usb(timeout_ms="null")
        pl.link(self.db, ask=self._answers("y", "y"), say=self.said.append)
        self.assertIn("forgets a PC it has not seen for 7 days", "\n".join(self.said))
        self.assertEqual(fake.timeout_ms, "0")
        fake2 = self._usb(timeout_ms="604800000")
        self.said.clear()
        pl.link(self.db, ask=self._answers("y", "n"), say=self.said.append)
        self.assertEqual(fake2.timeout_ms, "604800000")                                  # the owner said no: the phone's setting is not touched

    def test_auto_mode_never_changes_a_phone_setting(self):
        fake = self._use(FakeAdb(sweep_hits=["192.168.1.23:5555"], connectable={"192.168.1.23:5555": "device"}, models={"192.168.1.23:5555": ("samsung", "SM-A000X")}))
        fake.timeout_ms = "null"
        pl.link(self.db, ask=self._answers(), say=self.said.append, assume_yes=True)
        self.assertEqual(fake.timeout_ms, "null")
        self.assertFalse(any(c[2:5] == ("shell", "settings", "put") for c in fake.calls))


class Host(dict):
    """A running JARVIS, just after the PHONE was restarted: the saved Wi-Fi address is dead."""

    def __init__(self, saved="192.168.1.23:5555", devices=None, wifi="192.168.1.23", tcpip_works=True):
        super().__init__()
        self.cfg, self.devices, self.ran, self.logs = {"adb_target": saved}, dict(devices or {}), [], []

        def run(cmd, timeout=None):
            self.ran.append(cmd[1:])
            out = ""
            if "addr" in cmd and "wlan0" in cmd:
                out = f"    inet {wifi}/24 brd 192.168.1.255 scope global wlan0" if wifi else ""
            if "connect" in cmd and tcpip_works and any("tcpip" in r for r in self.ran):
                self.devices[cmd[-1]] = "device"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        self.update(ADB_BIN="adb", TARGET="R5CT123ABC", run_silent_cmd=run, _adb_state=lambda: dict(self.devices), get_config=lambda k, d=None: self.cfg.get(k, d),
                    set_config=self.cfg.__setitem__, file_log=self.logs.append, log_debug=lambda c, e: self.logs.append(f"[DEBUG {c}] {e!r}"),
                    ensure_adb_connected=lambda: True)


class TestWifiComesBackAfterAPhoneRestart(unittest.TestCase):
    def setUp(self):
        import time
        self.keep = time.sleep
        time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(time, "sleep", self.keep))

    def test_cable_plugged_in_after_a_restart_restores_the_wifi_link(self):
        h = Host(devices={"R5CT123ABC": "device"})
        self.assertEqual(pl.restore_wifi(h, {}), "192.168.1.23:5555")
        self.assertEqual(h["TARGET"], "192.168.1.23:5555")
        self.assertIn(["-s", "R5CT123ABC", "tcpip", "5555"], h.ran)
        self.assertEqual(h.cfg["adb_target"], "192.168.1.23:5555")

    def test_a_new_ip_address_from_the_router_is_saved(self):
        h = Host(devices={"R5CT123ABC": "device"}, wifi="192.168.1.44")
        self.assertEqual(pl.restore_wifi(h, {}), "192.168.1.44:5555")
        self.assertEqual(h.cfg["adb_target"], "192.168.1.44:5555")

    def test_nothing_is_done_when_it_is_not_needed_or_not_safe(self):
        for h, why in ((Host(devices={"192.168.1.23:5555": "device", "R5CT123ABC": "device"}), "Wi-Fi already works"),
                       (Host(devices={}), "no cable"),
                       (Host(devices={"AAA": "device", "BBB": "device"}), "two phones on cables: which one?"),
                       (Host(devices={"R5CT123ABC": "unauthorized"}), "the phone has not allowed this PC"),
                       (Host(saved="192.168.1.20:37215", devices={"R5CT123ABC": "device"}), "a Wireless-debugging phone, not a 5555 one"),
                       (Host(devices={"R5CT123ABC": "device"}, wifi=None), "the phone is not on Wi-Fi"),
                       (Host(devices={"R5CT123ABC": "device"}, tcpip_works=False), "the phone did not come up on Wi-Fi")):
            self.assertIsNone(pl.restore_wifi(h, {}), why)
            self.assertEqual(h["TARGET"], "R5CT123ABC", why)                              # JARVIS keeps working over the cable

    def test_a_cable_that_cannot_help_is_not_retried_every_few_seconds(self):
        h = Host(devices={"R5CT123ABC": "device"}, tcpip_works=False)
        state = {}
        pl.restore_wifi(h, state)
        n = len(h.ran)
        pl.restore_wifi(h, state)
        self.assertEqual(len(h.ran), n)

    def test_installed_hook_keeps_the_original_reconnect_and_adds_the_restore(self):
        h = Host(devices={"R5CT123ABC": "device"})
        self.assertTrue(pl.install(h))
        self.assertFalse(pl.install(h))
        self.assertTrue(h["ensure_adb_connected"]())
        self.assertEqual(h["TARGET"], "192.168.1.23:5555")
        self.assertTrue(any("Wi-Fi debugging switched back on" in s for s in h.logs))
        h2 = Host(devices={})
        h2["ensure_adb_connected"] = lambda: False
        pl.install(h2)
        self.assertFalse(h2["ensure_adb_connected"]())                                    # nothing to restore: the original answer is passed on


del TestLink                                                                              # imported only as a base class - do not run its tests twice

if __name__ == "__main__":
    unittest.main()
