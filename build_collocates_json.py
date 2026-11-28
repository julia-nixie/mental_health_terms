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

# Decade from which each diagnostic term was in use, so the page can hide earlier decades, where
# hits come from books Google has misdated. First matching substring of the variant wins; variants
# that match nothing (old everyday words such as "idiot", "on the spectrum") keep every decade.
FIRST_USE = [
    ("autism spectrum", 1980, "Wing, 1980s"), ("autistic spectrum", 1980, "Wing, 1980s"),
    ("early infantile autism", 1940, "Kanner, 1943"), ("kanner", 1940, "Kanner, 1943"),
    ("autistic psychopathy", 1940, "Asperger, 1944"),
    ("pdd", 1980, "DSM-III, 1980"), ("pervasive developmental", 1980, "DSM-III, 1980"),
    ("autistic disorder", 1980, "DSM-III, 1980"),
    ("infantile autism", 1940, "Kanner, 1943"), ("childhood autism", 1940, "after Kanner, 1943"),
    ("atypical autism", 1940, "after Kanner, 1943"),
    ("autis", 1910, "Bleuler, 1911"), ("asperger", 1940, "Asperger, 1944"),
    ("dementia praecox", 1890, "Kraepelin, 1893"), ("dementia infantilis", 1900, "Heller, 1908"),
    ("heller", 1900, "Heller, 1908"), ("childhood disintegrative", 1990, "ICD-10 and DSM-IV, 1990s"),
    ("schizophrenic reaction", 1950, "DSM-I, 1952"), ("schizophrenia childhood", 1950, "DSM-I, 1952"),
    ("childhood schizophrenia", 1930, "1930s"), ("symbiotic", 1950, "Mahler, 1952"),
    ("minimal brain", 1940, "1940s"), ("minimal cerebral", 1940, "1940s"),
    ("rett", 1960, "Rett, 1966"), ("refrigerator mother", 1940, "Kanner, 1949"),
    ("developmental disab", 1960, "1960s"), ("developmentally disabled", 1960, "1960s"),
    ("learning disab", 1960, "Kirk, 1963"),
]


def first_use(variant):
    v = variant.lower()
    for key, decade, src in FIRST_USE:
        if key in v:
            return decade, src
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "autism_collocates.csv"))
    ap.add_argument("--terms", default=str(HERE / "autism_terms.csv"))
    ap.add_argument("--out", default=str(HERE / "docs" / "autism" / "collocates.json"))
    args = ap.parse_args()

    data, decades = {}, set()
    with open(args.csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if len(r["collocate"]) < 2 and not r["pattern"].endswith("any"):
                continue        # one-letter "nouns"/"adjectives": initials, "J" of journal names
            d = int(r["decade"])
            decades.add(d)
            cell = data.setdefault(r["term"], {}).setdefault(r["variant"], {}) \
                       .setdefault(r["pattern"], {}).setdefault(str(d), [])
            cell.append([r["collocate"], round(float(r["share"]), 5), int(r["rank"])])

    terms = []
    with open(args.terms, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vs = [v.strip() for v in r["variants"].split(";") if v.strip()]
            got = []
            for v in vs:
                if v in data.get(r["term"], {}):
                    d, src = first_use(v)
                    got.append({"v": v, "p": data[r["term"]][v], **({"from": d, "src": src} if d else {})})
            if got:
                terms.append({"term": r["term"], "variants": got})

    out = {"decades": sorted(decades), "terms": terms}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(terms)} terms, {sum(len(t['variants']) for t in terms)} variants")


if __name__ == "__main__":
    main()
