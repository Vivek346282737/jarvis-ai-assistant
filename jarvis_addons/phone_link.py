"""Find and link the owner's Android phone WITHOUT typing an IP address (add-on, standalone: does not load JARVIS).

    venv\\Scripts\\python.exe -m jarvis_addons.phone_link            guided: find the phone, confirm it, save it
    venv\\Scripts\\python.exe -m jarvis_addons.phone_link --find     only show what can be found (changes nothing)
    venv\\Scripts\\python.exe -m jarvis_addons.phone_link --trust    will the phone keep trusting this PC? (changes nothing)
    venv\\Scripts\\python.exe -m jarvis_addons.phone_link --rescue   phone restarted: find its new Wireless-debugging port by a port scan, back to port 5555
    venv\\Scripts\\python.exe -m jarvis_addons.phone_link --helper   once: put the tiny "JARVIS Link" helper on the phone (see below)
    venv\\Scripts\\python.exe -m jarvis_addons.phone_link --helper-remove   take the helper off the phone again

After a PHONE RESTART Android itself switches all Wi-Fi debugging off (every Android since 11 does: at boot the Wi-Fi is not connected
yet, so it is not a "trusted network"). Nothing on the laptop and no adb command can undo that without root - something ON the phone
has to switch it on again. That is the only job of JARVIS Link (jarvis_addons/phone_helper, 13 KB, no screen, no internet): when the
phone is on the home Wi-Fi and Wireless debugging is off, it switches it on. JARVIS then finds the new port by a port scan and moves
the phone back to port 5555. Without the helper: plug the cable in for a few seconds after a phone restart.

How a phone is found, in this order:
  1. mDNS      - a phone with "Wireless debugging" on announces itself on the Wi-Fi (adb's own discovery).
  2. connected - a phone adb already sees (USB cable plugged in, or linked earlier).
  3. sweep     - every address of this PC's Wi-Fi network is asked whether adb's classic port 5555 is open
                 (phones put into "adb tcpip 5555" mode once, for example over a USB cable).
  4. manual    - typing IP:PORT always stays possible.

A found device is only USED after it answered as a real phone and the owner confirmed its brand and model - on a shared
Wi-Fi the first Android device that answers is not necessarily yours (a TV, someone else's phone).
What cannot be automatic: the first time, the PHONE must allow this PC (a pairing code for Wireless debugging, or the
"Allow debugging?" prompt). That is Android's own security and is asked on the phone."""
import os
import re
import socket
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADB = os.path.join(ROOT, "adb.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ADDR_RX = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\b")


def adb(*args, timeout=10):
    """-> (returncode, text). Never raises; a hung adb is a failure, not a freeze."""
    try:
        r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout, errors="replace", creationflags=NO_WINDOW,
                           env=dict(os.environ, ADB_MDNS_OPENSCREEN="1"))
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as e:
        return 127, str(e)


# ------------------------------------------------------------------ the pure parts (tested without a phone)
def parse_mdns(text):
    """`adb mdns services` -> [{"name", "kind": "connect" | "pairing" | "classic", "addr"}]"""
    out = []
    for line in (text or "").splitlines():
        m = ADDR_RX.search(line)
        if not m or "_adb" not in line:
            continue
        kind = "pairing" if "pairing" in line else "connect" if "tls-connect" in line else "classic"
        out.append({"name": line.split()[0], "kind": kind, "addr": m.group(0)})
    return out


def parse_devices(text):
    """`adb devices` -> {serial: state}"""
    out = {}
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def subnet_hosts(ip, mask="255.255.255.0"):
    """Every other address of a home-sized network (at most 254 - a bigger network is not swept)."""
    try:
        a = [int(x) for x in ip.split(".")]
        m = [int(x) for x in mask.split(".")]
    except ValueError:
        return []
    if len(a) != 4 or len(m) != 4 or m[:3] != [255, 255, 255] or a[0] in (127, 169):
        return []
    size = 256 - m[3]
    base = a[3] - a[3] % size
    return [f"{a[0]}.{a[1]}.{a[2]}.{h}" for h in range(base + 1, min(base + size - 1, 255)) if h != a[3]]


