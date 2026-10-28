"""Mechanical normalisation of agent-written profiles, applied when a profile is
loaded (the file itself is never changed). It only maps common wording onto the
vocabulary the rules use - synonyms of owner types, fiscal-year strings like
"2024/25 (FYE 31 Mar 2025)", stakes written as "45,5 %" - and never invents a
value. Anything it cannot map is left as it is and reported by the validator.
"""
from __future__ import annotations

import copy
import re

OWNER_SYNONYMS = {
    "state": "state", "state-owned": "state", "state owned": "state", "government": "state", "public sector": "state",
    "federal state": "state", "republic": "state", "sovereign state": "state", "province": "state", "canton": "state",
    "municipality": "municipality", "municipal": "municipality", "city": "municipality", "city-owned": "municipality",
    "local authority": "municipality", "municipal utility": "municipality", "public utility": "municipality",
    "listed free float": "listed free float", "listed": "listed free float", "public": "listed free float",
    "free float": "listed free float", "freefloat": "listed free float", "public shareholders": "listed free float",
    "institutional investors": "listed free float", "shareholders": "listed free float",
    "pe": "PE", "private equity": "PE", "pe fund": "PE", "pe-backed": "PE", "buyout fund": "PE", "pe firm": "PE",
    "private equity fund": "PE", "financial sponsor": "PE",
    "infrastructure fund": "infrastructure fund", "infrastructure": "infrastructure fund",
    "infrastructure investor": "infrastructure fund", "infra fund": "infrastructure fund",
    "vc": "VC", "venture capital": "VC", "vc fund": "VC",
    "growth fund": "growth fund", "growth equity": "growth fund", "growth investor": "growth fund",
    "pension/sovereign": "pension/sovereign", "pension fund": "pension/sovereign", "pension": "pension/sovereign",
    "sovereign wealth fund": "pension/sovereign", "sovereign": "pension/sovereign", "swf": "pension/sovereign",
    "corporate": "corporate", "company": "corporate", "corporation": "corporate", "parent company": "corporate",
    "holding company": "corporate", "holding": "corporate", "strategic": "corporate", "group": "corporate",
    "family": "family", "family-owned": "family", "families": "family", "family holding": "family",
    "founder": "founder", "founders": "founder", "founder-owned": "founder",
    "individual": "individual", "private individual": "individual", "private person": "individual",
    "private investor": "individual", "natural person": "individual",
    "management": "management", "management team": "management", "managing director": "management",
    "employees": "employees", "employee": "employees", "employee-owned": "employees", "staff": "employees",
    "family office": "family office", "foundation": "foundation", "trust": "foundation", "stiftung": "foundation",
    "privatstiftung": "foundation", "private foundation": "foundation",
    "cooperative": "cooperative", "co-op": "cooperative", "genossenschaft": "cooperative", "other": "other",
}
OWNERSHIP_SYNONYMS = {
    "state": "state-owned", "state owned": "state-owned", "government": "state-owned", "state-owned": "state-owned",
    "municipality": "municipal", "municipally owned": "municipal", "municipal": "municipal", "city-owned": "municipal",
    "public": "listed", "listed company": "listed", "publicly listed": "listed", "listed": "listed",
    "pe": "PE-backed", "private equity": "PE-backed", "pe-owned": "PE-backed", "pe-backed": "PE-backed",
    "pe backed": "PE-backed", "sponsor-backed": "PE-backed",
    "employee owned": "employee-owned", "employees": "employee-owned", "employee-owned": "employee-owned",
    "corporate subsidiary": "corporate", "subsidiary": "corporate", "corporate": "corporate",
    "family-owned": "family", "family": "family", "founder-owned": "founder", "founder": "founder",
    "foundation": "foundation", "foundation-owned": "foundation", "cooperative": "cooperative",
    "unknown": "other", "undisclosed": "other", "other": "other", "partnership": "other",
}
STATUS = {"verified": "VERIFIED", "estimate": "ESTIMATE", "estimated": "ESTIMATE", "n/a": "n/a", "na": "n/a",
          "not available": "n/a", "n.a.": "n/a", "n/m": "n/m"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], 1)}
