#!/usr/bin/env python3
"""System Two: the same questions to a frontier LLM, through Claude Code headless.

    python system_two.py 142439

Runs on a Claude subscription through `claude -p`, no API key. The answer is
checked the same way as jev's, because an LLM's answer can be malformed.
"""
import argparse
import json
import subprocess
import time

import triage


def schema():
    props = {}
    for name, q in triage.TAXONOMY["questions"].items():
        if q["type"] in ("choice", "score"):
            props[name] = {"type": "string", "enum": list(q["criteria"])}
            props[f"{name}_confidence"] = {"type": "number", "minimum": 0, "maximum": 1}
        else:
            props[name] = {"type": "boolean"}
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


def prompt(issue):
    lines = [f"You triage GitHub issues for {triage.TAXONOMY['repo']}. Answer every field.",
             "For each pick, give your confidence from 0 to 1.", ""]
    for name, q in triage.TAXONOMY["questions"].items():
        lines.append(f"{name}: {q['instructions']}")
        for option, text in q.get("criteria", {}).items():
            lines.append(f"  - {option}: {text}")
    state = triage.state_of(issue)
    lines += ["", f"Issue title: {state['title']}", "", state["body"]]
    return "\n".join(lines)


def check(answer):
    """The type checks bench.py runs on jev, applied to the LLM."""
    errs = []
    for name, q in triage.TAXONOMY["questions"].items():
        if name not in answer:
            errs.append(f"{name}: missing")
        elif q["type"] == "noul" and not isinstance(answer[name], bool):
            errs.append(f"{name}: not a boolean")
        elif q["type"] != "noul" and answer[name] not in q["criteria"]:
            errs.append(f"{name}: {answer[name]!r} is not an option")
    return errs


def ask(issue, model="sonnet"):
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--json-schema", json.dumps(schema()), "--tools", "",
           "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    t0 = time.perf_counter()
    out = subprocess.run(cmd, input=prompt(issue), capture_output=True, text=True,
                         encoding="utf-8", timeout=180)
    ms = (time.perf_counter() - t0) * 1000
    try:
        res = json.loads(out.stdout)
    except json.JSONDecodeError:
        res = {"is_error": True, "result": (out.stderr or out.stdout)[-300:]}
    answer = res.get("structured_output") or {}
    cfg = triage.TAXONOMY["questions"]
    labels = {n: cfg[n]["label_prefix"] + answer[n] for n in cfg
              if cfg[n]["type"] != "noul" and answer.get(n) in cfg[n]["criteria"]}
    errors = check(answer) + ([f"cli: {res.get('result')}"] if out.returncode or res.get("is_error") else [])
    # ms includes the CLI starting up; api_ms is the model alone
    return {"issue": issue["number"], "model": f"claude-{model}", "ms": round(ms),
            "api_ms": res.get("duration_api_ms"), "cost_usd": res.get("total_cost_usd"),
            "labels": labels, "answer": answer, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("issue")
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()
    r = ask(triage.fetch_issue(args.issue), args.model)
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
