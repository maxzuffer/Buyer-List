"""Stage 2 - buyer universe: sourcing lenses, raw candidate files, dedupe, ids.

Dedupe is by website domain (the same registrable domain = the same company).
Two candidates with the same normalised name but different domains are not
merged silently: the gate reports them until one is excluded (exclude.json) or
both are confirmed as different companies (distinct.json).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from . import state
from .common import Issues, StageError, is_url, read_json, read_text, sha256_file, text, write_json, write_text
from .tasks import load_index, write_tasks

TYPES = ("strategic", "financial")
# legal-form tokens only - generic words (capital, partners, group ...) stay part of the name
LEGAL = {"gmbh", "ag", "se", "sa", "nv", "bv", "plc", "ltd", "limited", "inc", "llc", "lp", "llp",
         "kg", "kgaa", "mbh", "spa", "sarl", "sas", "oy", "oyj", "ab", "publ", "asa", "srl", "kft",
         "sro", "zrt", "nyrt", "doo", "eg", "ug", "corp", "corporation", "the", "co", "aktiengesellschaft",
         "gesmbh", "kommanditgesellschaft", "incorporated", "as"}
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gv", "or", "ltd", "plc", "gov", "edu"}
COUNTRY_FIX = {"GB": "UK", "EN": "UK", "UK": "UK", "SP": "ES", "SWE": "SE", "NE": "NL", "LUX": "LU",
               "GER": "DE", "AUT": "AT", "SUI": "CH", "USA": "US"}
COUNTRY_NAMES = {"austria": "AT", "osterreich": "AT", "germany": "DE", "deutschland": "DE", "switzerland": "CH",
                 "united kingdom": "UK", "great britain": "UK", "england": "UK", "uk": "UK",
                 "united states": "US", "usa": "US", "us": "US", "france": "FR", "netherlands": "NL",
                 "the netherlands": "NL", "luxembourg": "LU", "belgium": "BE", "sweden": "SE", "norway": "NO",
                 "denmark": "DK", "finland": "FI", "italy": "IT", "spain": "ES", "portugal": "PT",
                 "poland": "PL", "czech republic": "CZ", "czechia": "CZ", "slovakia": "SK", "hungary": "HU",
                 "slovenia": "SI", "croatia": "HR", "romania": "RO", "bulgaria": "BG", "serbia": "RS",
                 "ireland": "IE", "japan": "JP", "canada": "CA", "australia": "AU", "israel": "IL",
                 "singapore": "SG", "hong kong": "HK", "china": "CN", "india": "IN", "mexico": "MX",
                 "united arab emirates": "AE", "uae": "AE", "qatar": "QA", "saudi arabia": "SA",
                 "greece": "GR", "turkey": "TR", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
                 "liechtenstein": "LI", "jersey": "JE", "guernsey": "GG", "bahrain": "BH", "cyprus": "CY",
                 "malta": "MT", "south korea": "KR", "korea": "KR", "brazil": "BR"}
MAX_SKIPPED_SHARE = 0.10


def udir(run: Path) -> Path:
    return run / "02_universe"


def raw_dir(run: Path) -> Path:
    return udir(run) / "raw"


def universe_path(run: Path) -> Path:
    return udir(run) / "universe.json"


def lenses_path(run: Path) -> Path:
    return udir(run) / "lenses.json"


def registrable_domain(url: str) -> str:
    u = text(url)
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").lower().strip(".")
    host = re.sub(r"^www\d?\.", "", host)
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", text(name)).encode("ascii", "ignore").decode().casefold()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("&", " ")
    tokens = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    core = [t for t in tokens if t not in LEGAL]
    return " ".join(core or tokens)


def country(c: str) -> str:
    raw = text(c)
    low = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().casefold().strip(" .")
    if low in COUNTRY_NAMES:
        return COUNTRY_NAMES[low]
    up = raw.upper().replace(".", "").strip()
    return COUNTRY_FIX.get(up, up)


def website(u: str) -> str:
    u = text(u)
    if u and "://" not in u and "." in u and " " not in u:
        u = "https://" + u
    return u


# ---------------------------------------------------------------------------
# Lenses
# ---------------------------------------------------------------------------
def check_lenses(run: Path, iss: Issues) -> list:
    p = lenses_path(run)
    if not p.exists():
        iss.error("universe", "lenses.json", "missing - write the sourcing lenses first")
        return []
    data = read_json(p)
    lenses = data.get("lenses") if isinstance(data, dict) else None
    if not isinstance(lenses, list):
        iss.error("universe", "lenses.json", "must be {\"lenses\": [...]}")
        return []
    ids = set()
    for i, l in enumerate(lenses):
        lid = text(l.get("id"))
        if not re.fullmatch(r"[A-Z][A-Za-z0-9-]{1,20}", lid):
            iss.error("universe", f"lens {i}", "id like S01 / F03 required")
        if lid in ids:
            iss.error("universe", f"lens {lid}", "duplicate id")
        ids.add(lid)
        if l.get("type") not in TYPES:
            iss.error("universe", f"lens {lid}", "type must be strategic or financial")
        if len(text(l.get("brief"))) < 40:
            iss.error("universe", f"lens {lid}", "brief too short - say what kind of buyer, where, and why")
        if not (isinstance(l.get("min"), int) and l["min"] > 0):
            iss.error("universe", f"lens {lid}", "min (candidates wanted) must be a positive integer")
        if l.get("round") is not None and not (isinstance(l["round"], int) and l["round"] >= 1):
            iss.error("universe", f"lens {lid}", "round must be the analyst round (1, 2, ...) the lens was written for")
    for t, need in (("strategic", 5), ("financial", 4)):
        n = sum(1 for l in lenses if l.get("type") == t)
        if n < need:
            iss.error("universe", "lenses.json", f"at least {need} {t} lenses required (has {n})")
    return lenses


def sourcing_tasks(run: Path, lens_ids: Optional[list] = None, round_no: int = 0) -> list:
    iss = Issues()
    lenses = check_lenses(run, iss)
    if not iss.ok():
        raise StageError(iss.render())
    from .mandate import client_sheet_path, load_mandate
    from . import rounds
    m = load_mandate(run)
    seen = udir(run) / "seen_names.txt"
    rno = rounds.current(run) if rounds.schedule(m) else 1
    fb = rounds.brief(run)
    if rno > 1 and not lens_ids:
        lens_ids = [l["id"] for l in lenses if l.get("round") == rno]
        if not lens_ids:
            raise StageError(f"round {rno}: write new lenses for this round first (\"round\": {rno} in lenses.json), "
                             "informed by the analyst feedback - see python -m buyerlist feedback list")
    tasks = []
    for l in lenses:
        if lens_ids and l["id"] not in lens_ids:
            continue
        out = raw_dir(run) / f"{l['id']}-r{round_no}.json"
        body = f"""Source candidate {'strategic buyers (operating companies)' if l['type'] == 'strategic' else 'financial investors'} for a sell-side mandate.

