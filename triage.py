#!/usr/bin/env python3
"""Triage one GitHub issue with a single jev call.

    python triage.py https://github.com/kubernetes/kubernetes/issues/142439
    python triage.py 142439 --json

Reads the issue (read-only), strips what would give the answer away, asks
every question in taxonomy.yml in one system_one call, and routes on confidence.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from typesafe_sdk import Choice, Noul, RetryPolicy, Score, TypeSafeClient

HERE = Path(__file__).parent
TAXONOMY = yaml.safe_load((HERE / "taxonomy.yml").read_text())
MAX_BODY = 8000  # characters; long logs add cost, not signal


def load_env():
    """Read .env next to this file, so the key never has to be typed on camera."""
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("TYPESAFE_API_KEY", os.environ.get("JEV_API_KEY", ""))


def github(path):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = requests.get(f"https://api.github.com/{path}", headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_issue(ref):
    number = re.search(r"(\d+)/?$", ref).group(1)
    url = re.search(r"github\.com/([^/]+/[^/]+)/issues", ref)
    return github(f"repos/{url.group(1) if url else TAXONOMY['repo']}/issues/{number}")


def clean(text):
    """Remove everything that states the answer instead of describing the problem:
    slash commands (/kind bug, /sig node), template headings (the bug template's
    headings alone say kind/bug), HTML comments, and very long bodies."""
    text = re.sub(r"<!--.*?-->", "", (text or "").lstrip("\ufeff"), flags=re.S)
    text = re.sub(r"^\s*/[a-z][\w-]*.*$", "", text, flags=re.M | re.I)
    text = re.sub(r"^\s*#{1,6} .*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY]


def state_of(issue):
    """The state jev reads. Labels are left out on purpose: they are the answer."""
    return {"title": issue["title"], "body": clean(issue.get("body"))}


def questions():
    out = {}
    for name, q in TAXONOMY["questions"].items():
        if q["type"] == "choice":
            out[name] = Choice(instructions=q["instructions"], criteria=q["criteria"])
        elif q["type"] == "score":
            out[name] = Score(instructions=q["instructions"], criteria=list(q["criteria"].values()))
        else:
            out[name] = Noul(instructions=q["instructions"])
    return out


def decide(answers):
    """Typed answers -> labels, flags and a route."""
    cfg, route = TAXONOMY["route"], TAXONOMY["questions"]
    labels, confidence, flags = {}, {}, {}
    for name, a in answers.items():
        q = route[name]
        if q["type"] == "choice":
            labels[name] = q["label_prefix"] + a["choice"]
            confidence[name] = a["confidence"]
        elif q["type"] == "score":
            levels = list(q["criteria"])
            labels[name] = q["label_prefix"] + levels[round(a["score"])]
            confidence[name] = a["confidence"]
        else:
            flags[name] = a["noul"] >= cfg["noul_yes"]

    # Each label routes on its own confidence: kind is often certain, priority rarely.
    routes = {}
    for name, c in confidence.items():
        may_apply = route[name].get("auto_apply", True) and not flags.get("is_prompt_injection")
        if c >= cfg["auto_apply"] and may_apply:
            routes[name] = "auto-apply"
        elif c >= cfg["suggest"]:
            routes[name] = "suggest"
        else:
            routes[name] = "system-two"
    if flags.get("is_security_sensitive"):
        action = "private"      # never discuss a vulnerability in public: alert maintainers
    elif "system-two" in routes.values():
        action = "system-two"
    elif "suggest" in routes.values():
        action = "suggest"
    else:
        action = "auto-apply"
    return {"labels": labels, "confidence": confidence, "routes": routes,
            "flags": flags, "route": action}


def triage(issue, client=None):
    client = client or TypeSafeClient(retry=RetryPolicy(max_retries=2))
    t0 = time.perf_counter()
    resp = client.system_one(state=state_of(issue), questions=questions(),
                             model=TAXONOMY["model"])
    ms = (time.perf_counter() - t0) * 1000
    raw = json.loads(resp.model_dump_json())
    return {"issue": issue["number"], "title": issue["title"], "ms": round(ms),
            "model": raw["model"], "input_tokens": raw["usage"]["input_tokens"],
            **decide(raw["answers"]), "answers": raw["answers"]}


def show(r):
    print(f"#{r['issue']}  {r['title']}")
    print(f"jev {r['model']}  {r['ms']} ms  {r['input_tokens']} input tokens\n")
    for name, label in r["labels"].items():
        print(f"  {name:<10} {label:<30} confidence {r['confidence'][name]:.2f}  {r['routes'][name]}")
    for name, yes in r["flags"].items():
        print(f"  {name:<22} {'yes' if yes else 'no ':<4} p={r['answers'][name]['noul']:.2f}")
    print(f"\n  route: {r['route']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("issue", help="issue URL or number")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("Set TYPESAFE_API_KEY (or put it in .env)")
    r = triage(fetch_issue(args.issue))
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        show(r)


if __name__ == "__main__":
    main()
