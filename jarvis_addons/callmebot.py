"""'JARVIS calls me': a real Telegram voice call that speaks a short message, through the free CallMeBot service.

A Telegram BOT cannot place calls; CallMeBot runs a user account that can. Before it may call you, you must (once) open
@CallMeBot_txtbot in Telegram and press Start, and your Telegram account needs a username.

Privacy: the text of the call passes through CallMeBot's servers (a third party). JARVIS therefore never puts personal
content into a call it places by itself - the call only says that something needs attention; the details go to your
own Telegram chat. The call is one-way: it speaks, it cannot hear you."""
import re
import socket
import urllib.parse
import urllib.request

API = "http://api.callmebot.com/start.php"
MAX_TEXT = 256                      # CallMeBot cuts longer text off
_BAD = re.compile(r"error|not\s+authori[sz]ed|missing|invalid|unknown\s+user|user\s+not\s+found|blocked|limit", re.I)


def clean_user(user):
    """'@Vivek_01' / 'vivek_01' / '+919876543210' -> the form CallMeBot wants, or None."""
    u = str(user or "").strip()
    if re.fullmatch(r"\+\d{8,15}", u):
        return u
    u = u.lstrip("@")
    return "@" + u if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", u) else None


def build_url(user, text, lang=None, repeat=2):
    params = {"user": user, "text": " ".join(str(text or "").split())[:MAX_TEXT], "rpt": int(repeat)}
    if lang:
        params["lang"] = lang
    return API + "?" + urllib.parse.urlencode(params)


def _open_ipv4(url, timeout):
    # the same lesson as with Telegram: this connection's IPv6 route is unreliable, so the lookup is limited to IPv4
    orig = socket.getaddrinfo
    socket.getaddrinfo = lambda host, port, family=0, *a, **k: orig(host, port, socket.AF_INET, *a, **k)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    finally:
        socket.getaddrinfo = orig


def read_answer(status, body):
    """-> (accepted, short plain text of what CallMeBot said)."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body or "")).strip()
    return (status == 200 and bool(text) and not _BAD.search(text)), text[:300]


def call(user, text, lang=None, repeat=2, timeout=120, opener=_open_ipv4):
    """Places the call. -> (accepted, what CallMeBot answered). Never raises."""
    u = clean_user(user)
    if not u:
        return False, "no valid Telegram username (or +number) is saved"
    if not str(text or "").strip():
        return False, "nothing to say"
    try:
        status, body = opener(build_url(u, text, lang, repeat), timeout)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"
    return read_answer(status, body)
