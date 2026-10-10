"""Persistent task state: survives model failures, rate limits and crashes so a task resumes instead of restarting.
Never stores secrets (everything is passed through redact() before it is written)."""
import json
import os
import threading
import time
import uuid

_SAVE_LOCK = threading.Lock()

from .util import redact

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_tasks")
PHASES = ("understand", "plan", "generate", "run", "verify", "deliver", "done")


class TaskState:
    def __init__(self, request, kind="", **kw):
        self.d = {"id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "request": request, "kind": kind, "requirements": [],
                  "project_path": None, "stack": None, "phase": "understand", "operation": "", "completed": [], "files_changed": [],
                  "errors": [], "tests": [], "retries": 0, "plan": None, "verified": False, "final": None, "models": []}
        self.d.update(kw)

    def __getitem__(self, k):
        return self.d[k]

    def __setitem__(self, k, v):
        self.d[k] = v

    def get(self, k, default=None):
        return self.d.get(k, default)

    def op(self, text):
        self.d["operation"] = text
        self.save()

    def done(self, text):
        self.d["completed"].append(text)
        self.save()

    def error(self, text):
        self.d["errors"].append(redact(str(text))[-600:])
        self.save()

    def test(self, name, ok, detail=""):
        self.d["tests"].append({"name": name, "ok": bool(ok), "detail": redact(str(detail))[-300:]})
        self.save()

    def phase(self, name):
        self.d["phase"] = name
        self.save()

    def save(self):
        try:
            with _SAVE_LOCK:                        # files are generated in parallel now
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(os.path.join(STATE_DIR, self.d["id"] + ".json"), "w", encoding="utf-8") as fh:
                    json.dump(json.loads(redact(json.dumps(self.d, default=str))), fh, indent=1)
        except (OSError, ValueError, RuntimeError):
            pass                                    # state is a safety net - failing to save must never break the task

    @classmethod
    def latest_unfinished(cls):
        try:
            files = sorted((f for f in os.listdir(STATE_DIR) if f.endswith(".json")), reverse=True)
        except OSError:
            return None
        for f in files:
            try:
                with open(os.path.join(STATE_DIR, f), encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            if d.get("phase") != "done":
                st = cls(d.get("request", ""))
                st.d.update(d)
                return st
        return None
