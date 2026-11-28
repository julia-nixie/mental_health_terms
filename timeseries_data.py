#!/usr/bin/env python3
"""Yearly series of the autism terms in two corpora, ready for time-series work:
  * the psychology-journal abstracts (counts and word totals per year), and
  * Google Books (Ngram Viewer, English), as phrases per million.

    python3 timeseries_data.py                 # writes autism_timeseries.csv
    python3 timeseries_data.py --out my.csv

In Python:

    from timeseries_data import load, wide
    df = load("autism_timeseries.csv")
    g = wide(df, level="group", column="google_per_million")      # one column per group, index = year
    c = wide(df, level="group", column="corpus_per_million")

Columns of the CSV (one row per unit and year; Google years 1800-2022, corpus years 1930-2019):
  level               group | era | variant
  group               the group (term) the unit belongs to
  era                 for level=era the era; for level=variant the era of the variant, if it has one
  variant             the variant (level=variant only)
  year
  corpus_count        hits in the corpus with phrases inside longer phrases taken out (so text is
                      counted once); empty where the corpus has no abstracts that year
  corpus_tokens       all words/tokens of the corpus that year (the same for every unit)
  corpus_per_million  corpus_count / corpus_tokens * 1e6
  google_per_million  Google Books frequency x 1e6, overlap-removed in the same way

A group is the sum of its variants and an era the sum of the variants tagged with it, so
pooled rates over several years are sum(corpus_count) / sum(corpus_tokens) * 1e6.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def build(ngram_path, corpus_path):
    ng = json.load(open(ngram_path))
    co = json.load(open(corpus_path))
    years = np.arange(min(ng["y0"], co["y0"]), max(ng["y1"], co["y1"]) + 1)
    n = len(years)

    def on_grid(arr, y0, fill=np.nan):
        out = np.full(n, fill, dtype=float)
        a = np.asarray(arr, dtype=float)
        out[y0 - years[0]: y0 - years[0] + len(a)] = a
        return out

    tokens = on_grid(co["tokens"], co["y0"], fill=np.nan)
    tokens[tokens == 0] = np.nan                                    # no abstracts that year
    google = {(t["c"], t["n"]): on_grid(t["v"], ng["y0"], fill=0.0) for t in ng["terms"]}

    # units: (level, group, era, variant, corpus counts, google per million)
    units = []
    by_group = {}
    for v in co["variants"]:
        by_group.setdefault(v["g"], []).append(v)
    for g, vs in by_group.items():
        def sums(sel):
            c = np.sum([on_grid(v["c"], co["y0"], fill=0.0) for v in sel], axis=0)
            gg = np.sum([google.get((v["g"], v["n"]), np.zeros(n)) for v in sel], axis=0)
            return c, gg
        c, gg = sums(vs)
        units.append(("group", g, "", "", c, gg))
        for e in dict.fromkeys(v["e"] for v in vs if v.get("e")):
            c, gg = sums([v for v in vs if v.get("e") == e])
            units.append(("era", g, e, "", c, gg))
        for v in vs:
            c, gg = sums([v])
            units.append(("variant", g, v.get("e", ""), v["n"], c, gg))

    frames = []
    for level, g, e, v, c, gg in units:
        in_corpus = ~np.isnan(tokens)
        frames.append(pd.DataFrame({
            "level": level, "group": g, "era": e, "variant": v, "year": years,
            "corpus_count": np.where(in_corpus, c, np.nan),
            "corpus_tokens": tokens,
            "corpus_per_million": np.where(in_corpus, c / np.where(in_corpus, tokens, 1) * 1e6, np.nan),
            "google_per_million": gg,
        }))
    return pd.concat(frames, ignore_index=True)


def load(path=HERE / "autism_timeseries.csv"):
    """The table as written by build(), with sensible dtypes."""
    df = pd.read_csv(path, keep_default_na=True)
    for col in ("era", "variant"):
        df[col] = df[col].fillna("")
    return df


def wide(df, level="group", column="google_per_million"):
    """One column per unit (group, era or variant), one row per year."""
    d = df[df["level"] == level].copy()
    d["unit"] = d["variant"].where(d["variant"] != "", d["era"].where(d["era"] != "", d["group"]))
    if level == "variant":
        d["unit"] = d["group"] + ": " + d["variant"]
    elif level == "era":
        d["unit"] = d["group"] + ": " + d["era"]
    return d.pivot(index="year", columns="unit", values=column)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ngram", default=str(HERE / "docs/autism/data.json"))
    ap.add_argument("--corpus", default=str(HERE / "docs/autism/corpus.json"))
    ap.add_argument("--out", default=str(HERE / "autism_timeseries.csv"))
    args = ap.parse_args()
    df = build(args.ngram, args.corpus)
    for col in ("corpus_count", "corpus_tokens"):
        df[col] = df[col].round().astype("Int64")                     # exact integers, empty where missing
    df.to_csv(args.out, index=False, float_format="%.6g")
    print(f"{len(df):,} rows ({df['level'].value_counts().to_dict()}), "
          f"years {df['year'].min()}-{df['year'].max()} -> {args.out}")


if __name__ == "__main__":
    main()
