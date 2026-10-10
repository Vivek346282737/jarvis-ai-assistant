"""'Phone ki battery kitni hai' used to be answered with the laptop's CPU and RAM: JARVIS has ONE 'battery / system status'
command, and when the phone could not be read its battery was silently left out. This add-on changes nothing in that command;
it only makes the answer fit the question:
  - the question is about the PHONE  -> the phone's battery, or a clear 'the phone is not connected';
  - a battery question without 'phone' and the phone cannot be read -> the usual status plus that same clear sentence;
  - anything else (system status, morning briefing) -> exactly as before."""
import re
import time

PHONE = re.compile(r"\b(phone|phones|fone|fon|phon|mobile|android)\b|फोन|फ़ोन|मोबाइल", re.I)
BATTERY = re.compile(r"\b(battery|batteries|charge|charging)\b|बैटरी|चार्ज", re.I)
NOT_CONNECTED = "The phone is not connected right now, so I cannot read its battery."
FRESH = 20                   # seconds a heard sentence counts as 'the question being answered'


def kind(texts):
    """'phone' = a question about the phone's battery, 'battery' = a battery question, '' = something else."""
    bat = [t for t in texts if BATTERY.search(t or "")]
    if not bat:
        return ""
    return "phone" if any(PHONE.search(t or "") for t in texts) else "battery"


def answer(question_kind, level, usual):
    """level = the phone's battery percent or None; usual = a function giving JARVIS's normal status sentence."""
    if question_kind == "phone":
        return f"Your phone battery is at {level} percent." if level is not None else NOT_CONNECTED
    text = usual()
    if question_kind == "battery" and "phone battery" not in text:
        text += " " + NOT_CONNECTED
    return text


def install(host):
    need = ("get_system_metrics", "get_real_phone_battery", "log_debug")
    if any(not callable(host.get(k)) for k in need) or getattr(host["get_system_metrics"], "_phone_battery", False):
        return False
    heard = {"ts": 0.0, "text": "", "said": False}              # the command being handled; said = the sentence as it was spoken

    # the sentence as it was said (the language step turns it into the fixed words 'battery status' afterwards)
    if callable(host.get("nlu_command")):
        orig_nlu = host["nlu_command"]

        def nlu_command(intent, slots, raw_text, *a, **k):
            heard.update(ts=time.time(), text=str(raw_text or ""), said=True)
            return orig_nlu(intent, slots, raw_text, *a, **k)
        host["nlu_command"] = nlu_command

    if callable(host.get("check_dynamic_rules")):               # the first thing every command passes through
        orig_rules = host["check_dynamic_rules"]

        def check_dynamic_rules(cmd, *a, **k):
            if not (heard["said"] and time.time() - heard["ts"] < 3):          # the spoken sentence wins over its fixed wording
                heard.update(ts=time.time(), text=str(cmd or ""), said=False)
            return orig_rules(cmd, *a, **k)
        host["check_dynamic_rules"] = check_dynamic_rules

    orig_metrics = host["get_system_metrics"]

    def get_system_metrics(*a, **k):
        k_ = kind([heard["text"]]) if time.time() - heard["ts"] < FRESH else ""
        heard.update(ts=0.0, text="", said=False)
        if k_ == "phone":
            return answer(k_, host["get_real_phone_battery"](), None)
        return answer(k_, None, lambda: orig_metrics(*a, **k))

    get_system_metrics._phone_battery = True
    host["get_system_metrics"] = get_system_metrics
    return True
