"""Model routing on top of JARVIS's EXISTING backends, built for SPEED and a hard latency bound.
Every call is a race: the fast provider (Groq, each call already capped at 25s) starts first; if it has not answered within
HEDGE_AFTER seconds the slower chain (Gemini -> Ollama, via llm_json) starts too and whichever produces a valid answer FIRST wins.
Nothing can block longer than DEADLINE. If a call still yields nothing, the caller's task state is untouched - the task
continues/resumes instead of restarting. (Private-data profiles never go to Groq.)"""
import queue
import threading
import time

HEDGE_AFTER = 12.0
DEADLINE = 60.0
LOCAL_DEADLINE = 170.0                          # last resort, local model only: it can never be rate limited, but a 7b on CPU needs minutes for a whole page
GROQ_TPM = 7000                                 # Groq free tier allows ~8000 tokens/minute PER MODEL for the whole account (all keys share it); keep a margin
GROQ_REQ_CAP = 8000                             # max answer size per request. Measured: a full homepage needs ~4.2K output tokens; capping lower made Groq
                                                # fail with 400 json_validate_failed "max completion tokens reached" and pushed every page to the slow local model
CHARS_PER_TOKEN = 3.8                           # measured on a real page prompt (13.5K chars = 3,505 tokens); //3 overestimated by ~30%
EXPECTED_OUT = 4500                             # what a page really uses - reserve this, not the whole max_tokens, or the budget empties 2x too fast
USE_GEMINI = False                              # owner's choice: Gemini is down/unusable, so builds use only Groq + the local model (tests switch it on to drive llm_json)
LOCAL_VISION = False                            # measured live: the local vision model timed out twice (2 x 150s) on 2 screenshots and never answered -
                                                # 5 minutes lost per build for nothing. Turn on only on a machine with a GPU.
GROQ_MIN_OUT = 1200                           # below this the answer would be cut off, so a call that cannot fit is not sent to Groq at all
_GWIN, _GLOCK = {}, threading.Lock()            # "smart"/"fast" -> [(time, tokens)] spent in the last 60s, across every thread

# profile -> (prefer_free for llm_json, groq size, may use groq?)
PROFILES = {
    "plan": (False, "smart", True),
    "debug": (False, "smart", True),
    "code": (False, "smart", True),
    "classify": (True, "fast", True),
    "summarize": (True, "fast", True),
    "private": (True, "fast", False),
    "vision": (False, "smart", False),         # screenshots: Gemini vision, then a local vision model; Groq has none
}


def _race(starters, deadline):
    """starters: [(start_delay_seconds, callable)] in priority order. Returns the first truthy result, else None."""
    q = queue.Queue()

    def worker(fn):
        try:
            q.put(fn())
        except Exception:
            q.put(None)

    t0, started, finished = time.time(), 0, 0
    pending = list(starters)
    while True:
        elapsed = time.time() - t0
        while pending and (elapsed >= pending[0][0] or started == finished):     # start the next one when its delay passed, or all running ones already failed
            threading.Thread(target=worker, args=(pending.pop(0)[1],), daemon=True).start()
            started += 1
        if finished >= started and not pending:
            return None
        wait = deadline - elapsed
        if pending:
            wait = min(wait, max(0.05, pending[0][0] - elapsed))
        if wait <= 0:
            return None
        try:
            r = q.get(timeout=wait)
        except queue.Empty:
            continue
        finished += 1
        if r:
            return r


