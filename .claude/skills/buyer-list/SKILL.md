---
name: buyer-list
description: "Build a sell-side buyer longlist (strategic + financial investors) in the reference longlist format with the gated pipeline, in analyst review rounds (default 10, 20, 20, 50 new names per side): client sheet, sourcing, profiles, revenue gate, PE reroute and platform rule, advocate/critic/Controller/judge debates, audit, Excel, analyst feedback. Use whenever the user gives a mandated company and asks for a buyer list / longlist, or gives feedback on a round."
---

# Buyer list - runbook (binding rules: CLAUDE.md)

`BL` below means: `.venv/bin/python -m buyerlist` run from the project folder (one command per
Bash call, nothing chained - the guard hook only trusts plain CLI calls).
`RUN TASKS <step>` means: run `BL tasks make <step>` and pass the printed JSON as `args` to the
Workflow tool with `scriptPath: ".claude/workflows/bl-tasks.js"` (background is fine; write
"WAITING: ..." if you end the turn while it runs). Afterwards check `BL tasks pending <step>`;
re-run only what is pending (use `--generic` if the workflow reports "agent type not found" - agent
files added during a session are only loaded by the next session).
Web search is a shared, rate-limited resource across all agents: task files ask agents to open known
pages directly and to search sparingly. If many agents report that search was unavailable, re-run
their tasks later in smaller batches rather than accepting thin research.

## Rounds (default)
A run is built in review rounds: 10, then 20, then 20, then the final 50 new names per side
(cumulative 10 / 30 / 50 / 100). Every round runs steps 2-8 below for its new names and ends with
a validated Excel (`output/<date>_Longlist <name> - round n of 4.xlsx`). After a review round:
1. Send the Excel and present the round's NEW names in chat, per side: rank, fit, For / Against,
   key figures (revenue / ticket), ownership, website. Ask the analyst to research them and give
   feedback on every one: keep / drop / maybe / data-wrong, with their reason, plus any names
   they want added. End the message with "Feedback needed: <round n - the names>".
2. Record ONLY the analyst's own words, one call per name:
   `BL feedback add --id <id> --status keep|drop|maybe|data-wrong --note "<analyst's words>"`;
   names they add: `BL feedback add --new --type strategic|financial --name "<name>"
   --website <url> --country <ISO-2> --status keep|maybe --note "<analyst's words>"`.
   Never invent, complete or guess feedback; if a name is not covered, ask. Drop and data-wrong
   need the analyst's reason. `BL feedback status` lists what is still missing.
3. When every new name has feedback: `BL round next --reason "<analyst feedback round n recorded>"`.
   It closes the round, writes the analyst's added names as a raw file `AN-r<top-up counter>.json`,
   and reopens stage 2 for the next round (n+1, printed by the command).