Client sheet (what the client does): {client_sheet_path(run)}
Lens {l['id']} - {l.get('title', '')}:
{l['brief']}

Find at least {l['min']} real, currently active {'companies' if l['type'] == 'strategic' else 'investors'} that fit this lens.
{('Already in the universe (do not repeat them): ' + str(seen)) if seen.exists() else ''}{fb}
Do not include the client itself ({m['client']['name']}, {m['client']['domain']}).

Save to: {out}
Format (JSON, read by a program):
{{"lens": "{l['id']}", "round": {round_no}, "candidates": [
  {{"name": "<official company / firm name>", "type": "{l['type']}",
   "website": "https://<main website>", "hq_country": "<ISO 2-letter, UK for United Kingdom>",
   "hq_city": "<city>", "segment": "<{'business segment, e.g. Data centres & colocation' if l['type'] == 'strategic' else 'investor type, e.g. Mid-market PE / Infrastructure fund / Family office'}>",
   "rationale": "<one sentence: why this is a plausible {'buyer' if l['type'] == 'strategic' else 'investor'} for the client>",
   "source_url": "<URL of a page that shows what it does>"}}
]}}
Rules: every candidate must exist (check its website); no duplicates; the group level, not
local subsidiaries, unless the subsidiary is the natural acquirer. {'Include PE-owned operating companies (they are handled later).' if l['type'] == 'strategic' else 'Include investors whose ticket might fit an EV of EUR ' + '-'.join(f'{v:g}' for v in m['ev']['value']) + 'm, plus some adjacent ones.'}
Web search is a shared, scarce resource: use at most 8 searches; prefer opening pages you know (company sites, lists of portfolio companies, industry association member lists) - at most 20 page reads. Reply only: "done - <n> candidates".
"""
        tasks.append({"id": f"{l['id']}-r{round_no}", "agent": "bl-sourcer", "body": body, "outputs": [out]})
    if rno > 1:
        write_json(feedback_used_path(run), {"round": rno, "digest": rounds.digest(run),
                                             "tasks": [t["id"] for t in tasks]})
    return write_tasks(run, "2", tasks)


def feedback_used_path(run: Path) -> Path:
    return udir(run) / "feedback_used.json"


def raw_ok(path: Path) -> bool:
    try:
        d = read_json(path)
        return isinstance(d, dict) and isinstance(d.get("candidates"), list) and bool(d["candidates"])
    except Exception:
        return False


def critic_task(run: Path, round_no: int, k: int = 0) -> list:
    from .mandate import client_sheet_path
    from .rounds import brief
    k = k or 1
    while (raw_dir(run) / f"CRIT{k}-r{round_no}.json").exists():
        k += 1                                    # never overwrite an earlier critic round
    out = raw_dir(run) / f"CRIT{k}-r{round_no}.json"
    body = f"""Completeness check of a buyer universe.

