# Buyer-List

A Claude Code project that builds sell-side **buyer longlists**: strategic buyers and financial
investors for a company being sold, written to an Excel longlist with backup sheets.

- A gated Python pipeline (`buyerlist/`) runs each stage in turn: client sheet, sourcing,
  profiles, revenue gate, PE reroute, advocate / critic / Controller / judge debates, audit and
  Excel. Each stage must pass its gate before the next one can start.
- Claude Code hooks (`.claude/hooks/`) block skipped steps, hand edits of gated files, and an
  Excel that hasn't been validated.
- The list is built in review rounds (10, 20, 20, then the final 50 new names per side). The
  analyst's feedback on each round shapes the next round.

Start with [START HERE.md](START HERE.md). The rules are in [CLAUDE.md](CLAUDE.md), and the
step-by-step runbook is `.claude/skills/buyer-list/SKILL.md`.

## Setup
```bash
python3 -m venv .venv
```
```bash
.venv/bin/pip install -r requirements.txt
```

## Tests
```bash
.venv/bin/python -m unittest discover tests
```

Run data (`runs/`) and finished lists (`output/`, `*.xlsx`) are client data and are never
committed.
