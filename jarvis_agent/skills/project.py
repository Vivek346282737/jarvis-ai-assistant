"""Universal project builder: PLAN -> GENERATE -> RUN -> TEST -> OBSERVE -> DEBUG -> FIX -> RETEST -> VERIFY -> DELIVER.
The stack is chosen per request by the model from what this machine can actually run; nothing here knows about any
particular kind of business."""
import os
import re
import shutil
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from . import register
from .. import design, polish, runner, seo, util, webcheck
from ..models import ask_json

MAX_FIX_ROUNDS = 6                            # real bugs + visual-defect fixes + design-critique fixes all share this overall budget
MAX_VISUAL_FIXES = 2                          # crash-level visual defects (overflow, unreadable text, blank page) - separate budget from design polish below
MAX_CRITIQUE_FIXES = 2                        # self-critique design polish (generic look, weak copy, bad photo choice) - its own budget so it can never be starved by MAX_VISUAL_FIXES
MAX_FILES = 14
GEN_WORKERS = 6
OPEN_RESULT = True                            # open the finished site in the browser (tests switch this off)
DESIGN_STACKS = ("static", "flask", "fastapi", "node", "python", "other")
RUNNING = {}                                  # slug -> Server, so a rebuild stops the previous server of the same project

PLAN_SYSTEM = (
    "You are a senior software architect. Design the SMALLEST complete solution that genuinely satisfies the request, at real production quality. "
    "Choose the technology yourself from what is available on THIS machine (given below) - static HTML/CSS/JS, Flask, FastAPI, Node/Express, Vite/React, SQLite, etc. "
    "Prefer the simplest stack that works. Do NOT add login/registration/accounts, payments, admin panels, or a dashboard unless the request explicitly asks for them. Fewer, complete files beat many. "
    "REAL FUNCTIONALITY IS MANDATORY for any form/action that implies persistence or a business process - this is what separates a working site from a screenshot. "
    "If the business/request implies ANY of: a reservation/booking, a contact/enquiry form, joining/signing up, ordering/menu selection, a newsletter signup, a review/testimonial submission - "
    "then stack MUST be \"flask\": the form POSTs to a real Flask route, validates server-side, stores the submission in SQLite, and returns a real success/already-exists/error response that the page shows "
    "(no fake client-only \"Thanks!\" popup for anything meant to reach the business - data-success on <form> is ONLY for a form with nowhere real to go, e.g. a pure newsletter widget with no backend planned). "
    "A purely informational/marketing page with NO form and NO such action stays \"static\". "
    "When flask is used for a form: include a minimal admin-visible list route (e.g. GET /admin/bookings) that requires a simple shared-secret query param (?key=...), documented in env_needed as ADMIN_KEY - "
    "this is how the business owner actually sees what came in; never build a fake and never skip building it. "
    "For any project with a UI, a design system (design-system.css, theme.css, app.js, icons.svg) is ADDED AUTOMATICALLY - never list those files; pages just use it, "
    "themed by your \"theme\" choice below. Sections are CONTENT-AWARE - pick the ones this exact business/product actually needs (a restaurant is not a SaaS is not a portfolio), "
    "not a fixed template. DEFAULT TO SEPARATE PAGES (index.html, about.html, services.html or menu.html, contact.html, etc., each linked from the same navbar) for any real "
    "business website - that is what a $200+ professional site actually looks like, not everything crammed onto one scrolling page. Use ONE page with anchor sections only for "
    "a small landing/campaign/portfolio page, or when the request is explicitly for a single page. When in doubt, prefer multiple pages. "
    "EVERY page, including a multi-page site's index.html, MUST be a full, rich page on its own - a real homepage always has 5-8 sections "
    "(hero, a value/features/stats section, social proof, a preview of what the other pages cover, a call-to-action, etc.), never just a hero and a footer. "
    "\"sections\" is MANDATORY and non-trivial for every HTML file, index.html included - list every section id that page will actually contain; a homepage with only "
    "[\"hero\"] in \"sections\" is an incomplete plan and will be rejected. "
    + design.PLANNER_GUIDE + " "
    "Reply ONLY JSON: {\"name\": \"short-slug\", \"stack\": \"static|flask|fastapi|node|vite|python-cli|other\", \"summary\": \"...\", "
    "\"theme\": {\"accent\": \"#RRGGBB\", \"accent2\": \"#RRGGBB\", \"mode\": \"dark|light\", \"style\": \"glass|editorial|minimal|bold|elegant|auto\", \"headingFont\": \"serif|sans\", \"radius\": \"sharp|soft|pill\", \"density\": \"airy|compact\"}, "
    "\"brand\": {\"name\": \"ONE business name used on EVERY page\", \"tagline\": \"..\", \"phone\": \"..\", \"email\": \"..\", \"address\": \"..\", \"hours\": \"opening hours - any timetable/schedule MUST fit inside these\"} "
    "(for any business/brand site: pages are written in parallel, so this is the ONLY place these facts are decided), "
    "\"ui\": true|false, \"contract\":\"the SHARED interface every file must follow: routes+methods, JSON shapes, DOM ids/classes, function/table/column names, file links\", \"files\": [{\"path\": \"relative/path\", \"purpose\": \"what it contains\", "
    "\"sections\": [\"section-id\", ...] (ONLY for an HTML page: every <section id=\\\"..\\\"> it must contain - a one-page site listing 8 sections in \\\"purpose\\\" MUST actually deliver all 8, this is how that gets verified)}], "
    "\"install\": [[\"pip\",\"install\",\"-r\",\"requirements.txt\"]] or [], "
    "\"run\": [\"python\",\"app.py\"] or null (a long-running server MUST listen on 127.0.0.1 and the port from env PORT; you may use the literal {PORT} in args), "
    "\"test\": [\"python\",\"-m\",\"pytest\",\"-q\"] or null (a real command that exits 0 when things work), "
    "\"smoke\": [{\"method\":\"GET\",\"path\":\"/\",\"expect\":200,\"contains\":\"text that must appear\"}], "
    "\"env_needed\": [\"NAMES of external credentials the app needs, if any\"]}. "
    f"At most {MAX_FILES} files. Allowed programs in commands: python, pip, node, npm, npx, uvicorn, flask, pytest. No shell operators."
)

