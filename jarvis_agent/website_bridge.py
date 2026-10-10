"""Phase 2 bridge: 'website for X' is built by the jarvis_agent builder; the legacy Section R builder stays as FALLBACK.

Nothing in live_cmd_console.py is edited. The legacy trigger (project_builder_commands: phrase matching, Devanagari
STT repair, the one-build-at-a-time lock, the background thread, the spoken error) keeps doing its job exactly as
before. That trigger looks up build_project_from_description in the module globals at call time, so install() only
swaps that one name for the wrapper below:

    voice build  ->  new builder  ->  (only if it produced nothing, or crashed)  ->  legacy builder

A direct call with an explicit max_fix_rounds, or one made outside a voice build, goes straight to the legacy engine
(that is how its own self-tests call it). Config 'website_builder' = 'old' switches the new builder off completely."""
import threading
import time

from . import router
from .state import TaskState

BUILD_KINDS = ("website", "app", "fullstack", "api")


def _is_voice_build(host, description):
    lock, active = host.get("_BUILD_LOCK"), host.get("_BUILD_ACTIVE")
    return bool(lock is not None and lock.locked() and isinstance(active, dict) and active.get("desc") == description)


def build_with_new(host, description):
    """Runs the jarvis_agent builder. Returns (built, verified). built=False means it left nothing usable behind
    (no plan / files could not be generated), which is the only case the legacy builder is asked to take over."""
    from . import core
    text = f"build a website for {description}"
    state = TaskState(text)
    host["speak"]("On it, Boss. Let me understand that first.")
    spec = router.classify(host, state, text, "build")
    if spec["kind"] not in BUILD_KINDS:
        spec["kind"] = "website"
    state["kind"] = spec["kind"]
    state.done("understood request as " + spec["kind"])
    verified = bool(core._execute(host, state, spec, "build"))
    return state["final"] in ("verified", "partial"), verified


def install(host):
    orig = host.get("build_project_from_description")
    if orig is None or getattr(orig, "_jarvis_agent", False):
        return False

    def build_project_from_description(description, max_fix_rounds=None):
        def legacy():
            return orig(description) if max_fix_rounds is None else orig(description, max_fix_rounds=max_fix_rounds)

        try:
            use_new = (max_fix_rounds is None and _is_voice_build(host, description)
                       and host["get_config"]("universal_agent", "on") == "on" and host["get_config"]("website_builder", "new") != "old")
        except Exception as e:
            host["log_debug"]("jarvis_agent/website_bridge", e)
            use_new = False
        if not use_new:
            return legacy()
        if not host["require_owner"]("build a project"):
            return True
        from . import core
        if core._LOCK.locked() and time.time() - core._ACTIVE["started"] < core.STALE_SECS:
            host["speak"](f"I am still working on {core._ACTIVE['title']}, Boss. Let me finish that first.")
            return True
        ctxs = host.get("SKILL_CTX")
        if isinstance(ctxs, dict):
            ctxs.setdefault("threads", []).append(threading.current_thread())          # lets `--say "..."` wait for the build
        try:
            built, verified = build_with_new(host, description)
        except Exception as e:
            host["log_debug"]("jarvis_agent/website_bridge", e)
            built, verified = False, False
        if built:
            return verified
        host["speak"]("The new builder could not finish that, Boss. Falling back to the classic builder.")
        return legacy()

    build_project_from_description._jarvis_agent = True
    host["build_project_from_description"] = build_project_from_description
    return True
