"""Figure rules (VERIFIED / ESTIMATE / n/a), ported from Business-Dev-2.0
.claude/tools/validate_buyer_list.py (check_figure, fiscal-year and source rules).

FIGURE = {"status": "VERIFIED", "value": 103.5, "fy": "2025", "source": "...",
          "scope": "entity | group", "unit": "EUR m", "fy_end_month": 12,
          "original": {"value": 120, "currency": "USD"}, "fx": "USD->EUR 0.92 ECB 2025 avg",
          "flagged_outdated": false}
       | {"status": "ESTIMATE", "value": [45, 60], "fy": "2025", "method": "...", "scope": "group"}
       | {"status": "n/a", "reason": "..."}
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .common import Issues, is_num, text

OUTDATED_YEARS = 3
SPLIT_FY_DEFAULT_END_MONTH = 6
UNIT = "EUR m"
WEAK_SOURCES = ("wikipedia", "linkedin", "crunchbase", "owler", "growjo", "rocketreach",
                "glassdoor", "kununu", "chatgpt", "perplexity", "pitchbook.com/profiles")
DIRECTORY_SOURCES = ("zoominfo", "dun & bradstreet", "d&b", "dnb.com", "northdata", "north data",
                     "companyhouse.de", "emis", "cbinsights", "firmenwissen", "statista")
FY_UNKNOWN = ("not stated", "unknown")
FY_RE = re.compile(r"^(?:FY\s*)?(\d{4}|\d{2})(?:\s*[/\-]\s*(\d{4}|\d{2}))?$", re.I)


def round1(x: float) -> float:
    return float(Decimal(str(round(x, 9))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def bounds(value: Any) -> Optional[tuple]:
    if is_num(value):
        return float(value), float(value)
    if (isinstance(value, list) and len(value) == 2 and all(is_num(x) for x in value)
            and value[0] <= value[1]):
        return float(value[0]), float(value[1])
    return None


def _year(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def fy_end_date(fy: Any, end_month: Any = None) -> Optional[date]:
    if is_num(fy):
        fy = str(int(fy))
    m = FY_RE.match(text(fy))
    if not m:
        return None
    if m.group(2):
        start_y, end_y = _year(m.group(1)), _year(m.group(2))
        if end_y != start_y + 1:
            return None
        month = end_month or SPLIT_FY_DEFAULT_END_MONTH
    else:
        end_y = _year(m.group(1))
        month = end_month or 12
    if not (isinstance(month, int) and 1 <= month <= 12):
        return None
    return date(end_y, month, calendar.monthrange(end_y, month)[1])


def period_end(v: Any) -> Optional[date]:
    s = str(int(v)) if is_num(v) else text(v)
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2) or 12)
    try:
        if m.group(3):
            return date(y, mo, int(m.group(3)))
        return date(y, mo, calendar.monthrange(y, mo)[1])
    except ValueError:
        return None


def fy_label(fy: Any) -> str:
    s = str(fy).strip()
    return s if s.upper().startswith("FY") else f"FY{s}"


def status_of(fig: Any) -> str:
    return text(fig.get("status")).upper() if isinstance(fig, dict) else ""


def check_source_quality(issues: Issues, subject: str, fld: str, obj: dict, status: str = "") -> None:
    source = text(obj.get("source")).lower()
    weak = next((w for w in WEAK_SOURCES if w in source), None)
    if weak:
        issues.warn(subject, fld, f"weak source ({weak}) - prefer a register filing, annual report or the company")
    directory = next((d for d in DIRECTORY_SOURCES if d in source), None)
    if directory and status == "VERIFIED":
        issues.error(subject, fld, f"third-party directory ({directory}) can only support an ESTIMATE, not VERIFIED")


def check_fy(issues: Issues, subject: str, fld: str, fig: dict, status: str, as_of: date) -> None:
    fy = fig.get("fy")
    if fy in (None, ""):
        issues.error(subject, fld, "no fiscal year - every figure needs one")
        return
    if str(fy).strip().lower() in FY_UNKNOWN:
        if text(fig.get("fy_note")):
            issues.warn(subject, fld, f"fiscal year not stated in the source ({fig['fy_note']})")
        else:
            issues.error(subject, fld, "fiscal year unknown without an fy_note explaining the gap")
        return
    end = fy_end_date(fy, fig.get("fy_end_month"))
    if end is None:
        issues.error(subject, fld, f"fiscal year {fy!r} not readable (use 2024, FY2024 or 2025/26)")
        return
    if end > as_of:
        if status == "VERIFIED":
            clearly_future = fig.get("fy_end_month") is not None or end.year > as_of.year
            msg = (f"{fy_label(fy)} has not ended by {as_of} - an actual can't be VERIFIED yet; "
                   "label it ESTIMATE (budget or forecast)")
            (issues.error if clearly_future else issues.warn)(subject, fld, msg)
        return
    age = (as_of - end).days / 365.25
    if age > OUTDATED_YEARS:
        issues.warn(subject, fld, f"{fy_label(fy)} is {age:.1f} years old - shown as outdated in the list")


def check_figure(issues: Issues, subject: str, fld: str, fig: Any, as_of: date, *,
                 require_fy: bool = True, require_scope: bool = True) -> Optional[tuple]:
    """Validate one FIGURE; return (low, high) when it carries a usable number."""
    if fig is None:
        issues.error(subject, fld, "missing - give the figure, or status n/a with a reason")
        return None
    if not isinstance(fig, dict):
        issues.error(subject, fld, "must be an object with status, value, fy and source/method")
        return None
    status = status_of(fig)
    if status not in ("VERIFIED", "ESTIMATE", "N/A", "N/M"):
        issues.error(subject, fld, f"status {fig.get('status')!r} must be VERIFIED, ESTIMATE, n/a or n/m")
        return None
    if status in ("N/A", "N/M"):
        if fig.get("value") not in (None, ""):
            issues.error(subject, fld, f"marked {status.lower()} but carries a value - label it VERIFIED or ESTIMATE")
        if not text(fig.get("reason")):
            issues.error(subject, fld, f"{status.lower()} without a reason - say why")
        return None
    b = bounds(fig.get("value"))
    if b is None:
        issues.error(subject, fld, "value must be a number or a [low, high] range")
    if status == "VERIFIED":
        if not text(fig.get("source")):
            issues.error(subject, fld, "VERIFIED without a source")
        if isinstance(fig.get("value"), list):
            issues.error(subject, fld, "VERIFIED figures are a single number, not a range")
    if status == "ESTIMATE" and not text(fig.get("method")):
        issues.error(subject, fld, "ESTIMATE without a method")
    unit = text(fig.get("unit")) or UNIT
    if not fld.endswith("employees") and unit != UNIT:
        issues.error(subject, fld, f"unit must be {UNIT!r} (convert and state fx), got {unit!r}")
    orig = fig.get("original")
    if isinstance(orig, dict) and text(orig.get("currency")).upper() not in ("", "EUR") and not text(fig.get("fx")):
        issues.error(subject, fld, "converted from another currency without an fx note (rate and basis)")
    if require_scope and text(fig.get("scope")).lower() not in ("entity", "group"):
        issues.error(subject, fld, "scope must be 'entity' (single legal entity) or 'group' (consolidated)")
    check_source_quality(issues, subject, fld, fig, status)
    if require_fy:
        check_fy(issues, subject, fld, fig, status, as_of)
    return b


def display(fig: Any, digits: int = 0) -> Any:
    """Excel cell value: number for VERIFIED, 'c. a-b (est.)' for ESTIMATE, 'n.a.' otherwise."""
    if not isinstance(fig, dict):
        return "n.a."
    st = status_of(fig)
    v = fig.get("value")
    if st == "VERIFIED" and is_num(v):
        return round(float(v), digits) if digits else int(round(float(v)))
    if st == "ESTIMATE":
        b = bounds(v)
        if b:
            fmt = (lambda x: f"{x:,.{digits}f}") if digits else (lambda x: f"{x:,.0f}")
            core = fmt(b[0]) if b[0] == b[1] else f"{fmt(b[0])}-{fmt(b[1])}"
            return f"c. {core} (est.)"
    return "n.a."
