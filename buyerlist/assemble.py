"""Stage 6 - ranking, PE reroute merge and the 100 + 100 selection -> list_data.json.

The list is a pure function of the route, the profiles and the debate scores;
gate 6 recomputes it and compares with the file, so the file cannot be padded
or edited by hand. A PE-owned strategic is shown under a sponsor only when it
qualifies as that sponsor's platform (platforms.py); every qualifying platform
keeps at least one of its qualifying sponsors on the financial list (they are
selected first). Platforms that do not qualify are listed on the Excluded sheet.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import rounds as R
from .common import Issues, is_num, is_url, read_json, text, write_json
from .figures import display
from .mandate import as_of, load_mandate
from .profiles import (OVERLAPS, capability, deployment_rating, load_profile, ma_rating, ownership_unverified,
                       pe_owned_calc, revenue_gate, _dicts)
from .platforms import close_holdings, qualify, rerouted
from .route import added_candidates, load_route
from .scoring import best_rank, expected_rank, own_fit, platform_check, platform_rules, strategic_section, weights_for
from .universe import load_universe, registrable_domain

CONF_RANK = {"High": 0, "Medium": 1, "Low": 2}
PLATFORM_MARK = "▶ PLATFORM:"


def list_path(run: Path) -> Path:
    return run / "06_list_data.json"


def shortfall_path(run: Path) -> Path:
    return run / "06_shortfall.json"


def load_list(run: Path) -> dict:
    return read_json(list_path(run))


def shareholders_text(p: dict) -> str:
    owners = sorted([o for o in _dicts(p.get("owners")) if is_num(o.get("stake_pct")) and o["stake_pct"] >= 10
                     and not text(o.get("via"))], key=lambda o: -o["stake_pct"])
    lines = []
    for o in owners:
        u = o.get("ultimate_owner") if isinstance(o.get("ultimate_owner"), dict) else None
        extra = f" (ultimately {u['name']})" if u and text(u.get("name")) and u["name"] != o["name"] else ""
        lines.append(f"{o['stake_pct']:g}% {o['name']}{extra}")
    if p.get("listed"):
        lines = ["Public"] + lines
    return "\n".join(lines) or text(p.get("ownership_type")) or "n.a."


def ma_text(p: dict, rating: str) -> str:
    ma = p.get("ma_activity") if isinstance(p.get("ma_activity"), dict) else {}
    deals = sorted([d for d in _dicts(ma.get("deals")) if is_num(d.get("year"))], key=lambda d: -d["year"])
    if not deals:
        return rating
    recent = "; ".join("{} {}".format(text(d.get("target")), int(d["year"])) for d in deals[:3])
    return f"{rating} ({recent})"


def ticket_text(p: dict) -> str:
    t = p.get("ticket") if isinstance(p.get("ticket"), dict) else {}
    st = text(t.get("status")).upper()
    parts = []
    for k, lbl in (("equity", "equity"), ("ev", "EV"), ("ebitda", "EBITDA"), ("revenue", "revenue")):
        v = t.get(k)
        if isinstance(v, list) and len(v) == 2:
            lo, hi = (v[0] if is_num(v[0]) else None), (v[1] if is_num(v[1]) else None)
            if lo is not None and hi is not None:
                parts.append(f"{lbl} EUR {lo:g}-{hi:g}m")
            elif lo is not None:
                parts.append(f"{lbl} EUR {lo:g}m+")
            elif hi is not None:
                parts.append(f"{lbl} up to EUR {hi:g}m")
    if not parts:
        return f"n/a ({text(t.get('reason'))})" if st == "N/A" else "n/a"
    return ", ".join(parts) + f" ({st.lower()})"


def fiscal_year(p: dict):
    for k in ("revenue", "ebitda"):
        f = p.get(k) if isinstance(p.get(k), dict) else {}
        fy = f.get("fy")
        if fy not in (None, "") and f.get("status") in ("VERIFIED", "ESTIMATE"):
            s = str(fy).strip()
            return int(s) if s.isdigit() else s
    return "n.a."


def select(ranked: list, side: str, key, rinfo: dict, carry: set, keep: set, shown: set, plat_must=()) -> tuple:
    """(selected, reserve, shortfall, overflow) for one side.
    Earlier listed names that were not dropped (carry) and analyst keeps stay; platform sponsors that
    must stay visible are added in the final round; new names (never shown before) fill the round's
    quota, or the target in the final round."""
    must = [r for r in ranked if r["id"] in carry or r["id"] in keep]
    if rinfo["final"]:
        must += [r for r in plat_must if r not in must]
        want = rinfo["targets"][side]
        new = [r for r in ranked if r not in must][:max(0, want - len(must))]
        short = max(0, want - len(must) - len(new))
    else:
        quota = rinfo["quota"][side]
        fresh_keep = [r for r in must if r["id"] not in shown]          # analyst-added keeps count as new names
        new = [r for r in ranked if r not in must and r["id"] not in shown][:max(0, quota - len(fresh_keep))]
        short = max(0, quota - len(fresh_keep) - len(new))
    sel = sorted(must + new, key=key)
    want_total = rinfo["targets"][side] if rinfo["final"] else None
    overflow = max(0, len(must) - want_total) if want_total is not None else 0
    return sel, [r for r in ranked if r not in sel], short, overflow


def compute(run: Path) -> dict:
    m = load_mandate(run)
    a = as_of(m)
    ev_hi = m["ev"]["value"][1]
    min_rev = float(m["revenue_gate"]["min_revenue"])
    uni = {c["id"]: c for c in load_universe(run)["candidates"]}
    for c in added_candidates(run):
        uni[c["id"]] = c
    # rounds and analyst feedback (only closed rounds count)
    rinfo = R.info(run, m)
    fb = R.effective(run)
    shown = R.shown(run)
    first = R.first_listed(run)
    dropped = {i for i, x in fb.items() if x["status"] == "drop"}
    added = R.added_ids(run)
    keep = {i for i, x in fb.items() if x["status"] == "keep"}
    keep |= {i for i, x in added.items() if x["status"] == "keep"}
    keep -= dropped

    def drop_row(base):
        x = fb[base["id"]]
        return {**base, "section": "analyst_drop", "reason": f"dropped by the analyst in round {x['round']}: {x['note']}"}
    route = load_route(run)
    s_scores = read_json(run / "05_debate" / "scores_strategic.json")["companies"]
    f_scores = read_json(run / "05_debate" / "scores_financial.json")["companies"]

    strategic_ranked, excluded_s = [], []
    plats = rerouted(run, s_scores)
    for r in route["strategic"]:
        c = uni[r["id"]]
        p = load_profile(run, r["id"]) or {}
        sc = s_scores.get(r["id"])
        base = {"id": r["id"], "name": text(p.get("name") or c["name"]),
                "country": p.get("hq_country") or c["hq_country"], "website": text(p.get("website") or c["website"])}
        if r["route"] == "excluded":
            excluded_s.append({**base, "section": "revenue_excluded",
                               "reason": f"revenue gate {r['revenue_gate']}: {r['revenue_note']}"})
            continue
        if r["route"] == "group_duplicate":
            excluded_s.append({**base, "section": "group_duplicate",
                               "reason": f"controlled by {uni.get(r['parent'], {}).get('name', r['parent'])} "
                                         f"({r['parent']}) - the group is listed once, at the parent"})
            continue
        if sc is None:
            excluded_s.append({**base, "section": "not_debated", "reason": "no adjudicated score"})
            continue
        if r["route"] == "reroute":
            continue                                   # a platform candidate (plats), decided below
        if r["id"] in dropped:
            excluded_s.append(drop_row(base))
            continue
        fit = sc["fit"]
        section, rank, reason = strategic_section(sc)
        rg, rnote = revenue_gate(p, min_rev)
        ma = ma_rating(p, a)
        cap = capability(p, ev_hi)
        row = {**base, "section": section, "reason": reason, "rank": rank if section == "ranked" else None,
               "fit": fit, "scores": sc["scores"], "reasons": sc["reasons"], "confidence": sc["confidence"],
               "for": sc["deciding"], "against": sc["against"], "role": sc["role"],
               "deciding_sources": sc["deciding_sources"], "against_sources": sc["against_sources"],
               "bereich": text(p.get("bereich")), "description": text(p.get("description")),
               "fiscal_year": fiscal_year(p),
               "revenue": p.get("revenue"), "ebitda": p.get("ebitda"), "employees": p.get("employees"),
               "shareholders": shareholders_text(p), "ownership_type": p.get("ownership_type"),
               "pe_owned": pe_owned_calc(p), "ownership_unverified": ownership_unverified(p),
               "revenue_gate": rg, "revenue_note": rnote,
               "ma_rating": ma, "ma_text": ma_text(p, ma), "capability": cap}
        subs = [x["name"] for x in route["strategic"] if x.get("parent") == r["id"]]
        row["comments"] = (f"Fit {fit:.1f}/5 ({sc['confidence']} confidence). For: {sc['deciding']} "
                           f"Against: {sc['against']} M&A activity: {row['ma_text']}. "
                           f"Financial capability: {cap['rating']} ({cap['justification']})."
                           + (f" Group incl. {', '.join(subs)}." if subs else "")
                           + (" Shareholders not disclosed - not verified PE-free." if ownership_unverified(p) else ""))
        (strategic_ranked if section == "ranked" else excluded_s).append(row)

    s_key = lambda r: (r["rank"], -r["fit"], CONF_RANK.get(r["confidence"], 3), r["name"].casefold())
    strategic_ranked.sort(key=s_key)
    s_sel, s_res, s_short, _ = select(strategic_ranked, "strategic", s_key, rinfo,
                                      shown["strategic"] - dropped, keep, shown["strategic"])

    # PE platforms: shown only under the sponsors they qualify for
    own = qualify(plats, f_scores, m)
    for pl in plats:
        if not pl["listed_with"]:
            excluded_s.append({"id": pl["strategic_id"], "name": pl["name"], "country": pl["country"],
                               "website": pl["website"], "section": "pe_platform_not_listed",
                               "reason": f"PE-owned ({', '.join(pl['sponsor_names'])}) - not shown as portfolio "
                                         f"company: {pl['reason']}"})

    # financial
    held = {}
    for pl in plats:
        for fid in pl["listed_with"]:
            held.setdefault(fid, []).append(pl)
    fin_ranked, excluded_f = [], []
    for fid, sc in f_scores.items():
        c = uni.get(fid, {})
        p = load_profile(run, fid) or {}
        if fid in dropped:
            excluded_f.append(drop_row({"id": fid, "name": text(p.get("name") or c.get("name")),
                                        "country": p.get("hq_country") or c.get("hq_country"),
                                        "website": text(p.get("website") or c.get("website"))}))
            continue
        pls = sorted(held.get(fid, []), key=lambda x: (x["rank"] or 9, -x["fit"]))
        fi_rank = expected_rank(sc["fit"])
        plat_rank = best_rank(*[x["rank"] for x in pls])
        final = best_rank(fi_rank, plat_rank)
        basis = ("platform" if plat_rank is not None and (fi_rank is None or plat_rank < fi_rank) else "investor fit")
        lines = [f"{PLATFORM_MARK} {x['name']} – PE-owned strategic moved from Strategic list "
                 f"(strategic fit {x['fit']:.1f}/5" + (f", rank {x['rank']})" if x["rank"] else ", not ranked)")
                 for x in pls]
        other = [text(h.get("name")) for h in close_holdings(p, plats, fid)]
        if other:
            lines.append("Relevant holdings: " + ", ".join(other[:5]))
        row = {"id": fid, "name": text(p.get("name") or c.get("name")), "country": p.get("hq_country") or c.get("hq_country"),
               "website": text(p.get("website") or c.get("website")), "investor_type": p.get("investor_type"),
               "description": text(p.get("description")), "portfolio_company": "\n".join(lines),
               "platforms": pls, "fit": sc["fit"], "own_fit": own.get(fid), "fi_rank": fi_rank,
               "platform_rank": plat_rank,
               "rank": final, "rank_basis": basis if final else "", "scores": sc["scores"], "reasons": sc["reasons"],
               "confidence": sc["confidence"], "for": sc["deciding"], "against": sc["against"],
               "deciding_sources": sc["deciding_sources"], "against_sources": sc["against_sources"],
               "ticket": ticket_text(p), "geo_focus": text((p.get("geo_focus") or {}).get("summary")) if isinstance(p.get("geo_focus"), dict) else "",
               "sector_focus": text((p.get("sector_focus") or {}).get("summary")) if isinstance(p.get("sector_focus"), dict) else "",
               "deployment": deployment_rating(p, a), "from_route": fid.startswith("f-r"),
               "section": "ranked" if final else "leermeldung",
               "reason": "" if final else f"adjudicated fit {sc['fit']:.1f} below 2.0 and no qualifying platform"}
        (fin_ranked if final else excluded_f).append(row)
    key = lambda r: (r["rank"], 0 if r["platform_rank"] else 1, -r["fit"],
                     CONF_RANK.get(r["confidence"], 3), r["name"].casefold())
    fin_ranked.sort(key=key)
    # every qualifying platform must stay visible in the final list: its best qualifying sponsor
    # (not dropped by the analyst) is selected first
    must = []
    carry_f = (shown["financial"] - dropped) | {k for k in keep if k.startswith("f-")}
    for pl in sorted(plats, key=lambda x: (x["rank"] or 9, -x["fit"])):
        pl["sponsors_dropped"] = [fid for fid in pl["listed_with"] if fid in dropped]
        avail = [fid for fid in pl["listed_with"] if fid not in dropped]
        if not avail or set(avail) & carry_f:          # nothing to add, or already visible via a carried sponsor
            continue
        cands = [r for r in fin_ranked if r["id"] in avail]
        if cands and not any(r in must for r in cands):
            must.append(cands[0])
    f_sel, f_res, f_short, overflow = select(fin_ranked, "financial", key, rinfo,
                                             shown["financial"] - dropped, keep, shown["financial"], must)

    # where each qualifying platform is visible; a platform whose qualifying sponsors the analyst
    # dropped is not shown - Excluded sheet and a conflict for the analyst
    conflicts = []
    sel_f_ids = {r["id"] for r in f_sel}
    for pl in plats:
        pl["shown_with"] = [fid for fid in pl["listed_with"] if fid in sel_f_ids]
        if pl["shown_with"] or not pl["listed_with"]:
            pl["status"] = "listed" if pl["shown_with"] else pl["status"]
            continue
        names = dict(zip(pl["sponsors"], pl["sponsor_names"]))
        if all(fid in dropped for fid in pl["listed_with"]):
            pl["status"] = "not shown - qualifying sponsor(s) dropped by the analyst"
            notes = "; ".join(f"{names[f]}: {fb[f]['note']}" for f in pl["listed_with"])
            excluded_s.append({"id": pl["strategic_id"], "name": pl["name"], "country": pl["country"],
                               "website": pl["website"], "section": "pe_platform_not_listed",
                               "reason": f"PE-owned - qualifies as platform of {', '.join(names[f] for f in pl['listed_with'])}"
                                         f", but the analyst dropped that sponsor ({notes})"})
            conflicts.append({"side": "financial", "id": pl["strategic_id"], "name": pl["name"],
                              "feedback": "; ".join(R.feedback_text(fb[f]) for f in pl["listed_with"]),
                              "why": f"qualifying PE platform (fit {pl['fit']:.1f}) is not shown because its "
                                     "qualifying sponsor(s) were dropped"})
        else:
            pl["status"] = "qualifies - no qualifying sponsor on this round's list"

    # round bookkeeping on the rows; names the analyst wanted that a hard rule now excludes
    for side, sel, ranked, excl in (("strategic", s_sel, strategic_ranked, excluded_s),
                                    ("financial", f_sel, fin_ranked, excluded_f)):
        for r in ranked:
            r["round_added"] = first.get(r["id"]) or (rinfo["no"] if r in sel else None)
            r["feedback_text"] = R.feedback_text(fb.get(r["id"]) or added.get(r["id"]))
        why = {x["id"]: f"{x['section']}: {x.get('reason', '')}" for x in excl}
        ids = {r["id"] for r in ranked}
        for i in sorted(((shown[side] - dropped) | {k for k in keep if k[0] == side[0]}) - ids):
            conflicts.append({"side": side, "id": i, "name": uni.get(i, {}).get("name", i),
                              "feedback": R.feedback_text(fb.get(i) or added.get(i)),
                              "why": why.get(i, "not in the current universe or not scored")})
    short = {"strategic": s_short, "financial": f_short}
    return {"project": m["project"], "as_of": m["as_of"], "mandate": m,
            "round": {k: rinfo[k] for k in ("no", "of", "final", "schedule", "quota")},
            "strategic": {"selected": s_sel, "reserve": s_res, "excluded": excluded_s},
            "financial": {"selected": f_sel, "reserve": f_res, "excluded": excluded_f},
            "rerouted": sorted(plats, key=lambda x: (x["rank"] or 9, -x["fit"])),
            "feedback_conflicts": conflicts,
            "shortfall": short, "must_include_overflow": overflow,
            "debate": {s: {k: v for k, v in read_json(run / "05_debate" / f"scores_{s}.json").items()
                           if k in ("points", "companies_retried", "weights")} for s in ("strategic", "financial")}}


def build(run: Path) -> dict:
    data = compute(run)
    write_json(list_path(run), data)
    short = data["shortfall"]
    if short["strategic"] or short["financial"]:
        write_json(shortfall_path(run), short)
    elif shortfall_path(run).exists():
        shortfall_path(run).unlink()
    return {"strategic": len(data["strategic"]["selected"]), "financial": len(data["financial"]["selected"]),
            "rerouted": len(data["rerouted"]), "shortfall": short}


def gate6(run: Path) -> tuple:
    iss = Issues()
    if not list_path(run).exists():
        iss.error("assemble", "list", "missing - run: python -m buyerlist assemble")
        return iss, []
    d = load_list(run)
    if d != json.loads(json.dumps(compute(run))):
        iss.error("assemble", "list", "06_list_data.json does not match the route, profiles and scores - "
                  "run: python -m buyerlist assemble")
        return iss, [list_path(run)]
    m = load_mandate(run)
    short = d["shortfall"]
    if short["strategic"] or short["financial"]:
        iss.error("assemble", "counts",
                  f"short by {short['strategic']} strategic and {short['financial']} financial ranked buyers. "
                  f"Run: python -m buyerlist universe topup --strategic {max(10, 2 * short['strategic'])} "
                  f"--financial {max(10, 2 * short['financial'])} --reason 'shortfall at assemble', "
                  "then repeat stages 2-6 for the new names only")
    if d.get("must_include_overflow"):
        iss.error("assemble", "financial", f"{d['must_include_overflow']} more ranked PE platforms than financial "
                  "slots - raise the financial target")
    seen = {}
    for side in ("strategic", "financial"):
        sel = d[side]["selected"]
        for r in sel:
            who = f"{r['id']} {r['name']}"
            if r["rank"] not in (1, 2, 3):
                iss.error("assemble", who, "selected without rank 1-3")
            if not is_url(r.get("website")):
                iss.error("assemble", who, "no valid website")
            if not text(r.get("description")):
                iss.error("assemble", who, "no description")
            from .universe import registrable_domain
            k = registrable_domain(r.get("website") or "") or r["id"]
            if k in seen:
                iss.error("assemble", who, f"duplicate of {seen[k]} (same website)")
            seen[k] = who
            if side == "strategic":
                if r["revenue_gate"] != "PASS":
                    iss.error("assemble", who, f"revenue gate {r['revenue_gate']} - strategic buyers need proven revenue "
                              f"> EUR {m['revenue_gate']['min_revenue']:g}m")
                if r["pe_owned"]:
                    iss.error("assemble", who, "PE-owned strategic on the strategic list - must be rerouted")
                if r["role"] in ("customer", "supplier"):
                    iss.error("assemble", who, "vertical buyer selected")
    sel_f = {r["id"] for r in d["financial"]["selected"]}
    final = d["round"]["final"]
    for pl in d["rerouted"]:
        names = dict(zip(pl["sponsors"], pl["sponsor_names"]))
        avail = [s for s in pl["listed_with"] if s not in pl.get("sponsors_dropped", [])]
        if final and avail and not any(s in sel_f for s in avail):
            iss.error("assemble", pl["name"], f"qualifying PE-owned platform (rank {pl['rank']}) but none of its "
                      f"qualifying sponsors ({', '.join(names[s] for s in pl['listed_with'])}) is on the financial list")
    # every platform shown on the financial list obeys the platform rule (re-derived from the row)
    rules, fw = platform_rules(m), weights_for("financial", m)
    for r in d["financial"]["selected"]:
        for x in r["platforms"]:
            ok, why = platform_check(x["fit"], x.get("product"), x["section"], own_fit(r["scores"], fw), rules)
            if not ok:
                iss.error("assemble", f"{r['id']} {r['name']}", f"{x['name']} is shown as PE platform but {why}")
    # investor holdings must carry a product-overlap class (profiles from before 2026-10-28 do not)
    uncl = []
    for fid in sorted(read_json(run / "05_debate" / "scores_financial.json")["companies"]):
        p = load_profile(run, fid) or {}
        if any(text(h.get("name")) and text(h.get("overlap")).lower() not in OVERLAPS
               for h in _dicts(p.get("relevant_portfolio"))):
            uncl.append(fid)
    if uncl:
        iss.error("assemble", "holdings", f"{len(uncl)} investor profile(s) have holdings without an overlap class "
                  f"(same products / same category / adjacent): {', '.join(uncl[:20])} - re-profile them: "
                  f"python -m buyerlist tasks make 3 --ids {','.join(uncl)} --per-agent 3 --note \"classify the "
                  "product overlap of every relevant_portfolio holding with the client\"")
    # analyst feedback of the closed rounds is applied (by id and by website domain)
    fb = R.effective(run)
    dropped_doms = {x["domain"]: x for x in fb.values() if x["status"] == "drop" and x.get("domain")}
    for side in ("strategic", "financial"):
        sel = {r["id"] for r in d[side]["selected"]}
        for i, x in fb.items():
            if x["side"] == side and x["status"] == "drop" and i in sel:
                iss.error("assemble", f"{i} {x['name']}", f"dropped by the analyst in round {x['round']} but listed")
        for r in d[side]["selected"]:
            x = dropped_doms.get(registrable_domain(r.get("website") or ""))
            if x and x["id"] != r["id"]:
                iss.error("assemble", f"{r['id']} {r['name']}", f"same website as {x['name']}, dropped by the analyst "
                          f"in round {x['round']}")
        for i in R.shown(run)[side]:
            if i in sel or fb.get(i, {}).get("status") == "drop":
                continue
            c = next((c for c in d["feedback_conflicts"] if c["id"] == i), None)
            if c is None:
                iss.error("assemble", i, "listed in an earlier round, not dropped by the analyst, but missing now")
    for c in d["feedback_conflicts"]:
        iss.warn("assemble", f"{c['id']} {c['name']}", f"the analyst's feedback ({c['feedback']}) cannot be applied - "
                 f"{c['why']}. Tell the analyst.")
    return iss, [list_path(run)]
