import unittest
from datetime import date

from buyerlist import figures, profiles, scoring
from buyerlist import sources as S
from buyerlist.common import Issues
from buyerlist.universe import name_key, registrable_domain

AS_OF = date(2026, 10, 19)


class Scoring(unittest.TestCase):
    def test_weighted_half_up(self):
        w = scoring.STRATEGIC_WEIGHTS
        # 0.5*4 + 0.2*3 + 0.2*4 + 0.05*3 + 0.05*4 = 3.75 -> 3.8
        self.assertEqual(scoring.weighted({"product": 4, "customer": 3, "geography": 4, "value_chain": 3,
                                           "synergy": 4}, w), 3.8)
        fw = scoring.FINANCIAL_WEIGHTS
        # 0.35*4 + 0.25*3 + 0.30*4 + 0.10*2 = 3.55 -> 3.6
        self.assertEqual(scoring.weighted({"ticket": 4, "geography": 3, "criteria": 4, "portfolio": 2}, fw), 3.6)

    def test_rank_floors(self):
        self.assertEqual(scoring.expected_rank(3.8), 1)
        self.assertEqual(scoring.expected_rank(3.75), 1)   # rounds to 3.8
        self.assertEqual(scoring.expected_rank(3.7), 2)
        self.assertEqual(scoring.expected_rank(2.95), 2)
        self.assertEqual(scoring.expected_rank(2.0), 3)
        self.assertIsNone(scoring.expected_rank(1.94))

    def test_partner_rule_and_best_rank(self):
        self.assertTrue(scoring.different_product_and_region({"product": 2, "geography": 2}))
        self.assertFalse(scoring.different_product_and_region({"product": 3, "geography": 1}))
        self.assertEqual(scoring.best_rank(None, 2, 3), 2)
        self.assertIsNone(scoring.best_rank(None, None))


class Figures(unittest.TestCase):
    def check(self, fig, **kw):
        iss = Issues()
        figures.check_figure(iss, "x", "revenue", fig, AS_OF, **kw)
        return iss

    def test_verified_needs_source(self):
        iss = self.check({"status": "VERIFIED", "value": 50, "fy": "2025", "scope": "group"})
        self.assertTrue(any("without a source" in i.message for i in iss.errors))

    def test_directory_cannot_verify(self):
        iss = self.check({"status": "VERIFIED", "value": 50, "fy": "2025", "scope": "group",
                          "source": "North Data company page"})
        self.assertTrue(any("directory" in i.message for i in iss.errors))

    def test_outdated_and_future(self):
        iss = self.check({"status": "VERIFIED", "value": 50, "fy": "2021", "scope": "group", "source": "AR"})
        self.assertTrue(iss.ok() and any("years old" in i.message for i in iss.warnings))
        iss = self.check({"status": "VERIFIED", "value": 50, "fy": "2021", "scope": "group", "source": "AR",
                          "flagged_outdated": True})
        self.assertTrue(iss.ok())
        iss = self.check({"status": "VERIFIED", "value": 50, "fy": "2027", "scope": "group", "source": "AR"})
        self.assertFalse(iss.ok())

    def test_na_and_estimate(self):
        self.assertTrue(self.check({"status": "n/a", "reason": "no P&L filed"}).ok())
        self.assertFalse(self.check({"status": "ESTIMATE", "value": [40, 60], "fy": "2025", "scope": "group"}).ok())
        self.assertEqual(figures.display({"status": "ESTIMATE", "value": [40, 60]}), "c. 40-60 (est.)")
        self.assertEqual(figures.display({"status": "VERIFIED", "value": 123.6}), 124)
        self.assertEqual(figures.display({"status": "n/a"}), "n.a.")


