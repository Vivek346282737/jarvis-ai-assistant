"""Existing-project skill: inspect -> understand -> baseline check -> targeted change -> retest -> regression -> verify.
Never rebuilds a project: it checkpoints first, changes only files the model shows are relevant, and if the final check is
worse than the baseline it restores the changed files and says so."""
import json
import os
import re
import shutil

from . import register
from .. import util
from ..models import ask_json
from ..router import _PATH_RX, extract_path

MAX_FIX_ROUNDS = 3
MAX_CHANGED = 8
READ_LIMIT = 12000                              # chars of one file shown to the model; a longer file is marked TRUNCATED and may not be rewritten
_PLACEHOLDER_RX = re.compile(r"(?:#|//|<!--)\s*(?:\.\.\.|\u2026)?\s*(?:rest of|remaining|existing|other)\b[^\n]{0,40}\b(?:code|file|routes?|functions?|unchanged)", re.I)
_WANTS_TEST_EDIT_RX = re.compile(r"\b(?:write|add|update|change|edit|rewrite|create)\s+(?:\w+\s+){0,3}tests?\b", re.I)

SELECT_SYSTEM = ("You are a senior engineer looking at an existing project. Given the request, the file tree and any failing check output, pick the files that "
                 "must be READ to do the job. Reply ONLY JSON: {\"files\": [\"rel/path\", ...up to 8], \"analysis\": \"one-paragraph diagnosis\"}. Use only paths from the tree.")
CHANGE_SYSTEM = ("You modify an existing project. Preserve all existing behaviour unless the request says otherwise, respect the existing stack and code style, and change "
                 "ONLY what is needed. Reply ONLY JSON: {\"files\": [{\"path\": \"...\", \"content\": \"<COMPLETE new file content>\"}], \"summary\": \"what changed and why\"}. "
                 f"At most {MAX_CHANGED} files; new files are allowed only when required.")


def _find_root(host, text):
    p = extract_path(text)
    if p:
        return p if os.path.isdir(p) else os.path.dirname(p)
    if _PATH_RX.search(text or ""):                                            # a folder WAS named but does not exist: never guess and edit some other project instead
        return None
    try:
        base = host["_project_base_dir"]()
        dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)) and d != "jarvis_checkpoints"]
        return max(dirs, key=os.path.getmtime) if dirs else None
    except Exception:
        return None


def detect_check(root):
    """The project's own way of checking itself. -> (cmd, label)."""
    pj = os.path.join(root, "package.json")
    if os.path.isfile(pj):
        try:
            scripts = (json.load(open(pj, encoding="utf-8")).get("scripts") or {})
        except (OSError, ValueError):
            scripts = {}
        for s in ("test", "build"):
            if s in scripts and "no test specified" not in scripts[s]:
                return ["npm", "run", s], f"npm run {s}"
    files = util.tree(root)
    if any(f.startswith("tests/") or f.startswith("test_") or "/test_" in f for f in files):
        try:
            import pytest  # noqa: F401
            return ["python", "-m", "pytest", "-q", "-x"], "pytest"
        except ImportError:
            return ["python", "-m", "unittest", "discover", "-q"], "unittest"
    if any(f.endswith(".py") for f in files):
        return ["python", "-m", "compileall", "-q", "."], "python compile check"
    return None, "no automated check found"


def _is_test_file(rel):
    b = os.path.basename(rel).lower()
    return rel.startswith("tests/") or "/tests/" in rel or b.startswith("test_") or bool(re.search(r"(_test\.py|\.(test|spec)\.[jt]sx?)$", b))


def guard_change(root, files, truncated, protect_tests):
    """Regression guard, deterministic: a proposed rewrite is refused when it would silently damage the project.
    -> (accepted_files, [reason, ...]). Nothing refused is ever written."""
    ok, rejected = [], []
    for f in files:
        rel, new = f["path"], str(f.get("content") or "")
        full = os.path.join(root, rel)
        exists = os.path.isfile(full)
        old_size = os.path.getsize(full) if exists else 0
        if exists and protect_tests and _is_test_file(rel):
            rejected.append(f"{rel}: an existing test may not be edited to make a failing check pass - fix the code under test instead")
        elif exists and rel in truncated:
            rejected.append(f"{rel}: only the first {READ_LIMIT} characters of this file were shown, so rewriting it would delete the rest")
        elif _PLACEHOLDER_RX.search(new):
            rejected.append(f"{rel}: the new content contains a placeholder instead of the real code - write the COMPLETE file")
        elif exists and old_size > 400 and len(new.encode("utf-8")) * 2 < old_size:
            rejected.append(f"{rel}: the new content is less than half the size of the original ({len(new)} vs {old_size} bytes) - existing code would be lost")
        else:
            ok.append(f)
    return ok, rejected


def _check(root, cmd):
    if not cmd:
        return True, ""
    rc, out = util.run_cmd(cmd, root, timeout=240)
    return rc == 0, out


