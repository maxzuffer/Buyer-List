"""Run state and stage enforcement.

Each run (runs/<slug>/) has a state.json that only this module writes. A stage
is one of: pending -> open -> pass (or stale when something upstream changed).

* A stage command (decorated with @stage) refuses to run unless every earlier
  stage has status "pass" AND every file recorded as that stage's output still
  has the hash it had when its gate passed. A changed upstream file marks that
  stage and everything after it "stale" and stops the command.
* Running a stage command re-opens that stage (its gate must pass again) and
  marks later passed stages stale.
* `gate <stage>` (gates.py) validates the stage's outputs and records their
  hashes. The Claude Code hook guard_artifacts.py blocks direct edits of
  state.json and of every recorded output of a passed stage.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable, Iterable, Optional

from .common import (ROOT, Issues, StageError, now_iso, read_json, rel, sha256_file,
                     write_json)

STAGES = [
    ("0", "init", "Mandate (target, revenue, EBITDA, EV range, deal type)"),
    ("1", "client_sheet", "Client sheet: sourced facts [F#] about the target"),
    ("2", "universe", "Buyer universe: multi-lens sourcing, deduplicated"),
    ("3", "profile", "Profiles: financials, ownership, PE flag (strategic); ticket, focus (financial)"),
    ("4", "route", "Revenue gate + PE reroute (PE owners added to the financial universe)"),
    ("5a", "debate_research", "Debate: advocate and critic briefs, merged and source-checked"),
    ("5b", "debate_controller", "Debate: Controller KEEP/DROP for every point"),
    ("5c", "debate_retry", "Debate: retry round (re-research + Controller round 2)"),
    ("5d", "debate_judge", "Debate: anonymised Brief A/B and the judge verdicts"),
    ("5e", "debate_collect", "Debate: scores collected and re-computed"),
    ("6", "assemble", "Ranking, reroute merge, 100 + 100 selection"),
    ("7", "audit", "Skeptic audit of the selected rows, every finding resolved"),
    ("8", "excel", "Reference-format Excel written and validated (final.lock)"),
]
KEYS = [k for k, _, _ in STAGES]
NAMES = {k: n for k, n, _ in STAGES}
DESCRIPTIONS = {k: d for k, _, d in STAGES}

NEXT_HINT = {
    "0": "python -m buyerlist init --project <name> ...",
    "1": "run the client-sheet task (python -m buyerlist tasks make 1), then: python -m buyerlist gate 1",
    "2": "write lenses.json, run sourcing tasks, python -m buyerlist universe merge, then gate 2",
    "3": "python -m buyerlist tasks make 3 -> run profiler tasks -> python -m buyerlist gate 3",
    "4": "python -m buyerlist route -> profile any added sponsors (tasks make 4) -> route -> gate 4",
    "5a": "python -m buyerlist debate prepare -> run 5a tasks -> debate merge-check -> gate 5a",
    "5b": "python -m buyerlist tasks make 5b -> run Controller tasks -> gate 5b",
    "5c": "python -m buyerlist tasks make 5c -> retry agents -> debate recheck -> tasks make 5c2 -> Controller r2 -> gate 5c",
    "5d": "python -m buyerlist debate clean -> tasks make 5d -> judges -> gate 5d",
    "5e": "python -m buyerlist debate collect -> gate 5e",
    "6": "python -m buyerlist assemble -> gate 6 (if short: universe topup)",
    "7": "python -m buyerlist audit prepare -> tasks make 7 -> audit resolve ... -> gate 7",
    "8": "python -m buyerlist excel -> python -m buyerlist validate",
}


def index(key: str) -> int:
    if key not in KEYS:
        raise StageError(f"unknown stage {key!r} (stages: {', '.join(KEYS)})")
    return KEYS.index(key)


def before(key: str) -> list:
    return KEYS[:index(key)]


def after(key: str) -> list:
    return KEYS[index(key) + 1:]


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------
def state_path(run: Path) -> Path:
    return run / "state.json"


def new_state(project: str, slug: str) -> dict:
    return {"project": project, "slug": slug, "created": now_iso(), "status": "active",
            "topup_round": 0, "round": 1,
            "stages": {k: {"status": "pending"} for k in KEYS},
            "history": []}


def load(run: Path) -> dict:
    st = read_json(state_path(run))
    for k in KEYS:
        st["stages"].setdefault(k, {"status": "pending"})
    return st


def _set_writable(paths: Iterable, writable: bool) -> None:
    """Passed outputs are made read-only (a speed bump for hand edits; the CLI
    replaces files by rename, which read-only files do not prevent)."""
    for p in paths:
        p = ROOT / p if not Path(p).is_absolute() else Path(p)
        try:
            if p.is_file():
                p.chmod(0o644 if writable else 0o444)
        except OSError:
            pass


def save(run: Path, st: dict) -> None:
    # read-only state follows the stage status
    for k in KEYS:
        e = st["stages"][k]
        _set_writable((e.get("outputs") or {}).keys(), e.get("status") != "pass")
    # the final lock is only valid while every stage has passed
    lock = run / "08_final.lock"
    if lock.exists() and any(st["stages"][k].get("status") != "pass" for k in KEYS):
        lock.unlink()
    write_json(state_path(run), st)
    _set_writable([state_path(run)], False)


def log(st: dict, event: str, stage: Optional[str] = None, detail: str = "") -> None:
    st.setdefault("history", []).append({"at": now_iso(), "event": event, "stage": stage, "detail": detail})


DEFAULT_OPTIONS = {"debate_sides": ["strategic", "financial"], "retry": True, "financial_scoring": "debate"}


def options(st: dict) -> dict:
    return {**DEFAULT_OPTIONS, **(st.get("options") or {})}


def set_options(run: Path, reason: str, **changes) -> dict:
    """Change run options (who / why logged); the debate stage and everything after it must pass again."""
    if not reason.strip():
        raise StageError("changing run options needs a reason")
    st = load(run)
    opts = options(st)
    opts.update({k: v for k, v in changes.items() if v is not None})
    if opts["financial_scoring"] not in ("debate", "profile"):
        raise StageError("financial_scoring must be 'debate' or 'profile'")
    if "strategic" not in opts["debate_sides"]:
        raise StageError("the strategic side is always debated")
    if "financial" not in opts["debate_sides"] and opts["financial_scoring"] != "profile":
        raise StageError("an undebated financial side needs financial_scoring 'profile'")
    st["options"] = opts
    log(st, "options", "5a", f"{opts} - {reason}")
    save(run, st)
    reopen(run, "5a", f"run options changed: {reason}")
    return opts


def status(st: dict, key: str) -> str:
    return st["stages"][key].get("status", "pending")


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
def changed_outputs(st: dict, key: str) -> list:
    """Recorded outputs of a stage that are missing or whose hash changed."""
    bad = []
    for path, digest in (st["stages"][key].get("outputs") or {}).items():
        p = ROOT / path
        if not p.exists():
            bad.append(f"{path} (missing)")
        elif sha256_file(p) != digest:
            bad.append(f"{path} (changed)")
    return bad


def mark_stale_from(st: dict, key: str, reason: str) -> None:
    for k in [key] + after(key):
        if status(st, k) == "pass":
            st["stages"][k]["status"] = "stale"
            st["stages"][k]["stale_reason"] = reason
    log(st, "stale", key, reason)


def require(run: Path, st: dict, key: str) -> None:
    """Every stage before `key` must have passed, with unchanged outputs."""
    for k in before(key):
        s = status(st, k)
        if s != "pass":
            raise StageError(
                f"stage {key} ({NAMES[key]}) cannot start: stage {k} ({NAMES[k]}) is {s}. "
                f"Next: {NEXT_HINT[k]}")
        bad = changed_outputs(st, k)
        if bad:
            mark_stale_from(st, k, "outputs changed after the gate passed: " + "; ".join(bad[:5]))
            save(run, st)
            raise StageError(
                f"stage {k} ({NAMES[k]}) outputs changed after its gate passed ({'; '.join(bad[:5])}). "
                f"Stage {k} and all later stages are now stale - re-run: python -m buyerlist gate {k}")


def can_start(run: Path, key: str) -> tuple:
    st = load(run)
    try:
        require(run, st, key)
    except StageError as exc:
        return False, str(exc)
    return True, f"stage {key} ({NAMES[key]}) may run"


def open_stage(run: Path, st: dict, key: str, detail: str = "") -> None:
    """A stage command is running: the stage must pass its gate again."""
    if status(st, key) == "pass":
        st["stages"][key]["status"] = "open"
        log(st, "reopened", key, detail or "stage command re-run")
    elif status(st, key) in ("pending", "stale"):
        st["stages"][key]["status"] = "open"
    for k in after(key):
        if status(st, k) == "pass":
            st["stages"][k]["status"] = "stale"
            st["stages"][k]["stale_reason"] = f"stage {key} re-opened"
    log(st, "open", key, detail)


def record_gate(run: Path, key: str, issues: Issues, outputs: Iterable, summary: str = "") -> dict:
    st = load(run)
    require(run, st, key)
    entry = st["stages"][key]
    entry["gate_at"] = now_iso()
    entry["gate_summary"] = summary or issues.summary()
    entry["errors"] = [i.line() for i in issues.errors[:50]]
    entry["warnings"] = [i.line() for i in issues.warnings[:100]]
    if issues.ok():
        new = {}
        for p in sorted({Path(o).resolve() for o in outputs}):
            if p.exists() and p.is_file():
                new[rel(p)] = sha256_file(p)
        old = entry.get("outputs") or {}
        entry["outputs"] = new
        entry["status"] = "pass"
        entry.pop("stale_reason", None)
        if old and old != new:
            for k in after(key):
                if status(st, k) == "pass":
                    st["stages"][k]["status"] = "stale"
                    st["stages"][k]["stale_reason"] = f"stage {key} outputs changed"
        log(st, "gate pass", key, entry["gate_summary"])
        if key == KEYS[-1]:
            st["status"] = "complete"
    else:
        entry["status"] = "open"
        log(st, "gate fail", key, entry["gate_summary"])
        if key == KEYS[-1]:
            st["status"] = "active"
    save(run, st)
    return entry


def reopen(run: Path, key: str, reason: str) -> None:
    st = load(run)
    if not reason.strip():
        raise StageError("reopen needs a reason")
    st["stages"][key]["status"] = "open"
    for k in after(key):
        if status(st, k) == "pass":
            st["stages"][k]["status"] = "stale"
            st["stages"][k]["stale_reason"] = f"stage {key} re-opened: {reason}"
    st["status"] = "active"
    log(st, "reopen", key, reason)
    save(run, st)


def locked_paths(run: Path) -> set:
    st = load(run)
    out = {rel(state_path(run))}
    for k in KEYS:
        if status(st, k) == "pass":
            out |= set((st["stages"][k].get("outputs") or {}).keys())
    return out


def next_stage(st: dict) -> Optional[str]:
    for k in KEYS:
        if status(st, k) != "pass":
            return k
    return None


def describe(st: dict) -> str:
    lines = [f"Run: {st['project']} ({st['slug']}) - {st.get('status')} - top-up round {st.get('topup_round', 0)}"]
    for k in KEYS:
        e = st["stages"][k]
        s = e.get("status", "pending")
        extra = e.get("gate_summary", "")
        if s == "stale":
            extra = e.get("stale_reason", "")
        lines.append(f"  {k:>3}  {NAMES[k]:<18} {s:<8} {extra}")
    nxt = next_stage(st)
    lines.append(f"Next: stage {nxt} - {NEXT_HINT[nxt]}" if nxt else "All stages passed.")
    return "\n".join(lines)


def describe_run(run: Path) -> str:
    """describe() plus the round and the analyst-feedback status."""
    from . import rounds
    st = load(run)
    out = describe(st)
    inf = rounds.info(run)
    if not inf["schedule"]:
        return out
    out += f"\nRound {inf['no']} of {inf['of']} (new names per side: {', '.join(map(str, inf['schedule']))})"
    if next_stage(st) is None and not inf["final"]:
        miss = rounds.missing(run)
        if miss:
            out += (f" - complete, waiting for analyst feedback: {len(miss)} new name(s) without feedback "
                    "(python -m buyerlist feedback status)")
        else:
            out += " - feedback complete. Next: python -m buyerlist round next --reason \"...\""
    return out


# ---------------------------------------------------------------------------
# Decorator for stage commands
# ---------------------------------------------------------------------------
def stage(key: str) -> Callable:
    """Wrap a command `fn(run, ...)`: check prerequisites, then re-open the stage."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(run: Path, *args, **kwargs):
            st = load(run)
            require(run, st, key)
            open_stage(run, st, key, fn.__name__)
            save(run, st)
            return fn(run, *args, **kwargs)
        wrapper.stage_key = key
        return wrapper
    return deco


def requires(key: str) -> Callable:
    """Like @stage but read-only: checks prerequisites without re-opening the stage."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(run: Path, *args, **kwargs):
            st = load(run)
            require(run, st, key)
            return fn(run, *args, **kwargs)
        wrapper.stage_key = key
        return wrapper
    return deco
