#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""stable — command-line client for the Stable (usestable.com) virtual mailbox.

Read, search, triage, and organize the physical mail Stable receives at your
business address, plus track transcribed checks. Authenticated with an
``x-api-key`` (env ``STABLE_API_KEY``, or a ``STABLE_API_KEY=`` line in ~/.env).

    stable mail list --since 7d               # recent mail, one line each
    stable mail summary --unread              # digest incl. AI scan summaries
    stable mail get <id> --json               # full item as JSON
    stable checks list --since 30d            # checks transcribed from mail
    stable tags assign "IRS" --mail <id>...   # organize mail with tags
    stable locations list                     # your mailbox locations

WHAT THIS CANNOT DO: Stable's public API does not expose endpoints to *request*
a scan / forward / shred / check-deposit — those are dashboard-only actions. The
API (and this CLI) only *reads* their resulting status. See references/api.md.

Machine use: pass --json to any read command for structured output; everything
else prints a compact human view. Non-zero exit codes classify failures (see
below).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stable import StableAPIError, StableClient, StableConfigError  # noqa: E402
from stable import format as fmt  # noqa: E402

# Exit codes (mirrors the convention used by the sibling `gws`/gog skill).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_EMPTY = 3
EXIT_AUTH = 4
EXIT_NOT_FOUND = 5
EXIT_RATE_LIMITED = 7


# --------------------------------------------------------------------------- #
# env / .env loading
# --------------------------------------------------------------------------- #
def _load_dotenv() -> None:
    """Populate os.environ from .env files without adding a dependency.

    Search order (first wins, real env always wins): ./.env, each parent of this
    script, then ~/.env (the recommended consolidated location).
    """
    seen: set[Path] = set()
    candidates = [Path.cwd() / ".env"]
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / ".env")
    candidates.append(Path.home() / ".env")
    for path in candidates:
        try:
            rp = path.resolve()
        except Exception:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        try:
            for line in rp.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
        except Exception:
            continue


