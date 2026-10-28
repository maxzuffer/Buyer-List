"""Stage 4 - revenue gate and PE reroute.

* Strategic candidates need proven revenue above the client's (strict gate).
* Every PE-owned strategic (sponsors together > 50%, or one sponsor in control,
  counted through ultimate owners) leaves the strategic list. Its sponsor(s) are
  scored as financial investors; the strategic is shown as a labelled portfolio
  company only where it qualifies as that sponsor's platform (platforms.py, stage 6).
  Sponsors not in the financial universe are added here (ids f-r001 ...; the
  id of a sponsor stays the same across re-runs) and must be profiled (step 4).
  The additions are rebuilt on every run, so a sponsor that later enters the
  universe through sourcing is not kept twice.
* Debate scope: strategic side = revenue PASS non-PE strategics + all PE-owned
  strategics (their fit feeds the sponsor's rank); financial side = every
  financial candidate incl. the added sponsors.
The gate recomputes the route from the current profiles and compares.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import Issues, read_json, text, write_json
from .mandate import as_of, load_mandate
from .profiles import load_profile, profile_path, revenue_gate, sponsors, tasks_for, validate
from .universe import country, load_universe, name_key, registrable_domain


def route_path(run: Path) -> Path:
    return run / "04_route.json"


def ids_path(run: Path) -> Path:
    return run / "04_route_ids.json"


def load_route(run: Path) -> dict:
    return read_json(route_path(run))


SPONSOR_NOISE = re.compile(r"\b(funds?|managed|by|vehicles?|investment|investments|advised|adviser|entities|"
                           r"affiliates?|lps?|limited partners|holding|holdings|via|its|and|the|of|infrastructure funds?)\b")


def clean_sponsor(name: str) -> str:
    """'Arcus Infrastructure Partners - independent fund manager (...)' -> 'arcus infrastructure partners'"""
    s = re.split(r"\s+[-–—]\s+|[(;,]", text(name))[0]
    s = re.sub(r"^(funds?|vehicles?)\s+(managed|advised)\s+by\s+", "", s, flags=re.I)
    return name_key(s)


def sponsor_match(o: dict, fin: list):
    """Financial candidate for a sponsor: same domain, same cleaned name, or one name containing the other."""
    dom = registrable_domain(o.get("website", "")) if o.get("website") else ""
    for c in fin:
        if dom and c["domain"] == dom:
            return c
    mgr = re.search(r"(?:managed|advised)\s+by\s+([^,;)]+)", text(o.get("name", "")), re.I)
    if mgr:
        m = sponsor_match({"name": mgr.group(1)}, fin)
        if m:
            return m
    key = clean_sponsor(o.get("name", ""))
    core = SPONSOR_NOISE.sub(" ", key).split()
    for c in fin:
        if name_key(c["name"]) == key:
            return c
    for c in fin:
        ck = SPONSOR_NOISE.sub(" ", name_key(c["name"])).split()
        if ck and core and len(" ".join(ck)) >= 4 and (" ".join(core).startswith(" ".join(ck))
                                                       or " ".join(ck).startswith(" ".join(core))):
            return c
    return None


def compute(run: Path) -> tuple:
    """(route, sponsor-id map) from the current universe and profiles - writes nothing."""
    m = load_mandate(run)
    min_rev = float(m["revenue_gate"]["min_revenue"])
    uni = load_universe(run)
    id_map = read_json(ids_path(run)) if ids_path(run).exists() else {"next": 1, "keys": {}}
    id_map = {"next": id_map["next"], "keys": dict(id_map["keys"])}
    by_key = {}
    for c in uni["candidates"]:
        by_key.setdefault(f"d:{c['domain']}", (c["id"], c["type"]))
        by_key.setdefault(f"n:{name_key(c['name'])}", (c["id"], c["type"]))
    strat_keys = {}
    for c in uni["candidates"]:
        if c["type"] == "strategic":
            strat_keys.setdefault(f"d:{c['domain']}", c["id"])
            strat_keys.setdefault(f"n:{name_key(c['name'])}", c["id"])
    fin_cands = [c for c in uni["candidates"] if c["type"] == "financial"]
    strat_cands = [c for c in uni["candidates"] if c["type"] == "strategic"]
    strategic, added, links, conflicts = [], {}, [], []
    for c in uni["candidates"]:
        if c["type"] != "strategic":
            continue
        p = load_profile(run, c["id"]) or {}
        gate, note = revenue_gate(p, min_rev)
        pe = p.get("pe_owned") is True
        row = {"id": c["id"], "name": c["name"], "revenue_gate": gate, "revenue_note": note,
               "pe_owned": pe, "sponsors": []}
        parent = None if pe else group_parent(p, c["id"], strat_keys)
        if parent:
            row["candidate_parent"] = parent           # applied below once the parent's own route is known
        if pe:
            row["route"] = "reroute"
            for o in sponsors(p):
                dom = registrable_domain(o.get("website", "")) if o.get("website") else ""
                keys = [f"d:{dom}"] if dom else []
                keys.append(f"n:{clean_sponsor(o['name'])}")
                hit = next((by_key[k] for k in keys if k in by_key), None)
                if hit is None:
                    m = sponsor_match(o, fin_cands + [dict(a, domain=a.get("domain", "")) for a in added.values()])
                    hit = (m["id"], "financial") if m else None
                if hit is None:
                    m = sponsor_match(o, strat_cands)
                    hit = (m["id"], "strategic") if m else None
                if hit and hit[1] == "strategic":
                    conflicts.append(f"{c['id']}: sponsor {o['name']} is in the universe as strategic {hit[0]} - "
                                     "fix its type in the raw file (it is a financial investor)")
                    continue
                if hit:
                    fid = hit[0]
                else:
                    akey = keys[0]
                    fid = id_map["keys"].get(akey) or next((id_map["keys"][k] for k in keys if k in id_map["keys"]), None)
                    if fid is None:
                        fid = f"f-r{id_map['next']:03d}"
                        id_map["next"] += 1
                    for k in keys:
                        id_map["keys"][k] = fid
                    a = added.setdefault(fid, {"id": fid, "key": akey,
                                               "name": re.split(r"\s+[-–—]\s+|[(;]", text(o["name"]))[0].strip(),
                                               "website": text(o.get("website")), "domain": dom,
                                               "hq_country": country(o.get("country", ""))[:2] if o.get("country") else "",
                                               "hq_city": "", "type": "financial", "segment": text(o.get("type")),
                                               "from_strategic": []})
                    a["from_strategic"].append(c["id"])
                    for k in keys:
                        by_key[k] = (fid, "financial")
                row["sponsors"].append({"fi_id": fid, "name": o["name"], "stake_pct": o.get("stake_pct"),
                                        "type": o.get("type")})
                links.append({"strategic_id": c["id"], "fi_id": fid})
        elif gate == "PASS":
            row["route"] = "strategic"
        else:
            row["route"] = "excluded"
        strategic.append(row)
    # a subsidiary is folded into its parent only if the parent itself stays on a list
    by_id = {r["id"]: r for r in strategic}
    for r in strategic:
        pid = r.pop("candidate_parent", None)
        if pid and by_id.get(pid, {}).get("route") in ("strategic", "reroute") and r["route"] != "reroute":
            r["route"] = "group_duplicate"
            r["parent"] = pid
    route = {"strategic": strategic, "financial_added": sorted(added.values(), key=lambda a: a["id"]),
             "links": links, "conflicts": conflicts,
             "counts": {"strategic": sum(r["route"] == "strategic" for r in strategic),
                        "reroute": sum(r["route"] == "reroute" for r in strategic),
                        "excluded": sum(r["route"] == "excluded" for r in strategic),
                        "group_duplicate": sum(r["route"] == "group_duplicate" for r in strategic),
                        "financial_added": len(added)}}
    return route, id_map


def group_parent(p: dict, own_id: str, strat_keys: dict):
    """Id of another strategic candidate that controls this one (directly or as ultimate owner):
    the group is listed once, at the parent."""
    from .profiles import _owners
    for o in _owners(p):
        big = (isinstance(o.get("stake_pct"), (int, float)) and o["stake_pct"] >= 50) or o.get("controlling") is True
        if not big or text(o.get("via")):
            continue
        cands = [o]
        if isinstance(o.get("ultimate_owner"), dict):
            cands.append(o["ultimate_owner"])
        for x in cands:
            keys = [f"n:{name_key(x.get('name', ''))}"]
            if x.get("website"):
                keys.insert(0, f"d:{registrable_domain(x['website'])}")
            for k in keys:
                pid = strat_keys.get(k)
                if pid and pid != own_id:
                    return pid
    return None


def build(run: Path) -> dict:
    route, id_map = compute(run)
    write_json(ids_path(run), id_map)
    write_json(route_path(run), route)
    return route


def added_candidates(run: Path) -> list:
    if not route_path(run).exists():
        return []
    return [dict(a, type="financial") for a in load_route(run).get("financial_added", [])]


def debate_scope(run: Path) -> dict:
    """Candidates per debate side, in id order."""
    uni = load_universe(run)
    route = load_route(run)
    rmap = {r["id"]: r for r in route["strategic"]}
    strat = [c for c in uni["candidates"] if c["type"] == "strategic"
             and rmap.get(c["id"], {}).get("route") in ("strategic", "reroute")]
    fin = [c for c in uni["candidates"] if c["type"] == "financial"] + added_candidates(run)
    return {"strategic": strat, "financial": fin}


def sponsor_tasks(run: Path) -> list:
    m = load_mandate(run)
    a = as_of(m)
    todo = [c for c in added_candidates(run) if not validate(run, c, a).ok()]
    return tasks_for(run, todo, "4", m["batching"]["profiles_per_agent"]) if todo else []


def gate4(run: Path) -> tuple:
    iss = Issues()
    if not route_path(run).exists():
        iss.error("route", "04_route.json", "missing - run: python -m buyerlist route")
        return iss, []
    route = load_route(run)
    fresh, _ = compute(run)
    if route != fresh:
        iss.error("route", "04_route.json", "does not match the current profiles (revenue, PE owners or sponsors "
                  "changed) - run: python -m buyerlist route")
    for c in route.get("conflicts", []):
        iss.error("route", "sponsor", c)
    for r in route["strategic"]:
        if r["route"] == "reroute" and not r["sponsors"]:
            iss.error("route", r["id"], "PE-owned but no sponsor resolved")
        if r["route"] == "strategic" and r["revenue_gate"] != "PASS":
            iss.error("route", r["id"], "on the strategic route without revenue PASS")
    m = load_mandate(run)
    a = as_of(m)
    outs = [route_path(run), ids_path(run)]
    for c in added_candidates(run):
        iss.extend(validate(run, c, a))
        outs.append(profile_path(run, c["id"]))
    from .rounds import reprofile_problems
    for msg in reprofile_problems(run, {c["id"] for c in added_candidates(run)}):
        iss.error("route", "analyst feedback", msg)
    return iss, [o for o in outs if Path(o).exists()]
