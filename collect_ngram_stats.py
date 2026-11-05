#!/usr/bin/env python3
"""Collect Google Books Ngram frequencies for the terms in mental_disorders.csv.

Each term is treated as the union of its variants (the ';'-separated 'variants'
column): per year, the term's frequency is the SUM of its variants' frequencies
(Ngram frequencies are shares of all n-grams of that length, so they add up).

Output: mental_disorders_stats.csv (long format)
    term, category, year, frequency, has_data   (has_data=false: no variant returned any data)
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


def to_query(variant: str, keep_punctuation: bool = True) -> str:
    """Convert a variant into Ngram Viewer syntax.

    Default (symbol-free): everything that is not a letter or digit becomes a
    space, so 'attention-deficit/hyperactivity disorder' is searched as
    'attention deficit hyperactivity disorder'. A trailing parenthetical
    ('... disorder (adhd)') is dropped (abbreviations are separate variants),
    a mid-phrase one keeps its words, and "'s" is removed
    ("alzheimer's disease" -> "alzheimer disease").
    This matches only text written without the symbols, but avoids the
    viewer counting '-', '/', '(' ... as extra tokens and as operators.

    keep_punctuation=True pads each of - / ( ) , with spaces instead, which is
    how the viewer tokenizes them (each then counts as a token).
    """
    v = variant.strip().lower()
    if keep_punctuation:
        v = v.replace("\u2019", "'")
        v = re.sub(r"(\w)'s\b", r"\1 's", v)   # the form the API itself echoes back
        v = re.sub(r"([/()\-,])", r" \1 ", v)
    else:
        v = re.sub(r"\s*\([^()]*\)\s*$", "", v)
        v = re.sub(r"['\u2019]s\b", "", v)
        v = re.sub(r"[^\w]+|_", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def spellings(q: str):
    """Alternative spellings of one query, in the order they are tried.

    The JSON endpoint is inconsistent: 'Newcomb - Benford' can return [] while
    'Newcomb-Benford' returns data (and the other way round for other terms).
    So the as-written (spaced) form is tried first, then ' - ' collapsed to
    '-', then " 's" collapsed to "'s", then both. Same tokens in every case.
    """
    hy = q.replace(" - ", "-")
    ap = q.replace(" 's", "'s")
    both = hy.replace(" 's", "'s")
    out = []
    for x in (q, hy, ap, both):
        if x not in out:
            out.append(x)
    return out


# Short single words that look like abbreviations to the rule below but are ordinary words.
NOT_ACRONYMS = {"pica", "mania", "jetlag"}

# Abbreviations whose capitals are also everyday words or abbreviations ("ED" = "Ed."/edition,
# "AN", "SAD", "DID", "HAND", ...). They show a large background in books long before the
# diagnoses existed (mean 1900-1949 comparable to 2000-2022) and swamp the real term, so they
# are skipped unless --keep-ambiguous is given.
AMBIGUOUS_ABBREVIATIONS = {"hand", "bed", "an", "ed", "odd", "ts", "sad", "bn", "did"}


def is_acronym(variant: str, all_variants) -> bool:
    """A short single-word variant of a term that also has a spelled-out variant
    (e.g. 'sad' for 'social anxiety disorder') is an abbreviation. Those are
    searched UPPER-CASE and case-sensitively, so 'SAD' does not also count the
    ordinary word 'sad'. Single-variant terms (e.g. 'pica') are real words."""
    v = variant.strip()
    return (v.lower() not in NOT_ACRONYMS and " " not in v and len(v) <= 6
            and any(" " in o.strip() for o in all_variants))


EDGE_WORDS = {"of", "the", "to", "and", "or", "in", "with", "a", "an", "by", "for", "on", "at", "from", "due"}


def candidate_windows(query: str):
    """All 2- to 5-token windows of an over-long query that can be searched on their own.

    Commas and parenthetical groups (an abbreviation, a list of examples) are barriers
    that no window may cross, and a window never starts or ends on '-' or '/'.
    Returns lower-case strings; the caller picks the most distinctive one.
    """
    toks = query.lower().split()
    segments, cur, depth = [], [], 0
    for t in toks:
        if t == "(":
            depth += 1
        if depth == 0 and t != ",":
            cur.append(t)
        elif depth == 0 or t == ",":
            if cur:
                segments.append(cur)
            cur = []
        if t == ")" and depth:
            depth -= 1
            if depth == 0 and cur:
                segments.append(cur)
                cur = []
    if cur:
        segments.append(cur)
    out = []
    for seg in segments:
        for n in range(min(MAX_NGRAM, len(seg)), 1, -1):
            for i in range(len(seg) - n + 1):
                w = seg[i:i + n]
                if w[0] in ("-", "/") or w[-1] in ("-", "/"):
                    continue
                if len([x for x in w if x not in ("-", "/")]) < 2:
                    continue
                out.append(" ".join(w))
    return list(dict.fromkeys(out))


def token_count(query: str) -> int:
    return len(query.split())


def load_terms(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            variants = [v.strip() for v in row["variants"].split(";") if v.strip()]
            yield row["term"], row["category"], variants


def fetch_batch(queries, corpus, start, end, smoothing, case_insensitive, retries=5):
    # Commas inside a phrase would split it, so they are stripped here.
    # Square brackets make the viewer treat the text literally. Without them
    # '/' is division, '-' subtraction and '(' ')' grouping, so e.g.
    # 'attention deficit/hyperactivity disorder' comes back as a ratio.
    content = ",".join("[" + q.replace(",", "") + "]" for q in queries)
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
    ap.add_argument("--strip-punctuation", action="store_true",
                    help="replace hyphens, slashes, parentheses and commas with spaces "
                         "(fits more variants in 5 tokens, but then only matches text "
                         "written WITHOUT those symbols)")
    ap.add_argument("--keep-ambiguous", action="store_true",
                    help="also search abbreviations that are everyday words (ED, AN, SAD, DID, ...)")
    ap.add_argument("--keep-overlaps", action="store_true",
                    help="do not subtract occurrences inside longer phrases (double counting)")
    ap.add_argument("--no-truncate", action="store_true",
                    help="drop variants over 5 tokens instead of cutting them to 5")
    args = ap.parse_args()
    args.keep_punctuation = not args.strip_punctuation
    ci = not args.case_sensitive

    terms = list(load_terms(Path(args.input)))
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    cache_key = lambda q, c: f"v2|{args.corpus}|{args.start}|{args.end}|{args.smoothing}|{int(c)}|{q}"

    def plan(v, variants):
        """-> (query, case_insensitive) for one variant."""
        q = to_query(v, args.keep_punctuation)
        if not args.no_acronym_case and is_acronym(v, variants):
            return q.upper(), False
        return q, ci

    # ---- Decide which (query, case-mode) pairs make up each term -------------
    # 1. variants of <= 5 tokens are used as they are;
    # 2. longer ones are replaced by a window of at most 5 tokens that is DISJOINT from every other
    #    term: it occurs in no phrase of any other term (neither as a whole variant nor inside a
    #    longer one). Among such windows the longest is used, so it stays as close to the full
    #    phrase as possible (ties: not starting or ending on a function word such as "of" or
    #    "due", then the rarest words). If a term has no such window the variant is dropped.
    full = {}                                   # lower-case query -> set of term indexes
    phrases = []                                # (term index, token list) of every variant
    for i, (_, _, variants) in enumerate(terms):
        for v in variants:
            q, _ = plan(v, variants)
            if token_count(q) <= MAX_NGRAM:
                full.setdefault(q.lower(), set()).add(i)
            phrases.append((i, to_query(v, args.keep_punctuation).lower().split()))
    df = {}
    for _, toks_ in phrases:
        for t in set(toks_):
            df[t] = df.get(t, 0) + 1

    owners_cache = {}

    def owners(window):
        """Indexes of all terms having a phrase that contains this window."""
        if window not in owners_cache:
            w = window.split()
            n = len(w)
            owners_cache[window] = {i for i, toks_ in phrases
                                    if any(toks_[k:k + n] == w for k in range(len(toks_) - n + 1))}
        return owners_cache[window]

    def rank(window):
        ws = window.split()
        edge = 1 if (ws[0] in EDGE_WORDS or ws[-1] in EDGE_WORDS) else 0
        rare = sum(1.0 / df.get(x, 1) for x in ws if x not in ("-", "/"))
        return (-len(ws), edge, -rare)

    claimed = {}                                # window -> term index that owns it
    choice = {}                                 # (term index, variant) -> window or None
    reason = {}
    for i, (_, _, variants) in enumerate(terms):
        for v in variants:
            q, c = plan(v, variants)
            if token_count(q) <= MAX_NGRAM or args.no_truncate:
                continue
            ranked = sorted(candidate_windows(to_query(v, args.keep_punctuation)), key=rank)
            pick = None
            for w in ranked:
                if owners(w) - {i}:
                    continue                    # also occurs in another term's phrases
                if claimed.get(w, i) != i:
                    continue
                if w in full:
                    reason[(i, v)] = f"window '{w}' duplicates a variant of the same term"
                    continue
                pick = w
                break
            if pick is None and terms[i][1] == "n/a":
                # A parent term's words always occur inside its children's phrases, so no window can
                # be disjoint; use the least shared one (the overlap is subtracted afterwards).
                for w in sorted(ranked, key=lambda w: (len(owners(w) - {i}),) + rank(w)):
                    if claimed.get(w, i) == i and w not in full and len(w.split()) >= 3:
                        pick = w
                        break
            if pick is not None:
                claimed[pick] = i
            elif (i, v) not in reason:
                reason[(i, v)] = "no distinctive window that no other term uses"
            choice[(i, v)] = pick

    final, truncated, dropped = [], [], []      # final[i] = [(query, ci), ...]
    for i, (_, _, variants) in enumerate(terms):
        used = []
        for v in variants:
            if (not args.keep_ambiguous and not args.no_acronym_case
                    and v.strip().lower() in AMBIGUOUS_ABBREVIATIONS and is_acronym(v, variants)):
                dropped.append((v, "abbreviation that is also an everyday word or abbreviation"))
                continue
            q, c = plan(v, variants)
            if token_count(q) <= MAX_NGRAM:
                if (q, c) not in used:
                    used.append((q, c))
                continue
            if args.no_truncate:
                dropped.append((v, "too long"))
                continue
            t = choice.get((i, v))
            if t is None:
                dropped.append((v, reason.get((i, v), "no usable window")))
            elif (t, ci) in used:
                dropped.append((v, f"window '{t}' duplicates another variant of the same term"))
            else:
                used.append((t, ci))
                truncated.append((v, t))
        final.append(used)

    pairs = []                                  # every (query, case_insensitive) in use
    for used in final:
        for pr in used:
            if pr not in pairs:
                pairs.append(pr)

    def resolve(q, c):
        """First non-empty answer over the spellings, or None if all are empty."""
        for sp in spellings(q):
            series = cache.get(cache_key(sp, c))
            if series:
                return series
        return None

    n_spell = max(len(spellings(q)) for q, _ in pairs)
    for level in range(n_spell):                # round k: try the k-th spelling where still needed
        todo = {True: [], False: []}            # keyed by case_insensitive
        for q, c in pairs:
            sps = spellings(q)
            if level >= len(sps) or any(cache.get(cache_key(x, c)) for x in sps[:level]):
                continue                        # no such spelling, or already answered
            if cache_key(sps[level], c) not in cache and sps[level] not in todo[c]:
                todo[c].append(sps[level])
        n = len(todo[True]) + len(todo[False])
        print(f"spelling {level + 1}: {n} queries to fetch"
              + (f"; {len(truncated)} long variants cut to {MAX_NGRAM} tokens, "
                 f"{len(dropped)} dropped" if level == 0 else ""), file=sys.stderr)
        done = 0
        for c, queries in todo.items():   # case mode is per request, so batch separately
            for i in range(0, len(queries), BATCH):
                batch = queries[i:i + BATCH]
                data = fetch_batch(batch, args.corpus, args.start, args.end, args.smoothing, c)
                for q, series in match_results(batch, data, c).items():
                    cache[cache_key(q, c)] = series  # None => empty for this spelling
                cache_path.write_text(json.dumps(cache))
                done += len(batch)
                print(f"  fetched {done}/{n}", file=sys.stderr)
                time.sleep(PAUSE)

    absent = [q for q, c in pairs if resolve(q, c) is None]

    # ---- remove double counting between phrases ---------------------------------
    # The viewer counts every occurrence of a phrase, including inside longer phrases:
    # "anxiety disorder" also counts the end of "social anxiety disorder". For each phrase
    # we subtract the occurrences of the shortest longer phrases that contain it (this covers
    # phrases of different terms and variants of the same term). Containers of containers are
    # not subtracted again, since the nearest container already includes them.
    def toks(q):
        return q.lower().split()

    def contains(big, small):
        n = len(small)
        return len(big) > n and any(big[i:i + n] == small for i in range(len(big) - n + 1))

    raw = {pr: resolve(*pr) for pr in pairs}
    ptoks = {pr: toks(pr[0]) for pr in pairs}
    exclusive, reduced = {}, []
    for a in pairs:
        if not raw[a]:
            continue
        cont = [b for b in pairs if b != a and raw[b] and contains(ptoks[b], ptoks[a])]
        nearest = [b for b in cont if not any(o != b and contains(ptoks[b], ptoks[o]) for o in cont)]
        if args.keep_overlaps or not nearest:
            exclusive[a] = raw[a]
            continue
        ex = list(raw[a])
        for b in nearest:
            for k, x in enumerate(raw[b][:len(ex)]):
                ex[k] -= x
        exclusive[a] = [max(0.0, x) for x in ex]
        before, after = sum(raw[a]), sum(exclusive[a])
        reduced.append((before - after, a[0], [b[0] for b in nearest], before, after))

    years = list(range(args.start, args.end + 1))
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["term", "category", "year", "frequency", "has_data"])
        for (term, category, _), used in zip(terms, final):
            total = [0.0] * len(years)
            has_data = False   # True if any variant returned data under some spelling
            for q, c in used:
                series = exclusive.get((q, c))
                if series:
                    has_data = has_data or sum(series) > 0
                    for k, x in enumerate(series[:len(years)]):
                        total[k] += x
            for y, x in zip(years, total):
                w.writerow([term, category, y, f"{x:.6e}", str(has_data).lower()])

    if reduced:
        print(f"Overlap removed from {len(reduced)} phrases (largest first):", file=sys.stderr)
        for lost, q, bs, before, after in sorted(reduced, reverse=True)[:25]:
            print(f"  - {q!r}: {before / max(before, 1e-30):.0%} -> {after / before:.0%} "
                  f"(inside {', '.join(repr(b) for b in bs[:3])}{'…' if len(bs) > 3 else ''})", file=sys.stderr)
    if absent:
        print(f"No data under any spelling ({len(absent)}):", file=sys.stderr)
        for q in absent:
            print("  -", q, file=sys.stderr)
    if truncated:
        print("Cut to 5 tokens:", file=sys.stderr)
        for v, t in truncated:
            print(f"  - {v!r} -> {t!r}", file=sys.stderr)
    if dropped:
        print("Dropped:", file=sys.stderr)
        for v, why in dropped:
            print(f"  - {v!r}: {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
