# START HERE – Buyer-List tool

## What it does
Give Claude a mandated company (name, website, revenue, EBITDA) and it builds a sell-side
**longlist of strategic buyers and financial investors** in the reference longlist format
(sheets "Strategic" and "Financial"). By default the list has 100 of each and is written to
`output/<date>_Longlist <name>.xlsx`.

## How to run
1. Open this folder in the Claude app (Code tab).
2. Say e.g.: *"Buyer list for <company> (<website>), revenue EUR 30m, EBITDA EUR 10m."*
3. Claude follows `.claude/skills/buyer-list/SKILL.md`. Python gates and hooks force every step:
   client research, sourcing, profiles, revenue gate, PE reroute, both debates, audit, Excel.

## Working in rounds with the analyst
The list is built in four rounds per side: **10, then 20, then 20, then the final 50 new names**.
1. Claude runs the full process for the round and sends a validated Excel
   (`... - round n of 4.xlsx`). In chat it lists the round's new names with fit, reasons and key
   figures.
2. You research each name and tell Claude in chat: **keep**, **drop**, **maybe** or
   **data wrong**, each with your reason. You can also name buyers that are missing.
3. Claude records your words as they are. It does not invent feedback, and it asks about any
   name you didn't cover. The next round can't start until every new name has feedback.
4. The next round applies your feedback:
   - Dropped names never come back.
   - Earlier names you didn't drop stay on the list.
   - Names you added are researched, and the ones you marked keep get a place.
   - Wrong data is re-researched with your note.
   - Your reasons steer the search for new buyers: more like the ones you kept, fewer like the ones you dropped.
5. The final round fills each side to 100. The Feedback column shows your earlier feedback.

To build the whole list in one go without review rounds, start with `--rounds off`.

## The rules in one minute
- **Strategic buyers** need proven revenue above the target's. Their fit comes from the
  Business-Dev-2.0 debate: an advocate and a critic research with verbatim quotes, a Controller
  checks every quote, and a judge scores. Weights: product 50%, customer 20%, geography 20%,
  value chain 5%, synergy 5%.
- **PE-owned strategics** leave the Strategic sheet. One appears on the Financial sheet as its
  PE owner's "▶ PLATFORM" (light-blue row) only when its fit is ≥ 3.0 and it offers the same
  product category (product score ≥ 4). If the PE owner itself fits poorly on ticket, geography
  and criteria (own fit below 3.0), the platform needs Rank 1 (fit ≥ 3.8) and the same products
  (product 5).
- **Financial investors** use the same debate, adapted: ticket size 35%, geography 25%,
  investment criteria 30%, portfolio 10%. Only holdings with the same products or product
  category as the target count.
- **Ranks:** 1 = fit ≥ 3.8, 2 = 3.0–3.7, 3 = 2.0–2.9. Anything below 2.0 is not listed.

## Auction simulation (separate tool)
Once a list is validated, Claude can simulate the sealed-bid sale process for it: say e.g.
*"Run the auction simulation for <project>."* It shows the expected price against the EV range,
the likely winners, which buyers drive the price, and how all of this changes when buyers bid
irrationally (a higher fit means less mispricing). The simulator lives in `../Auction Theory`
(see its `START HERE.md` and `MODEL.md`). It only reads the run, changes nothing here, and writes
its Excel / HTML report to `Auction Theory/output/`.

## Folder
| Item | What it is |
|---|---|
| `CLAUDE.md` | The rulebook |
| `.claude/skills/buyer-list/SKILL.md` | Step-by-step runbook |
| `.claude/skills/auction-simulation/SKILL.md` | How to run the auction simulator (`../Auction Theory`) on a finished run |
| `.claude/agents/` | Agent roles: debate agents (`bl-*`, `fi-*`), sourcer, profilers, auditor |
| `.claude/hooks/` | Hooks that block skipped steps, hand edits of gated files, unvalidated Excel, and a review round that ends without asking for feedback |
| `.claude/workflows/bl-tasks.js` | Runs a step's agent tasks in parallel |
| `buyerlist/` | Python package: stage gates, source checks, scoring, rounds and feedback, Excel writer and validator |
| `runs/<project>/` | Everything behind a list (sources, briefs, verdicts, feedback). Confidential, do not share |
| `output/` | The finished Excel files |

Check where a run stands: `.venv/bin/python -m buyerlist status`.
Run the tests: `.venv/bin/python -m unittest discover tests`.
