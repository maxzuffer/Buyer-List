"""Synthetic run for the end-to-end and hook tests (no network: BUYERLIST_NO_FETCH=1)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ["BUYERLIST_NO_FETCH"] = "1"

from buyerlist import assemble, audit, debate, excel, gates, mandate, route, state, universe  # noqa: E402
from buyerlist.common import ACTIVE, RUNS, read_json, write_json, write_text  # noqa: E402

PROJECT = "zz test run"
SLUG = "zz-test-run"

STRAT = [  # id, name, revenue fig, owners, pe, role, scores
    ("s-001", "BigTel AG", {"status": "VERIFIED", "value": 500, "fy": "2025", "scope": "group", "source": "AR 2025"},
     [], False, "horizontal", (5, 4, 4, 3, 3)),
    ("s-002", "DataCo GmbH", {"status": "VERIFIED", "value": 80, "fy": "2025", "scope": "group", "source": "AR"},
     [{"name": "Founder Family", "stake_pct": 100, "type": "family"}], False, "horizontal", (3, 4, 4, 3, 2)),
    ("s-003", "CloudCo SE", {"status": "ESTIMATE", "value": [40, 60], "fy": "2025", "scope": "group",
                             "method": "employees 300 x EUR 150k"}, [], False, "horizontal", (2, 3, 4, 2, 2)),
    ("s-004", "SmallCo GmbH", {"status": "VERIFIED", "value": 20, "fy": "2025", "scope": "entity", "source": "AR"},
     [{"name": "Founder", "stake_pct": 100, "type": "founder"}], False, "horizontal", None),
    ("s-005", "PEPlatform GmbH", {"status": "VERIFIED", "value": 100, "fy": "2025", "scope": "group", "source": "AR"},
     [{"name": "Alpha Capital", "stake_pct": 70, "type": "PE", "website": "https://alpha-capital.example"}],
     True, "horizontal", (5, 4, 4, 3, 3)),            # Rank 1, same products: platform even of a weak sponsor
    ("s-006", "Reseller AG", {"status": "VERIFIED", "value": 200, "fy": "2025", "scope": "group", "source": "AR"},
     [], False, "customer", (2, 3, 4, 2, 2)),
    ("s-007", "Sigma Platform GmbH", {"status": "VERIFIED", "value": 90, "fy": "2025", "scope": "group", "source": "AR"},
     [{"name": "Zeta Infra", "stake_pct": 40, "type": "PE", "website": "https://f-004.example"},
      {"name": "Alpha Capital", "stake_pct": 30, "type": "PE", "website": "https://alpha-capital.example"}],
     True, "horizontal", (4, 3, 3, 3, 3)),            # fit 3.5, product 4: platform of Zeta, not of weak Alpha
    ("s-008", "Tau Platform GmbH", {"status": "VERIFIED", "value": 70, "fy": "2025", "scope": "group", "source": "AR"},
     [{"name": "Zeta Infra", "stake_pct": 100, "type": "PE", "website": "https://f-004.example"}],
     True, "horizontal", (3, 4, 4, 3, 3)),            # fit 3.4 but product 3: never a platform
]
FIN = [  # id, name, scores (ticket, geo, criteria, portfolio)
    ("f-001", "Gamma Partners", (3, 3, 3, 2)), ("f-002", "Delta Equity", (2, 2, 3, 1)),
    ("f-003", "Epsilon Ventures", (1, 2, 2, 1)), ("f-004", "Zeta Infra", (4, 4, 4, 3)),
]
FR = ("f-r001", "Alpha Capital", (2, 2, 2, 1))


def run_dir() -> Path:
    return RUNS / SLUG


def cleanup() -> None:
    shutil.rmtree(run_dir(), ignore_errors=True)
    for f in Path(excel.OUTPUT).glob(f"*_Longlist {PROJECT}*.xlsx"):
        f.unlink()


def g(run, key):
    iss, _ = gates.run_gate(run, key)
    assert iss.ok(), f"gate {key} failed:\n{iss.render()}"


def figure_na():
    return {"status": "n/a", "reason": "not published"}


def s_profile(cid, name, rev, owners, pe):
    own = [dict(o, source="register", as_of="2026-05", country="DE") for o in owners]
    return {"id": cid, "type": "strategic", "name": name, "website": f"https://{cid}.example", "hq_country": "DE",
            "hq_city": "Munich", "bereich": "Data centres", "description": f"{name} runs data centres. "
            "It serves enterprises in Germany and Austria.", "revenue": rev, "ebitda": figure_na(),
            "employees": {"status": "VERIFIED", "value": 300, "fy": "2025", "source": "website"},
            "listed": not owners, "ownership_type": "PE-backed" if pe else ("listed" if not owners else "family"),
            "owners": own, "pe_owned": pe, "ownership_note": "",
            "ma_activity": {"deals": [{"year": 2024, "target": "X", "source": "press"}], "statements": [],
                            "searched": "press"}}


def f_profile(cid, name, website=None):
    return {"id": cid, "type": "financial", "name": name, "website": website or f"https://{cid}.example",
            "hq_country": "DE", "hq_city": "Munich", "investor_type": "PE",
            "description": f"{name} is a mid-market PE investor. It invests in DACH technology companies.",
            "aum": None, "latest_fund": {"name": "Fund I", "size": {"status": "VERIFIED", "value": 800,
                                                                    "source": "press", "fy": "2024"}, "year": 2024,
                                         "source": "https://x.example"},
            "ticket": {"status": "STATED", "equity": [20, 100], "quote": "EUR 20-100m", "source": "https://x.example"},
            "geo_focus": {"summary": "DACH", "quote": "DACH", "source": "https://x.example"},
            "sector_focus": {"summary": "TMT", "quote": "TMT", "source": "https://x.example"},
            "deal_types": ["majority buyout"], "offices": ["Munich"], "relevant_portfolio": [],
            "deployment": {"fund_year": 2024, "recent_deals": [{"year": 2025, "target": "Y", "source": "press"}]}}


def brief(side, role, comps):
    dims = ("Product/service", "Customer", "Geography") if side == "strategic" else \
        ("Ticket size", "Geography", "Investment criteria")
    lines, srcs, n = [f"# {role.capitalize()} brief"], [], 0
    for heading in comps:
        lines += ["", f"## {heading}", 'Searches: "x"']
        for i, d in enumerate(dims, 1):
            n += 1
            lines.append(f"{i}. {d}: point {n} [S{n}]")
            srcs.append(f'- [S{n}] https://src.example/{n} | "quote {n}" | company website')
    return "\n".join(lines + ["", "## Sources"] + srcs) + "\n"


def controller(d: Path, side, b, drop=None, r2=False):
    drop = drop or {}
    lines = []
    for role in ("advocate", "critic"):
        p = d / f"check_{role}{'_r2' if r2 else ''}.json"
        if not p.exists():
            continue
        rep = read_json(p)
        for c in rep["companies"]:
            for a in c["arguments"]:
                dec = "DROP" if a["n"] in drop.get((role, c["key"]), []) else "KEEP"
                lines.append(f"- {role.capitalize()} | {c['company']} | {a['n']} | {dec} | test")
    write_text(d / ("controller_r2.md" if r2 else "controller.md"), "\n".join(lines) + "\n## Summary\nok\n")


def write_verdicts(run):
    sc = {x[0]: x[6] for x in STRAT if x[6]}
    sc.update({x[0]: x[2] for x in FIN})
    sc[FR[0]] = FR[2]
    roles = {x[0]: x[5] for x in STRAT}
    for side in ("strategic", "financial"):
        for b in debate.load_batches(run, side):
            write_text(debate.bdir(run, side, b["batch"]) / "verdict.md", verdict(side, b, sc, roles))


def write_audit(run):
    idx = audit.prepare(run)
    rows = {x["id"]: x for x in audit.sample(run)}
    for t in idx:
        out = Path(t["outputs"][0])
        ids = read_json(out.with_name(out.stem + ".inputs.json"))["ids"]
        fs = []
        for cid in ids:
            for j in range(1, len(rows[cid]["claims"]) + 1):
                wrong = cid == "s-001" and j == 1
                fs.append({"id": cid, "claim": j, "verdict": "wrong" if wrong else "confirmed",
                           "detail": "rev 480" if wrong else "ok", "correct_value": "480" if wrong else "",
                           "source": "https://x"})
        write_json(out, {"findings": fs})


def verdict(side, b, scores_by_id, roles=None):
    out = []
    for c in debate.active(b):
        sc = scores_by_id[c["id"]]
        if side == "strategic":
            p, cu, ge, v, sy = sc
            out += [f"## {c['heading']} – 9.9", f"- Product {p}: r [A1]", f"- Customer {cu}: r [B1]",
                    f"- Geography {ge}: r [A2]", f"- Value chain {v}: r [A1]", f"- Synergy {sy}: r [B1]",
                    "- Calc: x", f"- Role: {(roles or {}).get(c['id'], 'horizontal')} - test",
                    "- Deciding argument: Brief A - strong [A1]", "- Stands against it: none",
                    "- Confidence: High (test)", ""]
        else:
            t, ge, cr, po = sc
            out += [f"## {c['heading']} – 9.9", f"- Ticket {t}: r [A1]", f"- Geography {ge}: r [B1]",
                    f"- Criteria {cr}: r [A2]", f"- Portfolio {po}: r [A1]", "- Calc: x",
                    "- Deciding argument: Brief A - fit [A1]", "- Stands against it: none",
                    "- Confidence: High (test)", ""]
    return "\n".join(out)


def build(until: str = "8", rounds=None) -> Path:
    """Create the synthetic run and pass every gate up to `until` (rounds: review schedule, e.g. [2, 1])."""
    cleanup()
    run = mandate.init(PROJECT, "Target GmbH", "https://www.target.example", 30, 10, [12, 16], [60, 120],
                       as_of_date="2026-10-19", hq_city="Vienna", targets=(3, 3), rounds=rounds)
    g(run, "0")
    if until == "0":
        return run
    # 1 client sheet
    facts = [
        "Target runs colocation data centres in Vienna {product}", "Target offers cloud IaaS {product}",
        "Target offers business internet connectivity {product}", "Customers are enterprises {customer}",
        "Customers include public bodies {customer}", "Sites in Vienna {geography}", "Serves Austria {geography}"]
    lines = ["# Client sheet - test", "Mandate: SELL-SIDE - find buyers for the client.", "", "## Facts"]
    for i, f in enumerate(facts, 1):
        lines.append(f'- [F{i}] {f} (source: https://www.target.example/{i} | "quote {i}")')
    n = len(facts)
    lines.append(f"- [F{n+1}] Assumed revenue EUR 30m and EBITDA EUR 10m {{financials}} (source: user assumption)")
    lines.append(f"- [F{n+2}] EV EUR 120-160m; equity cheque EUR 60-120m {{deal}} (source: user assumption)")
    state.open_stage(run, state.load(run), "1")
    write_text(mandate.client_sheet_path(run), "\n".join(lines) + "\n")
    g(run, "1")
    if until == "1":
        return run
    # 2 universe
    lenses = {"lenses": [{"id": f"S0{i}", "type": "strategic", "title": "t", "min": 5,
                          "brief": "Strategic peers running data centres and connectivity in DACH, for testing."}
                         for i in range(1, 6)] +
                        [{"id": f"F0{i}", "type": "financial", "title": "t", "min": 5,
                          "brief": "Mid-market buyout investors with DACH digital-infrastructure focus, for tests."}
                         for i in range(1, 5)]}
    st = state.load(run); state.open_stage(run, st, "2"); state.save(run, st)
    write_json(universe.lenses_path(run), lenses)
    cands = [{"name": nm, "type": "strategic", "website": f"https://{cid}.example", "hq_country": "DE",
              "hq_city": "Munich", "segment": "DC", "rationale": "r", "source_url": f"https://{cid}.example"}
             for cid, nm, *_ in STRAT]
    cands += [{"name": nm, "type": "financial", "website": f"https://{cid}.example", "hq_country": "DE",
               "hq_city": "Munich", "segment": "PE", "rationale": "r", "source_url": f"https://{cid}.example"}
              for cid, nm, _ in FIN]
    # a duplicate (same domain) and the client itself
    cands.append(dict(cands[0], name="BigTel Holding"))
    cands.append({"name": "Target GmbH", "type": "strategic", "website": "https://target.example", "hq_country": "AT",
                  "segment": "x", "rationale": "r", "source_url": "https://target.example"})
    per_lens = {"S01": [cands[0], cands[-2], cands[-1]], "S02": [cands[1]], "S03": [cands[2]],
                "S04": [cands[3]], "S05": [cands[4], cands[5], cands[6], cands[7]],
                "F01": [cands[8]], "F02": [cands[9]], "F03": [cands[10]], "F04": [cands[11]]}
    for lid, cs in per_lens.items():
        write_json(universe.raw_dir(run) / f"{lid}-r0.json", {"lens": lid, "round": 0, "candidates": cs})
    uni = universe.merge(run)
    got = sorted(c["id"] for c in uni["candidates"])
    assert got == sorted([x[0] for x in STRAT] + [x[0] for x in FIN]), got
    bad = [c["id"] for c in uni["candidates"] if c["website"] != f"https://{c['id']}.example"]
    assert not bad, f"universe ids do not match the fixture ids: {bad}"
    assert len(uni["duplicates"]) == 1 and len(uni["excluded"]) == 1, (uni["duplicates"], uni["excluded"])
    g(run, "2")
    if until == "2":
        return run
    # 3 profiles
    st = state.load(run); state.open_stage(run, st, "3"); state.save(run, st)
    for cid, nm, rev, owners, pe, _, _ in STRAT:
        write_json(run / "03_profiles" / f"{cid}.json", s_profile(cid, nm, rev, owners, pe))
    for cid, nm, _ in FIN:
        write_json(run / "03_profiles" / f"{cid}.json", f_profile(cid, nm))
    g(run, "3")
    if until == "3":
        return run
    # 4 route (+ sponsor profile)
    st = state.load(run); state.open_stage(run, st, "4"); state.save(run, st)
    r = route.build(run)
    assert r["counts"] == {"strategic": 4, "reroute": 3, "excluded": 1, "group_duplicate": 0, "financial_added": 1}, r["counts"]
    write_json(run / "03_profiles" / "f-r001.json", f_profile("f-r001", "Alpha Capital", "https://alpha-capital.example"))
    g(run, "4")
    if until == "4":
        return run
    # 5a research
    st = state.load(run); state.open_stage(run, st, "5a"); state.save(run, st)
    debate.prepare(run)
    for side in ("strategic", "financial"):
        for b in debate.load_batches(run, side):
            heads = {c["id"]: c["heading"] for c in b["companies"]}
            for gi, grp in enumerate(b["groups"], 1):
                for role in ("advocate", "critic"):
                    write_text(debate.part_path(run, side, b["batch"], role, gi),
                               brief(side, role, [heads[x] for x in grp]))
    debate.merge_check(run)
    g(run, "5a")
    # 5b controller: drop 2 critic points of s-003 -> retry
    st = state.load(run); state.open_stage(run, st, "5b"); state.save(run, st)
    debate.controller_tasks(run)
    for side in ("strategic", "financial"):
        for b in debate.load_batches(run, side):
            drop = {("critic", "s-003"): [1, 2]} if side == "strategic" else {}
            controller(debate.bdir(run, side, b["batch"]), side, b, drop)
    g(run, "5b")
    # 5c retry
    st = state.load(run); state.open_stage(run, st, "5c"); state.save(run, st)
    idx = debate.retry_tasks(run)
    assert [t["id"] for t in idx] == ["sb01-retry-critic-s-003"], idx
    b = debate.load_batches(run, "strategic")[0]
    head = next(c["heading"] for c in b["companies"] if c["id"] == "s-003")
    write_text(debate.retry_path(run, "strategic", 1, "critic", "s-003"), brief("strategic", "critic", [head]))
    debate.recheck(run)
    debate.controller2_tasks(run)
    for side in ("strategic", "financial"):
        for b in debate.load_batches(run, side):
            d = debate.bdir(run, side, b["batch"])
            if any(read_json(d / "retry.json").values()):
                controller(d, side, b, {}, r2=True)
    g(run, "5c")
    # 5d judge
    st = state.load(run); state.open_stage(run, st, "5d"); state.save(run, st)
    debate.clean(run)
    write_verdicts(run)
    g(run, "5d")
    st = state.load(run); state.open_stage(run, st, "5e"); state.save(run, st)
    debate.collect(run)
    g(run, "5e")
    if until == "5":
        return run
    # 6 assemble
    st = state.load(run); state.open_stage(run, st, "6"); state.save(run, st)
    assemble.build(run)
    g(run, "6")
    if until == "6":
        return run
    # 7 audit
    st = state.load(run); state.open_stage(run, st, "7"); state.save(run, st)
    write_audit(run)
    audit.resolve(run, "s-001", 1, "accepted", "difference immaterial, still > 30")
    g(run, "7")
    if until == "7":
        return run
    st = state.load(run); state.open_stage(run, st, "8"); state.save(run, st)
    excel.write(run)
    g(run, "8")
    return run


class ActiveGuard:
    """Restore runs/.active after the tests."""
    def __enter__(self):
        self.saved = ACTIVE.read_text() if ACTIVE.exists() else None
        return self

    def __exit__(self, *a):
        cleanup()
        if self.saved is None:
            if ACTIVE.exists():
                ACTIVE.unlink()
        else:
            ACTIVE.write_text(self.saved)
