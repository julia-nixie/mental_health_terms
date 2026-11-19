#!/usr/bin/env python3
"""Count how often each phrase in corpus_queries.csv occurs in a large delimited text file, per year.

Runs on your own machine over the whole file without loading it into memory (standard library only,
Python 3.8+). Records are separated by line breaks; fields by a separator string (default "|||||");
a record whose text contains line breaks is stitched back together using the number of fields.

Columns (no guessing): the text is in "abstract", the year in "publication_year", the journal in
"journal_title". Use --text-cols (comma-separated, e.g. abstract,title), --year-col and --group-col
to read other columns; the script stops and lists the columns if one is missing.

Step 1 - look at the file and the first records:
    python3 corpus_counts.py --input /path/to/psyc_journals.csv --peek

Step 2 - count:
    python3 corpus_counts.py --input /path/to/psyc_journals.csv --out psyc
    -> psyc_counts.csv         (query, case_sensitive, year, count)
       psyc_totals.csv         (year, documents, tokens, words)
       psyc_group_counts.csv   (group, query, case_sensitive, year, count)   per journal
       psyc_group_totals.csv   (group, year, documents, tokens, words)
    The most frequent journals are kept (--top-groups, default 60); the rest are pooled as "(other)".

Phrases are matched on tokens, the way the Ngram Viewer tokenizes: lower-cased, words and each
punctuation mark separate ("rett's" -> rett 's, "feeble-minded" -> feeble - minded). Phrases marked
case_sensitive (abbreviations such as PTSD) are matched in capitals only.
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"'s|\w+|[^\w\s]")
APOS_RE = re.compile(r"(?<=\w)['’]s\b")
YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")



def records(path, sep, encoding):
    """Yield the header, then one list of fields per record.

    A line holding all the separators of a record starts a new record; lines with fewer are
    continuations (text with line breaks in it) and are appended to the record before them.
    """
    need = None
    buf, have = None, 0
    bad = 0
    with open(path, encoding=encoding, errors="replace", newline=None) as f:
        for line in f:
            c = line.count(sep)
            if need is None:
                header = line.rstrip("\n").split(sep)
                need = len(header) - 1
                yield header
                continue
            if c >= need:  # a new record starts here
                if buf is not None:
                    if have >= need:
                        yield _split(buf, sep, need + 1)
                    else:
                        bad += 1
                buf, have = line, c
            elif buf is not None:
                if len(buf) < 50_000_000:  # guard against a runaway record
                    buf += line
                    have += c
        if buf is not None:
            if have >= need:
                yield _split(buf, sep, need + 1)
            else:
                bad += 1
    print(f"records skipped as incomplete: {bad}", file=sys.stderr)


def _split(text, sep, ncols):
    fields = text.rstrip("\n").split(sep)
    if len(fields) > ncols:  # the separator also occurs inside the last field
        fields = fields[:ncols - 1] + [sep.join(fields[ncols - 1:])]
    return fields


def pick(columns, wanted, what):
    names = [c.strip() for c in wanted.split(",") if c.strip()]
    missing = [n for n in names if n not in columns]
    if missing:
        sys.exit(f"{what} column {missing[0]!r} not found. The columns are: {columns}")
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent / "corpus_queries.csv"))
    ap.add_argument("--sep", default="|||||")
    ap.add_argument("--encoding", default="utf-8")
    ap.add_argument("--text-cols", default="abstract", help="comma-separated text columns (default: abstract)")
    ap.add_argument("--year-col", default="publication_year", help="column holding the year or a date")
    ap.add_argument("--group-col", default="journal_title", help="column to split by, i.e. the journal")
    ap.add_argument("--top-groups", type=int, default=60, help="keep this many most frequent groups; pool the rest")
    ap.add_argument("--no-groups", action="store_true", help="do not split by journal")
    ap.add_argument("--out", default="corpus", help="output prefix")
    ap.add_argument("--peek", action="store_true", help="show the columns and first records, then stop")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many records (for a test run)")
    args = ap.parse_args()

    gen = records(args.input, args.sep, args.encoding)
    header = [h.strip().strip('"') for h in next(gen)]
    text_cols = pick(header, args.text_cols, "text")
    year_col = pick(header, args.year_col, "year")
    group_col = [] if args.no_groups else pick(header, args.group_col, "journal")[:1]
    if args.peek:
        print("columns:", header)
        print("text columns:", text_cols, "| year column:", year_col[0], "| journal column:", group_col[0] if group_col else "none")
        for n, rec in enumerate(gen):
            print(f"\nrecord {n + 1}:")
            for h, v in zip(header, rec):
                print(f"   {h}: {v[:110]!r}")
            if n == 2:
                break
        return
    ti = [header.index(c) for c in text_cols]
    yi = header.index(year_col[0])
    gi = header.index(group_col[0]) if group_col else None
    keep = set()
    if gi is not None:  # first pass (cheap): which groups are the most frequent
        dc = Counter()
        pre = records(args.input, args.sep, args.encoding)
        next(pre)
        for rec in pre:
            if len(rec) > gi:
                dc[rec[gi].strip()] += 1
        keep = {g for g, _ in dc.most_common(args.top_groups)}
        print(f"{len(dc):,} distinct values in {group_col[0]!r}; keeping the {len(keep)} most frequent", file=sys.stderr)

    # ---- the phrases
    ci_phr, cs_phr = {}, {}  # first token -> list of token tuples
    with open(args.queries, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            toks = tuple(r["query"].split())
            if not toks:
                continue
            (cs_phr if r["case_sensitive"] == "1" else ci_phr).setdefault(toks[0] if r["case_sensitive"] == "1" else toks[0].lower(), set()).add(
                toks if r["case_sensitive"] == "1" else tuple(t.lower() for t in toks))
    ci_first, cs_first = set(ci_phr), set(cs_phr)
    ci_lists = {k: sorted(v, key=len, reverse=True) for k, v in ci_phr.items()}
    cs_lists = {k: sorted(v, key=len, reverse=True) for k, v in cs_phr.items()}
    print(f"{sum(len(v) for v in ci_lists.values())} case-insensitive and "
          f"{sum(len(v) for v in cs_lists.values())} case-sensitive phrases; "
          f"text columns {text_cols}, year column {year_col[0]!r}", file=sys.stderr)

    counts = defaultdict(int)      # (phrase string, case_sensitive, year) -> n
    docs, tokens, words = defaultdict(int), defaultdict(int), defaultdict(int)
    gcounts = defaultdict(int)    # (group, phrase string, case_sensitive, year) -> n
    gdocs, gtokens, gwords = defaultdict(int), defaultdict(int), defaultdict(int)
    t0, n, nodate = time.time(), 0, 0
    for rec in gen:
        if len(rec) <= max(ti + [yi]):
            continue
        m = YEAR_RE.search(rec[yi])
        if not m:
            nodate += 1
            continue
        year = int(m.group(1))
        text = APOS_RE.sub(" 's", " ".join(rec[i] for i in ti))
        toks = TOKEN_RE.findall(text)
        low = [t.lower() for t in toks]
        nwords = sum(1 for t in toks if t[0].isalnum() or t[0] == "_")
        docs[year] += 1
        tokens[year] += len(toks)
        words[year] += nwords
        group = None
        if gi is not None:
            group = rec[gi].strip()
            group = group if group in keep else "(other)"
            gdocs[(group, year)] += 1
            gtokens[(group, year)] += len(toks)
            gwords[(group, year)] += nwords
        for i in [i for i, t in enumerate(low) if t in ci_first]:
            for phrase in ci_lists[low[i]]:
                k = len(phrase)
                if tuple(low[i:i + k]) == phrase:
                    counts[(" ".join(phrase), 0, year)] += 1
                    if group is not None:
                        gcounts[(group, " ".join(phrase), 0, year)] += 1
        if cs_first:
            for i in [i for i, t in enumerate(toks) if t in cs_first]:
                for phrase in cs_lists[toks[i]]:
                    k = len(phrase)
                    if tuple(toks[i:i + k]) == phrase:
                        counts[(" ".join(phrase), 1, year)] += 1
                        if group is not None:
                            gcounts[(group, " ".join(phrase), 1, year)] += 1
        n += 1
        if n % 50000 == 0:
            print(f"  {n:,} records, {time.time() - t0:,.0f}s", file=sys.stderr)
        if args.limit and n >= args.limit:
            break

    with open(args.out + "_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "case_sensitive", "year", "count"])
        for (q, cs, y), c in sorted(counts.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
            w.writerow([q, cs, y, c])
    with open(args.out + "_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "documents", "tokens", "words"])
        for y in sorted(docs):
            w.writerow([y, docs[y], tokens[y], words[y]])
    if gi is not None:
        with open(args.out + "_group_counts.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", "query", "case_sensitive", "year", "count"])
            for (g, q, cs, y), c in sorted(gcounts.items()):
                w.writerow([g, q, cs, y, c])
        with open(args.out + "_group_totals.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", "year", "documents", "tokens", "words"])
            for (g, y) in sorted(gdocs):
                w.writerow([g, y, gdocs[(g, y)], gtokens[(g, y)], gwords[(g, y)]])
    with open(args.out + "_meta.json", "w") as f:
        json.dump({"records": n, "without_year": nodate, "text_columns": text_cols, "year_column": year_col[0], "group_column": group_col[0] if group_col else None,
                   "seconds": round(time.time() - t0)}, f)
    print(f"done: {n:,} records ({nodate:,} without a year) in {time.time() - t0:,.0f}s -> "
          f"{args.out}_counts.csv, {args.out}_totals.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
