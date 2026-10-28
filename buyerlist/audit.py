"""Stage 7 - skeptic audit of the selected rows.

Sample (stable per id, so a re-assembled list only adds new rows to audit):
all rank-1 strategics, every PE reroute, plus every selected row whose id hashes
into a ~1-in-6 bucket; in a review round (not the final one) also every row that is
new in the round. Independent skeptic agents try to REFUTE the key claims
of each sampled row. A finding counts only for the exact claims it was given
(claim hash); changed rows are audited again. Every claim of every sampled row
needs a finding, unreadable result files fail the gate, and every "wrong"
finding must be resolved (fixed upstream, or accepted / false alarm with a note).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .assemble import load_list
from .common import Issues, StageError, chunks, read_json, text, write_json
from .figures import display
from .mandate import client_sheet_path, load_mandate
from .tasks import write_tasks

VERDICTS = ("confirmed", "wrong", "unverifiable")
ACTIONS = ("fixed", "accepted", "false_alarm")
BUCKET = 6


def adir(run: Path) -> Path:
    return run / "07_audit"


def _h(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def claims(kind: str, r: dict, m: dict) -> list:
    thr = m["revenue_gate"]["min_revenue"]
    if kind == "strategic":
        rev = r.get("revenue") or {}
        return [f"Revenue: {display(rev, 1)} EUR m FY{r['fiscal_year']} ({rev.get('status')}, "
                f"source: {rev.get('source') or rev.get('method')}) - must be > EUR {thr:g}m",
                f"Ownership: not owned by a PE / infrastructure / growth fund (shareholders: "
                f"{r['shareholders'].replace(chr(10), '; ')})",
                f"Website {r['website']} is the company's own site; HQ country {r['country']}"]
    if kind == "financial":
        return [f"Ticket: {r['ticket']}", f"Geographic focus: {r['geo_focus']}",
                f"Sector focus: {r['sector_focus']}", f"Website {r['website']} is the investor's own site"]
    return [f"{r['name']} is PE-owned: {', '.join(r['sponsor_names'])} together hold > 50% or control it"]


def sample(run: Path) -> list:
    """[(kind, id, name, website, claims)] - deterministic and stable per id."""
    d = load_list(run)
    m = load_mandate(run)
    slug = m["slug"]
    rows = []
    rnd = d.get("round") or {"final": True}
    # in a review round (not the final one) every new row is audited: the analyst researches each of them
    new = (lambda r: not rnd["final"] and r.get("round_added") == rnd.get("no"))
    for r in d["strategic"]["selected"]:
        if r["rank"] == 1 or r.get("ownership_unverified") or _h(f"{slug}:{r['id']}") % BUCKET == 0 or new(r):
            rows.append(("strategic", r))
    for r in d["financial"]["selected"]:
        if _h(f"{slug}:{r['id']}") % BUCKET == 0 or new(r):
            rows.append(("financial", r))
    for pl in d["rerouted"]:
        rows.append(("platform", pl))
    out = []
    for kind, r in rows:
        cid = r.get("id") or r.get("strategic_id")
        out.append({"kind": kind, "id": cid, "name": r["name"], "website": r.get("website", ""),
                    "claims": claims(kind, r, m)})
    return out


def claims_hash(x: dict) -> str:
    return hashlib.sha256(json.dumps(x["claims"], ensure_ascii=False).encode()).hexdigest()[:16]


def _manifests(run: Path) -> dict:
    """audit file name -> {id: claims_hash} it was issued for."""
    out = {}
    for f in sorted(adir(run).glob("audit-*.inputs.json")):
        out[f.name.replace(".inputs.json", ".json")] = read_json(f).get("ids", {})
    return out


def valid_findings(run: Path) -> tuple:
    """({(id, claim_no): [findings]} for the current claims, [file problems])"""
    current = {x["id"]: claims_hash(x) for x in sample(run)}
    mans = _manifests(run)
    good, problems = {}, []
    for fname, ids in mans.items():
        f = adir(run) / fname
        if not f.exists():
            continue
        try:
            data = read_json(f)
            fs = data["findings"]
            assert isinstance(fs, list)
        except Exception:
            problems.append(f"{fname}: unreadable or no 'findings' list - re-run: python -m buyerlist audit prepare")
            continue
        for x in fs:
            if not isinstance(x, dict):
                problems.append(f"{fname}: a finding is not an object")
                continue
            cid = text(str(x.get("id", ""))).strip("[]")
            try:
                n = int(x.get("claim"))
            except (TypeError, ValueError):
                problems.append(f"{fname}: finding for {cid} without a claim number")
                continue
            if cid in ids and current.get(cid) == ids[cid]:
                good.setdefault((cid, n), []).append(dict(x, id=cid, claim=n, _file=fname))
    return good, problems


def prepare(run: Path, per_agent: int = 12) -> list:
    rows = sample(run)
    good, _ = valid_findings(run)
    todo = [x for x in rows if any((x["id"], j) not in good for j in range(1, len(x["claims"]) + 1))]
    write_json(adir(run) / "sample.json", {"list_sha": hashlib.sha256(json.dumps(load_list(run), sort_keys=True).encode()).hexdigest(),
                                           "rows": [{"kind": x["kind"], "id": x["id"], "name": x["name"],
                                                     "claims_hash": claims_hash(x)} for x in rows]})
    start = 1 + max([int(f.stem.split("-")[1]) for f in adir(run).glob("audit-*.json")
                     if f.stem.split("-")[1].isdigit()] or [0])
    tasks = []
    for i, chunk in enumerate(chunks(todo, per_agent), start):
        out = adir(run) / f"audit-{i:02d}.json"
        lines = []
        for x in chunk:
            lines.append(f"- [{x['id']}] {x['name']} ({x['website']}) - {x['kind']}")
            for j, cl in enumerate(x["claims"], 1):
                lines.append(f"    claim {j}: {cl}")
        body = f"""Skeptic audit of a buyer list. Try to REFUTE each claim below with your own research.
