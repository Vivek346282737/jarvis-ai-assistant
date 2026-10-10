"""Network discovery: parsing and classifying, with made-up answers (no real network is touched).
Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_network_discovery -v"""
import ipaddress
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import network_discovery as nd  # noqa: E402

ARP = """
Interface: 192.168.1.2 --- 0xf
  Internet Address      Physical Address      Type
  192.168.1.1           00-1a-2b-20-84-10     dynamic
  192.168.1.5           e2-00-00-33-4c-54     dynamic
  192.168.1.30          00-11-22-33-44-55     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
  239.255.255.250       01-00-5e-7f-ff-fa     static

Interface: 172.20.0.1 --- 0x2a
  172.20.0.7            00-15-5d-01-02-03     dynamic
"""
ROUTE = """
IPv4 Route Table
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.2     35
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
"""
OUI_CSV = 'Registry,Assignment,Organization Name,Organization Address\nMA-L,001A2B,"Realtek, Inc.",addr\nMA-L,001122,CIMSYS Inc,addr\nbad,line\n'


def dev(**kw):
    d = {"ip": "192.168.1.50", "mac": "00:11:22:33:44:55", "vendor": "", "role": "", "ports": [], "upnp": {}, "mdns": []}
    d.update(kw)
    return d


class TestParsing(unittest.TestCase):
    def test_arp_table(self):
        t = nd.parse_arp(ARP)
        self.assertEqual(t["192.168.1.2"], {"192.168.1.1": "00:1a:2b:20:84:10", "192.168.1.5": "e2:00:00:33:4c:54", "192.168.1.30": "00:11:22:33:44:55"})
        self.assertEqual(t["172.20.0.1"], {"172.20.0.7": "00:15:5d:01:02:03"})                    # another adapter stays separate
        self.assertEqual(nd.parse_arp(""), {})
        self.assertEqual(nd.parse_arp("garbage\n  not an address   xx-yy  dynamic"), {})

    def test_gateway_and_home_network(self):
        self.assertEqual(nd.parse_gateway(ROUTE), "192.168.1.1")
        self.assertIsNone(nd.parse_gateway("no routes"))
        self.assertEqual(str(nd.home_network("192.168.1.2")), "192.168.1.0/24")
        self.assertIsNone(nd.home_network("8.8.8.8"))                                             # never a public network
        self.assertIsNone(nd.home_network("127.0.0.1"))
        self.assertIsNone(nd.home_network("169.254.3.4"))

    def test_maker_lookup(self):
        table = nd.parse_oui_csv(OUI_CSV)
        self.assertEqual(table, {"001a2b": "Realtek, Inc.", "001122": "CIMSYS Inc"})
        self.assertEqual(nd.vendor("00:1a:2b:20:84:10", table), "Realtek, Inc.")
        self.assertEqual(nd.vendor("00:99:99:00:00:01", table), "unknown maker")
        self.assertEqual(nd.vendor("e2:00:00:33:4c:54", table), "private (randomised) address")   # never a made-up maker
        self.assertTrue(nd.is_private_mac("12:00:00:df:4b:a2"))
        self.assertFalse(nd.is_private_mac("00:1a:2b:20:84:10"))
        self.assertFalse(nd.is_private_mac("garbage"))

    def test_upnp_answers(self):
        r = nd.parse_ssdp("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLOCATION: http://192.168.1.30:9197/dmr\r\nSERVER: SHP, UPnP/1.0, Samsung UPnP SDK/1.0\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n")
        self.assertEqual(r, {"server": "SHP, UPnP/1.0, Samsung UPnP SDK/1.0", "st": "urn:schemas-upnp-org:device:MediaRenderer:1", "location": "http://192.168.1.30:9197/dmr"})
        x = nd.parse_upnp_xml("<root><device><deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType><friendlyName>[TV] Living Room</friendlyName>"
                              "<manufacturer>Samsung Electronics</manufacturer><modelName>UA43T5500</modelName></device></root>")
        self.assertEqual(x, {"name": "[TV] Living Room", "maker": "Samsung Electronics", "model": "UA43T5500", "kind": "urn:schemas-upnp-org:device:MediaRenderer:1"})
        self.assertEqual(nd.parse_upnp_xml("not xml"), {"name": "", "maker": "", "model": "", "kind": ""})

    def test_scan_phrases(self):
        for s in ("scan the network", "network scan karo", "scan my wifi", "kya devices mile", "kaun kaun se devices jude hai", "which devices are on my wifi",
                  "how many devices are connected", "devices on my network"):
            self.assertTrue(nd.SCAN_RX.search(s), s)
        for s in ("what time is it", "scan this document", "take a screenshot", "open device manager", "phone ki battery kitni hai", "connect to the tv"):
            self.assertFalse(nd.SCAN_RX.search(s), s)


