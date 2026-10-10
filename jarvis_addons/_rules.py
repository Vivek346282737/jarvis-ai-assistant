"""Shared plumbing for the small voice/chat add-ons (notes, expenses, converter, habits).

Each add-on supplies a `handle(cmd) -> str | None`; hook() puts it in front of the existing dynamic rules, so it works from
the microphone, Telegram, the dashboard and the terminal alike. A faulty handler never breaks the command pipeline."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "jarvis_addons", "data")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def hook(host, flag, handle, label):
    if host.get(flag) or not callable(host.get("check_dynamic_rules")):
        return False
    host[flag] = True
    orig = host["check_dynamic_rules"]

    def check_dynamic_rules(cmd, *a, **k):
        try:
            reply = handle(cmd or "")
        except Exception as e:
            try:
                host["log_debug"](f"jarvis_addons/{label}", e)
            except Exception:
                pass
            reply = None
        if reply is None:
            return orig(cmd, *a, **k)
        if callable(host.get("speak")):
            host["speak"](reply)
        return True
    host["check_dynamic_rules"] = check_dynamic_rules
    return True