def local_ipv4():
    """This PC's own Wi-Fi/LAN addresses (the one used to reach the router first)."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 9))                     # no packet is sent; this only asks the OS which address it would use
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254."))]


def port_open(ip, port=5555, timeout=0.35):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def sweep(hosts, port=5555, check=port_open):
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [f"{h}:{port}" for h, ok in zip(hosts, ex.map(lambda h: check(h, port), hosts)) if ok]


# ------------------------------------------------------------------ talking to adb
def state_of(serial):
    return parse_devices(adb("devices")[1]).get(serial)


def connect(addr):
    """-> 'device' (ready) | 'unauthorized' (the phone must allow this PC) | 'failed'"""
    adb("connect", addr, timeout=8)
    for _ in range(6):
        st = state_of(addr)
        if st in ("device", "unauthorized"):
            return st
        import time
        time.sleep(0.4)
    return "failed"


def describe(serial):
    """Brand and model of a READY device, or None when it is not a phone that answers."""
    rc, maker = adb("-s", serial, "shell", "getprop", "ro.product.manufacturer")
    rc2, model = adb("-s", serial, "shell", "getprop", "ro.product.model")
    if rc != 0 or rc2 != 0 or not model.strip():
        return None
    return f"{maker.strip()} {model.strip()}"


def wifi_ip(serial):
    m = re.search(r"inet (\d{1,3}(?:\.\d{1,3}){3})/", adb("-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0")[1])
    return m.group(1) if m else None


def pc_key():
    """This PC's adb identity (public key text, name) as the phone stores it, or (None, None)."""
    try:
        with open(os.path.join(os.path.expanduser("~"), ".android", "adbkey.pub"), encoding="utf-8", errors="replace") as fh:
            parts = fh.read().split()
        return (parts[0], parts[1] if len(parts) > 1 else "") if parts else (None, None)
    except OSError:
        return None, None


def trust_in_dump(dump, key, name):
    """Is this PC in the phone's list of permanently trusted computers (`dumpsys adb`)? True / False / None (the phone does not say)."""
    if not dump or "user_keys" not in dump and "keystore" not in dump.lower():
        return None
    if key and key[:48] in dump:
        return True
    return True if name and re.search(r"user_keys?.*\b" + re.escape(name) + r"\b", dump, re.S) else False