Client sheet: {client_sheet_path(run)}
Current universe (read it): {universe_path(run)}
Lenses used: {lenses_path(run)}

1. Read the universe and name the buyer groups that are missing or thin (by segment and by
   country: Austria, Germany, Switzerland, CEE, rest of Europe, global players with a Vienna
   presence; for investors: infrastructure funds, mid-market buyout funds with DACH/CEE focus,
   family offices/holdings, sector specialists).
2. Add up to 40 NEW candidates (not already in the universe; check the names) that close those
   gaps. Same JSON format as the sourcing lenses; use "lens": "CRIT{k}" and "round": {round_no}.
Save to: {out}
Format: {{"lens": "CRIT{k}", "round": {round_no}, "gaps": ["..."], "candidates": [ ... same fields as
the lens files: name, type, website, hq_country, hq_city, segment, rationale, source_url ... ]}}
Web search is scarce: at most 8 searches, up to 20 page reads. Reply only: "done - <n> candidates, gaps: <short list>".
{brief(run)}"""
    return write_tasks(run, "2c", [{"id": f"CRIT{k}-r{round_no}", "agent": "bl-universe-critic",
                                    "body": body, "outputs": [out]}])


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
def load_raw(run: Path, iss: Issues) -> list:
    rows = []
    files = sorted(raw_dir(run).glob("*.json")) if raw_dir(run).exists() else []
    if not files:
        iss.error("universe", "raw", "no raw candidate files - run the sourcing tasks")
    for f in files:
        try:
            data = read_json(f)
        except Exception as exc:
            iss.error("universe", f.name, f"unreadable JSON: {exc} - re-run that lens task")
            continue
        cands = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(cands, list):
            iss.error("universe", f.name, "no 'candidates' list - re-run that lens task")
            continue
        lens = text(data.get("lens")) or f.stem
        rnd = data.get("round", 0) if isinstance(data.get("round", 0), int) else 0
        for i, c in enumerate(cands):
            if not isinstance(c, dict):
                iss.warn("universe", f"{f.name}[{i}]", "not an object - skipped")
                rows.append({"_skip": True, "_file": f.name})
                continue
            c = dict(c)
            c["website"] = website(c.get("website"))
            c["source_url"] = website(c.get("source_url"))
            c["_lens"], c["_file"], c["_round"] = lens, f.name, rnd
            rows.append(c)
    return rows


def valid_candidate(c: dict, iss: Issues) -> bool:
    if c.get("_skip"):
        return False
    who = f"{c.get('name') or '?'} ({c['_file']})"
    ok = True
    if not text(c.get("name")):
        iss.warn("universe", who, "no name - skipped"); ok = False
    if c.get("type") not in TYPES:
        iss.warn("universe", who, f"type {c.get('type')!r} - skipped"); ok = False
    if not is_url(c.get("website")):
        iss.warn("universe", who, "no valid website - skipped"); ok = False
    if not re.fullmatch(r"[A-Z]{2}", country(c.get("hq_country"))):
        iss.warn("universe", who, f"hq_country {c.get('hq_country')!r} not a 2-letter code - skipped"); ok = False
    if not is_url(c.get("source_url")):
        iss.warn("universe", who, "no source_url - skipped"); ok = False
    return ok


def _pairs(path: Path, key: str) -> list:
    if not path.exists():
        return []
    try:
        return read_json(path).get(key, [])
    except Exception:
        return []


def build(run: Path) -> dict:
    """The universe computed from the raw files (pure: writes nothing)."""
    from .mandate import load_mandate
    m = load_mandate(run)
    iss = Issues()
    rows = load_raw(run, iss)
    ids_path = udir(run) / "ids.json"
    ids = read_json(ids_path) if ids_path.exists() else {"next": {"strategic": 1, "financial": 1}, "keys": {}}
    ids = {"next": dict(ids["next"]), "keys": dict(ids["keys"])}
    excl_domains = {d.lower() for d in m.get("exclude_domains", [])}
    excl_names = {name_key(m["client"]["name"])}
    for e in _pairs(udir(run) / "exclude.json", "exclude"):
        if e.get("name"):
            excl_names.add(name_key(e["name"]))
        if e.get("domain"):
            excl_domains.add(e["domain"].lower())
    distinct = {frozenset(p) for p in _pairs(udir(run) / "distinct.json", "pairs") if len(p) == 2}
    clusters: dict = {}
    excluded, skipped, total = [], 0, 0
    for c in rows:
        total += 1
        if not valid_candidate(c, iss):
            skipped += 1
            continue
        dom = registrable_domain(c["website"])
        if dom in excl_domains or name_key(c["name"]) in excl_names:
            excluded.append({"name": c["name"], "domain": dom, "reason": "the client itself or on the exclude list"})
            continue
        clusters.setdefault(dom, []).append(c)
    from .rounds import pinned_ids
    pinned = pinned_ids(run)
    candidates, duplicates, used = [], [], set()
    for dom in sorted(clusters, key=lambda d: min(r["_file"] + r["name"] for r in clusters[d])):
        rs = clusters[dom]
        votes = Counter(r["type"] for r in rs)
        ctype = "strategic" if votes["strategic"] >= votes["financial"] else "financial"
        analyst = [r for r in rs if r["_lens"] == "AN"]
        prev = ids["keys"].get(f"d:{dom}")
        if prev in pinned:                 # shown to / judged by the analyst: id and type stay
            ctype = "strategic" if prev.startswith("s-") else "financial"
        elif analyst:                      # the analyst said which side the name belongs to
            ctype = analyst[0]["type"]
        if len(votes) > 1:
            iss.warn("universe", rs[0]["name"], f"sourced as {dict(votes)} - kept as {ctype}")
        first = next((r for r in rs if r["type"] == ctype), rs[0])
        cid = ids["keys"].get(f"d:{dom}")
        if cid is None or not cid.startswith(ctype[0]) or cid in used:
            cid = f"{ctype[0]}-{ids['next'][ctype]:03d}"
            ids["next"][ctype] += 1
            ids["keys"][f"d:{dom}"] = cid
        used.add(cid)
        candidates.append({
            "id": cid, "type": ctype, "name": text(first["name"]), "website": text(first["website"]),
            "domain": dom, "hq_country": country(first["hq_country"]), "hq_city": text(first.get("hq_city")),
            "segment": text(first.get("segment")), "rationale": text(first.get("rationale")),
            "source_urls": sorted({text(r.get("source_url")) for r in rs if is_url(r.get("source_url"))}),
            "lenses": sorted({r["_lens"] for r in rs}), "round": min(r["_round"] for r in rs)})
        for r in rs[1:]:
            duplicates.append({"name": r["name"], "file": r["_file"], "merged_into": cid})
    # same name, different websites: probably the same company sourced twice
    by_name: dict = {}
    for c in candidates:
        by_name.setdefault((c["type"], name_key(c["name"])), []).append(c)
    for (t, k), cs in by_name.items():
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                if frozenset((cs[i]["domain"], cs[j]["domain"])) not in distinct:
                    iss.error("universe", f"{cs[i]['id']} / {cs[j]['id']}",
                              f"same name '{k}' with different websites ({cs[i]['domain']}, {cs[j]['domain']}) - "
                              "add the wrong one to 02_universe/exclude.json, or both domains as a pair to "
                              "02_universe/distinct.json if they really are different companies")
    if total and skipped / total > MAX_SKIPPED_SHARE:
        iss.error("universe", "raw", f"{skipped} of {total} raw candidates are unusable (> {MAX_SKIPPED_SHARE:.0%}) - "
                  "fix or re-run the lens tasks")
    raw_hashes = {f.name: sha256_file(f) for f in sorted(raw_dir(run).glob("*.json"))}
    for x in ("exclude.json", "distinct.json"):
        if (udir(run) / x).exists():
            raw_hashes[x] = sha256_file(udir(run) / x)
    uni = {"candidates": sorted(candidates, key=lambda c: c["id"]), "duplicates": duplicates,
           "excluded": excluded, "skipped": skipped, "raw_hashes": raw_hashes,
           "counts": {t: sum(1 for c in candidates if c["type"] == t) for t in TYPES}}
    return {"universe": uni, "ids": ids, "issues": iss}


def merge(run: Path) -> dict:
    res = build(run)
    uni = res["universe"]
    write_json(udir(run) / "ids.json", res["ids"])
    write_json(universe_path(run), uni)
    write_text(udir(run) / "seen_names.txt",
               "\n".join(f"{c['type']}: {c['name']} ({c['domain']})" for c in uni["candidates"]) + "\n")
    out = dict(uni)
    out["_issues"] = res["issues"]
    return out


def base_counts(run: Path) -> dict:
    """Unique candidates needed before top-ups: target x oversample, or the current round's need."""
    from .rounds import required_universe
    return required_universe(run)