def _groq_reserve(size, tokens_in, want_out, n_keys=1):
    """Groq counts prompt + requested output against a per-minute token budget shared by ALL keys (keys of one account are one quota).
    Six parallel page-writers (~5K prompt + 6K output each) blow through that in one second and every key answers 429 at once - that
    was the real cause of 'all limits finished together'. So: reserve tokens before calling. Returns (task, max_tokens) or None when
    this call does not fit right now, in which case it simply goes to Gemini/local instead of stampeding Groq."""
    now = time.time()
    with _GLOCK:
        for sz in (["smart", "fast"] if size == "smart" else ["fast"]):
            w = _GWIN.setdefault(sz, [])
            w[:] = [(t, n) for t, n in w if now - t < 60]
            room = GROQ_TPM * max(1, n_keys) - sum(n for _, n in w) - tokens_in       # keys from SEPARATE accounts each bring their own minute budget
            out = min(want_out, GROQ_REQ_CAP)
            if room < min(out, EXPECTED_OUT):
                continue

            if out >= min(GROQ_MIN_OUT, want_out):                              # a SMALL question (classify: 500, file-pick: 800) is not "cut off" - it used to be refused here
                                                                                # every time, wait 45s for nothing and then fall to the slow local model (1-2 min lost per task)
                w.append((now, tokens_in + min(out, EXPECTED_OUT)))
                return sz, out
    return None


def _n_keys(host):
    try:
        return len(host["groq_keys"]())
    except Exception:
        return 1


def _groq_all_cooling(host, size):
    """True only when EVERY configured Groq key is currently resting (429'd) on the model this call would use.
    A parallel burst of file-generation calls can exhaust all keys in seconds (seen live: 8 keys, both models,
    all cooling within ~12s) - once that happens, racing Groq first just burns HEDGE_AFTER doing nothing, so
    skip straight to the free chain instead of waiting it out on every single call until the cooldown clears."""
    try:
        keys = host["groq_keys"]()
        if not keys:
            return False
        model = host["GROQ_MODEL_SMART"] if size == "smart" else host["GROQ_MODEL_FAST"]
        cooldown = host["_GROQ_COOLDOWN"]
        now = time.time()
        return all(cooldown.get(f"{k}:{model}", 0) > now for k in keys)
    except Exception:
        return False


def _ollama_ready(host, image):
    """Direct local-model check, bypassing llm_json entirely. llm_json's own Gemini leg can block up to 150s
    (its internal timeout) before Ollama ever gets a turn inside it - during a real Gemini outage (seen live:
    503/504/read-timeout on every call) that alone can outlast our whole DEADLINE, so Ollama never answers in
    time even though it is sitting there idle and ready. Racing it directly, in parallel, fixes that."""
    try:
        if host.get("get_config") and host["get_config"]("free_ai", "on") != "on":
            return False
        if image is not None:
            return bool(host.get("ollama_vision_model") and host["ollama_vision_model"]())
        return bool(host.get("ollama_up") and host["ollama_up"]())
    except Exception:
        return False


GROQ_RECHECK = 5.0                              # while the local model works, look this often for a Groq key that finished its cooldown
GROQ_WAIT = 45.0                               # a full minute-budget frees itself within 60s; waiting for fast Groq beats a slow local model


def _log(state, entry):
    if state is not None:
        state["models"].append(entry)
        state.save()


def _groq_then_local(host, state, profile, system, prompt, image, max_tokens, size, groq_ready, ollama_ready):
    """No Gemini. Groq (6 separate accounts = 6 budgets) first; if the minute budget is full, wait for it to free up
    rather than stampede; if Groq still gives nothing, the local model gets the time it really needs."""
    if groq_ready:
        t0, tries = time.time(), 0
        while tries < 3 and time.time() - t0 < GROQ_WAIT + 30:
            slot = _groq_reserve(size, int((len(system) + len(prompt)) / CHARS_PER_TOKEN), min(max_tokens, 8000), _n_keys(host))
            if not slot:
                if time.time() - t0 > GROQ_WAIT:
                    break
                time.sleep(2)
                continue
            tries += 1
            gtask, gout = slot
            d = _race([(0.0, lambda: host["groq_json"](system, prompt, task=gtask, max_tokens=gout))], 30.0)
            _log(state, {"profile": profile, "provider": "groq", "ok": bool(d)})
            if d:
                return d
    if not ollama_ready:
        return None
    # Local model starts now, but Groq keys come back ~60s after a 429 - keep checking, and the moment a key (and the minute budget)
    # is free again send the same request to Groq too. Whichever answers first wins, so a recovered Groq never waits behind a slow local model.
    q = queue.Queue()

    def run(fn, tag):
        try:
            q.put((tag, fn()))
        except Exception:
            q.put((tag, None))
    threading.Thread(target=run, args=(lambda: host["_free_json"](system, prompt, image=image, max_tokens=max_tokens), "local"), daemon=True).start()
    running, t0, groq_tries, next_check = 1, time.time(), 0, 0.0
    can_groq = image is None and host.get("groq_json") is not None and PROFILES.get(profile, (0, 0, True))[2]
    while running and time.time() - t0 < LOCAL_DEADLINE:
        now = time.time()
        if can_groq and groq_tries < 3 and now >= next_check:
            next_check = now + GROQ_RECHECK
            try:
                back = bool(host["groq_configured"]()) and not _groq_all_cooling(host, size)
            except Exception:
                back = False
            slot = _groq_reserve(size, int((len(system) + len(prompt)) / CHARS_PER_TOKEN), min(max_tokens, 8000), _n_keys(host)) if back else None
            if slot:
                groq_tries += 1
                running += 1
                gtask, gout = slot
                threading.Thread(target=run, args=(lambda: host["groq_json"](system, prompt, task=gtask, max_tokens=gout), "groq"), daemon=True).start()
        try:
            tag, d = q.get(timeout=1.0)
        except queue.Empty:
            continue
        running -= 1
        _log(state, {"profile": profile, "provider": tag, "ok": bool(d)})
        if d:
            return d
    return None


