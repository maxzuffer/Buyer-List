"""Rounds and analyst feedback (CLAUDE.md "Rounds and analyst feedback", user decision 2026-10-28).

A run with a schedule (mandate "rounds", default 10, 20, 20, 50 new names per side) builds the
list in rounds. Every round runs the full pipeline (stages 2-8) for its new names and ends with a
validated Excel. The analyst researches the new names and gives feedback in chat; Claude records
it with `feedback add` (the analyst's own words only). `round next` refuses to start the next round
until every new name of the round has feedback. It then closes the round (snapshot of the listed
names) and reopens stage 2. In later rounds:
* drop       -> never listed again (Excluded sheet, analyst's reason);
* keep/maybe -> stays on the list (keep: also a guaranteed place for an analyst-added name);
* data-wrong -> the company is re-profiled with the analyst's note (gate 3/4 check it);
* added name -> new candidate (raw lens file AN-r<n>), profiled and debated like any other;
* all feedback steers the next round's sourcing (feedback section in every sourcing task).
Only feedback of closed rounds affects the list, so a round's list stays a pure function of its
inputs while the analyst is still reviewing it. A mandate without a schedule is one final round.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Optional

from . import state
from .common import StageError, is_url, now_iso, read_json, text, write_json

STATUSES = ("keep", "drop", "maybe", "data-wrong")
NEED_NOTE = ("drop", "data-wrong")
SIDES = ("strategic", "financial")
DEFAULT_SCHEDULE = [10, 20, 20, 50]
OVERSAMPLE = {"strategic": 1.6, "financial": 1.3}
MIN_EXTRA = {"strategic": 30, "financial": 20}       # round 1 sources >= 40 strategic / 30 financial


def fdir(run: Path) -> Path:
    return run / "feedback"


def rounds_path(run: Path) -> Path:
    return fdir(run) / "rounds.json"


def feedback_path(run: Path, n: int) -> Path:
    return fdir(run) / f"round-{n}.json"


def _mandate(run: Path) -> dict:
    from .mandate import load_mandate
    return load_mandate(run)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def schedule(m: dict) -> list:
    s = m.get("rounds")
    return [int(x) for x in s] if isinstance(s, list) and s else []


def current(run: Path) -> int:
    return int(state.load(run).get("round", 1))


def info(run: Path, m: Optional[dict] = None) -> dict:
    """{no, of, final, schedule, targets (list size in the final round), quota (new names, rounds 1..n-1)}"""
    m = m or _mandate(run)
    s = schedule(m)
    n = current(run) if s else 1
    of = len(s) or 1
    final = n >= of
    return {"no": n, "of": of, "final": final, "schedule": s,
            "targets": dict(m["targets"]),
            "quota": None if final else {t: s[n - 1] for t in SIDES}}


def cumulative(m: dict, n: int) -> dict:
    s = schedule(m)
    if not s or n >= len(s):
        return dict(m["targets"])
    return {t: min(m["targets"][t], sum(s[:n])) for t in SIDES}


def required_universe(run: Path) -> dict:
    """Unique candidates needed in the universe for the current round (before top-ups)."""
    m = _mandate(run)
    if not schedule(m):
        return {t: math.ceil(m["targets"][t] * m["oversample"][t]) for t in SIDES}
    cum = cumulative(m, current(run))
    return {t: max(math.ceil(cum[t] * OVERSAMPLE[t]), cum[t] + MIN_EXTRA[t]) for t in SIDES}


def default_schedule(targets: dict) -> list:
    """10, 20, 20, 50 for lists above 50 names per side; the same proportions (10/20/20/rest %) for smaller ones."""
    t = min(targets.values())
    if t > 50:
        return list(DEFAULT_SCHEDULE)
    s = [max(1, round(t * f)) for f in (0.1, 0.2, 0.2)]
    while s and sum(s) >= t:
        s.pop()
    return s + [t - sum(s)] if s else []


def validate_schedule(m: dict) -> list:
    """Problems with the mandate's schedule (gate 0)."""
    s = m.get("rounds")
    if s is None:
        return []
    if not (isinstance(s, list) and s and all(isinstance(x, int) and x > 0 for x in s)):
        return ["rounds must be a list of positive whole numbers (new names per side and round)"]
    probs = []
    for t in SIDES:
        if sum(s[:-1]) >= m["targets"][t]:
            probs.append(f"rounds {s}: the rounds before the last already reach the {t} target "
                         f"{m['targets'][t]} - the last round fills the list to the target")
    return probs