def required_counts(run: Path) -> dict:
    extra = state.load(run).get("topup_extra") or {}
    return {t: n + int(extra.get(t, 0)) for t, n in base_counts(run).items()}


def load_universe(run: Path) -> dict:
    return read_json(universe_path(run))


def gate2(run: Path) -> tuple:
    iss = Issues()
    lenses = check_lenses(run, iss)
    if not universe_path(run).exists():
        iss.error("universe", "universe.json", "missing - run: python -m buyerlist universe merge")
        return iss, []
    uni = load_universe(run)
    res = build(run)
    iss.extend(Issues([i for i in res["issues"].items if i.severity == "ERROR"]))
    if uni != res["universe"]:
        iss.error("universe", "universe.json", "does not match the raw files - run: python -m buyerlist universe merge")
    # every lens and every critic round must have produced a readable file with candidates
    files = {f.name: f for f in raw_dir(run).glob("*.json")}
    for l in lenses:
        mine = [f for n, f in files.items() if n.startswith(l["id"] + "-")]
        good = False
        for f in mine:
            try:
                good = good or bool(read_json(f).get("candidates"))
            except Exception:
                pass
        if not good:
            iss.error("universe", f"lens {l['id']}", "no readable raw file with candidates - run its sourcing task")
    for step in ("2", "2c"):
        for t in load_index(run, step):
            for o in t["outputs"]:
                try:
                    read_json(o)
                except Exception:
                    iss.error("universe", t["id"], f"task output {Path(o).name} missing or unreadable - re-run it")
    iss.extend(round_checks(run, lenses))
    need = required_counts(run)
    for t in TYPES:
        have = sum(1 for c in uni["candidates"] if c["type"] == t)
        if have < need[t]:
            iss.error("universe", t, f"{have} unique {t} candidates, {need[t]} required "
                      "(target x oversample + top-up) - run more lenses or the completeness critic")
    from .mandate import load_mandate
    client_dom = load_mandate(run)["client"]["domain"]
    for c in uni["candidates"]:
        if c["domain"] == client_dom:
            iss.error("universe", c["name"], "this is the client itself")
    outs = [universe_path(run), lenses_path(run), udir(run) / "ids.json"] + sorted(raw_dir(run).glob("*.json"))
    outs += [udir(run) / x for x in ("exclude.json", "distinct.json", "feedback_used.json") if (udir(run) / x).exists()]
    return iss, outs


