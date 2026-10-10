"""Offline acceptance tests. The AI is scripted (so they are deterministic and need no keys) but EVERYTHING else is real:
real files, real processes, real HTTP servers, real test commands, real checkpoints. Run:  venv\\Scripts\\python.exe -m unittest jarvis_agent.tests.test_agent -v"""
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_agent import core, router, runner, util  # noqa: E402
from jarvis_agent.state import TaskState  # noqa: E402
from jarvis_agent.skills import coding, data, project  # noqa: E402

SERVER_OK = '''import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
ITEMS = []
class H(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/": self._send(200, b"<html><title>Items</title><body><h1>Item manager</h1><p>Manage your items here</p></body></html>", "text/html")
        elif self.path == "/items": self._send(200, ITEMS)
        else: self._send(404, {"error": "not found"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); item = json.loads(self.rfile.read(n) or b"{}")
        if not item.get("name"): return self._send(400, {"error": "name required"})
        ITEMS.append(item); self._send(201, item)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


class FakeHost(dict):
    """Scripted AI + recorded speech. `script(system, prompt, prefer_free)` returns the JSON the 'model' answers with."""

    def __init__(self, base, script):
        super().__init__()
        self.said, self.calls, self.script = [], [], script
        self["speak"] = lambda t: self.said.append(t)
        self["log_debug"] = lambda ctx, e: self.said.append(f"[LOG {ctx}] {e!r}")
        self["require_owner"] = lambda what: True
        self["_project_base_dir"] = lambda: base
        self["get_config"] = lambda k, d=None: d
        self["llm_json"] = self._llm

    def _llm(self, system, prompt, image=None, max_tokens=0, prefer_free=False, task="fast"):
        self.calls.append((system[:40], prefer_free))
        return self.script(system, prompt, prefer_free)


def make_ctx(host, request, kind, requirements=None):
    st = TaskState(request, kind)
    st["requirements"] = requirements or []
    return core.Ctx(host, st, {"kind": kind, "title": request})


class Base(unittest.TestCase):
    def setUp(self):
        project.OPEN_RESULT = False                                          # tests must never pop browser windows
        from jarvis_agent import models
        models.USE_GEMINI = True                                             # scripted llm_json stands in for every provider here
        models._GWIN.clear()
        from jarvis_agent import polish
        orig_key = polish.pexels_key
        polish.pexels_key = lambda: ""                                       # offline tests must never call the real Pexels API
        self.addCleanup(lambda: setattr(polish, "pexels_key", orig_key))
        self.tmp = tempfile.mkdtemp(prefix="jarvis_agent_test_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def tearDown(self):
        for s in list(project.RUNNING.values()):
            s.stop()
        project.RUNNING.clear()


class TestPlannedToolMissing(Base):
    def test_pytest_planned_but_not_installed_does_not_burn_the_fix_budget(self):
        """Real failure seen in testing: the model planned test=[...pytest...] without adding pytest to "install", and
        every one of the 4 fix rounds got spent re-running a command that can never succeed, instead of on real bugs."""
        orig = project._module_available
        project._module_available = lambda name: False if name == "pytest" else orig(name)
        self.addCleanup(lambda: setattr(project, "_module_available", orig))

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "nopytest", "stack": "python", "summary": "s", "ui": False, "run": ["python", "app.py"], "install": [],
                        "test": ["python", "-m", "pytest", "-q"], "files": [{"path": "app.py", "purpose": "server"}],
                        "smoke": [{"path": "/", "expect": 200, "contains": "Item manager"}]}
            return {"content": SERVER_OK}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a small api", "api")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(ctx.state["retries"], 0)                            # never even entered the fix loop over a missing test tool
        self.assertFalse(any("No module named pytest" in e for e in ctx.state["errors"]))


class TestStaticWebsite(Base):
    def test_static_site_built_and_verified(self):
        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "gym-site", "stack": "static", "summary": "one-page gym site", "ui": False, "run": None,
                        "files": [{"path": "index.html", "purpose": "page"}, {"path": "style.css", "purpose": "styles"}],
                        "smoke": [{"path": "/", "expect": 200, "contains": "Iron Gym"}]}
            if "NOW WRITE: index.html" in prompt:
                return {"content": "<!doctype html><html><head><title>Iron Gym</title><link rel=stylesheet href=style.css></head><body><form><input required></form><h1>Iron Gym - train hard</h1></body></html>"}
            return {"content": "body{font-family:sans-serif}@media(max-width:600px){body{font-size:14px}}"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a professional gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertTrue(ctx.state["verified"])
        self.assertTrue(os.path.isfile(os.path.join(ctx.state["project_path"], "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(ctx.state["project_path"], "JARVIS_REPORT.md")))
        self.assertTrue(any(t["name"] == "http smoke checks" and t["ok"] for t in ctx.state["tests"]))


RESERVE_APP = '''import os, sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = os.path.join(os.path.dirname(__file__), "app.db")

def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    return c

@app.route("/")
def home():
    return "<html><title>Aurum</title><body><h1>Aurum Table</h1></body></html>"

@app.route("/api/reserve", methods=["POST"])
def reserve():
    data = request.get_json(silent=True) or {}
    name, email = data.get("name", "").strip(), data.get("email", "").strip()
    if not name or not email:
        return jsonify({"message": "Name and email are required."}), 400
    c = db(); c.execute("INSERT INTO bookings (name, email) VALUES (?,?)", (name, email)); c.commit()
    return jsonify({"message": "Thanks " + name + "! Table booked."}), 201

@app.route("/admin/bookings")
def admin_bookings():
    admin_key = os.environ.get("ADMIN_KEY")
    if not admin_key or request.args.get("key") != admin_key:
        return jsonify({"message": "unauthorized"}), 401
    rows = db().execute("SELECT name, email FROM bookings").fetchall()
    return jsonify(rows)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
