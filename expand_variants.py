#!/usr/bin/env python3
"""Add plural and spelling forms to the variants of a terms CSV (in place).

For every multi-word variant (not containing '(' or ','):
  * if its last word is a countable noun, add the plural ("learning disability" -> "learning disabilities");
  * add the forms with each hyphen and/or slash replaced by a space.
Variants another term already has are never added, so terms stay disjoint.
Single-word variants are left alone (list their forms by hand). Line endings are preserved.

Usage: python3 expand_variants.py autism_terms.csv
"""
import csv
import re
import sys

PLURALS = {"disorder": "disorders", "syndrome": "syndromes", "disability": "disabilities",
           "defective": "defectives", "handicap": "handicaps", "mother": "mothers",
           "condition": "conditions", "psychosis": "psychoses", "episode": "episodes",
           "attack": "attacks", "disease": "diseases", "phobia": "phobias", "dysfunction": "dysfunctions"}


SINGLE_PLURALS = {"idiot": "idiots", "imbecile": "imbeciles", "moron": "morons", "retard": "retards"}


def norm(v):
    return re.sub(r"\s+", " ", v.strip().lower())


def alternatives(v):
    out = []
    if v.strip().lower() in SINGLE_PLURALS:
        return [SINGLE_PLURALS[v.strip().lower()]]
    if "(" in v or "," in v or " " not in v.strip():
        return out
    words = v.split(" ")
    last = words[-1].lower()
    if last in PLURALS and not (len(words) > 1 and words[-2].lower().endswith("ly")):
        # no plural after an adverb: "mentally defective" is an adjective phrase
        out.append(" ".join(words[:-1] + [PLURALS[last]]))
    for hyphen, slash in ((True, False), (False, True), (True, True)):
        x = v
        if hyphen:
            x = x.replace("-", " ")
        if slash:
            x = x.replace("/", " ")
        x = re.sub(r"\s+", " ", x).strip()
        if x != v:
            out.append(x)
    return out


def main(path):
    raw = open(path, newline="", encoding="utf-8").read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    rows = list(csv.reader(raw.splitlines()))
    hdr, rows = rows[0], rows[1:]
    vi = hdr.index("variants")
    ei = hdr.index("eras") if "eras" in hdr else None
    owner = {}
    for r in rows:
        for v in r[vi].split(";"):
            owner.setdefault(norm(v), set()).add(r[0])
    added = 0
    for r in rows:
        vs = [v.strip() for v in r[vi].split(";") if v.strip()]
        have = {norm(v) for v in vs}
        new = []
        for v in list(vs):
            for a in alternatives(v):
                k = norm(a)
                if k in have or owner.get(k, set()) - {r[0]}:
                    continue
                have.add(k)
                owner.setdefault(k, set()).add(r[0])
                new.append(a)
        if new:
            r[vi] = "; ".join(vs + new)
            added += len(new)
        if ei is not None and r[ei]:
            # "era: v1, v2 | era2: v3" - generated forms inherit the era of the variant they come from
            eras = [(e.split(":", 1)[0].strip(), [x.strip() for x in e.split(":", 1)[1].split(",") if x.strip()])
                    for e in r[ei].split("|")]
            for name, members in eras:
                for m in list(members):
                    for a in alternatives(m):
                        if a not in members:
                            members.append(a)
            r[ei] = " | ".join(f"{n}: {', '.join(m)}" for n, m in eras)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator=eol)
        w.writerow(hdr)
        w.writerows(rows)
    print(f"{path}: {len(rows)} terms, {added} forms added")


if __name__ == "__main__":
    main(sys.argv[1])
