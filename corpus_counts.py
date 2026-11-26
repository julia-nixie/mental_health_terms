#!/usr/bin/env python3
"""Count how often each phrase in corpus_queries.csv occurs in a large delimited text file, per year.

Runs on your own machine over the whole file without loading it into memory (standard library only,
Python 3.8+). The input is a standard CSV (commas, quoted fields; line breaks inside quotes are fine).

Columns (no guessing): the id is "PMID", the text is in "AbstractText", the year is read from
"PubDate" (and from "MedlineDate" when PubDate has no year), the journal is "JournalTitle".
Use --id-col, --text-cols (comma-separated, e.g. ArticleTitle,AbstractText), --year-col and
--group-col to read other columns; the script stops and lists the columns if one is missing.

Step 1 - look at the file and the first records:
    python3 corpus_counts.py --input /path/to/psyc_journals.csv --peek

Step 2 - count:
    python3 corpus_counts.py --input /path/to/psyc_journals.csv --out psyc
    -> psyc_counts.csv         (query, case_sensitive, year, count)
       psyc_totals.csv         (year, documents, tokens, words)
       psyc_group_counts.csv   (group, query, case_sensitive, year, count)   per journal
       psyc_group_totals.csv   (group, year, documents, tokens, words)
       psyc_pmids.csv          (PMID, year, journal, terms, phrases)   one row per matching record
    The PMID file lists every record that contains at least one phrase: "terms" are the groups it
    belongs to once phrases inside longer phrases are taken out (as in the counts), "phrases" is
    everything found, as variant:occurrences. Use --no-pmids to skip it.
    Every journal is kept (nothing is truncated; any top-N is for display only).

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
from collections import defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"'s|\w+|[^\w\s]")
APOS_RE = re.compile(r"(?<=\w)['’]s\b")
YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")



def records(path, encoding):
    """Yield the header row, then one list of fields per record (standard CSV: commas, quoted fields,
    line breaks inside quotes)."""
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))   # abstracts can be long
    with open(path, encoding=encoding, errors="replace", newline="") as f:
        yield from csv.reader(f)


def pick(columns, wanted, what):
    names = [c.strip() for c in wanted.split(",") if c.strip()]
    missing = [n for n in names if n not in columns]
    if missing:
        sys.exit(f"{what} column {missing[0]!r} not found. The columns are: {columns}")
    return names


def contains(big, small):
    n = len(small)
    return len(big) > n and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent / "corpus_queries.csv"))
    ap.add_argument("--encoding", default="utf-8")
    ap.add_argument("--id-col", default="PMID", help="column with the record id (default: PMID)")
    ap.add_argument("--text-cols", default="AbstractText", help="comma-separated text columns (default: AbstractText)")
    ap.add_argument("--year-col", default="PubDate,MedlineDate",
                    help="column(s) holding the year or a date; tried in order (default: PubDate,MedlineDate)")
    ap.add_argument("--group-col", default="JournalTitle", help="column to split by, i.e. the journal")
    ap.add_argument("--no-groups", action="store_true", help="do not split by journal")
    ap.add_argument("--no-pmids", action="store_true", help="do not write the list of matching PMIDs")
    ap.add_argument("--out", default="corpus", help="output prefix")
    ap.add_argument("--peek", action="store_true", help="show the columns and first records, then stop")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many records (for a test run)")
    args = ap.parse_args()

    gen = records(args.input, args.encoding)
    header = [h.strip().strip('"') for h in next(gen)]
    id_col = pick(header, args.id_col, "id")[0]
    text_cols = pick(header, args.text_cols, "text")
    year_cols = pick(header, args.year_col, "year")
    group_col = [] if args.no_groups else pick(header, args.group_col, "journal")[:1]
    if args.peek:
        print("columns:", header)
        print("id:", id_col, "| text:", text_cols, "| year:", year_cols, "| journal:", group_col[0] if group_col else "none")
        for n, rec in enumerate(gen):
            print(f"\nrecord {n + 1}:")
            for h, v in zip(header, rec):
                print(f"   {h}: {v[:110]!r}")
            if n == 2:
                break
        return
    ti = [header.index(c) for c in text_cols]
    yis = [header.index(c) for c in year_cols]
    idi = header.index(id_col)
    gi = header.index(group_col[0]) if group_col else None

    # ---- the phrases; ci_lists / cs_lists map a first token to [(token tuple, phrase id), ...]
    pid_of, info = {}, []             # info[pid] = (phrase string, case_sensitive, term, variant)
    ci_phr, cs_phr = {}, {}
    with open(args.queries, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            toks = tuple(r["query"].split())
            if not toks:
                continue
            cs = r["case_sensitive"] == "1"
            toks = toks if cs else tuple(t.lower() for t in toks)
            key = (" ".join(toks), int(cs))
            if key in pid_of:
                continue
            pid_of[key] = len(info)
            info.append((key[0], key[1], r.get("term", ""), r.get("variant", key[0])))
            (cs_phr if cs else ci_phr).setdefault(toks[0], []).append((toks, pid_of[key]))
    ci_first, cs_first = set(ci_phr), set(cs_phr)
    ci_lists = {k: sorted(v, key=lambda x: -len(x[0])) for k, v in ci_phr.items()}
    cs_lists = {k: sorted(v, key=lambda x: -len(x[0])) for k, v in cs_phr.items()}
    # a phrase inside a longer phrase of the list: the nearest such containers (as in corpus_to_stats.py)
    low_toks = [tuple(p[0].lower().split()) for p in info]
    nearest = []
    for a in range(len(info)):
        cont = [b for b in range(len(info)) if b != a and contains(low_toks[b], low_toks[a])]
        nearest.append([b for b in cont if not any(o != b and contains(low_toks[b], low_toks[o]) for o in cont)])
    print(f"{len(info)} phrases ({sum(len(v) for v in cs_lists.values())} case-sensitive); "
          f"id {id_col!r}, text {text_cols}, year {year_cols}, journal {group_col[0] if group_col else None!r}", file=sys.stderr)

    counts = defaultdict(int)      # (phrase id, year) -> n
    docs, tokens, words = defaultdict(int), defaultdict(int), defaultdict(int)
    gcounts = defaultdict(int)     # (group, phrase id, year) -> n
    gdocs, gtokens, gwords = defaultdict(int), defaultdict(int), defaultdict(int)
    pm_file = pm = None
    if not args.no_pmids:
        pm_file = open(args.out + "_pmids.csv", "w", newline="", encoding="utf-8")
        pm = csv.writer(pm_file)
        pm.writerow([id_col, "year", "journal", "terms", "phrases"])
    matched = 0
    t0, n, nodate = time.time(), 0, 0
    for rec in gen:
        if len(rec) <= max(ti + yis + [idi] + ([gi] if gi is not None else [])):
            continue
        year = None
        for yi in yis:                     # PubDate first, MedlineDate when it has no year
            m = YEAR_RE.search(rec[yi])
            if m:
                year = int(m.group(1))
                break
        if year is None:
            nodate += 1
            continue
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
            gdocs[(group, year)] += 1
            gtokens[(group, year)] += len(toks)
            gwords[(group, year)] += nwords
        hit = {}                           # phrase id -> occurrences in this record
        for i in [i for i, t in enumerate(low) if t in ci_first]:
            for phrase, pid in ci_lists[low[i]]:
                if tuple(low[i:i + len(phrase)]) == phrase:
                    hit[pid] = hit.get(pid, 0) + 1
        if cs_first:
            for i in [i for i, t in enumerate(toks) if t in cs_first]:
                for phrase, pid in cs_lists[toks[i]]:
                    if tuple(toks[i:i + len(phrase)]) == phrase:
                        hit[pid] = hit.get(pid, 0) + 1
        for pid, c in hit.items():
            counts[(pid, year)] += c
            if group is not None:
                gcounts[(group, pid, year)] += c
        if hit:
            matched += 1
        if hit and pm is not None:
            terms = []
            for pid, c in hit.items():
                if c - sum(hit.get(b, 0) for b in nearest[pid]) > 0 and info[pid][2] not in terms:
                    terms.append(info[pid][2])
            pm.writerow([rec[idi].strip(), year, group or "", "; ".join(terms),
                         "; ".join(f"{info[pid][3]}:{c}" for pid, c in sorted(hit.items()))])
        n += 1
        if n % 50000 == 0:
            print(f"  {n:,} records, {time.time() - t0:,.0f}s", file=sys.stderr)
        if args.limit and n >= args.limit:
            break
    if pm_file is not None:
        pm_file.close()

    with open(args.out + "_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "case_sensitive", "year", "count"])
        for (pid, y), c in sorted(counts.items(), key=lambda x: (info[x[0][0]][0], info[x[0][0]][1], x[0][1])):
            w.writerow([info[pid][0], info[pid][1], y, c])
    with open(args.out + "_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "documents", "tokens", "words"])
        for y in sorted(docs):
            w.writerow([y, docs[y], tokens[y], words[y]])
    if gi is not None:
        with open(args.out + "_group_counts.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", "query", "case_sensitive", "year", "count"])
            for (g, pid, y), c in sorted(gcounts.items(), key=lambda x: (x[0][0], info[x[0][1]][0], info[x[0][1]][1], x[0][2])):
                w.writerow([g, info[pid][0], info[pid][1], y, c])
        with open(args.out + "_group_totals.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", "year", "documents", "tokens", "words"])
            for (g, y) in sorted(gdocs):
                w.writerow([g, y, gdocs[(g, y)], gtokens[(g, y)], gwords[(g, y)]])
    with open(args.out + "_meta.json", "w") as f:
        json.dump({"records": n, "without_year": nodate, "matching_records": matched,
                   "id_column": id_col, "text_columns": text_cols, "year_columns": year_cols,
                   "group_column": group_col[0] if group_col else None, "seconds": round(time.time() - t0)}, f)
    print(f"done: {n:,} records ({nodate:,} without a year, {matched:,} matching) in {time.time() - t0:,.0f}s -> "
          f"{args.out}_counts.csv, {args.out}_totals.csv"
          + (f", {args.out}_pmids.csv" if pm_file is not None else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