FIGURES = ("revenue", "ebitda", "employees")
OVERLAP_SYNONYMS = {"same products": "same products", "same product": "same products", "5": "same products",
                    "same products/services": "same products", "same services": "same products",
                    "identical products": "same products", "same category": "same category", "4": "same category",
                    "same product category": "same category", "same industry": "same category",
                    "adjacent": "adjacent", "related": "adjacent", "complementary": "adjacent", "3": "adjacent",
                    "2": "adjacent", "1": "adjacent", "different": "adjacent", "other": "adjacent"}


def _key(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower().replace("_", " "))


KEYWORDS = (("pension", "pension/sovereign"), ("sovereign", "pension/sovereign"), ("private equity", "PE"),
            ("buyout", "PE"), ("infrastructure", "infrastructure fund"), ("venture", "VC"),
            ("growth", "growth fund"), ("family office", "family office"), ("municipal", "municipality"),
            ("city of", "municipality"), ("stadt", "municipality"), ("government", "state"), ("republic", "state"),
            ("state", "state"), ("free float", "listed free float"), ("listed", "listed free float"),
            ("foundation", "foundation"), ("stiftung", "foundation"), ("trust", "foundation"),
            ("cooperative", "cooperative"), ("employee", "employees"), ("management", "management"),
            ("founder", "founder"), ("family", "family"), ("holding", "corporate"), ("corporate", "corporate"),
            ("company", "corporate"), ("group", "corporate"))


def owner_type(t):
    k = _key(t)
    if k in OWNER_SYNONYMS:
        return OWNER_SYNONYMS[k]
    base = re.sub(r"\s*\(.*?\)\s*", " ", k).strip()
    if base in OWNER_SYNONYMS:
        return OWNER_SYNONYMS[base]
    for word, typ in KEYWORDS:
        if word in k:
            return typ
    return t


