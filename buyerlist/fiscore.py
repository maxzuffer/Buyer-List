"""Profile-based scoring of financial investors (run option financial_scoring = profile, used
when the financial debate is not run - user decision 2026-10-20).

Deterministic and transparent: the same four dimensions and weights as the financial
debate (ticket 35 / geography 25 / criteria 30 / portfolio 10), scored from the sourced
investor profile. Every reason names the profile fact it uses; every score's sources are
the profile's source URLs.

Ticket: the deal (EV EUR lo-hi, equity cheque lo-hi, client EBITDA and revenue) against
  the investor's stated ranges - 5 inside, 4 overlapping, 3 within 1.5x of the range,
  2 within 3x, 1 further away; a range derived from fund size (10-15% per deal) counts
  at most 4; no ticket information: 2.
Geography: office or stated focus in Austria/Vienna 5; DACH / German-speaking / Germany /
  Switzerland / CEE 4; Europe-wide 3; global with no European signal 2; outside Europe 1.
Criteria: sector focus on the client's field (digital infrastructure, data centres,
  telecom, fibre, cloud, hosting, IT/managed services, TMT) with majority deals 5, without
  majority/buyout signal 4; generalist technology / services / infrastructure with majority
  deals 3, otherwise 2; no fitting sector 2 (majority) or 1; venture / seed investors max 2.
Portfolio: holds a PE-owned strategic that qualifies as its platform (platforms.py) 5; 2+
  holdings with the same products or category as the client 4; one 3; none 2.
"""
from __future__ import annotations

from pathlib import Path

from .common import is_num, text
from .mandate import load_mandate
from .profiles import load_profile
from .platforms import close_holdings, qualify, rerouted
from .route import added_candidates
from .scoring import calc_text, weighted, weights_for
from .universe import load_universe

STRONG = ("digital infrastructure", "data cent", "datacent", "data-cent", "telecom", "telco", "fibre", "fiber",
          "connectivity", "tmt", "cloud", "hosting", "it services", "it-services", "managed services",
          "communications infrastructure", "digital infra", "towers", "networks")
MEDIUM = ("technology", "software", "tech", "digital", "business services", "infrastructure", "services",
          "b2b")
MAJORITY = ("majority", "buyout", "buy-out", "control", "lbo", "mbo", "succession", "buy-and-build",
            "buy and build", "platform")
VC = ("seed", "series a", "series b", "venture", "early stage", "early-stage", "start-up", "startup")
AUSTRIA = ("austria", "vienna", "wien", "österreich", "oesterreich")
DACH = ("dach", "german-speaking", "germany", "german", "switzerland", "swiss", "cee", "central europe",
        "central and eastern", "eastern europe", "munich", "frankfurt", "zurich", "zürich", "berlin",
        "hamburg", "düsseldorf", "dusseldorf", "prague", "warsaw", "budapest")
EUROPE = ("europe", "european", "emea", "benelux", "nordic", "uk", "france", "italy", "spain", "netherlands")
OUTSIDE = ("north america", "united states", "asia", "australia", "latin america", "africa", "middle east")


def _interval(v):
    if not (isinstance(v, list) and len(v) == 2):
        return None
    lo = v[0] if is_num(v[0]) else None
    hi = v[1] if is_num(v[1]) else None
    if lo is None and hi is None:
        return None
    return (lo if lo is not None else 0.0, hi if hi is not None else float("inf"))


def _fit(deal, rng) -> int:
    dlo, dhi = deal
    rlo, rhi = rng
    if rlo <= dlo and dhi <= rhi:
        return 5
    if dlo <= rhi and rlo <= dhi:
        return 4
    ratio = (dlo / rhi) if dlo > rhi else (rlo / dhi if dhi > 0 else 99)
    return 3 if ratio <= 1.5 else 2 if ratio <= 3 else 1