Client (context only): {client_sheet_path(run)}

{chr(10).join(lines)}

For every claim decide:
- confirmed: you found a reliable source that supports it (give the URL);
- wrong: a reliable source contradicts it (give the correct value and the URL);
- unverifiable: you could not settle it with reasonable effort (say what you tried).
Default to "unverifiable", not "confirmed", when unsure. Annual reports, official registers and
the company's own pages beat press, press beats directories. Web search is scarce: open the
company's own pages directly where you can.
Save to: {out}
Format (JSON): {{"findings": [{{"id": "<id in brackets, e.g. s-012>", "claim": <claim number>,
  "verdict": "confirmed|wrong|unverifiable", "detail": "<one sentence>", "correct_value": "<if wrong>",
  "source": "<URL>"}}]}}  - one finding for EVERY claim of EVERY company above.
At most 2 searches and 4 page reads per company. Reply only: "done - <n> findings, <w> wrong".
"""
        write_json(adir(run) / f"audit-{i:02d}.inputs.json", {"ids": {x["id"]: claims_hash(x) for x in chunk}})
        tasks.append({"id": f"audit-{i:02d}", "agent": "bl-auditor", "body": body, "outputs": [out]})
    return write_tasks(run, "7", tasks)


def findings(run: Path) -> list:
    good, _ = valid_findings(run)
    return [f for k in sorted(good) for f in good[k]]


def resolutions_path(run: Path) -> Path:
    return adir(run) / "resolutions.json"


def resolve(run: Path, cid: str, claim: int, action: str, note: str) -> dict:
    if action not in ACTIONS:
        raise StageError(f"action must be one of {', '.join(ACTIONS)}")
    if not note.strip():
        raise StageError("a resolution needs a note (what was fixed, or why it is accepted)")
    p = resolutions_path(run)
    res = read_json(p) if p.exists() else {}
    good, _ = valid_findings(run)
    fs = good.get((cid, claim))
    if not fs:
        raise StageError(f"no current finding {cid}#{claim}")
    wrong = [f for f in fs if text(f.get("verdict")).lower() == "wrong"]
    if not wrong:
        raise StageError(f"{cid}#{claim} has no 'wrong' finding to resolve")
    ch = {x["id"]: claims_hash(x) for x in sample(run)}.get(cid)
    res[f"{cid}#{claim}"] = {"action": action, "note": note, "claims_hash": ch,
                             "files": sorted(f["_file"] for f in wrong), "detail": wrong[0].get("detail")}
    write_json(p, res)
    return res


def gate7(run: Path) -> tuple:
    iss, outs = Issues(), []
    sp = adir(run) / "sample.json"
    if not sp.exists():
        iss.error("audit", "sample", "no audit yet - run: python -m buyerlist audit prepare")
        return iss, []
    rows = sample(run)
    stored = read_json(sp)
    if [r["id"] for r in stored["rows"]] != [x["id"] for x in rows] or \
            [r["claims_hash"] for r in stored["rows"]] != [claims_hash(x) for x in rows]:
        iss.error("audit", "sample", "the list changed since the audit was prepared - run: python -m buyerlist audit prepare")
    good, problems = valid_findings(run)
    for p in problems:
        iss.error("audit", "file", p)
    for x in rows:
        for j in range(1, len(x["claims"]) + 1):
            if (x["id"], j) not in good:
                iss.error("audit", f"{x['id']}#{j}", "no finding for this claim - run audit prepare and the audit tasks")
    res = read_json(resolutions_path(run)) if resolutions_path(run).exists() else {}
    hashes = {x["id"]: claims_hash(x) for x in rows}
    flat = []
    for (cid, n), fs in sorted(good.items()):
        key = f"{cid}#{n}"
        r = res.get(key) or {}
        for f in fs:
            flat.append(f)
            v = text(f.get("verdict")).lower()
            if v not in VERDICTS:
                iss.error("audit", key, f"verdict {f.get('verdict')!r} invalid ({f['_file']})")
            elif v == "wrong" and not (r.get("claims_hash") == hashes.get(cid) and f["_file"] in r.get("files", [])):
                iss.error("audit", key, f"WRONG: {f.get('detail')} (correct: {f.get('correct_value')}; {f.get('source')}) - "
                          "fix it upstream (tasks make 3 --ids ... --note ..., regate, assemble, gate 6) and/or run: "
                          f"python -m buyerlist audit resolve {cid} {n} --action fixed|accepted|false_alarm --note '...'")
            elif v == "unverifiable":
                iss.warn("audit", key, f"unverifiable: {f.get('detail')}")
    wrong = sum(1 for f in flat if text(f.get("verdict")).lower() == "wrong")
    conf = sum(1 for f in flat if text(f.get("verdict")).lower() == "confirmed")
    summary = {"rows": len(rows), "findings": len(flat), "confirmed": conf, "wrong": wrong,
               "unverifiable": len(flat) - conf - wrong, "resolutions": len(res)}
    if iss.ok():
        write_json(adir(run) / "summary.json", summary)
    outs = [sp] + [adir(run) / n for n in _manifests(run)] + sorted(adir(run).glob("audit-*.inputs.json"))
    if resolutions_path(run).exists():
        outs.append(resolutions_path(run))
    if (adir(run) / "summary.json").exists():
        outs.append(adir(run) / "summary.json")
    return iss, [o for o in outs if o.exists()]
