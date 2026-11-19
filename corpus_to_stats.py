#!/usr/bin/env python3
"""Turn the raw phrase counts of a text corpus (corpus_counts.py) into the same kind of series as the
Google Ngram runs: overlap removal, plural/spelling merging, per-million rates, and the JSON the
site's corpus tab reads.

    python3 corpus_to_stats.py --dir corpus_results/autism --terms autism_terms.csv \
        --queries corpus_queries.csv --name autism --json docs/autism/corpus.json

The same rules as collect_ngram_stats.py are applied:
  * a phrase's count is reduced by the counts of the nearest longer phrases that contain it
    ("autism" loses "autism spectrum", "infantile autism", ...), so text is counted once;
  * plural and spelling forms of one phrase are summed into one variant under the same name.
Writes <dir>/<name>_corpus_variant_stats.csv and <dir>/<name>_corpus_stats.csv (term totals).
"""
import argparse
import csv
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("collector", HERE / "collect_ngram_stats.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def contains(big, small):
    n = len(small)
    return len(big) > n and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder with <name>_counts.csv, _totals.csv, _group_*.csv, _meta.json")
    ap.add_argument("--name", required=True)
    ap.add_argument("--terms", required=True)
    ap.add_argument("--queries", required=True, help="the corpus_queries.csv used for the counting run")
    ap.add_argument("--json", required=True)
    args = ap.parse_args()
    d = Path(args.dir)

    # ---- terms, variants and the names they are shown under (same rule as the Ngram runs)
    terms = list(collector.load_terms(Path(args.terms)))
    eras = {}
    with open(args.terms, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for chunk in (r.get("eras") or "").split("|"):
                if ":" in chunk:
                    era, members = chunk.split(":", 1)
                    for m in members.split(","):
                        eras[(r["term"], m.strip().lower())] = era.strip()
    name_of, labels = {}, []          # term -> {merge key -> display name}; labels = [(term, name)]
    for term, _cat, variants in terms:
        rank_of, names = {}, {}
        for n, v in enumerate(variants):
            last = v.lower().split()[-1] if v.split() else ""
            rank = (last in collector.PLURAL_WORDS, n)
            k = collector.merge_key(v)
            if k not in rank_of or rank < rank_of[k]:
                rank_of[k], names[k] = rank, v.strip()
        name_of[term] = names
        seen = []
        for v in variants:
            nm = names[collector.merge_key(v)]
            if nm not in seen:
                seen.append(nm)
        labels += [(term, nm) for nm in seen]
    vindex = {lab: i for i, lab in enumerate(labels)}

    # ---- phrases -> variant
    qinfo = {}                         # (query, cs) -> variant index
    for r in csv.DictReader(open(args.queries, newline="", encoding="utf-8")):
        key = (r["query"], r["case_sensitive"])
        if key not in qinfo:
            term = r["term"]
            nm = name_of[term][collector.merge_key(r["variant"])]
            qinfo[key] = vindex[(term, nm)]
    toks = {k: tuple(k[0].lower().split()) for k in qinfo}
    nearest = {}
    for a in qinfo:
        cont = [b for b in qinfo if b != a and contains(toks[b], toks[a])]
        nearest[a] = [b for b in cont if not any(o != b and contains(toks[b], toks[o]) for o in cont)]

    # ---- totals and counts
    tot = {}
    for r in csv.DictReader(open(d / f"{args.name}_totals.csv", newline="", encoding="utf-8")):
        tot[int(r["year"])] = (int(r["documents"]), int(r["tokens"]))
    y0, y1 = min(tot), max(tot)
    n = y1 - y0 + 1
    raw = defaultdict(lambda: [0] * n)
    for r in csv.DictReader(open(d / f"{args.name}_counts.csv", newline="", encoding="utf-8")):
        raw[(r["query"], r["case_sensitive"])][int(r["year"]) - y0] += int(r["count"])
    unknown = [k for k in raw if k not in qinfo]
    if unknown:
        raise SystemExit(f"counts for phrases that are not in {args.queries}: {unknown[:5]}")

    def exclusive_overall(a):
        ex = list(raw[a])
        for b in nearest[a]:
            for i, x in enumerate(raw[b] if b in raw else [0] * n):
                ex[i] -= x
        return [max(0, x) for x in ex]

    var_counts = [[0] * n for _ in labels]
    for a in raw:
        ex = exclusive_overall(a)
        row = var_counts[qinfo[a]]
        for i, x in enumerate(ex):
            row[i] += x

    # ---- per journal (same removal, cell by cell)
    jraw = defaultdict(lambda: defaultdict(dict))        # journal -> phrase -> {year: count}
    for r in csv.DictReader(open(d / f"{args.name}_group_counts.csv", newline="", encoding="utf-8")):
        jraw[r["group"]][(r["query"], r["case_sensitive"])][int(r["year"])] = int(r["count"])
    jtok = defaultdict(dict)
    for r in csv.DictReader(open(d / f"{args.name}_group_totals.csv", newline="", encoding="utf-8")):
        jtok[r["group"]][int(r["year"])] = int(r["tokens"])
    journals = []
    for j in sorted(jtok, key=lambda j: -sum(jtok[j].values())):
        per_var = defaultdict(lambda: defaultdict(int))  # variant index -> {year: count}
        for a, years in jraw.get(j, {}).items():
            for y, c in years.items():
                c -= sum(jraw[j].get(b, {}).get(y, 0) for b in nearest[a])
                if c > 0:
                    per_var[qinfo[a]][y] += c
        journals.append({
            "n": j,
            "tok": [x for y in sorted(jtok[j]) for x in (y - y0, jtok[j][y])],
            "c": {str(vi): [x for y in sorted(ys) for x in (y - y0, ys[y])] for vi, ys in sorted(per_var.items())},
        })

    # ---- outputs
    with open(d / f"{args.name}_corpus_variant_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["term", "variant", "year", "count", "tokens", "per_million"])
        for (term, nm), row in zip(labels, var_counts):
            for i, c in enumerate(row):
                y = y0 + i
                if y in tot and tot[y][1]:
                    w.writerow([term, nm, y, c, tot[y][1], f"{c / tot[y][1] * 1e6:.6g}"])
    with open(d / f"{args.name}_corpus_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["term", "year", "count", "tokens", "per_million"])
        for term, _c, _v in terms:
            for i in range(n):
                y = y0 + i
                if y in tot and tot[y][1]:
                    c = sum(var_counts[vi][i] for vi, (t, _) in enumerate(labels) if t == term)
                    w.writerow([term, y, c, tot[y][1], f"{c / tot[y][1] * 1e6:.6g}"])
    meta = json.load(open(d / f"{args.name}_meta.json"))
    out = {
        "y0": y0, "y1": y1,
        "docs": [tot.get(y0 + i, (0, 0))[0] for i in range(n)],
        "tokens": [tot.get(y0 + i, (0, 0))[1] for i in range(n)],
        "meta": {"records": meta["records"], "journals": len(jtok)},
        "variants": [{"g": t, "n": nm, **({"e": eras[(t, nm.lower())]} if (t, nm.lower()) in eras else {}), "c": var_counts[i]}
                     for i, (t, nm) in enumerate(labels)],
        "journals": journals,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    # cache-bust: the page fetches corpus.json?v=<hash of its content>
    import hashlib
    import re
    page = Path(args.json).parent / "index.html"
    if page.exists():
        version = hashlib.md5(Path(args.json).read_bytes()).hexdigest()[:10]
        html = page.read_text(encoding="utf-8")
        html2 = re.sub(r"fetch\('corpus\.json[^']*'\)", f"fetch('corpus.json?v={version}')", html)
        if html2 != html:
            page.write_text(html2, encoding="utf-8")
    print(f"{len(labels)} variants, {len(journals)} journals, {y0}-{y1}; "
          f"{Path(args.json).stat().st_size / 1024:.0f} KB -> {args.json}")


if __name__ == "__main__":
    main()
