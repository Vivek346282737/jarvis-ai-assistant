"""Research skill: wraps JARVIS's existing cross-checking research() and turns the result into a saved report."""
import os
import re

from . import register
from .. import util


@register("research")
def run(ctx):
    host, st, say = ctx.host, ctx.state, ctx.say
    topic = str(ctx.spec.get("topic") or re.sub(r"^(?:jarvis[,\s]+)?(?:please\s+)?research\s+(?:about\s+|on\s+)?", "", st["request"], flags=re.I)).strip(" .")
    fn = host.get("research")
    if not fn or not topic:
        say("Research is not available right now, Boss.")
        return False
    st.phase("run")
    st.op("researching")
    res = fn(topic, depth=4)
    if not res:
        st.error("no sources found")
        say(f"I could not find reliable sources for {topic[:60]}, Boss - I would rather say that than guess.")
        return False
    lines = [f"# Research: {topic}", "", f"Confidence: {res['confidence']:.2f}", "", "## Summary", res["summary"]]
    if res["key_points"]:
        lines += ["", "## Key points"] + [f"- {p}" for p in res["key_points"]]
    if res["procedure"]:
        lines += ["", "## Procedure"] + [f"{i}. {s}" for i, s in enumerate(res["procedure"], 1)]
    if res["conflicts"]:
        lines += ["", "## Sources disagree on"] + [f"- {c}" for c in res["conflicts"]]
    lines += ["", "## Sources"] + [f"- {u}" for u in res["sources"]]
    try:
        base = host["_project_base_dir"]()
    except Exception:
        base = os.path.expanduser("~")
    path = os.path.join(base, f"research-{util.slug(topic)}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    st["files_changed"].append(path)
    st["verified"], st["final"] = True, "verified"
    st.phase("done")
    say(f"Done, Boss. {res['summary'][:220]} Sources cross-checked: {len(res['sources'])}. The full report is at {path}.")
    return True
