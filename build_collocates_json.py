#!/usr/bin/env python3
"""Turn autism_collocates.csv into docs/autism/collocates.json for the collocates page.

Layout: {"decades": [...], "terms": [{"term": ..., "variants": [{"v": ..., "p": {pattern: {decade:
[[word, share, rank], ...]}}}]}]}, terms and variants in the order of the terms CSV.

Usage: python3 build_collocates_json.py [--csv autism_collocates.csv] [--terms autism_terms.csv]
                                         [--out docs/autism/collocates.json]
"""
import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "autism_collocates.csv"))
    ap.add_argument("--terms", default=str(HERE / "autism_terms.csv"))
    ap.add_argument("--out", default=str(HERE / "docs" / "autism" / "collocates.json"))
    args = ap.parse_args()

    data, decades = {}, set()
    with open(args.csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = int(r["decade"])
            decades.add(d)
            cell = data.setdefault(r["term"], {}).setdefault(r["variant"], {}) \
                       .setdefault(r["pattern"], {}).setdefault(str(d), [])
            cell.append([r["collocate"], round(float(r["share"]), 5), int(r["rank"])])

    # All patterns of the same word kind are combined into one list per kind ("any", "noun", "adj"):
    # before and after the term, next to it or through a linking word. The linking word is not part of
    # the entry ("children with autism" counts for "children"), and a word found by several patterns
    # ("children autism", "children with autism", "autism in children") gets the sum of their shares:
    # these phrases never overlap, and all shares are relative to the variant's own frequency.
    # Every entry is kept; each list is sorted by share.
    for variants in data.values():
        for pats in variants.values():
            for name in [n for n in pats if "_" in n]:
                kind = name.rsplit("_", 1)[1]
                for d, items in pats.pop(name).items():
                    pats.setdefault(kind, {}).setdefault(d, []).extend(items)
            for cells in pats.values():
                for d, items in cells.items():
                    tot = {}
                    for w, share, _ in items:
                        tot[w] = tot.get(w, 0.0) + share
                    cells[d] = [[w, round(share, 5), i] for i, (w, share) in
                                enumerate(sorted(tot.items(), key=lambda x: -x[1]), 1)]

    terms = []
    with open(args.terms, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vs = [v.strip() for v in r["variants"].split(";") if v.strip()]
            got = [{"v": v, "p": data[r["term"]][v]} for v in vs if v in data.get(r["term"], {})]
            if got:
                terms.append({"term": r["term"], "variants": got})

    out = {"decades": sorted(decades), "terms": terms}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(terms)} terms, {sum(len(t['variants']) for t in terms)} variants")


if __name__ == "__main__":
    main()
