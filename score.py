#!/usr/bin/env python3
"""Score the shadow run: jev's blind predictions vs the labels issues have now.

    python score.py

Kind and SIG labels the author applied before jev saw the issue are reported
apart: jev predicted without them, but the "truth" there is the author's own call.
"""
import json
from collections import defaultdict

import eval as ev
import shadow
import triage


def main():
    triage.load_env()
    preds = [json.loads(l) for l in shadow.OUT.read_text().splitlines() if l]
    per = defaultdict(list)
    for p in preds:
        now = ev.truth(triage.github(f"repos/{triage.TAXONOMY['repo']}/issues/{p['issue']}"))
        at_sight = set(p["labels_at_sight"])
        for n in ev.LABEL_QUESTIONS:
            if now[n]:
                blind = not (now[n] & at_sight)   # nobody had labelled this yet when jev saw it
                per[(n, blind)].append(p["labels"][n] in now[n])
    first = min(p["seen_at"] for p in preds)
    last = max(p["seen_at"] for p in preds)
    print(f"{len(preds)} issues predicted blind, {first} to {last}")
    for (n, blind), hits in sorted(per.items()):
        how = "blind" if blind else "author had labelled it"
        print(f"  {n:<9} {sum(hits) / len(hits):6.1%} of {len(hits):<4} {how}")


if __name__ == "__main__":
    main()
