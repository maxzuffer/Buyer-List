"""PreToolUse hook (Workflow|Agent|SendUserFile|Artifact): force the stage order.

- Workflow "bl-tasks" (the generic task runner): its args.stage must be allowed to start
  (every earlier stage passed, outputs unchanged), and every task id must have a task
  file written by `python -m buyerlist tasks make <step>`.
- Agent with a buyer-list role (bl-*, fi-*): the role's stage must be allowed to start.
- SendUserFile / Artifact with an output/*.xlsx: only after `python -m buyerlist validate`
  passed and the file still matches final.lock.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooklib import ROOT, active_run, deny, read_input  # noqa: E402

ROLE_STAGES = {"bl-client-researcher": ["1"], "bl-sourcer": ["2"], "bl-universe-critic": ["2"],
               "bl-profiler-strategic": ["3", "4"], "bl-profiler-financial": ["3", "4"],
               "bl-advocate": ["5a", "5c"], "bl-critic": ["5a", "5c"], "fi-advocate": ["5a", "5c"],
               "fi-critic": ["5a", "5c"], "bl-controller": ["5b", "5c"], "bl-judge": ["5d"],
               "fi-judge": ["5d"], "bl-auditor": ["7"]}
META_RE = re.compile(r"export\s+const\s+meta\s*=\s*\{.*?\bname\s*:\s*['\"]([^'\"]+)['\"]", re.S)


def can_start(run: Path, stage: str):
    from buyerlist import state
    return state.can_start(run, stage)


def check_workflow(ti: dict) -> None:
    script = ti.get("script") or ""
    if not script and ti.get("scriptPath"):
        try:
            script = Path(ti["scriptPath"]).read_text(encoding="utf-8")
        except Exception:
            script = ""
    m = META_RE.search(script)
    name = m.group(1) if m else (ti.get("name") or "")
    if not name.startswith("bl-"):
        return
    args = ti.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = None
    if not isinstance(args, dict) or not args.get("stage") or not args.get("run") or not args.get("step"):
        deny("Blocked by require_stage_order: buyer-list workflows need args {stage, step, run, groups} as "
             "printed by: python -m buyerlist tasks make <step>")
    run = Path(args["run"])
    if not (run / "state.json").exists():
        deny(f"Blocked by require_stage_order: {run} is not a buyer-list run")
    ok, msg = can_start(run, args["stage"])
    if not ok:
        deny(f"Blocked by require_stage_order: {msg}")
    tdir = run / "tasks" / args["step"]
    missing = [i for g in args.get("groups", []) for i in g.get("ids", []) if not (tdir / f"{i}.md").exists()]
    if missing:
        deny(f"Blocked by require_stage_order: no task file for {', '.join(missing[:5])} in {tdir} - "
             "write tasks with: python -m buyerlist tasks make <step>")


def check_agent(ti: dict) -> None:
    role = ti.get("subagent_type") or ""
    stages = ROLE_STAGES.get(role)
    if not stages:
        return
    run, _ = active_run()
    if run is None:
        deny(f"Blocked by require_stage_order: {role} belongs to a buyer-list run, but no run is active "
             "(python -m buyerlist init ...)")
    msgs = []
    for s in stages:
        ok, msg = can_start(run, s)
        if ok:
            return
        msgs.append(msg)
    deny("Blocked by require_stage_order: " + " / ".join(msgs))


def check_delivery(tool: str, ti: dict) -> None:
    files = []
    if tool == "SendUserFile":
        files = ti.get("files") or []
    elif tool == "Artifact":
        files = [ti.get("file_path") or ""] + list(ti.get("file_paths") or [])
    xl = [f for f in files if str(f).lower().endswith(".xlsx")]
    if not xl:
        return
    run, _ = active_run()
    if run is None:
        return
    from buyerlist.excel import lock_ok, xlsx_path
    ok, msg = lock_ok(run)
    target = xlsx_path(run).resolve()
    for f in xl:
        p = Path(f)
        p = (ROOT / p) if not p.is_absolute() else p
        if p.resolve() == target and not ok:
            deny(f"Blocked by require_stage_order: {p.name} is not validated ({msg}). "
                 "Run: python -m buyerlist validate")


def main() -> None:
    data = read_input()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    try:
        if tool == "Workflow":
            check_workflow(ti)
        elif tool == "Agent":
            check_agent(ti)
        elif tool in ("SendUserFile", "Artifact"):
            check_delivery(tool, ti)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"require_stage_order internal error (allowed): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
