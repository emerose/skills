"""Human-readable formatting + small helpers shared by the CLI.

Kept free of I/O and of the client so it is trivially unit-testable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_REL = re.compile(r"^(\d+)\s*([dhwm])$", re.I)  # 7d, 24h, 2w, 3m


def parse_when(value: str) -> str:
    """Normalize a --since/--until value to an ISO-8601 UTC timestamp.

    Accepts:
      * relative offsets into the past: ``7d`` (days), ``24h`` (hours),
        ``2w`` (weeks), ``3m`` (~30-day months);
      * ``today`` / ``yesterday``;
      * anything else is passed through unchanged (assumed already ISO-8601).
    """
    v = value.strip()
    low = v.lower()
    now = datetime.now(timezone.utc)
    if low == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return _iso(start)
    if low == "yesterday":
        start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return _iso(start)
    m = _REL.match(v)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = {
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "w": timedelta(weeks=n),
            "m": timedelta(days=30 * n),
        }[unit]
        return _iso(now - delta)
    return v


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _short_dt(value: Optional[str]) -> str:
    if not value:
        return "—"
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return str(value)[:10]


def recipient_name(item: dict[str, Any]) -> str:
    """Best-effort display name for a mail item's recipient."""
    rec = item.get("recipients") or {}
    biz = rec.get("business") or {}
    if biz.get("name"):
        return biz["name"]
    ind = rec.get("individual") or {}
    if ind.get("firstName") or ind.get("lastName"):
        return f"{ind.get('firstName', '')} {ind.get('lastName', '')}".strip()
    line1 = (rec.get("line1") or {}).get("text")
    if line1:
        return line1
    # deprecated flat fields as a last resort
    return item.get("businessRecipient") or item.get("individualRecipient") or "—"


def _flags(item: dict[str, Any]) -> str:
    flags = []
    if not item.get("readAt"):
        flags.append("unread")
    if item.get("isReturnedToSender"):
        flags.append("returned")
    if item.get("checks"):
        flags.append(f"{len(item['checks'])}✓")
    scan = (item.get("scanDetails") or {}).get("status")
    if scan:
        flags.append(f"scan:{scan}")
    fwd = (item.get("forwardDetails") or {}).get("status")
    if fwd:
        flags.append(f"fwd:{fwd}")
    if item.get("archivedAt"):
        flags.append("archived")
    return ",".join(flags) if flags else "-"


def mail_line(item: dict[str, Any]) -> str:
    """A single dense line for ``mail list``.

    The full item id is shown (not truncated) because it is the handle callers
    feed back into ``mail get`` / ``mail image`` / ``tags assign --mail`` — the
    API rejects a shortened id with "Invalid UUID".
    """
    mid = item.get("id") or "—"
    date = _short_dt(item.get("createdAt") or item.get("clearAt"))
    frm = (item.get("from") or "—")[:24]
    to = recipient_name(item)[:22]
    tags = ",".join(t.get("name", "") for t in (item.get("tags") or []))
    tag_str = f"  #{tags}" if tags else ""
    return f"{mid}  {date}  {frm:<24}  → {to:<22}  [{_flags(item)}]{tag_str}"


def mail_summary_block(item: dict[str, Any]) -> str:
    """A short multi-line digest for ``mail summary`` / ``mail get``."""
    lines = []
    lines.append(f"[{item.get('id')}]  {_short_dt(item.get('createdAt'))}")
    lines.append(f"  From:      {item.get('from') or '—'}")
    lines.append(f"  To:        {recipient_name(item)}")
    loc = (item.get("location") or {}).get("address") or {}
    if loc:
        lines.append(f"  Location:  {loc.get('city', '')}, {loc.get('state', '')}")
    lines.append(f"  Status:    {_flags(item)}")
    scan = item.get("scanDetails") or {}
    if scan.get("scanNoticeType"):
        lines.append(f"  ScanNotice: {scan['scanNoticeType']}")
    summary = scan.get("summary")
    if summary:
        wrapped = summary.strip().replace("\n", " ")
        lines.append(f"  Summary:   {wrapped}")
    for chk in item.get("checks") or []:
        lines.append("  " + check_line(chk, indent=False))
    return "\n".join(lines)


def check_line(chk: dict[str, Any], *, indent: bool = True) -> str:
    amt = chk.get("amount")
    cur = chk.get("currency") or "USD"
    amt_str = f"{amt:,.2f} {cur}" if isinstance(amt, (int, float)) else "?"
    payer = chk.get("payer") or "?"
    status = chk.get("status") or "?"
    dest = chk.get("destinationAccount") or {}
    dest_str = ""
    if dest:
        dest_str = f" → {dest.get('name', '')} …{dest.get('last4', '')}"
    prefix = "" if not indent else ""
    return f"{prefix}Check ${amt_str} from {payer} [{status}]{dest_str}"