def ask_json(host, state, profile, system, prompt, image=None, max_tokens=3000):
    llm = host["llm_json"]
    prefer_free, size, may_groq = PROFILES.get(profile, (False, "smart", True))
    groq_ok = may_groq and image is None and host.get("groq_json") is not None
    groq_ready = False
    if groq_ok:
        try:
            groq_ready = bool(host["groq_configured"]()) and not _groq_all_cooling(host, size)
        except Exception:
            groq_ready = False
    ollama_ready = _ollama_ready(host, image) and host.get("_free_json") is not None
    if not USE_GEMINI and image is not None and not LOCAL_VISION:
        _log(state, {"profile": profile, "provider": "none", "ok": False, "why": "no cloud vision; local vision too slow on this PC"})
        return None                                                             # caller reports the screenshot review as skipped, honestly
    if not USE_GEMINI:
        return _groq_then_local(host, state, profile, system, prompt, image, max_tokens, size, groq_ready, ollama_ready)
    for pf in (prefer_free, not prefer_free):
        def via_llm(pf=pf):
            return llm(system, prompt, image=image, max_tokens=max_tokens, prefer_free=pf, task=size)
        starters = [(0.0, via_llm)]
        slot = _groq_reserve(size, int((len(system) + len(prompt)) / CHARS_PER_TOKEN), min(max_tokens, 8000), _n_keys(host)) if groq_ready and pf == prefer_free else None
        if slot:                                                                # only when it fits Groq's token budget right now
            gtask, gout = slot
            starters = [(0.0, lambda: host["groq_json"](system, prompt, task=gtask, max_tokens=gout)),
                        (HEDGE_AFTER, via_llm)]
        if ollama_ready:                                                        # give the local model its OWN fair shot instead of waiting on it
            starters = starters + [(HEDGE_AFTER, lambda: host["_free_json"](system, prompt, image=image, max_tokens=max_tokens))]
        try:
            d = _race(starters, DEADLINE)
        except Exception as e:
            host["log_debug"]("jarvis_agent/models", e)
            d = None
        if state is not None:
            state["models"].append({"profile": profile, "prefer_free": pf, "ok": bool(d)})
            state.save()
        if d:
            return d
    if ollama_ready:                                                            # cloud is down/limited: the local model alone, given the time it really needs
        try:
            d = _race([(0.0, lambda: host["_free_json"](system, prompt, image=image, max_tokens=max_tokens))], LOCAL_DEADLINE)
        except Exception as e:
            host["log_debug"]("jarvis_agent/models", e)
            d = None
        if state is not None:
            state["models"].append({"profile": profile, "local_only": True, "ok": bool(d)})
            state.save()
        if d:
            return d
    return None
    return None
