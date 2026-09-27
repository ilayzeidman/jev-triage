#!/usr/bin/env python3
"""Shadow mode on the real repo: predict every new issue the moment it appears,
before any maintainer has labelled it. Read-only; it never writes to GitHub.

    python shadow.py            # poll every 10 minutes, forever
    python shadow.py --once     # one pass

Predictions go to shadow/predictions.jsonl, one line per issue, first sight only.
score.py later compares them with the labels the issues ended up with.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from typesafe_sdk import TypeSafeClient

import triage

OUT = triage.HERE / "shadow" / "predictions.jsonl"
STARTED = triage.HERE / "shadow" / "started.txt"  # issues older than this are not blind


def started():
    if not STARTED.exists():
        STARTED.parent.mkdir(exist_ok=True)
        STARTED.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return STARTED.read_text().strip()


def seen():
    if not OUT.exists():
        return set()
    return {json.loads(line)["issue"] for line in OUT.read_text().splitlines() if line}


def one_pass(client):
    done, since = seen(), started()
    new = [i for i in triage.github(f"repos/{triage.TAXONOMY['repo']}/issues"
                                    "?state=all&sort=created&direction=desc&per_page=50")
           if "pull_request" not in i and i["number"] not in done
           and i["created_at"] > since]
    for issue in reversed(new):
        # The author's /kind and /sig are applied by a bot within seconds, so record
        # which labels were already there: those were not jev's to predict blind.
        labels_at_sight = [l["name"] for l in issue["labels"]]
        try:
            r = triage.triage(issue, client)
        except Exception as e:  # one bad issue must not stop a week-long run
            print(f"#{issue['number']} failed: {e}")
            continue
        r.update(seen_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 created_at=issue["created_at"], labels_at_sight=labels_at_sight)
        with OUT.open("a") as f:
            f.write(json.dumps(r) + "\n")
        print(f"#{r['issue']} {r['route']:<11} {' '.join(r['labels'].values())}")
    return len(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=600, help="seconds between passes")
    args = ap.parse_args()
    triage.load_env()
    client = TypeSafeClient()
    while True:
        try:
            n = one_pass(client)
            print(f"{datetime.now():%Y-%m-%d %H:%M} {n} new")
        except Exception as e:  # GitHub hiccup: try again next pass
            print(f"{datetime.now():%Y-%m-%d %H:%M} pass failed: {e}")
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
