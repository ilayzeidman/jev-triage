# jev-triage

A GitHub issue triage bot built on [jev](https://typesafe.ai), TypeSafe AI's System One model.
One typed call per issue answers every question at once (kind, SIG, priority, five yes/no flags),
each label is applied, suggested or escalated on its own confidence, and only the unsure ones
go to an LLM. Built on `kubernetes/kubernetes`, strictly read-only.

Companion code for the episode "jev from zero to production".

## Run it

```
pip install -r requirements.txt
cp .env.example .env          # put your TYPESAFE_API_KEY in it
python triage.py https://github.com/kubernetes/kubernetes/issues/142430
```

```
#142430  Leverage remote partitions for exclusive CPUs (Linux 6.7+)
jev jev-1.13.0  768 ms  1856 input tokens

  kind       kind/feature                   confidence 0.99  auto-apply
  sig        sig/node                       confidence 1.00  auto-apply
  priority   priority/important-longterm    confidence 0.48  system-two
  ...
```

| file | what it does |
|---|---|
| `taxonomy.yml` | the labels as questions, the pinned model, the routing thresholds |
| `triage.py` | one issue: clean the text, one jev call, route each label |
| `system_two.py` | the same questions to Claude through `claude -p` (a Claude subscription, no API key) |
| `fetch.py` / `eval.py` | recent issues with their real labels; accuracy, latency, cost, calibration, the cascade |
| `shadow.py` / `score.py` | predict new issues before maintainers label them, score later |
| `.github/workflows/triage.yml` | the bot on your own repo |

## Point it at your repo

Edit `taxonomy.yml`: `repo`, then one Choice per label family and its options, with a one-line
description per option. Descriptions matter: jev reads them as the criteria.

## Honest-eval notes

- Kubernetes authors write `/kind bug` and `/sig node` in the body, and the bug template's
  headings alone say "bug". `triage.clean()` strips both; `python eval.py --raw` shows the gap.
- Eval issues are recent (created since May 2026), so jev cannot have trained on them.
- An issue can carry several `sig/*` labels; a pick counts as right if it is any of them.
- Ground truth for kind and SIG is mostly the author's own slash command, applied by a bot.
