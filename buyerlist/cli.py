"""Command line: .venv/bin/python -m buyerlist <command> ...  (run from the project folder)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import assemble, audit, debate, excel, gates, mandate, route, state, universe
from .common import StageError, run_dir
from .profiles import tasks_for, validate
from .tasks import STEP_STAGE, load_index, pending


def _open(run: Path, key: str, detail: str) -> None:
    st = state.load(run)
    state.require(run, st, key)
    state.open_stage(run, st, key, detail)
    state.save(run, st)


def _require(run: Path, key: str) -> None:
    st = state.load(run)
    state.require(run, st, key)


def _parse_rounds(spec: str, targets: list):
    from .rounds import default_schedule
    s = spec.strip().lower()
    if s == "off":
        return None
    if s == "auto":
        return default_schedule({"strategic": targets[0], "financial": targets[1]}) or None
    try:
        return [int(x) for x in s.split(",") if x.strip()]
    except ValueError:
        raise StageError(f"--rounds {spec!r}: give whole numbers separated by commas (e.g. 10,20,20,50), "
                         "'auto' or 'off'")


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def workflow_args(run: Path, step: str, tasks: list, generic: bool) -> dict:
    groups = {}
    for t in tasks:
        agent = "general-purpose" if generic else t["agent"]
        g = groups.setdefault((agent, t["model"]), [])
        g.append(t["id"])
    return {"stage": STEP_STAGE[step], "step": step, "run": str(run),
            "groups": [{"agent": a, "model": mo, "ids": ids} for (a, mo), ids in groups.items()]}


def make_tasks(run: Path, step: str, args) -> list:
    key = STEP_STAGE[step]
    _require(run, key)
    if step == "1":
        _open(run, key, "client sheet task")
        from .tasks import write_tasks
        return write_tasks(run, "1", [{"id": "client-sheet", "agent": "bl-client-researcher",
                                       "body": mandate.client_sheet_task(run),
                                       "outputs": [mandate.client_sheet_path(run)]}])
    if step == "2":
        _open(run, key, "sourcing tasks")
        st = state.load(run)
        rnd = int(st.get("topup_round", 0))
        lenses = args.lenses.split(",") if args.lenses else None
        idx = universe.sourcing_tasks(run, lenses, rnd)
        return [t for t in idx if not universe.raw_ok(Path(t["outputs"][0]))]
    if step == "2c":
        _open(run, key, "completeness critic")
        st = state.load(run)
        return universe.critic_task(run, int(st.get("topup_round", 0)), args.k)
    if step == "3":
        m = mandate.load_mandate(run)
        a = mandate.as_of(m)
        cands = universe.load_universe(run)["candidates"] + route.added_candidates(run)
        if args.ids:
            want = {x.strip() for x in args.ids.split(",") if x.strip()}
            todo = [c for c in cands if c["id"] in want]
            unknown = want - {c["id"] for c in todo}
            if unknown:
                raise StageError(f"unknown ids: {', '.join(sorted(unknown))}")
            key = "4" if all(c["id"].startswith("f-r") for c in todo) else "3"
            _open(run, key, f"re-profile {', '.join(sorted(want))}: {args.note or 'no note'}")
            return tasks_for(run, todo, "3", max(1, args.per_agent), note=args.note)
        from . import rounds
        due = rounds.reprofile_due(run)
        stale = {x.split(":")[0] for x in rounds.reprofile_problems(run, set(due))}
        todo = [c for c in universe.load_universe(run)["candidates"] if not validate(run, c, a).ok()]
        if not args.redo_invalid:
            todo = [c for c in todo if not (run / "03_profiles" / f"{c['id']}.json").exists()] or todo
        todo += [c for c in cands if c["id"] in stale and c not in todo]
        if not todo:
            return []
        _open(run, key, f"profile tasks ({len(todo)} candidates)")
        return tasks_for(run, todo, "3", m["batching"]["profiles_per_agent"], notes={i: due[i] for i in stale})
    if step == "4":
        _open(run, key, "sponsor profile tasks")
        return route.sponsor_tasks(run)
    if step == "5a":
        _open(run, key, "research tasks")
        return debate.research_tasks(run)
    if step == "5b":
        _open(run, key, "controller tasks")
        return debate.controller_tasks(run)
    if step == "5c":
        _open(run, key, "retry tasks")
        return debate.retry_tasks(run)          # already skips valid retry files
    if step == "5c2":
        _open(run, key, "controller round-2 tasks")
        return debate.controller2_tasks(run)
    if step == "5d":
        _open(run, key, "judge tasks")
        return debate.judge_tasks(run)
    if step == "7":
        _open(run, key, "audit tasks")
        return audit.prepare(run)
    raise StageError(f"unknown step {step}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="buyerlist")
    ap.add_argument("--project", help="run name or slug (default: the active run)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--project", dest="name", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--website", required=True)
    p.add_argument("--revenue", type=float, required=True)
    p.add_argument("--ebitda", type=float, required=True)
    p.add_argument("--multiple", type=float, nargs=2, required=True)
    p.add_argument("--equity", type=float, nargs=2, required=True)
    p.add_argument("--as-of")
    p.add_argument("--hq-country", default="AT")
    p.add_argument("--hq-city", default="")
    p.add_argument("--deal-type", default="majority sale (100%)")
    p.add_argument("--revenue-note", default="")
    p.add_argument("--targets", type=int, nargs=2, default=[100, 100])
    p.add_argument("--rounds", default="auto",
                   help="new names per side and round, e.g. 10,20,20,50 (the last round fills to the target); "
                        "'auto' (default) = 10,20,20,50, scaled down for lists of 50 or fewer; "
                        "'off' = one final round without analyst review")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("can-start"); p.add_argument("stage")
    p = sub.add_parser("gate"); p.add_argument("stage")
    p = sub.add_parser("regate"); p.add_argument("--from", dest="start", required=True)
    p = sub.add_parser("reopen"); p.add_argument("stage"); p.add_argument("--reason", required=True)
    p = sub.add_parser("options", help="run options: which sides are debated, retry round, financial scoring")
    p.add_argument("--debate-sides", help="strategic or strategic,financial")
    p.add_argument("--retry", choices=["on", "off"])
    p.add_argument("--financial-scoring", choices=["debate", "profile"])
    p.add_argument("--reason", default="")

    p = sub.add_parser("tasks")
    p.add_argument("action", choices=["make", "pending", "cancel"])
    p.add_argument("step", choices=sorted(STEP_STAGE))
    p.add_argument("--generic", action="store_true", help="run as general-purpose agents (role file read from the task)")
    p.add_argument("--lenses", default="")
    p.add_argument("--k", type=int, default=0, help="critic round number (default: next free)")
    p.add_argument("--redo-invalid", action="store_true")
    p.add_argument("--ids", default="", help="step 3: re-profile these ids (comma-separated), e.g. after an audit")
    p.add_argument("--note", default="", help="step 3 with --ids: reviewer note passed to the profiler")
    p.add_argument("--per-agent", type=int, default=1, help="step 3 with --ids: profiles per agent (default 1)")

    p = sub.add_parser("universe"); p.add_argument("action", choices=["merge", "topup"])
    p.add_argument("--strategic", type=int, default=0); p.add_argument("--financial", type=int, default=0)
    p.add_argument("--reason", default="")
    sub.add_parser("route")
    p = sub.add_parser("debate")
    p.add_argument("action", choices=["prepare", "merge-check", "recheck", "clean", "collect"])
    p.add_argument("--force", action="store_true")
    sub.add_parser("assemble")
    p = sub.add_parser("audit"); p.add_argument("action", choices=["prepare", "resolve", "findings"])
    p.add_argument("id", nargs="?"); p.add_argument("claim", nargs="?", type=int)
    p.add_argument("--action", dest="res_action"); p.add_argument("--note", default="")
    sub.add_parser("excel")
    sub.add_parser("validate")
    p = sub.add_parser("feedback", help="record the analyst's feedback (their own words, from the chat)")
    p.add_argument("action", choices=["add", "status", "list"])
    p.add_argument("--id", dest="fid", default="")
    p.add_argument("--status", dest="fstatus", default="", help="keep | drop | maybe | data-wrong")
    p.add_argument("--note", default="", help="the analyst's words")
    p.add_argument("--new", action="store_true", help="a name the analyst adds (with --type, --name, --website, --country)")
    p.add_argument("--type", dest="ftype", default="")
    p.add_argument("--name", dest="fname", default="")
    p.add_argument("--website", dest="fwebsite", default="")
    p.add_argument("--country", dest="fcountry", default="")
    p = sub.add_parser("round", help="analyst review rounds")
    p.add_argument("action", choices=["status", "next"])
    p.add_argument("--reason", default="")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            run = mandate.init(a.name, a.client, a.website, a.revenue, a.ebitda, a.multiple, a.equity,
                               as_of_date=a.as_of, hq_country=a.hq_country, hq_city=a.hq_city,
                               deal_type=a.deal_type, revenue_note=a.revenue_note, targets=tuple(a.targets),
                               force=a.force, rounds=_parse_rounds(a.rounds, a.targets))
            iss, _ = gates.run_gate(run, "0")
            print(f"Run created: {run}\nGate 0: {'PASS' if iss.ok() else 'FAIL'} - {iss.summary()}")
            if not iss.ok():
                print(iss.render())
            return 0 if iss.ok() else 1
        run = run_dir(a.project)
        if a.cmd == "status":
            st = state.load(run)
            if a.json:
                _print({"project": st["project"], "status": st["status"], "next": state.next_stage(st),
                        "stages": {k: st["stages"][k].get("status") for k in state.KEYS}})
            else:
                print(state.describe_run(run))
            return 0
        if a.cmd == "feedback":
            from . import rounds
            if a.action == "add":
                if a.new:
                    rec = rounds.add_name(run, a.ftype, a.fname, a.fwebsite, a.fcountry, a.fstatus, a.note)
                else:
                    rec = rounds.add_feedback(run, a.fid, a.fstatus, a.note)
                _print(rec)
            if a.action == "list":
                if rounds.closed(run):
                    print("# Feedback of the closed rounds (applied to this round):" + rounds.brief(run))
                fb = rounds.round_feedback(run, rounds.current(run))
                if fb["items"] or fb["new"]:
                    print(f"# Round {rounds.current(run)} (open):")
                for x in fb["items"]:
                    print(f"{x['id']:>7} {x['status']:<10} {x['name']}: {x['note']}")
                for x in fb["new"]:
                    print(f"  added {x['status']:<10} {x['name']} ({x['type']}, {x['website']}): {x['note']}")
            if rounds.info(run)["final"]:
                print("Final round (or a run without review rounds) - no review feedback is required.")
                return 0
            if not rounds.round_complete(run):
                st = state.load(run)
                print(f"Round {rounds.current(run)} is in progress (next: stage {state.next_stage(st)}) - feedback "
                      "is given on the round's validated list.")
                return 0
            miss = rounds.missing(run)
            print(f"Round {rounds.current(run)}: {len(miss)} new name(s) still without analyst feedback"
                  + (": " + "; ".join(f"{i} {nm} ({t})" for t, i, nm in miss) if miss else
                     " - next: python -m buyerlist round next --reason \"...\""))
            return 0
        if a.cmd == "round":
            from . import rounds
            if a.action == "next":
                if not a.reason.strip():
                    raise StageError("round next needs --reason")
                _print(rounds.next_round(run, a.reason))
                print("Next: write this round's lenses (\"round\": n, informed by the feedback), then "
                      "tasks make 2 -> sourcing workflow -> universe merge -> gate 2, and stages 3-8.")
            else:
                print(state.describe_run(run))
            return 0
        if a.cmd == "can-start":
            ok, msg = state.can_start(run, a.stage)
            print(msg)
            return 0 if ok else 1
        if a.cmd == "gate":
            iss, entry = gates.run_gate(run, a.stage)
            print(f"Gate {a.stage} ({state.NAMES[a.stage]}): {'PASS' if iss.ok() else 'FAIL'} - {iss.summary()}")
            if iss.items:
                print(iss.render())
            return 0 if iss.ok() else 1
        if a.cmd == "regate":
            for k in state.KEYS[state.index(a.start):]:
                iss, _ = gates.run_gate(run, k)
                print(f"Gate {k} ({state.NAMES[k]}): {'PASS' if iss.ok() else 'FAIL'} - {iss.summary()}")
                if not iss.ok():
                    print(iss.render(limit=40))
                    return 1
            return 0
        if a.cmd == "options":
            if not (a.debate_sides or a.retry or a.financial_scoring):
                _print(state.options(state.load(run)))
                return 0
            opts = state.set_options(run, a.reason,
                                     debate_sides=a.debate_sides.split(",") if a.debate_sides else None,
                                     retry=(a.retry == "on") if a.retry else None,
                                     financial_scoring=a.financial_scoring)
            _print(opts)
            return 0
        if a.cmd == "reopen":
            state.reopen(run, a.stage, a.reason)
            print(state.describe(state.load(run)))
            return 0
        if a.cmd == "tasks":
            if a.action == "cancel":
                from .tasks import cancel_pending
                ids = cancel_pending(run, a.step)
                st = state.load(run)
                state.log(st, "tasks cancelled", STEP_STAGE[a.step], f"{len(ids)} queued tasks of step {a.step} (user request)")
                state.save(run, st)
                print(f"Cancelled {len(ids)} task(s) of step {a.step} whose output is not written yet.")
                return 0
            if a.action == "make":
                tasks = make_tasks(run, a.step, a)
            else:
                tasks = pending(run, a.step)
            pend = [t for t in tasks if not all(Path(o).exists() for o in t["outputs"])] if a.action == "pending" else tasks
            print(f"# {len(pend)} task(s) for step {a.step} (stage {STEP_STAGE[a.step]}). Workflow args:")
            _print(workflow_args(run, a.step, pend, a.generic))
            return 0
        if a.cmd == "universe":
            if a.action == "merge":
                _open(run, "2", "universe merge")
                uni = universe.merge(run)
                iss = uni.pop("_issues")
                print(f"Universe: {uni['counts']} unique (required {universe.required_counts(run)}), "
                      f"{len(uni['duplicates'])} duplicates merged, {len(uni['excluded'])} excluded, "
                      f"{uni['skipped']} skipped")
                if iss.items:
                    print(iss.render(limit=60))
                return 0
            st = state.load(run)
            res = universe.topup(run, a.strategic, a.financial, a.reason or "shortfall")
            print(f"Top-up round {res['round']}: required now {res['required']}. Next: write new lenses or run "
                  "tasks make 2 / 2c, universe merge, gate 2, then stages 3-6 for the new names.")
            return 0
        if a.cmd == "route":
            _open(run, "4", "route")
            r = route.build(run)
            print(f"Route: {r['counts']}")
            todo = route.sponsor_tasks(run)
            if todo:
                print(f"{len(todo)} sponsor profile task(s) written - run step 4, then: route, gate 4")
                _print(workflow_args(run, "4", todo, False))
            return 0
        if a.cmd == "debate":
            key = {"prepare": "5a", "merge-check": "5a", "recheck": "5c", "clean": "5d", "collect": "5e"}[a.action]
            _open(run, key, f"debate {a.action}")
            if a.action == "prepare":
                _print(debate.prepare(run))
                print(f"{len(load_index(run, '5a'))} research tasks pending (tasks pending 5a)")
            elif a.action == "merge-check":
                _print(debate.merge_check(run, force=a.force))
            elif a.action == "recheck":
                _print(debate.recheck(run))
            elif a.action == "clean":
                _print(debate.clean(run))
                print(f"{len(load_index(run, '5d'))} judge tasks written")
            else:
                _print(debate.collect(run))
            return 0
        if a.cmd == "assemble":
            _open(run, "6", "assemble")
            _print(assemble.build(run))
            return 0
        if a.cmd == "audit":
            if a.action == "prepare":
                _open(run, "7", "audit prepare")
                idx = audit.prepare(run)
                _print(workflow_args(run, "7", idx, False))
            elif a.action == "resolve":
                _open(run, "7", "audit resolve")
                audit.resolve(run, a.id, a.claim, a.res_action or "", a.note)
                print("resolved")
            else:
                for f in audit.findings(run):
                    if f.get("verdict") != "confirmed":
                        print(f"{f.get('verdict'):>12} {f.get('id')}#{f.get('claim')}: {f.get('detail')} | "
                              f"correct: {f.get('correct_value')} | {f.get('source')}")
            return 0
        if a.cmd == "excel":
            _open(run, "8", "excel")
            print(f"Written: {excel.write(run)}")
            return 0
        if a.cmd == "validate":
            iss, entry = gates.run_gate(run, "8")
            ok = iss.ok()
            print(f"Validation: {'PASS' if ok else 'FAIL'} - {iss.summary()}")
            if iss.items:
                print(iss.render(limit=80))
            if ok:
                print(f"final.lock written for {excel.xlsx_path(run)}")
            return 0 if ok else 1
    except StageError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
