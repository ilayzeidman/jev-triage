#!/usr/bin/env python3
"""The bot inside GitHub Actions: triage the issue that was just opened.

TRIAGE_MODE=shadow  comment the suggested labels, change nothing (the default)
TRIAGE_MODE=enforce apply the confident labels, comment the rest for a human

A security-sensitive issue gets no public comment at all.
"""
import json
import os

import requests

import triage


def gh(method, path, **kw):
    r = requests.request(method, f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
                         headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                  "Accept": "application/vnd.github+json"}, timeout=20, **kw)
    r.raise_for_status()


def main():
    issue = json.load(open(os.environ["GITHUB_EVENT_PATH"]))["issue"]
    mode = os.environ.get("TRIAGE_MODE", "shadow")
    r = triage.triage(issue)
    print(json.dumps({k: r[k] for k in ("labels", "confidence", "routes", "flags", "route", "ms")}))
    if r["route"] == "private":
        return  # alerting maintainers privately is left to your setup

    apply = [l for n, l in r["labels"].items() if mode == "enforce" and r["routes"][n] == "auto-apply"]
    if apply:
        gh("POST", f"issues/{issue['number']}/labels", json={"labels": apply})
    rows = "\n".join(f"| `{l}` | {r['confidence'][n]:.2f} | {'applied' if l in apply else r['routes'][n]} |"
                     for n, l in r["labels"].items())
    flags = ", ".join(n for n, yes in r["flags"].items() if yes) or "none"
    body = (f"jev triage ({r['model']}, {r['ms']} ms, mode `{mode}`)\n\n"
            f"| label | confidence | action |\n|---|---|---|\n{rows}\n\nflags: {flags}")
    gh("POST", f"issues/{issue['number']}/comments", json={"body": body})


if __name__ == "__main__":
    main()
