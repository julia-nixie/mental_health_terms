#!/usr/bin/env python3
"""Convert mental_disorders_stats.csv into docs/data.json for the GitHub Pages site.

Run after every collection:  python3 build_site_data.py
Values are phrases per million (frequency x 1e6) rounded to 4 significant digits.

Variant mode (e.g. the autism page): every searched phrase becomes an entry of its group, so the
page can open a group into its variants:
    python3 build_site_data.py --variants autism_variant_stats.csv --terms autism_terms.csv \
        --out docs/autism/data.json
"""
import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--variants", help="per-variant stats CSV from collect_ngram_stats.py --variants-output")
ap.add_argument("--terms", help="terms CSV, used to list groups that have no data at all")
ap.add_argument("--out", default=str(HERE / "docs" / "data.json"))
args = ap.parse_args()
SRC = HERE / "mental_disorders_stats.csv"
DST = Path(args.out)


def sig(x):
    return int(x) if x == int(x) else x


if args.variants:
    series, order, years = {}, [], set()
    with open(args.variants, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["term"], r["variant"])
            if key not in series:
                series[key] = {}
                order.append(key)
            y = int(r["year"])
            years.add(y)
            series[key][y] = float(r["frequency"]) * 1e6
    y0, y1 = min(years), max(years)

    def label(q):  # the variant name as written in the terms file
        return q

    # era tags: the terms CSV's "eras" column is "era: variant, variant | era2: ..."
    era_of = {}
    if args.terms:
        with open(args.terms, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for chunk in (r.get("eras") or "").split("|"):
                    if ":" in chunk:
                        era, members = chunk.split(":", 1)
                        for m in members.split(","):
                            era_of[(r["term"], m.strip().lower())] = era.strip()

    out, seen, groups = {"y0": y0, "y1": y1, "terms": []}, set(), []
    for term, q in order:
        vals = [float(f"{series[(term, q)].get(y, 0.0):.4g}") for y in range(y0, y1 + 1)]
        name = label(q)
        if name in seen:
            name = f"{name} ({term})"
        seen.add(name)
        if term not in groups:
            groups.append(term)
        entry = {"n": name, "c": term, "h": any(v > 0 for v in vals), "v": [sig(v) for v in vals]}
        era = era_of.get((term, label(q).lower()))
        if era:
            entry["e"] = era
        out["terms"].append(entry)
    if args.terms:  # groups whose phrases returned no data at all
        with open(args.terms, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["term"] not in groups:
                    out["terms"].append({"n": r["term"], "c": r["term"], "h": False, "v": [0] * (y1 - y0 + 1)})
else:
    terms, order, years = {}, [], set()
    with SRC.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            y = int(r["year"])
            years.add(y)
            t = terms.get(r["term"])
            if t is None:
                t = terms[r["term"]] = {"n": r["term"], "c": r["category"], "h": r["has_data"] == "true", "y": {}}
                order.append(r["term"])
            t["y"][y] = float(r["frequency"]) * 1e6
    y0, y1 = min(years), max(years)
    out = {"y0": y0, "y1": y1, "terms": []}
    for name in order:
        t = terms[name]
        vals = [float(f"{t['y'].get(y, 0.0):.4g}") for y in range(y0, y1 + 1)]
        out["terms"].append({"n": t["n"], "c": t["c"], "h": t["h"], "v": [sig(v) for v in vals]})
DST.parent.mkdir(parents=True, exist_ok=True)
DST.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"{len(out['terms'])} entries, {y0}-{y1}, {DST.stat().st_size / 1024:.0f} KB -> {DST}")