class TestClassify(unittest.TestCase):
    def kind(self, **kw):
        return nd.classify(dev(**kw))[0]

    def test_roles_come_first(self):
        self.assertEqual(self.kind(role="self"), "this laptop")
        self.assertEqual(self.kind(role="router", ports=[80, 443]), "router")
        self.assertEqual(self.kind(role="phone", ports=[5555]), "your phone (linked to JARVIS)")

    def test_types_need_real_evidence(self):
        self.assertEqual(self.kind(upnp={"kind": "urn:schemas-upnp-org:device:MediaRenderer:1", "name": "[TV] Living Room"}), "smart TV / media player")
        self.assertEqual(self.kind(ports=[8001, 8002], vendor="Samsung Electronics Co.,Ltd"), "smart TV / media player")
        self.assertEqual(self.kind(ports=[8008, 8009]), "Chromecast / Google TV device")
        self.assertEqual(self.kind(mdns=["_googlecast._tcp"]), "Chromecast / Google TV device")
        self.assertEqual(self.kind(ports=[9100]), "printer")
        self.assertEqual(self.kind(mdns=["_ipp._tcp"]), "printer")
        self.assertEqual(self.kind(ports=[62078]), "iPhone or iPad")
        self.assertEqual(self.kind(ports=[554]), "camera (maybe)")
        self.assertEqual(self.kind(ports=[445]), "computer (Windows sharing)")
        self.assertEqual(self.kind(vendor="Espressif Inc."), "smart home device (maybe)")
        self.assertEqual(self.kind(upnp={"kind": "urn:schemas-upnp-org:device:InternetGatewayDevice:1"}), "router")

    def test_unknown_instead_of_a_confident_wrong_guess(self):
        self.assertEqual(self.kind(), "unknown")
        self.assertEqual(self.kind(ports=[80, 443]), "unknown")                                   # a web page alone says nothing
        self.assertEqual(self.kind(ports=[3000], vendor="Dell Inc."), "unknown")                  # port 3000 is only a TV on an LG
        self.assertEqual(self.kind(ports=[8001], vendor="Intel Corporate"), "unknown")
        self.assertEqual(self.kind(vendor="Samsung Electronics Co.,Ltd"), "unknown")              # a Samsung chip can be a phone, a TV, a fridge
        self.assertIn("private Wi-Fi address", nd.classify(dev(mac="e2:00:00:33:4c:54"))[1])

    def test_summary_sentence(self):
        devs = [dev(ip="192.168.1.2", role="self"), dev(ip="192.168.1.1", role="router", hostname="router.lan"),
                dev(ip="192.168.1.5", upnp={"kind": "MediaRenderer", "name": "[TV] Living Room"}), dev(ip="192.168.1.8", ports=[9100]), dev(ip="192.168.1.15"),
                dev(ip="192.168.1.23", role="phone")]
        for d in devs:
            d["type"], d["why"] = nd.classify(d)
        s = nd.summary(devs)
        self.assertEqual(s, "5 devices found: 192.168.1.1 (the router, router.lan), 192.168.1.5 (looks like a smart TV / media player, [TV] Living Room), "
                            "192.168.1.8 (looks like a printer), 192.168.1.15 (unknown), 192.168.1.23 (your phone, linked to JARVIS).")
        self.assertEqual(nd.summary(devs[:1]), "I found no other device on this network, Boss.")


class TestHook(unittest.TestCase):
    def test_command_scans_and_other_sentences_pass_through(self):
        said, old = [], []
        keep = nd.scan
        nd.scan = lambda linked_phone_ip=None, deep=True: [dict(dev(ip="192.168.1.2", role="self"), type="this laptop", why=""), dict(dev(ip="192.168.1.15"), type="unknown", why="")]
        self.addCleanup(lambda: setattr(nd, "scan", keep))
        host = {"check_dynamic_rules": lambda cmd: old.append(cmd) or False, "speak": said.append, "require_owner": lambda w: True, "log_debug": lambda c, e: said.append(repr(e)),
                "get_config": lambda k, d=None: None}
        self.assertTrue(nd.install(host))
        self.assertFalse(nd.install(host))
        self.assertFalse(host["check_dynamic_rules"]("what time is it"))
        self.assertEqual(old, ["what time is it"])
        self.assertTrue(host["check_dynamic_rules"]("scan the network"))
        self.assertEqual(said[-1], "1 device found: 192.168.1.15 (unknown).")
        host["require_owner"] = lambda w: False
        n = len(said)
        self.assertTrue(host["check_dynamic_rules"]("scan the network"))                          # not the owner: no scan, nothing said by this add-on
        self.assertEqual(len(said), n)

    def test_only_addresses_of_the_home_network_are_ever_kept(self):
        net = nd.home_network("192.168.1.2")
        self.assertIn(ipaddress.ip_address("192.168.1.30"), net)
        self.assertNotIn(ipaddress.ip_address("192.168.2.30"), net)
        self.assertNotIn(ipaddress.ip_address("8.8.8.8"), net)


if __name__ == "__main__":
    unittest.main()
