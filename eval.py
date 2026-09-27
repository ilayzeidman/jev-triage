#!/usr/bin/env python3
"""Score jev, System Two and the cascade against the maintainers' labels.

    python eval.py                     # jev on every issue in data/issues.jsonl
    python eval.py --system-two 200    # plus Claude and the cascade on 200 of them
    python eval.py --raw               # skip the cleaning: see how much the answer leaks

A pick is right when the issue carries that label; issues often have several
sig/* labels, and any of them counts. Questions an issue has no label for are
left out of that question's score. Writes results/eval-<stamp>.json and
results/reliability.png.
"""
import argparse
import json
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from typesafe_sdk import TypeSafeClient

import system_two
import triage

JEV_PRICE = 0.042 / 1e6        # $ per input token; output tokens are free
LABEL_QUESTIONS = [n for n, q in triage.TAXONOMY["questions"].items() if q["type"] != "noul"]
RESULTS = triage.HERE / "results"


def truth(issue):
    names = {l["name"] for l in issue["labels"]}
    return {n: {l for l in names if l.startswith(triage.TAXONOMY["questions"][n]["label_prefix"])}
            for n in LABEL_QUESTIONS}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def score(rows, key):
    """Accuracy per question for one column (key = 'jev', 'claude' or 'cascade')."""
    out = {}
    for n in LABEL_QUESTIONS:
        hits = [r[key]["labels"].get(n) in r["truth"][n] for r in rows if r["truth"][n] and r.get(key)]
        out[n] = {"n": len(hits), "accuracy": round(sum(hits) / len(hits), 3) if hits else None}
    return out


def threshold_table(rows):
    """What auto-applying above each confidence would do: share of issues, accuracy."""
    out = {}
    for n in LABEL_QUESTIONS:
        scored = [(r["jev"]["confidence"][n], r["jev"]["labels"][n] in r["truth"][n])
                  for r in rows if r["truth"][n]]
        out[n] = {}
        for t in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
            kept = [ok for c, ok in scored if c >= t]
            out[n][str(t)] = {"coverage": round(len(kept) / len(scored), 3) if scored else None,
                              "accuracy": round(sum(kept) / len(kept), 3) if kept else None}
    return out


