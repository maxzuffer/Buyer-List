---
name: auction-simulation
description: "Simulate the sealed-bid sale process for a buyer list: Monte Carlo over levels of irrational bidding (a higher fit means less mispricing) -> expected price range vs the EV range, likely winners, strategic vs financial win share, winner's curse, price drivers, value of more bidders. Use when the user asks for an auction / bid / price simulation, expected price, likely winners or competitive tension for a buyer-list run."
---

# Auction simulation (separate tool in ../Auction Theory - read-only on this project)

The simulator is the package `auction_sim` in the sibling folder `Auction Theory/` (model and
assumptions: `Auction Theory/MODEL.md`). It READS this project - `runs/<slug>/06_list_data.json`,
`state.json`, `08_final.lock` and the longlist Excel - and never writes here. Its reports go to
`Auction Theory/output/`. It is not a pipeline stage: it changes no rank, file or gate.

`AS` below means: `"../Auction Theory/.venv/bin/python" "../Auction Theory/run_auction.py"`
(one command per Bash call, run from this project folder).

## Steps
1. `AS runs` - lists the runs and whether each list is validated. Simulate a validated run; an
   unvalidated one is marked "provisional" in every report.
2. `AS run --project <slug>` - 10,000 simulations at irrationality levels 0-50 %, competition
   sweep, Excel + HTML + JSON report. Useful options:
   - `--expected-strategic 2 --expected-financial 4` - expected binding bids per side (default 3 + 3)
   - `--status-from "<working copy>.xlsx"` - the deal team's Status column (declined -> out,
     offer/IOI/NBO -> bids, NDA/IM/data room -> more likely). The Feedback column is not read (it
     holds the analyst's review-round notes). Use a working copy in the project root, never a file
     under `output/` or `runs/` (the guard hook blocks python on those paths).
   - `--only "s-035,f-031,..." --all-participate --name final-round` - a named final round
   - `--levels 0,0.1,0.2,0.3`, `--sims 20000`, `--reserve none|<EURm>`, `--config <json>` - see MODEL.md
3. Report to the user: the list's validation in the Buyer-List form ("Validation: PASS - n errors,
   n warnings (...)", from the `Input:` line), the expected price at the rational level and at the
   reference level vs the EV range, deal probability, the most likely winners, the price drivers,
   and the files written. Send the HTML/Excel from `Auction Theory/output/` with SendUserFile.

## Rules
- Results are model outputs on labelled assumptions (participation, uplift, irrationality); say so
  and point to the Method sheet. They are not research facts and never go into the longlist.
- Never copy auction output into `runs/` or `output/` here, and never edit this project's files
  to change a simulation - change the simulator's `--config` instead.
- Client data: the reports are confidential, like the run artefacts.
