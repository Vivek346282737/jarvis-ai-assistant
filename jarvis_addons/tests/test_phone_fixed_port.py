"""The phone link that stays: fixed port 5555, found again by itself. Run:
venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_phone_fixed_port -v"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import phone_link as pl  # noqa: E402

SERIAL = "RZTEST0001"


class Host(dict):
    """A running JARVIS with a fake phone. `listening` = addresses the phone's adb service answers on right now."""

    def __init__(self, target, saved, listening, wifi="192.168.1.23", keeps_5555=True, serials=None, cfg=None):
        super().__init__()
        self.cfg, self.devices, self.ran, self.logs = dict({"adb_target": saved}, **(cfg or {})), {}, [], []
        self.listening, self.serials = set(listening), dict(serials or {})
        if target:
            self.devices[target] = "device"

        def run(cmd, timeout=None):
            a, out = cmd[1:], ""
            self.ran.append(a)
            if "wlan0" in a:
                out = f"    inet {wifi}/24 brd 192.168.1.255 scope global wlan0" if wifi else ""
            elif "ro.serialno" in a:
                out = self.serials.get(a[1], SERIAL)
            elif "tcpip" in a:
                self.devices.pop(a[1], None)                                  # the phone's adb service restarts
                self.listening = {f"{wifi}:5555"} if keeps_5555 else set()
            elif a[0] == "connect" and a[1] in self.listening:
                self.devices[a[1]] = "device"
            elif a[0] == "disconnect":
                self.devices.pop(a[1], None)
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        self.update(ADB_BIN="adb", TARGET=target or saved, run_silent_cmd=run, _adb_state=lambda: dict(self.devices), get_config=lambda k, d=None: self.cfg.get(k, d),
                    set_config=self.cfg.__setitem__, file_log=self.logs.append, log_debug=lambda c, e: self.logs.append(f"[DEBUG {c}] {e!r}"))

    def probe(self, ip, port):
        return f"{ip}:{port}" in self.listening


class NoSleep(unittest.TestCase):
    def setUp(self):
        import time
        keep = time.sleep
        time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(time, "sleep", keep))


class TestFixedPort(NoSleep):
    def test_addresses(self):
        self.assertEqual(pl.fixed_addr("192.168.1.23:35427"), "192.168.1.23:5555")
        self.assertIsNone(pl.fixed_addr("adb-RZTEST0001-QhZxcb._adb-tls-connect._tcp"))
        self.assertTrue(pl.is_fixed("192.168.1.23:5555"))
        self.assertFalse(pl.is_fixed("192.168.1.23:35427"))
        self.assertEqual(pl.reconnect_candidates("192.168.1.23:35427", "192.168.1.30"), ["192.168.1.23:5555", "192.168.1.30:5555"])
        self.assertEqual(pl.reconnect_candidates("192.168.1.23:5555", "192.168.1.23"), ["192.168.1.23:5555"])
        self.assertEqual(pl.reconnect_candidates("", ""), [])

    def test_a_random_wireless_debugging_port_is_replaced_by_the_fixed_port(self):
        """What broke on 2026-10-05: the random port 35427 was saved and later dead for ever."""
        h = Host(target="192.168.1.23:35427", saved="192.168.1.23:35427", listening={"192.168.1.23:35427"})
        self.assertEqual(pl.pin_fixed_port(h, {}), "192.168.1.23:5555")
        self.assertEqual((h["TARGET"], h.cfg["adb_target"], h.cfg["adb_last_ip"], h.cfg["adb_phone_serial"]), ("192.168.1.23:5555", "192.168.1.23:5555", "192.168.1.23", SERIAL))
        self.assertIn(["-s", "192.168.1.23:35427", "tcpip", "5555"], h.ran)
        self.assertTrue(any("fixed port 5555" in s for s in h.logs))

    def test_wireless_debugging_seen_by_adb_itself_is_moved_too(self):
        tls = "adb-RZTEST0001-QhZxcb._adb-tls-connect._tcp"
        h = Host(target=tls, saved="192.168.1.23:35427", listening=set())
        self.assertEqual(pl.pin_fixed_port(h, {}), "192.168.1.23:5555")

    def test_an_already_fixed_phone_is_left_alone_and_only_remembered(self):
        h = Host(target="192.168.1.23:5555", saved="192.168.1.23:5555", listening={"192.168.1.23:5555"})
        state = {}
        self.assertIsNone(pl.pin_fixed_port(h, state))
        self.assertFalse(any("tcpip" in r for r in h.ran))
        self.assertEqual((h.cfg["adb_phone_serial"], h.cfg["adb_last_ip"]), (SERIAL, "192.168.1.23"))
        n = len(h.ran)
        pl.pin_fixed_port(h, state)
        self.assertEqual(len(h.ran), n)                                       # asked once, not every 3 seconds

    def test_a_phone_that_cannot_keep_port_5555_is_not_restarted_again_and_again(self):
        h = Host(target="192.168.1.23:35427", saved="192.168.1.23:35427", listening={"192.168.1.23:35427"}, keeps_5555=False)
        state = {}
        self.assertIsNone(pl.pin_fixed_port(h, state))
        self.assertTrue(any("could not move" in s for s in h.logs))
        h.devices["192.168.1.23:35427"] = "device"
        n = len(h.ran)
        self.assertIsNone(pl.pin_fixed_port(h, state))
        self.assertEqual(len(h.ran), n)
        h2 = Host(target="192.168.1.23:35427", saved="192.168.1.23:35427", listening={"192.168.1.23:35427"}, wifi=None)
        self.assertIsNone(pl.pin_fixed_port(h2, {}))                          # not on Wi-Fi: nothing is touched
        self.assertFalse(any("tcpip" in r for r in h2.ran))