# ---------------------------------------------------------------------------
# Closed rounds and feedback
# ---------------------------------------------------------------------------
def closed(run: Path) -> list:
    p = rounds_path(run)
    return read_json(p)["rounds"] if p.exists() else []


def round_feedback(run: Path, n: int) -> dict:
    p = feedback_path(run, n)
    return read_json(p) if p.exists() else {"round": n, "items": [], "new": []}


def _dom(url) -> str:
    from .universe import registrable_domain
    return registrable_domain(url) if text(url) else ""


def side_of(cid: str) -> str:
    return "strategic" if cid.startswith("s-") else "financial"


def id_map(run: Path) -> dict:
    """{website domain: current id} (universe + route-added sponsors). Feedback and round snapshots are
    matched by domain, so an id that changes later (a route-added sponsor that is sourced in a later
    round, for example) keeps the analyst's feedback."""
    from .route import added_candidates
    from .universe import load_universe, universe_path
    out = {}
    if universe_path(run).exists():
        for c in load_universe(run)["candidates"]:
            out.setdefault(c["domain"], c["id"])
    for c in added_candidates(run):
        if c.get("domain"):
            out.setdefault(c["domain"], c["id"])
    return out


def _now_id(cid: str, dom: str, m: dict) -> str:
    return m.get(dom, cid) if dom else cid


def pinned_ids(run: Path) -> set:
    """Ids as recorded in the closed rounds (snapshots and feedback): their type and id must not change."""
    out = set()
    for r in closed(run):
        for t in SIDES:
            out |= set(r["listed"][t])
        out |= {it["id"] for it in round_feedback(run, r["round"])["items"]}
    return out


def effective(run: Path, upto: Optional[int] = None) -> dict:
    """{current id: latest feedback record} from the closed rounds (or rounds <= upto)."""
    last = upto if upto is not None else len(closed(run))
    m = id_map(run) if last else {}
    out = {}
    for n in range(1, last + 1):
        for it in round_feedback(run, n)["items"]:
            cid = _now_id(it["id"], it.get("domain", ""), m)
            out[cid] = dict(it, id=cid, side=side_of(cid))
    return out


def added_names(run: Path, upto: Optional[int] = None) -> list:
    last = upto if upto is not None else len(closed(run))
    return [dict(x, round=n) for n in range(1, last + 1) for x in round_feedback(run, n)["new"]]


def added_ids(run: Path) -> dict:
    """{current id: record} for names the analyst added in closed rounds that are now candidates."""
    m = id_map(run)
    return {m[_dom(x["website"])]: x for x in added_names(run) if _dom(x["website"]) in m}


def _snapshot_ids(run: Path):
    """(round, side, current id) for every name listed in a closed round."""
    rs = closed(run)
    m = id_map(run) if rs else {}
    for r in rs:
        doms = r.get("domains") or {}
        for t in SIDES:
            for i in r["listed"][t]:
                cid = _now_id(i, doms.get(i, ""), m)
                yield r["round"], side_of(cid), cid


def shown(run: Path) -> dict:
    """{side: set of current ids listed in any closed round}"""
    out = {t: set() for t in SIDES}
    for _, t, i in _snapshot_ids(run):
        out[t].add(i)
    return out


def first_listed(run: Path) -> dict:
    out = {}
    for n, _, i in _snapshot_ids(run):
        out.setdefault(i, n)
    return out


def digest(run: Path) -> str:
    """Hash of every closed round's feedback - the sourcing of the next round must carry it."""
    data = [round_feedback(run, r["round"]) for r in closed(run)]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def feedback_text(rec: Optional[dict]) -> str:
    if not rec:
        return ""
    label = rec["status"] if rec.get("id") else f"added ({rec['status']})"
    return f"R{rec['round']} {label}" + (f": {rec['note']}" if text(rec.get("note")) else "")


