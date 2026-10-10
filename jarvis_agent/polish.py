"""Deterministic clean-up of generated pages - things models get wrong again and again even when told not to, so they are
fixed in code after generation instead of burning AI fix rounds:
  - photos: picsum placeholders -> real, on-topic Pexels photos (needs PEXELS_API_KEY; without it nothing changes) + credit
  - icons: <use href="icons.svg#i-yoga"> for an icon that does not exist -> the closest one that does
  - stylesheets: one page loading its own extra stylesheet (so it looks like a different site) -> same sheets as the rest
  - brand: pages written in parallel inventing different business names -> the one name from the plan"""
import json
import os
import re
import threading
import urllib.parse
import urllib.request

from . import design, util

_PICSUM_RX = re.compile(r'(<img\b[^>]*?\bsrc=")(https://picsum\.photos/[^"]+)("[^>]*>)', re.I)
_ALT_RX = re.compile(r'\balt="([^"]*)"', re.I)
_ICON_RX = re.compile(r'(icons\.svg#i-)([a-z0-9_-]+)', re.I)
_LINK_RX = re.compile(r'[ \t]*<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>[ \t]*\n?', re.I)
_BRAND_RX = re.compile(r'class="brand"[^>]*>\s*([^<]{2,60}?)\s*<', re.I)
_STOP = {"build", "make", "create", "a", "an", "the", "me", "my", "for", "professional", "premium", "modern", "website", "site", "web",
         "page", "pages", "please", "of", "with", "and", "new", "best", "beautiful", "stunning", "simple", "full", "complete", "responsive"}
_CACHE, _CLOCK = {}, threading.Lock()
CREDIT = '<p class="photo-credit muted" style="font-size:.8rem;margin-top:.5rem">Photos from <a href="https://www.pexels.com" rel="noopener">Pexels</a></p>'


def pexels_key():
    k = os.environ.get("PEXELS_API_KEY", "").strip()
    if k:
        return k
    try:                                                                        # setx writes the user environment; a process started before that does not see it yet
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return str(winreg.QueryValueEx(h, "PEXELS_API_KEY")[0]).strip()
    except Exception:
        return ""


def topic_of(request):
    words = [w for w in re.findall(r"[a-zA-Z]+", str(request).lower()) if w not in _STOP]
    return " ".join(words[:3]) or "business"


def search(query, key, per_page=15):
    """-> [{"id", "url", "alt", "by"}] landscape photos, [] on any problem (network, quota, bad key)."""
    with _CLOCK:
        if query in _CACHE:
            return _CACHE[query]
    try:
        req = urllib.request.Request("https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"}),
                                     headers={"Authorization": key, "User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        out = [{"id": p["id"], "url": p["src"].get("large2x") or p["src"]["large"], "alt": p.get("alt") or "", "by": p.get("photographer") or ""}
               for p in data.get("photos") or [] if p.get("src")]
    except Exception:
        out = []
    with _CLOCK:
        _CACHE[query] = out
    return out


def _html_files(root):
    return [r for r in util.tree(root) if r.lower().endswith((".html", ".htm")) and not r.startswith(("_shots", "node_modules"))]


def _rw(root, rel, fn):
    path = os.path.join(root, rel)
    src = util.read_text(path, 10 ** 7)
    new = fn(src)
    if new != src:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return True
    return False


def fix_photos(root, request, key=None):
    """Every picsum placeholder becomes a real photo about THIS business (query = business topic + the img's own alt text),
    never the same photo twice across the site. Returns the pages changed."""
    key = key or pexels_key()
    if not key:
        return []
    topic, used, changed = topic_of(request), set(), []

    def pick(alt):
        words = [w for w in re.findall(r"[a-zA-Z]+", alt.lower()) if w not in _STOP and w not in topic.split()][:3]
        for q in ([f"{topic} {' '.join(words)}"] if words else []) + [topic]:
            for p in search(q.strip(), key):
                if p["id"] not in used:
                    used.add(p["id"])
                    return p
        return None

    def page(src):
        def swap(m):
            alt = _ALT_RX.search(m.group(0))
            p = pick(alt.group(1) if alt else "")
            return m.group(1) + p["url"] + m.group(3) if p else m.group(0)
        new = _PICSUM_RX.sub(swap, src)
        if new != src and "</footer>" in new and "photo-credit" not in new:  # Pexels asks for attribution
            new = new.replace("</footer>", CREDIT + "\n</footer>", 1)
        return new

    for rel in _html_files(root):
        if _rw(root, rel, page):
            changed.append(rel)
    if changed and not any("photo-credit" in util.read_text(os.path.join(root, r), 10 ** 7) for r in _html_files(root)):
        for rel in _html_files(root):                                           # Jinja sites: the footer lives in base.html, not in the page with the photo
            if _rw(root, rel, lambda s: s.replace("</footer>", CREDIT + "\n</footer>", 1) if "</footer>" in s else s):
                break
    return changed


def fix_icons(root):
    known = set(design.ICON_NAMES)

    def page(src):
        def swap(m):
            name = m.group(2).lower()
            if name in known:
                return m.group(0)
            return m.group(1) + (design.ICON_ALIASES.get(name) if design.ICON_ALIASES.get(name) in known else "star")
        return _ICON_RX.sub(swap, src)
    return [rel for rel in _html_files(root) if _rw(root, rel, page)]