def ticket(p: dict, m: dict) -> tuple:
    t = p.get("ticket") if isinstance(p.get("ticket"), dict) else {}
    st = text(t.get("status")).upper()
    deals = {"equity": tuple(m["equity_ticket"]), "ev": tuple(m["ev"]["value"]),
             "ebitda": (m["ebitda"]["value"], m["ebitda"]["value"]),
             "revenue": (m["revenue"]["value"], m["revenue"]["value"])}
    best, why = None, ""
    for k in ("equity", "ev", "ebitda", "revenue"):
        rng = _interval(t.get(k))
        if rng:
            s = _fit(deals[k], rng)
            hi = "+" if rng[1] == float("inf") else f"-{rng[1]:g}"
            if best is None or s > best:
                best, why = s, (f"stated {k} range EUR {rng[0]:g}{hi}m vs deal {k} EUR "
                                f"{deals[k][0]:g}-{deals[k][1]:g}m")
    src = [t.get("source")] if text(t.get("source")) else []
    if best is not None:
        if st == "DERIVED":
            best = min(best, 4)
            why += f" (derived: {text(t.get('method'))})"
        return best, why, src, st
    lf = p.get("latest_fund") if isinstance(p.get("latest_fund"), dict) else {}
    size = lf.get("size") if isinstance(lf.get("size"), dict) else {}
    v = size.get("value")
    if is_num(v) and v > 0:
        rng = (0.10 * v, 0.15 * v)
        s = min(4, _fit(deals["equity"], rng))
        return s, (f"no stated ticket; 10-15% of latest fund EUR {v:g}m = equity EUR {rng[0]:.0f}-{rng[1]:.0f}m "
                   f"vs EUR {deals['equity'][0]:g}-{deals['equity'][1]:g}m"), [lf.get("source")] if lf.get("source") else [], "DERIVED"
    return 2, "no ticket or fund-size information - default 2", [], "N/A"


def geography(p: dict) -> tuple:
    g = p.get("geo_focus") if isinstance(p.get("geo_focus"), dict) else {}
    offices = " ".join(str(x) for x in (p.get("offices") or []) if isinstance(x, str)).lower()
    focus = f"{text(g.get('summary'))} {text(g.get('quote'))}".lower()
    hq = text(p.get("hq_country")).upper()
    src = [g.get("source")] if text(g.get("source")) else []
    if any(w in offices for w in AUSTRIA) or hq == "AT":
        return 5, f"office / HQ in Austria ({offices or hq})", src
    if any(w in focus for w in AUSTRIA):
        return 5, f"stated focus includes Austria: {text(g.get('summary'))[:90]}", src
    if any(w in focus for w in DACH) or any(w in offices for w in DACH) or hq in ("DE", "CH"):
        return 4, f"DACH / CEE presence or focus: {text(g.get('summary'))[:90]}", src
    if any(w in focus for w in EUROPE) or any(w in offices for w in EUROPE) or hq in (
            "UK", "FR", "NL", "LU", "BE", "SE", "NO", "DK", "FI", "IT", "ES", "IE", "PL", "CZ"):
        return 3, f"European focus or offices: {text(g.get('summary'))[:90]}", src
    if any(w in focus for w in OUTSIDE) and "global" not in focus:
        return 1, f"focus outside Europe: {text(g.get('summary'))[:90]}", src
    return 2, f"global / unspecified focus without a European signal: {text(g.get('summary'))[:90]}", src


def criteria(p: dict) -> tuple:
    s = p.get("sector_focus") if isinstance(p.get("sector_focus"), dict) else {}
    sector = f"{text(s.get('summary'))} {text(s.get('quote'))}".lower()
    deals = " ".join(str(x) for x in (p.get("deal_types") or []) if isinstance(x, str)).lower()
    itype = text(p.get("investor_type")).lower()
    src = [s.get("source")] if text(s.get("source")) else []
    majority = any(w in deals for w in MAJORITY) or any(w in sector for w in MAJORITY)
    vc = itype in ("vc", "corporate vc") or (any(w in deals for w in VC) and not majority)
    strong = [w for w in STRONG if w in sector]
    medium = [w for w in MEDIUM if w in sector]
    if strong:
        sc = 5 if majority else 4
        why = f"sector focus on {', '.join(strong[:3])}" + ("; majority / buyout deals" if majority else "")
    elif medium:
        sc = 3 if majority else 2
        why = f"generalist focus ({', '.join(medium[:3])})" + ("; majority / buyout deals" if majority else "")
    else:
        sc = 2 if majority else 1
        why = f"sector focus elsewhere: {text(s.get('summary'))[:80]}"
    if vc:
        sc = min(sc, 2)
        why += "; venture-stage investor"
    return sc, why, src


