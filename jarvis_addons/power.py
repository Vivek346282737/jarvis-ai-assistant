"""One click: everything of JARVIS off, or on again (add-on).

    venv\\Scripts\\pythonw.exe -m jarvis_addons.power --off         JARVIS, the Telegram bot, the local AI (Ollama) and adb: all stopped
    venv\\Scripts\\pythonw.exe -m jarvis_addons.power --on          everything started again
    venv\\Scripts\\python.exe  -m jarvis_addons.power --status      what is running, and is JARVIS switched off?
    venv\\Scripts\\python.exe  -m jarvis_addons.power --shortcuts   put "JARVIS Band" and "JARVIS Chalu" on the Desktop

By voice: "Jarvis exam mode" (also: "exam mode on", "sab band karo", "poora band ho jao") = the same as --off.
("Jarvis so jao" stays what it always was: JARVIS only goes quiet and wakes up by voice.)

Off STAYS off: --off leaves the file jarvis_off.flag in the JARVIS folder. When Windows starts JARVIS again after a restart, JARVIS sees
the file, stops the local AI that Windows started too, and exits at once. Only --on (the "JARVIS Chalu" shortcut) removes the file."""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = os.path.join(ROOT, "jarvis_off.flag")
TASK = "JARVIS Assistant"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HELPERS = ("ollama app.exe", "ollama.exe", "llama-server.exe", "ollama_llama_server.exe", "adb.exe")
OFF_RX = re.compile(r"\bexam\s+mode\b(?!\s+(?:off|band|khatam|over))|\bsab\s+(?:kuch\s+)?band\s+kar(?:o|\s+do)?\b|\b(?:poora|pura|puri tarah)\s+band\s+ho\s+ja(?:o)?\b", re.I)

BACK_RX = re.compile(r"\bexam\s+mode\s+(?:off|band|khatam|over)\b", re.I)


def wants_off(text):
    return bool(OFF_RX.search(text or ""))


def kind_of(name, cmdline):
    """Which part of JARVIS is this process? -> 'jarvis' | 'telegram' | 'autostart' | 'helper' | None"""
    n, cl = (name or "").lower(), cmdline or ""
    if n in HELPERS:
        return "helper"
    if n.startswith("python"):
        if "jarvis_addons.power" in cl:
            return None                                       # this tool itself
        if "live_cmd_console.py" in cl:
            return "jarvis"
        if "telegram_bot" in cl:
            return "telegram"
        if "jarvis_addons.dashboard" in cl:
            return "dashboard"
    if n.startswith("powershell") and ("JARVIS_Autostart.ps1" in cl or "JARVIS_Launcher.ps1" in cl):
        return "autostart"
    return None


def running():
    import psutil
    out = []
    for p in psutil.process_iter(["name", "cmdline", "exe"]):
        try:
            k = kind_of(p.info["name"], " ".join(p.info["cmdline"] or []))
            if k and p.pid != os.getpid():
                out.append((k, p))
        except Exception:
            pass
    return out


def is_off():
    return os.path.exists(FLAG)


def _quiet(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    except Exception:
        return None


def stop_helpers():
    """The local AI and adb only. -> how many processes were stopped"""
    import psutil
    procs = [p for k, p in running() if k == "helper"]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)
    return len(procs)


def stop_all(keep_pids=()):
    """Everything off, and it stays off after a restart of the laptop. -> names of what was stopped"""
    import psutil
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write(time.strftime("switched off %Y-%m-%d %H:%M:%S - 'JARVIS Chalu' switches it on again\n"))
    _quiet(["schtasks", "/end", "/tn", TASK])
    done = []
    for order in ("autostart", "jarvis", "telegram", "dashboard", "helper"):          # the starter first, so that it cannot start JARVIS again
        procs = [p for k, p in running() if k == order and p.pid not in keep_pids]
        for p in procs:
            try:
                p.kill()
                done.append(order)
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=5)
    return done