def fix_stylesheets(root):
    """A stylesheet that only a minority of the full pages load is what made one page (contact) look like a different site."""
    pages = {}
    for rel in _html_files(root):
        src = util.read_text(os.path.join(root, rel), 10 ** 7)
        if re.search(r"<head\b", src, re.I):
            pages[rel] = [h for h in _LINK_RX.findall(src) if not h.startswith(("http:", "https:", "//"))]
    if len(pages) < 3:
        return []
    count = {}
    for hrefs in pages.values():
        for h in set(hrefs):
            count[h] = count.get(h, 0) + 1
    odd = {h for h, n in count.items() if n * 2 < len(pages) and not h.endswith(("design-system.css", "theme.css"))}
    if not odd:
        return []

    def page(src):
        return _LINK_RX.sub(lambda m: "" if m.group(1) in odd else m.group(0), src)
    return [rel for rel in pages if _rw(root, rel, page)]


def fix_brand(root, name):
    name = str(name or "").strip()
    if len(name) < 2:
        return []

    def page(src):
        m = _BRAND_RX.search(src)
        old = m.group(1).strip() if m else ""
        if not old or old == name or len(old) < 3:
            return src
        return re.sub(r"(?<![A-Za-z0-9])" + re.escape(old) + r"(?![A-Za-z0-9])", name, src)
    return [rel for rel in _html_files(root) if _rw(root, rel, page)]


_NAV_RX = re.compile(r'(<ul\b[^>]*class="[^"]*\bnav-links\b[^"]*"[^>]*>)(.*?)(</ul>)', re.I | re.S)
_HREF_RX = re.compile(r'href="([^"]*)"', re.I)


def _is_home(rel):
    return os.path.basename(rel).lower() in ("index.html", "index.htm", "home.html")


def fix_navbar(root):
    """Real bug: a multi-page site's homepage had its OWN navbar of '#about', '#blog' anchors (half of them pointing at sections that
    are not on that page) while every other page linked /classes, /contact... Give every page the site's real navbar."""
    navs = {}
    for rel in _html_files(root):
        m = _NAV_RX.search(util.read_text(os.path.join(root, rel), 10 ** 7))
        if m:
            navs[rel] = m
    real = [(rel, m) for rel, m in navs.items() if sum(1 for h in _HREF_RX.findall(m.group(2)) if h and not h.startswith("#")) >= 3]
    if not real:
        return []                                                               # a genuine one-page site: anchor navigation is correct there
    canon = max(real, key=lambda x: (os.path.basename(x[0]) == "base.html", len(_HREF_RX.findall(x[1].group(2)))))[1].group(2)

    def page(src):
        m = _NAV_RX.search(src)
        hrefs = _HREF_RX.findall(m.group(2)) if m else []
        if not m or not hrefs or sum(1 for h in hrefs if h.startswith("#")) * 2 < len(hrefs):
            return src
        return src[:m.start(2)] + canon + src[m.end(2):]
    return [rel for rel in navs if _rw(root, rel, page)]


_HERO_RX = re.compile(r'<section\b([^>]*\bclass="([^"]*\bhero\b[^"]*)"[^>]*)>(.*?)</section>', re.I | re.S)


def fix_home_hero(root, request, key=None):
    """Every reference site the owner showed opens with a big real photo; the model kept writing a text-only homepage hero.
    If the homepage hero has no image, turn it into a full-bleed photo hero (CSS .hero-cover) with an on-topic Pexels photo."""
    key = key or pexels_key()
    if not key:
        return []
    for rel in _html_files(root):
        if not _is_home(rel):
            continue
        src = util.read_text(os.path.join(root, rel), 10 ** 7)
        m = _HERO_RX.search(src)
        if not m or "<img" in m.group(3).lower() or "hero-cover" in m.group(2):
            continue
        topic = topic_of(request)
        photos = search(topic, key)
        if not photos:
            return []
        url = photos[0]["url"].split("?")[0] + "?auto=compress&cs=tinysrgb&w=1920"
        attrs = m.group(1).replace(f'class="{m.group(2)}"', f'class="{m.group(2)} hero-cover"', 1)
        attrs += f' style="--hero-img:url(\'{url}\')"'
        new = src[:m.start()] + f"<section{attrs}>" + m.group(3) + "</section>" + src[m.end():]
        if "photo-credit" not in new and "</footer>" in new:
            new = new.replace("</footer>", CREDIT + "\n</footer>", 1)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(new)
        return [rel]
    return []


def fix_hours(root, hours):
    """Plan says e.g. 'Mon-Fri 5am-10pm' but a page still claims 'Open 24/7' - a customer-facing contradiction."""
    hours = str(hours or "")
    if not hours or re.search(r"24\s*/\s*7|24\s*hours", hours, re.I):
        return []

    def page(src):
        src = re.sub(r"\bOpen\s+24\s*/\s*7\b", "Open 7 Days", src, flags=re.I)
        src = re.sub(r"\b24\s*/\s*7\s+Access\b", "7-Day Access", src, flags=re.I)
        return re.sub(r"\b24\s*/\s*7\b", "7 days a week", src)
    return [rel for rel in _html_files(root) if _rw(root, rel, page)]


def run_all(root, request, brand_name=None, hours=None):
    """-> short human summary of what was fixed ("" if nothing)."""
    done = []
    for label, fn in (("brand name", lambda: fix_brand(root, brand_name)), ("stylesheets", lambda: fix_stylesheets(root)),
                      ("navbar", lambda: fix_navbar(root)), ("opening hours", lambda: fix_hours(root, hours)),
                      ("icons", lambda: fix_icons(root)), ("photos", lambda: fix_photos(root, request)),
                      ("homepage photo hero", lambda: fix_home_hero(root, request))):
        try:
            ch = fn()
        except Exception:
            ch = []
        if ch:
            done.append(f"{label} in {len(ch)} page(s)")
    return ", ".join(done)