def portfolio(p: dict, listed: list, platforms: list, investor_id: str = "") -> tuple:
    """listed: names of the PE-owned strategics that qualify as this investor's platform;
    platforms: every rerouted strategic (they never count as ordinary holdings)."""
    if listed:
        return 5, f"holds PE-owned strategic(s) that qualify as platform: {', '.join(listed[:3])}", []
    rel = close_holdings(p, platforms, investor_id)
    src = [h.get("source") for h in rel if text(h.get("source"))][:3]
    names = ", ".join(text(h.get("name")) for h in rel[:3])
    if len(rel) >= 2:
        return 4, f"holdings with the same products or category as the client: {names}", src
    if len(rel) == 1:
        return 3, f"one holding with the same products or category as the client: {names}", src
    return 2, "no holding with the same products or category recorded - default 2", []


def compute(run: Path) -> dict:
    m = load_mandate(run)
    w = weights_for("financial", m)
    uni = {c["id"]: c for c in load_universe(run)["candidates"] if c["type"] == "financial"}
    for c in added_candidates(run):
        uni[c["id"]] = c
    from .debate import side_scores, sides
    plats = rerouted(run, side_scores(run, "strategic")["companies"]) if "strategic" in sides(run) else []
    parts = {}
    for fid in sorted(uni):
        p = load_profile(run, fid) or {}
        parts[fid] = (p, ticket(p, m), geography(p), criteria(p))
    qualify(plats, {fid: {"scores": {"ticket": t[0], "geography": g[0], "criteria": c[0]}}
                    for fid, (p, t, g, c) in parts.items()}, m)
    listed = {}
    for pl in plats:
        for fid in pl["listed_with"]:
            listed.setdefault(fid, []).append(pl["name"])
    out = {}
    labels = {"ticket": "Ticket", "geography": "Geography", "criteria": "Criteria", "portfolio": "Portfolio"}
    for fid in sorted(uni):
        p, (t_s, t_why, t_src, t_st), (g_s, g_why, g_src), (c_s, c_why, c_src) = parts[fid]
        p_s, p_why, p_src = portfolio(p, listed.get(fid, []), plats, fid)
        scores = {"ticket": t_s, "geography": g_s, "criteria": c_s, "portfolio": p_s}
        reasons = {"ticket": t_why, "geography": g_why, "criteria": c_why, "portfolio": p_why}
        srcs = {"ticket": t_src, "geography": g_src, "criteria": c_src, "portfolio": p_src}
        fit = weighted(scores, w)
        conf = ("High" if t_st == "STATED" and g_src and c_src else
                "Medium" if t_st in ("STATED", "DERIVED") else "Low")
        best = max(("ticket", "geography", "criteria"), key=lambda d: (scores[d], w[d]))
        worst = min(("ticket", "geography", "criteria"), key=lambda d: (scores[d], -w[d]))
        out[fid] = {
            "id": fid, "name": text(p.get("name") or uni[fid].get("name")), "batch": 0, "scores": scores,
            "reasons": reasons, "fit": fit, "stated_fit": fit, "calc": calc_text(scores, w), "fit_mismatch": False,
            "confidence": conf, "confidence_mech": conf, "confidence_judge": "",
            "confidence_reason": f"profile-based score; ticket {t_st.lower()}", "survived_points": 0,
            "deciding": f"{labels[best]} {scores[best]}/5: {reasons[best]}.",
            "against": (f"{labels[worst]} {scores[worst]}/5: {reasons[worst]}." if scores[worst] <= 3 else "none."),
            "deciding_sources": [{"url": u, "quote": ""} for u in srcs[best] if u],
            "against_sources": [{"url": u, "quote": ""} for u in srcs[worst] if u],
            "dim_sources": {d: [{"url": u, "quote": ""} for u in srcs[d] if u] for d in scores},
            "role": "", "judge_problems": [], "retried": False, "method": "profile"}
    return {"side": "financial", "weights": w, "companies": out, "points": {"kept": {}, "dropped": {}},
            "companies_retried": 0, "method": "profile-based scoring (financial debate not run - user decision)"}