def start_all():
    """-> what was started"""
    if os.path.exists(FLAG):
        os.remove(FLAG)
    started = []
    names = {(p.info["name"] or "").lower() for k, p in running() if k == "helper"}
    if "ollama.exe" not in names and "ollama app.exe" not in names:
        app = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe")
        try:
            if os.path.isfile(app):
                subprocess.Popen([app], creationflags=NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["ollama", "serve"], creationflags=NO_WINDOW, close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started.append("local AI (Ollama)")
        except OSError:
            pass                                              # this PC has no Ollama: JARVIS works without it
    if not any(k == "jarvis" for k, p in running()):
        r = _quiet(["schtasks", "/run", "/tn", TASK])
        if not (r and r.returncode == 0):                     # no scheduled task on this PC: start it directly, without a window
            pyw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
            subprocess.Popen([pyw if os.path.isfile(pyw) else sys.executable, os.path.join(ROOT, "live_cmd_console.py")], cwd=ROOT,
                             creationflags=NO_WINDOW, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append("JARVIS")
    return started


def make_shortcuts(desktop=None):
    """'JARVIS Band' and 'JARVIS Chalu' on the Desktop; they run without any window. -> the two paths"""
    pyw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
    ps = ("$d = if ($env:JV_DESK) { $env:JV_DESK } else { [Environment]::GetFolderPath('Desktop') }; $w = New-Object -ComObject WScript.Shell; "
          "foreach ($x in @(@('JARVIS Band','--off','shell32.dll,27','Stops JARVIS, the Telegram bot, the local AI and adb'),"
          "@('JARVIS Chalu','--on','shell32.dll,137','Starts JARVIS and the local AI again'))) { "
          "$p = Join-Path $d ($x[0] + '.lnk'); $s = $w.CreateShortcut($p); $s.TargetPath = $env:JV_PYW; $s.Arguments = '-m jarvis_addons.power ' + $x[1]; "
          "$s.WorkingDirectory = $env:JV_ROOT; $s.IconLocation = $x[2]; $s.Description = $x[3]; $s.WindowStyle = 7; $s.Save(); Write-Output $p }")
    env = dict(os.environ, JV_PYW=pyw, JV_ROOT=ROOT, JV_DESK=desktop or "")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60, env=env, creationflags=NO_WINDOW)
    return [x.strip() for x in (r.stdout or "").splitlines() if x.strip().lower().endswith(".lnk")]


def _log(text):
    try:
        with open(os.path.join(ROOT, "jarvis.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" [pid {os.getpid()}] [POWER] {text}\n")
    except OSError:
        pass


def install(host):
    """In a running JARVIS: 'exam mode' by voice, and staying off after a restart of the laptop."""
    if host.get("_power") or not callable(host.get("check_dynamic_rules")):
        return False
    host["_power"] = True
    log = host.get("file_log") or _log
    if is_off() and not host.get("CLI_MODE"):                 # Windows started JARVIS, but the owner had switched it off
        log("[POWER] JARVIS is switched off (jarvis_off.flag): not starting. 'JARVIS Chalu' on the Desktop switches it on.")
        try:
            stop_helpers()                                    # Windows also started the local AI: off means off
        finally:
            os._exit(0)
    orig = host["check_dynamic_rules"]

    def check_dynamic_rules(cmd, *a, **k):
        if BACK_RX.search(cmd or ""):                          # answered here, so that it is never guessed to be some other mode
            if callable(host.get("speak")):
                host["speak"]("Exam mode is not on, Boss. I am here and listening.")
            return True
        if not wants_off(cmd):
            return orig(cmd, *a, **k)
        if "telegram_bot" in (sys.argv[0] or ""):               # asked from Telegram: it could not be switched on again from there
            if callable(host.get("speak")):
                host["speak"]("I cannot switch myself off from Telegram, Boss, because you could not switch me on again from there. Use JARVIS Band on the laptop.")
            return True
        owner = host.get("require_owner")
        if callable(owner) and not owner("switch JARVIS off"):
            return True
        if callable(host.get("speak")):
            host["speak"]("Exam mode, Boss. I am switching everything off. Press JARVIS Chalu on the desktop when you want me back.")
        log("[POWER] switched off by voice: " + str(cmd)[:60])
        stop_all(keep_pids=_family())
        os._exit(0)

    host["check_dynamic_rules"] = check_dynamic_rules
    return True


def _family():
    """This process and its parents (the venv starter): they end by themselves when this process exits."""
    import psutil
    try:
        return {os.getpid()} | {p.pid for p in psutil.Process().parents()}
    except Exception:
        return {os.getpid()}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    say = print if sys.stdout else (lambda *a: None)          # pythonw has no console
    if "--off" in argv:
        done = stop_all()
        _log("switched off with the shortcut: stopped " + (", ".join(sorted(set(done))) or "nothing (nothing was running)"))
        say("JARVIS band. Stopped: " + (", ".join(sorted(set(done))) or "nothing was running"))
        return 0
    if "--on" in argv:
        started = start_all()
        _log("switched on with the shortcut: started " + (", ".join(started) or "nothing (already running)"))
        say("JARVIS chalu. Started: " + (", ".join(started) or "already running"))
        return 0
    if "--shortcuts" in argv:
        for p in make_shortcuts():
            say("made: " + p)
        return 0
    rows = running()
    say("JARVIS is " + ("SWITCHED OFF (stays off after a restart)" if is_off() else "switched on"))
    for k in ("jarvis", "telegram", "dashboard", "autostart", "helper"):
        names = sorted({(p.info["name"] or "") for kk, p in rows if kk == k})
        say(f"  {k:9}: " + (", ".join(names) if names else "not running"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
