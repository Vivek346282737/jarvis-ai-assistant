"""Verification that does NOT depend on what happens to be on the user's screen:
  lint()    - deterministic HTML checks (SEO/a11y basics, broken local links/assets, placeholders)
  capture() - screenshots through HEADLESS Chrome/Edge with its own throw-away profile (desktop + phone width)
  review()  - a vision model judges the screenshots against a rubric; only MAJOR defects count, and if no vision model
              is available the check is reported as SKIPPED - it never fakes a pass and never blocks on a guess."""
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
from html.parser import HTMLParser

from . import util
from .models import ask_json

_SKIP_REF = re.compile(r"^(?:https?:|mailto:|tel:|data:|javascript:|#|//|/|\{\{|\{%)", re.I)


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = self.viewport = self.desc = self.in_title = self.has_html_tag = self.has_head_tag = False
        self.title = ""
        self.h1 = 0
        self.refs, self.imgs_no_alt, self.text, self.dead_forms = [], 0, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.has_html_tag = True
            if a.get("lang"):
                self.lang = True
        elif tag == "head":
            self.has_head_tag = True
        elif tag == "meta":
            n = (a.get("name") or "").lower()
            self.viewport |= n == "viewport"
            self.desc |= n == "description" and bool((a.get("content") or "").strip())
        elif tag == "title":
            self.in_title = True
        elif tag == "h1":
            self.h1 += 1
        elif tag == "form":
            if not a.get("data-api") and a.get("data-success") is None:            # neither wired to a backend nor a demo confirm - submit would silently do nothing
                self.dead_forms += 1
        elif tag == "img":
            if "alt" not in a:
                self.imgs_no_alt += 1
            if a.get("src"):
                self.refs.append(a["src"])
        elif tag in ("script",) and a.get("src"):
            self.refs.append(a["src"])
        elif tag == "link" and a.get("href"):
            self.refs.append(a["href"])
        elif tag == "a" and a.get("href"):
            self.refs.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        self.text.append(data)


def lint(root):
    """-> list of concrete problems for every .html page (templates with {{ }} are tolerated)."""
    problems = []
    for rel in util.tree(root):
        if not rel.lower().endswith((".html", ".htm")) or rel.startswith(("_shots", "jarvis_checkpoints")):
            continue
        src = util.read_text(os.path.join(root, rel), 10 ** 6)
        s = _Scan()
        try:
            s.feed(src)
        except Exception:
            problems.append(f"{rel}: the HTML could not be parsed")
            continue
        # a Jinja child template ({% extends %}) inherits <head> from its base; a base/layout template defines
        # {% block %} slots for children to fill (it never has its own <h1> etc.); an {% include %}'d fragment
        # (navbar/footer/etc.) is not a standalone document at all - none of these three should be judged as a full page
        is_partial = bool(re.search(r"\{%-?\s*(extends|block)\b", src)) or not (s.has_html_tag and s.has_head_tag)
        if not is_partial:
            if not s.lang:
                problems.append(f'{rel}: <html> has no lang attribute')
            if not s.viewport:
                problems.append(f'{rel}: missing <meta name="viewport"> - the page will not be responsive on phones')
            if not s.title.strip():
                problems.append(f"{rel}: missing or empty <title>")
            if not s.desc:
                problems.append(f'{rel}: missing <meta name="description"> (SEO)')
            if s.h1 != 1:
                problems.append(f"{rel}: has {s.h1} <h1> elements, it should have exactly one")
        if s.imgs_no_alt:
            problems.append(f"{rel}: {s.imgs_no_alt} <img> without an alt attribute")
        if s.dead_forms:
            problems.append(f'{rel}: {s.dead_forms} <form> has neither data-api (a real backend) nor data-success (a demo confirm) - submitting it would do nothing')
        if re.search(r"lorem ipsum|your text here|placeholder text", " ".join(s.text), re.I):
            problems.append(f"{rel}: contains placeholder text (lorem ipsum) instead of real content")
        base = os.path.dirname(os.path.join(root, rel))
        for ref in s.refs:
            ref = ref.split("#")[0].split("?")[0].strip()
            if not ref or _SKIP_REF.match(ref) or "{{" in ref:
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, ref))):
                problems.append(f"{rel}: links to '{ref}' which does not exist")
    return problems[:12]


