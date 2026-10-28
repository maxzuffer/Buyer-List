"""Weights, rounding and rank thresholds for both debates."""
from __future__ import annotations

from typing import Optional

from .figures import round1

STRATEGIC_WEIGHTS = {"product": 0.50, "customer": 0.20, "geography": 0.20,
                     "value_chain": 0.05, "synergy": 0.05}
FINANCIAL_WEIGHTS = {"ticket": 0.35, "geography": 0.25, "criteria": 0.30, "portfolio": 0.10}
WEIGHTS = {"strategic": STRATEGIC_WEIGHTS, "financial": FINANCIAL_WEIGHTS}

# Rank: minimum adjudicated fit (CLAUDE.md, same floors as the BD-memo tool)
RANK_FLOORS = ((1, 3.8), (2, 3.0), (3, 2.0))
# Default dimension score when the briefs contain no evidence
DEFAULT_NO_EVIDENCE = 2
# PE platforms (CLAUDE.md "PE reroute", user decision 2026-10-28): a PE-owned strategic is shown as its
# sponsor's portfolio company only with fit >= min_fit and product >= min_product; a sponsor whose own
# fit (ticket, geography, criteria - portfolio left out) is below sponsor_floor needs a Rank-1 platform
# with the same products (strict_fit, strict_product).
PLATFORM_RULE = {"min_fit": 3.0, "min_product": 4, "sponsor_floor": 3.0, "strict_fit": 3.8, "strict_product": 5}
OWN_FIT_DIMS = ("ticket", "geography", "criteria")


def weights_for(side: str, mandate: Optional[dict] = None) -> dict:
    w = dict(WEIGHTS[side])
    custom = ((mandate or {}).get("weights") or {}).get(side)
    if isinstance(custom, dict) and custom:
        w = {k: float(v) for k, v in custom.items()}
    return w


def weighted(scores: dict, weights: dict) -> float:
    return round1(sum(weights[d] * scores[d] for d in weights))


def calc_text(scores: dict, weights: dict) -> str:
    parts = " + ".join(f"{weights[d]:.2f} x {scores[d]}" for d in weights)
    return f"{parts} = {sum(weights[d] * scores[d] for d in weights):.3f} -> {weighted(scores, weights):.1f}"


def expected_rank(fit: Optional[float]) -> Optional[int]:
    if fit is None:
        return None
    for rank, floor in RANK_FLOORS:
        if round1(fit) >= floor:
            return rank
    return None


def different_product_and_region(scores: dict) -> bool:
    """Partner rule (strategic only): product <= 2 AND geography <= 2 -> no rank."""
    p, g = scores.get("product"), scores.get("geography")
    return isinstance(p, int) and isinstance(g, int) and p <= 2 and g <= 2


def strategic_section(sc: dict) -> tuple:
    """(section, rank, reason) of an adjudicated strategic: ranked | leermeldung | vertical."""
    fit = sc["fit"]
    rank = expected_rank(fit)
    if different_product_and_region(sc["scores"]):
        return "leermeldung", None, "different product and region (product <= 2, geography <= 2)"
    if rank is None:
        return "leermeldung", None, f"adjudicated fit {fit:.1f} below 2.0"
    if sc.get("role") in ("customer", "supplier"):
        return "vertical", None, f"vertical integration - the company is the client's {sc['role']}"
    return "ranked", rank, ""


def best_rank(*ranks: Optional[int]) -> Optional[int]:
    valid = [r for r in ranks if isinstance(r, int)]
    return min(valid) if valid else None


def platform_rules(mandate: Optional[dict] = None) -> dict:
    r = dict(PLATFORM_RULE)
    custom = (mandate or {}).get("platform_rule")
    if isinstance(custom, dict):
        r.update({k: float(v) for k, v in custom.items() if k in PLATFORM_RULE})
    return r


def platform_rule_text(r: dict) -> str:
    return (f"a PE-owned strategic is shown as its sponsor's portfolio company only with strategic fit >= "
            f"{r['min_fit']:.1f} and product >= {r['min_product']:g}; if the sponsor's own fit on ticket, geography "
            f"and criteria is below {r['sponsor_floor']:.1f}, the platform needs Rank 1 (fit >= {r['strict_fit']:.1f}) "
            f"and product {r['strict_product']:g} (the same products); otherwise it is not shown and the sponsor "
            f"competes on its own fit")


def own_fit(scores: dict, weights: dict) -> Optional[float]:
    """An investor's fit on ticket, geography and criteria only (portfolio left out), 1-5 scale."""
    if not all(d in weights and isinstance(scores.get(d), (int, float)) for d in OWN_FIT_DIMS):
        return None
    tot = sum(weights[d] for d in OWN_FIT_DIMS)
    return round1(sum(weights[d] * scores[d] for d in OWN_FIT_DIMS) / tot) if tot > 0 else None


def platform_check(fit: float, product, section: str, sponsor_fit: Optional[float], rules: dict) -> tuple:
    """(listed?, reason) for one PE-owned strategic under one of its sponsors."""
    if section != "ranked":
        return False, f"not listed: strategic section {section} (no strategic rank)"
    weak = sponsor_fit is None or round1(sponsor_fit) < rules["sponsor_floor"]
    need_fit, need_prod = ((rules["strict_fit"], rules["strict_product"]) if weak
                           else (rules["min_fit"], rules["min_product"]))
    why = ""
    if weak:
        own = "no own investor score" if sponsor_fit is None else \
            f"own fit {sponsor_fit:.1f} < {rules['sponsor_floor']:.1f} (ticket, geography, criteria)"
        why = f"sponsor {own} - needs Rank 1 and the same products"
    short = []
    if round1(fit) < need_fit:
        short.append(f"fit {fit:.1f} < {need_fit:.1f}")
    if not isinstance(product, (int, float)) or product < need_prod:
        short.append(f"product {product} < {need_prod:g}")
    if short:
        return False, "not listed: " + (f"{why}: " if why else "") + ", ".join(short)
    return True, f"listed: fit {fit:.1f}, product {product}" + (f" ({why})" if why else "")
