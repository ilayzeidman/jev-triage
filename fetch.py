#!/usr/bin/env python3
"""Fetch recent issues with the labels maintainers gave them, for the eval.

    python fetch.py --since 2026-05-01 --max 1000

Recent issues only: older ones may be in jev's training data. Writes
data/issues.jsonl (raw GitHub issues, pull requests skipped).
"""
import argparse
import json
from datetime import date, timedelta

import triage

OUT = triage.HERE / "data" / "issues.jsonl"


def search(query, limit):
    """GitHub search returns at most 1,000 results per query, 100 a page."""
    items, page = [], 1
    while len(items) < limit and page <= 10:
        res = triage.github(f"search/issues?q={query}&sort=created&order=desc&per_page=100&page={page}")
        items += res["items"]
        if len(res["items"]) < 100:
            break
        page += 1
    return items[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=str(date.today() - timedelta(days=150)))
    ap.add_argument("--max", type=int, default=1000)
    args = ap.parse_args()
    triage.load_env()
    q = f"repo:{triage.TAXONOMY['repo']}+is:issue+created:>={args.since}"
    issues = [i for i in search(q, args.max) if "pull_request" not in i]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("".join(json.dumps(i) + "\n" for i in issues))
    print(f"{len(issues)} issues since {args.since} -> {OUT.relative_to(triage.HERE)}")


if __name__ == "__main__":
    main()
