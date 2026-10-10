"""Voice setup for the owner of THIS copy of JARVIS - one guided command instead of five.

    venv\\Scripts\\python.exe -m jarvis_addons.voice_setup            guided: model -> your voice -> other voices -> limits -> lock on
    venv\\Scripts\\python.exe -m jarvis_addons.voice_setup --status   only show what is enrolled (changes nothing)
    venv\\Scripts\\python.exe -m jarvis_addons.voice_setup --cohort   only re-record the comparison voices (checked) + limits

It only calls JARVIS's existing voice functions; the voice print is saved in this folder's own database under the owner's
name, and nothing is shared with any other installation."""
import contextlib
import io
import re
import sys

from ._host import load


def status(L):
    """-> dict describing the voice lock of this installation. Reads only."""
    owner = L.CURRENT_ACTIVE_USER
    prints = L.spk_load_all(include_cohort=True)
    people = L.people_all()
    known = {p["name"].lower() for p in people}
    owner_key = next((n for n in prints if n.lower() == owner.lower()), None)
    owner_rows = [p for p in people if p["role"] == "owner"]
    foreign = sorted(n for n in prints if not n.startswith("__") and n.lower() != owner.lower() and n.lower() not in known)
    wrong_owner = sorted(p["name"] for p in owner_rows if p["name"].lower() != owner.lower())
    return {
        "owner": owner,
        "model_ready": bool(L.spk_ready()),
        "owner_prints": int(len(prints[owner_key])) if owner_key else 0,
        "legacy_vectors": len(owner_rows[0]["vectors"]) if owner_rows and owner_rows[0]["name"].lower() == owner.lower() else 0,
        "cohort": int(len(prints.get("__cohort__", []))),
        "others": {n: int(len(v)) for n, v in prints.items() if not n.startswith("__") and n.lower() != owner.lower()},
        "foreign_prints": foreign,                  # voice prints of someone who is not in this owner's people list
        "foreign_owner_rows": wrong_owner,          # an 'owner' row that is not this owner = data from another installation
        "threshold": L.spk_threshold(), "margin": L.spk_margin(),
        "lock": L.get_config("voice_lock", "on"),
        "lock_active": bool(L.voice_lock_active()),
    }


def show(s):
    print(f"Owner of this JARVIS      : {s['owner']}")
    print(f"Voice model usable        : {'yes' if s['model_ready'] else 'NO (not installed, or blocked by Windows) -> the built-in voice lock is used'}")
    print(f"Built-in lock samples     : {s['legacy_vectors']}" + ("" if s["legacy_vectors"] >= 5 else "   -> not learned yet (say: learn my voice)"))
    print(f"Model fingerprints        : {s['owner_prints']}" + ("" if s["owner_prints"] >= 4 else "   -> not enrolled yet"))
    print(f"Other voices (comparison) : {s['cohort']}" + ("" if s["cohort"] >= 6 else "   -> not recorded yet"))
    print(f"Limits                    : threshold {s['threshold']:.2f}, margin {s['margin']:.2f}")
    print(f"Voice lock                : {s['lock']}" + (" (active)" if s["lock_active"] else " (not active yet - it needs your voice first)"))
    if s["others"]:
        print("Other people enrolled     : " + ", ".join(f"{n} ({c})" for n, c in s["others"].items()))
    for n in s["foreign_prints"]:
        print(f"WARNING: there is a voice print for '{n}', who is not in your people list. If you do not know this person, this database came from someone else.")
    for n in s["foreign_owner_rows"]:
        print(f"WARNING: this database has an owner entry for '{n}', not for you. Do not use a database copied from another JARVIS.")
    return not (s["foreign_prints"] or s["foreign_owner_rows"])


def calibrate_and_apply(L):
    """Runs JARVIS's own --spk-calibrate and, when it finds a clean split, applies the two limits it prints
    (the old flow made the user copy them by hand). -> (applied, threshold, margin)"""
    buf = io.StringIO()

    class Tee(io.TextIOBase):
        def write(self, s):
            sys.__stdout__.write(s)
            sys.__stdout__.flush()
            return buf.write(s)

    with contextlib.redirect_stdout(Tee()):
        ok = L.spk_calibrate()
    return apply_limits(L, buf.getvalue()) if ok else (False, None, None)


def apply_limits(L, text):
    m = re.search(r"--spk-threshold (\d\.\d+)\s+--spk-margin (\d\.\d+)", text)
    if not m or "Clean split" not in text:
        return False, (float(m.group(1)) if m else None), (float(m.group(2)) if m else None)
    L.set_config("spk_thr", m.group(1))
    L.set_config("spk_margin", m.group(2))
    return True, float(m.group(1)), float(m.group(2))


MIN_LOUDNESS = 120          # a played voice must reach the microphone clearly. Measured: playback at a low volume arrived at 2-48
                            # (the owner's own voice: 700-900) and every "fingerprint" of it was the fingerprint of near-silence
MAX_SAMENESS = 0.97         # different voices never look this alike; the broken cohort saved on 2026-09-21 measured 1.00
COHORT_SENTENCE = "Please open the browser and search for the latest news about the weather in my city."