class Profiles(unittest.TestCase):
    def test_revenue_gate_strict(self):
        g = lambda fig: profiles.revenue_gate({"revenue": fig}, 30)[0]
        self.assertEqual(g({"status": "VERIFIED", "value": 31}), "PASS")
        self.assertEqual(g({"status": "VERIFIED", "value": 30}), "FAIL")
        self.assertEqual(g({"status": "ESTIMATE", "value": [35, 50]}), "PASS")
        self.assertEqual(g({"status": "ESTIMATE", "value": [20, 40]}), "UNVERIFIABLE")
        self.assertEqual(g({"status": "ESTIMATE", "value": [10, 30]}), "FAIL")
        self.assertEqual(g({"status": "n/a", "reason": "x"}), "UNVERIFIABLE")

    def test_pe_owned(self):
        p = lambda owners: profiles.pe_owned_calc({"owners": owners})
        self.assertTrue(p([{"type": "PE", "stake_pct": 30}, {"type": "infrastructure fund", "stake_pct": 25}]))
        self.assertTrue(p([{"type": "PE", "stake_pct": 40, "controlling": True}]))
        self.assertFalse(p([{"type": "family office", "stake_pct": 60}]))
        self.assertFalse(p([{"type": "PE", "stake_pct": 20}, {"type": "family", "stake_pct": 80}]))

    def test_ma_rating(self):
        r = lambda ma: profiles.ma_rating({"ma_activity": ma}, AS_OF)
        self.assertEqual(r({"deals": [{"year": 2024}, {"year": 2025}]}), "High")
        self.assertEqual(r({"deals": [{"year": 2024}], "statements": [{"date": "2025-03"}]}), "High")
        self.assertEqual(r({"deals": [{"year": 2024}]}), "Medium")
        self.assertEqual(r({"deals": [{"year": 2015}]}), "Low")
        self.assertEqual(r({}), "Not available")

    def test_pe_flag_must_match(self):
        cand = {"id": "s-001", "name": "X", "type": "strategic"}
        prof = {"id": "s-001", "type": "strategic", "name": "X", "website": "https://x.com", "hq_country": "DE",
                "bereich": "DC", "description": "X runs data centres. It serves enterprises in Germany.",
                "revenue": {"status": "VERIFIED", "value": 50, "fy": "2025", "scope": "group", "source": "AR"},
                "ebitda": {"status": "n/a", "reason": "not published"},
                "employees": {"status": "n/a", "reason": "not published"},
                "ownership_type": "family", "listed": False, "pe_owned": False,
                "owners": [{"name": "Fund", "stake_pct": 60, "type": "PE", "website": "https://f.com",
                            "source": "register", "as_of": "2026-05"}],
                "ma_activity": {"deals": [], "statements": [], "searched": "news"}}
        iss = profiles.validate_strategic(prof, cand, AS_OF)
        self.assertTrue(any(i.field == "pe_owned" for i in iss.errors))


BRIEF = """# Advocate brief

## Equinix #s-014
Searches: "equinix vienna"
1. Product/service: Equinix runs colocation data centres like the client [S1, F2]
2. Customer: no supporting evidence
3. Geography: Equinix operates a Vienna data centre [S2]

## Digital Realty #s-015
Searches: "digital realty vienna"
1. Product/service: colocation [S3]
2. Customer: serves enterprises [S9]
3. Pricing: cheap [S3]

## Sources
- [S1] https://a.example/1 | "Equinix colocation data centers" | company website
- [S2] https://a.example/2 | "Vienna VI1" | company website
- [S3] https://a.example/3 | "colocation services" | company website
"""


