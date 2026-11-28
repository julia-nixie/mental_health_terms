#!/usr/bin/env python3
"""Turn autism_collocates.csv into docs/autism/collocates.json for the collocates page.

Everything is lemmatized (lemminflect):
  * collocates: a possessive ending is removed ("child's" -> "child"), then the word is reduced to its
    lemma: in noun lists the noun lemma ("children" -> "child"), in adjective lists the adjective
    lemma, in any-word lists the noun lemma if the word is a known noun, else the verb lemma
    ("diagnosed" -> "diagnose"), else the adjective lemma. Unknown words are kept as they are.
  * variants: possessives and noun plurals are reduced the same way, word by word, so "autism" and
    "autisms", or "kanner's syndrome", "kanner syndrome" and their plurals, become one entry (named
    after the first of its forms in the terms CSV). Spelling differences ("retts") stay separate.

All patterns of the same word kind are combined into one list per kind ("any", "noun", "adj"): before
and after the term, next to it or through a linking word, without the linking word ("children with
autism" and "autism in children" both count for "child"). A lemma's share is the sum of the
frequencies of all its phrases (they never overlap) divided by the summed frequency of the merged
variant forms in that decade (autism_collocate_bases.csv). Every entry is kept, sorted by share.

Layout: {"decades": [...], "terms": [{"term": ..., "variants": [{"v": ..., "forms": [...], "p":
{kind: {decade: [[lemma, share, rank], ...]}}}]}]}, terms and variants in the order of the terms CSV.

Usage: python3 build_collocates_json.py [--csv autism_collocates.csv]
                                         [--bases autism_collocate_bases.csv]
                                         [--terms autism_terms.csv] [--out docs/autism/collocates.json]
"""
import argparse
import csv
import json
from pathlib import Path

from lemminflect import getAllLemmas

HERE = Path(__file__).resolve().parent


def strip_possessive(w):
    w = w.replace("’", "'")
    if w.endswith("'s"):
        return w[:-2]
    if w.endswith("s'"):
        return w[:-1]
    return w


def lemma(w, kind):
    w = strip_possessive(w)
    found = getAllLemmas(w)
    order = {"noun": ["NOUN"], "adj": ["ADJ"], "any": ["NOUN", "VERB", "ADJ", "ADV", "AUX"]}[kind]
    for pos in order:
        if pos in found:
            return found[pos][0]
    return w


def variant_key(v):
    """Possessives and noun plurals reduced, word by word ("kanner's syndromes" -> "kanner syndrome")."""
    out = []
    for tok in v.lower().replace("’", "'").split():
        tok = strip_possessive(tok)
        if tok and "-" not in tok:
            tok = getAllLemmas(tok).get("NOUN", (tok,))[0]
        if tok:
            out.append(tok)
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "autism_collocates.csv"))
    ap.add_argument("--bases", default=str(HERE / "autism_collocate_bases.csv"))
    ap.add_argument("--terms", default=str(HERE / "autism_terms.csv"))
    ap.add_argument("--out", default=str(HERE / "docs" / "autism" / "collocates.json"))
    args = ap.parse_args()

    # variant forms grouped by lemma key, per term, in the order of the terms CSV
    groups = []                                  # (term, [(key, [forms])])
    key_of = {}
    with open(args.terms, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            order = {}
            for v in (x.strip() for x in r["variants"].split(";")):
                if v:
                    k = variant_key(v)
                    order.setdefault(k, []).append(v)
                    key_of[(r["term"], v)] = k
            groups.append((r["term"], list(order.items())))

    base = {}                                    # (term, key, decade) -> summed frequency of the forms
    with open(args.bases, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["term"], key_of[(r["term"], r["variant"])], r["decade"])
            base[k] = base.get(k, 0.0) + float(r["frequency"])

    freq, decades = {}, set()                    # (term, key) -> kind -> decade -> lemma -> frequency
    with open(args.csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            decades.add(int(r["decade"]))
            kind = r["pattern"].rsplit("_", 1)[1]
            cell = freq.setdefault((r["term"], key_of[(r["term"], r["variant"])]), {}) \
                       .setdefault(kind, {}).setdefault(r["decade"], {})
            w = lemma(r["collocate"], kind)
            cell[w] = cell.get(w, 0.0) + float(r["frequency"])

    terms = []
    for term, keyed in groups:
        variants = []
        for k, forms in keyed:
            if (term, k) not in freq:
                continue
            p = {}
            for kind, cells in freq[(term, k)].items():
                for d, tot in cells.items():
                    b = base[(term, k, d)]
                    ranked = sorted(tot.items(), key=lambda x: -x[1])
                    p.setdefault(kind, {})[d] = [[w, round(x / b, 5), i] for i, (w, x) in enumerate(ranked, 1)]
            variants.append({"v": forms[0], "forms": forms, "p": p})
        if variants:
            terms.append({"term": term, "variants": variants})

    out = {"decades": sorted(decades), "terms": terms}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(terms)} terms, {sum(len(t['variants']) for t in terms)} variants")


if __name__ == "__main__":
    main()
