import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests import fixture as F
from buyerlist import excel, state
from buyerlist.common import PYTHON, ROOT

HOOKS = ROOT / ".claude" / "hooks"


def hook(name, payload):
    r = subprocess.run([str(PYTHON), str(HOOKS / name)], input=json.dumps(payload), capture_output=True,
                       text=True, cwd=str(ROOT), timeout=60)
    out = r.stdout.strip()
    return json.loads(out) if out else {}


def denied(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class Guard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = F.ActiveGuard().__enter__()
        cls.rd = F.build("3")

    @classmethod
    def tearDownClass(cls):
        cls.guard.__exit__()

    def test_state_json(self):
        p = str(self.rd / "state.json")
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Write", "tool_input": {"file_path": p}})))
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Bash",
                                                           "tool_input": {"command": f"echo x > '{p}'"}})))
        self.assertFalse(denied(hook("guard_artifacts.py", {"tool_name": "Bash",
                                                            "tool_input": {"command": f"cat '{p}'"}})))

    def test_passed_outputs_locked(self):
        prof = str(self.rd / "03_profiles" / "s-001.json")      # stage 3 passed
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Edit", "tool_input": {"file_path": prof}})))
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Bash",
                                                           "tool_input": {"command": f"rm '{prof}'"}})))
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Bash", "tool_input": {
            "command": f"python3 -c \"open('{prof}','w').write('x')\""}})))
        # copying it somewhere else is fine
        self.assertFalse(denied(hook("guard_artifacts.py", {"tool_name": "Bash",
                                                            "tool_input": {"command": f"cp '{prof}' /tmp/x.json"}})))
        # unlocked file in an open stage
        other = str(self.rd / "05_debate" / "strategic" / "b01" / "parts" / "advocate-g1.md")   # stage 5a open
        self.assertFalse(denied(hook("guard_artifacts.py", {"tool_name": "Write", "tool_input": {"file_path": other}})))
        cli_only = str(self.rd / "04_route.json")                                  # CLI-only, any time
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Write", "tool_input": {"file_path": cli_only}})))

    def test_cli_allowed(self):
        cmd = ".venv/bin/python -m buyerlist gate 3"
        self.assertFalse(denied(hook("guard_artifacts.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})))

    def test_xlsx_protected(self):
        p = str(ROOT / "output" / "20261019_Longlist anything.xlsx")
        self.assertTrue(denied(hook("guard_artifacts.py", {"tool_name": "Write", "tool_input": {"file_path": p}})))

    def test_workflow_stage_order(self):
        args = {"stage": "5b", "step": "5b", "run": str(self.rd), "groups": []}
        res = hook("require_stage_order.py", {"tool_name": "Workflow", "tool_input": {
            "scriptPath": str(ROOT / ".claude" / "workflows" / "bl-tasks.js"), "args": args}})
        self.assertTrue(denied(res))
        self.assertIn("stage 4", res["hookSpecificOutput"]["permissionDecisionReason"])
        args = {"stage": "4", "step": "4", "run": str(self.rd), "groups": [{"agent": "x", "ids": ["nope"]}]}
        res = hook("require_stage_order.py", {"tool_name": "Workflow", "tool_input": {
            "scriptPath": str(ROOT / ".claude" / "workflows" / "bl-tasks.js"), "args": args}})
        self.assertTrue(denied(res))                                   # no task file
        args["groups"] = []
        res = hook("require_stage_order.py", {"tool_name": "Workflow", "tool_input": {
            "scriptPath": str(ROOT / ".claude" / "workflows" / "bl-tasks.js"), "args": args}})
        self.assertFalse(denied(res))
        res = hook("require_stage_order.py", {"tool_name": "Workflow", "tool_input": {
            "script": "export const meta = { name: 'other', description: 'x' }"}})
        self.assertFalse(denied(res))

    def test_agent_roles(self):
        self.assertTrue(denied(hook("require_stage_order.py", {"tool_name": "Agent",
                                                               "tool_input": {"subagent_type": "bl-judge"}})))
        self.assertFalse(denied(hook("require_stage_order.py", {"tool_name": "Agent",
                                                                "tool_input": {"subagent_type": "bl-profiler-financial"}})))
        self.assertFalse(denied(hook("require_stage_order.py", {"tool_name": "Agent",
                                                                "tool_input": {"subagent_type": "Explore"}})))


class StopAndDelivery(unittest.TestCase):
    def setUp(self):
        self.guard = F.ActiveGuard().__enter__()

    def tearDown(self):
        self.guard.__exit__()

    def transcript(self, text, ts, running=False, earlier=None, touched=True):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write(json.dumps({"type": "user", "timestamp": ts, "message": {"content": "build the list"}}) + "\n")
        if touched:
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [
                {"type": "tool_use", "name": "Bash", "id": "t1",
                 "input": {"command": ".venv/bin/python -m buyerlist gate 3"}}]}}) + "\n")
        if earlier:
            f.write(json.dumps({"type": "assistant", "timestamp": ts,
                                "message": {"content": [{"type": "text", "text": earlier}]}}) + "\n")
        if running:
            f.write(json.dumps({"type": "user", "timestamp": ts, "message": {"content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "Workflow launched in background. Task ID: wabc123"}]}}) + "\n")
        f.write(json.dumps({"type": "assistant", "timestamp": ts,
                            "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        f.close()
        return f.name

    def test_stop_mid_run(self):
        run = F.build("3")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60)) + "Z"
        res = hook("require_pipeline_complete.py", {"transcript_path": self.transcript("Profiles done.", ts)})
        self.assertEqual(res.get("decision"), "block")
        self.assertIn("stage 4", res["reason"])
        res = hook("require_pipeline_complete.py",
                   {"transcript_path": self.transcript("WAITING: profiles workflow running", ts, running=True)})
        self.assertEqual(res, {})
        # WAITING without a running workflow is not an exit
        res = hook("require_pipeline_complete.py",
                   {"transcript_path": self.transcript("WAITING: nothing", ts)})
        self.assertEqual(res.get("decision"), "block")
        # an earlier WAITING in the turn does not excuse the final message
        res = hook("require_pipeline_complete.py",
                   {"transcript_path": self.transcript("All good.", ts, running=True, earlier="WAITING: x")})
        self.assertEqual(res.get("decision"), "block")
        # a turn that did not touch the run is not blocked
        future = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600)) + "Z"
        res = hook("require_pipeline_complete.py", {"transcript_path": self.transcript("hello", future, touched=False)})
        self.assertEqual(res, {})

    def test_complete_run_and_delivery(self):
        run = F.build("8")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 600)) + "Z"
        res = hook("require_pipeline_complete.py", {"transcript_path": self.transcript("Done.", ts)})
        self.assertEqual(res.get("decision"), "block")
        self.assertIn("Validation: PASS", res["reason"])
        res = hook("require_pipeline_complete.py",
                   {"transcript_path": self.transcript("Validation: PASS - 0 errors, 0 warnings", ts)})
        self.assertEqual(res, {})
        x = str(excel.xlsx_path(run))
        self.assertFalse(denied(hook("require_stage_order.py", {"tool_name": "SendUserFile",
                                                                "tool_input": {"files": [x]}})))
        from openpyxl import load_workbook
        import os
        os.chmod(x, 0o644)
        load_workbook(x).save(x)                                       # file changed after validation
        self.assertTrue(denied(hook("require_stage_order.py", {"tool_name": "SendUserFile",
                                                               "tool_input": {"files": [x]}})))


if __name__ == "__main__":
    unittest.main()
