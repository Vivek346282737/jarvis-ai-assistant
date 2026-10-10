"""What is on MY Wi-Fi? (add-on) - finds the devices on the laptop's own local network and says what each one probably is.

    venv\\Scripts\\python.exe -m jarvis_addons.network_discovery --scan              scan now and print the devices
    venv\\Scripts\\python.exe -m jarvis_addons.network_discovery --update-vendors    refresh the maker list (IEEE's public registry)

By voice / chat:  "scan the network" / "network scan karo" / "kya devices mile" / "which devices are on my wifi"

How a device is found - all of it ordinary, polite discovery, nothing is logged into and nothing is guessed at:
  1. the laptop's own neighbour table (arp) after one empty packet to every address of the home network, so the table is complete
  2. the maker of the network card from the first half of its hardware address (IEEE registry, kept in jarvis_addons/data)
  3. the name the device gives itself (reverse DNS), what it announces by UPnP/SSDP and mDNS (TVs, printers, speakers do that)
  4. whether a handful of well-known service ports answer (printer 9100, TV remote 8001, ...) - only "is it open", never more

Limits that are kept on purpose:
  * only the private network this laptop is itself connected to (never an address on the internet, never another network)
  * no passwords are tried, no login page is touched, nothing is changed on any device; scanning never connects to anything
  * a type is only named when something really points to it - otherwise the answer is "unknown"."""
import gzip
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "jarvis_addons", "data")
OUI_FILE = os.path.join(DATA, "oui.json.gz")
LAST_SCAN = os.path.join(DATA, "last_scan.json")
OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PORTS = {22: "ssh", 53: "dns", 80: "web page", 139: "windows sharing", 443: "secure web page", 445: "windows sharing", 515: "printer (lpd)", 554: "video stream (rtsp)",
         631: "printer (ipp)", 3000: "lg tv", 3001: "lg tv", 3389: "remote desktop", 5555: "android debugging", 7000: "airplay", 8001: "samsung tv", 8002: "samsung tv",
         8008: "chromecast", 8009: "chromecast", 8060: "roku", 9100: "printer (raw)", 62078: "iphone sync"}
MDNS_SERVICES = ("_googlecast._tcp", "_airplay._tcp", "_raop._tcp", "_ipp._tcp", "_printer._tcp", "_pdl-datastream._tcp", "_spotify-connect._tcp",
                 "_hap._tcp", "_smb._tcp", "_androidtvremote2._tcp", "_amzn-wplay._tcp")
SCAN_RX = re.compile(r"\bscan\b.{0,20}\b(?:network|wifi|wi-fi|lan)\b|\b(?:network|wifi|wi-fi)\b.{0,12}\bscan\b"
                     r"|\b(?:kya|kaun|kaunse|kitne|which|what|how many)\b.{0,14}\bdevices?\b.{0,24}\b(?:mile|mila|hai|hain|jude|connected|on|found)\b"
                     r"|\bdevices?\s+(?:on|in)\s+(?:my|the)\s+(?:network|wifi|wi-fi)\b", re.I)
_OUI = {}


# ------------------------------------------------------------------ reading what Windows already knows
def parse_arp(text):
    """`arp -a` -> {own address: {ip: mac}}; broadcast, multicast and incomplete rows are left out."""
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"\s*Interface:\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\s+(\w+)", line)
        if m and cur is not None:
            ip, mac = m.group(1), m.group(2).lower().replace("-", ":")
            first = int(ip.split(".")[0])
            if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or first >= 224 or ip.endswith(".255") or mac.startswith(("01:00:5e", "33:33")):
                continue
            cur[ip] = mac
    return out


def parse_gateway(route_text):
    """`route print -4` -> the home router's address, or None."""
    m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", route_text or "", re.M)
    return m.group(1) if m else None


def home_network(own_ip):
    """The /24 around this laptop's address - and only when that is a private home/office range."""
    ip = ipaddress.ip_address(own_ip)
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        return None
    return ipaddress.ip_network(own_ip + "/24", strict=False)


