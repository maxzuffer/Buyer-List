"""Source checks for the fit debates.

Ported from Business-Dev-2.0 .claude/tools/check_sources.py (merge / check /
clean / retry / judge) and generalised to two sides:

  strategic: researchers argue Product/service, Customer, Geography;
             the judge scores Product, Customer, Geography, Value chain, Synergy.
  financial: researchers argue Ticket size, Geography, Investment criteria;
             the judge scores Ticket, Geography, Criteria, Portfolio.

Company headings carry the candidate id: "## <Name> #s-012" (or #f-007), so
names never have to match letter by letter. Markdown decoration (bold labels,
### headings) is tolerated; anything that still cannot be read is reported,
never silently dropped. Downloaded pages are only searched as text; nothing in
them is executed or followed.
"""
from __future__ import annotations

import html
import os
import random
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .common import text
from .scoring import DEFAULT_NO_EVIDENCE

SIDES = {
    "strategic": {
        "dims": {"product/service": "Product/service", "product": "Product/service",
                 "products/services": "Product/service", "product / service": "Product/service",
                 "customer": "Customer", "customers": "Customer", "geography": "Geography"},
        "required": ("Product/service", "Customer", "Geography"),
        "judge": (("Product", "product"), ("Customer", "customer"), ("Geography", "geography"),
                  ("Value chain", "value_chain"), ("Synergy", "synergy")),
        "argued": ("product", "customer", "geography"),
    },
    "financial": {
        "dims": {"ticket size": "Ticket size", "ticket": "Ticket size", "geography": "Geography",
                 "investment criteria": "Investment criteria", "criteria": "Investment criteria"},
        "required": ("Ticket size", "Geography", "Investment criteria"),
        "judge": (("Ticket", "ticket"), ("Geography", "geography"), ("Criteria", "criteria"),
                  ("Portfolio", "portfolio")),
        "argued": ("ticket", "geography", "criteria"),
    },
}
MAX_QUOTE_WORDS = 40
NO_EVIDENCE = "no supporting evidence"
NO_EVIDENCE_JUDGE = "no evidence in the briefs"
QUOTE_CHARS = "\"'“”„‟«»‚‘’"

ARG_RE = re.compile(r"^\s*(?:[-*]\s+)?(\d+)\s*[.)]\s*([A-Za-z/ ]+?)\s*(?:\([^)]*\))?\s*:\s*(.+?)\s*$")
ARG_LIKE_RE = re.compile(r"^\s*(?:[-*]\s+)?\d+\s*[.)]\s*\S")
SRC_RE = re.compile(r"^\s*[-*]\s*\[(S\d+)\]\s*(.+?)\s*$")
CITE_RE = re.compile(r"\[([^\]]+)\]")
ID_RE = re.compile(r"#([sf]-[a-z]?\d+)\b", re.I)
SECTION_RE = re.compile(r"^\s*(?:#{2,4}\s*|\*\*)\s*(sources|notes?)\s*(?:\*\*)?\s*:?\s*$", re.I)
COMPANY_RE = re.compile(r"^\s*#{2,3}\s+(.+?)\s*$")
DECISION_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(Advocate|Critic)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(KEEP|DROP)\s*\|\s*(.*)$", re.I)


def demark(line: str) -> str:
    """Remove markdown emphasis that agents like to add (**label**, __x__, `x`)."""
    return re.sub(r"\*\*|__|`", "", line)