def _make_client(args: argparse.Namespace) -> StableClient:
    key = args.api_key or os.environ.get("STABLE_API_KEY")
    base = os.environ.get("STABLE_API_BASE") or "https://api.usestable.com"
    return StableClient(key, base_url=base)


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #
def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _die(msg: str, code: int = EXIT_ERROR) -> "NoReturn":  # type: ignore[valid-type]
    print(f"stable: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _print_created(created: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        _emit_json(created)
        return
    for t in created:
        print(f"created {t.get('id')}  {t.get('name')}")


# --------------------------------------------------------------------------- #
# mail
# --------------------------------------------------------------------------- #
def _mail_filters(args: argparse.Namespace) -> dict[str, Any]:
    f: dict[str, Any] = {}
    if args.location:
        f["location_id"] = args.location
    if getattr(args, "since", None):
        f["created_gte"] = fmt.parse_when(args.since)
    if getattr(args, "until", None):
        f["created_lte"] = fmt.parse_when(args.until)
    if getattr(args, "scan_status", None):
        f["scan_status"] = args.scan_status
    return f


def _client_side_filter(items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Apply filters the API does not support server-side.

    NOTE: these run over the pages already fetched, so combine with a --limit or
    date window when the mailbox is large.
    """
    out = items
    if getattr(args, "unread", False):
        out = [i for i in out if not i.get("readAt")]
    if getattr(args, "returned", False):
        out = [i for i in out if i.get("isReturnedToSender")]
    if getattr(args, "with_checks", False):
        out = [i for i in out if i.get("checks")]
    tags = {t.lower() for t in (getattr(args, "tag", None) or [])}
    if tags:
        out = [
            i for i in out
            if tags & {t.get("name", "").lower() for t in (i.get("tags") or [])}
        ]
    teams = {t.lower() for t in (getattr(args, "team", None) or [])}
    if teams:
        out = [
            i for i in out
            if teams & {t.get("name", "").lower() for t in (i.get("teams") or [])}
        ]
    return out


def _collect_mail(sc: StableClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    # When client-side filters are active we may need to scan more than --limit
    # to satisfy it, so fetch a wider window then trim.
    has_client_filter = any(
        getattr(args, a, None) for a in ("unread", "returned", "with_checks", "tag", "team")
    )
    fetch_limit = None if args.all else args.limit
    if has_client_filter and fetch_limit is not None:
        fetch_limit = max(fetch_limit * 5, 100)
    items = list(sc.iter_mail_items(limit=fetch_limit, **_mail_filters(args)))
    items = _client_side_filter(items, args)
    if not args.all and args.limit is not None:
        items = items[: args.limit]
    return items


def cmd_mail_list(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        items = _collect_mail(sc, args)
    if args.json:
        _emit_json(items)
        return EXIT_OK if items else EXIT_EMPTY
    if not items:
        print("(no mail items match)")
        return EXIT_EMPTY
    for it in items:
        print(fmt.mail_line(it))
    print(f"\n{len(items)} item(s).")
    return EXIT_OK


def cmd_mail_summary(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        items = _collect_mail(sc, args)
    if args.json:
        _emit_json(items)
        return EXIT_OK if items else EXIT_EMPTY
    if not items:
        print("(no mail items match)")
        return EXIT_EMPTY
    for it in items:
        print(fmt.mail_summary_block(it))
        print()
    return EXIT_OK


def cmd_mail_get(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        try:
            item = sc.get_mail_item(args.id)
        except StableAPIError as e:
            if e.is_not_found:
                _die(f"mail item {args.id} not found", EXIT_NOT_FOUND)
            raise
    if args.json:
        _emit_json(item)
    else:
        print(fmt.mail_summary_block(item))
    return EXIT_OK


def cmd_mail_image(args: argparse.Namespace) -> int:
    return _download_mail_asset(args, kind="image")


def cmd_mail_scan(args: argparse.Namespace) -> int:
    return _download_mail_asset(args, kind="scan")


def _download_mail_asset(args: argparse.Namespace, *, kind: str) -> int:
    with _make_client(args) as sc:
        try:
            item = sc.get_mail_item(args.id)
        except StableAPIError as e:
            if e.is_not_found:
                _die(f"mail item {args.id} not found", EXIT_NOT_FOUND)
            raise
        if kind == "image":
            url = item.get("imageUrl")
            default_name = f"{args.id}-envelope"
        else:
            url = (item.get("scanDetails") or {}).get("imageUrl")
            default_name = f"{args.id}-scan"
        if not url:
            _die(f"mail item {args.id} has no {kind} available", EXIT_EMPTY)
        data = sc.download(url)
    out = Path(args.out) if args.out else Path.cwd() / default_name
    # Guess an extension from the URL path if the user gave a bare name.
    if out.suffix == "":
        ext = _guess_ext(url)
        out = out.with_suffix(ext)
    out.write_bytes(data)
    print(f"wrote {len(data):,} bytes → {out}")
    return EXIT_OK


def _guess_ext(url: str) -> str:
    lower = url.split("?", 1)[0].lower()
    for ext in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"):
        if lower.endswith(ext):
            return ext
    return ".bin"


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def cmd_checks_list(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        items = list(sc.iter_mail_items(limit=(None if args.all else args.limit), **_mail_filters(args)))
    rows: list[dict[str, Any]] = []
    for it in items:
        for chk in it.get("checks") or []:
            if args.status and chk.get("status") != args.status:
                continue
            rows.append({**chk, "mailItemId": it.get("id")})
    if args.json:
        _emit_json(rows)
        return EXIT_OK if rows else EXIT_EMPTY
    if not rows:
        print("(no checks found in the scanned window)")
        return EXIT_EMPTY
    total = 0.0
    for chk in rows:
        print(fmt.check_line(chk))
        if isinstance(chk.get("amount"), (int, float)):
            total += chk["amount"]
    print(f"\n{len(rows)} check(s), total ${total:,.2f}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# tags / teams
# --------------------------------------------------------------------------- #
def _resolve_ids(existing: list[dict[str, Any]], tokens: list[str], label: str) -> list[str]:
    """Map a mix of names and ids to ids using an existing list."""
    by_name = {t.get("name", "").lower(): t.get("id") for t in existing}
    by_id = {t.get("id") for t in existing}
    ids: list[str] = []
    for tok in tokens:
        if tok in by_id:
            ids.append(tok)
        elif tok.lower() in by_name:
            ids.append(by_name[tok.lower()])
        else:
            _die(f"no {label} named or id'd {tok!r} (see `stable {label}s list`)", EXIT_NOT_FOUND)
    return ids


def cmd_tags_list(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        tags = sc.list_tags()
    if args.json:
        _emit_json(tags)
        return EXIT_OK if tags else EXIT_EMPTY
    for t in tags:
        print(f"{t.get('id')}  {t.get('name')}")
    return EXIT_OK if tags else EXIT_EMPTY


def cmd_tags_create(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        created = sc.create_tags(args.name)
    _print_created(created, args.json)
    return EXIT_OK


def cmd_tags_delete(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_tags(), args.name, "tag")
        sc.delete_tags(ids)
    print(f"deleted {len(ids)} tag(s)")
    return EXIT_OK


def cmd_tags_rename(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_tags(), [args.tag], "tag")
        sc.rename_tag(ids[0], args.new_name)
    print(f"renamed → {args.new_name}")
    return EXIT_OK


def cmd_tags_assign(args: argparse.Namespace) -> int:
    return _set_tags(args, applied=True)


def cmd_tags_remove(args: argparse.Namespace) -> int:
    return _set_tags(args, applied=False)


def _set_tags(args: argparse.Namespace, *, applied: bool) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_tags(), args.tag, "tag")
        sc.set_mail_item_tags(args.mail, [(i, applied) for i in ids])
    verb = "assigned" if applied else "removed"
    print(f"{verb} {len(ids)} tag(s) on {len(args.mail)} mail item(s)")
    return EXIT_OK


def cmd_teams_list(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        teams = sc.list_teams()
    if args.json:
        _emit_json(teams)
        return EXIT_OK if teams else EXIT_EMPTY
    for t in teams:
        print(f"{t.get('id')}  {t.get('name')}")
    return EXIT_OK if teams else EXIT_EMPTY


def cmd_teams_create(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        created = sc.create_teams(args.name)
    _print_created(created, args.json)
    return EXIT_OK


def cmd_teams_delete(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_teams(), args.name, "team")
        sc.delete_teams(ids)
    print(f"deleted {len(ids)} team(s)")
    return EXIT_OK


def cmd_teams_rename(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_teams(), [args.team], "team")
        sc.rename_team(ids[0], args.new_name)
    print(f"renamed → {args.new_name}")
    return EXIT_OK


def cmd_teams_assign(args: argparse.Namespace) -> int:
    return _set_teams(args, applied=True)


def cmd_teams_remove(args: argparse.Namespace) -> int:
    return _set_teams(args, applied=False)


def _set_teams(args: argparse.Namespace, *, applied: bool) -> int:
    with _make_client(args) as sc:
        ids = _resolve_ids(sc.list_teams(), args.team, "team")
        sc.set_mail_item_teams(args.mail, [(i, applied) for i in ids])
    verb = "assigned" if applied else "removed"
    print(f"{verb} {len(ids)} team(s) on {len(args.mail)} mail item(s)")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# locations
# --------------------------------------------------------------------------- #
def cmd_locations_list(args: argparse.Namespace) -> int:
    with _make_client(args) as sc:
        locs = sc.list_locations()
    if args.json:
        _emit_json(locs)
        return EXIT_OK if locs else EXIT_EMPTY
    for loc in locs:
        addr = loc.get("address") or {}
        line = ", ".join(
            x for x in [addr.get("line1"), addr.get("city"), addr.get("state"), addr.get("postalCode")] if x
        )
        onboard = (loc.get("onboarding") or {}).get("status", "?")
        print(f"{loc.get('id')}  [{loc.get('type', '?')}/{loc.get('status', '?')}/{onboard}]  {line}")
    return EXIT_OK if locs else EXIT_EMPTY


# --------------------------------------------------------------------------- #
# arg parsing
# --------------------------------------------------------------------------- #
def _add_mail_filter_flags(p: argparse.ArgumentParser, *, client_side: bool = True) -> None:
    p.add_argument("--location", help="filter by location id")
    p.add_argument("--since", help="only mail created on/after this time (ISO, or 7d/24h/2w/today)")
    p.add_argument("--until", help="only mail created on/before this time")
    p.add_argument("--scan-status", choices=["processing", "completed"], help="filter by scan status")
    p.add_argument("--limit", type=int, default=50, help="max items (default 50)")
    p.add_argument("--all", action="store_true", help="fetch every page (ignores --limit)")
    if client_side:
        p.add_argument("--unread", action="store_true", help="only mail not yet read")
        p.add_argument("--returned", action="store_true", help="only returned-to-sender mail")
        p.add_argument("--with-checks", action="store_true", help="only mail containing a check")
        p.add_argument("--tag", action="append", help="only mail with this tag name (repeatable)")
        p.add_argument("--team", action="append", help="only mail with this team name (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stable", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-key", help="Stable API key (else $STABLE_API_KEY / ~/.env)")
    sub = p.add_subparsers(dest="group", required=True)

    # mail
    mail = sub.add_parser("mail", help="read / search / triage physical mail").add_subparsers(dest="cmd", required=True)
    m_list = mail.add_parser("list", help="one dense line per mail item")
    _add_mail_filter_flags(m_list)
    m_list.add_argument("--json", action="store_true")
    m_list.set_defaults(func=cmd_mail_list)

    m_sum = mail.add_parser("summary", help="multi-line digest incl. AI scan summaries")
    _add_mail_filter_flags(m_sum)
    m_sum.add_argument("--json", action="store_true")
    m_sum.set_defaults(func=cmd_mail_summary)

    m_get = mail.add_parser("get", help="one mail item in full")
    m_get.add_argument("id")
    m_get.add_argument("--json", action="store_true")
    m_get.set_defaults(func=cmd_mail_get)

    m_img = mail.add_parser("image", help="download the envelope image")
    m_img.add_argument("id")
    m_img.add_argument("--out", help="output path (extension inferred if omitted)")
    m_img.set_defaults(func=cmd_mail_image)

    m_scan = mail.add_parser("scan", help="download the interior scan (if one exists)")
    m_scan.add_argument("id")
    m_scan.add_argument("--out", help="output path (extension inferred if omitted)")
    m_scan.set_defaults(func=cmd_mail_scan)

    # checks
    checks = sub.add_parser("checks", help="checks transcribed from mail").add_subparsers(dest="cmd", required=True)
    c_list = checks.add_parser("list", help="list checks across the scanned window")
    _add_mail_filter_flags(c_list, client_side=False)
    c_list.add_argument("--status", choices=["notRequested", "processing", "completed", "failed"], help="filter by check status")
    c_list.add_argument("--json", action="store_true")
    c_list.set_defaults(func=cmd_checks_list)

    # tags
    tags = sub.add_parser("tags", help="manage + assign tags").add_subparsers(dest="cmd", required=True)
    t_list = tags.add_parser("list"); t_list.add_argument("--json", action="store_true"); t_list.set_defaults(func=cmd_tags_list)
    t_new = tags.add_parser("create"); t_new.add_argument("name", nargs="+"); t_new.add_argument("--json", action="store_true"); t_new.set_defaults(func=cmd_tags_create)
    t_del = tags.add_parser("delete"); t_del.add_argument("name", nargs="+", help="tag name(s) or id(s)"); t_del.set_defaults(func=cmd_tags_delete)
    t_ren = tags.add_parser("rename"); t_ren.add_argument("tag", help="name or id"); t_ren.add_argument("new_name"); t_ren.set_defaults(func=cmd_tags_rename)
    t_asg = tags.add_parser("assign"); t_asg.add_argument("tag", nargs="+", help="tag name(s) or id(s)"); t_asg.add_argument("--mail", nargs="+", required=True, help="mail item id(s)"); t_asg.set_defaults(func=cmd_tags_assign)
    t_rm = tags.add_parser("remove"); t_rm.add_argument("tag", nargs="+", help="tag name(s) or id(s)"); t_rm.add_argument("--mail", nargs="+", required=True, help="mail item id(s)"); t_rm.set_defaults(func=cmd_tags_remove)

    # teams
    teams = sub.add_parser("teams", help="manage + assign teams").add_subparsers(dest="cmd", required=True)
    e_list = teams.add_parser("list"); e_list.add_argument("--json", action="store_true"); e_list.set_defaults(func=cmd_teams_list)
    e_new = teams.add_parser("create"); e_new.add_argument("name", nargs="+"); e_new.add_argument("--json", action="store_true"); e_new.set_defaults(func=cmd_teams_create)
    e_del = teams.add_parser("delete"); e_del.add_argument("name", nargs="+", help="team name(s) or id(s)"); e_del.set_defaults(func=cmd_teams_delete)
    e_ren = teams.add_parser("rename"); e_ren.add_argument("team", help="name or id"); e_ren.add_argument("new_name"); e_ren.set_defaults(func=cmd_teams_rename)
    e_asg = teams.add_parser("assign"); e_asg.add_argument("team", nargs="+", help="team name(s) or id(s)"); e_asg.add_argument("--mail", nargs="+", required=True, help="mail item id(s)"); e_asg.set_defaults(func=cmd_teams_assign)
    e_rm = teams.add_parser("remove"); e_rm.add_argument("team", nargs="+", help="team name(s) or id(s)"); e_rm.add_argument("--mail", nargs="+", required=True, help="mail item id(s)"); e_rm.set_defaults(func=cmd_teams_remove)

    # locations
    locs = sub.add_parser("locations", help="your mailbox locations").add_subparsers(dest="cmd", required=True)
    l_list = locs.add_parser("list"); l_list.add_argument("--json", action="store_true"); l_list.set_defaults(func=cmd_locations_list)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except StableConfigError as e:
        _die(str(e), EXIT_AUTH)
    except StableAPIError as e:
        if e.is_auth:
            _die(f"{e} — check your STABLE_API_KEY (see references/setup.md)", EXIT_AUTH)
        if e.is_not_found:
            _die(str(e), EXIT_NOT_FOUND)
        if e.is_rate_limited:
            _die(f"{e} — slow down and retry", EXIT_RATE_LIMITED)
        _die(str(e), EXIT_ERROR)
    except KeyboardInterrupt:  # pragma: no cover
        _die("interrupted", 130)


if __name__ == "__main__":
    raise SystemExit(main())