class TestFoundAgain(NoSleep):
    def test_the_fixed_port_is_tried_even_when_a_dead_random_port_is_what_was_saved(self):
        h = Host(target=None, saved="192.168.1.23:35427", listening={"192.168.1.23:5555"})
        self.assertEqual(pl.quick_reconnect(h, {}, probe=h.probe, is_alive=lambda ip: False), "192.168.1.23:5555")
        self.assertEqual((h["TARGET"], h.cfg["adb_target"]), ("192.168.1.23:5555", "192.168.1.23:5555"))
        self.assertTrue(any("back on its fixed port" in s for s in h.logs))

    def test_no_adb_process_is_started_while_the_phone_does_not_answer(self):
        h = Host(target=None, saved="192.168.1.23:5555", listening=set())
        state = {}
        for _ in range(5):
            self.assertIsNone(pl.quick_reconnect(h, state, probe=h.probe, is_alive=lambda ip: False, do_sweep=lambda: []))
        self.assertEqual(h.ran, [])

    def test_a_new_address_from_the_router_is_found_but_only_for_the_same_phone(self):
        cfg = {"adb_phone_serial": SERIAL, "adb_last_ip": "192.168.1.23"}
        h = Host(target=None, saved="192.168.1.23:5555", listening={"192.168.1.40:5555", "192.168.1.55:5555"}, cfg=cfg, serials={"192.168.1.40:5555": "SOMEONE-ELSE"})
        state = {}
        got = pl.quick_reconnect(h, state, probe=h.probe, is_alive=lambda ip: False, do_sweep=lambda: ["192.168.1.40:5555", "192.168.1.55:5555"])
        self.assertEqual(got, "192.168.1.55:5555")
        self.assertEqual((h.cfg["adb_target"], h.cfg["adb_last_ip"]), ("192.168.1.55:5555", "192.168.1.55"))
        self.assertIn(["disconnect", "192.168.1.40:5555"], h.ran)             # the stranger's device is let go
        h3 = Host(target=None, saved="192.168.1.23:5555", listening={"192.168.1.40:5555"}, cfg=cfg, serials={"192.168.1.40:5555": "SOMEONE-ELSE"})
        st, swept = {}, []
        for _ in range(3):
            self.assertIsNone(pl.quick_reconnect(h3, st, probe=h3.probe, is_alive=lambda ip: False, do_sweep=lambda: swept.append(1) or ["192.168.1.40:5555"]))
        self.assertEqual(len(swept), 1)                                       # the whole Wi-Fi is searched once per 5 minutes, not every time
        self.assertEqual(h3["TARGET"], "192.168.1.23:5555")

    def test_without_a_known_serial_the_wifi_is_never_searched(self):
        h = Host(target=None, saved="192.168.1.23:5555", listening={"192.168.1.40:5555"})
        self.assertIsNone(pl.quick_reconnect(h, {}, probe=h.probe, is_alive=lambda ip: False, do_sweep=lambda: self.fail("must not sweep")))

    def test_installed_hook_does_both(self):
        h = Host(target="192.168.1.23:35427", saved="192.168.1.23:35427", listening={"192.168.1.23:35427"})
        h["ensure_adb_connected"] = lambda: h["TARGET"] in h.devices
        self.assertTrue(pl.install(h))
        self.assertTrue(h["ensure_adb_connected"]())
        self.assertEqual(h["TARGET"], "192.168.1.23:5555")                    # connected on a random port -> fixed
        h.devices.clear()                                                     # the phone slept / left the Wi-Fi
        h.listening = set()
        keep, pl.port_open, keep_ping, pl.ping = pl.port_open, h.probe, pl.ping, lambda ip: False
        self.addCleanup(lambda: (setattr(pl, "port_open", keep), setattr(pl, "ping", keep_ping)))
        self.assertFalse(h["ensure_adb_connected"]())
        h.listening = {"192.168.1.23:5555"}                                   # ... and came back
        self.assertTrue(h["ensure_adb_connected"]())
        self.assertEqual(h["TARGET"], "192.168.1.23:5555")