def _browser():
    cands = [shutil.which("chrome"), shutil.which("msedge"),
             r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def _shoot(exe, url, path, w, h):
    prof = tempfile.mkdtemp(prefix="jarvis_shot_")
    try:
        # --force-prefers-reduced-motion: --virtual-time-budget never fires real scroll events, so IntersectionObserver
        # reveal animations would stay at opacity:0 forever in a headless capture; the design system's own reduced-motion
        # rule (a real accessibility feature, not a hack) shows content immediately instead.
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                        "--force-prefers-reduced-motion", f"--user-data-dir={prof}", f"--window-size={w},{h}",
                        "--virtual-time-budget=6000", f"--screenshot={path}", url],
                       capture_output=True, timeout=60, creationflags=util.NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    return os.path.isfile(path) and os.path.getsize(path) > 2000


def capture(url, out_dir):
    """-> [(label, png_path)] for desktop and phone. Empty list when no Chrome/Edge is installed."""
    exe = _browser()
    if not exe:
        return []
    os.makedirs(out_dir, exist_ok=True)
    jobs, res = [("desktop", 1440, 2300), ("mobile", 520, 2000)], {}
    wrapper = os.path.join(out_dir, "_mobile_frame.html")
    with open(wrapper, "w", encoding="utf-8") as fh:                              # headless Chrome cannot make a window narrower than ~500px, so a phone
        fh.write(f'<!doctype html><body style="margin:0;background:#20242f;display:flex;justify-content:center">'          # view is rendered in a real 390px iframe: its media
                 f'<iframe src="{url}" style="width:390px;height:2000px;border:0;background:#fff"></iframe></body>')          # queries then see a true phone width

    def go(label, w, h):
        p = os.path.join(out_dir, f"{label}.png")
        if os.path.exists(p):
            os.remove(p)
        target = url if label == "desktop" else "file:///" + wrapper.replace("\\", "/")
        if _shoot(exe, target, p, w, h):
            res[label] = p
    ths = [threading.Thread(target=go, args=j, daemon=True) for j in jobs]
    [t.start() for t in ths]
    [t.join(90) for t in ths]
    return [(l, res[l]) for l, _, _ in jobs if l in res]


def is_blank(png_path):
    """Deterministic: a screenshot that is (almost) one flat colour means the page did not render."""
    try:
        from PIL import Image
        im = Image.open(png_path).convert("L").resize((64, 64))
        px = list(im.getdata())
        mean = sum(px) / len(px)
        return (sum((p - mean) ** 2 for p in px) / len(px)) < 4.0
    except Exception:
        return False


def _jpeg(png_path, max_w=1100):
    try:
        from PIL import Image
        im = Image.open(png_path).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return None


REVIEW_SYSTEM = (
    "You are a strict senior product designer reviewing a SCREENSHOT of a website that was just generated for a request. Judge ONLY what is visible. "
    "Expected: a coherent, premium, production-quality design appropriate to the brand/business (the exact visual language - glass, editorial, minimal, bold, elegant, "
    "or something else - is the site's own choice, not a fixed look), clear hierarchy, readable contrast, consistent spacing, polished buttons, and a layout appropriate "
    "for the viewport ({view}). Report a defect as MAJOR only if it clearly hurts the result: blank/mostly empty page, "
    "text unreadable (low contrast), overlapping or clipped content, horizontal overflow / content cut off at the edge, broken or missing sections, unrelated content, "
    "or raw unstyled HTML. Taste/minor polish is MINOR. Reply ONLY JSON: {\"score\": 1-10, \"problems\": [{\"severity\": \"major|minor\", \"issue\": \"specific, actionable\"}]}."
)