'''


class TestRealBackendForm(Base):
    def test_reservation_form_persists_validates_and_admin_route_is_locked_down(self):
        """The core '$200-website' gap: a form must actually save data server-side, reject bad input with a real 400,
        and an admin route must NEVER default-open just because ADMIN_KEY was left unconfigured."""
        from jarvis_agent import runner
        root = os.path.join(self.tmp, "reserve_backend")
        os.makedirs(root)
        open(os.path.join(root, "app.py"), "w", encoding="utf-8").write(RESERVE_APP)
        srv = runner.Server(root, ["python", "app.py"])
        self.addCleanup(srv.stop)
        url = srv.start()
        self.assertTrue(srv.wait_ready(), srv.log_tail())
        status, body = runner.http_check(url + "/api/reserve", method="POST", body={"name": "Vivek", "email": "v@x.com"})
        self.assertEqual(status, 201)
        self.assertIn("Vivek", body)
        status, body = runner.http_check(url + "/api/reserve", method="POST", body={"name": "", "email": ""})
        self.assertEqual(status, 400)                                        # bad input is REJECTED, not silently accepted
        status, body = runner.http_check(url + "/admin/bookings")            # no ADMIN_KEY configured at all
        self.assertEqual(status, 401)                                        # must stay locked, never default-open
        status, body = runner.http_check(url + "/admin/bookings?key=wrong")
        self.assertEqual(status, 401)


class TestFullstackAndRepair(Base):
    def _script(self, broken_first):
        state = {"fixed": False}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "item-api", "stack": "python", "summary": "items CRUD backend", "ui": False, "run": ["python", "app.py"], "install": [],
                        "files": [{"path": "app.py", "purpose": "server"}],
                        "smoke": [{"path": "/", "expect": 200, "contains": "Item manager"},
                                  {"method": "POST", "path": "/items", "json": {"name": "pen"}, "expect": 201},
                                  {"method": "POST", "path": "/items", "json": {}, "expect": 400},
                                  {"path": "/items", "expect": 200, "contains": "pen"}]}
            if "debugging a project" in system:
                state["fixed"] = True
                self.assertIn("SyntaxError", prompt)                       # the REAL error reached the model
                return {"files": [{"path": "app.py", "content": SERVER_OK}]}
            return {"content": SERVER_OK.replace("HTTPServer((", "HTTPServer(( ", 1).replace("class H(", "class H (") if not broken_first else "def broken(:\n    pass\n"}
        return script, state

    def test_crud_backend_runs_and_passes_real_http_tests(self):
        script, _ = self._script(broken_first=False)
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build an inventory management backend", "api")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(ctx.state["retries"], 0)

    def test_intentional_coding_error_is_repaired_autonomously(self):
        script, state = self._script(broken_first=True)
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build an inventory management backend", "api")
        self.assertTrue(project.run(ctx), host.said)
        self.assertTrue(state["fixed"])
        self.assertGreaterEqual(ctx.state["retries"], 1)
        self.assertTrue(any(not t["ok"] for t in ctx.state["tests"]))       # the failure was really observed first
        self.assertTrue(ctx.state["verified"])

    def test_unfixable_project_is_reported_honestly_not_faked(self):
        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "bad", "stack": "python", "summary": "x", "ui": False, "run": ["python", "app.py"], "files": [{"path": "app.py", "purpose": "s"}]}
            if "debugging a project" in system:
                return {"files": [{"path": "app.py", "content": "def broken(:\n"}]}
            return {"content": "def broken(:\n"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build something impossible app", "app")
        self.assertFalse(project.run(ctx))
        self.assertEqual(ctx.state["retries"], project.MAX_FIX_ROUNDS)      # finite retry limit, then stops
        self.assertFalse(ctx.state["verified"])
        self.assertTrue(any("could not fully fix" in s for s in host.said))


class TestExistingProject(Base):
    def _make_project(self):
        root = os.path.join(self.tmp, "myproj")
        os.makedirs(root)
        open(os.path.join(root, "calc.py"), "w").write("def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n")
        open(os.path.join(root, "test_calc.py"), "w").write("import unittest\nfrom calc import add, mul\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n    def test_mul(self): self.assertEqual(mul(2, 3), 6)\nif __name__ == '__main__': unittest.main()\n")
        return root

    def test_bug_fix_changes_only_relevant_file_and_keeps_regression_green(self):
        root = self._make_project()
        untouched = open(os.path.join(root, "test_calc.py")).read()

        def script(system, prompt, pf):
            if "existing project" in system and "pick the files" in system:
                return {"files": ["calc.py"], "analysis": "add subtracts instead of adding"}
            return {"files": [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n"}], "summary": "fix add"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, f'fix my project at "{root}"', "coding")
        self.assertTrue(coding.run(ctx), host.said)
        self.assertEqual(ctx.state["files_changed"], ["calc.py"])
        self.assertEqual(open(os.path.join(root, "test_calc.py")).read(), untouched)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "jarvis_checkpoints")))
        self.assertTrue(any(t["name"].startswith("baseline") and not t["ok"] for t in ctx.state["tests"]))   # it really saw the failure first

    def test_failed_fix_restores_original_files(self):
        root = self._make_project()
        original = open(os.path.join(root, "calc.py")).read()

        def script(system, prompt, pf):
            if "pick the files" in system:
                return {"files": ["calc.py"], "analysis": "?"}
            return {"files": [{"path": "calc.py", "content": "def add(a, b):\n    return 0\n"}], "summary": "wrong"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, f'fix my project at "{root}"', "coding")
        self.assertFalse(coding.run(ctx))
        self.assertEqual(open(os.path.join(root, "calc.py")).read(), original)   # never left worse than found


class TestData(Base):
    def test_csv_cleaned_into_new_file_and_reported(self):
        p = os.path.join(self.tmp, "sales.csv")
        open(p, "w").write("Name,Amount\nAnn,10\nAnn,10\nBob,20\n")
        host = FakeHost(self.tmp, lambda s, p_, pf: {"summary": "two customers", "findings": ["Bob spends most"]})
        ctx = make_ctx(host, f'clean and analyze the csv "{p}"', "data")
        self.assertTrue(data.run(ctx), host.said)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "sales_clean.csv")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "sales_report.md")))
        self.assertEqual(open(p).read().count("Ann"), 2)                     # original untouched
        self.assertEqual(open(os.path.join(self.tmp, "sales_clean.csv")).read().count("Ann"), 1)


class TestModelFallbackAndResume(Base):
    def test_fallback_continues_the_same_task(self):
        seen = []

        def script(system, prompt, pf):
            seen.append(pf)
            if not pf and "senior software architect" in system:
                return None                                                   # cloud side gives nothing (rate limit / outage)
            if "senior software architect" in system:
                return {"name": "p", "stack": "static", "summary": "s", "ui": False, "run": None, "files": [{"path": "index.html", "purpose": "x"}], "smoke": [{"path": "/", "contains": "hello"}]}
            return {"content": "<html><title>t</title><body>hello world, this page is fine</body></html>"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a small app", "app")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(seen[:2], [False, True])                            # cloud tried first, then the local side, same task
        self.assertTrue(any(not m["ok"] for m in ctx.state["models"]) and any(m["ok"] for m in ctx.state["models"]))

    def test_resume_skips_files_already_written(self):
        calls = {"files": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "r", "stack": "static", "summary": "s", "ui": False, "run": None, "files": [{"path": "index.html", "purpose": "x"}, {"path": "b.html", "purpose": "y"}],
                        "smoke": [{"path": "/", "contains": "page"}]}
            calls["files"] += 1
            if "NOW WRITE: b.html" in prompt and calls["files"] < 10:
                return None                                                  # second file fails on every provider AND on the automatic retry pass -> task stops, state saved
            return {"content": "<html><title>t</title><body>page content for the test</body></html>"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a two page site app", "app")
        self.assertFalse(project.run(ctx))
        first_dir = ctx.state["project_path"]
        self.assertEqual(ctx.state["files_changed"], ["index.html"])
        calls["files"] = 10                                                  # provider recovers
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(ctx.state["project_path"], first_dir)               # same project folder, not a fresh restart
        self.assertTrue(os.path.isfile(os.path.join(first_dir, "b.html")))


GOOD_PAGE = ('<!doctype html><html lang="en" class="no-js"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
             '<title>IronFit Gym</title><meta name="description" content="Strength and conditioning gym">'
             '<link rel="stylesheet" href="assets/design-system.css"><link rel="stylesheet" href="assets/theme.css"></head><body>'
             '<a class="skip-link" href="#main">Skip</a><header><nav class="navbar glass" aria-label="Main"><a class="brand" href="#top">IronFit</a>'
             '<button class="nav-toggle" aria-label="Menu" aria-expanded="false"><svg class="icon"><use href="assets/icons.svg#i-menu"></use></svg></button>'
             '<ul class="nav-links"><li><a href="#about">About</a></li><li><a href="#contact">Contact</a></li></ul></nav></header>'
             '<main id="main"><section class="hero"><div class="container"><div class="hero-inner"><span class="badge">Open 24/7</span>'
             '<h1>Train <span class="gradient-text">harder</span></h1><p class="lead">Coaching, classes and recovery under one roof.</p>'
             '<div class="hero-actions"><a class="btn btn-primary btn-lg" href="#contact">Join now</a></div></div></div></section>'
             '<section class="section" id="about"><div class="container"><div class="grid grid-3"><article class="glass card reveal"><h3>Coaching</h3><p>Certified trainers.</p></article>'
             '<article class="glass card reveal"><h3>Classes</h3><p>40 weekly sessions.</p></article><article class="glass card reveal"><h3>Recovery</h3><p>Sauna and mobility.</p></article></div></div></section>'
             '<section class="section" id="contact"><div class="container"><form class="glass form" data-success="Thanks!"><div class="field"><label for="n">Name</label><input id="n" name="name" required></div>'
             '<button class="btn btn-primary" type="submit">Send</button></form></div></section></main>'
             '<footer class="footer glass"><p class="copyright">&copy; <span data-year></span> IronFit</p></footer><script src="assets/app.js" defer></script></body></html>')


class TestSEO(Base):
    def test_favicon_robots_sitemap_are_written_and_match_brand(self):
        from jarvis_agent import seo
        root = os.path.join(self.tmp, "seo")
        os.makedirs(root)
        wrote = seo.install(root, "Aurum Table", "#8a5a2b", "#c9a35a", ["index.html", "menu.html"], base_url="https://aurumtable.example")
        self.assertEqual(sorted(wrote), ["favicon.svg", "robots.txt", "sitemap.xml"])
        fav = open(os.path.join(root, "favicon.svg")).read()
        self.assertIn("#8a5a2b", fav)
        self.assertIn(">A<", fav)                                            # brand initial
        robots = open(os.path.join(root, "robots.txt")).read()
        self.assertIn("Sitemap: https://aurumtable.example/sitemap.xml", robots)
        sitemap = open(os.path.join(root, "sitemap.xml")).read()
        self.assertIn("<loc>https://aurumtable.example/</loc>", sitemap)     # index.html maps to the bare root URL
        self.assertIn("<loc>https://aurumtable.example/menu.html</loc>", sitemap)


class TestDesignAndVisual(Base):
    def test_design_kit_is_written_with_brand_colors_and_no_model(self):
        from jarvis_agent import design
        root = os.path.join(self.tmp, "site")
        wrote, style_key, mode, accent, accent2 = design.install(root, "assets", "Coffee Roasters", {"accent": "#ff5500", "accent2": "#00ccff", "mode": "dark", "style": "editorial"})
        self.assertEqual(sorted(os.path.basename(w) for w in wrote), ["app.js", "design-system.css", "icons.svg", "theme.css"])
        self.assertEqual(style_key, "editorial")
        self.assertIn("#ff5500", open(os.path.join(root, "assets", "theme.css")).read())
        css = open(os.path.join(root, "assets", "design-system.css")).read()
        self.assertIn("backdrop-filter", css)                                # glass is available as ONE of the visual languages
        self.assertIn("@media (max-width:900px)", css)                       # responsive
        # invalid model colours must never reach the CSS; a stable brand hue is derived instead
        css2 = design.theme_css("Iron Gym", {"accent": "red; } body{display:none", "accent2": "nope"})[0]
        self.assertNotIn("display:none", css2)
        self.assertEqual(design.theme_css("Iron Gym", None)[0], design.theme_css("Iron Gym", None)[0])
        self.assertNotEqual(design.theme_css("Iron Gym", None)[0], design.theme_css("Coffee Roasters", None)[0])
        # different explicit styles genuinely diverge (not just colour): glass uses blur, editorial/minimal do not
        glass_css = design.theme_css("X", {"style": "glass"})[0]
        flat_css = design.theme_css("X", {"style": "minimal"})[0]
        self.assertIn("--blur:18px", glass_css)
        self.assertIn("--blur:0px", flat_css)

    def test_lint_catches_real_html_problems(self):
        from jarvis_agent import webcheck
        root = os.path.join(self.tmp, "bad")
        os.makedirs(root)
        open(os.path.join(root, "index.html"), "w").write(
            '<html><head><title>x</title></head><body><h1>a</h1><h1>b</h1><img src="missing.png"><a href="nowhere.html">x</a>Lorem ipsum dolor'
            '<form><input name="n"></form></body></html>')
        p = " | ".join(webcheck.lint(root))
        for needle in ("lang", "viewport", "description", "2 <h1>", "alt", "nowhere.html", "missing.png", "lorem", "neither data-api"):
            self.assertIn(needle.lower(), p.lower(), needle)

    def test_lint_ignores_jinja_layouts_and_includes_but_still_checks_real_pages(self):
        """Real failure seen in testing: a Flask base.html (defines {% block %} slots) and partials/navbar.html
        (an {% include %}'d fragment) were judged as broken full pages and burned the whole fix budget."""
        from jarvis_agent import webcheck
        root = os.path.join(self.tmp, "jinja")
        os.makedirs(os.path.join(root, "partials"))
        open(os.path.join(root, "base.html"), "w").write(
            '<!doctype html><html lang="en"><head><title>{% block title %}Site{% endblock %}</title>'
            '<meta name="viewport" content="width=device-width"><meta name="description" content="x"></head>'
            '<body>{% block content %}{% endblock %}</body></html>')                 # a real base layout: never its own h1
        open(os.path.join(root, "partials", "navbar.html"), "w").write('<nav><a href="/">Home</a></nav>')           # an include fragment: no <html> at all
        open(os.path.join(root, "index.html"), "w").write(                            # a real standalone page: SHOULD still be checked
            '<html><head><title>x</title></head><body>no h1 here</body></html>')
        problems = " | ".join(webcheck.lint(root))
        self.assertNotIn("base.html", problems)
        self.assertNotIn("navbar.html", problems)
        self.assertIn("index.html", problems)                                # the real page's own problems are still caught

    def test_lint_accepts_a_real_backend_form_and_a_demo_form(self):
        from jarvis_agent import webcheck
        root = os.path.join(self.tmp, "ok_forms")
        os.makedirs(root)
        open(os.path.join(root, "index.html"), "w").write(GOOD_PAGE.replace('data-success="Thanks!"', 'data-api="/api/reserve"'))
        self.assertFalse([p for p in webcheck.lint(root) if "data-api" in p])
        open(os.path.join(root, "index.html"), "w").write(GOOD_PAGE)                # data-success form (demo, no backend) is also fine
        self.assertFalse([p for p in webcheck.lint(root) if "data-api" in p])

    def test_headless_screenshots_are_real_and_not_blank(self):
        from jarvis_agent import design, webcheck
        if not webcheck._browser():
            self.skipTest("no Chrome/Edge installed")
        root = os.path.join(self.tmp, "shot")
        design.install(root, "assets", "IronFit", {"style": "glass"})
        open(os.path.join(root, "index.html"), "w", encoding="utf-8").write(GOOD_PAGE)
        self.assertEqual(webcheck.lint(root), [])                            # the design-kit page passes every deterministic check
        from jarvis_agent import runner
        srv = runner.Server(root, runner.static_cmd())
        url = srv.start()
        try:
            self.assertTrue(srv.wait_ready())
            shots = webcheck.capture(url, os.path.join(root, "_shots"))
        finally:
            srv.stop()
        self.assertEqual([s[0] for s in shots], ["desktop", "mobile"])
        for _, path in shots:
            self.assertGreater(os.path.getsize(path), 5000)
            self.assertFalse(webcheck.is_blank(path))

    def test_visual_problem_is_seen_fixed_and_reverified(self):
        from jarvis_agent import webcheck
        if not webcheck._browser():
            self.skipTest("no Chrome/Edge installed")
        seen = {"vision": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "ironfit", "stack": "static", "summary": "gym site", "ui": True, "run": None, "theme": {"accent": "#ff4d4d", "accent2": "#ffb347", "mode": "dark"},
                        "files": [{"path": "index.html", "purpose": "one-page site"}], "smoke": [{"path": "/", "contains": "IronFit"}]}
            if "senior product designer" in system:
                seen["vision"] += 1
                return {"score": 4, "problems": [{"severity": "major", "issue": "hero text is unreadable"}]} if seen["vision"] <= 2 else {"score": 9, "problems": []}
            if "creative director" in system:
                return {"looks_generic": False, "brand_match": True, "clear_next_action": True, "strong_hierarchy": True, "verdict": "on-brand and clean", "fixes": []}
            if "debugging a project" in system:
                self.assertIn("unreadable", prompt)                          # the reviewer's finding reached the fixer
                return {"files": [{"path": "index.html", "content": GOOD_PAGE}]}
            return {"content": GOOD_PAGE.replace("Train", "Train ")}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a professional gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        names = [t["name"] for t in ctx.state["tests"]]
        self.assertIn("visual review (issues)", names)
        self.assertIn("visual review (ok)", names)
        self.assertIn("self-critique (ok)", names)
        self.assertGreaterEqual(ctx.state["retries"], 1)
        self.assertTrue(os.path.isdir(os.path.join(ctx.state["project_path"], "assets")))

    def test_self_critique_flags_generic_look_and_feeds_a_concrete_fix(self):
        from jarvis_agent import webcheck
        if not webcheck._browser():
            self.skipTest("no Chrome/Edge installed")
        seen = {"critique": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "genericco", "stack": "static", "summary": "s", "ui": True, "run": None,
                        "files": [{"path": "index.html", "purpose": "x"}], "smoke": [{"path": "/", "contains": "IronFit"}]}
            if "senior product designer" in system:
                return {"score": 9, "problems": []}                          # no crash-level defects - critique gets a turn
            if "creative director" in system:
                seen["critique"] += 1
                if seen["critique"] == 1:
                    return {"looks_generic": True, "brand_match": False, "clear_next_action": True, "strong_hierarchy": True,
                            "verdict": "reads as a stock AI template, not a real brand", "fixes": ["Replace the generic hero gradient with a brand-specific composition"]}
                return {"looks_generic": False, "brand_match": True, "clear_next_action": True, "strong_hierarchy": True, "verdict": "fixed", "fixes": []}
            if "debugging a project" in system:
                self.assertIn("design critique", prompt)                     # the critique's concrete fix reached the fixer, not just a vague complaint
                return {"files": [{"path": "index.html", "content": GOOD_PAGE}]}
            return {"content": GOOD_PAGE}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a professional gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(seen["critique"], 2)                                 # flagged once, then re-checked after the fix - not looped forever
        names = [t["name"] for t in ctx.state["tests"]]
        self.assertIn("self-critique (issues)", names)
        self.assertIn("self-critique (ok)", names)

    def test_critique_and_visual_defect_budgets_are_independent(self):
        """Real failure seen in testing: visual review spent the shared budget on a real defect, so the self-critique
        never got a genuine second attempt and the build was marked unverified/server killed over pure design polish."""
        from jarvis_agent import webcheck
        if not webcheck._browser():
            self.skipTest("no Chrome/Edge installed")
        calls = {"review": 0, "critique": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "budgetco", "stack": "static", "summary": "s", "ui": True, "run": None,
                        "files": [{"path": "index.html", "purpose": "x"}], "smoke": [{"path": "/", "contains": "IronFit"}]}
            if "senior product designer" in system:
                calls["review"] += 1
                # a real crash-level defect on the FIRST pass only (consumes the review budget), clean afterwards
                return {"score": 4, "problems": [{"severity": "major", "issue": "overlapping content"}]} if calls["review"] == 1 else {"score": 9, "problems": []}
            if "creative director" in system:
                calls["critique"] += 1
                return {"looks_generic": True, "brand_match": False, "clear_next_action": True, "strong_hierarchy": True,
                        "verdict": "generic", "fixes": ["Vary the hero composition"]} if calls["critique"] == 1 else {"looks_generic": False, "brand_match": True,
                        "clear_next_action": True, "strong_hierarchy": True, "verdict": "better", "fixes": []}
            return {"content": GOOD_PAGE, "files": [{"path": "index.html", "content": GOOD_PAGE}]}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a professional gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(calls["critique"], 2)                                # critique got its OWN full attempt despite review using a round first
        self.assertTrue(ctx.state["verified"])

    def test_no_vision_model_is_reported_as_unverified_not_faked(self):
        from jarvis_agent import webcheck
        if not webcheck._browser():
            self.skipTest("no Chrome/Edge installed")

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "ironfit2", "stack": "static", "summary": "s", "ui": True, "run": None, "files": [{"path": "index.html", "purpose": "x"}], "smoke": [{"path": "/", "contains": "IronFit"}]}
            if "senior product designer" in system:
                return None                                                  # no vision model available at all
            return {"content": GOOD_PAGE}
        host = FakeHost(self.tmp, script)
        import jarvis_agent.models as models
        models.DEADLINE = 3.0
        ctx = make_ctx(host, "build a gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertTrue(any("skipped" in t["name"] and "NOT verified" in t["detail"] for t in ctx.state["tests"]), ctx.state["tests"])
        models.DEADLINE = 70.0


class TestSpeed(Base):
    def test_fast_provider_wins_and_a_hung_provider_cannot_block(self):
        from jarvis_agent import models
        models.HEDGE_AFTER, models.DEADLINE = 0.3, 5.0

        def slow_llm(system, prompt, image=None, max_tokens=0, prefer_free=False, task="fast"):
            time.sleep(60)                                                   # a hung Gemini/Ollama
            return {"late": True}
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = slow_llm
        host["groq_configured"] = lambda: True
        host["groq_json"] = lambda system, prompt, task="fast", max_tokens=0: {"from": "groq"}
        t0 = time.time()
        self.assertEqual(models.ask_json(host, None, "code", "s", "p"), {"from": "groq"})
        self.assertLess(time.time() - t0, 2.0)

    def test_everything_hung_returns_within_the_deadline(self):
        from jarvis_agent import models
        models.HEDGE_AFTER, models.DEADLINE = 0.2, 1.5
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = lambda *a, **k: time.sleep(30)
        t0 = time.time()
        self.assertIsNone(models.ask_json(host, None, "code", "s", "p"))
        self.assertLess(time.time() - t0, 6.0)                               # 2 attempts x ~1.5s, never a 150s stall

    def test_without_gemini_groq_answers_and_gemini_is_never_called(self):
        from jarvis_agent import models
        models.USE_GEMINI = False
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = lambda *a, **k: self.fail("Gemini path must not be used")
        host["groq_configured"] = lambda: True
        host["groq_keys"] = lambda: ["a", "b", "c", "d", "e", "f"]
        host["groq_json"] = lambda system, prompt, task="fast", max_tokens=0: {"from": "groq", "max": max_tokens}
        d = models.ask_json(host, None, "code", "s" * 3000, "p" * 9000)
        self.assertEqual(d["from"], "groq")
        self.assertGreaterEqual(d["max"], 3000)                            # measured: a full page needs ~4.2K output tokens - a lower cap makes Groq 400 with
                                                                             # json_validate_failed 'max completion tokens reached' (the real bug)

    def test_groq_is_used_again_as_soon_as_a_key_recovers_while_local_is_still_working(self):
        """All keys resting -> the page goes to the slow local model; a key that finishes its cooldown meanwhile must be
        used right away instead of waiting behind the local model for minutes."""
        import threading as th
        from jarvis_agent import models
        models.USE_GEMINI, models.GROQ_RECHECK = False, 0.2
        self.addCleanup(lambda: setattr(models, "GROQ_RECHECK", 5.0))
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = lambda *a, **k: self.fail("Gemini path must not be used")
        cooldown = {"a:big": time.time() + 999}
        host.update(groq_configured=lambda: True, groq_keys=lambda: ["a"], GROQ_MODEL_SMART="big", GROQ_MODEL_FAST="small", _GROQ_COOLDOWN=cooldown,
                    groq_json=lambda *a, **k: {"from": "groq"}, get_config=lambda k, d=None: "on", ollama_up=lambda: True,
                    _free_json=lambda *a, **k: time.sleep(30) or {"from": "ollama"})
        th.Timer(1.0, lambda: cooldown.clear()).start()                        # the key's 60s rest ends (compressed to 1s)
        t0 = time.time()
        self.assertEqual(models.ask_json(host, None, "code", "s", "p"), {"from": "groq"})
        self.assertLess(time.time() - t0, 4.0)

    def test_without_gemini_falls_to_local_when_groq_gives_nothing(self):
        from jarvis_agent import models
        models.USE_GEMINI = False
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = lambda *a, **k: self.fail("Gemini path must not be used")
        host["groq_configured"] = lambda: True
        host["groq_keys"] = lambda: ["a"]
        host["groq_json"] = lambda *a, **k: None
        host["get_config"] = lambda k, d=None: "on"
        host["ollama_up"] = lambda: True
        host["_free_json"] = lambda system, prompt, image=None, max_tokens=0: {"from": "ollama"}
        self.assertEqual(models.ask_json(host, None, "code", "s", "p"), {"from": "ollama"})

    def test_local_ollama_gets_its_own_shot_when_cloud_providers_are_all_down(self):
        """Real failure seen live: Groq exhausted (all 8 keys rate-limited) AND Gemini failing (503/504/timeout)
        at the same time. llm_json's OWN internal Gemini timeout (150s) is longer than our whole DEADLINE, so it
        never even reaches its internal Ollama fallback in time - Ollama sits there idle and ready but never
        answers. Racing it directly, in parallel, must let it answer well within DEADLINE regardless of what
        llm_json/Groq are doing."""
        from jarvis_agent import models
        models.HEDGE_AFTER, models.DEADLINE = 0.3, 5.0
        host = FakeHost(self.tmp, lambda *a: None)
        host["llm_json"] = lambda *a, **k: time.sleep(60)                    # stuck on a dead Gemini, would never return in time
        host["groq_configured"] = lambda: False
        host["get_config"] = lambda k, d=None: "on"
        host["ollama_up"] = lambda: True
        host["_free_json"] = lambda system, prompt, image=None, max_tokens=0: {"from": "ollama"}
        t0 = time.time()
        self.assertEqual(models.ask_json(host, None, "code", "s", "p"), {"from": "ollama"})
        self.assertLess(time.time() - t0, 2.0)

    def test_files_are_generated_in_parallel(self):
        from jarvis_agent import models
        models.HEDGE_AFTER, models.DEADLINE = 12.0, 70.0

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "par", "stack": "static", "summary": "s", "ui": False, "run": None, "smoke": [{"path": "/", "contains": "page"}],
                        "files": [{"path": "index.html", "purpose": "x"}] + [{"path": f"p{i}.html", "purpose": "y"} for i in range(5)]}
            time.sleep(1.5)                                                  # each file "takes" 1.5s to write
            return {"content": "<html><title>t</title><body>page content for the test</body></html>"}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a six page site app", "app")
        t0 = time.time()
        self.assertTrue(project.run(ctx), host.said)
        self.assertLess(time.time() - t0, 6.0)                               # sequential would be >= 9s for the six files alone


class TestSectionCompleteness(Base):
    def test_incomplete_one_page_site_is_caught_and_retried(self):
        """The real-world failure: planner promises 8 sections, the model's first pass only writes hero+footer
        ('severely incomplete') - this must be caught deterministically and retried BEFORE the run/visual phase,
        not left for a subjective visual review to maybe notice."""
        calls = {"index.html": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "gym", "stack": "static", "summary": "s", "ui": False, "run": None, "smoke": [{"path": "/", "contains": "Gym"}],
                        "files": [{"path": "index.html", "purpose": "one-pager", "sections": ["hero", "about", "programs", "contact"]}]}
            calls["index.html"] += 1
            if calls["index.html"] == 1:
                self.assertNotIn("YOUR PREVIOUS ATTEMPT", prompt)
                return {"content": '<html><title>Gym</title><body><section id="hero">Gym</section><footer>done</footer></body></html>'}
            self.assertIn("about", prompt)
            self.assertIn("programs", prompt)
            self.assertNotIn("hero", prompt.split("YOUR PREVIOUS ATTEMPT")[1])          # only the ACTUALLY missing ids are called out
            return {"content": '<html><title>Gym</title><body><section id="hero">Gym</section><section id="about">A</section>'
                                '<section id="programs">P</section><section id="contact">C</section></body></html>'}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(calls["index.html"], 2)                             # exactly one targeted retry, not the whole build restarting
        html = open(os.path.join(ctx.state["project_path"], "index.html")).read()
        for sec in ("hero", "about", "programs", "contact"):
            self.assertIn(f'id="{sec}"', html)

    def test_homepage_with_a_thin_plan_is_forced_to_earn_real_sections(self):
        """The real bug found live: a multi-page site's plan lists index.html with only ['hero'] in 'sections'
        (assuming other pages carry the content), so the id-based check finds nothing missing and a
        hero-plus-footer-only homepage sails through untouched. This must be caught even though the plan
        itself, not just the delivery, was too thin."""
        calls = {"index.html": 0}

        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "gym3", "stack": "static", "summary": "s", "ui": False, "run": None, "smoke": [{"path": "/", "contains": "Gym"}],
                        "files": [{"path": "index.html", "purpose": "homepage", "sections": ["hero"]}]}
            calls["index.html"] += 1
            if calls["index.html"] == 1:
                return {"content": '<html><title>Gym</title><body><section id="hero">Gym</section><footer>done</footer></body></html>'}
            self.assertIn("too thin for a real homepage", prompt)
            return {"content": '<html><title>Gym</title><body><section id="hero">Gym</section><section id="value">V</section>'
                                '<section id="stats">S</section><section id="cta">C</section><footer>done</footer></body></html>'}
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a gym website", "website")
        self.assertTrue(project.run(ctx), host.said)
        self.assertEqual(calls["index.html"], 2)                             # forced one retry even though the id-based check alone would have passed
        html = open(os.path.join(ctx.state["project_path"], "index.html")).read()
        for sec in ("hero", "value", "stats", "cta"):
            self.assertIn(f'id="{sec}"', html)

    def test_still_incomplete_after_retry_is_flagged_not_hidden(self):
        def script(system, prompt, pf):
            if "senior software architect" in system:
                return {"name": "gym2", "stack": "static", "summary": "s", "ui": False, "run": None, "smoke": [{"path": "/", "contains": "Gym"}],
                        "files": [{"path": "index.html", "purpose": "one-pager", "sections": ["hero", "pricing"]}]}
            return {"content": '<html><title>Gym</title><body><section id="hero">Gym</section></body></html>'}          # always missing "pricing"
        host = FakeHost(self.tmp, script)
        ctx = make_ctx(host, "build a gym website", "website")
        project.run(ctx)
        self.assertTrue(any("still missing sections" in e and "pricing" in e for e in ctx.state["errors"]), ctx.state["errors"])


class TestRouterAndWrapper(Base):
    def test_gate_is_generic_not_per_business(self):
        for s in ("build me a professional restaurant website", "create an e-commerce website for a clothing brand", "build a CRM for my agency",
                  "make a portfolio website for me", "build a complete food delivery application", "create a booking platform with authentication",
                  "build an admin dashboard with analytics", "add authentication to my project", "fix this project", "analyze this csv",
                  "research quantum computing and create a report"):
            self.assertIsNotNone(router.gate(s), s)

    def test_ordinary_commands_are_not_stolen(self):
        for s in ("open chrome", "what time is it", "call didi", "play some music", "send a message to rahul", "set a timer for five minutes", "make it louder"):
            self.assertIsNone(router.gate(s), s)

    def test_wrapper_delegates_legacy_and_unrelated_to_original(self):
        host = FakeHost(self.tmp, lambda *a: None)
        seen = []
        host["project_builder_commands"] = lambda cmd, raw: seen.append(cmd) or True
        host["_BUILD_RX"] = re.compile(r"^(?:(?:build|make|create)\s+(?:me\s+)?(?:a\s+)?)?website\s+for\s+(.+)$", re.I)
        self.assertTrue(core.install(host))
        self.assertFalse(core.install(host))                                 # idempotent
        host["project_builder_commands"]("build me a website for a coffee company", "x")
        host["project_builder_commands"]("open chrome", "x")
        self.assertEqual(seen, ["build me a website for a coffee company", "open chrome"])

    def test_agent_error_falls_back_to_original(self):
        host = FakeHost(self.tmp, lambda *a: None)
        seen = []
        host["project_builder_commands"] = lambda cmd, raw: seen.append(cmd) or True
        host["get_config"] = lambda k, d=None: (_ for _ in ()).throw(RuntimeError("boom"))
        core.install(host)
        self.assertTrue(host["project_builder_commands"]("build a crm system for me", "x"))
        self.assertEqual(seen, ["build a crm system for me"])


class TestSafety(Base):
    def test_path_escape_and_shell_and_secrets_are_blocked(self):
        w = util.write_files(self.tmp, [{"path": "../evil.txt", "content": "x"}, {"path": "C:/evil.txt", "content": "x"}, {"path": "ok/a.txt", "content": "x"}])
        self.assertEqual(w, ["ok/a.txt"])
        rc, out = util.run_cmd(["powershell", "-c", "calc"], self.tmp)
        self.assertEqual(rc, 127)
        rc, out = util.run_cmd(["python", "-c", "print(1)", "&", "calc"], self.tmp)
        self.assertEqual(rc, 127)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", util.redact("api_key = sk-abcdefghijklmnopqrstuvwxyz123456"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPolish(Base):
    """The 9 problems seen on a real gym build, fixed in code after generation (no AI round needed)."""

    def _site(self, pages):
        root = os.path.join(self.tmp, "site")
        for rel, html in pages.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(html)
        return root

    def _read(self, root, rel):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_brand_name_and_odd_stylesheet_and_unknown_icons_are_unified(self):
        from jarvis_agent import polish
        page = ('<html><head><link rel="stylesheet" href="/static/assets/design-system.css">{extra}</head><body>'
                '<a class="brand" href="/">{name}</a><h1>{name} gym</h1><svg><use href="/static/assets/icons.svg#i-{icon}"></use></svg>'
                '<footer>&copy; {name}</footer></body></html>')
        root = self._site({"templates/index.html": page.format(name="PowerFit", extra="", icon="check"),
                           "templates/about.html": page.format(name="GymX", extra="", icon="yoga"),
                           "templates/classes.html": page.format(name="FitPeak", extra="", icon="made-up-thing"),
                           "templates/contact.html": page.format(name="PowerGym", extra='<link rel="stylesheet" href="/static/style.css">', icon="weight")})
        polish.run_all(root, "build a gym website", "PowerFit")
        for rel in ("index", "about", "classes", "contact"):
            html = self._read(root, f"templates/{rel}.html")
            self.assertIn('class="brand" href="/">PowerFit<', html)
            for other in ("GymX", "FitPeak", "PowerGym"):
                self.assertNotIn(other, html)
        self.assertNotIn("style.css", self._read(root, "templates/contact.html"))       # the page that looked like a different site
        self.assertIn("#i-leaf", self._read(root, "templates/about.html"))
        self.assertIn("#i-star", self._read(root, "templates/classes.html"))
        self.assertIn("#i-dumbbell", self._read(root, "templates/contact.html"))

    def test_picsum_placeholders_become_on_topic_photos_with_credit(self):
        from jarvis_agent import polish
        seen = []

        def fake_search(query, key, per_page=15):
            seen.append(query)
            return [{"id": i, "url": f"https://images.pexels.com/photos/{i}/x.jpeg", "alt": "", "by": ""} for i in range(1, 4)]
        old = polish.search
        polish.search = fake_search
        try:
            root = self._site({"index.html": '<html><body><div class="photo-frame"><img src="https://picsum.photos/seed/a/1600/900" alt="woman lifting dumbbells"></div>'
                                             '<div class="photo-frame"><img src="https://picsum.photos/seed/b/1600/900" alt="yoga class"></div><footer>f</footer></body></html>'})
            polish.fix_photos(root, "build a professional gym website", key="k")
        finally:
            polish.search = old
        html = self._read(root, "index.html")
        self.assertNotIn("picsum", html)
        self.assertIn("photos/1/", html)
        self.assertIn("photos/2/", html)                                           # never the same photo twice
        self.assertIn("Pexels", html)                                              # attribution
        self.assertTrue(all(q.startswith("gym") for q in seen), seen)

    def test_no_key_means_photos_are_left_alone(self):
        from jarvis_agent import polish
        root = self._site({"index.html": '<img src="https://picsum.photos/seed/a/1600/900" alt="x">'})
        self.assertEqual(polish.fix_photos(root, "gym"), [])                     # Base.setUp makes pexels_key() return ""
        self.assertIn("picsum", self._read(root, "index.html"))

    def test_near_background_accent_is_made_readable_and_harmonious(self):
        from jarvis_agent import design
        css, style, mode, a1, a2 = design.theme_css("gym", {"accent": "#e63946", "accent2": "#f1faee", "mode": "light", "style": "bold"})
        self.assertGreaterEqual(design.contrast(a2, "#eef2ff"), 3.0)
        self.assertGreaterEqual(design.contrast(a1, "#eef2ff"), 3.0)
        self.assertIn("--on-accent:", css)

    def test_removed_flask_first_request_hook_is_patched_so_the_app_imports(self):
        root = self._site({"app.py": "import os\nfrom flask import (\n    Flask,\n)\napp = Flask(__name__)\n\n@app.before_first_request\ndef boot():\n    pass\n"})
        self.assertEqual(project._patch_known_breakage(root), ["app.py"])
        src = self._read(root, "app.py")
        self.assertNotIn("before_first_request\n", src.replace("removed before_first_request", ""))
        compile(src, "app.py", "exec")                                             # the helper must not land inside a multi-line import


class TestEveryPageIsChecked(Base):
    def test_every_flask_page_is_opened_not_just_the_home_page(self):
        """Real build shipped 'verified' with /contact crashing (500): the plan's smoke list only had '/'."""
        root = os.path.join(self.tmp, "p")
        os.makedirs(root)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write('@app.route("/")\ndef a(): pass\n@app.route("/contact", methods=["GET", "POST"])\ndef b(): pass\n'
                     '@app.route("/api/send", methods=["POST"])\ndef c(): pass\n@app.route("/post/<int:i>")\ndef d(i): pass\n@app.route("/admin/leads")\ndef e(): pass\n')
        checks = project._with_every_page(root, [{"path": "/", "expect": 200}])
        paths = {c["path"]: c["expect"] for c in checks}
        self.assertEqual(paths, {"/": 200, "/contact": 200, "/admin/leads": "no-crash"})   # no POST-only, no parameter routes


class TestPolishRound2(TestPolish):
    def test_homepage_anchor_navbar_is_replaced_by_the_sites_real_navbar(self):
        from jarvis_agent import polish
        real = '<ul class="nav-links"><li><a href="/">Home</a></li><li><a href="/classes">Classes</a></li><li><a href="/contact">Contact</a></li></ul>'
        root = self._site({"templates/base.html": "<nav>" + real + "</nav>",
                           "templates/index.html": '<nav><ul class="nav-links"><li><a href="#about">About</a></li><li><a href="#blog">Blog</a></li></ul></nav>'})
        polish.fix_navbar(root)
        self.assertIn('href="/classes"', self._read(root, "templates/index.html"))
        self.assertNotIn('href="#blog"', self._read(root, "templates/index.html"))

    def test_one_page_site_keeps_its_anchor_navbar(self):
        from jarvis_agent import polish
        root = self._site({"index.html": '<ul class="nav-links"><li><a href="#about">A</a></li><li><a href="#menu">M</a></li><li><a href="#contact">C</a></li></ul>'})
        self.assertEqual(polish.fix_navbar(root), [])

    def test_text_only_homepage_hero_becomes_a_photo_hero(self):
        from jarvis_agent import polish
        old = polish.search
        polish.search = lambda q, key, per_page=15: [{"id": 1, "url": "https://images.pexels.com/photos/1/x.jpeg?a=1", "alt": "", "by": ""}]
        try:
            root = self._site({"index.html": '<section class="hero" id="hero"><h1>Gym</h1></section><footer>f</footer>',
                               "about.html": '<section class="hero"><h1>About</h1></section>'})
            self.assertEqual(polish.fix_home_hero(root, "build a gym website", key="k"), ["index.html"])
        finally:
            polish.search = old
        html = self._read(root, "index.html")
        self.assertIn("hero-cover", html)
        self.assertIn("--hero-img:url('https://images.pexels.com/photos/1/x.jpeg", html)
        self.assertNotIn("hero-cover", self._read(root, "about.html"))           # only the homepage

    def test_open_24_7_claim_is_removed_when_hours_say_otherwise(self):
        from jarvis_agent import polish
        root = self._site({"index.html": '<span class="badge">Open 24/7</span><h3>24/7 Access</h3>'})
        polish.fix_hours(root, "Mon-Fri 5am-10pm, Sat-Sun 7am-8pm")
        html = self._read(root, "index.html")
        self.assertNotIn("24/7", html)
        self.assertIn("Open 7 Days", html)
        root2 = self._site({"index.html": "<p>Open 24/7</p>"})
        self.assertEqual(polish.fix_hours(root2, "Open 24/7, staffed 6am-10pm"), [])   # a real 24/7 gym keeps its claim


class TestServerStartLine(Base):
    def test_flask_app_without_run_line_gets_one_and_really_starts(self):
        """Seen live: a fix round dropped app.run(); the process exited 0 with an empty log and 5 rounds were wasted."""
        root = os.path.join(self.tmp, "p")
        os.makedirs(root)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write('from flask import Flask\napp = Flask(__name__)\n\n@app.route("/")\ndef home():\n    return "<html><title>t</title><body>coffee shop home page</body></html>"\n')
        self.assertEqual(project._patch_known_breakage(root), ["app.py"])
        self.assertEqual(project._patch_known_breakage(root), [])                # idempotent
        srv = runner.Server(root, ["python", "app.py"])
        url = srv.start()
        self.addCleanup(srv.stop)
        self.assertTrue(srv.wait_ready(), srv.log_tail())
        self.assertEqual(runner.smoke(url, [{"path": "/", "contains": "coffee"}]), [])


class TestFixCannotDeletePages(Base):
    def _root(self, app_src, pages=None):
        root = os.path.join(self.tmp, "p")
        os.makedirs(os.path.join(root, "templates"), exist_ok=True)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write(app_src)
        for rel, html in (pages or {}).items():
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(html)
        return root

    def test_long_file_is_shown_whole_or_clearly_marked_truncated(self):
        """Seen live: app.py was silently cut at 4,000 chars, the fixer 'completed' it and deleted half the routes + app.run()."""
        body = "".join(f'@app.route("/p{i}")\ndef p{i}():\n    return "page {i} " * 20\n\n' for i in range(120))
        root = self._root("from flask import Flask\napp = Flask(__name__)\n" + body)
        shown = project._files_for_fix(root, "error in app.py")
        self.assertIn("@app.route(\"/p60\")", shown)                              # well past the old 4,000-char cut
        huge = self._root("x = 1\n" + "# filler line\n" * 5000)
        self.assertIn("TRUNCATED", project._files_for_fix(huge, "app.py"))

    def test_fix_that_deletes_routes_is_rejected(self):
        root = self._root('@app.route("/")\ndef a(): pass\n@app.route("/menu")\ndef b(): pass\n@app.route("/order")\ndef c(): pass\n')
        self.assertEqual(project._drops_routes(root, [{"path": "app.py", "content": '@app.route("/")\ndef a(): pass\n# other routes here\n'}]), ["app.py"])
        self.assertEqual(project._drops_routes(root, [{"path": "app.py", "content": '@app.route("/")\n@app.route("/menu")\n@app.route("/order")\n@app.route("/new")\n'}]), [])

    def test_every_navbar_link_is_checked(self):
        root = self._root('@app.route("/")\ndef a(): pass\n',
                          {"templates/base.html": '<ul class="nav-links"><li><a href="/">Home</a></li><li><a href="/menu">Menu</a></li><li><a href="/order#now">Order</a></li><li><a href="#top">Top</a></li></ul>'})
        paths = [c["path"] for c in project._with_every_page(root, [{"path": "/", "expect": 200}])]
        self.assertEqual(sorted(paths), ["/", "/menu", "/order"])


class TestWebsiteBridge(Base):
    """Phase 2: 'website for X' -> new builder first, legacy builder only as fallback."""

    def _host(self, desc="a coffee company", locked=True, config=None):
        import threading
        from jarvis_agent import website_bridge
        host = FakeHost(self.tmp, lambda *a: None)
        self.legacy = []
        host["build_project_from_description"] = lambda description, max_fix_rounds=3: self.legacy.append((description, max_fix_rounds)) or "LEGACY"
        host["_BUILD_LOCK"] = threading.Lock()
        host["_BUILD_ACTIVE"] = {"desc": desc}
        if locked:
            host["_BUILD_LOCK"].acquire()
        host["get_config"] = lambda k, d=None: (config or {}).get(k, d)
        self.new = []
        keep = website_bridge.build_with_new
        self.addCleanup(lambda: setattr(website_bridge, "build_with_new", keep))
        self.assertTrue(website_bridge.install(host))
        self.assertFalse(website_bridge.install(host))                       # idempotent
        return host, website_bridge

    def test_voice_build_uses_new_builder_and_not_legacy(self):
        host, wb = self._host()
        wb.build_with_new = lambda h, d: self.new.append(d) or (True, True)
        self.assertTrue(host["build_project_from_description"]("a coffee company"))
        self.assertEqual((self.new, self.legacy), (["a coffee company"], []))

    def test_built_but_unverified_does_not_rebuild_with_legacy(self):
        host, wb = self._host()
        wb.build_with_new = lambda h, d: (True, False)
        self.assertFalse(host["build_project_from_description"]("a coffee company"))
        self.assertEqual(self.legacy, [])

    def test_new_builder_producing_nothing_falls_back_to_legacy(self):
        host, wb = self._host()
        wb.build_with_new = lambda h, d: (False, False)
        self.assertEqual(host["build_project_from_description"]("a coffee company"), "LEGACY")
        self.assertEqual(self.legacy, [("a coffee company", 3)])
        self.assertTrue(any("classic builder" in s for s in host.said), host.said)

    def test_new_builder_crash_falls_back_to_legacy(self):
        host, wb = self._host()
        wb.build_with_new = lambda h, d: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertEqual(host["build_project_from_description"]("a coffee company"), "LEGACY")
        self.assertEqual(self.legacy, [("a coffee company", 3)])

    def test_direct_calls_and_switch_go_straight_to_legacy(self):
        host, wb = self._host()
        wb.build_with_new = lambda h, d: self.new.append(d) or (True, True)
        host["build_project_from_description"]("a coffee company", max_fix_rounds=1)          # how the legacy self-tests call it
        host["build_project_from_description"]("something else")                             # not the build the voice trigger started
        self.assertEqual((self.new, self.legacy), ([], [("a coffee company", 1), ("something else", 3)]))
        host2, wb2 = self._host(locked=False)                                                # no voice build in progress
        wb2.build_with_new = lambda h, d: self.new.append(d) or (True, True)
        host2["build_project_from_description"]("a coffee company")
        host3, wb3 = self._host(config={"website_builder": "old"})                           # the off switch
        wb3.build_with_new = lambda h, d: self.new.append(d) or (True, True)
        host3["build_project_from_description"]("a coffee company")
        self.assertEqual(self.new, [])

    def test_real_new_builder_runs_end_to_end_through_the_bridge(self):
        host, wb = self._host(desc="a zz bakery")
        page = '<!doctype html><html><head><title>ZZ Bakery</title><meta name="viewport" content="width=device-width"></head><body><h1>ZZ Bakery</h1><p>Fresh bread daily</p></body></html>'

        def script(system, prompt, prefer_free):
            if system.startswith(router.CLASSIFY_SYSTEM[:30]):
                return {"kind": "website", "title": "ZZ Bakery", "requirements": []}
            if system.startswith(project.PLAN_SYSTEM[:30]):
                return {"name": "zz-bakery", "stack": "static", "ui": False, "summary": "one page", "files": [{"path": "index.html", "purpose": "home"}],
                        "run": None, "checks": [{"path": "/", "expect": 200}]}
            if system.startswith(project.FILE_SYSTEM[:30]):
                return {"content": page}
            return None
        host.script = script
        host["build_project_from_description"]("a zz bakery")
        self.assertEqual(self.legacy, [], host.said)                                         # the new builder really built it; legacy never ran
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "zz-bakery", "index.html")), host.said)


class TestPhase3RealRunFindings(Base):
    """Bugs found by running the skills for real (not scripted)."""

    def test_named_folder_that_does_not_exist_never_edits_another_project(self):
        other = os.path.join(self.tmp, "latest-project")
        os.makedirs(other)
        with open(os.path.join(other, "app.py"), "w") as fh:
            fh.write("x = 1\n")
        host = FakeHost(self.tmp, lambda *a: {"files": [{"path": "app.py", "content": "x = 2\n"}]})
        ctx = make_ctx(host, 'fix the project at "C:\zz_no_such_dir_123\shop" its tests are failing', "coding")
        self.assertFalse(coding.run(ctx))
        self.assertEqual(open(os.path.join(other, "app.py")).read(), "x = 1\n")          # the most recent project was NOT touched
        self.assertTrue(any("does not exist" in s for s in host.said), host.said)
        self.assertEqual(host.calls, [])                                                 # and no model was even asked

    def test_stdlib_clean_drops_empty_rows_and_tidies_headers(self):
        out = os.path.join(self.tmp, "c.csv")
        rows = [{"Name": "Ravi", " City ": "Pune"}, {"Name": "Ravi", " City ": "Pune"}, {"Name": "", " City ": ""}, {"Name": "Asha", " City ": " Delhi "}]
        import builtins
        real_import = builtins.__import__
        builtins.__import__ = lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name == "pandas" else real_import(name, *a, **k)
        try:
            removed = data.clean(rows, out)
        finally:
            builtins.__import__ = real_import
        self.assertEqual(removed, 2)
        self.assertEqual(open(out, encoding="utf-8").read().split(), ["name,city", "Ravi,Pune", "Asha,Delhi"])



class TestCodingRegressionGuard(Base):
    def _proj(self, files):
        root = os.path.join(self.tmp, "proj")
        os.makedirs(root, exist_ok=True)
        for rel, content in files.items():
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(content)
        return root

    def test_editing_a_test_to_make_it_pass_is_refused_and_files_are_restored(self):
        good_test = "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
        root = self._proj({"calc.py": "def add(a, b):\n    return a - b\n", "test_calc.py": good_test})
        cheat = {"files": [{"path": "test_calc.py", "content": good_test.replace("5)", "-1)")}], "summary": "made the test pass"}
        host = FakeHost(self.tmp, lambda system, prompt, pf: {"files": ["calc.py", "test_calc.py"], "analysis": "x"} if "READ" in system else cheat)
        ctx = make_ctx(host, f'fix the project at "{root}" its tests are failing', "coding")
        self.assertFalse(coding.run(ctx))
        self.assertEqual(open(os.path.join(root, "test_calc.py")).read(), good_test)          # the test was never weakened
        self.assertTrue(any("existing test may not be edited" in e for e in ctx.state["errors"]), ctx.state["errors"])

    def test_truncated_half_size_and_placeholder_rewrites_are_refused(self):
        big = "def a():\n    return 1\n" * 1200                                              # longer than READ_LIMIT
        root = self._proj({"big.py": big, "mid.py": "x = 1\n" * 200, "ok.py": "y = 1\n"})
        files = [{"path": "big.py", "content": big}, {"path": "mid.py", "content": "x = 1\n"},
                 {"path": "ok.py", "content": "y = 2\n# ... rest of the code unchanged\n"}, {"path": "new.py", "content": "z = 3\n"}]
        ok, rejected = coding.guard_change(root, files, {"big.py"}, True)
        self.assertEqual([f["path"] for f in ok], ["new.py"])
        self.assertEqual(len(rejected), 3, rejected)

    def test_asking_for_tests_allows_test_edits(self):
        root = self._proj({"test_a.py": "import unittest\n"})
        protect = not coding._WANTS_TEST_EDIT_RX.search("add more unit tests to my project")
        ok, rejected = coding.guard_change(root, [{"path": "test_a.py", "content": "import unittest\n# more\n"}], set(), protect)
        self.assertEqual((len(ok), rejected), (1, []))

    def test_small_questions_are_allowed_on_groq(self):
        from jarvis_agent import models
        models._GWIN.clear()
        self.assertEqual(models._groq_reserve("fast", 300, 500), ("fast", 500))                # classify-sized call: used to be refused -> 45s wait + slow local model
