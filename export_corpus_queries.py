#!/usr/bin/env python3
"""Write corpus_queries.csv: every phrase to count in your own text corpus.

Unlike the Ngram Viewer, a corpus can be searched for phrases of any length, so the FULL variants
are used (no 5-token windows). Phrases are tokenized the way the Ngram Viewer does it
("rett's" -> "rett 's", "feeble-minded" -> "feeble - minded"). Abbreviations that the Ngram runs
search in capitals only are marked case_sensitive.

Usage: python3 export_corpus_queries.py            (writes corpus_queries.csv)
"""
import csv
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("collector", HERE / "collect_ngram_stats.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

SETS = [  # (set name, terms file, search abbreviations in capitals?)
    ("mental", HERE / "mental_disorders.csv", True),
    ("autism", HERE / "autism_terms.csv", False),
]

rows = []
for name, path, acronyms in SETS:
    for term, _category, variants in collector.load_terms(path):
        for v in variants:
            lower = v.strip().lower()
            if acronyms and lower in collector.AMBIGUOUS_ABBREVIATIONS and collector.is_acronym(v, variants):
                continue  # ED, AN, SAD ... are skipped in the Ngram runs too
            q = collector.to_query(v, True)
            case_sensitive = bool(acronyms and collector.is_acronym(v, variants))
            rows.append([name, term, v.strip(), q.upper() if case_sensitive else q, int(case_sensitive)])

with open(HERE / "corpus_queries.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["set", "term", "variant", "query", "case_sensitive"])
    w.writerows(rows)
print(f"{len(rows)} phrases -> corpus_queries.csv")
