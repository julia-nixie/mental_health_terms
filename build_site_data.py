#!/usr/bin/env python3
"""Convert mental_disorders_stats.csv into docs/data.json for the GitHub Pages site.

Run after every collection:  python3 build_site_data.py
Values are phrases per million (frequency x 1e6) rounded to 4 significant digits.
"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "mental_disorders_stats.csv"
DST = HERE / "docs" / "data.json"

terms, order, years = {}, [], set()
with SRC.open(newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        y = int(r["year"])
        years.add(y)
        t = terms.get(r["term"])
        if t is None:
            t = terms[r["term"]] = {
                "n": r["term"],
                "c": r["category"],
                "h": r["has_data"] == "true",
                "y": {},
            }
            order.append(r["term"])
        t["y"][y] = float(r["frequency"]) * 1e6

y0, y1 = min(years), max(years)
out = {"y0": y0, "y1": y1, "terms": []}
for name in order:
    t = terms[name]
    vals = [float(f"{t['y'].get(y, 0.0):.4g}") for y in range(y0, y1 + 1)]
    out["terms"].append({"n": t["n"], "c": t["c"], "h": t["h"],
                         "v": [int(v) if v == int(v) else v for v in vals]})
DST.parent.mkdir(exist_ok=True)
DST.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"{len(out['terms'])} terms, {y0}-{y1}, {DST.stat().st_size / 1024:.0f} KB -> {DST}")
