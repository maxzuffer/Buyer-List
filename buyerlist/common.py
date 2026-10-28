"""Shared helpers: paths, JSON I/O, hashing, issue collection."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
OUTPUT = ROOT / "output"
AGENTS = ROOT / ".claude" / "agents"
ACTIVE = RUNS / ".active"
PYTHON = ROOT / ".venv" / "bin" / "python"


class StageError(Exception):
    """Raised when a command is run out of order or on tampered inputs."""


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def read_text(path: Path | str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_text(path: Path | str, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path: Path | str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path | str, obj: Any) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path | str) -> str:
    """Path relative to the project root, with forward slashes."""
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return s or "project"


def today() -> _dt.date:
    return _dt.date.today()


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def run_dir(project: Optional[str] = None) -> Path:
    """The run folder of `project` (name or slug), else the active run."""
    if project:
        d = RUNS / slugify(project)
    else:
        if not ACTIVE.exists():
            raise StageError("no active run - start one with: python -m buyerlist init ...")
        d = RUNS / read_text(ACTIVE).strip()
    if not (d / "state.json").exists():
        raise StageError(f"run folder {rel(d)} has no state.json - run init first")
    return d


def set_active(slug: str) -> None:
    write_text(ACTIVE, slug + "\n")


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
@dataclass
class Issue:
    severity: str          # ERROR or WARNING
    subject: str
    field: str
    message: str

    def line(self) -> str:
        return f"{self.severity}: {self.subject} / {self.field}: {self.message}"


@dataclass
class Issues:
    items: list = field(default_factory=list)

    def error(self, subject: str, fld: str, message: str) -> None:
        self.items.append(Issue("ERROR", subject, fld, message))

    def warn(self, subject: str, fld: str, message: str) -> None:
        self.items.append(Issue("WARNING", subject, fld, message))

    def extend(self, other: "Issues") -> None:
        self.items.extend(other.items)

    @property
    def errors(self) -> list:
        return [i for i in self.items if i.severity == "ERROR"]

    @property
    def warnings(self) -> list:
        return [i for i in self.items if i.severity == "WARNING"]

    def ok(self) -> bool:
        return not self.errors

    def render(self, limit: int = 200) -> str:
        lines = [i.line() for i in self.errors[:limit]]
        if len(self.errors) > limit:
            lines.append(f"... {len(self.errors) - limit} more errors")
        lines += [i.line() for i in self.warnings[:limit]]
        if len(self.warnings) > limit:
            lines.append(f"... {len(self.warnings) - limit} more warnings")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"{len(self.errors)} errors, {len(self.warnings)} warnings"


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------
def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)


def is_url(v: Any) -> bool:
    return bool(URL_RE.match(text(v)))
