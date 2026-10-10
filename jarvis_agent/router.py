"""Understands WHAT the user wants. A cheap generic gate decides whether a sentence is a big autonomous task at all (so
ordinary commands like 'open chrome' are never stolen); the model then classifies the request and extracts the
requirements. There is no per-business logic anywhere - 'coffee' and 'gym' take exactly the same path."""
import os
import re

from .models import ask_json

_BUILD = r"(?:build|create|make|develop|generate|design|code|write|set\s*up|implement)"
_THING = (r"(?:web\s*site|website|web\s*app|webpage|landing\s*page|site|app|application|dashboard|platform|system|crm|erp|api|backend|frontend|"
          r"portfolio|game|script|program|software|service|tool|bot|marketplace|admin\s*panel|cms|blog|e-?commerce|saas|tracker|manager|management)")
GATES = [
    ("build", re.compile(rf"^(?:jarvis[,\s]+)?(?:please\s+)?(?:can you\s+)?{_BUILD}\b.*\b{_THING}\b", re.I)),
    ("coding", re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:fix|debug|improve|refactor|optimi[sz]e|upgrade|add|open)\b.*\b(?:project|codebase|repo|repository|my app|this app|my website|this website|my api|this api|module|authentication|login|payments?)\b", re.I)),
    ("coding", re.compile(r"^(?:jarvis[,\s]+)?(?:find why|why is)\b.*\b(?:crash\w*|failing|broken|not working)\b", re.I)),
    ("data", re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:analy[sz]e|clean|summari[sz]e|visuali[sz]e|build a dashboard from)\b.*\b(?:csv|excel|xlsx|dataset|data|json|files?)\b", re.I)),
    ("research", re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?research\b.+", re.I)),
    ("resume", re.compile(r"^(?:jarvis[,\s]+)?(?:resume|continue)\b.*\b(?:task|build|project|work)\b", re.I)),
]

CLASSIFY_SYSTEM = (
    "You classify a request to an autonomous software agent and extract its requirements. Reply ONLY JSON: "
    '{"kind": "website|app|fullstack|api|data|coding|research", "title": "short name", "requirements": ["concrete requirement", ...], '
    '"needs_backend": true|false, "needs_database": true|false, "needs_auth": true|false, "existing_project": true|false, '
    '"private": true|false, "topic": "only for research"}. '
    "kind=website: static/marketing site. app: a self-contained application. fullstack: needs backend and storage. api: backend service only. "
    "data: analysing/cleaning/visualising given files. coding: modify/fix/improve an EXISTING project. research: gather information into a report. "
    "Only set needs_* true when the request genuinely needs it. Do not invent requirements the user did not imply."
)


def gate(cmd):
    c = (cmd or "").strip(" .!?")
    if len(c.split()) < 3 and not re.match(r"^(?:jarvis[,\s]+)?(?:resume|continue)\b", c, re.I):
        return None
    for name, rx in GATES:
        if rx.search(c):
            return name
    return None


_PATH_RX = re.compile(r"""(?:"([A-Za-z]:\\[^"]+)"|'([A-Za-z]:\\[^']+)'|([A-Za-z]:\\\S+))""")


def extract_path(text):
    m = _PATH_RX.search(text or "")
    if not m:
        return None
    p = next(g for g in m.groups() if g).strip().rstrip(".,")
    return p if os.path.exists(p) else None


def classify(host, state, text, gate_kind):
    d = ask_json(host, state, "classify", CLASSIFY_SYSTEM, f"Request: {text}", max_tokens=500) or {}
    kind = str(d.get("kind") or "").lower()
    if kind not in ("website", "app", "fullstack", "api", "data", "coding", "research"):
        kind = {"build": "app", "coding": "coding", "data": "data", "research": "research"}.get(gate_kind, "app")
    if gate_kind in ("coding", "data", "research"):            # the sentence's own shape is more reliable than a model's guess for these
        kind = gate_kind
    d["kind"] = kind
    d["title"] = str(d.get("title") or text)[:80]
    d["requirements"] = [str(r) for r in (d.get("requirements") or [])][:14]
    return d
