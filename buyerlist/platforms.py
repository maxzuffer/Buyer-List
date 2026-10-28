"""PE-owned strategics as their sponsors' portfolio companies, and close holdings.

CLAUDE.md "PE reroute" and "Portfolio" (user decision 2026-10-28):
* A PE-owned strategic is shown under a sponsor only if platform_check passes for that
  sponsor: fit >= 3.0 and product >= 4; a sponsor whose own fit on ticket, geography and
  criteria is below 3.0 needs a Rank-1 platform with product 5. The check is per
  (platform, sponsor) pair.
* An investor's other holdings count (Portfolio score, "Relevant holdings") only when they
  offer the same products or the same product category as the client. A holding that is
  itself a rerouted strategic counts only through the platform rule above.
fiscore (stage 5e) and assemble (stage 6) both use this module, so they apply the same rule.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import text
from .profiles import OVERLAPS, _dicts
from .scoring import own_fit, platform_check, platform_rules, strategic_section, weights_for
from .universe import name_key, registrable_domain

CLOSE = OVERLAPS[:2]
GENERIC = {"group", "holding", "holdings", "company", "companies"}


def rerouted(run: Path, s_scores: dict) -> list:
    """Every rerouted (PE-owned) strategic that has an adjudicated score, in route order."""
    from .profiles import load_profile
    from .route import load_route
    from .universe import load_universe
    uni = {c["id"]: c for c in load_universe(run)["candidates"]}
    out = []
    for r in load_route(run)["strategic"]:
        sc = s_scores.get(r["id"])
        if r["route"] != "reroute" or sc is None:
            continue
        c = uni.get(r["id"], {})
        p = load_profile(run, r["id"]) or {}
        section, rank, _ = strategic_section(sc)
        out.append({"strategic_id": r["id"], "name": text(p.get("name") or c.get("name")),
                    "country": p.get("hq_country") or c.get("hq_country"),
                    "website": text(p.get("website") or c.get("website")),
                    "fit": sc["fit"], "rank": rank, "section": section, "confidence": sc["confidence"],
                    "product": sc["scores"].get("product"), "role": sc.get("role", ""),
                    "sponsors": [s["fi_id"] for s in r["sponsors"]],
                    "sponsor_names": [s["name"] for s in r["sponsors"]],
                    "bereich": text(p.get("bereich"))})
    return out


def qualify(platforms: list, f_scores: dict, mandate: dict) -> dict:
    """Sets listed_with / status / reason on every platform; returns {investor id: own fit}.
    f_scores: {investor id: {"scores": {ticket, geography, criteria, ...}}}."""
    rules = platform_rules(mandate)
    w = weights_for("financial", mandate)
    own = {fid: own_fit(sc["scores"], w) for fid, sc in f_scores.items()}
    for pl in platforms:
        names, checks = {}, {}
        for fid, nm in zip(pl["sponsors"], pl["sponsor_names"]):
            if fid not in checks:
                names[fid] = nm
                checks[fid] = platform_check(pl["fit"], pl["product"], pl["section"], own.get(fid), rules)
        pl["listed_with"] = [fid for fid, (ok, _) in checks.items() if ok]
        pl["status"] = "listed" if pl["listed_with"] else "not listed"
        pl["reason"] = "; ".join(f"{names[fid]}: {why}" for fid, (_, why) in checks.items())
    return own


# single words too common to identify a company on their own
WEAK_WORDS = {"cloud", "data", "net", "network", "networks", "telecom", "telekom", "telecommunications", "digital",
              "fiber", "fibre", "it", "tech", "technology", "services", "systems", "solutions", "europe", "european",
              "austria", "germany", "infra", "infrastructure", "connect", "communications", "media", "energy",
              "capital", "partners", "hosting", "datacenter", "datacentres", "datacenters", "centers", "centres"}


def _core(name) -> str:
    joined = re.sub(r"(?<=\w)-(?=\w)", "", text(name))               # 'M-net' -> 'Mnet'
    return " ".join(t for t in name_key(joined).split() if len(t) > 1 and t not in GENERIC)


def same_company(a: dict, b: dict, own: bool = False) -> bool:
    """Same company by website domain or name: equal cleaned names, the shorter name starting the longer
    one ('NTS' / 'NTS Netzwerk Telekom Service'), or ending it when it is distinctive ('Retelit' /
    'IRIDEOS Retelit'). own=True (the investor sponsors b): the same first word is enough."""
    da = registrable_domain(a.get("website") or "") if text(a.get("website")) else ""
    db = registrable_domain(b.get("website") or "") if text(b.get("website")) else ""
    if da and da == db:
        return True
    na, nb = _core(a.get("name")), _core(b.get("name"))
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = sorted((na, nb), key=len)
    if " " not in short and short in WEAK_WORDS:
        return False
    if long.startswith(short + " "):
        return True
    if long.endswith(" " + short) and len(short) >= 5:
        return True
    fa, fb = na.split()[0], nb.split()[0]
    return own and fa == fb and fa not in WEAK_WORDS


def overlap(h: dict) -> str:
    return text(h.get("overlap")).lower()


def close_holdings(p: dict, platforms: list, investor_id: str = "") -> list:
    """The investor's holdings with the same products or category as the client, without rerouted strategics
    (a platform the investor sponsors is matched more loosely: it is most likely among its holdings)."""
    return [h for h in _dicts(p.get("relevant_portfolio")) if text(h.get("name")) and overlap(h) in CLOSE
            and not any(same_company(h, pl, own=investor_id in pl.get("sponsors", [])) for pl in platforms)]
