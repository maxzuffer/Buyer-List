"""PreToolUse hook (Write|Edit|MultiEdit|NotebookEdit|Bash): protect run artefacts.

Policy
- CLI-only files can never be written by hand (Write/Edit) - only by
  `.venv/bin/python -m buyerlist ...`: run state, locks, mandate, universe, ids,
  route, batches, check reports, retry lists, anonymised briefs, manifests,
  scores, list data, audit sample/resolutions, task files and the Excel.
- Agent-written files (client sheet, raw sourcing files, profiles, briefs,
  Controller reports, verdicts, audit results) are writable while their stage
  is open, and locked once its gate has passed (reopen the stage to change them).
- Bash: a plain CLI call (exactly `.venv/bin/python -m buyerlist ...`, one
  command, no redirection - quoted --note/--reason text may contain anything)
  is allowed. Any other command that touches runs/ or output/ (by path, by a
  run-file name, or because the working directory is inside them) must be
  read-only: cat/head/grep/ls/... with no redirection, no find -delete/-exec,
  copies only OUT of runs/; python only when the code has no write operation;
  heredoc bodies are checked as the code of their reader.
This is a strong guard, not a sandbox: every gate also recomputes derived files
and checks hashes, and passed outputs are read-only on disk.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooklib import ROOT, all_runs, deny, read_input  # noqa: E402

READ_ONLY = {"cat", "head", "tail", "less", "more", "ls", "wc", "grep", "egrep", "fgrep", "rg", "shasum",
             "sha256sum", "md5", "stat", "file", "jq", "diff", "cmp", "echo", "printf", "pwd", "true", "test",
             "[", "sort", "uniq", "cut", "column", "tr", "nl", "find", "du", "basename", "dirname",
             "realpath", "zipinfo", "xxd", "od", "date"}
INTERPRETERS = {"python", "python3", "python3.9", "python3.11", "python3.12"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
OPERATORS = {";", "&&", "||", "|", "&", "|&", "\n"}
HARMLESS_TARGETS = {"/dev/null", "/dev/stderr", "/dev/stdout", "&1", "&2", "1", "2"}
TOUCH_RE = re.compile(r"runs/|output/|state\.json|08_final\.lock|03_profiles|05_debate|06_list|07_audit|"
                      r"_Longlist[^/]*\.xlsx", re.I)
PY_WRITE_RE = re.compile(r"open\([^)]*['\"][wax+]|\.write|\bdump\(|unlink|\bremove\(|rmtree|\brename\(|"
                         r"os\.replace|\.replace\(\s*(?:Path|['\"]?/)|chmod|chflags|\bsystem\(|subprocess|shutil|"
                         r"\.save\(|truncate|makedirs|mkdir|copyfile|\bexec\(|\beval\(|__import__|importlib|"
                         r"\.touch\(|symlink|\.link_to|popen|spawn", re.I)
FIND_WRITE = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}

CLI_ONLY = [re.compile(p) for p in (
    r"^runs/\.active$", r"^runs/[^/]+/state\.json$", r"^runs/[^/]+/[^/]*\.lock$", r"^runs/[^/]+/00_mandate\.json$",
    r"^runs/[^/]+/02_universe/(universe|ids|feedback_used)\.json$", r"^runs/[^/]+/02_universe/seen_names\.txt$",
    r"^runs/[^/]+/02_universe/raw/AN-[^/]*$", r"^runs/[^/]+/feedback/.*$",
    r"^runs/[^/]+/04_route(_ids)?\.json$", r"^runs/[^/]+/05_debate/[^/]+/batches\.json$",
    r"^runs/[^/]+/05_debate/[^/]+/b\d+/(check_[^/]*|[^/]*_raw\.md|retry\.json|\.sides\.json|Brief [AB]\.md|"
    r"final_[^/]*\.json|[^/]*\.inputs\.json)$",
    r"^runs/[^/]+/05_debate/[^/]+/b\d+/parts/round1/.*$", r"^runs/[^/]+/05_debate/scores_[^/]*\.json$",
    r"^runs/[^/]+/06_[^/]*$", r"^runs/[^/]+/07_audit/(sample|resolutions|summary)\.json$",
    r"^runs/[^/]+/07_audit/[^/]*\.inputs\.json$", r"^runs/[^/]+/tasks/.*$", r"^output/[^/]+\.xlsx$")]


def canon(p: str, cwd: Path) -> Path:
    q = Path(os.path.expanduser(p))
    if not q.is_absolute():
        q = cwd / q
    return Path(os.path.normpath(str(q)))


def relp(p: Path):
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        try:
            return p.relative_to(ROOT).as_posix()
        except ValueError:
            return None


def under_runs(rel) -> bool:
    return bool(rel) and (rel in ("runs", "output") or rel.startswith("runs/") or rel.startswith("output/"))


def locked_outputs() -> dict:
    out = {}
    for run, st in all_runs():
        for k, e in (st.get("stages") or {}).items():
            if e.get("status") == "pass":
                for p in (e.get("outputs") or {}):
                    out[p] = f"recorded output of stage {k}, whose gate has passed - reopen it first: " \
                             f"python -m buyerlist reopen {k} --reason \"...\""
    return out


def path_reason(r, locked: dict):
    if r is None:
        return None
    for rx in CLI_ONLY:
        if rx.match(r):
            return f"{r}: written only by the buyerlist CLI"
    if r in locked:
        return f"{r}: {locked[r]}"
    return None


def check_write(target: str, cwd: Path) -> None:
    if not target:
        return
    reason = path_reason(relp(canon(target, cwd)), locked_outputs())
    if reason:
        deny(f"Blocked by guard_artifacts: {reason}")


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------
def strip_heredocs(cmd: str) -> tuple:
    """(command lines without heredoc bodies, [(line with '<<', body)])"""
    lines, out, bodies, i = cmd.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        for _, term in re.findall(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line):
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != term:
                body.append(lines[i])
                i += 1
            bodies.append((line, "\n".join(body)))
        i += 1
    return out, bodies


def tokens(line: str) -> list:
    lx = shlex.shlex(line, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    try:
        return list(lx)
    except ValueError:
        return line.split()


def segments(line: str) -> list:
    segs, cur = [], []
    for t in tokens(line):
        if t in OPERATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def is_cli(seg: list) -> bool:
    return (len(seg) >= 3 and seg[0].endswith(".venv/bin/python") and seg[1] == "-m" and seg[2] == "buyerlist")


def redirect_targets(seg: list) -> list:
    out = []
    for i, t in enumerate(seg):
        if re.fullmatch(r"\d*(>|>>|&>|>&|>\|)", t):
            if i + 1 < len(seg):
                out.append(seg[i + 1])
        elif re.fullmatch(r"\d*>>?\S+", t):
            out.append(re.sub(r"^\d*>>?", "", t))
    return out


def touches(tok: str, cwd: Path) -> bool:
    if TOUCH_RE.search(tok):
        return True
    if not tok or tok.startswith("-"):
        return False
    return under_runs(relp(canon(tok, cwd)))


def check_bash(cmd: str, cwd: Path) -> None:
    lines, bodies = strip_heredocs(cmd)
    all_segs = [seg for line in lines for seg in segments(line)]
    # 1. a plain CLI call
    if len(all_segs) == 1 and not bodies and is_cli(all_segs[0]) and not redirect_targets(all_segs[0]) \
            and len([l for l in lines if l.strip()]) == 1:
        return
    in_runs = under_runs(relp(canon(".", cwd)))
    # 2. heredoc bodies are code for their reader
    for line, body in bodies:
        before = line.split("<<")[0]
        segs = segments(before)
        reader = segs[-1] if segs else []
        reader = [t for t in reader if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t)]
        prog = Path(reader[0]).name if reader else ""
        if TOUCH_RE.search(body) or TOUCH_RE.search(line) or in_runs:
            if prog in INTERPRETERS and PY_WRITE_RE.search(body):
                deny("Blocked by guard_artifacts: a python script that touches run files must not write - use "
                     "the buyerlist CLI (python -m buyerlist ...) to change run files")
            if prog in SHELLS:
                deny("Blocked by guard_artifacts: shell heredoc in a command that touches run files")
    # 3. every command segment, in order (a cd changes the directory for what follows)
    for line in lines:
        for seg in segments(line):
            seg = [t for t in seg if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t)] or seg
            if not seg:
                continue
            prog = Path(seg[0]).name
            if prog == "cd":
                if len(seg) > 1:
                    cwd = canon(seg[1], cwd)
                    in_runs = under_runs(relp(cwd))
                continue
            seg_touch = in_runs or any(touches(t, cwd) for t in seg[1:])
            if not seg_touch:
                continue
            reds = [t for t in redirect_targets(seg) if t not in HARMLESS_TARGETS]
            if reds:
                deny(f"Blocked by guard_artifacts: output redirection in a command that touches run files "
                     f"({' '.join(seg)[:80]})")
            if is_cli(seg):
                continue
            if prog in INTERPRETERS:
                if "-c" in seg[1:3]:
                    code = " ".join(seg[seg.index("-c") + 1:])
                    if PY_WRITE_RE.search(code):
                        deny("Blocked by guard_artifacts: a python one-liner that touches run files must not write")
                    continue
                if seg[1:2] == ["-"] and bodies:
                    continue                                      # heredoc body checked above
                deny("Blocked by guard_artifacts: running a python script on run files - use the buyerlist CLI")
            if prog in SHELLS:
                deny("Blocked by guard_artifacts: nested shell on run files")
            if prog == "find" and FIND_WRITE & set(seg):
                deny("Blocked by guard_artifacts: find with -delete/-exec on run files")
            if prog in ("cp", "ditto") and len(seg) >= 3 and not any(t.startswith("-t") or t == "--target-directory"
                                                                     for t in seg):
                if not under_runs(relp(canon(seg[-1], cwd))):
                    continue                                      # copying run files OUT is a read
                deny("Blocked by guard_artifacts: copying into runs/ or output/ - use the buyerlist CLI")
            if prog not in READ_ONLY:
                deny(f"Blocked by guard_artifacts: '{prog}' on run or output files - only read-only commands are "
                     "allowed there; change run files only through python -m buyerlist ...")


def main() -> None:
    data = read_input()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or ROOT)
    try:
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            check_write(ti.get("file_path") or ti.get("notebook_path") or "", cwd)
        elif tool == "Bash":
            check_bash(ti.get("command") or "", cwd)
    except SystemExit:
        raise
    except Exception as exc:  # fail open, but say so
        print(f"guard_artifacts internal error (allowed): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
