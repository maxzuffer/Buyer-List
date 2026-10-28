"""Stop hook: a buyer-list run that this session worked on in this turn must reach the end.

"Worked on" = this turn called the buyerlist CLI, ran a bl-tasks workflow, or wrote a file
under the run folder (other sessions are never blocked by a file timestamp).
Blocks ending such a turn while the active run is not complete - naming the next stage
and command - unless the FINAL message says "PAUSED: <reason>", "stopped at the user's
request", or "WAITING: <what is running>" while a background workflow launched in this
conversation has not reported completion. When the run is complete, the final message
must report "Validation: PASS ..." and the Excel must still match final.lock. After a
review round (not the final one) the final message must also ask the analyst for
feedback ("Feedback needed: ..."); once all feedback is recorded, the next round starts
(python -m buyerlist round next) unless the message says "PAUSED: <reason>".
(Modelled on Business-Dev-2.0 require_debate.py / require_data_check.py.)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooklib import active_run, blocks, block_stop, current_turn, load_transcript, read_input  # noqa: E402

TASK_ID_RE = re.compile(r"Task ID:\s*([A-Za-z0-9_-]+)")
DONE_RE = re.compile(r"<task-id>\s*([A-Za-z0-9_-]+)\s*</task-id>")


def final_text(turn: list) -> str:
    for e in reversed(turn):
        if e.get("type") == "assistant":
            t = "".join(b.get("text", "") for b in blocks(e) if b.get("type") == "text")
            if t.strip():
                return t
    return ""


def running_workflows(path: str) -> set:
    """Background workflows whose launch was seen but whose completion was not (any entry shape)."""
    launched, done = set(), set()
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if "launched in background" in line:
                    launched |= set(TASK_ID_RE.findall(line.replace("\\n", "\n")))
                if "task-id" in line:
                    done |= set(DONE_RE.findall(line.replace("\\n", "\n").replace('\\"', '"')))
    except Exception:
        pass
    return launched - done


def ran_validate(turn: list) -> bool:
    return any(b.get("type") == "tool_use" and b.get("name") == "Bash"
               and re.search(r"-m buyerlist\b.*\bvalidate\b", str((b.get("input") or {}).get("command", "")))
               for e in turn if e.get("type") == "assistant" for b in blocks(e))


def touched_run(turn: list, run: Path, slug: str, project: str) -> bool:
    runs_path = str(run)
    for e in turn:
        if e.get("type") != "assistant":
            continue
        for b in blocks(e):
            if b.get("type") != "tool_use":
                continue
            name, ti = b.get("name"), b.get("input") or {}
            if name == "Bash":
                cmd = str(ti.get("command", ""))
                if "-m buyerlist" in cmd:
                    m = re.search(r"--project\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))", cmd)
                    proj = next((g for g in (m.groups() if m else ()) if g), None)
                    if proj is None or proj in (slug, project) or proj.lower().replace(" ", "-") == slug:
                        return True
            elif name == "Workflow":
                if runs_path in json.dumps(ti.get("args", "")):
                    return True
            elif name in ("Write", "Edit", "MultiEdit"):
                if str(ti.get("file_path", "")).startswith(runs_path):
                    return True
    return False


def main() -> None:
    data = read_input()
    try:
        run, st = active_run()
        if run is None:
            return
        tpath = data.get("transcript_path", "")
        turn = current_turn(load_transcript(tpath))
        text = final_text(turn)
        low = text.lower().replace("’", "'")
        from buyerlist import state as S
        from buyerlist.excel import lock_ok, xlsx_path
        cur = S.load(run)
        touched = touched_run(turn, run, cur.get("slug", run.name), cur.get("project", ""))
        mentions_xlsx = xlsx_path(run).name.lower() in low
        if not touched and not mentions_xlsx:
            return
        nxt = S.next_stage(cur)
        if nxt is not None:
            if "paused:" in low or "stopped at the user's request" in low:
                return
            if "waiting:" in low:
                if running_workflows(tpath):
                    return
                block_stop("You wrote 'WAITING:' but no background workflow from this conversation is still "
                           "running. Continue the pipeline, or write 'PAUSED: <reason>'.")
            stt = cur["stages"][nxt].get("status", "pending")
            block_stop(f"Buyer-list run '{cur.get('project')}' is not finished: stage {nxt} ({S.NAMES[nxt]}) is "
                       f"{stt}. Next: {S.NEXT_HINT[nxt]}. Continue the pipeline. If you must stop, end with "
                       "'PAUSED: <reason>', or 'WAITING: <workflow running>' while a background workflow runs.")
        ok, msg = lock_ok(run)
        if not ok:
            block_stop(f"The run is marked complete but the Excel is not validated ({msg}). "
                       "Run: python -m buyerlist validate")
        from buyerlist import rounds as R
        inf = R.info(run)
        # the validation line is due when the list was (re)validated - in a review round a turn that only
        # records feedback does not have to repeat it
        if "validation: pass" not in low and touched and (inf["final"] or ran_validate(turn)):
            block_stop("Report the final validation in the final answer, one line: "
                       "'Validation: PASS - n errors, n warnings (...)'.")
        # a finished review round (not the final one) always ends by asking the analyst for feedback
        if touched and not inf["final"]:
            miss = R.missing(run)
            if miss and "feedback needed:" not in low:
                block_stop(f"Round {inf['no']} of {inf['of']} is complete. Present its new names to the analyst and "
                           f"ask for feedback: the final message must contain 'Feedback needed:' and list the "
                           f"{len(miss)} new name(s) without feedback (python -m buyerlist feedback status).")
            if not miss and "paused:" not in low and "stopped at the user's request" not in low:
                block_stop(f"The analyst's feedback on round {inf['no']} is complete. Start the next round: "
                           "python -m buyerlist round next --reason \"...\" - or write 'PAUSED: <reason>'.")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"require_pipeline_complete internal error (allowed): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
