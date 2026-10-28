"""One gate per stage. A gate validates the stage's outputs; when it passes,
state.record_gate stores their hashes and the next stage may start."""
from __future__ import annotations

from pathlib import Path

from . import assemble, audit, debate, excel, mandate, rounds, route, state, universe
from .common import Issues
from .profiles import profile_path, validate


def gate3(run: Path) -> tuple:
    iss, outs = Issues(), []
    m = mandate.load_mandate(run)
    a = mandate.as_of(m)
    uni = universe.load_universe(run)
    missing = 0
    for c in uni["candidates"]:
        v = validate(run, c, a)
        if not v.ok():
            missing += 1
        iss.extend(v)
        if profile_path(run, c["id"]).exists():
            outs.append(profile_path(run, c["id"]))
    if missing:
        iss.warn("profiles", "summary", f"{missing} of {len(uni['candidates'])} profiles have errors - "
                 "re-run: python -m buyerlist tasks make 3 (only those are re-done)")
    for msg in rounds.reprofile_problems(run, {c["id"] for c in uni["candidates"]}):
        iss.error("profiles", "analyst feedback", msg)
    return iss, outs


GATES = {
    "0": mandate.gate0, "1": mandate.gate1, "2": universe.gate2, "3": gate3, "4": route.gate4,
    "5a": debate.gate5a, "5b": debate.gate5b, "5c": debate.gate5c, "5d": debate.gate5d, "5e": debate.gate5e,
    "6": assemble.gate6, "7": audit.gate7, "8": excel.gate8,
}


def run_gate(run: Path, key: str) -> tuple:
    st = state.load(run)
    state.require(run, st, key)
    iss, outs = GATES[key](run)
    entry = state.record_gate(run, key, iss, outs)
    if key == "8" and iss.ok():
        excel.write_lock(run, f"PASS - {iss.summary()}")
    return iss, entry
