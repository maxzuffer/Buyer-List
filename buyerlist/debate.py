"""Stage 5 - the fit debates (strategic and financial), ported from the
Business-Dev-2.0 bd-fit-debate skill and run per batch of companies:

5a  advocate + critic briefs (2 companies per agent) -> merge-check (format + quotes)
5b  Controller KEEP / DROP for every point
5c  retry round for agents with > half their points dropped -> recheck -> Controller round 2
5d  clean + anonymise (random Brief A / B per batch) -> judge verdicts
5e  collect: scores re-computed, confidence set, sources resolved

Integrity rules: every derived file (check reports, retry list, Brief A/B,
scores) is recomputed by its gate and compared with the file; every agent
review (Controller, round 2, judge) is tied by a manifest to the hash of the
inputs it was given - a review of older inputs counts as missing.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

from .common import Issues, StageError, chunks, read_json, read_text, sha256_file, write_json, write_text
from .mandate import client_sheet_path, fact_ids, load_mandate
from .route import debate_scope, load_route
from .scoring import calc_text, weighted, weights_for
from . import sources as S
from .tasks import write_tasks

SIDES = ("strategic", "financial")
ROLES = ("advocate", "critic")
AGENT = {("strategic", "advocate"): "bl-advocate", ("strategic", "critic"): "bl-critic",
         ("financial", "advocate"): "fi-advocate", ("financial", "critic"): "fi-critic",
         ("strategic", "controller"): "bl-controller", ("financial", "controller"): "bl-controller",
         ("strategic", "judge"): "bl-judge", ("financial", "judge"): "fi-judge"}
CONF_ORDER = {"Low": 0, "Medium": 1, "High": 2}
BRIEF_SRC_RE = re.compile(r"^\s*-\s*\[([AB]\d+)\]\s*(.+?)\s*$")


def options(run: Path) -> dict:
    from . import state
    return state.options(state.load(run))


def sides(run: Path) -> tuple:
    """Debate sides of this run (run option; default both)."""
    return tuple(s for s in SIDES if s in options(run)["debate_sides"])


def ddir(run: Path) -> Path:
    return run / "05_debate"


def side_dir(run: Path, side: str) -> Path:
    return ddir(run) / side


def bdir(run: Path, side: str, batch: int) -> Path:
    return side_dir(run, side) / f"b{batch:02d}"


def batches_path(run: Path, side: str) -> Path:
    return side_dir(run, side) / "batches.json"


def load_batches(run: Path, side: str) -> list:
    p = batches_path(run, side)
    return [b for b in read_json(p)["batches"] if not b.get("dropped")] if p.exists() else []


def active(b: dict) -> list:
    """Companies of a batch that are still in the debate scope."""
    ex = set(b.get("excluded_ids", []))
    return [c for c in b["companies"] if c["id"] not in ex]


def mandate_line(side: str) -> str:
    return ("Mandate: SELL-SIDE - find strategic buyers for the client." if side == "strategic"
            else "Mandate: SELL-SIDE - find financial investors (buyers) for the client.")


def heading(c: dict) -> str:
    return f"{c['name']} #{c['id']}"


def facts_digest(run: Path) -> str:
    return hashlib.sha256(",".join(sorted(fact_ids(run))).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Manifests: tie an agent's output to the inputs it was given
# ---------------------------------------------------------------------------
def digest(paths: list, extra: str = "") -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).name.encode())
        h.update((sha256_file(p) if Path(p).exists() else "missing").encode())
    h.update(extra.encode())
    return h.hexdigest()


def manifest_path(out: Path) -> Path:
    return out.with_name(out.name + ".inputs.json")


def fresh(out: Path, dg: str) -> bool:
    mp = manifest_path(out)
    if not (out.exists() and mp.exists()):
        return False
    m = read_json(mp)
    return m.get("digest") == dg and out.stat().st_mtime >= m.get("issued_at", 0) - 1


def issue(out: Path, dg: str) -> bool:
    """True if a task must be (re)issued for `out`. Removes a stale output."""
    if fresh(out, dg):
        return False
    if out.exists():
        out.unlink()
    write_json(manifest_path(out), {"digest": dg, "issued_at": time.time()})
    return True


# ---------------------------------------------------------------------------
# 5a - prepare
# ---------------------------------------------------------------------------
def platform_context(run: Path) -> dict:
    """fi id -> text about PE-owned strategics it holds (from the route stage)."""
    route = load_route(run)
    names = {r["id"]: r for r in route["strategic"]}
    from .universe import load_universe
    uni = {c["id"]: c for c in load_universe(run)["candidates"]}
    ctx: dict = {}
    for l in route.get("links", []):
        s = uni.get(l["strategic_id"], {})
        ctx.setdefault(l["fi_id"], []).append(
            f"{names[l['strategic_id']]['name']} ({s.get('website', '')}) - PE-owned operating company held by "
            f"this investor. Do NOT count it for Portfolio or Criteria: the program decides from the strategic "
            f"debate whether it is this investor's platform")
    return {k: "known holding: " + "; ".join(v) for k, v in ctx.items()}


def plan_batches(run: Path, side: str, old: list, scope: list, ctx: dict) -> list:
    m = load_mandate(run)
    per_agent = m["batching"]["companies_per_researcher"]
    per_batch = m["batching"]["companies_per_batch"]
    in_scope = {c["id"]: c for c in scope}
    batches = json.loads(json.dumps(old))
    covered = set()
    for b in batches:
        if b.get("dropped"):
            continue
        ids = [c["id"] for c in b["companies"]]
        b["excluded_ids"] = sorted(i for i in ids if i not in in_scope)
        for c in b["companies"]:
            c["context"] = ctx.get(c["id"], "")
        covered |= set(ids)
    new = [c for c in scope if c["id"] not in covered]
    nxt = 1 + max([b["batch"] for b in batches] or [0])
    for chunk in chunks(new, per_batch):
        comps = [{"id": c["id"], "name": c["name"], "heading": heading(c),
                  "town": f"{c.get('hq_city') or ''} {c.get('hq_country') or ''}".strip(),
                  "website": c.get("website", ""), "context": ctx.get(c["id"], "")} for c in chunk]
        groups = [[c["id"] for c in g] for g in chunks(comps, per_agent)]
        batches.append({"batch": nxt, "companies": comps, "groups": groups, "excluded_ids": []})
        nxt += 1
    return batches


def prepare(run: Path) -> dict:
    scope = debate_scope(run)
    ctx = platform_context(run)
    summary = {}
    for side in sides(run):
        p = batches_path(run, side)
        old = read_json(p)["batches"] if p.exists() else []
        batches = plan_batches(run, side, old, scope[side], ctx)
        write_json(p, {"side": side, "batches": batches})
        act = [b for b in batches if not b.get("dropped")]
        summary[side] = {"batches": len(act), "companies": len(scope[side]),
                         "excluded_from_old_batches": sum(len(b["excluded_ids"]) for b in act)}
    research_tasks(run)
    return summary


def part_path(run: Path, side: str, batch: int, role: str, g: int) -> Path:
    return bdir(run, side, batch) / "parts" / f"{role}-g{g}.md"


def part_ok(path: Path, side: str, group: list) -> bool:
    """Mirror of the structural check: every expected company exactly once, readable."""
    if not path.exists():
        return False
    try:
        brief = S.parse_brief(read_text(path), side)
    except Exception:
        return False
    return not S.structural_problems(brief, side, [g.lower() for g in group])


def research_tasks(run: Path) -> list:
    cs = client_sheet_path(run)
    tasks = []
    for side in sides(run):
        for b in load_batches(run, side):
            comps = {c["id"]: c for c in b["companies"]}
            for gi, group in enumerate(b["groups"], 1):
                for role in ROLES:
                    out = part_path(run, side, b["batch"], role, gi)
                    if part_ok(out, side, group):
                        continue
                    lines = []
                    for cid in group:
                        c = comps[cid]
                        lines.append(f"- {c['name']} ({c['town']}, {c['website']})\n"
                                     f"  Use this company name exactly as the heading: {c['heading']}"
                                     + (f"\n  Context: {c['context']}" if c["context"] else ""))
                    body = (f"{mandate_line(side)}\nClient sheet: {cs}\nCompanies ({len(group)}):\n"
                            + "\n".join(lines) + f"\nSave to: {out}\n"
                            "(If this file already exists from an earlier attempt, overwrite it completely.)\n")
                    tasks.append({"id": f"{side[0]}b{b['batch']:02d}g{gi}-{role}", "agent": AGENT[(side, role)],
                                  "body": body, "outputs": [out]})
    return write_tasks(run, "5a", tasks)


# ---------------------------------------------------------------------------
# 5a - merge and check
# ---------------------------------------------------------------------------
def check_json(run: Path, side: str, batch: int, role: str, r2: bool = False) -> Path:
    return bdir(run, side, batch) / f"check_{role}{'_r2' if r2 else ''}.json"


def _parts(run: Path, side: str, b: dict, role: str) -> list:
    return [part_path(run, side, b["batch"], role, gi) for gi in range(1, len(b["groups"]) + 1)]


def _merged_round1(run: Path, side: str, b: dict, role: str, fetch: bool) -> dict:
    facts = fact_ids(run)
    parts = _parts(run, side, b, role)
    missing = [p.name for p in parts if not p.exists()]
    merged = S.merge([S.parse_brief(read_text(p), side) for p in parts if p.exists()])
    S.check(merged, side, facts, expected_keys=[c["id"].lower() for c in b["companies"]], fetch=fetch)
    if missing:
        merged["structural"].append(f"part files missing: {', '.join(missing)}")
    merged["part_hashes"] = {p.name: sha256_file(p) for p in parts if p.exists()}
    merged["facts_digest"] = facts_digest(run)
    return merged


def merge_check(run: Path, force: bool = False) -> dict:
    report = {}
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            for role in ROLES:
                cj = check_json(run, side, b["batch"], role)
                parts = _parts(run, side, b, role)
                hashes = {p.name: sha256_file(p) for p in parts if p.exists()}
                if (not force and cj.exists() and read_json(cj).get("part_hashes") == hashes
                        and read_json(cj).get("facts_digest") == facts_digest(run)
                        and all(p.exists() for p in parts)):
                    continue
                merged = _merged_round1(run, side, b, role, fetch=True)
                write_text(d / f"{role}_raw.md", S.render_brief(merged, f"{role.capitalize()} brief"))
                write_json(cj, merged)
                write_text(d / f"check_{role}.md", S.render_check_md(merged, f"{side} b{b['batch']:02d} {role}"))
                report[f"{side} b{b['batch']:02d} {role}"] = {**S.stats(merged), "structural": merged["structural"],
                                                             "format_errors": len(merged["errors"])}
    return report


def _strip_status(brief: dict) -> dict:
    """Comparable view of a check report (quote statuses come from the network)."""
    b = json.loads(json.dumps(brief))
    for s in b.get("sources", {}).values():
        s.pop("status", None)
        s.pop("detail", None)
    return {"companies": b["companies"], "sources": b["sources"], "errors": b.get("errors"),
            "structural": b.get("structural")}


def gate5a(run: Path) -> tuple:
    iss, outs = Issues(), []
    scope = debate_scope(run)
    for side in sides(run):
        bs = load_batches(run, side)
        outs.append(batches_path(run, side))
        if not bs and scope[side]:
            iss.error("debate", side, "no batches - run: python -m buyerlist debate prepare")
            continue
        planned = plan_batches(run, side, read_json(batches_path(run, side))["batches"] if bs else [],
                               scope[side], platform_context(run))
        if [b for b in planned if not b.get("dropped")] != bs:
            iss.error("debate", side, "batches do not match the current debate scope (route changed?) - "
                      "run: python -m buyerlist debate prepare")
        for b in bs:
            for role in ROLES:
                cj = check_json(run, side, b["batch"], role)
                who = f"{side} b{b['batch']:02d} {role}"
                if not cj.exists():
                    iss.error("debate", who, "no source check - run: python -m buyerlist debate merge-check")
                    continue
                rep = read_json(cj)
                fresh_rep = _merged_round1(run, side, b, role, fetch=False)
                if rep.get("part_hashes") != fresh_rep["part_hashes"] or rep.get("facts_digest") != fresh_rep["facts_digest"]:
                    iss.error("debate", who, "part files or client facts changed after the check - run debate merge-check")
                elif _strip_status(rep) != _strip_status(fresh_rep):
                    iss.error("debate", who, "check report does not match the part files - run debate merge-check --force")
                for s in fresh_rep["structural"]:
                    iss.error("debate", who, s + " - re-run: python -m buyerlist tasks make 5a")
                outs += [cj, cj.with_suffix(".md"), bdir(run, side, b["batch"]) / f"{role}_raw.md"] + \
                        [p for p in _parts(run, side, b, role) if p.exists()]
    return iss, outs


# ---------------------------------------------------------------------------
# 5b - Controller
# ---------------------------------------------------------------------------
def controller_inputs(run: Path, side: str, b: dict) -> str:
    d = bdir(run, side, b["batch"])
    return digest([client_sheet_path(run), d / "check_advocate.md", d / "check_critic.md"])


def controller_tasks(run: Path) -> list:
    cs = client_sheet_path(run)
    tasks = []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            out = d / "controller.md"
            if not issue(out, controller_inputs(run, side, b)) and not controller_missing(run, side, b, "controller.md"):
                continue
            if out.exists():
                out.unlink()
                write_json(manifest_path(out), {"digest": controller_inputs(run, side, b), "issued_at": time.time()})
            body = (f"Client sheet: {cs}\nSource check advocate: {d / 'check_advocate.md'}\n"
                    f"Source check critic: {d / 'check_critic.md'}\nSave to: {out}\n"
                    + ("Debate side: FINANCIAL INVESTORS (dimensions Ticket size, Geography, Investment criteria)\n"
                       if side == "financial" else
                       "Debate side: STRATEGIC BUYERS (dimensions Product/service, Customer, Geography)\n"))
            tasks.append({"id": f"{side[0]}b{b['batch']:02d}-controller", "agent": AGENT[(side, "controller")],
                          "body": body, "outputs": [out]})
    return write_tasks(run, "5b", tasks)


def controller_missing(run: Path, side: str, b: dict, fname: str, only: Optional[dict] = None,
                       r2: bool = False) -> list:
    d = bdir(run, side, b["batch"])
    p = d / fname
    if not p.exists():
        return [f"{fname} missing"]
    dec = S.parse_decisions(read_text(p))
    miss = []
    for role in ROLES:
        cj = check_json(run, side, b["batch"], role, r2=r2)
        if not cj.exists():
            continue
        keys = None if only is None else set(only.get(role, []))
        if keys is not None and not keys:
            continue
        miss += S.missing_decisions(read_json(cj), role, dec, keys)
    return miss


def gate5b(run: Path) -> tuple:
    iss, outs = Issues(), []
    for side in sides(run):
        for b in load_batches(run, side):
            who = f"{side} b{b['batch']:02d}"
            out = bdir(run, side, b["batch"]) / "controller.md"
            if not fresh(out, controller_inputs(run, side, b)):
                iss.error("controller", who, "Controller report missing or made for older check files - "
                          "run: python -m buyerlist tasks make 5b")
                continue
            miss = controller_missing(run, side, b, "controller.md")
            for x in miss[:20]:
                iss.error("controller", who, f"no decision: {x} - re-run the Controller (delete nothing; tasks make 5b)")
            outs += [out, manifest_path(out)]
    return iss, outs


# ---------------------------------------------------------------------------
# 5c - retry round
# ---------------------------------------------------------------------------
def retry_path(run: Path, side: str, batch: int, role: str, key: str) -> Path:
    return bdir(run, side, batch) / "parts" / f"retry-{role}-{key}.md"


def compute_retry(run: Path, side: str, b: dict) -> dict:
    if not options(run).get("retry", True):
        return {role: [] for role in ROLES}           # retry round switched off (run option)
    d = bdir(run, side, b["batch"])
    dec = S.parse_decisions(read_text(d / "controller.md"))
    out = {}
    for role in ROLES:
        brief = read_json(check_json(run, side, b["batch"], role))
        hits = S.retry_candidates(brief, role, dec, side)
        out[role] = [h for h in hits if h["key"] in {c["id"].lower() for c in active(b)}]
    return out


def retry_ok(path: Path, side: str, key: str) -> bool:
    if not path.exists():
        return False
    br = S.parse_brief(read_text(path), side)
    return not S.structural_problems(br, side, [key])


def retry_tasks(run: Path) -> list:
    cs = client_sheet_path(run)
    tasks = []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            comps = {c["id"].lower(): c for c in b["companies"]}
            hits = compute_retry(run, side, b)
            write_json(d / "retry.json", {r: [h["key"] for h in hits[r]] for r in ROLES})
            for role in ROLES:
                brief = read_json(check_json(run, side, b["batch"], role))
                for h in hits[role]:
                    c = comps[h["key"]]
                    out = retry_path(run, side, b["batch"], role, h["key"])
                    if retry_ok(out, side, h["key"]):
                        continue
                    sec = {"companies": [x for x in brief["companies"] if x["key"] == h["key"]], "sources": {}, "notes": []}
                    used = {s for x in sec["companies"] for a in x["arguments"] for s in a["sources"]}
                    sec["sources"] = {k: v for k, v in brief["sources"].items() if k in used}
                    r1 = d / "parts" / "round1" / f"{role}-{h['key']}.md"
                    write_text(r1, S.render_brief(sec, f"{role.capitalize()} brief"))
                    reasons = "\n".join(f"    point {n or '-'}: {dd} - {why}" for n, dd, why in sorted(h["points"]))
                    body = (f"Retry - {mandate_line(side)}\nClient sheet: {cs}\n"
                            f"Company: {c['name']} ({c['town']}, {c['website']})\n"
                            f"Use this company name exactly as the heading: {c['heading']}\n"
                            + (f"Context: {c['context']}\n" if c["context"] else "")
                            + f"Your brief (round 1): {r1}\nController reasons for your points:\n"
                            f"  RETRY {role.capitalize()} | {c['heading']}\n{reasons}\n"
                            "Write the COMPLETE company section again: all 3 points (Product/service or Ticket size, "
                            "Customer or Investment criteria, Geography) - kept points copied unchanged with their "
                            f"sources, dropped or missing points rewritten.\nSave to: {out}\n")
                    tasks.append({"id": f"{side[0]}b{b['batch']:02d}-retry-{role}-{h['key']}",
                                  "agent": AGENT[(side, role)], "body": body, "outputs": [out]})
    return write_tasks(run, "5c", tasks)


def _r2_brief(run: Path, side: str, b: dict, role: str, keys: list, fetch: bool) -> dict:
    facts = fact_ids(run)
    files = [retry_path(run, side, b["batch"], role, k) for k in keys]
    missing = [f.name for f in files if not f.exists()]
    merged = S.merge([S.parse_brief(read_text(f), side) for f in files if f.exists()])
    S.check(merged, side, facts, expected_keys=keys, fetch=fetch)
    if missing:
        merged["structural"].append(f"retry files missing: {', '.join(missing)}")
    merged["part_hashes"] = {f.name: sha256_file(f) for f in files if f.exists()}
    merged["facts_digest"] = facts_digest(run)
    return merged


def recheck(run: Path) -> dict:
    out = {}
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            rp = d / "retry.json"
            if not rp.exists():
                raise StageError(f"{side} b{b['batch']:02d}: retry.json missing - run: python -m buyerlist tasks make 5c")
            retry = read_json(rp)
            for role in ROLES:
                keys = retry.get(role, [])
                if not keys:
                    continue
                cj = check_json(run, side, b["batch"], role, r2=True)
                files = [retry_path(run, side, b["batch"], role, k) for k in keys]
                hashes = {f.name: sha256_file(f) for f in files if f.exists()}
                if (cj.exists() and read_json(cj).get("part_hashes") == hashes and len(hashes) == len(files)
                        and read_json(cj).get("facts_digest") == facts_digest(run)):
                    continue
                merged = _r2_brief(run, side, b, role, keys, fetch=True)
                write_json(check_json(run, side, b["batch"], role, r2=True), merged)
                write_text(d / f"check_{role}_r2.md", S.render_check_md(merged, f"{side} b{b['batch']:02d} {role} round 2"))
                out[f"{side} b{b['batch']:02d} {role}"] = {**S.stats(merged), "structural": merged["structural"]}
    return out


def controller2_inputs(run: Path, side: str, b: dict) -> str:
    d = bdir(run, side, b["batch"])
    return digest([client_sheet_path(run)] + [d / f"check_{r}_r2.md" for r in ROLES], json.dumps(read_json(d / "retry.json")))


def controller2_tasks(run: Path) -> list:
    cs = client_sheet_path(run)
    tasks = []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            retry = read_json(d / "retry.json")
            if not any(retry.get(r) for r in ROLES):
                continue
            out = d / "controller_r2.md"
            dg = controller2_inputs(run, side, b)
            if not issue(out, dg) and not controller_missing(run, side, b, "controller_r2.md", retry, r2=True):
                continue
            if out.exists():
                out.unlink()
                write_json(manifest_path(out), {"digest": dg, "issued_at": time.time()})
            comps = {c["id"].lower(): c["heading"] for c in b["companies"]}
            lines = ["Round 2", f"Client sheet: {cs}"]
            for role in ROLES:
                if retry.get(role):
                    lines.append(f"Source check {role}: {d / f'check_{role}_r2.md'}")
            lines.append("Companies retried: " + "; ".join(
                f"{role} - {', '.join(comps.get(k, k) for k in retry[role])}" for role in ROLES if retry.get(role)))
            lines.append(f"Save to: {out}")
            tasks.append({"id": f"{side[0]}b{b['batch']:02d}-controller-r2", "agent": AGENT[(side, "controller")],
                          "body": "\n".join(lines) + "\n", "outputs": [out]})
    return write_tasks(run, "5c2", tasks)


def gate5c(run: Path) -> tuple:
    iss, outs = Issues(), []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            who = f"{side} b{b['batch']:02d}"
            rp = d / "retry.json"
            if not rp.exists():
                iss.error("retry", who, "retry.json missing - run: python -m buyerlist tasks make 5c")
                continue
            retry = read_json(rp)
            want = {r: [h["key"] for h in hs] for r, hs in compute_retry(run, side, b).items()}
            if retry != want:
                iss.error("retry", who, f"retry.json {retry} does not match the Controller's decisions {want} - "
                          "run: python -m buyerlist tasks make 5c")
                continue
            outs.append(rp)
            if not any(retry.get(r) for r in ROLES):
                continue
            for role in ROLES:
                keys = retry.get(role, [])
                if not keys:
                    continue
                cj = check_json(run, side, b["batch"], role, r2=True)
                files = [retry_path(run, side, b["batch"], role, k) for k in keys]
                for k, f in zip(keys, files):
                    if not retry_ok(f, side, k):
                        iss.error("retry", who, f"{role} {k}: retry file missing or unreadable - re-run: python -m buyerlist tasks make 5c")
                if not cj.exists():
                    iss.error("retry", who, f"{role}: no round-2 source check - run: python -m buyerlist debate recheck")
                    continue
                fresh_rep = _r2_brief(run, side, b, role, keys, fetch=False)
                rep = read_json(cj)
                if rep.get("part_hashes") != fresh_rep["part_hashes"] or _strip_status(rep) != _strip_status(fresh_rep):
                    iss.error("retry", who, f"{role}: round-2 check does not match the retry files - run debate recheck")
                outs += [cj, cj.with_suffix(".md")] + [f for f in files if f.exists()]
            out = d / "controller_r2.md"
            if not fresh(out, controller2_inputs(run, side, b)):
                iss.error("retry", who, "Controller round 2 missing or made for older checks - run: python -m buyerlist tasks make 5c2")
            else:
                for x in controller_missing(run, side, b, "controller_r2.md", retry, r2=True)[:20]:
                    iss.error("retry", who, f"Controller round 2: {x}")
                outs += [out, manifest_path(out)]
    return iss, outs


# ---------------------------------------------------------------------------
# 5d - clean, anonymise, judge
# ---------------------------------------------------------------------------
def final_brief(run: Path, side: str, b: dict, role: str) -> tuple:
    """(brief with retried companies rebuilt point by point, merged decisions).
    For a retried company the round-2 point replaces the round-1 point with the
    same number; a round-1 point the retry left out is carried over with its
    round-1 decision."""
    d = bdir(run, side, b["batch"])
    r1 = read_json(check_json(run, side, b["batch"], role))
    dec = S.parse_decisions(read_text(d / "controller.md"))
    retry = read_json(d / "retry.json").get(role, [])
    if not retry:
        return r1, dec
    r2 = read_json(check_json(run, side, b["batch"], role, r2=True))
    dec2 = S.parse_decisions(read_text(d / "controller_r2.md"))
    keep = {"companies": [c for c in r1["companies"] if c["key"] not in retry], "sources": {}, "notes": r1.get("notes", [])}
    used = {s for c in keep["companies"] for a in c["arguments"] for s in a["sources"]}
    keep["sources"] = {k: v for k, v in r1["sources"].items() if k in used}
    rebuilt = []
    for key in retry:
        c2 = next((c for c in r2["companies"] if c["key"] == key), None)
        c1 = next((c for c in r1["companies"] if c["key"] == key), None)
        if c2 is None:
            continue
        n2 = {a["n"] for a in c2["arguments"]}
        carry = [a for a in (c1 or {}).get("arguments", []) if a["n"] not in n2]
        b2 = {"companies": [c2], "sources": {k: v for k, v in r2["sources"].items()
                                              if k in {s for a in c2["arguments"] for s in a["sources"]}}, "notes": []}
        b1 = {"companies": [dict(c1, arguments=carry)] if carry else [],
              "sources": {k: v for k, v in r1["sources"].items() if k in {s for a in carry for s in a["sources"]}},
              "notes": []}
        rebuilt.append((key, b2, b1, {a["n"] for a in carry}))
    parts = [keep]
    for key, b2, b1, carried in rebuilt:
        parts += [b2, b1]
    merged = S.merge(parts)
    # fold each retried company's two halves into one entry
    companies, seen = [], {}
    for c in merged["companies"]:
        if c["key"] in seen:
            seen[c["key"]]["arguments"] = sorted(seen[c["key"]]["arguments"] + c["arguments"], key=lambda a: a["n"])
        else:
            seen[c["key"]] = c
            companies.append(c)
    merged["companies"] = companies
    new_dec = {k: v for k, v in dec.items() if not (k[0] == role and k[1] in retry)}
    for key, b2, b1, carried in rebuilt:
        for n in carried:
            if (role, key, n) in dec:
                new_dec[(role, key, n)] = dec[(role, key, n)]
        for k, v in dec2.items():
            if k[0] == role and k[1] == key and k[2] not in carried:
                new_dec[k] = v
    order = {c["id"].lower(): i for i, c in enumerate(b["companies"])}
    merged["companies"].sort(key=lambda c: order.get(c["key"], 999))
    return merged, new_dec


def compute_clean(run: Path, side: str, b: dict) -> dict:
    m = load_mandate(run)
    a_role = S.side_label(f"{m['slug']}-{side}-b{b['batch']}")
    labels = {a_role: "A", ("critic" if a_role == "advocate" else "advocate"): "B"}
    keys = [c["id"].lower() for c in active(b)]
    info = {"A": a_role, "B": [r for r in ROLES if r != a_role][0], "kept": {}, "dropped": {},
            "mapping": {}, "kept_substantive": {}, "briefs": {}, "final": {}}
    for role in ROLES:
        brief, dec = final_brief(run, side, b, role)
        md, kept, dropped, mapping = S.clean(brief, role, dec, labels[role], keys)
        info["briefs"][labels[role]] = md
        info["kept"][role], info["dropped"][role] = kept, dropped
        info["mapping"][role] = mapping
        info["kept_substantive"][role] = S.kept_substantive(brief, role, dec)
        info["final"][role] = brief
    return info


def clean(run: Path) -> dict:
    out = {}
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            info = compute_clean(run, side, b)
            for lbl, md in info.pop("briefs").items():
                write_text(d / f"Brief {lbl}.md", md)
            for role, brief in info.pop("final").items():
                write_json(d / f"final_{role}.json", brief)
            write_json(d / ".sides.json", info)
            out[f"{side} b{b['batch']:02d}"] = {r: info["kept"][r] for r in ROLES}
    judge_tasks(run)
    return out


def judge_inputs(run: Path, side: str, b: dict) -> str:
    d = bdir(run, side, b["batch"])
    comps = "|".join(f"{c['heading']}::{c['context']}" for c in active(b))
    return digest([client_sheet_path(run), d / "Brief A.md", d / "Brief B.md"], comps)


def judge_tasks(run: Path) -> list:
    cs = client_sheet_path(run)
    tasks = []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            out = d / "verdict.md"
            dg = judge_inputs(run, side, b)
            if not issue(out, dg) and not verdict_problems(run, side, b):
                continue
            if out.exists():
                out.unlink()
                write_json(manifest_path(out), {"digest": dg, "issued_at": time.time()})
            comps = "\n".join(f"- {c['heading']}" + (f" ({c['context']})" if c["context"] else "") for c in active(b))
            body = (f"{mandate_line(side)}\nClient sheet: {cs}\nBrief A: {d / 'Brief A.md'}\n"
                    f"Brief B: {d / 'Brief B.md'}\nCompanies ({len(active(b))}):\n{comps}\nSave to: {out}\n")
            tasks.append({"id": f"{side[0]}b{b['batch']:02d}-judge", "agent": AGENT[(side, "judge")],
                          "body": body, "outputs": [out]})
    return write_tasks(run, "5d", tasks)


def valid_ids(run: Path, side: str, b: dict) -> set:
    d = bdir(run, side, b["batch"])
    ids = set(fact_ids(run))
    for lbl in ("A", "B"):
        p = d / f"Brief {lbl}.md"
        if p.exists():
            ids |= set(re.findall(r"^\s*-\s*\[([AB]\d+)\]", read_text(p), re.M))
    return ids


def verdict_problems(run: Path, side: str, b: dict) -> list:
    d = bdir(run, side, b["batch"])
    p = d / "verdict.md"
    if not p.exists():
        return ["verdict.md missing"]
    v = S.parse_verdict(read_text(p), side)
    probs = []
    for c in active(b):
        e = v.get(c["id"].lower())
        if e is None:
            probs.append(f"{c['heading']}: no verdict block ('## <Company> - <score>')")
            continue
        for lbl, dim in S.SIDES[side]["judge"]:
            sc = e["scores"].get(dim)
            if not sc or not 1 <= sc["score"] <= 5:
                probs.append(f"{c['heading']}: {lbl} score missing or not 1-5")
        if not e["confidence"]:
            probs.append(f"{c['heading']}: confidence missing")
        if side == "strategic" and not e["role"]:
            probs.append(f"{c['heading']}: Role not readable ({e.get('role_text')!r}) - "
                         "must start with horizontal / customer / supplier")
    return probs


def gate5d(run: Path) -> tuple:
    iss, outs = Issues(), []
    for side in sides(run):
        for b in load_batches(run, side):
            d = bdir(run, side, b["batch"])
            who = f"{side} b{b['batch']:02d}"
            missing = [f for f in ("Brief A.md", "Brief B.md", ".sides.json") if not (d / f).exists()]
            if missing:
                iss.error("judge", who, f"{', '.join(missing)} missing - run: python -m buyerlist debate clean")
                continue
            info = compute_clean(run, side, b)
            briefs, finals = info.pop("briefs"), info.pop("final")
            if any((d / f"Brief {l}.md").read_text(encoding="utf-8") != md for l, md in briefs.items()):
                iss.error("judge", who, "Brief A/B do not match the briefs and Controller decisions - run debate clean")
                continue
            if read_json(d / ".sides.json") != json.loads(json.dumps(info)) or any(
                    read_json(d / f"final_{r}.json") != json.loads(json.dumps(finals[r])) for r in ROLES):
                iss.error("judge", who, ".sides.json / final briefs do not match - run debate clean")
                continue
            out = d / "verdict.md"
            if not fresh(out, judge_inputs(run, side, b)):
                iss.error("judge", who, "verdict missing or made for older Brief A/B - run: python -m buyerlist tasks make 5d")
                continue
            for pr in verdict_problems(run, side, b):
                iss.error("judge", who, pr + " - re-run the judge (tasks make 5d)")
            outs += [d / "Brief A.md", d / "Brief B.md", d / ".sides.json", out, manifest_path(out),
                     d / "final_advocate.json", d / "final_critic.json"]
    return iss, [o for o in outs if o.exists()]


# ---------------------------------------------------------------------------
# 5e - collect
# ---------------------------------------------------------------------------
def scores_path(run: Path, side: str) -> Path:
    return ddir(run) / f"scores_{side}.json"


def _resolve(s: str, srcmap: dict) -> list:
    return [srcmap[c] for c in re.findall(r"\b([AB]\d+)\b", s or "") if c in srcmap]


def compute_scores(run: Path, side: str) -> dict:
    m = load_mandate(run)
    w = weights_for(side, m)
    out, kept_tot, drop_tot, retried = {}, {r: 0 for r in ROLES}, {r: 0 for r in ROLES}, 0
    for b in load_batches(run, side):
        d = bdir(run, side, b["batch"])
        info = read_json(d / ".sides.json")
        verdict = S.parse_verdict(read_text(d / "verdict.md"), side)
        problems = S.judge_check(verdict, side, valid_ids(run, side, b))
        srcmap = {}
        for lbl in ("A", "B"):
            for line in read_text(d / f"Brief {lbl}.md").splitlines():
                mm = BRIEF_SRC_RE.match(line)
                if mm:
                    srcmap[mm.group(1)] = S.parse_source(mm.group(2))
        retry = read_json(d / "retry.json")
        for role in ROLES:
            kept_tot[role] += info["kept"][role]
            drop_tot[role] += len(info["dropped"][role])
        for c in active(b):
            key = c["id"].lower()
            e = verdict[key]
            scores = {dim: e["scores"][dim]["score"] for _, dim in S.SIDES[side]["judge"]}
            fit = weighted(scores, w)
            survived = sum(info["kept_substantive"][role].get(key, 0) for role in ROLES)
            own = [p for p in problems if p[0] == key]
            argued = S.SIDES[side]["argued"]
            no_source = [p for p in own if p[1] in argued and p[2] == "no valid source cited"]
            if survived <= 2 or no_source:
                mech = "Low"
            elif survived >= 5:
                mech = "High"
            else:
                mech = "Medium"
            stated = e["confidence"] or "Low"
            conf = min((mech, stated), key=lambda x: CONF_ORDER.get(x, 0))
            out[c["id"]] = {
                "id": c["id"], "name": c["name"], "batch": b["batch"], "scores": scores,
                "reasons": {dim: S.strip_cites(e["scores"][dim]["reason"]) for _, dim in S.SIDES[side]["judge"]},
                "fit": fit, "stated_fit": e["stated_fit"], "calc": calc_text(scores, w),
                "fit_mismatch": abs(fit - e["stated_fit"]) > 0.05,
                "confidence": conf, "confidence_mech": mech, "confidence_judge": stated,
                "confidence_reason": e["confidence_reason"], "survived_points": survived,
                "deciding": S.strip_cites(e["deciding"]), "against": S.strip_cites(e["against"]),
                "deciding_sources": _resolve(e["deciding"], srcmap),
                "against_sources": _resolve(e["against"], srcmap),
                "dim_sources": {dim: _resolve(e["scores"][dim]["reason"], srcmap) for _, dim in S.SIDES[side]["judge"]},
                "role": e["role"] if side == "strategic" else "",
                "judge_problems": [f"{p[1]}: {p[2]}" for p in own],
                "retried": any(key in retry.get(r, []) for r in ROLES)}
            retried += int(out[c["id"]]["retried"])
    return {"side": side, "weights": w, "companies": out, "points": {"kept": kept_tot, "dropped": drop_tot},
            "companies_retried": retried}


def platform_portfolio(run: Path, data: dict) -> dict:
    """PE platform rule in the debated financial scores (CLAUDE.md): the judge leaves PE-owned holdings
    out of Portfolio; an investor with a qualifying platform gets Portfolio 5 here, as in fiscore."""
    from .platforms import qualify, rerouted
    w = data["weights"]
    plats = rerouted(run, compute_scores(run, "strategic")["companies"])
    qualify(plats, data["companies"], load_mandate(run))
    for pl in plats:
        for fid in pl["listed_with"]:
            c = data["companies"].get(fid)
            if c and c["scores"].get("portfolio") != 5:
                c["scores"]["portfolio"] = 5
                c["reasons"]["portfolio"] = (f"qualifying PE platform {pl['name']} (platform rule; judge's score "
                                             f"replaced): {c['reasons'].get('portfolio', '')}")
                c["fit"] = weighted(c["scores"], w)
                c["calc"] = calc_text(c["scores"], w)
    return data


def side_scores(run: Path, side: str) -> dict:
    """Scores of one side: from the judge verdicts, or profile-based for an undebated financial side."""
    if side in sides(run):
        data = compute_scores(run, side)
        return platform_portfolio(run, data) if side == "financial" else data
    if side == "financial" and options(run).get("financial_scoring") == "profile":
        from .fiscore import compute
        return compute(run)
    raise StageError(f"{side} side is neither debated nor scored from profiles - check the run options")


def collect(run: Path) -> dict:
    summary = {}
    for side in SIDES:
        data = side_scores(run, side)
        write_json(scores_path(run, side), data)
        out = data["companies"]
        summary[side] = {"companies": len(out), "kept": data["points"]["kept"], "dropped": data["points"]["dropped"],
                         "retried": data["companies_retried"],
                         "fit_mismatches": sum(1 for v in out.values() if v["fit_mismatch"])}
    return summary


def gate5e(run: Path) -> tuple:
    iss, outs = Issues(), []
    scope = debate_scope(run)
    for side in SIDES:
        p = scores_path(run, side)
        if not p.exists():
            iss.error("collect", side, "scores missing - run: python -m buyerlist debate collect")
            continue
        outs.append(p)
        data = read_json(p)
        if data != json.loads(json.dumps(side_scores(run, side))):
            iss.error("collect", side, "scores file does not match the verdicts - run: python -m buyerlist debate collect")
        sc = data["companies"]
        in_scope = {c["id"] for c in scope[side]}
        for cid in sorted(in_scope - set(sc)):
            iss.error("collect", cid, "no adjudicated score (debate incomplete)")
        for cid in sorted(set(sc) - in_scope):
            iss.error("collect", cid, "scored but no longer in the debate scope - run debate prepare")
        for cid, v in sc.items():
            if v["fit_mismatch"]:
                iss.warn("collect", cid, f"judge stated {v['stated_fit']}, weights give {v['fit']} - computed value used")
            for pr in v["judge_problems"]:
                iss.warn("collect", cid, f"judge: {pr}")
    return iss, outs