class TestAfterAPhoneRestart(NoSleep):
    """Only Wireless debugging is on, with a random port; mDNS does not work on this router."""

    def host(self, **kw):
        cfg = {"adb_phone_serial": SERIAL, "adb_last_ip": "192.168.1.23"}
        return Host(target=None, saved="192.168.1.23:5555", listening={"192.168.1.23:41873"}, cfg=cfg, **kw)

    def test_the_random_port_is_found_by_a_scan_and_the_phone_goes_back_to_the_fixed_port(self):
        h = self.host()
        got = pl.quick_reconnect(h, {}, probe=h.probe, is_alive=lambda ip: True, scan=lambda ip: [39733, 41873], do_sweep=lambda: [])
        self.assertEqual(got, "192.168.1.23:5555")
        self.assertEqual((h["TARGET"], h.cfg["adb_target"]), ("192.168.1.23:5555", "192.168.1.23:5555"))
        self.assertIn(["-s", "192.168.1.23:41873", "tcpip", "5555"], h.ran)
        self.assertTrue(any("port scan" in s for s in h.logs))

    def test_no_scan_when_the_phone_is_not_on_the_wifi_and_only_one_scan_per_two_minutes(self):
        h, scans, pings = self.host(), [], []
        st = {}
        for _ in range(4):
            pl.quick_reconnect(h, st, probe=h.probe, is_alive=lambda ip: pings.append(ip) or False, scan=lambda ip: scans.append(ip) or [], do_sweep=lambda: [])
        self.assertEqual((len(pings), scans), (1, []))                        # one ping, and no scan because nothing answered
        h2, st2 = self.host(), {}
        h2.listening = set()                                                  # the phone is there but Wireless debugging is off
        for _ in range(4):
            self.assertIsNone(pl.quick_reconnect(h2, st2, probe=h2.probe, is_alive=lambda ip: True, scan=lambda ip: scans.append(ip) or [], do_sweep=lambda: []))
        self.assertEqual(scans, ["192.168.1.23"])

    def test_another_app_port_is_tried_once_and_another_phone_is_not_taken(self):
        h, st = self.host(), {}
        h.listening = set()
        for _ in range(2):
            st["scan_ts"] = 0
            pl.quick_reconnect(h, st, probe=h.probe, is_alive=lambda ip: True, scan=lambda ip: [39733], do_sweep=lambda: [])
        self.assertEqual(len([r for r in h.ran if r == ["connect", "192.168.1.23:39733"]]), 1)
        h2 = self.host(serials={"192.168.1.23:41873": "SOMEONE-ELSE"})
        self.assertIsNone(pl.quick_reconnect(h2, {}, probe=h2.probe, is_alive=lambda ip: True, scan=lambda ip: [41873], do_sweep=lambda: []))
        self.assertEqual(h2["TARGET"], "192.168.1.23:5555")

    def test_a_phone_that_refuses_the_fixed_port_still_works_on_the_found_port(self):
        h = self.host(keeps_5555=False)
        got = pl.quick_reconnect(h, {}, probe=h.probe, is_alive=lambda ip: True, scan=lambda ip: [41873], do_sweep=lambda: [])
        self.assertIsNone(got)                                                # this fake phone's adb service is gone after the failed move
        self.assertTrue(any("could not move" in s for s in h.logs))

    def test_on_battery_the_scan_runs_three_times_less_often(self):
        import time
        h, scans = self.host(), []
        h.listening = set()
        h["_perf"] = {"pace": "eco"}
        st = {"scan_ts": time.time() - pl.SCAN_EVERY * 2}
        pl.quick_reconnect(h, st, probe=h.probe, is_alive=lambda ip: True, scan=lambda ip: scans.append(1) or [], do_sweep=lambda: [])
        self.assertEqual(scans, [])
        h["_perf"]["pace"] = "normal"
        pl.quick_reconnect(h, st, probe=h.probe, is_alive=lambda ip: True, scan=lambda ip: scans.append(1) or [], do_sweep=lambda: [])
        self.assertEqual(scans, [1])