def _loudness(audio):
    import numpy as np
    x = np.frombuffer(audio.get_raw_data(convert_rate=16000, convert_width=2), dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def hear(L, voice, sentence):
    """One synthetic voice through the speakers and the microphone -> (fingerprint or None, loudness at the microphone)."""
    import os
    path = os.path.join(L.JARVIS_DIR, "_spkplay.mp3")
    try:
        L._tts_generate(sentence, voice, path)
        audio = L.record_while_playing(path)
        return L.spk_embed(audio), _loudness(audio)
    except Exception as e:
        L.log_debug("voice_setup/hear", e)
        return None, 0.0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def cohort_problem(embs, loud):
    """Why a set of recorded comparison voices must NOT be saved, or None when it is good."""
    import numpy as np
    if len(embs) < 6:
        return f"only {len(embs)} voices could be recorded (6 are needed)"
    if min(loud) < MIN_LOUDNESS:
        return f"the voices reached the microphone too quietly (lowest {min(loud):.0f}, needed {MIN_LOUDNESS}) - turn the speaker volume up to about 50 percent"
    E = np.stack(embs)
    S = E @ E.T
    same = float(S[np.triu_indices(len(E), 1)].mean())
    if same > MAX_SAMENESS:
        return f"the recordings are all alike ({same:.2f}) - the microphone did not really hear different voices"
    return None


def record_cohort_checked(L, hear_fn=hear):
    """Records the comparison voices and saves them ONLY when the recordings are real. -> (saved, message)"""
    if not L.spk_ready():
        return False, "the voice model is not usable right now"
    try:
        others = L.other_jarvis_processes()
    except Exception:
        others = []
    if others:
        return False, "JARVIS is running in another window - close it first (it would hear these voices and answer them)"
    first, loud = hear_fn(L, L._COHORT_VOICES[0], COHORT_SENTENCE)
    if loud < MIN_LOUDNESS:                                                     # stop after ONE voice instead of playing two minutes of them for nothing
        return False, f"the test voice reached the microphone too quietly ({loud:.0f}, needed {MIN_LOUDNESS}) - turn the speaker volume up to about 50 percent and run this again"
    embs, louds = ([first], [loud]) if first is not None else ([], [])
    for v in L._COHORT_VOICES[1:]:
        e, ld = hear_fn(L, v, COHORT_SENTENCE)
        print(f"  {v:34} " + ("recorded" if e is not None else "skipped"))
        if e is not None:
            embs.append(e)
            louds.append(ld)
    why = cohort_problem(embs, louds)
    if why:
        return False, why + ". Nothing was saved; the comparison voices are as they were."
    L.spk_save("__cohort__", embs, replace=True)
    return True, f"{len(embs)} comparison voices saved"


def cohort_only(L):
    """Re-record the comparison voices (checked) and then find the limits for the owner's voice."""
    print("JARVIS will play other voices through the speakers. Stay silent; volume at about 50 percent.\n")
    ok, msg = record_cohort_checked(L)
    print(("Done: " if ok else "STOP: ") + msg)
    if not ok:
        return 1
    print("\nNow YOUR voice, three short sentences, to find the best limits.")
    applied, t, m = calibrate_and_apply(L)
    print(f"Limits applied: threshold {t:.2f}, margin {m:.2f}" if applied else "No clean split was found, so the limits stay as they were.")
    return 0


def guided(L):
    print("=" * 62 + f"\n  Voice setup for {L.CURRENT_ACTIVE_USER}  (about 5 minutes, in a quiet room)\n" + "=" * 62)
    s = status(L)
    if not show(s):
        print("\nSTOP: fix the warnings above first (start from a clean package).")
        return 2
    if not s["model_ready"]:
        print("\n[1/4] Downloading the voice model ...")
        if not L.setup_voiceid():
            return 1
        L._SPK["tried"] = False
        if not L.spk_ready():
            # seen for real: Windows "Application Control" blocks the model's DLL on some PCs. JARVIS's built-in voice lock
            # (voice features, no extra model) still works there, so the owner is enrolled with that instead of being left unprotected.
            print("\nThe voice model cannot be loaded on this PC (Windows may be blocking it). Using JARVIS's built-in voice lock instead.")
            print("[2/2] Your voice. Say each sentence after the beep.")
            ok = L.learn_owner_voice()
            print()
            show(status(L))
            return 0 if ok else 1
    print("\n[2/4] Your voice. Say each sentence after the beep.")
    if not L.spk_enroll_owner():
        return 1
    print("\n[3/4] Other voices, played through the speakers. Stay silent.")
    cohort_ok, cohort_msg = record_cohort_checked(L)
    if not cohort_ok:
        print(f"The comparison voices were not saved: {cohort_msg}\nYour voice is saved and the lock works with the default limits.")
    else:
        print("\n[4/4] Finding the best limits for YOUR voice.")
        applied, t, m = calibrate_and_apply(L)
        if applied:
            print(f"Limits applied: threshold {t:.2f}, margin {m:.2f}")
        else:
            print("No clean split was found, so the default limits stay. You can run this again in a quieter room.")
    L.set_config("voice_lock", "on")
    print()
    show(status(L))
    print("\nDone. Check any time with:  venv\\Scripts\\python.exe live_cmd_console.py --spk-test")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    L = load()
    if "--status" in argv:
        return 0 if show(status(L)) else 3
    if "--cohort" in argv:
        return cohort_only(L)
    return guided(L)


if __name__ == "__main__":
    sys.exit(main())