# ---------------------------------------------------------------------------
# Recording feedback (chat -> CLI)
# ---------------------------------------------------------------------------
def _round_complete(run: Path) -> None:
    from .excel import lock_ok
    ok, msg = lock_ok(run)
    if not ok:
        raise StageError(f"round {current(run)} is not finished ({msg}) - feedback is recorded on a validated round list")


def _listed_now(run: Path) -> dict:
    from .assemble import load_list
    d = load_list(run)
    return {t: {r["id"]: r for r in d[t]["selected"]} for t in SIDES}


def new_this_round(run: Path) -> dict:
    """{side: {id: row}} of the names first listed in the current round."""
    seen = shown(run)
    return {t: {i: r for i, r in rows.items() if i not in seen[t]} for t, rows in _listed_now(run).items()}


def add_feedback(run: Path, cid: str, status: str, note: str) -> dict:
    _round_complete(run)
    status = status.strip().lower()
    if status not in STATUSES:
        raise StageError(f"status must be one of {', '.join(STATUSES)}")
    if status in NEED_NOTE and not text(note):
        raise StageError(f"'{status}' needs the analyst's reason (--note \"<analyst's words>\")")
    listed = _listed_now(run)
    side = next((t for t in SIDES if cid in listed[t]), None)
    if side is None:
        raise StageError(f"{cid} is not on the current round's list - feedback is given on listed names")
    n = current(run)
    fb = round_feedback(run, n)
    rec = {"id": cid, "domain": _dom(listed[side][cid].get("website")), "name": listed[side][cid]["name"],
           "side": side, "status": status, "note": text(note), "round": n, "by": "analyst (chat)", "at": now_iso()}
    fb["items"] = [x for x in fb["items"] if x["id"] != cid] + [rec]
    write_json(feedback_path(run, n), fb)
    _log(run, f"feedback {cid} {status}")
    return rec


def add_name(run: Path, side: str, name: str, website: str, country: str, status: str, note: str) -> dict:
    _round_complete(run)
    status = status.strip().lower()
    if side not in SIDES:
        raise StageError("type must be strategic or financial")
    if status not in ("keep", "maybe"):
        raise StageError("a name the analyst adds is 'keep' or 'maybe'")
    if not (text(name) and is_url(website)):
        raise StageError("an added name needs --name and a valid --website (https://...)")
    from .universe import country as cc, registrable_domain
    code = cc(country or "")
    if len(code) != 2:
        raise StageError("an added name needs --country (ISO 2-letter, UK for United Kingdom)")
    n = current(run)
    fb = round_feedback(run, n)
    dom = registrable_domain(website)
    rec = {"name": text(name), "type": side, "website": website, "hq_country": code, "status": status,
           "note": text(note), "round": n, "by": "analyst (chat)", "at": now_iso()}
    fb["new"] = [x for x in fb["new"] if registrable_domain(x["website"]) != dom] + [rec]
    write_json(feedback_path(run, n), fb)
    _log(run, f"analyst added {side} {name} ({status})")
    return rec


def round_complete(run: Path) -> bool:
    from .excel import lock_ok
    return lock_ok(run)[0]


def missing(run: Path) -> list:
    """New names of the current (validated, non-final) round without feedback: [(side, id, name)].
    A name the analyst added in an earlier round already carries the analyst's feedback."""
    if info(run)["final"] or not round_complete(run):
        return []
    got = {x["id"] for x in round_feedback(run, current(run))["items"]} | set(added_ids(run))
    return [(t, i, r["name"]) for t, rows in new_this_round(run).items() for i, r in sorted(rows.items())
            if i not in got]


def _log(run: Path, detail: str) -> None:
    st = state.load(run)
    state.log(st, "feedback", None, detail)
    state.save(run, st)