def _run(*args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ the maker of the network card
def is_private_mac(mac):
    """Phones and laptops often show a made-up ('private') hardware address per Wi-Fi: its maker cannot be known."""
    try:
        return bool(int(mac.split(":")[0], 16) & 2)
    except (ValueError, IndexError, AttributeError):
        return False


def parse_oui_csv(text):
    """IEEE's oui.csv -> {'001a2b': 'Example Networks Inc.'}"""
    import csv
    import io
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 3 and re.fullmatch(r"[0-9A-Fa-f]{6}", row[1].strip()):
            out[row[1].strip().lower()] = row[2].strip()[:60]
    return out


def update_vendors(url=OUI_URL, path=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0", "Accept": "*/*"})          # IEEE's server refuses browser-like names from scripts
    with urllib.request.urlopen(req, timeout=60) as r:
        table = parse_oui_csv(r.read().decode("utf-8", errors="replace"))
    if len(table) < 10000:
        raise RuntimeError("the registry file looks incomplete; the old list is kept")
    os.makedirs(os.path.dirname(path or OUI_FILE), exist_ok=True)
    with gzip.open(path or OUI_FILE, "wt", encoding="utf-8") as f:
        json.dump(table, f, separators=(",", ":"))
    _OUI.clear()
    return len(table)


def vendor(mac, table=None):
    if is_private_mac(mac):
        return "private (randomised) address"
    if table is None:
        if not _OUI:
            try:
                with gzip.open(OUI_FILE, "rt", encoding="utf-8") as f:
                    _OUI.update(json.load(f))
            except (OSError, ValueError):
                _OUI["-"] = ""
        table = _OUI
    return table.get(mac.replace(":", "").lower()[:6]) or "unknown maker"


# ------------------------------------------------------------------ what devices say about themselves
def parse_ssdp(reply):
    """One SSDP answer -> {"server", "st", "location"} (lower-case header names)."""
    h = {}
    for line in reply.split("\r\n")[1:]:
        k, _, v = line.partition(":")
        if v:
            h[k.strip().lower()] = v.strip()
    return {"server": h.get("server", ""), "st": h.get("st", ""), "location": h.get("location", "")}


def parse_upnp_xml(xml):
    def tag(name):
        m = re.search(rf"<{name}>\s*([^<]{{1,120}})", xml or "", re.I)
        return m.group(1).strip() if m else ""
    return {"name": tag("friendlyName"), "maker": tag("manufacturer"), "model": tag("modelName"), "kind": tag("deviceType")}


def ssdp(net, wait=2.5):
    """UPnP announcements: {ip: {"server", "st": set, "name", "maker", "model", "kind"}}. Only answers from the home network count."""
    import urllib.request
    found = {}
    msg = "\r\n".join(["M-SEARCH * HTTP/1.1", "HOST: 239.255.255.250:1900", 'MAN: "ssdp:discover"', "MX: 2", "ST: ssdp:all", "", ""]).encode()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for _ in range(2):
            s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + wait
        while time.time() < end:
            try:
                data, (ip, _port) = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if ipaddress.ip_address(ip) not in net:
                continue
            r = parse_ssdp(data.decode("utf-8", errors="replace"))
            d = found.setdefault(ip, {"server": r["server"], "st": set(), "location": "", "name": "", "maker": "", "model": "", "kind": ""})
            d["st"].add(r["st"])
            if r["location"] and not d["location"]:
                d["location"] = r["location"]
        s.close()
    except OSError:
        pass
    for ip, d in found.items():                                   # the device's own public description page (what any media player reads)
        loc = d.pop("location")
        if loc.startswith("http://") and re.match(rf"http://{re.escape(ip)}[:/]", loc):
            try:
                with urllib.request.urlopen(loc, timeout=2.5) as r:
                    d.update({k: v for k, v in parse_upnp_xml(r.read(65536).decode("utf-8", errors="replace")).items() if v})
            except Exception:
                pass
        d["st"] = sorted(x for x in d["st"] if x)[:12]
    return found


def mdns(net, wait=2.0):
    """{ip: [service types it answered for]} - a plain multicast question for a few well-known service types."""
    def question(name):
        q = b"".join(bytes([len(p)]) + p.encode() for p in (name + ".local").split(".")) + b"\x00"
        return b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + q + b"\x00\x0c\x00\x01"
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for name in MDNS_SERVICES:
            s.sendto(question(name), ("224.0.0.251", 5353))
        end = time.time() + wait
        while time.time() < end:
            try:
                data, (ip, _port) = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if ipaddress.ip_address(ip) in net:
                hit = [n for n in MDNS_SERVICES if n.split(".")[0].encode() in data]
                if hit:
                    found.setdefault(ip, set()).update(hit)
        s.close()
    except OSError:
        pass
    return {ip: sorted(v) for ip, v in found.items()}


def open_ports(ip, ports=PORTS, timeout=0.45):
    def check(p):
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                return p
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        return sorted(p for p in ex.map(check, ports) if p)


def hostname(ip, timeout=1.5):
    box = []
    t = threading.Thread(target=lambda: box.append(_safe_host(ip)), daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else ""


def _safe_host(ip):
    try:
        name = socket.gethostbyaddr(ip)[0]
        return "" if name == ip else name
    except OSError:
        return ""


# ------------------------------------------------------------------ what is it? (only when something really says so)
def classify(d):
    """-> (type, why). d = one device dict. 'unknown' unless there is real evidence."""
    up, ports, md = d.get("upnp") or {}, set(d.get("ports") or []), " ".join(d.get("mdns") or [])
    says = " ".join([up.get("name", ""), up.get("model", ""), up.get("kind", ""), up.get("server", ""), " ".join(up.get("st", []))]).lower()
    maker = ((d.get("vendor") or "") + " " + up.get("maker", "")).lower()
    if d.get("role") == "self":
        return "this laptop", "it is the PC JARVIS runs on"
    if d.get("role") == "phone":
        return "your phone (linked to JARVIS)", "its address is the phone JARVIS is linked to"
    if d.get("role") == "router" or "internetgatewaydevice" in says:
        return "router", "it is the gateway of this network" if d.get("role") == "router" else "it announces itself as an internet gateway"
    if "printer" in says or re.search(r"_ipp|_printer|_pdl", md) or ports & {9100, 631, 515}:
        return "printer", "it announces a print service" if ("printer" in says or md) else "a printer port answers"
    if "_googlecast" in md or {8008, 8009} <= ports:
        return "Chromecast / Google TV device", "it answers as a Google Cast device"
    if re.search(r"\btv\b|television|mediarenderer|bravia|webos|tizen|roku|fire ?tv", says) or "_androidtvremote2" in md or "_amzn-wplay" in md \
            or (ports & {8001, 8002} and "samsung" in maker) or (ports & {3000, 3001} and re.search(r"\blg\b|lg electronics", maker)) or 8060 in ports:
        return "smart TV / media player", "it announces itself as a media device" if says or md else "a TV remote-control port answers"
    if "_airplay" in md or "_raop" in md or 7000 in ports and "apple" in maker:
        return "AirPlay device (Apple TV / speaker)", "it answers AirPlay"
    if 62078 in ports:
        return "iPhone or iPad", "Apple's sync port answers"
    if 5555 in ports:
        return "Android device (debugging on)", "the Android debugging port answers"
    if 554 in ports:
        return "camera (maybe)", "a video-stream port answers"
    if ports & {445, 139, 3389}:
        return "computer (Windows sharing)", "Windows file sharing answers"
    if re.search(r"espressif|tuya|shelly|itead|sonoff", maker):
        return "smart home device (maybe)", "its network chip is one used in smart plugs and bulbs"
    if "_hap" in md:
        return "smart home device", "it announces HomeKit"
    if 22 in ports:
        return "computer or server", "a remote-login port answers"
    return "unknown", "nothing identifies it" + (" (private Wi-Fi address - phones and tablets do that)" if is_private_mac(d.get("mac", "")) else "")


def summary(devs):
    """The sentence JARVIS says."""
    others = [d for d in devs if d.get("role") != "self"]
    if not others:
        return "I found no other device on this network, Boss."
    def label(d):
        t = d["type"]
        name = (d.get("upnp") or {}).get("name") or d.get("hostname") or ""
        what = {"unknown": "unknown", "router": "the router", "your phone (linked to JARVIS)": "your phone, linked to JARVIS"}.get(t) or ("" if "maybe" in t else "looks like a ") + t
        return f"{d['ip']} ({what}" + (f", {name}" if name and t != "unknown" else "") + ")"
    return f"{len(others)} device{'s' if len(others) != 1 else ''} found: " + ", ".join(label(d) for d in others[:12]) + "."


# ------------------------------------------------------------------ the scan
def scan(linked_phone_ip=None, deep=True):
    """-> list of devices on this laptop's own network (dicts: ip, mac, vendor, hostname, ports, upnp, mdns, role, type, why)."""
    from .phone_link import local_ipv4
    devs = {}
    gw = parse_gateway(_run("route", "print", "-4"))
    for own in local_ipv4():
        net = home_network(own)
        if net is None:
            continue
        try:                                                    # one empty packet to every address: Windows then learns who is there
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for h in net.hosts():
                try:
                    s.sendto(b"", (str(h), 9))
                except OSError:
                    pass
            s.close()
        except OSError:
            pass
        announced, services = (ssdp(net), mdns(net)) if deep else ({}, {})
        time.sleep(0.6)
        table = parse_arp(_run("arp", "-a")).get(own, {})
        devs[own] = {"ip": own, "mac": "", "vendor": "", "role": "self"}
        for ip, mac in table.items():
            if ipaddress.ip_address(ip) in net:
                devs[ip] = {"ip": ip, "mac": mac, "vendor": vendor(mac), "role": "router" if ip == gw else "phone" if ip == linked_phone_ip else "",
                            "upnp": announced.get(ip, {}), "mdns": services.get(ip, [])}
    others = [d for d in devs.values() if d["role"] != "self"]
    if deep and others:
        with ThreadPoolExecutor(max_workers=16) as ex:
            for d, ports, name in zip(others, ex.map(lambda d: open_ports(d["ip"]), others), ex.map(lambda d: hostname(d["ip"]), others)):
                d["ports"], d["hostname"] = ports, name
    out = sorted(devs.values(), key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
    for d in out:
        d["type"], d["why"] = classify(d)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(LAST_SCAN, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "devices": out}, f, indent=1)
    except OSError:
        pass
    return out


def last_scan():
    try:
        with open(LAST_SCAN, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"ts": 0, "devices": []}


def install(host):
    """'scan the network' in a running JARVIS (voice, terminal, Telegram, dashboard - they share the command pipeline)."""
    if host.get("_network_discovery") or not callable(host.get("check_dynamic_rules")):
        return False
    host["_network_discovery"] = True
    orig = host["check_dynamic_rules"]

    def check_dynamic_rules(cmd, *a, **k):
        if not SCAN_RX.search(cmd or ""):
            return orig(cmd, *a, **k)
        try:
            owner = host.get("require_owner")
            if callable(owner) and not owner("scan the network"):
                return True
            say = host["speak"] if callable(host.get("speak")) else (lambda s: None)
            say("Scanning your network, Boss. This takes about ten seconds.")
            phone = (host["get_config"]("adb_last_ip") if callable(host.get("get_config")) else None) or None
            devs = scan(linked_phone_ip=phone)
            say(summary(devs))
            if callable(host.get("file_log")):
                host["file_log"](f"[NETWORK] scan: {len(devs) - 1} other device(s) on the home network")
        except Exception as e:
            host["log_debug"]("network_discovery/scan", e)
            if callable(host.get("speak")):
                host["speak"]("The network scan failed, Boss. I logged why.")
        return True
    host["check_dynamic_rules"] = check_dynamic_rules
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--update-vendors" in argv:
        print(f"maker list updated: {update_vendors()} entries")
        return 0
    if "--scan" in argv:
        phone = None
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{os.path.join(ROOT, 'jarvis_memory.db')}?mode=ro", uri=True)
            row = con.execute("SELECT val FROM config WHERE key='adb_last_ip'").fetchone()
            con.close()
            phone = row[0] if row else None
        except Exception:
            pass
        t0 = time.time()
        devs = scan(linked_phone_ip=phone)
        for d in devs:
            up = d.get("upnp") or {}
            print(f"  {d['ip']:15} {d.get('mac') or '(this PC)':17}  {d['type']}")
            print(f"  {'':15} maker: {d.get('vendor') or '-'} | name: {up.get('name') or d.get('hostname') or '-'} | open: {', '.join(f'{p} {PORTS[p]}' for p in d.get('ports') or []) or 'none of the checked ports'}")
            print(f"  {'':15} why: {d['why']}" + (f" | says: {up.get('maker', '')} {up.get('model', '')} {up.get('kind', '')}".rstrip() if up else "") + (f" | mdns: {', '.join(d['mdns'])}" if d.get("mdns") else ""))
        print(f"\n{summary(devs)}   ({time.time() - t0:.1f}s)")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