def round_checks(run: Path, lenses: list) -> Issues:
    """From round 2 on: new lenses for the round, and sourcing tasks that carry the current analyst feedback."""
    from . import rounds
    from .mandate import load_mandate
    iss = Issues()
    rno = rounds.current(run) if rounds.schedule(load_mandate(run)) else 1
    if rno < 2:
        return iss
    for t, need in (("strategic", 2), ("financial", 2)):
        n = sum(1 for l in lenses if l.get("type") == t and l.get("round") == rno)
        if n < need:
            iss.error("universe", "lenses.json", f"round {rno}: at least {need} new {t} lenses (\"round\": {rno}) "
                      "written from the analyst feedback are required")
    p = feedback_used_path(run)
    used = read_json(p) if p.exists() else {}
    if used.get("round") != rno or used.get("digest") != rounds.digest(run):
        iss.error("universe", "sourcing", f"round {rno}: the sourcing tasks do not carry the current analyst feedback "
                  "- run: python -m buyerlist tasks make 2 (then the sourcing workflow and universe merge)")
    doms = set()
    for f in raw_dir(run).glob("AN-*.json"):
        try:
            doms |= {registrable_domain(c.get("website", "")) for c in read_json(f).get("candidates") or []}
        except Exception:
            pass
    for x in rounds.added_names(run):
        if registrable_domain(x["website"]) not in doms:
            iss.error("universe", x["name"], f"added by the analyst in round {x['round']} but missing from the "
                      "raw files (AN-r*.json, written by: python -m buyerlist round next)")
    return iss


def topup(run: Path, strategic: int, financial: int, reason: str) -> dict:
    st = state.load(run)
    if state.status(st, "6") == "pass":
        raise StageError("stage 6 passed - no top-up needed")
    if state.status(st, "5e") != "pass" and state.status(st, "6") == "pending":
        raise StageError("top-up is for a shortfall found at stage 6 (assemble) - finish the debate first")
    have = load_universe(run)["counts"]
    base = base_counts(run)
    add = {"strategic": strategic, "financial": financial}
    # new requirement: everything we have now plus the requested additional names
    st["topup_extra"] = {t: max(0, have[t] + add[t] - base[t]) for t in TYPES}
    st["topup_round"] = int(st.get("topup_round", 0)) + 1
    state.save(run, st)
    state.reopen(run, "2", f"top-up round {st['topup_round']}: +{strategic} strategic, +{financial} financial ({reason})")
    return {"round": st["topup_round"], "required": required_counts(run)}