4. Read all feedback so far (`BL feedback list`) and write the new round's lenses from it: add at
   least 2 strategic and 2 financial lenses with `"round": <n+1>` (the new round's number) to
   lenses.json - more like the kept names and their reasons, away from the dropped ones' problems,
   filling gaps the analyst named. `BL tasks make 2` then sources only the new round's lenses, and
   every task carries the feedback (gate 2 checks the digest).
5. Continue with steps 2-8. Data-wrong names are re-profiled with the analyst's note
   (`BL tasks make 3` includes them; gates 3/4 check it). Dropped names never return; earlier
   names that were not dropped stay; analyst-added keeps get a place. `BL gate 6` warns when a
   hard rule blocks the analyst's wish - tell the analyst.
The final round fills the list to the target; after it, report as in step 8.
`BL init ... --rounds off` builds the whole list in one round (no review).

## 0. Mandate
`BL init --project "<name>" --client "<legal name>" --website <url> --revenue <EURm> --ebitda <EURm>
 --multiple <lo> <hi> --equity <lo> <hi> --hq-country AT --hq-city Vienna --as-of <YYYY-MM-DD>`
(EV = EBITDA x multiple; equity cheque = EV minus typical debt; `--rounds auto` is the default:
10,20,20,50, scaled down for lists of 50 or fewer. Gate 0 runs automatically.)
For a run created before 2026-10-28 that is re-run: investor holdings have no overlap class yet -
gate 6 names them; re-profile with `BL tasks make 3 --ids <ids> --per-agent 3 --note "..."`.

## 1. Client sheet
RUN TASKS 1 -> `BL gate 1`. Fix by re-running the task, never by writing facts yourself.

## 2. Universe
1. Write `runs/<slug>/02_universe/lenses.json`: >= 5 strategic and >= 4 financial lenses, each
   `{"id": "S01", "type": "strategic", "title": "...", "brief": "<who, where, why>", "min": 15,
   "round": 1}`. Round 1 (10 + 10) needs the closest fits first: same-service peers in the
   client's country and neighbours, regional platforms of the client's segments; specialist
   digital-infrastructure / DACH mid-market funds. Later rounds widen: adjacent segments with a
   concrete route; global players present in the client's city; utilities/telcos; IT-services/MSP
   consolidators; pan-European mid-market tech/services, family offices and holdings, sector
   specialists, US/UK sponsors with European digital infra.
2. RUN TASKS 2 -> `BL universe merge`.
3. Completeness critic: `BL tasks make 2c --k 1` -> run -> `BL universe merge`. Repeat (k=2, 3 ...)
   until the counts in `BL gate 2` are met AND a critic round adds few new names.
4. `BL gate 2`.

## 3. Profiles
RUN TASKS 3 -> `BL gate 3`. Repeat RUN TASKS 3 for profiles with errors
(`BL tasks make 3 --redo-invalid`) until the gate passes.

## 4. Route
`BL route` -> if sponsor tasks are printed: RUN TASKS 4 -> `BL route` again -> `BL gate 4`.

## 5. Debates (both sides together)
- 5a: `BL debate prepare` -> RUN TASKS 5a -> `BL debate merge-check` -> `BL gate 5a`
  (structural errors = a part file is missing/incomplete: RUN TASKS 5a again).
- 5b: RUN TASKS 5b (Controllers) -> `BL gate 5b`.
- 5c: RUN TASKS 5c (retries; may be 0) -> `BL debate recheck` -> RUN TASKS 5c2 (Controller round 2)
  -> `BL gate 5c`.
- 5d: `BL debate clean` -> RUN TASKS 5d (judges) -> `BL gate 5d`.
- 5e: `BL debate collect` -> `BL gate 5e`.
Report one line per side: points kept/dropped, companies retried.

## 6. Assemble
`BL assemble` -> `BL gate 6`. If short: `BL universe topup --strategic <n> --financial <n> --reason "..."`
then repeat 2 (new lenses / critic rounds) -> 3 -> 4 -> 5 (prepare only adds new batches) -> 6.
PE-owned strategics appear under a sponsor only when they qualify as its platform (CLAUDE.md);
the PE reroutes and Excluded sheets give the reason for the others.

## 7. Audit
`BL audit prepare` -> run the printed args with the bl-tasks workflow -> `BL audit findings`.
In a review round every new row is audited. For every "wrong" finding:
1. fix upstream: `BL tasks make 3 --ids <id> --note "<auditor finding + source>"` -> RUN the task
   (this re-opens stage 3 for that profile; `--per-agent 3` batches a larger re-check);
2. walk the chain again: `BL regate --from 3`; when a gate fails, run the command it names and
   continue: `BL route` (4), `BL debate prepare` (5a), `BL debate clean` + RUN TASKS 5d (5d),
   `BL debate collect` (5e), `BL assemble` -> `BL gate 6` (top up if it reports a shortfall);
3. `BL audit prepare` again (only changed / new rows are re-audited) -> run it, then
   `BL audit resolve <id> <claim> --action fixed|accepted|false_alarm --note "..."` for any
   remaining "wrong" -> `BL gate 7`.

## 8. Excel
`BL excel` -> `BL validate` (writes final.lock). Then send the file (SendUserFile) and report:
counts, rank split, number of PE platforms listed / not listed, debate kept/dropped, audit result,
open issues, and "Validation: PASS - ...". After a review round, continue with "Rounds" above
(present the new names, "Feedback needed: ...").