def trust_status(serial):
    """-> {"permanent": True/False/None, "forgets_after_days": int or 0 (never) or None (unknown)}
    permanent False = the phone allowed this PC only for now: "Always allow from this computer" was not ticked."""
    key, name = pc_key()
    rc, dump = adb("-s", serial, "shell", "dumpsys", "adb", timeout=10)
    rc2, t = adb("-s", serial, "shell", "settings", "get", "global", "adb_allowed_connection_time")
    t = t.strip()
    days = None
    if rc2 == 0:
        days = 7 if t in ("null", "") else (0 if t == "0" else max(1, int(t) // 86400000) if t.isdigit() else None)
    return {"permanent": trust_in_dump(dump, key, name) if rc == 0 else None, "forgets_after_days": days}


def explain_trust(serial, ask=None, say=print):
    """Tells the owner whether the phone will keep trusting this PC, and (asked first) removes Android's 7-day expiry."""
    st = trust_status(serial)
    if st["permanent"] is False:
        say("    IMPORTANT: the phone allowed this PC only for NOW. To make it permanent: unplug the cable, plug it in again and, in the\n"
            "    'Allow USB debugging?' box on the phone, TICK 'Always allow from this computer' before tapping Allow.")
    elif st["permanent"]:
        say("    The phone trusts this PC permanently ('Always allow' is saved) - you will not be asked again.")
    if st["forgets_after_days"]:
        say(f"    Note: this phone forgets a PC it has not seen for {st['forgets_after_days']} days (Android's default).")
        if ask is not None and ask("    Turn that expiry off so the phone never forgets this PC? [Y/n]: ").strip().lower() != "n":
            adb("-s", serial, "shell", "settings", "put", "global", "adb_allowed_connection_time", "0")
            ok = trust_status(serial)["forgets_after_days"] == 0
            say("    Done - the phone keeps trusting this PC." if ok else
                "    The phone did not accept that. Do it by hand: Developer options > 'Disable adb authorization timeout'.")
    return st


def find(do_sweep=True, log=lambda s: None):
    """-> [{"addr", "how", "state"}] candidates, best first. Reads only (connecting to a candidate is the only side effect)."""
    out, seen = [], set()

    def add(addr, how, state=None):
        if addr not in seen:
            seen.add(addr)
            out.append({"addr": addr, "how": how, "state": state})

    services = parse_mdns(adb("mdns", "services", timeout=8)[1])
    log(f"mDNS announced {len(services)} adb service(s)")
    for s in services:
        if s["kind"] in ("connect", "classic"):
            add(s["addr"], "mDNS (the phone announced itself)")
    for serial, st in parse_devices(adb("devices")[1]).items():
        add(serial, "already connected" if ADDR_RX.fullmatch(serial) else "USB cable", st)
    if do_sweep:
        for ip in local_ipv4():
            hosts = subnet_hosts(ip)
            hits = sweep(hosts)
            log(f"swept {len(hosts)} addresses around {ip} for port 5555: {len(hits)} open")
            for h in hits:
                add(h, "found on the Wi-Fi (port 5555)")
    return out, [s for s in services if s["kind"] == "pairing"]


def save_target(db, addr):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('adb_target', ?)", (addr,))
    con.commit()
    con.close()


def usb_to_wifi(serial, log=print):
    """A phone on a USB cable: switch it to Wi-Fi debugging on port 5555 and return its Wi-Fi address (cable can then be removed)."""
    ip = wifi_ip(serial)
    if not ip:
        log("    The phone is not on Wi-Fi, so it stays on the cable.")
        return serial
    adb("-s", serial, "tcpip", "5555", timeout=10)
    import time
    time.sleep(2.0)
    addr = f"{ip}:5555"
    return addr if connect(addr) == "device" else serial


def link(db, ask=input, say=print, assume_yes=False, do_sweep=True):
    """Guided linking. -> the saved address, or None. `ask` is only used for the confirmation, a pairing code or the manual fallback."""
    if not os.path.isfile(ADB):
        say("adb.exe is missing from the JARVIS folder.")
        return None
    say("Looking for your phone on this Wi-Fi ...")
    cands, pairing = find(do_sweep=do_sweep, log=lambda s: say("    " + s))
    for c in cands:
        state = c["state"] if c["state"] in ("device", "unauthorized") else connect(c["addr"])
        if state == "unauthorized":
            say(f"    {c['addr']} ({c['how']}): the phone is asking 'Allow debugging?' - tap ALLOW on the phone, then run this again.")
            continue
        if state != "device":
            continue
        what = describe(c["addr"])
        if not what:
            continue
        say(f"    Found: {what}  at {c['addr']}  - {c['how']}")
        if not assume_yes and ask("    Is this YOUR phone? [Y/n]: ").strip().lower() == "n":
            if ADDR_RX.fullmatch(c["addr"]):
                adb("disconnect", c["addr"])
            continue
        was_usb = not ADDR_RX.fullmatch(c["addr"])
        addr = c["addr"] if is_fixed(c["addr"]) else usb_to_wifi(c["addr"], say)     # a cable or a random Wireless-debugging port -> the fixed port
        save_target(db, addr)
        say(f"    Linked: {what} ({addr}). Saved - you never have to type it.")
        try:
            explain_trust(addr, ask=None if assume_yes else ask, say=say)
        except Exception:
            pass
        if was_usb and ADDR_RX.fullmatch(addr):
            say("    You can unplug the cable now. After the PHONE is restarted, plug the cable in once for a few seconds -\n"
                "    JARVIS puts it back on Wi-Fi by itself (Android switches Wi-Fi debugging off at every restart).")
        return addr
    if pairing and not assume_yes:
        p = pairing[0]
        say(f"    Your phone is waiting to be paired ({p['addr']}).")
        code = ask("    Type the 6-digit code shown on the phone (blank to skip): ").strip()
        if code:
            rc, out = adb("pair", p["addr"], code, timeout=20)
            say("    " + ("Paired." if rc == 0 and "Successfully" in out else "Pairing did not work: " + out[:120]))
            if rc == 0 and "Successfully" in out:
                return link(db, ask=ask, say=say, assume_yes=assume_yes, do_sweep=False)
    say("    No phone could be linked automatically.")
    if assume_yes:
        return None
    manual = ask("    Type the phone's IP:PORT yourself (blank to skip): ").strip()
    if ADDR_RX.fullmatch(manual) and connect(manual) == "device":
        save_target(db, manual)
        say(f"    Linked: {describe(manual) or 'phone'} ({manual}).")
        return manual
    if manual:
        say("    That address did not answer as a phone. Later:  venv\\Scripts\\python.exe -m jarvis_addons.phone_link")
    return None


RESTORE_EVERY = 45          # seconds between attempts, so a cable that cannot help is not hammered


def restore_wifi(host, state):
    """After the phone was restarted its Wi-Fi debugging (port 5555) is off and only the USB cable works. When the saved phone is
    unreachable and exactly ONE trusted phone is on a cable, switch it back to Wi-Fi - the owner only has to plug the cable in.
    -> the Wi-Fi address that now works, or None (nothing to do / not possible)."""
    import time
    saved = host["get_config"]("adb_target") or ""
    if not saved.endswith(":5555") or time.time() - state.get("ts", 0) < RESTORE_EVERY:
        return None
    st = host["_adb_state"]()
    usb = [s for s, v in st.items() if v == "device" and not ADDR_RX.fullmatch(s)]
    if st.get(saved) == "device" or len(usb) != 1:
        return None
    state["ts"] = time.time()
    run = host["run_silent_cmd"]
    out = run([host["ADB_BIN"], "-s", usb[0], "shell", "ip", "-f", "inet", "addr", "show", "wlan0"], timeout=8).stdout or ""
    m = re.search(r"inet (\d{1,3}(?:\.\d{1,3}){3})/", out)
    if not m:
        return None                                           # the phone is not on Wi-Fi: it simply keeps working over the cable
    run([host["ADB_BIN"], "-s", usb[0], "tcpip", "5555"], timeout=10)
    time.sleep(2.5)
    addr = f"{m.group(1)}:5555"
    run([host["ADB_BIN"], "connect", addr], timeout=8)
    time.sleep(0.5)
    if host["_adb_state"]().get(addr) != "device":
        return None
    host["TARGET"] = addr
    if addr != saved:
        host["set_config"]("adb_target", addr)               # the router gave the phone a new address
    if host.get("file_log"):
        host["file_log"](f"[ADB] phone was on the cable only (after a restart): Wi-Fi debugging switched back on at {addr}")
    return addr


# ------------------------------------------------------------------ a link that stays: one fixed port, found again by itself
# Why the link kept breaking (jarvis.log, 2026-10-05): "Wireless debugging" gives the phone a NEW random port every time it is switched
# on, and Android switches it off when the Wi-Fi drops. JARVIS had saved such a random port (35427) over the fixed one (5555) and from
# then on only ever tried the dead port. Now: whatever way the phone is reached (cable, Wireless debugging), it is moved to the fixed
# port 5555 at once and THAT is saved; a lost phone is looked for on its last address and, if the router gave it a new one, on the
# whole Wi-Fi - and only a phone with the same serial number is taken back.
FIXED_PORT = 5555
PIN_EVERY = 600             # seconds between two moves to the fixed port (each one restarts the phone's adb service)
SWEEP_EVERY = 300           # seconds between two searches of the whole Wi-Fi for a phone whose address changed
CFG_TTL = 60
SCAN_RANGE = (30000, 50000)  # where Android puts the random Wireless-debugging port
SCAN_EVERY = 120            # seconds between two port scans while the phone is on the Wi-Fi but not reachable by adb
SCAN_WORKERS = 1100         # connections open at the same time during a scan
HELPER = "com.jarvis.adbkeeper"
HELPER_APK = os.path.join(ROOT, "jarvis_addons", "phone_helper", "jarvis_link.apk")


def phone_gateway(serial):
    """The address of the Wi-Fi router the phone is on now (how the helper recognises the home Wi-Fi)."""
    m = re.search(r"\bvia (\d{1,3}(?:\.\d{1,3}){3}) dev wlan", adb("-s", serial, "shell", "ip", "route", "get", "8.8.8.8")[1])
    return m.group(1) if m else None


def helper_status(serial):
    """-> {"installed", "may_switch", "battery_free", "scheduled"}"""
    pkg = adb("-s", serial, "shell", "dumpsys", "package", HELPER, timeout=20)[1]
    jobs = adb("-s", serial, "shell", "dumpsys", "jobscheduler", HELPER, timeout=20)[1]
    return {"installed": HELPER in adb("-s", serial, "shell", "pm", "list", "packages", HELPER)[1],
            "may_switch": "android.permission.WRITE_SECURE_SETTINGS: granted=true" in pkg,
            "battery_free": HELPER in adb("-s", serial, "shell", "dumpsys", "deviceidle", "whitelist")[1],
            "scheduled": f"{HELPER}/.Keep" in jobs}


def helper_install(serial, say=print):
    """Installs / updates JARVIS Link on the connected phone and tells it which Wi-Fi is home. -> True when everything is in place"""
    if not os.path.isfile(HELPER_APK):
        say("jarvis_link.apk is missing from jarvis_addons\\phone_helper.")
        return False
    gw = phone_gateway(serial)
    if not gw:
        say("The phone is not on Wi-Fi right now; connect it to your home Wi-Fi first.")
        return False
    rc, out = adb("-s", serial, "install", "-r", HELPER_APK, timeout=120)
    if "Success" not in out:
        say("The phone did not install the helper: " + out.strip()[-200:] + "\n(If the phone shows a question from Play Protect, tap 'Install anyway' and run this again.)")
        return False
    adb("-s", serial, "shell", "pm", "grant", HELPER, "android.permission.WRITE_SECURE_SETTINGS")
    adb("-s", serial, "shell", "dumpsys", "deviceidle", "whitelist", "+" + HELPER)
    adb("-s", serial, "shell", "am", "broadcast", "-a", HELPER + ".START", "-n", HELPER + "/.Boot", "--es", "gw", gw, "-f", "32")     # 32 = also wakes a freshly installed app
    st = helper_status(serial)
    say(f"JARVIS Link on the phone: installed {st['installed']}, may switch Wireless debugging {st['may_switch']}, "
        f"free of battery limits {st['battery_free']}, scheduled {st['scheduled']}; home Wi-Fi router {gw}")
    return all(st.values())
CABLE_EVERY = 10            # seconds between two looks for a phone on a cable
KEEPERS = ("_adb_keeper_loop",)                              # JARVIS's background thread that watches the link (named after its function)


def ping(ip, timeout_ms=700):
    """Is this address alive on the Wi-Fi? (one ping; nothing is scanned when the phone is simply not there)"""
    try:
        out = subprocess.run(["ping", "-n", "1", "-w", str(timeout_ms), ip], capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW).stdout
        return "TTL=" in (out or "").upper()
    except Exception:
        return False


def scan_ports(ip, lo=SCAN_RANGE[0], hi=SCAN_RANGE[1], timeout=0.3, workers=SCAN_WORKERS):
    """Open TCP ports of ONE address in lo..hi in about 6 seconds (mDNS is not needed, many routers block it). A phone does not
    answer on closed ports at all, so every port costs the full timeout. Measured on a real phone: more threads or a shorter
    timeout is faster but then misses the open port now and then."""
    ports = range(lo, hi + 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [p for p, ok in zip(ports, ex.map(lambda p: port_open(ip, p, timeout), ports)) if ok]


def fixed_addr(addr):
    """'192.168.1.23:35427' -> '192.168.1.23:5555' (None for a cable serial)."""
    m = ADDR_RX.fullmatch(addr or "")
    return f"{m.group(1)}:{FIXED_PORT}" if m else None


def is_fixed(addr):
    return bool(addr) and fixed_addr(addr) == addr


def reconnect_candidates(saved, last_ip):
    out = []
    for a in (fixed_addr(saved), f"{last_ip}:{FIXED_PORT}" if last_ip else None):
        if a and a not in out:
            out.append(a)
    return out


def _sh(host, serial, *args, timeout=8):
    return (host["run_silent_cmd"]([host["ADB_BIN"], "-s", serial, "shell", *args], timeout=timeout).stdout or "").strip()


def _adopt(host, state, addr, why):
    old = host.get("TARGET")
    host["TARGET"] = addr
    if host["get_config"]("adb_target") != addr:
        host["set_config"]("adb_target", addr)
    host["set_config"]("adb_last_ip", addr.rsplit(":", 1)[0])
    state["cfg_ts"] = 0
    if host.get("file_log"):
        host["file_log"](f"[ADB] {why}: {addr}" + (f" (was {old})" if old and old != addr else ""))
    return addr


def pin_fixed_port(host, state):
    """The phone is connected, but through a cable or a random Wireless-debugging port: move it to port 5555 and save that.
    -> the fixed address, or None (already fixed / not possible / tried a moment ago)."""
    import time
    target = host.get("TARGET") or ""
    if not target or (is_fixed(target) and state.get("known") == target):
        return None                                           # the normal case: nothing to ask the phone, no adb process started
    if host["_adb_state"]().get(target) != "device":
        return None
    if is_fixed(target):
        if state.get("known") != target:                      # once per address: remember who this phone is
            state["known"] = target
            sn = _sh(host, target, "getprop", "ro.serialno")
            if sn and host["get_config"]("adb_phone_serial") != sn:
                host["set_config"]("adb_phone_serial", sn)
            if host["get_config"]("adb_last_ip") != target.rsplit(":", 1)[0]:
                host["set_config"]("adb_last_ip", target.rsplit(":", 1)[0])
        return None
    now = time.time()
    if now - state.get("pin_ts", 0) < state.get("pin_gap", PIN_EVERY):
        return None
    state["pin_ts"] = now
    if now - state.get("pinned", 0) < 300:                    # it was moved minutes ago and is on a random port again:
        state["pin_gap"] = 3600                               # this phone does not keep port 5555 - stop restarting its adb service
        if host.get("file_log"):
            host["file_log"]("[ADB] the phone left the fixed port right after being moved to it; staying on " + target + " for now")
        return None
    m = re.search(r"inet (\d{1,3}(?:\.\d{1,3}){3})/", _sh(host, target, "ip", "-f", "inet", "addr", "show", "wlan0"))
    if not m:
        return None                                           # not on Wi-Fi: it keeps working over the cable
    sn = _sh(host, target, "getprop", "ro.serialno")
    run, addr = host["run_silent_cmd"], f"{m.group(1)}:{FIXED_PORT}"
    run([host["ADB_BIN"], "-s", target, "tcpip", str(FIXED_PORT)], timeout=10)
    for _ in range(4):
        time.sleep(2.0)
        run([host["ADB_BIN"], "connect", addr], timeout=8)
        if host["_adb_state"]().get(addr) == "device":
            break
    else:
        state["pin_gap"] = 3600
        if host.get("file_log"):
            host["file_log"](f"[ADB] could not move the phone to the fixed port {FIXED_PORT}; it stays on {target}")
        return None
    if ADDR_RX.fullmatch(target):
        run([host["ADB_BIN"], "disconnect", target], timeout=5)
    if sn:
        host["set_config"]("adb_phone_serial", sn)
    state.update(pinned=now, known=addr)
    return _adopt(host, state, addr, f"phone moved to the fixed port {FIXED_PORT} (it no longer changes when Wireless debugging restarts)")


def wireless_port_rescue(host, state, ip, works, is_alive=None, scan=None):
    """After a phone restart only 'Wireless debugging' is on, with a random port nobody knows. The phone answers a ping, so its
    ports are scanned, the adb one is connected and the phone is moved to the fixed port at once. -> address, or None."""
    if not (is_alive or ping)(ip):
        return None
    ports = (scan or scan_ports)(ip)
    skip = state.setdefault("not_adb", set())                # open ports that turned out to be some other app of the phone
    for port in [p for p in ports if p not in skip][:6]:
        addr = f"{ip}:{port}"
        if not works(addr):
            skip.add(port)
            host["run_silent_cmd"]([host["ADB_BIN"], "disconnect", addr], timeout=5)
            continue
        serial = _sh(host, addr, "getprop", "ro.serialno")
        if state.get("serial") and serial and serial != state["serial"]:
            host["run_silent_cmd"]([host["ADB_BIN"], "disconnect", addr], timeout=5)          # another phone took this address
            continue
        host["TARGET"] = addr
        if host.get("file_log"):
            host["file_log"](f"[ADB] phone found by a port scan on its Wireless-debugging port {port}")
        state["pin_ts"] = 0
        fixed = pin_fixed_port(host, state)
        if fixed or host["_adb_state"]().get(addr) != "device":
            return fixed                                      # moved - or the phone dropped the link while trying (the next round finds it)
        return _adopt(host, state, addr, "phone linked on its Wireless-debugging port")
    return None


def quick_reconnect(host, state, probe=None, do_sweep=None, is_alive=None, scan=None, heavy=True):
    """The phone is not connected. A plain network knock (no adb process) on its fixed port every time; the moment it answers, connect.
    Every few minutes the whole Wi-Fi is searched for the SAME phone under a new address. -> the address that works again, or None."""
    import time
    probe, now = probe or port_open, time.time()
    if now - state.get("cfg_ts", 0) > CFG_TTL:
        get = host["get_config"]
        state.update(cfg_ts=now, saved=get("adb_target") or "", last_ip=get("adb_last_ip") or "", serial=get("adb_phone_serial") or "")
    cands = reconnect_candidates(state["saved"], state["last_ip"])
    run = host["run_silent_cmd"]

    def works(addr):
        run([host["ADB_BIN"], "connect", addr], timeout=8)
        time.sleep(0.4)
        st = host["_adb_state"]().get(addr)
        if st == "unauthorized" and now - state.get("unauth_ts", 0) > 600 and host.get("file_log"):
            state["unauth_ts"] = now
            host["file_log"]("[ADB] the phone answers but asks 'Allow debugging?' - tick 'Always allow from this computer' and tap Allow")
        return st == "device"

    for addr in cands:
        if not probe(addr.rsplit(":", 1)[0], FIXED_PORT):
            continue
        if host.get("TARGET") == addr and host["_adb_state"]().get(addr) == "device":
            return addr                                       # already linked (the caller's own attempt was only held back)
        if works(addr):
            return _adopt(host, state, addr, "phone is back on its fixed port")
    if not heavy:
        return None                                           # a command of the owner never waits for a scan; the keeper does those
    ip = state["last_ip"] or (cands[0].rsplit(":", 1)[0] if cands else "")
    eco = (host.get("_perf") or {}).get("pace") == "eco"     # on battery / hot: the scan (6 s of work) runs three times less often
    if ip and now - state.get("scan_ts", 0) >= SCAN_EVERY * (3 if eco else 1):
        state["scan_ts"] = now
        got = wireless_port_rescue(host, state, ip, works, is_alive, scan)
        if got:
            return got
    if not state["serial"] or now - state.get("sweep_ts", 0) < SWEEP_EVERY:
        return None
    state["sweep_ts"] = now
    hits = do_sweep() if do_sweep else [h for ip in local_ipv4() for h in sweep(subnet_hosts(ip))]
    for addr in hits:
        if addr in cands or not works(addr):
            continue
        if _sh(host, addr, "getprop", "ro.serialno") == state["serial"]:
            return _adopt(host, state, addr, "the router gave the phone a new address; found it again")
        run([host["ADB_BIN"], "disconnect", addr], timeout=5)                 # somebody else's device: leave it alone
    return None


def install(host):
    """Hook for a running JARVIS: wraps its own reconnect so that the phone is kept on one fixed port and found again by itself."""
    need = ("ensure_adb_connected", "_adb_state", "get_config", "set_config", "run_silent_cmd", "ADB_BIN", "log_debug")
    if any(k not in host for k in need) or getattr(host["ensure_adb_connected"], "_phone_link", False):
        return False
    import threading
    import time
    orig, state, busy = host["ensure_adb_connected"], {}, threading.Lock()

    def ensure_adb_connected(*a, **k):
        ok = orig(*a, **k)
        if not busy.acquire(False):                           # another thread is already looking after the link
            return ok
        try:
            now = time.time()
            if now - state.get("cable_ts", 0) >= CABLE_EVERY:  # looking for a cable costs an adb process: not on every phone command
                state["cable_ts"] = now
                if restore_wifi(host, state):
                    return True
            if ok:
                pin_fixed_port(host, state)
            elif quick_reconnect(host, state, heavy=bool(host.get("CLI_MODE")) or threading.current_thread().name in KEEPERS):
                return True
        except Exception as e:
            host["log_debug"]("phone_link/keep", e)
        finally:
            busy.release()
        return ok

    ensure_adb_connected._phone_link = True
    ensure_adb_connected._state = state
    host["ensure_adb_connected"] = ensure_adb_connected
    return True


HELP = """Before this step, on the phone (once):
  Settings > About phone > tap "Build number" 7 times, then Settings > Developer options.
  EASIEST: turn on "USB debugging", plug the phone into this PC with a cable. The phone asks "Allow USB debugging?":
           TICK "Always allow from this computer" FIRST, then tap ALLOW (without the tick it asks again every time).
  WITHOUT a cable: turn on "Wireless debugging", open it, tap "Pair device with pairing code" and keep that screen open.
The phone and this PC must be on the same Wi-Fi."""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db = argv[argv.index("--db") + 1] if "--db" in argv else os.path.join(ROOT, "jarvis_memory.db")
    if "--trust" in argv:
        con = sqlite3.connect(db) if os.path.exists(db) else None
        row = con.execute("SELECT val FROM config WHERE key='adb_target'").fetchone() if con else None
        if con:
            con.close()
        serial = row[0] if row else None
        if not serial or state_of(serial) != "device":
            print("The saved phone is not connected right now.")
            return 1
        st = trust_status(serial)
        print(f"Phone {describe(serial)} at {serial}")
        print("  trusts this PC permanently ('Always allow' saved): " + {True: "YES", False: "NO - only for now", None: "the phone does not say"}[st["permanent"]])
        print("  forgets an unused PC after: " + ("never" if st["forgets_after_days"] == 0 else f"{st['forgets_after_days']} days" if st["forgets_after_days"] else "unknown"))
        rc, port = adb("-s", serial, "shell", "getprop", "persist.adb.tcp.port")
        print("  Wi-Fi debugging survives a phone restart: " + ("yes" if port.strip() else "NO - after a restart plug the cable in once; JARVIS switches it back on"))
        return 0
    if "--rescue" in argv or "--helper" in argv or "--helper-remove" in argv:
        con = sqlite3.connect(db) if os.path.exists(db) else None
        cfg = dict(con.execute("SELECT key, val FROM config WHERE key IN ('adb_target', 'adb_last_ip')").fetchall()) if con else {}
        if con:
            con.close()
        saved = cfg.get("adb_target") or ""
        ip = cfg.get("adb_last_ip") or (saved.rsplit(":", 1)[0] if ADDR_RX.fullmatch(saved) else "")
        fixed = f"{ip}:{FIXED_PORT}" if ip else ""
    if "--rescue" in argv:
        if not ip:
            print("No phone is saved yet. First:  venv\\Scripts\\python.exe -m jarvis_addons.phone_link")
            return 1
        if connect(fixed) == "device":
            print(f"The phone is already linked on its fixed port: {fixed}")
            return 0
        if not ping(ip):
            print(f"The phone ({ip}) does not answer on the Wi-Fi: is it on the same Wi-Fi as this PC?")
            return 1
        import time
        t0 = time.time()
        ports = scan_ports(ip)
        print(f"Scanned {ip} ports {SCAN_RANGE[0]}-{SCAN_RANGE[1]} in {time.time() - t0:.1f}s: open {ports or 'none'}")
        for port in ports[:6]:
            if connect(f"{ip}:{port}") == "device":
                addr = usb_to_wifi(f"{ip}:{port}")
                save_target(db, addr)
                print(f"Linked on {addr}" + ("" if is_fixed(addr) else "  (the phone did not accept the fixed port)"))
                return 0
        print("No adb port found: 'Wireless debugging' is off on the phone (JARVIS Link switches it on within a few minutes after a restart).")
        return 1
    if "--helper" in argv or "--helper-remove" in argv:
        if not fixed or connect(fixed) != "device":
            print("The phone is not connected right now.")
            return 1
        if "--helper-remove" in argv:
            print("Removed: " + adb("-s", fixed, "uninstall", HELPER, timeout=60)[1].strip())
            return 0
        return 0 if helper_install(fixed) else 1
    if "--find" in argv:
        cands, pairing = find(log=lambda s: print("  " + s))
        for c in cands:
            print(f"  candidate: {c['addr']:24} {c['how']}" + (f"  [{c['state']}]" if c["state"] else ""))
        for p in pairing:
            print(f"  waiting to be paired: {p['addr']}")
        if not cands and not pairing:
            print("  nothing found")
        return 0
    print(HELP + "\n")
    return 0 if link(db, assume_yes="--yes" in argv) else 1


if __name__ == "__main__":
    sys.exit(main())