FILE_SYSTEM = (
    "You write ONE file of a project. Reply ONLY JSON: {\"content\": \"<the COMPLETE file>\"} - never a snippet, never placeholders like TODO. "
    "Production quality: clean architecture, meaningful names, input validation, proper error handling, security-conscious defaults (no hard-coded secrets: read "
    "credentials from environment variables and document them in .env.example), config via environment. For any UI: responsive (real @media breakpoints), "
    "semantic HTML, accessible (labels, alt, focus states, contrast), good typography and consistent spacing, loading/empty/error states, working navigation, "
    "real client-side form validation, basic SEO (title, meta description). NEVER reference an external font URL. "
    "A map, if any, uses ONLY the keyless embed <iframe src=\"https://maps.google.com/maps?q=URL-ENCODED+ADDRESS&output=embed\" loading=\"lazy\"></iframe> - never a Maps API key, "
    "and a Jinja template may only use variables its route actually passes (never env/config/os lookups in a template - that crashes the page). Photography IS allowed (see the design guide's Image "
    "section) via picsum.photos, but ONLY using the exact markup pattern given there (it degrades safely if the image fails) - never a raw <img src=\"https://...\"> "
    "with no fallback, and never place text directly over a photo without the pattern's own gradient overlay. Servers must bind 127.0.0.1 and read the port from env PORT. Files must be consistent with each other (same routes, ids, names). "
    "A Flask API route that a form's data-api posts to MUST: read JSON via request.get_json(silent=True) or {}, validate required fields server-side (never trust the client), "
    "on success INSERT into SQLite and return jsonify({\"message\": \"a specific real confirmation, e.g. include what was booked/ordered\"}), 201, "
    "on validation failure return jsonify({\"message\": \"specific reason\"}), 400, and NEVER return a 200 for bad input. "
    "The paired GET /admin/... route MUST read ADMIN_KEY = os.environ.get(\"ADMIN_KEY\") once, return 401 immediately if ADMIN_KEY is falsy (never allow open access just because it was not configured) OR "
    "if request.args.get(\"key\") != ADMIN_KEY, and only otherwise render the stored rows as a simple table using the SAME design system as the rest of the site."
)

FIX_SYSTEM = (
    "You are debugging a project that failed a real run/test. You get the REAL error output and the current files. Find the ROOT CAUSE and fix only what is needed. "
    "Reply ONLY JSON: {\"files\": [{\"path\": \"...\", \"content\": \"<COMPLETE corrected file>\"}], \"install\": null or [[...]], \"run\": null or [...], \"test\": null or [...]}. "
    "Include only files that must change. Do not rewrite unrelated files. A file you return REPLACES the old one completely: keep every existing "
    "route, function and the server start line; never write placeholders like '# other routes here'. Never return a file marked TRUNCATED or not shown."
)


