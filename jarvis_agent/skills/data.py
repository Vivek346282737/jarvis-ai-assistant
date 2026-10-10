"""Data skill: profile a local CSV/JSON/Excel file deterministically, clean it into a NEW file (original untouched),
write a report. Only aggregated statistics ever leave the machine, and 'private' data goes to the local model first."""
import csv
import json
import os
import re

from . import register
from .. import util
from ..models import ask_json
from ..router import extract_path


def _load(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        import pandas as pd
    except ImportError:
        pd = None
    if pd is not None:
        if ext == ".csv":
            return pd.read_csv(path)
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(path)
        if ext == ".json":
            return pd.read_json(path)
    if ext == ".csv":                                                          # stdlib fallback so the skill still works without pandas
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    if ext == ".json":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else [d]
    raise ValueError(f"cannot read {ext or 'that'} files without pandas installed")


def profile(data):
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            return {"rows": int(len(data)), "columns": {c: {"dtype": str(data[c].dtype), "nulls": int(data[c].isna().sum()), "unique": int(data[c].nunique())} for c in data.columns},
                    "duplicates": int(data.duplicated().sum()), "describe": json.loads(data.describe(include="all").fillna("").astype(str).to_json())}
    except ImportError:
        pass
    cols = list(data[0].keys()) if data else []
    return {"rows": len(data), "columns": {c: {"nulls": sum(1 for r in data if r.get(c) in (None, "")), "unique": len({str(r.get(c)) for r in data})} for c in cols}}


def clean(data, out_path):
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            df = data.copy()
            df.columns = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in df.columns]
            for c in df.select_dtypes(include="object"):
                df[c] = df[c].astype(str).str.strip().replace({"nan": None, "": None})
            before = len(df)
            df = df.drop_duplicates().dropna(how="all")
            df.to_csv(out_path, index=False)
            return before - len(df)
    except ImportError:
        pass
    seen, rows = set(), []
    for r in data:
        row = {re.sub(r"\s+", "_", str(k).strip().lower()): ("" if v is None else str(v).strip()) for k, v in r.items()}          # same tidy-up the pandas path does
        key = tuple(sorted(row.items()))
        if key not in seen and any(row.values()):                              # drop duplicates AND rows that are completely empty
            seen.add(key)
            rows.append(row)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    return len(data) - len(rows)


@register("data")
def run(ctx):
    host, st, say = ctx.host, ctx.state, ctx.say
    desc = st["request"]
    path = extract_path(desc)
    if not path or not os.path.isfile(path):
        say("Tell me the file, Boss - for example: analyze the csv \"C:\\path\\to\\data.csv\".")
        st.error("no data file identified")
        return False
    st.phase("run")
    st.op("loading data")
    try:
        data = _load(path)
    except Exception as e:
        st.error(e)
        say(f"I could not read that file, Boss: {str(e)[:140]}")
        return False
    prof = profile(data)
    st.test("data loaded", True, f"{prof['rows']} rows")
    out_dir = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    removed = None
    if re.search(r"\bclean\b", desc, re.I):
        removed = clean(data, os.path.join(out_dir, f"{stem}_clean.csv"))
        st["files_changed"].append(f"{stem}_clean.csv")
        st.test("cleaned copy written", True, f"{removed} rows dropped")
    st.op("writing analysis")
    d = ask_json(host, st, "private", "You are a data analyst. From this dataset PROFILE (aggregate statistics only) reply ONLY JSON: "
                 '{"summary": "<=120 words", "findings": ["..."], "quality_issues": ["..."], "suggested_next_steps": ["..."]}',
                 f"Request: {desc}\nProfile: {json.dumps(prof, default=str)[:5000]}", max_tokens=1000) or {}
    lines = [f"# Data report: {os.path.basename(path)}", "", f"Rows: {prof['rows']}", "", "## Columns"] + \
            [f"- {c}: {json.dumps(v)}" for c, v in prof["columns"].items()]
    if removed is not None:
        lines += ["", f"## Cleaning", f"- Wrote {stem}_clean.csv ({removed} duplicate/empty rows removed). The original file was not modified."]
    for title, key in (("Summary", "summary"), ("Findings", "findings"), ("Quality issues", "quality_issues"), ("Next steps", "suggested_next_steps")):
        v = d.get(key)
        if v:
            lines += ["", f"## {title}"] + ([str(v)] if isinstance(v, str) else [f"- {x}" for x in v])
    if not d:
        lines += ["", "(No AI model was available for the narrative summary; the statistics above are exact.)"]
    rp = os.path.join(out_dir, f"{stem}_report.md")
    with open(rp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    st["files_changed"].append(os.path.basename(rp))
    st["verified"], st["final"] = True, "verified"
    st.phase("done")
    say(f"Done, Boss. {prof['rows']} rows analysed{'; ' + str(removed) + ' bad rows removed in a cleaned copy' if removed is not None else ''}. " +
        (str(d.get("summary"))[:200] + " " if d.get("summary") else "") + f"The full report is at {rp}.")
    return True