# ---------------------------------------------------------------------------
# Next round
# ---------------------------------------------------------------------------
def next_round(run: Path, reason: str) -> dict:
    inf = info(run)
    if inf["final"]:
        raise StageError("this is the final round - there is no next round")
    _round_complete(run)
    miss = missing(run)
    if miss:
        raise StageError(f"{len(miss)} new name(s) of round {inf['no']} have no analyst feedback yet: "
                         + "; ".join(f"{i} {nm}" for _, i, nm in miss[:15])
                         + " - ask the analyst, then: python -m buyerlist feedback add ...")
    n = inf["no"]
    listed = _listed_now(run)
    rounds = closed(run) + [{"round": n, "closed_at": now_iso(), "closed_epoch": time.time(),
                             "listed": {t: sorted(listed[t]) for t in SIDES},
                             "domains": {i: _dom(r.get("website")) for t in SIDES for i, r in listed[t].items()},
                             "new": {t: sorted(new_this_round(run)[t]) for t in SIDES}}]
    write_json(rounds_path(run), {"rounds": rounds})
    st = state.load(run)
    st["round"] = n + 1
    st["topup_round"] = int(st.get("topup_round", 0)) + 1
    st["topup_extra"] = {}
    state.log(st, "round", "2", f"round {n} closed, round {n + 1} started: {reason}")
    state.save(run, st)
    state.reopen(run, "2", f"round {n + 1} of {inf['of']}: {reason}")
    added = round_feedback(run, n)["new"]
    if added:
        from .universe import raw_dir
        raw_dir(run).mkdir(parents=True, exist_ok=True)
        write_json(raw_dir(run) / f"AN-r{st['topup_round']}.json",
                   {"lens": "AN", "round": st["topup_round"], "candidates": [
                       {"name": x["name"], "type": x["type"], "website": x["website"], "hq_country": x["hq_country"],
                        "hq_city": "", "segment": "analyst suggestion", "rationale": f"analyst: {x['note']}",
                        "source_url": x["website"]} for x in added]})
    return {"round": n + 1, "of": inf["of"], "required_universe": required_universe(run),
            "added_names": len(added), "reprofile": sorted(reprofile_due(run))}


# ---------------------------------------------------------------------------
# What the next rounds must do with the feedback
# ---------------------------------------------------------------------------
def reprofile_due(run: Path) -> dict:
    """{id: analyst note} - data-wrong feedback of the last closed round."""
    rs = closed(run)
    if not rs:
        return {}
    m = id_map(run)
    return {_now_id(it["id"], it.get("domain", ""), m): it["note"]
            for it in round_feedback(run, rs[-1]["round"])["items"] if it["status"] == "data-wrong"}


def reprofile_problems(run: Path, ids: set) -> list:
    """Data-wrong ids whose profile was not rewritten after the round closed."""
    from .profiles import profile_path
    rs = closed(run)
    if not rs:
        return []
    t0 = rs[-1]["closed_epoch"]
    out = []
    for cid, note in sorted(reprofile_due(run).items()):
        if cid not in ids:
            continue
        p = profile_path(run, cid)
        if not p.exists() or p.stat().st_mtime <= t0:
            out.append(f"{cid}: the analyst reported wrong data ({note}) - re-profile it: "
                       "python -m buyerlist tasks make 3")
    return out


def brief(run: Path) -> str:
    """Feedback section for the sourcing and completeness-critic tasks of the next rounds."""
    rs = closed(run)
    if not rs:
        return ""
    fb = effective(run)
    by = {s: [x for x in fb.values() if x["status"] == s] for s in STATUSES}

    def lines(xs):
        return "\n".join(f"- {x['name']} ({x['side']}): \"{x['note'] or 'no reason given'}\"" for x in xs) or "- none"
    added = added_names(run)
    add = "\n".join(f"- {x['name']} ({x['type']}, {x['website']}): \"{x['note']}\"" for x in added) or "- none"
    return f"""
## Analyst feedback from the earlier rounds (the deal team's own words - follow it)
Kept / good fit - find more buyers like these, for the reasons given:
{lines(by['keep'])}
Dropped - do not propose these again, and avoid buyers with the same problem:
{lines(by['drop'])}
Unsure (maybe):
{lines(by['maybe'])}
Data the analyst found wrong:
{lines(by['data-wrong'])}
Names the analyst added:
{add}
Feedback digest: {digest(run)}
"""