def _module_available(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _env_facts():
    def has(x):
        return "yes" if shutil.which(x) else "no"
    mods = []
    for m in ("flask", "fastapi", "uvicorn", "pytest"):
        try:
            mod = __import__(m)
            ver = getattr(mod, "__version__", None)
            mods.append(f"{m}=={ver}" if ver else m)
        except ImportError:
            pass
    return (f"OS=Windows; python={sys.version.split()[0]} (installed modules: {', '.join(mods) or 'none of flask/fastapi/uvicorn/pytest'}; "
            f"pip can install any exact version listed above already present, or a newer one); node={has('node')}; npm={has('npm')}. "
            "IMPORTANT: if \"pytest\" is not already listed as installed above and you plan test=[...pytest...], you MUST also add pytest to \"install\" "
            "(e.g. [\"pip\",\"install\",\"pytest\"]) - otherwise omit \"test\" (null) rather than reference a tool that will not exist. "
            "Flask (if installed above) is a MODERN version: before_first_request/before_app_first_request do NOT exist - use an in-module flag "
            "or app.app_context() startup code instead, never those decorators.")


def _project_base(host):
    try:
        return host["_project_base_dir"]()
    except Exception:
        d = os.path.join(os.path.expanduser("~"), "JARVIS_Projects")
        os.makedirs(d, exist_ok=True)
        return d


def _new_dir(base, name):
    s = util.slug(name)
    path, n = os.path.join(base, s), 2
    while os.path.exists(path):
        path = os.path.join(base, f"{s}-{n}")
        n += 1
    os.makedirs(path)
    return path


def _summary_of(files_on_disk, root, budget=5000):
    out, used = [], 0
    for rel in files_on_disk:
        t = util.read_text(os.path.join(root, rel), 700)
        used += len(t)
        if used > budget:
            break
        out.append(f"--- {rel} ---\n{t}")
    return "\n".join(out)


def _files_for_fix(root, problems_text, budget=15000, per_file=12000):          # ~4K tokens: 22K chars got HTTP 413 "Payload Too Large" from Groq
    """Real bug: every file used to be cut at 4,000 chars with no marker. The fixer saw half of app.py, returned it as the
    'COMPLETE corrected file' and silently deleted the other routes and app.run() - the site then 'passed' with half its pages gone.
    Now: server/entry code and files named in the error come first and are shown whole; anything cut is clearly marked."""
    rels = [r for r in util.tree(root) if not r.endswith((".log", ".png", ".jpg", ".ico", ".db", ".lock", ".svg")) and not r.startswith(("_jarvis", "_shots"))
            and not r.endswith(("design-system.css", "app.js"))]
    mentioned = [r for r in rels if os.path.basename(r) in problems_text]
    entry = [r for r in rels if r.endswith(".py") and r not in mentioned]
    ordered = mentioned + entry + [r for r in rels if r not in mentioned and r not in entry]
    out, used = [], 0
    for r in ordered:
        full = util.read_text(os.path.join(root, r), 10 ** 6)
        t = full[:per_file]
        if len(full) > len(t):
            t += f"\n...[TRUNCATED: {len(full) - len(t)} more characters NOT shown - do NOT return this file, you cannot see all of it]"
        if used + len(t) > budget and out:
            out.append(f"--- {r} --- (not shown: over budget - do NOT return this file)")
            continue
        used += len(t)
        out.append(f"--- {r} ---\n{t}")
    return "\n".join(out)


_NAV_LINKS_RX = re.compile(r'class="[^"]*\bnav-links\b[^"]*"[^>]*>(.*?)</ul>', re.I | re.S)
_ANY_ROUTE_RX = re.compile(r"@\w+\.(?:route|get|post|put|delete|patch)\(")


def _drops_routes(root, files):
    """-> paths whose proposed new content has FEWER routes than the file on disk (a fix must never delete pages)."""
    bad = []
    for f in files or []:
        p = str(f.get("path") or "")
        full = os.path.join(root, p)
        if p.endswith(".py") and os.path.isfile(full) and isinstance(f.get("content"), str):
            if len(_ANY_ROUTE_RX.findall(f["content"])) < len(_ANY_ROUTE_RX.findall(util.read_text(full, 10 ** 6))):
                bad.append(p)
    return bad


def _uses_design(plan):
    return bool(plan.get("ui", True)) and str(plan.get("stack") or "static").lower() in DESIGN_STACKS


def _asset_dir(plan):
    return "assets" if str(plan.get("stack") or "static").lower() == "static" else "static/assets"


_FLASK_APP_RX = re.compile(r"^(\w+)\s*=\s*Flask\(", re.M)
_FIRST_REQ_RX = re.compile(r"^(\s*)@(\w+)\.before(?:_app)?_first_request\s*$", re.M)
_ONCE_HELPER = '''def _jarvis_run_once(owner):                       # added by JARVIS: Flask 2.3+ removed before_first_request
    def deco(fn):
        done = []
        hook = getattr(owner, "before_app_request", None) or owner.before_request
        @hook
        def _once():
            if not done:
                done.append(True)
                fn()
        return fn
    return deco


'''


def _patch_known_breakage(root):
    """Models keep writing @app.before_first_request (removed in Flask 2.3) even when told not to, and the app then dies at
    import. That is a known, mechanical breakage - fix it in code, deterministically, instead of burning an AI fix round."""
    fixed = []
    for rel in util.tree(root):
        if not rel.endswith(".py") or rel.startswith(("venv", ".venv")):
            continue
        path = os.path.join(root, rel)
        src = util.read_text(path, 10 ** 6)
        m_app = _FLASK_APP_RX.search(src)
        if m_app and "@" + m_app.group(1) + ".route" in src and ".run(" not in src and "__main__" not in src:
            # seen live: a fix round rewrote app.py without its `app.run(...)` - the process then exits 0 with an EMPTY log
            # and 5 more fix rounds were burnt guessing "the app did not start listening"
            src = src.rstrip() + ('\n\n\nif __name__ == "__main__":                       # added by JARVIS: the server start line was missing\n'
                                  f'    {m_app.group(1)}.run(host="127.0.0.1", port=int(__import__("os").environ.get("PORT", 5000)))\n')
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            fixed.append(rel)
        if not _FIRST_REQ_RX.search(src):
            continue
        if "def _jarvis_run_once(" not in src:                              # define the helper just before the first top-level statement that uses it
            lines = src.split("\n")
            i = src[:_FIRST_REQ_RX.search(src).start()].count("\n")
            while i > 0 and (lines[i][:1] in (" ", "\t", "") or lines[i - 1].lstrip().startswith("@")):
                i -= 1                                                          # climb out of an indented block / a decorator stack
            src = "\n".join(lines[:i]) + ("\n" if i else "") + _ONCE_HELPER + "\n".join(lines[i:])
        src = _FIRST_REQ_RX.sub(lambda m: f"{m.group(1)}@_jarvis_run_once({m.group(2)})", src)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        fixed.append(rel)
    return fixed


_ROUTE_RX = re.compile(r"@\w+\.(?:route|get)\(\s*['\"](/[^'\"<]*)['\"]([^)]*)\)")


def _with_every_page(root, checks):
    """The plan's smoke list usually only has "/" - a real build shipped 'verified' with /contact crashing (500) because no check
    ever opened it. Add a GET for every page the app actually serves: each parameter-free Flask/FastAPI GET route, or each
    .html file of a static site. Admin routes are expected to refuse without a key, so they only must not crash."""
    checks = list(checks or [{"path": "/", "expect": 200}])
    have = {str(c.get("path") or "/") for c in checks if str(c.get("method") or "GET").upper() == "GET"}
    paths = []
    for rel in util.tree(root):
        if rel.endswith(".py") and not rel.startswith(("venv", ".venv", "tests")):
            for path, rest in _ROUTE_RX.findall(util.read_text(os.path.join(root, rel), 10 ** 6)):
                if "methods" not in rest or "GET" in rest:
                    paths.append(path)
    if not paths:
        paths = ["/" + r for r in util.tree(root) if r.lower().endswith(".html") and "/" not in r and r.lower() != "index.html"]
    for rel in util.tree(root):                                                 # every page the NAVBAR promises must exist too - seen live: routes
        if rel.lower().endswith(".html"):                                       # deleted from app.py, nav links 404, yet the build "passed"
            m = _NAV_LINKS_RX.search(util.read_text(os.path.join(root, rel), 10 ** 6))
            if m:
                paths += [h.split("#")[0].split("?")[0] for h in re.findall(r'href="(/[^"#?]*)', m.group(1)) if "{{" not in h]
    for p in dict.fromkeys(paths):
        if p not in have and not p.startswith(("/static", "/api")):
            checks.append({"path": p, "expect": "no-crash" if "admin" in p.lower() else 200})
    return checks


@register("website", "app", "fullstack", "api")
def run(ctx):
    host, st, say, spec = ctx.host, ctx.state, ctx.say, ctx.spec
    desc = st["request"]
    reqs = "\n".join("- " + r for r in st["requirements"])
    st.phase("plan")
    plan = st["plan"]
    if not plan:
        st.op("planning the architecture")
        plan = ask_json(host, st, "plan", PLAN_SYSTEM + "\nMachine: " + _env_facts(),
                        f"Request: {desc}\nExtracted requirements:\n{reqs or '- (none beyond the request)'}\nKind: {st['kind']}", max_tokens=3000)
        if not plan or not plan.get("files"):
            st.error("no plan could be produced by any model")
            say("I could not get any AI model to plan that right now, Boss. The task is saved, say 'resume the last task' to try again.")
            return False
        plan["files"] = [f for f in plan["files"] if isinstance(f, dict) and util.safe_rel(f.get("path"))][:MAX_FILES]
        st["plan"], st["stack"] = plan, plan.get("stack")
        st.done("plan")
    base = _project_base(host)
    root = st["project_path"] = st["project_path"] or _new_dir(base, plan.get("name") or spec.get("title") or desc)
    slug = os.path.basename(root)
    say(f"Plan ready: {str(plan.get('summary') or plan.get('stack'))[:160]}. Writing {len(plan['files'])} files, Boss.")

    use_design = _uses_design(plan)
    guide = ""
    if use_design:                                                              # premium glass UI kit: written by code, identical quality every time
        adir = _asset_dir(plan)
        plan["files"] = [f for f in plan["files"] if not f["path"].startswith(adir + "/")]          # the kit's own files are never regenerated by a model
        wrote, style_key, mode, accent, accent2 = design.install(root, adir, plan.get("name") or desc, plan.get("theme"))
        st["files_changed"] += [w for w in wrote if w not in st["files_changed"]]
        guide = "\n" + design.guide(("" if adir == "assets" else "/") + adir + "/", style_key, mode, seo.SEO_GUIDE)
        seo_wrote = seo.install(root, plan.get("name") or desc, accent, accent2, [f["path"] for f in plan["files"] if f["path"].endswith(".html")])
        st["files_changed"] += [w for w in seo_wrote if w not in st["files_changed"]]
        st.done(f"design system installed (style: {style_key}/{mode})")

    st.phase("generate")
    existing = [x["path"] for x in plan["files"] if os.path.isfile(os.path.join(root, x["path"]))]          # resumed task: keep what was already written
    todo = [x for x in plan["files"] if x["path"] not in existing]
    contract = str(plan.get("contract") or "(none)")
    file_list = ", ".join(x["path"] + " (" + str(x.get("purpose", ""))[:60] + ")" for x in plan["files"])
    already = _summary_of(existing, root)
    brand = plan.get("brand") if isinstance(plan.get("brand"), dict) else {}
    brand_facts = ("BRAND FACTS - use EXACTLY these on this page, never invent a different business name, phone, email, address or hours; "
                   "every schedule/timetable must fall inside the hours, and use the SAME navbar links and footer as every other page:\n"
                   + "\n".join(f"{k}: {v}" for k, v in brand.items() if isinstance(v, str) and v.strip())) if brand else ""

    def _gen(f, extra=""):
        prompt = "\n".join([f"Request: {desc}", f"Requirements:\n{reqs}", f"Architecture: {plan.get('summary')} (stack {plan.get('stack')})",
                            f"All files: {file_list}",
                            f"SHARED CONTRACT - every file MUST follow it exactly (other files are being written at the same time):\n{contract}",
                            brand_facts,
                            f"Already written:\n{already}", f"NOW WRITE: {f['path']} - {f.get('purpose', '')}" + extra])
        return f, ask_json(host, st, "code", FILE_SYSTEM + guide, prompt, max_tokens=8000)

    def _is_homepage(path):
        p = path.lower().replace("templates/", "").replace("static/", "")
        return p in ("index.html", "index.htm", "home.html")

    def _missing_sections(f, content):
        """A one-page site that promised N sections in the plan but only delivered a few (e.g. hero + footer) is the
        exact 'severely incomplete' failure real testing found - catch it deterministically right here, not only if
        the (subjective, sampled) visual review happens to notice a mostly-empty page. A homepage that only ever
        promised 1-2 sections in the plan is ALSO incomplete (thin plan, not just thin delivery) - a real homepage
        is never just hero+footer, so force it to earn at least a few real sections regardless of what the plan asked for."""
        want = [s for s in (f.get("sections") or []) if isinstance(s, str)]
        if not f["path"].lower().endswith((".html", ".htm")):
            return []
        # header/nav/footer are <header>/<nav>/<footer> tags (or live in a Jinja base layout), never "<section id=footer>" - counting them
        # flagged EVERY page of a real build as incomplete and re-wrote 7 pages at once, which then ran the Groq keys into their limit
        want = [s for s in want if s.lower() not in ("footer", "header", "nav", "navbar", "site-footer", "site-header", "top", "main")]
        by_id = [s for s in want if f'id="{s}"' not in content and f"id='{s}'" not in content]
        if by_id and (len(by_id) * 2 >= len(want) or content.lower().count("<section") < 3):
            return by_id                                                        # genuinely thin page (the real 'hero + footer only' failure) -> retry
        if by_id:
            return []                                                           # 1-2 of 8 sections named differently / merged: a full page, not worth a rewrite
        if _is_homepage(f["path"]) and len(want) <= 1:
            return ["(homepage needs at least 4-5 real content sections beyond hero - the plan under-specified this page)"]
        return []

    st.op(f"writing {len(todo)} files in parallel")
    with ThreadPoolExecutor(max_workers=GEN_WORKERS) as ex:                     # all files at once instead of one after another: minutes -> seconds
        results = list(ex.map(_gen, todo))
    failed = []
    retry_jobs = []
    for f, d in results:
        rel = f["path"]
        if not d or not isinstance(d.get("content"), str):
            if rel.startswith(("tests/", "test_")) or rel.lower().endswith(".md"):
                st.error(f"skipped optional file {rel} (no model could write it)")          # a missing test/doc must not sink a working build; it is reported, not hidden
                plan["files"] = [x for x in plan["files"] if x["path"] != rel]
                if rel.startswith(("tests/", "test_")):
                    plan["test"] = None
            else:
                failed.append(rel)
            continue
        missing = _missing_sections(f, d["content"])
        if missing:
            retry_jobs.append((f, missing))
            continue
        util.write_files(root, [{"path": rel, "content": d["content"]}])
        st["files_changed"].append(rel)
    if retry_jobs:                                                              # one immediate, targeted retry per incomplete page - cheaper and more
        st.op(f"completing {len(retry_jobs)} page(s) that were missing sections")          # reliable than waiting for the visual-review loop to notice
        def _redo(job):
            f, missing = job
            if _is_homepage(f["path"]) and missing and missing[0].startswith("("):
                extra = ("\n\nYOUR PREVIOUS ATTEMPT was too thin for a real homepage (hero + footer only is not acceptable). "
                          "Write the FULL page again with at least 4-5 real content sections beyond the hero: for example a value/features "
                          "section, a stats or social-proof section, a preview of what other pages cover, and a closing call-to-action - "
                          "content that genuinely fits this exact business, using the design system's components (cards, grid, trust-strip, etc).")
            else:
                extra = f"\n\nYOUR PREVIOUS ATTEMPT was missing these required section ids: {missing}. Write the FULL page again with EVERY section from \"sections\" actually present."
            return _gen(f, extra)
        with ThreadPoolExecutor(max_workers=GEN_WORKERS) as ex:
            redone = list(ex.map(_redo, retry_jobs))
        for f, d in redone:
            rel = f["path"]
            if not d or not isinstance(d.get("content"), str):
                failed.append(rel)
                continue
            still_missing = _missing_sections(f, d["content"])
            if still_missing:
                st.error(f"{rel}: still missing sections {still_missing} after a retry - keeping it, flagged for the fix loop")
            util.write_files(root, [{"path": rel, "content": d["content"]}])
            st["files_changed"].append(rel)
    if failed:                                                                  # providers had a bad moment (rate limit / 503): one more pass, gentler, before giving up
        st.op(f"retrying {len(failed)} file(s) that no model answered for")
        by_path = {x["path"]: x for x in plan["files"]}
        time.sleep(3)
        with ThreadPoolExecutor(max_workers=2) as ex:
            again = list(ex.map(_gen, [by_path[r] for r in failed if r in by_path]))
        failed = []
        for f, d in again:
            if not d or not isinstance(d.get("content"), str):
                failed.append(f["path"])
                continue
            util.write_files(root, [{"path": f["path"], "content": d["content"]}])
            st["files_changed"].append(f["path"])
    st.save()
    if failed:
        st.error(f"could not generate {', '.join(failed)}")
        say(f"I could not generate {', '.join(failed)[:80]}, Boss. Everything else is saved - say 'resume the last task' to continue.")
        return False
    if plan.get("env_needed"):
        util.write_files(root, [{"path": ".env.example", "content": "\n".join(f"{n}=" for n in plan["env_needed"]) + "\n"}])
    st.done("files written")

    st.phase("run")
    old = RUNNING.pop(slug, None)
    if old:
        old.stop()
    static_index = os.path.isfile(os.path.join(root, "index.html")) and not plan.get("run")
    run_cmd = plan.get("run") or (runner.static_cmd() if static_index else None)
    server, url, problems = None, None, []
    visual_fixes, visual_left, visual_problem_img = 0, [], None
    critique_done, critique_fixes = [False], [0]  # lists so they can be mutated from inside the loop below without a nonlocal/global
    for attempt in range(MAX_FIX_ROUNDS + 1):
        visual_left = []
        st["retries"] = attempt
        patched = _patch_known_breakage(root)
        if patched:
            st.op("auto-fixed removed Flask APIs in " + ", ".join(patched))
        if use_design:
            polished = polish.run_all(root, desc, brand.get("name"), brand.get("hours"))
            if polished:
                st.op("auto-fixed " + polished)
        problems = []
        if server:
            server.stop()
            server = None
        st.op("installing dependencies")
        ok, err = runner.install_deps(root, plan.get("install"))
        if not ok:
            problems.append(err)
            st.test("install", False, err)
        else:
            st.op("running tests")
            t_cmd = plan.get("test")
            if t_cmd and isinstance(t_cmd, list) and "pytest" in t_cmd and not _module_available("pytest"):
                t_cmd = None                                                    # the model planned pytest but never listed it in "install" - do not waste
            if not t_cmd and not run_cmd:                                       # fix rounds chasing a missing test tool instead of real bugs; fall through to compileall
                t_cmd = ["python", "-m", "compileall", "-q", "."]
            ok, out = runner.run_tests(root, t_cmd)
            st.test("project tests" if t_cmd else "tests (none defined)", ok, out)
            if not ok:
                problems.append(f"the test/build command {t_cmd} failed:\n{out[-1200:]}")
            if run_cmd:
                st.op("starting the app")
                server = runner.Server(root, run_cmd)
                try:
                    url = server.start()
                except (ValueError, FileNotFoundError, OSError) as e:
                    problems.append(f"could not start the app: {e}")
                else:
                    if not server.wait_ready():
                        rc = server.proc.poll() if server.proc else None
                        tail = server.log_tail().strip()
                        if rc is not None and not tail:                         # an empty log gives the fixer nothing to go on - say what we DO know
                            tail = (f"the process exited with code {rc} and printed NOTHING - it never started a web server. "
                                    f"The entry file run by {run_cmd} must end with the server start, e.g. "
                                    "if __name__ == \"__main__\": app.run(host=\"127.0.0.1\", port=int(os.environ.get(\"PORT\", 5000)))")
                        problems.append("the app did not start listening:\n" + tail)
                        st.test("startup", False, server.log_tail(400))
                    else:
                        st.test("startup", True)
                        sm = runner.smoke(url, _with_every_page(root, plan.get("smoke")))
                        st.test("http smoke checks", not sm, "; ".join(sm))
                        problems += sm
                        tb = server.new_traceback()
                        if tb:
                            problems.append("the server logged an error while handling requests:\n" + tb)
                        if plan.get("ui", True) and not problems:
                            st.op("checking HTML quality")
                            lint = webcheck.lint(root)
                            st.test("HTML quality (SEO, a11y, links)", not lint, "; ".join(lint))
                            problems += lint
                            if static_index and not problems and host.get("_static_quality_problems"):
                                files = [{"path": r, "content": util.read_text(os.path.join(root, r), 10 ** 6)} for r in util.tree(root) if r.endswith((".html", ".css", ".js"))]
                                q = host["_static_quality_problems"](files)
                                st.test("static quality checks", not q, "; ".join(q))
                                problems += q
                            if not problems:
                                st.op("taking desktop + phone screenshots")
                                shots = webcheck.capture(url, os.path.join(root, "_shots"))
                                if not shots:
                                    st.test("visual review", True, "skipped: no Chrome/Edge found for headless screenshots")
                                else:
                                    vis, detail, status = webcheck.review(host, st, shots, desc)
                                    st.test(f"visual review ({status})", status != "issues", detail + (" | " + "; ".join(vis) if vis else ""))
                                    if vis and visual_fixes >= MAX_VISUAL_FIXES:
                                        visual_left = vis                              # polish budget spent: report it honestly instead of looping forever
                                    elif vis:
                                        visual_fixes += 1
                                        visual_problem_img = shots[0][1]
                                        problems += vis
                                    elif not critique_done[0]:                         # page genuinely works - NOW ask "does this look custom, or like a template?"
                                        st.op("self-critique: does this look custom-designed or generic?")
                                        cfixes, cdetail, cstatus = webcheck.critique(host, st, shots, desc)
                                        st.test(f"self-critique ({cstatus})", cstatus != "issues", cdetail)
                                        if cfixes and critique_fixes[0] < MAX_CRITIQUE_FIXES:
                                            critique_fixes[0] += 1                     # NOT marking critique_done: after the fix, one more pass re-verifies it actually improved
                                            visual_problem_img = shots[0][1]
                                            problems += [f"design critique: {f}" for f in cfixes]
                                        else:
                                            critique_done[0] = True                    # satisfied (or the polish budget is spent) - never ask again this build
                                            visual_left = visual_left + [f"design critique: {f}" for f in cfixes]
        if not problems:
            break
        st.error(problems[0])
        if attempt >= MAX_FIX_ROUNDS:
            break
        st.phase("run")
        say(f"I found a problem: {problems[0].splitlines()[0][:140]}. Fixing it, Boss.")
        st.op("debugging")
        img_for_fix = None
        if visual_problem_img and all(p.startswith("[") for p in problems):        # purely visual problem: let the model SEE the screenshot
            j = webcheck._jpeg(visual_problem_img)
            img_for_fix = (j, "image/jpeg") if j else None
        looks_visual = any(p.startswith(("[", "design critique:")) or "HTML" in p[:60] or ".html" in p[:80] for p in problems)
        fix = ask_json(host, st, "debug", FIX_SYSTEM + (guide if looks_visual else ""),          # a Python crash does not need the 2.7K-token design guide; leaving it out keeps the request small enough for Groq
                       f"Request: {desc}\nStack: {plan.get('stack')}\nREAL ERRORS:\n" + "\n\n".join(p[:1500] for p in problems[:3]) +
                       f"\n\nCurrent files:\n{_files_for_fix(root, ' '.join(problems))}", image=img_for_fix, max_tokens=8000)
        visual_problem_img = None
        if not fix or not fix.get("files"):
            st.error("the model returned no fix")
            break
        dropped = _drops_routes(root, fix["files"])
        if dropped:                                                             # a "fix" that deletes pages is worse than the bug - keep the old file
            st.error(f"rejected a fix that deleted routes from {', '.join(dropped)}")
            fix["files"] = [f for f in fix["files"] if f.get("path") not in dropped]
        written = util.write_files(root, fix["files"])
        st["files_changed"] += [w for w in written if w not in st["files_changed"]]
        for k in ("install", "run", "test"):
            if fix.get(k):
                plan[k] = fix[k]
        run_cmd = plan.get("run") or run_cmd
        st.save()

    st.phase("verify")
    st["verified"] = not problems
    # a design-critique-only leftover (server up, real bugs all fixed) is still a WORKING site - only a real crash/defect
    # should stop it from running and being shown; only critique/design-critique problems remaining counts as "running fine"
    running_ok = server is not None and all(p.startswith("design critique:") for p in problems)
    if server and (not problems or running_ok):
        RUNNING[slug] = server
    elif server:
        server.stop()
    st.phase("deliver")
    report = [f"# {spec.get('title') or desc}", "", f"Request: {desc}", f"Stack: {plan.get('stack')}", f"Folder: {root}", f"Verified: {'yes' if st['verified'] else 'NO'}",
              "", "## Tests performed"] + [f"- [{'x' if t['ok'] else ' '}] {t['name']} {t['detail'][:120]}" for t in st["tests"]]
    if plan.get("env_needed"):
        report += ["", "## Credentials needed (not provided - integrations NOT tested)"] + [f"- {n}" for n in plan["env_needed"]]
    if problems:
        report += ["", "## Remaining problems"] + [f"- {p[:300]}" for p in problems]
    if visual_left and not problems:
        report += ["", "## Visual concerns still flagged after the polish rounds (not blocking)"] + [f"- {p[:300]}" for p in visual_left]
    util.write_files(root, [{"path": "JARVIS_REPORT.md", "content": util.mask_secrets("\n".join(report))}])
    if st["verified"] or running_ok:
        extra = f" It needs these credentials that I do not have, so those parts are untested: {', '.join(plan['env_needed'])}." if plan.get("env_needed") else ""
        if visual_left:
            extra += f" One visual thing is still not perfect: {visual_left[0][:110]}."
        if running_ok and not st["verified"]:
            extra += f" One design point I could not fully resolve: {problems[0][:150]}"
            say(f"It runs and works, Boss, but I have an unresolved design note: {problems[0][:150]}. Saved in {root} and running at {url}.{extra if plan.get('env_needed') else ''}")
        else:
            say(f"Done, Boss. It is built, tested and verified{(' and running at ' + url) if url else ''}. Saved in {root}.{extra}")
        if url and use_design and OPEN_RESULT:
            try:
                webbrowser.open(url)
            except Exception as e:
                host["log_debug"]("jarvis_agent/open", e)
    else:
        say(f"I built it, Boss, but I could not fully fix everything: {problems[0].splitlines()[0][:160]}. Saved in {root}, details in JARVIS_REPORT.md.")
    st.phase("done")
    st["final"] = "verified" if st["verified"] else "partial"
    st.save()
    return st["verified"]