def norm(s: str) -> str:
    s = html.unescape(s).replace("­", "")
    for a, b in (("“", '"'), ("”", '"'), ("„", '"'), ("’", "'"), ("‘", "'"), ("–", "-"),
                 ("—", "-"), (" ", " ")):
        s = s.replace(a, b)
    s = re.sub(r"[\"'`]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def company_key(heading: str) -> str:
    m = ID_RE.search(heading)
    if m:
        return m.group(1).lower()
    return re.sub(r"\W+", "", heading.casefold())


def _cites(body: str) -> list:
    return [c.strip() for g in CITE_RE.findall(body) for c in g.split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Brief parsing
# ---------------------------------------------------------------------------
def parse_brief(body: str, side: str) -> dict:
    dims = SIDES[side]["dims"]
    companies, sources, current, notes = [], {}, None, []
    section = None                      # None | "company" | "sources" | "notes"
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = demark(raw)
        sm = SECTION_RE.match(raw) or SECTION_RE.match(line)
        if sm:
            section = "sources" if sm.group(1).lower() == "sources" else "notes"
            current = None
            continue
        cm = COMPANY_RE.match(line)
        if cm and not line.lstrip().startswith("# "):
            section = "company"
            title = cm.group(1).strip()
            current = {"company": title, "key": company_key(title), "searches": "", "arguments": [],
                       "unreadable": []}
            companies.append(current)
            continue
        if section == "sources":
            m = SRC_RE.match(line)
            if m:
                sources[m.group(1)] = parse_source(m.group(2))
            continue
        if section == "notes":
            if line.strip():
                notes.append(line.strip())
            continue
        if current is None:
            continue
        if line.strip().lower().startswith("searches:"):
            current["searches"] = line.split(":", 1)[1].strip()
            continue
        m = ARG_RE.match(line)
        if m:
            dim = dims.get(re.sub(r"\s+", " ", m.group(2).strip().lower()))
            arg_body = m.group(3)
            cites = _cites(arg_body)
            current["arguments"].append({
                "n": int(m.group(1)), "dimension": dim, "raw_dimension": m.group(2).strip(),
                "text": arg_body, "sources": [c for c in cites if re.fullmatch(r"S\d+", c)],
                "facts": [c for c in cites if re.fullmatch(r"F\d+", c)],
                "other": [c for c in cites if not re.fullmatch(r"[SF]\d+", c)],
                "no_evidence": arg_body.strip().lower().startswith(NO_EVIDENCE)})
        elif ARG_LIKE_RE.match(line):
            current["unreadable"].append(line.strip()[:120])
    return {"companies": companies, "sources": sources, "notes": notes}


def parse_source(rest: str) -> dict:
    parts = [p.strip() for p in rest.split("|")]
    url = parts[0] if parts else ""
    quote = parts[1].strip(QUOTE_CHARS + " ") if len(parts) > 1 else ""
    return {"url": url, "quote": quote, "type": " | ".join(parts[2:]) if len(parts) > 2 else ""}


def renumber(brief: dict, start: int) -> tuple:
    """Renumber a parsed brief's sources S<start+1>...; returns (brief, next start).
    A citation of a source the brief does not define becomes 'S?<n>' - an
    unreadable citation - so it can never bind to another brief's source."""
    mapping, n = {}, start
    for sid in brief["sources"]:
        n += 1
        mapping[sid] = f"S{n}"

    def sub(s):
        return re.sub(r"\bS(\d+)\b", lambda m: mapping.get("S" + m.group(1), "S?" + m.group(1)), s)

    out = {"companies": [], "sources": {mapping[k]: v for k, v in brief["sources"].items()},
           "notes": list(brief.get("notes", []))}
    for c in brief["companies"]:
        c2 = dict(c)
        c2["arguments"] = []
        for a in c["arguments"]:
            a2 = dict(a)
            a2["text"] = sub(a["text"])
            a2["sources"] = [mapping[s] for s in a["sources"] if s in mapping]
            a2["other"] = list(a.get("other", [])) + ["S?" + s[1:] for s in a["sources"] if s not in mapping]
            c2["arguments"].append(a2)
        out["companies"].append(c2)
    return out, n


def merge(briefs: list) -> dict:
    """Join parsed briefs of one side; sources renumbered S1, S2 ... across all."""
    merged = {"companies": [], "sources": {}, "notes": []}
    n = 0
    for b in briefs:
        r, n = renumber(b, n)
        merged["companies"] += r["companies"]
        merged["sources"].update(r["sources"])
        merged["notes"] += r["notes"]
    return merged


def render_brief(brief: dict, title: str) -> str:
    lines = [f"# {title}"]
    for c in brief["companies"]:
        lines += ["", f"## {c['company']}"]
        if c.get("searches"):
            lines.append(f"Searches: {c['searches']}")
        for a in c["arguments"]:
            lines.append(f"{a['n']}. {a['dimension'] or a['raw_dimension']}: {a['text']}")
    lines += ["", "## Sources"]
    for sid, s in brief["sources"].items():
        lines.append(f"- [{sid}] {s['url']} | \"{s['quote']}\" | {s['type']}")
    if brief.get("notes"):
        lines += ["", "## Notes"] + brief["notes"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Quote check (pages fetched in parallel, once per URL)
# ---------------------------------------------------------------------------
_cache: dict = {}


def _fetch(url: str) -> tuple:
    if not re.match(r"^https?://", url):
        return None, "not a web address"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) buyer-list source check",
            "Accept-Language": "en,de;q=0.8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read(6_000_000)
        if "pdf" in ctype.lower() or data[:4] == b"%PDF":
            return None, "PDF - check by reading the document"
        raw = data.decode("utf-8", errors="ignore")
        raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        return norm(raw), ""
    except Exception as exc:  # network, HTTP, timeout
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"


def prefetch(urls: list, workers: int = 16) -> None:
    todo = sorted({u for u in urls if u and u not in _cache})
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, res in zip(todo, ex.map(_fetch, todo)):
            _cache[url] = res


def page_text(url: str) -> tuple:
    if url not in _cache:
        _cache[url] = _fetch(url)
    return _cache[url]


def quote_status(url: str, quote: str) -> tuple:
    body, why = page_text(url)
    if body is None:
        return "FETCH_FAILED", why
    q = norm(quote)
    if q and q in body:
        return "FOUND", ""
    words = q.split()
    if len(words) >= 4:
        grams = {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}
        hit = sum(1 for g in grams if g in body) / len(grams)
        if hit >= 0.8:
            return "PARTIAL", f"{hit:.0%} of the quote's word sequences found"
    if len(body) < 2000:
        return "NOT_FOUND", "page text is very short (probably loaded by script) - check in a browser"
    return "NOT_FOUND", ""


# ---------------------------------------------------------------------------
# check: format + quotes
# ---------------------------------------------------------------------------
def structural_problems(brief: dict, side: str, expected_keys: Optional[list] = None) -> list:
    """Problems that make a brief unusable (the research task must be re-run)."""
    out = []
    seen = {}
    for c in brief["companies"]:
        seen[c["key"]] = seen.get(c["key"], 0) + 1
        if not c["arguments"]:
            out.append(f"{c['company']}: no readable argument lines"
                       + (f" (unreadable: {c['unreadable'][0]!r})" if c.get("unreadable") else ""))
        if any(a["sources"] for a in c["arguments"]) and not brief["sources"]:
            out.append(f"{c['company']}: cites sources but no '## Sources' list could be read")
    for k, n in seen.items():
        if n > 1:
            out.append(f"company {k} appears {n} times")
    if expected_keys is not None:
        miss = [k for k in expected_keys if k not in seen]
        extra = [k for k in seen if k not in expected_keys]
        if miss:
            out.append(f"companies missing from the brief: {', '.join(miss)}")
        if extra:
            out.append(f"unexpected companies in the brief: {', '.join(extra)}")
    return out


def check(brief: dict, side: str, fact_ids: set, expected_keys: Optional[list] = None,
          fetch: bool = True) -> dict:
    """Return the check report: per-argument format errors, per-source status, structural errors."""
    required = SIDES[side]["required"]
    errors = []
    fetch = fetch and not os.environ.get("BUYERLIST_NO_FETCH")
    if fetch:
        prefetch([s["url"] for s in brief["sources"].values() if s.get("url")])
    for sid, s in brief["sources"].items():
        if not s["url"]:
            errors.append(f"source {sid}: no URL")
        if not s["quote"]:
            errors.append(f"source {sid}: no verbatim quote")
        elif len(s["quote"].split()) > MAX_QUOTE_WORDS:
            errors.append(f"source {sid}: quote longer than {MAX_QUOTE_WORDS} words")
        if s["url"] and s["quote"] and fetch:
            s["status"], s["detail"] = quote_status(s["url"], s["quote"])
        else:
            s["status"], s["detail"] = ("NO_QUOTE", "") if not s["quote"] else ("NOT_CHECKED", "")
    for c in brief["companies"]:
        dims = [a["dimension"] for a in c["arguments"]]
        c["errors"] = []
        if len(c["arguments"]) != 3:
            c["errors"].append(f"{len(c['arguments'])} arguments - exactly 3 required")
        missing = [d for d in required if d not in dims]
        if missing:
            c["errors"].append(f"no argument for {', '.join(missing)}")
        if not c["searches"]:
            c["errors"].append("'Searches:' line missing")
        for u in c.get("unreadable", []):
            c["errors"].append(f"unreadable argument line: {u!r}")
        for a in c["arguments"]:
            a["format_errors"] = []
            if a["dimension"] is None:
                a["format_errors"].append(
                    f"dimension '{a['raw_dimension']}' not allowed ({', '.join(required)})")
            if a["dimension"] and dims.count(a["dimension"]) > 1:
                a["format_errors"].append(f"dimension {a['dimension']} argued more than once")
            if not a["no_evidence"]:
                if not a["sources"]:
                    a["format_errors"].append("cites no source [S#]")
                unknown = ([s for s in a["sources"] if s not in brief["sources"]]
                           + [f for f in a["facts"] if f not in fact_ids])
                if unknown:
                    a["format_errors"].append(f"unknown IDs {', '.join(unknown)}")
                if a["other"]:
                    a["format_errors"].append(f"unreadable citation {', '.join(a['other'])}")
            elif a["sources"] or a["other"]:
                a["format_errors"].append("'no supporting evidence' point must not cite sources")
            if a["format_errors"]:
                errors.append(f"{c['company']} #{a['n']}: " + "; ".join(a["format_errors"]))
        for e in c["errors"]:
            errors.append(f"{c['company']}: {e}")
    brief["errors"] = errors
    brief["structural"] = structural_problems(brief, side, expected_keys)
    return brief


def render_check_md(brief: dict, title: str) -> str:
    lines = [f"# Source check - {title}", "",
             "Status: FOUND = quote is on the page; PARTIAL = nearly identical; NOT_FOUND / FETCH_FAILED = "
             "the Controller must open the page itself.", ""]
    if brief.get("errors"):
        lines += ["## Format errors (these arguments are dropped automatically)"]
        lines += [f"- {e}" for e in brief["errors"]] + [""]
    for c in brief["companies"]:
        lines.append(f"## {c['company']}")
        lines.append(f"Searches: {c['searches'] or '(missing)'}")
        for a in c["arguments"]:
            lines.append(f"{a['n']}. {a['dimension'] or a['raw_dimension']}: {a['text']}")
            for sid in a["sources"]:
                s = brief["sources"].get(sid)
                if s:
                    det = f" ({s['detail']})" if s.get("detail") else ""
                    lines.append(f"   - {sid} [{s.get('status')}{det}] {s['url']} | \"{s['quote']}\" | {s['type']}")
        lines.append("")
    return "\n".join(lines)


def stats(brief: dict) -> dict:
    st = [s.get("status") for s in brief["sources"].values()]
    return {"companies": len(brief["companies"]),
            "arguments": sum(len(c["arguments"]) for c in brief["companies"]),
            "sources": len(st),
            **{k: st.count(k) for k in ("FOUND", "PARTIAL", "NOT_FOUND", "FETCH_FAILED", "NO_QUOTE") if st.count(k)}}


# ---------------------------------------------------------------------------
# Controller decisions
# ---------------------------------------------------------------------------
def parse_decisions(report_text: str) -> dict:
    """{(role, company_key, n): (KEEP|DROP, reason)}"""
    out = {}
    for line in report_text.replace("\r\n", "\n").split("\n"):
        m = DECISION_RE.match(demark(line))
        if m:
            out[(m.group(1).lower(), company_key(m.group(2)), int(m.group(3)))] = (
                m.group(4).upper(), m.group(5).strip())
    return out


def missing_decisions(brief: dict, role: str, decisions: dict, only: Optional[set] = None) -> list:
    miss = []
    for c in brief["companies"]:
        if only is not None and c["key"] not in only:
            continue
        for a in c["arguments"]:
            if (role, c["key"], a["n"]) not in decisions:
                miss.append(f"{role} | {c['company']} | {a['n']}")
    return miss


def decision_of(a: dict, role: str, key: str, decisions: dict) -> tuple:
    dec, why = decisions.get((role, key, a["n"]), ("DROP", "not reviewed by the Controller"))
    if a.get("format_errors"):
        dec, why = "DROP", "format: " + "; ".join(a["format_errors"])
    return dec, why


def retry_candidates(brief: dict, role: str, decisions: dict, side: str) -> list:
    """Companies where more than half of the 3 required dimensions have no kept point
    (dropped, format error, or never readable). 'No supporting evidence' kept = kept."""
    hits = []
    required = SIDES[side]["required"]
    for c in brief["companies"]:
        points, kept_dims = [], set()
        for a in c["arguments"]:
            dec, why = decision_of(a, role, c["key"], decisions)
            points.append((a["n"], dec, why))
            if dec == "KEEP" and a["dimension"]:
                kept_dims.add(a["dimension"])
        dropped = [d for d in required if d not in kept_dims]
        if len(dropped) * 2 > len(required):
            for d in dropped:
                if d not in {a["dimension"] for a in c["arguments"]}:
                    points.append((0, "DROP", f"no readable {d} point"))
            hits.append({"key": c["key"], "company": c["company"], "points": points})
    return hits


# ---------------------------------------------------------------------------
# clean: apply decisions, anonymise as Brief A / B
# ---------------------------------------------------------------------------
def clean(brief: dict, role: str, decisions: dict, label: str, keys: Optional[list] = None) -> tuple:
    """Return (markdown of the cleaned brief, kept count, dropped list, id mapping)."""
    mapping: dict = {}
    kept_total, dropped = 0, []
    body = [f"# Brief {label}", ""]
    for c in brief["companies"]:
        if keys is not None and c["key"] not in keys:
            continue
        body.append(f"## {c['company']}")
        for a in c["arguments"]:
            dec, why = decision_of(a, role, c["key"], decisions)
            if dec == "KEEP" and any(s not in brief["sources"] for s in a["sources"]):
                dec, why = "DROP", "cites a source that is not in the list"
            if dec != "KEEP":
                dropped.append(f"{c['company']} #{a['n']} ({a['dimension']}): {why}")
                continue
            kept_total += 1
            for sid in a["sources"]:
                mapping.setdefault(sid, f"{label}{len(mapping) + 1}")
            t = re.sub(r"\bS(\d+)\b", lambda m: mapping.get("S" + m.group(1), "S?" + m.group(1)), a["text"])
            body.append(f"{a['n']}. {a['dimension']}: {t}")
        body.append("")
    body.append("## Sources")
    for sid, new in mapping.items():
        s = brief["sources"][sid]
        body.append(f"- [{new}] {s['url']} | \"{s['quote']}\" | {s['type']}")
    return "\n".join(body) + "\n", kept_total, dropped, mapping


def kept_substantive(brief: dict, role: str, decisions: dict) -> dict:
    """{company_key: number of KEPT points that carry evidence (not 'no supporting evidence')}"""
    out = {}
    for c in brief["companies"]:
        n = 0
        for a in c["arguments"]:
            dec, _ = decision_of(a, role, c["key"], decisions)
            if dec == "KEEP" and not a.get("no_evidence") and all(s in brief["sources"] for s in a["sources"]):
                n += 1
        out[c["key"]] = n
    return out


def side_label(seed: str) -> str:
    """Which role becomes Brief A - random but reproducible per batch."""
    return random.Random(seed).choice(["advocate", "critic"])


# ---------------------------------------------------------------------------
# Judge verdict
# ---------------------------------------------------------------------------
HEAD_RE = re.compile(r"^\s*#{2,3}\s+(.+?)\s+[–—:-]+\s+([0-9]+(?:[.,][0-9]+)?)(?:\s*/\s*5)?\s*$")


def parse_role(val: str) -> str:
    v = demark(val).strip().lower()
    head = re.split(r"\s+[-–—:]\s+|[(,;]", v, maxsplit=1)[0]
    for word, role in (("customer", "customer"), ("reseller", "customer"), ("distributor", "customer"),
                       ("supplier", "supplier"), ("vendor", "supplier"), ("horizontal", "horizontal"),
                       ("peer", "horizontal"), ("competitor", "horizontal")):
        if word in head:
            return role
    return ""


def parse_verdict(body: str, side: str) -> dict:
    """{company_key: {heading, stated_fit, scores{dim: {score, reason, cites}}, calc, deciding,
    against, confidence, confidence_reason, role}}"""
    dims = SIDES[side]["judge"]
    label_to_key = {lbl.lower(): k for lbl, k in dims}
    dim_re = re.compile(r"^\s*[-*]\s*(" + "|".join(re.escape(l) for l, _ in dims) + r")\s*[:(]?\s*(\d)\s*\)?\s*:\s*(.*)$", re.I)
    out, cur = {}, None
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = demark(raw)
        m = HEAD_RE.match(line)
        if m:
            heading = m.group(1).strip()
            cur = {"heading": heading, "stated_fit": float(m.group(2).replace(",", ".")), "scores": {},
                   "calc": "", "deciding": "", "against": "", "confidence": "", "confidence_reason": "",
                   "role": "", "role_text": ""}
            out[company_key(heading)] = cur
            continue
        if cur is None:
            continue
        m = dim_re.match(line)
        if m:
            key = label_to_key[m.group(1).lower()]
            reason = m.group(3).strip()
            cur["scores"][key] = {"score": int(m.group(2)), "reason": reason, "cites": _cites(reason)}
            continue
        low = line.strip().lower().lstrip("-* ").strip()
        val = line.split(":", 1)[1].strip() if ":" in line else ""
        if low.startswith("calc"):
            cur["calc"] = val
        elif low.startswith("deciding argument"):
            cur["deciding"] = val
        elif low.startswith("stands against"):
            cur["against"] = val
        elif low.startswith("role"):
            cur["role_text"] = val
            cur["role"] = parse_role(val)
        elif low.startswith("confidence"):
            m2 = re.match(r"(High|Medium|Low)\b\s*(.*)", val, re.I)
            if m2:
                cur["confidence"] = m2.group(1).capitalize()
                cur["confidence_reason"] = m2.group(2).strip(" ()-")
    return out


def judge_check(verdict: dict, side: str, valid_ids: set) -> list:
    """Problems: unknown IDs or a dimension score without a valid source (-> confidence Low)."""
    problems = []
    for key, v in verdict.items():
        for lbl, dim in SIDES[side]["judge"]:
            e = v["scores"].get(dim)
            if not e:
                problems.append((key, dim, "missing"))
                continue
            ok = [c for c in e["cites"] if c in valid_ids]
            bad = [c for c in e["cites"] if c not in valid_ids and re.fullmatch(r"[ABF]\d+", c)]
            if bad:
                problems.append((key, dim, f"unknown IDs {', '.join(bad)}"))
            if not ok and NO_EVIDENCE_JUDGE not in e["reason"].lower():
                problems.append((key, dim, "no valid source cited"))
            if NO_EVIDENCE_JUDGE in e["reason"].lower() and e["score"] != DEFAULT_NO_EVIDENCE:
                problems.append((key, dim, f"'no evidence' must score the default {DEFAULT_NO_EVIDENCE}"))
    return problems


def strip_cites(s: str) -> str:
    s = CITE_RE.sub("", demark(text(s)))
    s = re.sub(r"^(Brief [AB]\s*[-–:]\s*)", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .;-")
    return s + "." if s else ""
