"""Phone brand independence (add-on). JARVIS was written against one Samsung phone; this makes the brand-specific parts work
on any Android phone WITHOUT changing what already works:

  * 'open camera / gallery / calculator on phone' - the Samsung package names are only used when they are really installed;
    otherwise the same app of that phone's brand is found (installed-package check), then the standard Android intent.
  * the unlock swipe uses fixed pixels (fine on every normal screen); on a screen too small for them it is scaled.
  * incoming-call notifications of other dialer apps are recognised (never changed on a Samsung phone).

On a phone where the original values work, every wrapper here passes the call through untouched.

    venv\\Scripts\\python.exe -m jarvis_addons.phone_compat      report for the connected phone (reads only)"""
import re
import sys
import time

APP_CANDIDATES = {
    "camera": ["com.sec.android.app.camera", "com.google.android.GoogleCamera", "com.android.camera", "com.android.camera2", "com.oneplus.camera",
               "com.oppo.camera", "com.motorola.camera3", "com.motorola.camera2", "com.huawei.camera", "com.mediatek.camera", "org.codeaurora.snapcam",
               "com.transsion.camera", "com.nothing.camera", "com.asus.camera", "com.sonyericsson.android.camera"],
    "gallery": ["com.sec.android.gallery3d", "com.google.android.apps.photos", "com.miui.gallery", "com.coloros.gallery3d", "com.oneplus.gallery",
                "com.vivo.gallery", "com.android.gallery3d", "com.motorola.MotGallery2", "com.huawei.photos"],
    "calculator": ["com.sec.android.app.popupcalculator", "com.google.android.calculator", "com.miui.calculator", "com.coloros.calculator",
                   "com.oneplus.calculator", "com.vivo.calculator", "com.android.calculator2", "com.transsion.calculator"],
}
FALLBACK_INTENT = {
    "camera": ["-a", "android.media.action.STILL_IMAGE_CAMERA"],
    "gallery": ["-a", "android.intent.action.MAIN", "-c", "android.intent.category.APP_GALLERY"],
    "calculator": ["-a", "android.intent.action.MAIN", "-c", "android.intent.category.APP_CALCULATOR"],
}
EXTRA_CALL_PKGS = ("com.android.phone", "com.oneplus.dialer")
UNLOCK_SWIPE = ["input", "swipe", "500", "1100", "500", "400", "200"]          # the exact fixed swipe inside unlock_phone()
MIN_W, MIN_H = 600, 1300                                                       # below this the fixed swipe would start off-screen
CACHE_SECS = 600
_cache = {}                                                                     # (target, what) -> (time, value)


def _cached(host, what, fn, ttl=CACHE_SECS):
    key = (host.get("TARGET"), what)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val:                                                                     # an unreachable phone is asked again next time, never remembered
        _cache[key] = (time.time(), val)
    return val


def _shell(host, *args, timeout=15):
    run = host["run_silent_cmd"]
    if run is host.get("_phone_compat_wrapper"):                                # normal case: skip our own wrapper. If something replaced
        run = host["_phone_compat_raw_run"]                                     # run_silent_cmd (a self-test's fake phone), that one is respected.
    res = run([host["ADB_BIN"], "-s", host["TARGET"], "shell", *args], timeout=timeout)
    return (res.stdout or "") if getattr(res, "returncode", 1) == 0 else ""


def installed_packages(host):
    return _cached(host, "pkgs", lambda: frozenset(re.findall(r"^package:(\S+)", _shell(host, "pm", "list", "packages"), re.M)))