def number(v):
    """'45%' -> 45.0, '45,5 %' -> 45.5, '>50' -> 50.1, 'majority' -> 50.1; None if no number."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if "majority" in s and not re.search(r"\d", s):
        return 50.1
    m = re.search(r"\d[\d.,']*", s.replace(" ", "").replace(" ", ""))
    if not m:
        return None
    t = m.group(0).rstrip(".,").replace("'", "")
    if "," in t and "." in t:                      # the last separator is the decimal one
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", t) else t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        x = float(t)
    except ValueError:
        return None
    if s.startswith(">") and x == 50:
        x = 50.1
    return x


def fiscal_year(fig: dict) -> None:
    fy = fig.get("fy")
    if fy is None or isinstance(fy, (int, float)):
        if isinstance(fy, float):
            fig["fy"] = str(int(fy))
        return
    s = str(fy).strip()
    if re.fullmatch(r"(?:FY\s*)?\d{4}(?:\s*/\s*\d{2,4})?", s, re.I):
        return
    low = s.lower()
    if low in ("undated", "n.d.", "nd", "unknown", "not stated", "n/a"):
        fig["fy"] = "not stated"
        fig.setdefault("fy_note", f"source gives no fiscal year ({s})")
        return
    m = re.search(r"(\d{4})\s*/\s*(\d{2,4})", s)
    if m:
        fig["fy"] = f"{m.group(1)}/{m.group(2)[-2:]}"
    else:
        m = re.search(r"(?:FY\s*)?(\d{4})", s, re.I)
        if not m:
            return
        fig["fy"] = m.group(1)
    mm = re.search(r"(?:fye|year end(?:ing)?|ending|ended|to|-)\s*(?:\d{1,2}\s+)?([a-z]{3})[a-z]*\.?\s+\d{4}", low)
    if mm and mm.group(1) in MONTHS and not fig.get("fy_end_month"):
        fig["fy_end_month"] = MONTHS[mm.group(1)]


def figure(fig):
    if not isinstance(fig, dict):
        return fig
    f = dict(fig)
    st = _key(f.get("status"))
    if st in STATUS:
        f["status"] = STATUS[st]
    if isinstance(f.get("value"), str):
        n = number(f["value"])
        if n is not None:
            f["value"] = n
    if isinstance(f.get("value"), list):
        f["value"] = [number(x) if isinstance(x, str) else x for x in f["value"]]
    fiscal_year(f)
    return f


def overlap(v):
    k = re.sub(r"[^a-z0-9/ ]", "", _key(v))
    if k in OVERLAP_SYNONYMS:
        return OVERLAP_SYNONYMS[k]
    for word, val in (("same product", "same products"), ("same service", "same products"),
                      ("same categor", "same category"), ("same industry", "same category"),
                      ("adjacent", "adjacent"), ("related", "adjacent"), ("complement", "adjacent")):
        if k.startswith(word):
            return val
    return v


def owner(o):
    if not isinstance(o, dict):
        return o
    o = dict(o)
    o["type"] = owner_type(o.get("type"))
    if not isinstance(o.get("stake_pct"), (int, float)) or isinstance(o.get("stake_pct"), bool):
        raw = o.get("stake_pct")
        n = number(raw)
        o["stake_pct"] = n
        if n is None and raw not in (None, ""):
            o.setdefault("stake_note", str(raw))
        if isinstance(raw, str) and "majority" in raw.lower() and o.get("controlling") is None:
            o["controlling"] = True
    u = o.get("ultimate_owner")
    if isinstance(u, dict):
        u = dict(u)
        u["type"] = owner_type(u.get("type"))
        o["ultimate_owner"] = u
    return o


def profile(p):
    if not isinstance(p, dict):
        return p
    q = copy.deepcopy(p)
    for k in FIGURES:
        if k in q:
            q[k] = figure(q[k])
    if isinstance(q.get("owners"), list):
        q["owners"] = [owner(o) for o in q["owners"]]
    ot = q.get("ownership_type")
    if isinstance(ot, str):
        q["ownership_type"] = OWNERSHIP_SYNONYMS.get(_key(ot), ot)
    lf = q.get("latest_fund")
    if isinstance(lf, dict):
        lf = dict(lf)
        size = lf.get("size")
        if isinstance(size, dict):
            lf["size"] = figure(size)
        elif number(size) is not None:                 # a plain number: the size as stated by the investor
            src = str(lf.get("source") or "").strip()
            lf["size"] = ({"status": "VERIFIED", "value": number(size), "source": src, "unit": "EUR m"} if src else
                          {"status": "ESTIMATE", "value": number(size), "method": "fund size as stated", "unit": "EUR m"})
            if lf.get("currency") and str(lf["currency"]).upper() != "EUR":
                lf["size"]["original"] = {"value": number(size), "currency": lf["currency"]}
                lf["size"]["fx"] = lf.get("fx") or "not converted (as stated)"
        if isinstance(lf.get("year"), str) and number(lf["year"]):
            lf["year"] = int(number(lf["year"]))
        q["latest_fund"] = lf
    if isinstance(q.get("aum"), dict):
        q["aum"] = figure(q["aum"])
    t = q.get("ticket")
    if isinstance(t, dict):
        t = dict(t)
        st = _key(t.get("status"))
        t["status"] = {"stated": "STATED", "derived": "DERIVED", "n/a": "n/a", "na": "n/a"}.get(st, t.get("status"))
        for k in ("equity", "ev", "ebitda", "revenue"):
            v = t.get(k)
            if isinstance(v, list) and len(v) == 2:
                t[k] = [number(x) if isinstance(x, str) else x for x in v]
            elif v in ([], "", "n/a", None):
                t.pop(k, None)
        q["ticket"] = t
    if isinstance(q.get("relevant_portfolio"), list):
        q["relevant_portfolio"] = [dict(h, overlap=overlap(h["overlap"])) if isinstance(h, dict) and "overlap" in h
                                   else h for h in q["relevant_portfolio"]]
    if isinstance(q.get("hq_country"), str):
        from .universe import country
        q["hq_country"] = country(q["hq_country"])
    return q
