"""Small, dependency-free helpers shared by the universal agent: safe file writing, project tree, checkpoints, and a
locked-down command runner (no shell, allowlisted programs only)."""
import os
import re
import shutil
import socket
import subprocess
import sys
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IGNORE_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build", ".next", ".idea", ".vscode", "jarvis_checkpoints"}
ALLOWED_EXE = {"python", "python3", "pip", "node", "npm", "npx", "uvicorn", "flask", "pytest", "gunicorn"}
_SAFE_REL = re.compile(r"^[\w.][\w\-./]*$")
_SECRET_RX = re.compile(r"(api[_-]?key|secret|token|passwd|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I)


def slug(text, fallback="project"):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    return s or fallback


def safe_rel(rel):
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/") or rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel) or not _SAFE_REL.match(rel):
        return None
    return rel


def write_files(root, files):
    """Writes [{'path','content'}] under root. Model output is DATA: any path that could escape root is skipped. Returns written rel paths."""
    written = []
    root_n = os.path.normpath(root)
    for f in files or []:
        rel = safe_rel(f.get("path"))
        content = f.get("content")
        if rel is None or content is None:
            continue
        full = os.path.normpath(os.path.join(root_n, rel))
        if not full.startswith(root_n + os.sep):
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(str(content))
        written.append(rel)
    return written


def read_text(path, limit=6000):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def tree(root, max_files=200):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in fn:
            out.append(os.path.relpath(os.path.join(dp, f), root).replace("\\", "/"))
            if len(out) >= max_files:
                return out
    return out


def checkpoint(root, base):
    """Copy of the project before it is modified (skips heavy/derived folders). Returns the checkpoint path."""
    dest = os.path.join(base, "jarvis_checkpoints", f"{slug(os.path.basename(root))}-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copytree(root, dest, ignore=shutil.ignore_patterns(*IGNORE_DIRS))
    return dest


SECRET_ENV_NAMES = ("PEXELS_API_KEY",)          # keys that live in the Windows user environment (not in JARVIS's database)


def secret_values():
    """The actual values of those keys, for masking only. Read from this process's environment and from the user
    environment in the registry (where `setx` puts them)."""
    out = set()
    for name in SECRET_ENV_NAMES:
        v = os.environ.get(name, "").strip()
        if len(v) >= 8:
            out.add(v)
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                v = str(winreg.QueryValueEx(h, name)[0]).strip()
            if len(v) >= 8:
                out.add(v)
        except Exception:
            pass
    return out


def mask_secrets(text):
    """Replaces a secret's VALUE wherever it appears, whatever is around it. Text without a secret comes back unchanged."""
    text = text or ""
    for v in secret_values():
        if v in text:
            text = text.replace(v, "***")
    return text


def child_env(**extra):
    """Environment for a program JARVIS starts (a generated site's server, its tests): everything as before, minus the keys
    above - those programs have no use for them, and one printing its environment would put a key into logs and reports."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in SECRET_ENV_NAMES}
    env.update(extra)
    return env


def redact(text):
    return mask_secrets(_SECRET_RX.sub(lambda m: re.split(r"[=:]", m.group(0))[0] + "=***", text or ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_exe(cmd):
    exe = os.path.basename(str(cmd[0])).lower()
    exe = re.sub(r"\.(exe|cmd|bat)$", "", exe)
    if exe not in ALLOWED_EXE:
        raise ValueError(f"program '{cmd[0]}' is not on the allowed list")
    if any(re.search(r"[&|;<>`]|\$\(", str(a)) for a in cmd):
        raise ValueError("shell operators are not allowed in a command")
    if exe in ("python", "python3"):
        return [sys.executable] + [str(a) for a in cmd[1:]]
    found = shutil.which(str(cmd[0])) or shutil.which(exe)
    if not found:
        raise FileNotFoundError(f"'{exe}' is not installed on this machine")
    return [found] + [str(a) for a in cmd[1:]]


def run_cmd(cmd, cwd, timeout=180, env=None):
    """-> (returncode, output_tail). Never uses a shell; a timeout is reported as a failure, never a hang."""
    try:
        argv = _resolve_exe(cmd)
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=child_env() if env is None else env,
                           creationflags=NO_WINDOW, errors="replace")
        return r.returncode, mask_secrets((r.stdout or "") + (r.stderr or ""))[-3000:]
    except subprocess.TimeoutExpired:
        return 124, f"command timed out after {timeout}s: {' '.join(map(str, cmd))}"
    except (ValueError, FileNotFoundError, OSError) as e:
        return 127, str(e)


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15, creationflags=NO_WINDOW)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
