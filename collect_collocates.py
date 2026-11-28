#!/usr/bin/env python3
"""Collect Google Books Ngram collocates of the terms in a terms CSV (default: autism_terms.csv).

For every variant and every decade, six wildcard queries are run, each returning the viewer's
top 10 completions for that decade:
    right_any   "<variant> *"          next word (any)
    left_any    "* <variant>"          previous word (any)
    right_noun  "<variant> *_NOUN"     next word, tagged as a noun   ("autistic children")
    left_noun   "*_NOUN <variant>"     previous word, tagged noun    ("childhood autism")
    right_adj   "<variant> *_ADJ"      next word, tagged adjective
    left_adj    "*_ADJ <variant>"      previous word, tagged adjective ("infantile autism")
and eight bridged ones, where the wildcard is one fixed word away (one wildcard per query is all
the viewer allows), queried only in decades where the bridge phrase itself occurs:
    left_with_any "* with <variant>"   left_with_noun "*_NOUN with <variant>"  ("children with autism")
    left_of_any   "* of <variant>"     left_of_noun   "*_NOUN of <variant>"    ("diagnosis of autism")
    left_from_any "* from <variant>"   left_to_any    "* to <variant>"
    right_in_noun "<variant> in *_NOUN"   right_and_noun "<variant> and *_NOUN"
Bridged patterns need the variant to have at most 3 tokens.

Wildcard queries are always case-sensitive, so each variant is searched in two casings, lower case
and first letter capitalised ("asperger" / "Asperger", "autism is" / "Autism is"). The completions
are lower-cased and summed over both casings and over the years of the decade, and the top 10 is
taken again from those sums. A collocate in one casing's top 10 but not the other's therefore
counts only the casing in which it made the list, so the sums are slight underestimates.

The share column divides a collocate's frequency by the frequency of the variant itself in the same
decade (both casings), i.e. the share of the variant's occurrences that have that word next to it.

Decades in which a variant has no data are skipped; variants of 5 tokens cannot take a wildcard and
are skipped too.

Output: autism_collocates.csv
    term, variant, pattern, decade, rank, collocate, frequency, share
plus a cache (collocate_cache.json) so re-runs only fetch what is missing.

Usage: python3 collect_collocates.py [--terms autism_terms.csv] [--out autism_collocates.csv]
"""
import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from collect_ngram_stats import to_query, spellings

HERE = Path(__file__).resolve().parent
API = "https://books.google.com/ngrams/json"
BATCH = 10             # plain phrases per request
WILD_BATCH = 50        # wildcard queries per request (tested up to 60)
PAUSE = 1.5
START, END = 1800, 2022
PATTERNS = {
    "right_any": "{} *", "left_any": "* {}",
    "right_noun": "{} *_NOUN", "left_noun": "*_NOUN {}",
    "right_adj": "{} *_ADJ", "left_adj": "*_ADJ {}",
}
# Bridged patterns: the wildcard sits one fixed word away from the variant (the viewer allows only one
# wildcard per query). Each is queried only in decades where its bridge phrase ("with autism") occurs.
BRIDGED = {
    "left_with_any": ("* with {}", "with {}"), "left_with_noun": ("*_NOUN with {}", "with {}"),
    "left_of_any": ("* of {}", "of {}"), "left_of_noun": ("*_NOUN of {}", "of {}"),
    "left_from_any": ("* from {}", "from {}"), "left_to_any": ("* to {}", "to {}"),
    "right_in_noun": ("{} in *_NOUN", "{} in"), "right_and_noun": ("{} and *_NOUN", "{} and"),
}
GATE_BATCH = 50


def decades():
    """Decades 1800-1809 ... 2000-2009; the last one, 2010-2022, takes the remaining years."""
    return [(d, END if d == 2010 else d + 9) for d in range(START, 2011, 10)]


def fetch(content, start, end, retries=6):
    params = {"content": content, "year_start": start, "year_end": end,
              "corpus": "en", "smoothing": 0, "case_insensitive": "false"}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "mental-health-terms-ngram/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if attempt == retries - 1 or (isinstance(e, urllib.error.HTTPError)
                                          and e.code not in (429, 500, 502, 503)):
                raise
            time.sleep(5 * 2 ** attempt)