def reliability(rows, path):
    """Confidence bucket vs how often jev was actually right. Calibrated = on the diagonal."""
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.plot([0, 1], [0, 1], ls="--", color="grey", lw=1, label="perfectly calibrated")
    for n in LABEL_QUESTIONS:
        pairs = [(r["jev"]["answers"][n]["probabilities"][r["jev"]["answers"][n]["choice"]]
                  if "choice" in r["jev"]["answers"][n] else r["jev"]["confidence"][n],
                  r["jev"]["labels"][n] in r["truth"][n]) for r in rows if r["truth"][n]]
        xs, ys, sizes = [], [], []
        for lo in [i / 10 for i in range(10)]:
            b = [ok for p, ok in pairs if lo <= p < lo + 0.1 or (lo == 0.9 and p == 1.0)]
            if len(b) >= 5:
                xs.append(lo + 0.05)
                ys.append(sum(b) / len(b))
                sizes.append(len(b))
        ax.plot(xs, ys, marker="o", label=f"{n} (n={len(pairs)})")
    ax.set(xlabel="jev's probability for its pick", ylabel="share actually right",
           xlim=(0, 1), ylim=(0, 1), title="Is jev's confidence honest? (kubernetes issues)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def run_jev(issues, raw):
    client = TypeSafeClient()
    if raw:  # the first, naive version: the body exactly as the author wrote it
        triage.clean = lambda text: (text or "")[:triage.MAX_BODY]

    def one(issue):
        try:
            return triage.triage(issue, client)
        except Exception as e:
            return {"issue": issue["number"], "error": str(e)}

    with ThreadPoolExecutor(10) as pool:   # ~10 in flight stays well under 1,200/min
        return list(pool.map(one, issues))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system-two", type=int, default=0, help="issues to also send to Claude")
    ap.add_argument("--raw", action="store_true", help="do not strip slash commands and headings")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    triage.load_env()

    issues = [json.loads(l) for l in (triage.HERE / "data" / "issues.jsonl").read_text().splitlines()]
    issues = issues[:args.limit] if args.limit else issues
    t0 = datetime.now()
    jev = run_jev(issues, args.raw)
    wall = (datetime.now() - t0).total_seconds()

    rows = [{"issue": i["number"], "truth": truth(i), "jev": j}
            for i, j in zip(issues, jev) if "error" not in j]
    errors = [j for j in jev if "error" in j]

    subset = []
    if args.system_two:
        random.seed(7)
        subset = random.sample(rows, min(args.system_two, len(rows)))
        by_number = {i["number"]: i for i in issues}
        with ThreadPoolExecutor(4) as pool:
            answers = list(pool.map(lambda r: system_two.ask(by_number[r["issue"]]), subset))
        for r, c in zip(subset, answers):
            r["claude"] = c
            # Cascade: keep jev's label unless jev routed it to System Two.
            r["cascade"] = {"labels": {n: (c["labels"].get(n, r["jev"]["labels"][n])
                                           if r["jev"]["routes"][n] == "system-two"
                                           else r["jev"]["labels"][n]) for n in LABEL_QUESTIONS}}
            r["cascade"]["escalated"] = "system-two" in r["jev"]["routes"].values()

    ms = [r["jev"]["ms"] for r in rows]
    tokens = [r["jev"]["input_tokens"] for r in rows]
    report = {
        "at": t0.isoformat(timespec="seconds"), "raw": args.raw, "issues": len(issues),
        "errors": len(errors), "wall_s": round(wall, 1),
        "jev": {"model": triage.TAXONOMY["model"], "accuracy": score(rows, "jev"),
                "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95),
                "cost_per_1000_usd": round(statistics.mean(tokens) * JEV_PRICE * 1000, 4),
                "routes": {k: sum(r["jev"]["route"] == k for r in rows)
                           for k in ("auto-apply", "suggest", "system-two", "private")},
                "thresholds": threshold_table(rows)},
    }
    if subset:
        cms = [r["claude"]["ms"] for r in subset]
        costs = [r["claude"]["cost_usd"] or 0 for r in subset]
        report["subset"] = {
            "n": len(subset),
            "jev": score(subset, "jev"),
            "claude": {"accuracy": score(subset, "claude"), "p50_ms": pct(cms, 50), "p95_ms": pct(cms, 95),
                       "cost_per_1000_usd": round(statistics.mean(costs) * 1000, 2),
                       "malformed": sum(bool(r["claude"]["errors"]) for r in subset)},
            "cascade": {"accuracy": score(subset, "cascade"),
                        "escalated_share": round(sum(r["cascade"]["escalated"] for r in subset) / len(subset), 3)},
        }

    RESULTS.mkdir(exist_ok=True)
    stamp = f"{t0:%Y%m%d-%H%M%S}{'-raw' if args.raw else ''}"
    (RESULTS / f"eval-{stamp}.json").write_text(json.dumps(report, indent=2))
    (RESULTS / f"eval-{stamp}.rows.jsonl").write_text("".join(json.dumps(r, default=list) + "\n" for r in rows))
    if not args.raw:
        reliability(rows, RESULTS / "reliability.png")

    j = report["jev"]
    print(f"{len(rows)} issues in {wall:.0f} s, {len(errors)} errors{'  (RAW: nothing stripped)' if args.raw else ''}")
    print(f"jev  p50 {j['p50_ms']} ms  p95 {j['p95_ms']} ms  ${j['cost_per_1000_usd']} per 1,000 issues")
    for n, a in j["accuracy"].items():
        print(f"  {n:<9} {a['accuracy']:.1%} of {a['n']}")
    print(f"  routes {j['routes']}")
    if subset:
        s = report["subset"]
        print(f"\non {s['n']} issues:        jev     claude  cascade")
        for n in LABEL_QUESTIONS:
            cells = [s[k]["accuracy"][n]["accuracy"] if k != "jev" else s["jev"][n]["accuracy"]
                     for k in ("jev", "claude", "cascade")]
            print(f"  {n:<20} " + "  ".join(f"{c:6.1%}" if c is not None else "   n/a" for c in cells))
        c = s["claude"]
        print(f"  claude p50 {c['p50_ms']} ms  ${c['cost_per_1000_usd']} per 1,000 (API list price)  "
              f"{c['malformed']} malformed;  cascade escalates {s['cascade']['escalated_share']:.0%}")
    print(f"wrote results/eval-{stamp}.json")


if __name__ == "__main__":
    main()