def screen_size(host):
    def read():
        out = _shell(host, "wm", "size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None
    return _cached(host, "size", read, ttl=86400)


def manufacturer(host):
    return _cached(host, "maker", lambda: _shell(host, "getprop", "ro.product.manufacturer").strip().lower(), ttl=86400)


def resolve_app(app_key, current_pkg, installed):
    """-> the package to launch for app_key on THIS phone, or None when no known package is installed.
    The package JARVIS already uses always wins when it is installed."""
    if not installed or current_pkg in installed:
        return current_pkg
    for pkg in APP_CANDIDATES.get(app_key, []):
        if pkg in installed:
            return pkg
    return None


def scaled_unlock_swipe(size):
    """-> the swipe args for this screen, or None to keep the original fixed swipe (every normal phone)."""
    if not size or (size[0] >= MIN_W and size[1] >= MIN_H):
        return None
    w, h = size
    return ["input", "swipe", str(w // 2), str(int(h * 0.75)), str(w // 2), str(int(h * 0.25)), "200"]


def install(host):
    need = ("open_phone_app", "PHONE_APPS", "run_silent_cmd", "ADB_BIN", "speak", "log_debug")
    if any(k not in host for k in need) or host.get("_phone_compat_raw_run"):
        return False
    orig_open, raw_run = host["open_phone_app"], host["run_silent_cmd"]
    host["_phone_compat_raw_run"] = raw_run
    state = {"calls_checked": False}

    def open_phone_app(app_key):
        try:
            apps = host["PHONE_APPS"]
            current = apps.get(app_key)
            if current and app_key in APP_CANDIDATES:
                installed = installed_packages(host)
                pkg = resolve_app(app_key, current, installed)
                if pkg and pkg != current:
                    apps[app_key] = pkg                                         # remembered for this run: this phone's own app
                elif pkg is None and installed:
                    out = _shell(host, "am", "start", *FALLBACK_INTENT[app_key], timeout=20)
                    if out and "Error" not in out:
                        host["speak"](f"{app_key.title()} opened on your phone, Boss.")
                        if host.get("log_audit"):
                            host["log_audit"](f"Opened {app_key} on phone", "LOW", "SUCCESS")
                        return
        except Exception as e:
            host["log_debug"]("phone_compat/open", e)
        return orig_open(app_key)

    def run_silent_cmd(cmd_list, timeout=None):
        try:
            if isinstance(cmd_list, list) and len(cmd_list) > 4 and cmd_list[3] == "shell":
                if cmd_list[4:] == UNLOCK_SWIPE:
                    alt = scaled_unlock_swipe(screen_size(host))
                    if alt:
                        cmd_list = cmd_list[:4] + alt
                if not state["calls_checked"] and "_CALL_PKGS" in host:
                    maker = manufacturer(host)
                    if maker:
                        state["calls_checked"] = True
                        if maker != "samsung":                                  # a Samsung phone keeps exactly the list it always had
                            host["_CALL_PKGS"] = tuple(host["_CALL_PKGS"]) + tuple(p for p in EXTRA_CALL_PKGS if p not in host["_CALL_PKGS"])
        except Exception as e:
            host["log_debug"]("phone_compat/run", e)
        return raw_run(cmd_list, timeout=timeout)

    host["open_phone_app"] = open_phone_app
    host["run_silent_cmd"] = host["_phone_compat_wrapper"] = run_silent_cmd
    return True


def report(host):
    """What JARVIS will do on the connected phone. Reads only."""
    maker, size, installed = manufacturer(host), screen_size(host), installed_packages(host)
    if not maker or not installed:
        print("No phone answered. Connect it first (Wireless debugging on, then --set-target IP:PORT).")
        return 1
    model = _shell(host, "getprop", "ro.product.model").strip()
    android = _shell(host, "getprop", "ro.build.version.release").strip()
    print(f"Phone        : {maker} {model}, Android {android}")
    print(f"Screen       : {size[0]}x{size[1]}" if size else "Screen       : could not be read")
    alt = scaled_unlock_swipe(size)
    print("Unlock swipe : " + ("scaled to this screen: " + " ".join(alt[2:6]) if alt else "the standard swipe fits this screen"))
    for app, current in host["PHONE_APPS"].items():
        pkg = resolve_app(app, current, installed)
        if pkg == current and current in installed:
            note = "installed"
        elif pkg and pkg != current:
            note = f"using this phone's app instead of {current}"
        elif app in FALLBACK_INTENT:
            pkg, note = "(standard Android intent)", "no known package installed, the phone's default app is asked for"
        else:
            pkg, note = current, "NOT installed on this phone"
        print(f"  {app:11}: {pkg:42} {note}")
    try:
        nodes = host["get_ui_nodes"]() if host.get("get_ui_nodes") else []          # JARVIS's own reader, exactly what WhatsApp sending uses
    except Exception:
        nodes = []
    print("Screen reader: " + (f"works ({len(nodes)} elements)" if nodes else "did NOT answer (WhatsApp sending needs it)"))
    return 0


if __name__ == "__main__":
    from ._host import load
    L = load()
    sys.exit(report(L.__dict__))
