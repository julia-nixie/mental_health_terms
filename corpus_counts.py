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
       psyc_group_counts.csv.gz  (group, query, case_sensitive, year, count)   per journal, ALL journals
       psyc_group_totals.csv.gz  (group, year, documents, tokens, words)
    Nothing is truncated: every journal is kept. The per-journal counts are written compressed as
    the run goes (memory stays bounded); a (group, query, year) row can appear more than once and
    should be summed.
    The phrase list is built into this script (corpus_queries.csv is only needed with --queries).

Phrases are matched on tokens, the way the Ngram Viewer tokenizes: lower-cased, words and each
punctuation mark separate ("rett's" -> rett 's, "feeble-minded" -> feeble - minded). Phrases marked
case_sensitive (abbreviations such as PTSD) are matched in capitals only.
"""
import argparse
import base64
import csv
import gzip
import io
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"'s|\w+|[^\w\s]")
APOS_RE = re.compile(r"(?<=\w)['’]s\b")
YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")



# BEGIN EMBEDDED QUERIES
EMBEDDED_QUERIES = """
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
"""
# END EMBEDDED QUERIES


def load_queries(path):
    """The phrase list: --queries file if given, else corpus_queries.csv beside this script, else the
    list built into this script."""
    if path is None:
        beside = Path(__file__).resolve().parent / "corpus_queries.csv"
        path = str(beside) if beside.exists() else None
    if path is not None:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if not EMBEDDED_QUERIES.strip():
        sys.exit("No phrase list: pass --queries corpus_queries.csv")
    raw = gzip.decompress(base64.b64decode("".join(EMBEDDED_QUERIES.split()))).decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


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
    ap.add_argument("--queries", default=None, help="phrase list CSV (default: the list built into this script)")
    ap.add_argument("--sep", default="|||||")
    ap.add_argument("--encoding", default="utf-8")
    ap.add_argument("--text-cols", default="abstract", help="comma-separated text columns (default: abstract)")
    ap.add_argument("--year-col", default="publication_year", help="column holding the year or a date")
    ap.add_argument("--group-col", default="journal_title", help="column to split by, i.e. the journal")
    ap.add_argument("--no-groups", action="store_true", help="do not split by journal")
    ap.add_argument("--spill-every", type=int, default=4_000_000, help=argparse.SUPPRESS)
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

    # ---- the phrases: ci_lists / cs_lists map a first token to [(token tuple, phrase id), ...]
    pid_of, pid_names = {}, []
    ci_phr, cs_phr = {}, {}
    for r in load_queries(args.queries):
        toks = tuple(r["query"].split())
        if not toks:
            continue
        cs = r["case_sensitive"] == "1"
        toks = toks if cs else tuple(t.lower() for t in toks)
        key = (" ".join(toks) if not cs else " ".join(toks), int(cs))
        if key not in pid_of:
            pid_of[key] = len(pid_names)
            pid_names.append(key)
            (cs_phr if cs else ci_phr).setdefault(toks[0], []).append((toks, pid_of[key]))
    ci_first, cs_first = set(ci_phr), set(cs_phr)
    ci_lists = {k: sorted(v, key=lambda x: -len(x[0])) for k, v in ci_phr.items()}
    cs_lists = {k: sorted(v, key=lambda x: -len(x[0])) for k, v in cs_phr.items()}
    NP = len(pid_names)
    print(f"{NP} phrases ({sum(len(v) for v in cs_lists.values())} case-sensitive); "
          f"text columns {text_cols}, year column {year_col[0]!r}, journal column "
          f"{group_col[0] if group_col else None!r}", file=sys.stderr)

    counts = defaultdict(int)      # (phrase id, year) -> n
    docs, tokens, words = defaultdict(int), defaultdict(int), defaultdict(int)
    gids, gnames = {}, []          # journal -> id
    gcounts = defaultdict(int)     # ((gid * NP + pid) << 10 | year - 1500) -> n, spilled to disk when large
    gdocs, gtokens, gwords = defaultdict(int), defaultdict(int), defaultdict(int)
    gout = None
    if gi is not None:
        gout = gzip.open(args.out + "_group_counts.csv.gz", "wt", newline="", encoding="utf-8")
        gw = csv.writer(gout)
        gw.writerow(["group", "query", "case_sensitive", "year", "count"])

    def spill():
        for key, c in gcounts.items():
            rest, ydiff = key >> 10, key & 1023
            gid, pid = divmod(rest, NP)
            q, cs = pid_names[pid]
            gw.writerow([gnames[gid], q, cs, 1500 + ydiff, c])
        gcounts.clear()

    t0, n, nodate = time.time(), 0, 0
    for rec in gen:
        if len(rec) <= max(ti + [yi] + ([gi] if gi is not None else [])):
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
        gid = None
        if gi is not None:
            name = rec[gi].strip()
            gid = gids.get(name)
            if gid is None:
                gid = gids[name] = len(gnames)
                gnames.append(name)
            gdocs[(gid, year)] += 1
            gtokens[(gid, year)] += len(toks)
            gwords[(gid, year)] += nwords
        ybit = year - 1500
        for i in [i for i, t in enumerate(low) if t in ci_first]:
            for phrase, pid in ci_lists[low[i]]:
                if tuple(low[i:i + len(phrase)]) == phrase:
                    counts[(pid, year)] += 1
                    if gid is not None:
                        gcounts[((gid * NP + pid) << 10) | ybit] += 1
        if cs_first:
            for i in [i for i, t in enumerate(toks) if t in cs_first]:
                for phrase, pid in cs_lists[toks[i]]:
                    if tuple(toks[i:i + len(phrase)]) == phrase:
                        counts[(pid, year)] += 1
                        if gid is not None:
                            gcounts[((gid * NP + pid) << 10) | ybit] += 1
        if gout is not None and len(gcounts) >= args.spill_every:
            spill()
        n += 1
        if n % 50000 == 0:
            print(f"  {n:,} records, {time.time() - t0:,.0f}s", file=sys.stderr)
        if args.limit and n >= args.limit:
            break

    with open(args.out + "_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "case_sensitive", "year", "count"])
        for (pid, y), c in sorted(counts.items(), key=lambda x: (pid_names[x[0][0]], x[0][1])):
            w.writerow([pid_names[pid][0], pid_names[pid][1], y, c])
    with open(args.out + "_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "documents", "tokens", "words"])
        for y in sorted(docs):
            w.writerow([y, docs[y], tokens[y], words[y]])
    if gout is not None:
        spill()
        gout.close()
        with gzip.open(args.out + "_group_totals.csv.gz", "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", "year", "documents", "tokens", "words"])
            for (gid, y) in sorted(gdocs, key=lambda k: (gnames[k[0]], k[1])):
                w.writerow([gnames[gid], y, gdocs[(gid, y)], gtokens[(gid, y)], gwords[(gid, y)]])
    with open(args.out + "_meta.json", "w") as f:
        json.dump({"records": n, "without_year": nodate, "text_columns": text_cols, "year_column": year_col[0],
                   "group_column": group_col[0] if group_col else None, "journals": len(gnames),
                   "seconds": round(time.time() - t0)}, f)
    print(f"done: {n:,} records ({nodate:,} without a year, {len(gnames):,} journals) in "
          f"{time.time() - t0:,.0f}s -> {args.out}_counts.csv, {args.out}_totals.csv"
          + (f", {args.out}_group_counts.csv.gz, {args.out}_group_totals.csv.gz" if gout is not None else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
