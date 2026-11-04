#!/usr/bin/env python3
"""Collect Google Books Ngram frequencies for the terms in mental_disorders.csv.

Each term is treated as the union of its variants (the ';'-separated 'variants'
column): per year, the term's frequency is the SUM of its variants' frequencies
(Ngram frequencies are shares of all n-grams of that length, so they add up).

Output: mental_disorders_stats.csv (long format)
    term, category, year, frequency
plus a per-variant cache (ngram_cache.json) so re-runs only fetch what is missing.

Usage:
    python3 collect_ngram_stats.py [--corpus en] [--start 1800] [--end 2022]
                                   [--smoothing 0] [--case-sensitive]
"""
import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://books.google.com/ngrams/json"
MAX_NGRAM = 5          # Ngram Viewer supports 1- to 5-grams
BATCH = 10             # phrases per request (viewer allows ~12 comma-separated)
PAUSE = 1.5            # seconds between requests (be polite / avoid 429)


def to_query(variant: str) -> str:
    """Convert a variant into Ngram Viewer syntax.

    The viewer tokenizes punctuation as separate tokens ("post-traumatic" ->
    "post - traumatic"), and '-', '/', '+', '(' ')' etc. are query operators,
    so each punctuation mark is padded with spaces and the phrase is later
    wrapped in no extra quoting. Commas separate queries, so they are dropped
    from the phrase (and counted as a token by the viewer: we keep them as
    token for length checking only).
    """
    v = variant.strip().lower()
    v = re.sub(r"([/()\-,])", r" \1 ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def is_acronym(variant: str, all_variants) -> bool:
    """A short single-word variant of a term that also has a spelled-out variant
    (e.g. 'sad' for 'social anxiety disorder') is an abbreviation. Those are
    searched UPPER-CASE and case-sensitively, so 'SAD' does not also count the
    ordinary word 'sad'. Single-variant terms (e.g. 'pica') are real words."""
    v = variant.strip()
    return (" " not in v and len(v) <= 6
            and any(" " in o.strip() for o in all_variants))


def token_count(query: str) -> int:
    return len(query.split())


def load_terms(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            variants = [v.strip() for v in row["variants"].split(";") if v.strip()]
            yield row["term"], row["category"], variants


def fetch_batch(queries, corpus, start, end, smoothing, case_insensitive, retries=5):
    # Commas inside a phrase would split it, so they are stripped here.
    content = ",".join(q.replace(",", "") for q in queries)
    params = {
        "content": content,
        "year_start": start,
        "year_end": end,
        "corpus": corpus,
        "smoothing": smoothing,
        "case_insensitive": "true" if case_insensitive else "false",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "mental-health-terms-ngram/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(5 * 2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(5 * 2 ** attempt)
                continue
            raise


def match_results(queries, results, case_insensitive):
    """Map each query to its timeseries (or None when the viewer returned nothing).

    The viewer drops phrases it has no data for, and in case-insensitive mode
    labels results like "panic disorder (All)", so match on normalised names.
    """
    def norm(s):
        s = re.sub(r"\s*\(all\)$", "", s, flags=re.I) if case_insensitive else s
        return re.sub(r"\s+", "", s.lower())

    by_name = {}
    for r in results:
        if r.get("type") in ("NGRAM", "CASE_INSENSITIVE", None):
            by_name[norm(r["ngram"])] = r["timeseries"]
    return {q: by_name.get(norm(q.replace(",", ""))) for q in queries}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(HERE / "mental_disorders.csv"))
    ap.add_argument("--output", default=str(HERE / "mental_disorders_stats.csv"))
    ap.add_argument("--cache", default=str(HERE / "ngram_cache.json"))
    ap.add_argument("--corpus", default="en", help="Ngram corpus id (default: en, latest English)")
    ap.add_argument("--start", type=int, default=1800)
    ap.add_argument("--end", type=int, default=2022)
    ap.add_argument("--smoothing", type=int, default=0)
    ap.add_argument("--case-sensitive", action="store_true",
                    help="match every variant case-sensitively (lower-case)")
    ap.add_argument("--no-acronym-case", action="store_true",
                    help="do not search abbreviations upper-case/case-sensitive")
    args = ap.parse_args()
    ci = not args.case_sensitive

    terms = list(load_terms(Path(args.input)))
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    cache_key = lambda q, c: f"{args.corpus}|{args.start}|{args.end}|{args.smoothing}|{int(c)}|{q}"

    def plan(v, variants):
        """-> (query, case_insensitive) for one variant."""
        q = to_query(v)
        if not args.no_acronym_case and is_acronym(v, variants):
            return q.upper(), False
        return q, ci

    # Collect every distinct query still to fetch; skip those longer than 5 tokens.
    skipped, todo = set(), {True: [], False: []}   # keyed by case_insensitive
    for _, _, variants in terms:
        for v in variants:
            q, c = plan(v, variants)
            if token_count(q) > MAX_NGRAM:
                skipped.add(v)
            elif cache_key(q, c) not in cache and q not in todo[c]:
                todo[c].append(q)

    n = len(todo[True]) + len(todo[False])
    print(f"{n} queries to fetch, {len(skipped)} variants skipped (> {MAX_NGRAM} tokens)",
          file=sys.stderr)
    done = 0
    for c, queries in todo.items():   # case mode is per request, so batch separately
        for i in range(0, len(queries), BATCH):
            batch = queries[i:i + BATCH]
            data = fetch_batch(batch, args.corpus, args.start, args.end, args.smoothing, c)
            for q, series in match_results(batch, data, c).items():
                cache[cache_key(q, c)] = series  # None => no data in corpus
            cache_path.write_text(json.dumps(cache))
            done += len(batch)
            print(f"  fetched {done}/{n}", file=sys.stderr)
            time.sleep(PAUSE)

    years = list(range(args.start, args.end + 1))
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["term", "category", "year", "frequency"])
        for term, category, variants in terms:
            total = [0.0] * len(years)
            for v in variants:
                series = cache.get(cache_key(*plan(v, variants)))
                if series:
                    for k, x in enumerate(series[:len(years)]):
                        total[k] += x
            for y, x in zip(years, total):
                w.writerow([term, category, y, f"{x:.6e}"])

    if skipped:
        print("Skipped (too long for Ngram Viewer):", file=sys.stderr)
        for v in sorted(skipped):
            print("  -", v, file=sys.stderr)


if __name__ == "__main__":
    main()
