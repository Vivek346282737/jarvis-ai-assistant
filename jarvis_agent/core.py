"""Orchestrator. install() wraps JARVIS's existing project_builder_commands so nothing that worked before changes:
the old 'website for X' phrasing still goes through the legacy trigger (website_bridge then builds it with the new
builder and keeps the Phase 1-3 engine as fallback), everything else that looks like a real
task goes through UNDERSTAND -> skill pipeline. If the agent errors or does not recognise the sentence, control falls
straight back to the original function."""
import threading
import time

from . import router
from .skills import load_all
from .state import TaskState
from .util import mask_secrets

_LOCK = threading.Lock()
_ACTIVE = {"title": None, "started": 0.0, "token": None}
STALE_SECS = 3600                                   # a big build can legitimately take a long time; an hour with no end = stuck


class Ctx:
    def __init__(self, host, state, spec):
        self.host, self.state, self.spec = host, state, spec

    def say(self, text):
        try:
            self.host["speak"](mask_secrets(text))  # spoken text is also printed and written to jarvis.log
        except Exception as e:
            self.host["log_debug"]("jarvis_agent/say", e)


def _execute(host, state, spec, gate_kind):
    ctx = Ctx(host, state, spec)
    skills = load_all()
    fn = skills.get(spec["kind"])
    if fn is None:
        ctx.say(f"I do not have a skill for {spec['kind']} yet, Boss.")
        return False
    state["kind"] = spec["kind"]
    state["requirements"] = spec.get("requirements") or state["requirements"]
    state.phase("plan")
    return fn(ctx)


def handle(cmd, raw_text, host):
    """True = the universal agent took this sentence."""
    c = cmd.strip(" .!?")
    legacy = host.get("_BUILD_RX")
    if legacy is not None and legacy.match(c):
        return False                                # 'website for X' -> the legacy trigger (lock, thread, Devanagari); website_bridge then builds it with the new builder
    gate_kind = router.gate(c)
    if gate_kind is None:
        return False
    if not host["require_owner"]("run an autonomous task"):
        return True
    busy = host.get("_BUILD_LOCK")
    if _LOCK.locked() and time.time() - _ACTIVE["started"] < STALE_SECS:
        host["speak"](f"I am still working on {_ACTIVE['title']}, Boss. Let me finish that first.")
        return True
    if busy is not None and busy.locked():
        host["speak"]("I am still building a website, Boss. Let me finish that first.")
        return True
    if _LOCK.locked():
        _LOCK.release()                             # reclaim a stuck one; the token below stops the old thread releasing the new lock
    if not _LOCK.acquire(blocking=False):
        host["speak"]("I am already on another task, Boss.")
        return True
    token = object()
    _ACTIVE.update(title=cmd[:60], started=time.time(), token=token)

    def _run():
        state = None
        try:
            if gate_kind == "resume":
                state = TaskState.latest_unfinished()
                if not state:
                    host["speak"]("There is no unfinished task to resume, Boss.")
                    return
                host["speak"](f"Resuming: {state['request'][:80]}, from the {state['phase']} step.")
                spec = {"kind": state["kind"] or "app", "title": state["request"], "requirements": state["requirements"]}
            else:
                state = TaskState(raw_text or cmd)
                host["speak"]("On it, Boss. Let me understand that first.")
                spec = router.classify(host, state, raw_text or cmd, gate_kind)
                state["kind"] = spec["kind"]
                state.done("understood request as " + spec["kind"])
            _execute(host, state, spec, gate_kind)
        except Exception as e:                      # never die silently, never hide it
            host["log_debug"]("jarvis_agent", e)
            if state is not None:
                state.error(repr(e))
            host["speak"]("Something went wrong in that task, Boss. I saved its state and logged the error; say 'resume the last task' to continue.")
        finally:
            if _ACTIVE["token"] is token:
                _LOCK.release()

    th = threading.Thread(target=_run, daemon=True)
    ctxs = host.get("SKILL_CTX")
    if isinstance(ctxs, dict):
        ctxs.setdefault("threads", []).append(th)   # lets `--say "..."` wait for the task instead of exiting mid-build
    th.start()
    return True


def _cli_wait_for_build(host):
    """`--say "build me a website for X"` ended after a few seconds and killed the build: the legacy trigger starts its
    own background thread and never tells --say to wait for it. In CLI mode only, hand --say a thread that lives exactly
    as long as the website build holds its lock. The voice loop is not affected (JARVIS keeps running there anyway)."""
    lock, ctxs = host.get("_BUILD_LOCK"), host.get("SKILL_CTX")
    if not host.get("CLI_MODE") or lock is None or not isinstance(ctxs, dict) or not lock.locked():
        return

    def _wait():
        t0 = time.time()
        while lock.locked() and time.time() - t0 < STALE_SECS:
            time.sleep(0.5)
    th = threading.Thread(target=_wait, daemon=True)
    ctxs.setdefault("threads", []).append(th)
    th.start()


def install(host):
    orig = host.get("project_builder_commands")
    if orig is None or getattr(orig, "_jarvis_agent", False):
        return False

    def project_builder_commands(cmd, raw_text):
        try:
            if host["get_config"]("universal_agent", "on") == "on" and handle(cmd, raw_text, host):
                return True
        except Exception as e:
            host["log_debug"]("jarvis_agent/install", e)
        handled = orig(cmd, raw_text)
        try:
            _cli_wait_for_build(host)
        except Exception as e:
            host["log_debug"]("jarvis_agent/cli_wait", e)
        return handled

    project_builder_commands._jarvis_agent = True
    host["project_builder_commands"] = project_builder_commands
    try:                                            # Phase 2: 'website for X' -> new builder first, legacy builder as fallback
        from . import website_bridge
        website_bridge.install(host)
    except Exception as e:
        host["log_debug"]("jarvis_agent/website_bridge", e)
    try:                                            # separate add-ons (phone brands, pattern lock, usage log): each installs itself, none can stop JARVIS
        import jarvis_addons
        jarvis_addons.install(host)
    except Exception as e:
        host["log_debug"]("jarvis_addons", e)
    return True
