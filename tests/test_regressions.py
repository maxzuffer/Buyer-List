"""Regression tests for the findings of the adversarial review (2026-10-19)."""
import json
import os
import subprocess
import unittest
from datetime import date

from tests import fixture as F
from buyerlist import assemble, audit, debate, excel, gates, profiles, route, state, universe
from buyerlist import sources as S
from buyerlist.common import PYTHON, ROOT, read_json, write_json, write_text
from openpyxl import load_workbook

AS_OF = date(2026, 10, 19)


def g(run, key):
    iss, _ = gates.run_gate(run, key)
    return iss


def guard(payload):
    r = subprocess.run([str(PYTHON), str(ROOT / ".claude" / "hooks" / "guard_artifacts.py")],
                       input=json.dumps(payload), capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    out = r.stdout.strip()
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


class FixFlow(unittest.TestCase):
    """Finding 1/8/24/36: a profile fix after validation must reach the Excel."""

    def setUp(self):
        self.g = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.g.__exit__()

    def test_revenue_fix_removes_buyer(self):
        run = F.build("8")
        self.assertTrue(excel.lock_ok(run)[0])
        state.reopen(run, "3", "audit: DataCo revenue is EUR 25m")
        self.assertFalse((run / "08_final.lock").exists())              # lock dropped on reopen
        p = read_json(run / "03_profiles" / "s-002.json")
        p["revenue"] = {"status": "VERIFIED", "value": 25, "fy": "2025", "scope": "group", "source": "AR 2025"}
        write_json(run / "03_profiles" / "s-002.json", p)
        self.assertTrue(g(run, "3").ok())
        self.assertFalse(g(run, "4").ok())                              # route must be rebuilt
        st = state.load(run); state.open_stage(run, st, "4"); state.save(run, st)
        route.build(run)
        self.assertTrue(g(run, "4").ok())
        self.assertFalse(g(run, "5a").ok())                             # batches must follow the new scope
        st = state.load(run); state.open_stage(run, st, "5a"); state.save(run, st)
        debate.prepare(run)
        b = debate.load_batches(run, "strategic")[0]
        self.assertIn("s-002", b["excluded_ids"])
        self.assertTrue(g(run, "5a").ok())
        self.assertTrue(g(run, "5b").ok())
        self.assertTrue(g(run, "5c").ok())
        self.assertFalse(g(run, "5d").ok())                             # Brief A/B must be rebuilt
        st = state.load(run); state.open_stage(run, st, "5d"); state.save(run, st)
        debate.clean(run)
        self.assertFalse(g(run, "5d").ok())                             # verdict made for older briefs
        F.write_verdicts(run)
        self.assertTrue(g(run, "5d").ok())
        self.assertFalse(g(run, "5e").ok())
        st = state.load(run); state.open_stage(run, st, "5e"); state.save(run, st)
        debate.collect(run)
        self.assertTrue(g(run, "5e").ok())
        self.assertFalse(g(run, "6").ok())
        st = state.load(run); state.open_stage(run, st, "6"); state.save(run, st)
        assemble.build(run)
        iss = g(run, "6")
        # 2 strategic left -> shortfall instead of a stale or padded list
        self.assertFalse(iss.ok())
        self.assertTrue(any("short by 1 strategic" in i.message for i in iss.errors))
        names = [r["id"] for r in assemble.load_list(run)["strategic"]["selected"]]
        self.assertNotIn("s-002", names)


class Guard(unittest.TestCase):
    """Finding 2/3: hand edits and shell bypasses."""

    @classmethod
    def setUpClass(cls):
        cls.g = F.ActiveGuard().__enter__()
        cls.rd = F.build("3")

    @classmethod
    def tearDownClass(cls):
        cls.g.__exit__()

    def bash(self, cmd):
        return guard({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(ROOT)})

    def test_bypasses_blocked(self):
        rel = f"runs/{F.SLUG}"
        for cmd in [
            f".venv/bin/python -m buyerlist status\npython3 -c \"open('{rel}/state.json','w')\"",
            f".venv/bin/python -m buyerlist status & echo x > {rel}/state.json",
            f"cd {rel} && python3 -c \"import json;json.dump({{}},open('state.json','w'))\"",
            f"echo x > runs//{F.SLUG}/state.json",
            f"echo x > ./runs/./{F.SLUG}/03_profiles/s-001.json",
            f"rsync /tmp/x.json {rel}/03_profiles/",
            f"find runs -name state.json -delete",
            f"rm -rf {rel}/03_profiles",
            f"unzip -o /tmp/x.zip -d {rel}",
            f"chmod u+w {rel}/state.json",
            f"sed -i '' 's/a/b/' {rel}/03_profiles/s-001.json",
        ]:
            self.assertTrue(self.bash(cmd), cmd)

    def test_reads_allowed(self):
        rel = f"runs/{F.SLUG}"
        for cmd in [f"cat {rel}/state.json", f"ls -la {rel}", ".venv/bin/python -m buyerlist status",
                    f"cd {ROOT} && .venv/bin/python -m buyerlist status",
                    f"cp {rel}/03_profiles/s-001.json /tmp/x.json",
                    f"grep -c ERROR {rel}/state.json",
                    f".venv/bin/python - <<'EOF'\nimport json\nprint(json.load(open('{rel}/state.json'))['status'])\nEOF"]:
            self.assertFalse(self.bash(cmd), cmd)

    def test_cli_only_files(self):
        for f in ("06_list_data.json", "04_route.json", "02_universe/universe.json", "05_debate/scores_strategic.json",
                  "05_debate/strategic/b01/retry.json", "05_debate/strategic/b01/Brief A.md",
                  "tasks/3/index.json", "07_audit/resolutions.json"):
            self.assertTrue(guard({"tool_name": "Write", "tool_input": {"file_path": str(self.rd / f)}}), f)


class Parsing(unittest.TestCase):
    """Findings 11/16/18/19/20/21/40/44: robust parsing, nothing dropped silently."""

    def test_bold_labels_and_sources_heading(self):
        body = """# Advocate brief

## Equinix #s-014
Searches: "x"
1. **Product/service**: colocation [S1]
2. **Customer** (strongest): enterprises [S2]
3. Geography: Vienna [S2]

### Sources
- [S1] https://a.example | "q1" | web
- [S2] https://b.example | "q2" | web
"""
        b = S.parse_brief(body, "strategic")
        self.assertEqual([a["dimension"] for a in b["companies"][0]["arguments"]],
                         ["Product/service", "Customer", "Geography"])
        self.assertEqual(len(b["sources"]), 2)

    def test_unreadable_lines_are_structural(self):
        body = "## X #s-001\nSearches: x\n- Product: foo [S1]\n\n## Sources\n- [S1] https://a | \"q\" | w\n"
        b = S.parse_brief(body, "strategic")
        S.check(b, "strategic", set(), expected_keys=["s-001"], fetch=False)
        self.assertTrue(b["structural"])

    def test_role_parsing(self):
        v = "## Arrow #s-009 – 2.4\n- Product 2: r [A1]\n- Customer 2: r [A1]\n- Geography 3: r [A1]\n" \
            "- Value chain 2: r [A1]\n- Synergy 2: r [A1]\n- **Role**: **customer** - resells\n- Confidence: Low (x)\n"
        self.assertEqual(S.parse_verdict(v, "strategic")["s-009"]["role"], "customer")
        self.assertEqual(S.parse_role("Supplier (hardware vendor)"), "supplier")
        self.assertEqual(S.parse_role("unclear"), "")

    def test_no_evidence_with_citation_flagged(self):
        body = "## X #s-001\nSearches: x\n1. Product/service: no supporting evidence [S1]\n" \
               "2. Customer: a [S1]\n3. Geography: no supporting evidence for Vienna, but [S1]\n\n" \
               "## Sources\n- [S1] https://a | \"q\" | w\n"
        b = S.parse_brief(body, "strategic")
        S.check(b, "strategic", set(), fetch=False)
        errs = " ".join(b["errors"])
        self.assertIn("must not cite sources", errs)

    def test_undefined_citation_not_rebound(self):
        b1 = S.parse_brief("## A #s-001\nSearches: x\n1. Product/service: a [S2]\n\n## Sources\n- [S1] https://a | \"q\" | w\n", "strategic")
        b2 = S.parse_brief("## B #s-002\nSearches: x\n1. Product/service: b [S1]\n\n## Sources\n- [S1] https://b | \"q\" | w\n", "strategic")
        m = S.merge([b1, b2])
        S.check(m, "strategic", set(), fetch=False)
        a = m["companies"][0]["arguments"][0]
        self.assertEqual(a["sources"], [])
        self.assertTrue(a["format_errors"])

    def test_retry_counts_missing_dimensions(self):
        b = S.parse_brief("## A #s-001\nSearches: x\n1. Product/service: a [S1]\n\n## Sources\n- [S1] https://a | \"q\" | w\n",
                          "strategic")
        S.check(b, "strategic", set(), fetch=False)
        dec = S.parse_decisions("- Advocate | A #s-001 | 1 | KEEP | ok")
        self.assertEqual([h["key"] for h in S.retry_candidates(b, "advocate", dec, "strategic")], ["s-001"])


class UniverseAndProfiles(unittest.TestCase):
    """Findings 12/14/37/39/42/43."""

    def setUp(self):
        self.g = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.g.__exit__()

    def test_same_name_different_domain(self):
        run = F.build("1")
        st = state.load(run); state.open_stage(run, st, "2"); state.save(run, st)
        F.build.__globals__  # noqa
        cands = [{"name": "Acme GmbH", "type": "strategic", "website": "https://acme.de", "hq_country": "Germany",
                  "segment": "x", "rationale": "r", "source_url": "acme.de"},
                 {"name": "Acme AG", "type": "strategic", "website": "https://acme.ch", "hq_country": "CH",
                  "segment": "x", "rationale": "r", "source_url": "https://acme.ch"},
                 {"name": "Deutsche Telekom Capital Partners", "type": "financial", "website": "https://dtcp.capital",
                  "hq_country": "DE", "segment": "VC", "rationale": "r", "source_url": "https://dtcp.capital"},
                 {"name": "Deutsche Telekom AG", "type": "strategic", "website": "https://telekom.com",
                  "hq_country": "DE", "segment": "telco", "rationale": "r", "source_url": "https://telekom.com"}]
        write_json(universe.raw_dir(run) / "S01-r0.json", {"lens": "S01", "candidates": cands})
        res = universe.build(run)
        types = {c["name"]: c["type"] for c in res["universe"]["candidates"]}
        self.assertEqual(types["Deutsche Telekom AG"], "strategic")
        self.assertEqual(types["Deutsche Telekom Capital Partners"], "financial")
        self.assertEqual({c["hq_country"] for c in res["universe"]["candidates"] if c["name"] == "Acme GmbH"}, {"DE"})
        self.assertTrue(any("same name 'acme'" in i.message for i in res["issues"].errors))
        write_json(universe.udir(run) / "distinct.json", {"pairs": [["acme.de", "acme.ch"]]})
        res = universe.build(run)
        self.assertFalse(any("same name" in i.message for i in res["issues"].errors))

    def test_ultimate_owner_rules(self):
        cand = {"id": "s-001", "name": "X", "type": "strategic"}
        base = F.s_profile("s-001", "X", {"status": "VERIFIED", "value": 50, "fy": "2025", "scope": "group",
                                          "source": "AR"}, [], False)
        base["listed"] = False
        base["owners"] = [{"name": "HoldCo GmbH", "stake_pct": 100, "type": "corporate", "source": "register",
                           "as_of": "2026-05"}]
        base["ownership_type"] = "corporate"
        iss = profiles.validate_strategic(base, cand, AS_OF)
        self.assertTrue(any("ultimate_owner" in i.message for i in iss.errors))
        base["owners"][0]["ultimate_owner"] = {"name": "Alpha Capital", "type": "PE",
                                               "website": "https://alpha.example", "source": "press"}
        self.assertTrue(profiles.pe_owned_calc(base))
        base["pe_owned"], base["ownership_type"] = True, "PE-backed"
        self.assertTrue(profiles.validate_strategic(base, cand, AS_OF).ok())

    def test_malformed_profile_values(self):
        cand = {"id": "f-001", "name": "X", "type": "financial"}
        p = F.f_profile("f-001", "X")
        p["ticket"]["ev"] = [None, None]
        p["deployment"]["recent_deals"] = ["2025 deal"]
        p["relevant_portfolio"] = ["Some holding"]
        iss = profiles.validate_financial(p, cand, AS_OF)
        msgs = " ".join(i.message for i in iss.errors)
        self.assertIn("[low, high]", msgs)
        self.assertIn("list of objects", msgs)
        assemble.ticket_text(p)                                          # must not crash
        profiles.deployment_rating(p, AS_OF)


class GroupDuplicate(unittest.TestCase):
    def setUp(self):
        self.g = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.g.__exit__()

    def test_subsidiary_folded_into_parent(self):
        run = F.build("3")
        state.reopen(run, "3", "DataCo is owned by BigTel")
        p = read_json(run / "03_profiles" / "s-002.json")
        p["owners"] = [{"name": "BigTel Aktiengesellschaft", "stake_pct": 100, "type": "corporate", "country": "DE",
                        "source": "register", "as_of": "2026-05",
                        "ultimate_owner": {"name": "BigTel AG", "type": "listed free float",
                                           "website": "https://s-001.example", "source": "AR"}}]
        p["ownership_type"] = "corporate"
        write_json(run / "03_profiles" / "s-002.json", p)
        self.assertTrue(g(run, "3").ok())
        st = state.load(run); state.open_stage(run, st, "4"); state.save(run, st)
        r = route.build(run)
        row = next(x for x in r["strategic"] if x["id"] == "s-002")
        self.assertEqual((row["route"], row["parent"]), ("group_duplicate", "s-001"))
        self.assertNotIn("s-002", [c["id"] for c in route.debate_scope(run)["strategic"]])


class AuditAndLock(unittest.TestCase):
    """Findings 5/25/30/35."""

    def setUp(self):
        self.g = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.g.__exit__()

    def test_audit_coverage_and_malformed(self):
        run = F.build("6")
        st = state.load(run); state.open_stage(run, st, "7"); state.save(run, st)
        idx = audit.prepare(run)
        write_text(idx[0]["outputs"][0], "{not json")
        iss, _ = gates.GATES["7"](run)
        msgs = " ".join(i.message for i in iss.errors)
        self.assertIn("unreadable", msgs)
        self.assertIn("no finding for this claim", msgs)

    def test_lock_follows_state(self):
        run = F.build("8")
        self.assertTrue(excel.lock_ok(run)[0])
        state.reopen(run, "7", "re-audit")
        self.assertFalse(excel.lock_ok(run)[0])


if __name__ == "__main__":
    unittest.main()
