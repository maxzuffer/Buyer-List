"""Regression tests for the second review (2026-10-19)."""
import json
import subprocess
import unittest
from datetime import date

from tests import fixture as F
from buyerlist import audit, gates, normalize, profiles, route, state
from buyerlist.common import PYTHON, ROOT, read_json, write_json

AS_OF = date(2026, 10, 19)


def guard(payload):
    r = subprocess.run([str(PYTHON), str(ROOT / ".claude" / "hooks" / "guard_artifacts.py")],
                       input=json.dumps(payload), capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    out = r.stdout.strip()
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


class Normalisation(unittest.TestCase):
    def test_vocabulary_and_years(self):
        p = normalize.profile({"ownership_type": "State owned", "hq_country": "Austria",
                               "owners": [{"name": "Republic", "type": "state-owned", "stake_pct": "51,0 %"},
                                          {"name": "Wien", "type": "Municipal", "stake_pct": "majority"},
                                          {"name": "Float", "type": "listed", "stake_pct": None}],
                               "revenue": {"status": "verified", "value": "1,234.5", "fy": "2024/25 (FYE 31 Mar 2025)"},
                               "employees": {"status": "VERIFIED", "value": 300, "fy": "undated"}})
        self.assertEqual([o["type"] for o in p["owners"]], ["state", "municipality", "listed free float"])
        self.assertEqual(p["owners"][0]["stake_pct"], 51.0)
        self.assertEqual(p["owners"][1]["stake_pct"], 50.1)
        self.assertTrue(p["owners"][1]["controlling"])
        self.assertEqual(p["ownership_type"], "state-owned")
        self.assertEqual(p["hq_country"], "AT")
        self.assertEqual((p["revenue"]["fy"], p["revenue"]["fy_end_month"], p["revenue"]["status"]), ("2024/25", 3, "VERIFIED"))
        self.assertEqual(p["revenue"]["value"], 1234.5)
        self.assertEqual(p["employees"]["fy"], "not stated")

    def test_fund_via_non_sponsor_ultimate_owner_still_counts(self):
        p = {"owners": [{"name": "Fund IV LP", "type": "PE", "stake_pct": 80,
                         "ultimate_owner": {"name": "Some Family", "type": "family"}}]}
        self.assertTrue(profiles.pe_owned_calc(p))

    def test_undisclosed_owners_is_a_warning(self):
        cand = {"id": "s-001", "name": "X", "type": "strategic"}
        base = F.s_profile("s-001", "X", {"status": "VERIFIED", "value": 50, "fy": "2025", "scope": "group",
                                          "source": "AR"}, [], False)
        base.update(listed=False, owners=[], ownership_type="other", ownership_note="employee-owned, no register entry")
        iss = profiles.validate_strategic(base, cand, AS_OF)
        self.assertTrue(iss.ok(), iss.render())
        self.assertTrue(profiles.ownership_unverified(base))


class GuardHeredocsAndCwd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = F.ActiveGuard().__enter__()
        cls.rd = F.build("3")

    @classmethod
    def tearDownClass(cls):
        cls.g.__exit__()

    def bash(self, cmd, cwd=None):
        return guard({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd or ROOT)})

    def test_heredoc_cases(self):
        rel = f"runs/{F.SLUG}"
        self.assertTrue(self.bash(f"cat > {rel}/03_profiles/s-001.json <<'EOF'\n{{}}\nEOF"))
        self.assertTrue(self.bash(f"cat <<'EOF'\nhello\nEOF\necho x > {rel}/state.json"))
        self.assertTrue(self.bash(f"python3 - <<'EOF'\nopen('{rel}/state.json','w').write('x')\nEOF"))
        self.assertFalse(self.bash(f"python3 - <<'EOF'\nprint(open('{rel}/state.json').read())\nEOF"))

    def test_cwd_inside_runs(self):
        self.assertTrue(self.bash("echo x > state.json", cwd=self.rd))
        self.assertTrue(self.bash("sed -i '' s/a/b/ 03_profiles/s-001.json", cwd=self.rd))
        self.assertFalse(self.bash("cat state.json", cwd=self.rd))
        self.assertTrue(guard({"tool_name": "Write", "tool_input": {"file_path": "state.json"}, "cwd": str(self.rd)}))

    def test_cli_with_quoted_operators(self):
        self.assertFalse(self.bash('.venv/bin/python -m buyerlist reopen 3 --reason "audit: rev > 30; fix"'))
        self.assertTrue(self.bash('.venv/bin/python -m buyerlist status; rm -rf runs'))


class RouteAndAudit(unittest.TestCase):
    def setUp(self):
        self.g = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.g.__exit__()

    def test_group_duplicate_needs_listed_parent(self):
        run = F.build("3")
        state.reopen(run, "3", "SmallCo owns DataCo")
        p = read_json(run / "03_profiles" / "s-002.json")
        p["owners"] = [{"name": "SmallCo GmbH", "stake_pct": 100, "type": "corporate", "source": "register",
                        "as_of": "2026-05", "ultimate_owner": {"name": "SmallCo GmbH", "type": "founder"}}]
        p["ownership_type"] = "corporate"
        write_json(run / "03_profiles" / "s-002.json", p)
        st = state.load(run); state.open_stage(run, st, "4"); state.save(run, st)
        r = route.build(run)
        row = next(x for x in r["strategic"] if x["id"] == "s-002")
        self.assertEqual(row["route"], "strategic")         # SmallCo (parent) is revenue-excluded

    def test_resolution_bound_to_finding(self):
        run = F.build("7")
        self.assertTrue(gates.run_gate(run, "7")[0].ok())
        # a second audit file says 'wrong' again for the same claim -> old resolution does not cover it
        state.reopen(run, "7", "second opinion")
        sample = {x["id"]: x for x in audit.sample(run)}
        out = run / "07_audit" / "audit-99.json"
        write_json(run / "07_audit" / "audit-99.inputs.json", {"ids": {"s-001": audit.claims_hash(sample["s-001"])}})
        write_json(out, {"findings": [{"id": "s-001", "claim": 1, "verdict": "wrong", "detail": "rev 300",
                                       "source": "https://y"}]})
        iss, _ = gates.GATES["7"](run)
        self.assertTrue(any("WRONG" in i.message for i in iss.errors))


if __name__ == "__main__":
    unittest.main()