CRITIQUE_SYSTEM = (
    "You are a skeptical creative director doing a FINAL pre-delivery critique of a website screenshot, before it is shown to the client. This is separate from a bug check - "
    "the page already works. You are judging whether it looks CUSTOM-DESIGNED for this specific business, or like a generic AI-generated template. Answer these exactly, honestly, harshly: "
    '{"looks_generic": true|false (would a designer immediately recognise this as a templated/AI output rather than something art-directed for this brand?), '
    '"brand_match": true|false (does the visual language - colour, type, mood - actually fit THIS business/audience, not just look nice in general?), '
    '"clear_next_action": true|false (is it obvious within 2 seconds what the visitor should do?), '
    '"strong_hierarchy": true|false (is the most important information unmistakably the most prominent?), '
    '"verdict": "one blunt sentence, the kind a creative director would actually say", '
    '"fixes": ["at most 3 SPECIFIC, actionable design changes - concrete enough for an engineer to implement, e.g. exact section/element and what to change - only if something is genuinely weak; empty array if it already looks custom and on-brand"]}'
)


def critique(host, state, shots, desc):
    """A separate, harsher pass from review(): not 'does it work' but 'does it look custom-designed for this brand,
    not a generic template'. Only ever asked for the DESKTOP shot (that is the one a client actually judges a design
    from) and only feeds concrete 'fixes' back into the build loop - a bare 'looks generic' with no fix is not
    actionable and is reported, never silently retried into more rounds."""
    shot = next((p for l, p in shots if l == "desktop"), None)
    if not shot or is_blank(shot):
        return [], "skipped: no usable desktop screenshot", "skipped"
    img = _jpeg(shot)
    if img is None:
        return [], "skipped: could not prepare the screenshot", "skipped"
    d = ask_json(host, state, "vision", CRITIQUE_SYSTEM, f"The website was requested as: {desc}", image=(img, "image/jpeg"), max_tokens=500)
    if not isinstance(d, dict) or "looks_generic" not in d:
        return [], "no vision model answered - self-critique NOT performed", "skipped"
    fixes = [str(f) for f in (d.get("fixes") or []) if str(f).strip()][:3]
    verdict = str(d.get("verdict") or "").strip()
    flags = [k for k in ("looks_generic",) if d.get(k)] + [k for k in ("brand_match", "clear_next_action", "strong_hierarchy") if d.get(k) is False]
    detail = (verdict or "reviewed") + (f" [{', '.join(flags)}]" if flags else "")
    return fixes, detail, ("issues" if fixes else "ok")


def review(host, state, shots, desc):
    """-> (problems_that_must_be_fixed, detail_text, status) with status in ok|issues|skipped."""
    found, notes, asked = [], [], 0
    out = {}

    def one(label, path):
        if is_blank(path):
            out[label] = ("blank", None)
            return
        img = _jpeg(path)
        if img is None:
            return
        d = ask_json(host, state, "vision", REVIEW_SYSTEM.replace("{view}", "desktop 1440px wide" if label == "desktop" else "phone 390px wide"),
                     f"The website was requested as: {desc}", image=(img, "image/jpeg"), max_tokens=500)
        out[label] = ("ok", d)
    ths = [threading.Thread(target=one, args=s, daemon=True) for s in shots]
    [t.start() for t in ths]
    [t.join(120) for t in ths]
    for label, _ in shots:
        kind, d = out.get(label, ("none", None))
        if kind == "blank":
            found.append(f"the {label} page renders blank (one flat colour) - the page is not rendering at all")
            continue
        if not isinstance(d, dict) or ("score" not in d and "problems" not in d):
            continue                                                       # no vision model answered for this view
        asked += 1
        try:
            score = int(d.get("score", 10))
        except (TypeError, ValueError):
            score = 10
        majors = [p.get("issue", "") for p in (d.get("problems") or []) if isinstance(p, dict) and str(p.get("severity", "")).lower() == "major" and p.get("issue")]
        notes.append(f"{label}: score {score}/10")
        if majors and score <= 7:
            found += [f"[{label}] {m}" for m in majors[:3]]
    if found:
        return found, "; ".join(notes), "issues"
    if not asked:
        return [], "no vision model answered - visual quality NOT verified", "skipped"
    return [], "; ".join(notes), "ok"