class TestHelper(unittest.TestCase):
    """JARVIS Link, the helper on the phone that switches Wireless debugging on again after a phone restart."""

    def _adb(self, answers, calls):
        def adb(*args, timeout=10):
            calls.append(args)
            for key, out in answers.items():
                if key in " ".join(args):
                    return 0, out
            return 0, ""
        keep = pl.adb
        pl.adb = adb
        self.addCleanup(lambda: setattr(pl, "adb", keep))

    def test_install_grants_the_permission_and_tells_the_helper_the_home_router(self):
        calls, said = [], []
        self._adb({"ip route get": "8.8.8.8 via 192.168.1.1 dev wlan0 src 192.168.1.23 uid 2000", "install -r": "Performing Streamed Install" + chr(10) + "Success",
                   "pm list packages": "package:" + pl.HELPER, "dumpsys package": "android.permission.WRITE_SECURE_SETTINGS: granted=true",
                   "deviceidle whitelist": "user," + pl.HELPER + ",10412", "dumpsys jobscheduler": "JOB #u0a412/1: " + pl.HELPER + "/.Keep"}, calls)
        self.assertTrue(pl.helper_install("192.168.1.23:5555", say=said.append))
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("pm grant " + pl.HELPER + " android.permission.WRITE_SECURE_SETTINGS" in c for c in flat))
        self.assertTrue(any(".START" in c and "--es gw 192.168.1.1" in c for c in flat))
        self.assertIn("192.168.1.1", said[-1])

    def test_nothing_is_installed_when_the_phone_is_not_on_wifi_or_refuses(self):
        calls, said = [], []
        self._adb({"ip route get": "RTNETLINK answers: Network is unreachable"}, calls)
        self.assertFalse(pl.helper_install("192.168.1.23:5555", say=said.append))
        self.assertFalse(any("install" in c for c in calls))
        calls2 = []
        self._adb({"ip route get": "8.8.8.8 via 192.168.1.1 dev wlan0", "install -r": "Failure [INSTALL_FAILED_VERIFICATION_FAILURE]"}, calls2)
        self.assertFalse(pl.helper_install("192.168.1.23:5555", say=said.append))
        self.assertFalse(any("grant" in c for c in calls2))


if __name__ == "__main__":
    unittest.main()