def capitalise(q):
    return q[:1].upper() + q[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default=str(HERE / "autism_terms.csv"))
    ap.add_argument("--out", default=str(HERE / "autism_collocates.csv"))
    ap.add_argument("--cache", default=str(HERE / "collocate_cache.json"))
    args = ap.parse_args()

    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    def save():
        cache_path.write_text(json.dumps(cache))

    with open(args.terms, newline="", encoding="utf-8") as f:
        terms = [(r["term"], [v.strip() for v in r["variants"].split(";") if v.strip()])
                 for r in csv.DictReader(f)]

    # ---- 1. plain yearly series of each variant in both casings; pick the spelling with data ----
    forms = {}                                   # (term, variant) -> [query in each casing]
    for term, variants in terms:
        for v in variants:
            q = to_query(v)
            if len(q.split()) >= 5:
                print(f"skip (5 tokens, no room for a wildcard): {v}", file=sys.stderr)
                continue
            forms[(term, v)] = [q, capitalise(q)]
    plain_todo = sorted({sp for qs in forms.values() for q in qs for sp in spellings(q)}
                        - {k[6:] for k in cache if k.startswith("plain|")})
    for i in range(0, len(plain_todo), BATCH):
        batch = plain_todo[i:i + BATCH]
        data = fetch(",".join("[" + q + "]" for q in batch), START, END)
        got = {r["ngram"]: r["timeseries"] for r in data}
        for q in batch:
            cache["plain|" + q] = got.get(q)
        save()
        print(f"plain series {i + len(batch)}/{len(plain_todo)}", file=sys.stderr)
        time.sleep(PAUSE)

    def series(q):
        """(spelling, yearly series) of the first spelling with data, else (q, None)."""
        for sp in spellings(q):
            s = cache.get("plain|" + sp)
            if s and any(s):
                return sp, s
        return q, None

    # ---- 1b. yearly series of each bridge phrase, for the forms that have data and room for it ----
    gates = sorted({g.format(sp) for qs in forms.values() for q in qs for sp, s in [series(q)]
                    if s and len(sp.split()) <= 3 for _, g in BRIDGED.values()}
                   - {k[5:] for k in cache if k.startswith("gate|")})
    for i in range(0, len(gates), GATE_BATCH):
        batch = gates[i:i + GATE_BATCH]
        data = fetch(",".join("[" + q + "]" for q in batch), START, END)
        got = {r["ngram"].replace(" ", ""): r["timeseries"] for r in data}
        for q in batch:
            cache["gate|" + q] = got.get(q.replace(" ", ""))
        save()
        print(f"bridge phrases {i + len(batch)}/{len(gates)}", file=sys.stderr)
        time.sleep(PAUSE)

    # ---- 2. wildcard queries per (form, decade) where the form (or bridge phrase) has data ----
    jobs = []                                    # (decade, pattern, query)
    for qs in forms.values():
        for q in qs:
            sp, s = series(q)
            if not s:
                continue
            for d0, d1 in decades():
                if any(s[d0 - START:d1 - START + 1]):
                    for p in PATTERNS.values():
                        jobs.append((d0, d1, p.format(sp)))
                    if len(sp.split()) > 3:
                        continue
                    for p, g in BRIDGED.values():
                        gs = cache.get("gate|" + g.format(sp))
                        if gs and any(gs[d0 - START:d1 - START + 1]):
                            jobs.append((d0, d1, p.format(sp)))
    todo = [j for j in dict.fromkeys(jobs) if f"wild|{j[0]}|{j[2]}" not in cache]
    print(f"{len(jobs)} wildcard queries, {len(todo)} to fetch", file=sys.stderr)
    by_decade = {}
    for j in todo:
        by_decade.setdefault((j[0], j[1]), []).append(j[2])
    done = 0
    for (d0, d1), queries in sorted(by_decade.items()):
        for i in range(0, len(queries), WILD_BATCH):
            batch = queries[i:i + WILD_BATCH]
            data = fetch(",".join(batch), d0, d1)
            res = {q: [] for q in batch}
            key = {q.replace(" ", ""): q for q in batch}   # the viewer may re-space "'s" or "-"
            for r in data:
                q = key.get((r.get("parent") or "").replace(" ", ""))
                if r.get("type") == "EXPANSION" and q:
                    res[q].append([r["ngram"], sum(r["timeseries"])])
            for q in batch:
                cache[f"wild|{d0}|{q}"] = res[q]
            save()
            done += len(batch)
            print(f"  wildcard {done}/{len(todo)}", file=sys.stderr)
            time.sleep(PAUSE)

    # ---- 3. merge casings, rank, write ----
    rows = []
    for (term, v), qs in forms.items():
        found = [series(q) for q in qs]
        for d0, d1 in decades():
            base = sum(sum(s[d0 - START:d1 - START + 1]) for _, s in found if s)
            if not base:
                continue
            for name, p in list(PATTERNS.items()) + [(n, pg[0]) for n, pg in BRIDGED.items()]:
                tot = {}
                for sp, s in found:
                    if not s:
                        continue
                    for ngram, freq in cache.get(f"wild|{d0}|{p.format(sp)}") or []:
                        toks = ngram.split()
                        w = toks[-1] if name.startswith("right") else toks[0]
                        w = w.rsplit("_", 1)[0].lower() if "_" in w else w.lower()
                        if not w:
                            continue
                        tot[w] = tot.get(w, 0.0) + freq
                top = sorted(tot.items(), key=lambda x: -x[1])[:10]
                for rank, (w, freq) in enumerate(top, 1):
                    rows.append([term, v, name, d0, rank, w, f"{freq:.6e}", f"{freq / base:.6f}"])

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["term", "variant", "pattern", "decade", "rank", "collocate", "frequency", "share"])
        wr.writerows(rows)
    print(f"wrote {len(rows)} rows to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