class Sources(unittest.TestCase):
    def test_parse_and_check(self):
        b = S.parse_brief(BRIEF, "strategic")
        self.assertEqual([c["key"] for c in b["companies"]], ["s-014", "s-015"])
        S.check(b, "strategic", {"F2"}, expected_keys=["s-014", "s-015", "s-016"], fetch=False)
        self.assertIn("companies missing from the brief: s-016", b["structural"])
        errs = " ".join(b["errors"])
        self.assertIn("unknown IDs S9", errs)
        self.assertIn("dimension 'Pricing' not allowed", errs)
        self.assertNotIn("Equinix #s-014 #2", errs)   # no-evidence point needs no source

    def test_financial_dims(self):
        fb = BRIEF.replace("Product/service", "Ticket size").replace("Customer", "Investment criteria")
        b = S.parse_brief(fb, "financial")
        self.assertEqual(b["companies"][0]["arguments"][0]["dimension"], "Ticket size")
        self.assertEqual(b["companies"][0]["arguments"][1]["dimension"], "Investment criteria")

    def test_merge_renumbers(self):
        b1 = S.parse_brief(BRIEF, "strategic")
        b2 = S.parse_brief(BRIEF.replace("#s-014", "#s-020").replace("#s-015", "#s-021"), "strategic")
        m = S.merge([b1, b2])
        self.assertEqual(sorted(m["sources"], key=lambda s: int(s[1:])), [f"S{i}" for i in range(1, 7)])
        self.assertEqual(m["companies"][2]["arguments"][0]["sources"], ["S4"])

    def test_decisions_retry_clean(self):
        b = S.parse_brief(BRIEF, "strategic")
        S.check(b, "strategic", {"F2"}, fetch=False)
        rep = ("- Advocate | Equinix #s-014 | 1 | KEEP | ok\n- Advocate | Equinix #s-014 | 2 | KEEP | ok\n"
               "- Advocate | Equinix #s-014 | 3 | DROP | quote does not say Vienna\n"
               "- Advocate | Digital Realty #s-015 | 1 | DROP | x\n- Advocate | Digital Realty #s-015 | 2 | DROP | x\n"
               "- Advocate | Digital Realty #s-015 | 3 | DROP | x\n")
        dec = S.parse_decisions(rep)
        self.assertEqual(S.missing_decisions(b, "advocate", dec), [])
        hits = S.retry_candidates(b, "advocate", dec, "strategic")
        self.assertEqual([h["key"] for h in hits], ["s-015"])
        md, kept, dropped, mapping = S.clean(b, "advocate", dec, "A")
        self.assertEqual(kept, 2)
        self.assertIn("[A1]", md)
        self.assertNotIn("Vienna VI1", md)
        self.assertEqual(S.kept_substantive(b, "advocate", dec), {"s-014": 1, "s-015": 0})

    def test_verdict(self):
        v = """## Equinix #s-014 – 3.9
- Product 4: same category [A1]
- Customer 4: enterprises [B1]
- Geography 4: Vienna [A2]
- Value chain 3: ops [A1]
- Synergy 2: no evidence in the briefs - default 2
- Calc: ...
- Role: horizontal - peer
- Deciding argument: Brief A - colocation [A1]
- Stands against it: none
- Confidence: Medium (company claims)
"""
        out = S.parse_verdict(v, "strategic")
        e = out["s-014"]
        self.assertEqual(e["scores"]["product"]["score"], 4)
        self.assertEqual(e["confidence"], "Medium")
        self.assertTrue(e["role"].startswith("horizontal"))
        probs = S.judge_check(out, "strategic", {"A1", "A2", "B1"})
        self.assertEqual(probs, [])
        probs = S.judge_check(out, "strategic", {"A1", "A2"})
        self.assertIn(("s-014", "customer", "no valid source cited"), probs)


class Universe(unittest.TestCase):
    def test_domain(self):
        self.assertEqual(registrable_domain("https://www.example.co.uk/x"), "example.co.uk")
        self.assertEqual(registrable_domain("http://www2.digitalrealty.com"), "digitalrealty.com")
        self.assertEqual(registrable_domain("example-isp.at"), "example-isp.at")

    def test_name_key(self):
        self.assertEqual(name_key("EQT AB"), name_key("EQT"))
        self.assertNotEqual(name_key("Deutsche Telekom Capital Partners"), name_key("Deutsche Telekom AG"))
        self.assertEqual(name_key("Bechtle AG"), "bechtle")
        self.assertNotEqual(name_key("Partners Group"), "")


if __name__ == "__main__":
    unittest.main()
