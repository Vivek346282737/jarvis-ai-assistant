"""Loads live_cmd_console.py as a library, ALWAYS in CLI mode. A plain `import live_cmd_console` starts the whole voice
loop (the file ends with an unguarded main()), so every add-on tool goes through load() instead."""
import contextlib
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(root=ROOT, quiet=True):
    path = os.path.join(root, "live_cmd_console.py")
    if root not in sys.path:
        sys.path.insert(0, root)
    old_argv, old_cwd = sys.argv, os.getcwd()
    os.chdir(root)
    sys.argv = [path, "--dry-run", "status"]          # a real, harmless CLI flag -> CLI_MODE is true, the voice loop never starts
    spec = importlib.util.spec_from_file_location("live_cmd_console", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_cmd_console"] = mod
    buf = io.StringIO()
    os.environ["JARVIS_ADDON_TOOL"] = "1"            # tells the add-ons this is a tool load, not the owner starting JARVIS
    try:
        with (contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext()):
            try:
                spec.loader.exec_module(mod)
            except SystemExit:
                pass
    finally:
        os.environ.pop("JARVIS_ADDON_TOOL", None)
        sys.argv = old_argv
        os.chdir(old_cwd)
    if not getattr(mod, "CLI_MODE", False):
        raise RuntimeError("live_cmd_console did not load in CLI mode")
    return mod
