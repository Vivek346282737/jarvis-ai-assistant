"""Offline tests for automatic phone linking (adb and the network are scripted). Run:
venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_phone_link -v"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import phone_link as pl  # noqa: E402


class TestParsing(unittest.TestCase):
    def test_mdns_lines(self):
        text = ("List of discovered mdns services\n"
                "adb-R5CT123\t_adb-tls-connect._tcp\t192.168.1.20:37215\n"
                "adb-R5CT123\t_adb-tls-pairing._tcp\t192.168.1.20:40111\n"
                "adb-OLD\t_adb._tcp\t192.168.1.30:5555\n"
                "printer\t_ipp._tcp\t192.168.1.40:631\n")
        got = pl.parse_mdns(text)
        self.assertEqual([(g["kind"], g["addr"]) for g in got], [("connect", "192.168.1.20:37215"), ("pairing", "192.168.1.20:40111"), ("classic", "192.168.1.30:5555")])
        self.assertEqual(pl.parse_mdns("List of discovered mdns services\n"), [])

    def test_devices(self):
        text = "List of devices attached\n192.168.1.23:5555\tdevice\nR5CT123ABC\tunauthorized\n\n"
        self.assertEqual(pl.parse_devices(text), {"192.168.1.23:5555": "device", "R5CT123ABC": "unauthorized"})
        self.assertEqual(pl.parse_devices("List of devices attached\n"), {})          # the header line is never a device

    def test_only_a_home_sized_network_is_swept_and_never_this_pc_itself(self):
        hosts = pl.subnet_hosts("192.168.1.2")
        self.assertEqual((len(hosts), hosts[0], hosts[-1]), (253, "192.168.1.1", "192.168.1.254"))
        self.assertNotIn("192.168.1.2", hosts)
        self.assertEqual(pl.subnet_hosts("10.0.0.5", "255.255.0.0"), [])                # a big network: not swept
        self.assertEqual(pl.subnet_hosts("127.0.0.1"), [])
        self.assertEqual(pl.subnet_hosts("169.254.3.4"), [])
        self.assertEqual(len(pl.subnet_hosts("192.168.5.130", "255.255.255.128")), 125)

    def test_sweep_reports_only_hosts_with_the_port_open(self):
        self.assertEqual(pl.sweep(["192.168.1.%d" % i for i in range(1, 30)], check=lambda h, p: h.endswith((".23", ".20"))), ["192.168.1.20:5555", "192.168.1.23:5555"])


class FakeAdb:
    """Scripted adb + network for link()."""

    def __init__(self, devices=None, mdns="", models=None, connectable=None, sweep_hits=(), pair_ok=True):
        self.devices = dict(devices or {})
        self.mdns, self.models, self.connectable, self.sweep_hits, self.pair_ok = mdns, dict(models or {}), dict(connectable or {}), list(sweep_hits), pair_ok
        self.calls = []
        self.dump = "ADB MANAGER STATE\n  user_keys=PCKEYPCKEYPCKEYPCKEYPCKEYPCKEYPCKEYPCKEYPCKEYPCKEYPCKEY pc@host\n"          # 'Always allow' was ticked
        self.timeout_ms = "0"                                                            # the expiry is off

    def __call__(self, *args, timeout=10):
        self.calls.append(args)
        if args[0] == "-s" and args[2:4] == ("shell", "dumpsys"):
            return 0, self.dump
        if args[0] == "-s" and args[2:5] == ("shell", "settings", "get"):
            return 0, self.timeout_ms
        if args[0] == "-s" and args[2:5] == ("shell", "settings", "put"):
            self.timeout_ms = args[-1]
            return 0, ""
        if args[:2] == ("mdns", "services"):
            return 0, "List of discovered mdns services\n" + self.mdns
        if args[0] == "devices":
            return 0, "List of devices attached\n" + "".join(f"{k}\t{v}\n" for k, v in self.devices.items())
        if args[0] == "connect":
            if args[1] in self.connectable:
                self.devices[args[1]] = self.connectable[args[1]]
            return 0, ""
        if args[0] == "disconnect":
            self.devices.pop(args[1], None)
            return 0, ""
        if args[0] == "pair":
            if self.pair_ok:
                self.mdns = self.mdns.replace("pairing", "connect")
                return 0, "Successfully paired to " + args[1]
            return 1, "Failed: Wrong password or connection was dropped."
        if args[0] == "-s" and args[2:4] == ("shell", "getprop"):
            maker, model = self.models.get(args[1], ("", ""))
            return (0, maker if "manufacturer" in args[4] else model) if model else (1, "")
        if args[0] == "-s" and args[2:4] == ("shell", "ip"):
            return 0, "    inet 192.168.1.77/24 brd 192.168.1.255 scope global wlan0"
        if args[0] == "-s" and args[2] == "tcpip":
            self.connectable["192.168.1.77:5555"] = "device"
            self.models["192.168.1.77:5555"] = self.models[args[1]]
            return 0, "restarting in TCP mode port: 5555"
        return 0, ""


class TestLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis_link_test_")
        self.db = os.path.join(self.tmp, "j.db")
        self.said, self.asked = [], []
        self.keep = (pl.adb, pl.sweep, pl.local_ipv4, pl.os.path.isfile)
        self.keep_key = pl.pc_key
        pl.pc_key = lambda: ("PCKEY" * 11, "pc@host")
        self.addCleanup(lambda: setattr(pl, "pc_key", self.keep_key))
        self.addCleanup(self._restore)
        pl.os.path.isfile = lambda p: True if p == pl.ADB else self.keep[3](p)
        pl.local_ipv4 = lambda: ["192.168.1.2"]

    def _restore(self):
        pl.adb, pl.sweep, pl.local_ipv4, pl.os.path.isfile = self.keep
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _use(self, fake):
        pl.adb = fake
        pl.sweep = lambda hosts, port=5555, check=None: list(fake.sweep_hits)
        return fake

    def _answers(self, *answers):
        it = iter(answers)

        def ask(prompt):
            self.asked.append(prompt)
            return next(it, "")
        return ask

    def _target(self):
        if not os.path.exists(self.db):
            return None
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT val FROM config WHERE key='adb_target'").fetchone()
        con.close()
        return row[0] if row else None

    def test_phone_found_on_the_wifi_is_linked_after_the_owner_confirms_no_ip_typed(self):
        self._use(FakeAdb(sweep_hits=["192.168.1.23:5555"], connectable={"192.168.1.23:5555": "device"}, models={"192.168.1.23:5555": ("samsung", "SM-A000X")}))
        got = pl.link(self.db, ask=self._answers("y"), say=self.said.append)
        self.assertEqual((got, self._target()), ("192.168.1.23:5555", "192.168.1.23:5555"))
        self.assertEqual(len(self.asked), 1)                                             # the ONLY question: "is this your phone?"
        self.assertTrue(any("samsung SM-A000X" in s for s in self.said))

    def test_someone_elses_device_is_not_linked_when_the_owner_says_no(self):
        fake = self._use(FakeAdb(sweep_hits=["192.168.1.50:5555"], connectable={"192.168.1.50:5555": "device"}, models={"192.168.1.50:5555": ("Sony", "BRAVIA TV")}))
        got = pl.link(self.db, ask=self._answers("n", ""), say=self.said.append)
        self.assertEqual((got, self._target()), (None, None))
        self.assertIn(("disconnect", "192.168.1.50:5555"), fake.calls)

    def test_wireless_debugging_phone_announced_by_mdns_is_preferred(self):
        self._use(FakeAdb(mdns="adb-X\t_adb-tls-connect._tcp\t192.168.1.20:37215\n", connectable={"192.168.1.20:37215": "device"},
                          models={"192.168.1.20:37215": ("Google", "Pixel 8")}, sweep_hits=[]))
        self.assertEqual(pl.link(self.db, ask=self._answers(""), say=self.said.append), "192.168.1.77:5555")     # found on its random Wireless-debugging port, then moved to the fixed port

    def test_pairing_only_asks_for_the_code_shown_on_the_phone(self):
        fake = self._use(FakeAdb(mdns="adb-X\t_adb-tls-pairing._tcp\t192.168.1.20:40111\n", connectable={"192.168.1.20:40111": "device"}, models={"192.168.1.20:40111": ("Google", "Pixel 8")}))
        got = pl.link(self.db, ask=self._answers("123456", "y"), say=self.said.append)
        self.assertIn(("pair", "192.168.1.20:40111", "123456"), fake.calls)
        self.assertEqual(got, "192.168.1.77:5555")                                           # paired, then moved to the fixed port
        self.assertIn("6-digit code", self.asked[0])

    def test_usb_phone_is_moved_to_wifi_so_the_cable_can_go(self):
        self._use(FakeAdb(devices={"R5CT123ABC": "device"}, models={"R5CT123ABC": ("samsung", "SM-A546E")}))
        self.assertEqual(pl.link(self.db, ask=self._answers("y"), say=self.said.append), "192.168.1.77:5555")
        self.assertEqual(self._target(), "192.168.1.77:5555")

    def test_unauthorized_phone_tells_the_owner_to_tap_allow_and_links_nothing(self):
        self._use(FakeAdb(devices={"R5CT123ABC": "unauthorized"}))
        self.assertIsNone(pl.link(self.db, ask=self._answers(""), say=self.said.append))
        self.assertTrue(any("ALLOW" in s for s in self.said))
        self.assertIsNone(self._target())

    def test_nothing_found_falls_back_to_typing_the_address(self):
        self._use(FakeAdb(connectable={"192.168.1.33:41000": "device"}, models={"192.168.1.33:41000": ("OnePlus", "CPH2581")}))
        self.assertEqual(pl.link(self.db, ask=self._answers("192.168.1.33:41000"), say=self.said.append), "192.168.1.33:41000")
        self.assertIn("IP:PORT", self.asked[-1])

    def test_auto_mode_never_asks_and_never_links_a_wrong_answer(self):
        self._use(FakeAdb())
        self.assertIsNone(pl.link(self.db, ask=self._answers(), say=self.said.append, assume_yes=True))
        self.assertEqual(self.asked, [])

    def test_a_device_that_does_not_answer_as_a_phone_is_skipped(self):
        self._use(FakeAdb(sweep_hits=["192.168.1.60:5555"], connectable={"192.168.1.60:5555": "device"}, models={}))
        self.assertIsNone(pl.link(self.db, ask=self._answers(""), say=self.said.append))


if __name__ == "__main__":
    unittest.main()
