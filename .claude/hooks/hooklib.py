"""Shared helpers for the buyer-list hooks (standard library + the buyerlist package)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INJECTED_PREFIXES = ("Base directory for this skill", "Stop hook feedback", "<system-reminder>",
                     "<task-notification>", "<side-sessions-event>", "<ci-monitor-event>")


def read_input() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def deny(reason: str) -> None:
    """PreToolUse: refuse the tool call and tell Claude why."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def block_stop(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def active_run():
    """(run_dir, state) of the active run, or (None, None)."""
    act = ROOT / "runs" / ".active"
    if not act.exists():
        return None, None
    run = ROOT / "runs" / act.read_text(encoding="utf-8").strip()
    sp = run / "state.json"
    if not sp.exists():
        return None, None
    try:
        return run, json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return None, None


def all_runs():
    for sp in sorted((ROOT / "runs").glob("*/state.json")):
        try:
            yield sp.parent, json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Transcript helpers (same turn logic as the Business-Dev-2.0 Stop hooks)
# ---------------------------------------------------------------------------
def load_transcript(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def blocks(e: dict) -> list:
    c = e.get("message", {}).get("content", [])
    return c if isinstance(c, list) else []


def user_text(e: dict) -> str:
    c = e.get("message", {}).get("content", "")
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def is_real_user_prompt(e: dict) -> bool:
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    if any(b.get("type") == "tool_result" for b in blocks(e)):
        return False
    return not user_text(e).lstrip().startswith(INJECTED_PREFIXES)


def current_turn(entries: list) -> list:
    start = 0
    for i, e in enumerate(entries):
        if is_real_user_prompt(e):
            start = i
    return entries[start:]


def assistant_text(turn: list) -> str:
    return "".join(b.get("text", "") for e in turn if e.get("type") == "assistant"
                   for b in blocks(e) if b.get("type") == "text")
