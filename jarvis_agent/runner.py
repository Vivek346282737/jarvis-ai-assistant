"""RUN + TEST for any project type: dependency install, start a server (static / python / node), wait until it really
answers, run HTTP smoke checks, run a project's own test/build command, and read back real tracebacks from the log."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import util


class Server:
    def __init__(self, cwd, cmd, log_name="_jarvis_server.log"):
        self.cwd, self.cmd, self.proc, self.port = cwd, cmd, None, None
        self.log_path = os.path.join(cwd, log_name)
        self.pid_path = os.path.join(cwd, "_jarvis_server.pid")
        self.log_size = 0
        _kill_stale(self.pid_path)                                              # a server from an EARLIER JARVIS process (different PID space) for this
                                                                                  # same project folder would otherwise never be found and leak forever

    def start(self):
        self.port = util.free_port()
        argv = util._resolve_exe([str(a).replace("{PORT}", str(self.port)) for a in self.cmd])
        env = util.child_env(PORT=str(self.port), HOST="127.0.0.1")
        log_f = open(self.log_path, "w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(argv, cwd=self.cwd, stdout=log_f, stderr=log_f, env=env, creationflags=util.NO_WINDOW)
        finally:
            log_f.close()                                                      # the child keeps its own handle; ours would just leak
        try:
            with open(self.pid_path, "w") as fh:
                fh.write(str(self.proc.pid))
        except OSError:
            pass
        return f"http://127.0.0.1:{self.port}"

    def wait_ready(self, timeout=30):
        import socket
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.4):
                    return True
            except OSError:
                time.sleep(0.25)
        return False

    def log_tail(self, n=1800):
        return util.mask_secrets(util.read_text(self.log_path, 10 ** 6))[-n:]

    def new_traceback(self):
        """Fresh traceback/error text that landed in the log since the last call (a RUNTIME crash, not just startup)."""
        try:
            size = os.path.getsize(self.log_path)
            if size <= self.log_size:
                return None
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self.log_size)
                new = fh.read()
            self.log_size = size
            if "Traceback (most recent call last)" in new or "Error:" in new:
                return util.mask_secrets(new)[-1500:]
        except OSError:
            pass
        return None

    def stop(self):
        util.kill_tree(self.proc)
        try:
            if os.path.isfile(self.pid_path):
                os.remove(self.pid_path)
        except OSError:
            pass


def _kill_stale(pid_path):
    """Kills whatever process this project's PID file still points to (a server started by an earlier JARVIS run that
    this process's own RUNNING dict has no memory of), then clears the file."""
    try:
        pid = int(open(pid_path).read().strip())
    except (OSError, ValueError):
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, creationflags=util.NO_WINDOW)
    except Exception:
        pass
    try:
        os.remove(pid_path)
    except OSError:
        pass


def static_cmd():
    return [sys.executable, "-m", "http.server", "{PORT}", "--bind", "127.0.0.1"]


def install_deps(root, plan_install=None):
    """-> (ok, problem_text). requirements.txt -> pip, package.json -> npm (only if node is installed)."""
    cmds = [c for c in (plan_install or []) if isinstance(c, list) and c]
    if not cmds:
        if os.path.isfile(os.path.join(root, "requirements.txt")):
            cmds.append(["pip", "install", "-r", "requirements.txt", "--quiet"])
        if os.path.isfile(os.path.join(root, "package.json")) and not os.path.isdir(os.path.join(root, "node_modules")):
            cmds.append(["npm", "install", "--no-audit", "--no-fund"])
    for c in cmds[:4]:
        if c[0] == "pip":
            c = ["python", "-m"] + c
        rc, out = util.run_cmd(c, root, timeout=300)
        if rc != 0:
            return False, f"dependency install failed ({' '.join(c)}):\n{out[-800:]}"
    return True, ""


def http_check(url, timeout=12, method="GET", body=None):
    """-> (status, text). status 0 = connection failed."""
    try:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(60000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(4000) or b"").decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def smoke(base_url, checks):
    """checks: [{'method','path','expect','contains','json'}] -> list of human-readable problems (empty = all passed)."""
    problems = []
    for c in checks or [{"path": "/", "expect": 200}]:
        path = str(c.get("path") or "/")
        status, text = http_check(base_url + path, method=str(c.get("method") or "GET").upper(), body=c.get("json"))
        exp = c.get("expect") or 200
        if exp == "no-crash":                                                   # e.g. a key-protected admin page: refusing is fine, a 500 is not
            if not status or status >= 500:
                problems.append(f"GET {path} crashed ({status}): {text[:200]}")
            continue
        if status != exp:
            problems.append(f"{c.get('method', 'GET')} {path} returned {status}, expected {exp}: {text[:200]}")
        elif c.get("contains") and str(c["contains"]).lower() not in text.lower():
            problems.append(f"{path} responded but does not contain '{c['contains']}'")
        elif path == "/" and len(text.strip()) < 20:
            problems.append("the home page is empty")
    return problems


def run_tests(root, test_cmd):
    """The project's own test/build command. -> (ok, output)."""
    if not test_cmd:
        return True, ""
    rc, out = util.run_cmd(test_cmd, root, timeout=240)
    return rc == 0, out