@register("coding")
def run(ctx):
    host, st, say = ctx.host, ctx.state, ctx.say
    desc = st["request"]
    root = st["project_path"] or _find_root(host, desc)
    if not root or not os.path.isdir(root):
        if _PATH_RX.search(desc or ""):
            say("That folder does not exist, Boss, so I did not touch anything. Check the path and tell me again.")
            st.error("the named project folder does not exist")
            return False
        say("Tell me the project folder, Boss - for example: fix my project at \"C:\\path\\to\\project\".")
        st.error("no project folder identified")
        return False
    st["project_path"] = root
    st.phase("plan")
    base = os.path.dirname(root)
    say(f"Looking at {os.path.basename(root)} first, Boss. I will not rebuild it, only change what is needed.")
    st.op("checkpoint")
    try:
        cp = util.checkpoint(root, base)
    except (OSError, shutil.Error) as e:
        st.error(f"could not create a checkpoint: {e}")
        say("I could not create a safety backup, so I am not touching that project, Boss.")
        return False
    st.done(f"checkpoint {cp}")
    tree = util.tree(root)
    cmd, label = detect_check(root)
    st.op("baseline check")
    base_ok, base_out = _check(root, cmd)
    st.test(f"baseline: {label}", base_ok, base_out)

    sel = ask_json(host, st, "debug", SELECT_SYSTEM, f"Request: {desc}\nFile tree:\n" + "\n".join(tree[:150]) +
                   (f"\n\nFAILING CHECK ({label}):\n{base_out[-1500:]}" if not base_ok else ""), max_tokens=800) or {}
    picks = [p for p in (sel.get("files") or []) if p in tree][:8] or [t for t in tree if t.endswith((".py", ".js", ".ts", ".html"))][:6]
    changed = []
    truncated = set()
    protect_tests = not _WANTS_TEST_EDIT_RX.search(desc or "")
    problems = [] if base_ok else [f"{label} failed:\n{base_out[-1500:]}"]
    for attempt in range(MAX_FIX_ROUNDS + 1):
        st["retries"] = attempt
        st.phase("generate")
        st.op("modifying")
        shown = []
        for p in dict.fromkeys(picks + changed):
            text = util.read_text(os.path.join(root, p), READ_LIMIT + 1)
            if len(text) > READ_LIMIT:                                          # 5000 chars used to be cut off SILENTLY and the model then "rewrote the complete file" without its tail
                truncated.add(p)
                text = text[:READ_LIMIT] + "\n[TRUNCATED - this file is longer; do NOT rewrite it]"
            shown.append(f"--- {p} ---\n{text}")
        contents = "\n".join(shown)
        ch = ask_json(host, st, "code", CHANGE_SYSTEM,
                      f"Request: {desc}\nDiagnosis: {sel.get('analysis', '')}\n" + (f"Problems to fix:\n{chr(10).join(problems)[:2500]}\n" if problems else "") +
                      f"Relevant files:\n{contents}", max_tokens=6000)
        if not ch or not ch.get("files"):
            st.error("the model proposed no change")
            break
        files = [f for f in ch["files"] if util.safe_rel(f.get("path"))][:MAX_CHANGED]
        files, rejected = guard_change(root, files, truncated, protect_tests)
        for r in rejected:
            st.error("change refused: " + r)
        if not files:                                                           # nothing safe to write: tell the model exactly why and let it try again
            problems = ["Your change was REFUSED and nothing was written: " + "; ".join(rejected)]
            continue
        for f in files:                                                        # backup-on-first-touch is already covered by the full checkpoint
            if f["path"] not in changed:
                changed.append(f["path"])
        util.write_files(root, files)
        st["files_changed"] = list(changed)
        st.phase("verify")
        st.op("retesting")
        ok, out = _check(root, cmd)
        st.test(f"after change ({label})", ok, out)
        py = [p for p in changed if p.endswith(".py") and os.path.isfile(os.path.join(root, p))]
        if ok and py:                                                           # the project's own check may not load every changed file (npm project, no tests...)
            rc, out2 = util.run_cmd(["python", "-m", "py_compile"] + py, root, timeout=60)
            st.test("regression: every changed python file still compiles", rc == 0, out2)
            if rc != 0:
                ok, out = False, out2
        if ok:
            problems = []
            break
        problems = [f"{label} failed after the change:\n{out[-1500:]}"]
        st.error(problems[0])
    verified = not problems
    if not verified:                                                            # never leave the project worse than we found it
        for rel in changed:
            src = os.path.join(cp, rel)
            dst = os.path.join(root, rel)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
            elif os.path.isfile(dst):
                os.remove(dst)
        say(f"I could not make that change pass its checks, Boss, so I restored your files exactly as they were. Backup is at {cp}. The last error: {problems[0].splitlines()[0][:140]}")
    else:
        note = "" if cmd else " There is no automated test in that project, so I could only check it by reading, not by running."
        say(f"Done, Boss. Changed {len(changed)} file{'s' if len(changed) != 1 else ''} ({', '.join(changed)[:120]}); {label} passes. Backup of the original is at {cp}.{note}")
    st["verified"] = verified
    st["final"] = "verified" if verified else "reverted"
    st.phase("done")
    return verified
