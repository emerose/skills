"""Tests for the Stable client + CLI — all offline via httpx.MockTransport.

Run with:  uv run --with pytest --with httpx pytest skills/stable/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from stable import StableAPIError, StableClient, StableConfigError  # noqa: E402
from stable import format as fmt  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _mail_node(mid: str, **over):
    node = {
        "id": mid,
        "from": "IRS",
        "createdAt": "2026-06-30T12:00:00.000Z",
        "readAt": None,
        "recipients": {"line1": {"text": "Acme Inc"}, "business": {"id": "b1", "name": "Acme Inc"}},
        "location": {"address": {"city": "SF", "state": "CA"}},
        "scanDetails": {"status": "completed", "summary": "Tax notice CP2000."},
        "forwardDetails": {},
        "shredDetails": {},
        "depositDetails": {},
        "checks": [],
        "tags": [],
        "teams": [],
    }
    node.update(over)
    return node


def _connection(nodes, *, has_next=False, end_cursor=None):
    return {
        "edges": [{"cursor": f"c{i}", "node": n} for i, n in enumerate(nodes)],
        "pageInfo": {"hasNextPage": has_next, "hasPreviousPage": False, "endCursor": end_cursor},
        "totalCount": len(nodes),
    }


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.usestable.com")
    return StableClient("test-key", http_client=http)


# --------------------------------------------------------------------------- #
# auth / config
# --------------------------------------------------------------------------- #
def test_missing_key_raises():
    with pytest.raises(StableConfigError):
        StableClient(None)


def test_api_key_header_sent():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("x-api-key")
        return httpx.Response(200, json=_connection([]))

    with make_client(handler) as sc:
        sc.list_mail_items()
    assert seen["key"] == "test-key"


def test_error_maps_status():
    def handler(req):
        return httpx.Response(403, json={"message": "Forbidden"})

    with make_client(handler) as sc:
        with pytest.raises(StableAPIError) as ei:
            sc.list_mail_items()
    assert ei.value.is_auth and ei.value.status_code == 403


# --------------------------------------------------------------------------- #
# pagination
# --------------------------------------------------------------------------- #
def test_iter_paginates_and_respects_limit():
    pages = {
        None: _connection([_mail_node("a"), _mail_node("b")], has_next=True, end_cursor="CUR"),
        "CUR": _connection([_mail_node("c")], has_next=False),
    }
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        after = dict(req.url.params).get("after")
        calls.append(after)
        return httpx.Response(200, json=pages[after])

    with make_client(handler) as sc:
        got = [n["id"] for n in sc.iter_mail_items(page_size=2)]
    assert got == ["a", "b", "c"]
    assert calls == [None, "CUR"]

    with make_client(handler) as sc:
        got = [n["id"] for n in sc.iter_mail_items(limit=1, page_size=2)]
    assert got == ["a"]


def test_filters_forwarded_as_params():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return httpx.Response(200, json=_connection([]))

    with make_client(handler) as sc:
        sc.list_mail_items(location_id="loc1", created_gte="2026-01-01", scan_status="completed")
    assert seen["locationId"] == "loc1"
    assert seen["createdAt_gte"] == "2026-01-01"
    assert seen["scan.status"] == "completed"


# --------------------------------------------------------------------------- #
# tags / teams write bodies
# --------------------------------------------------------------------------- #
def test_set_mail_item_tags_body():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={})

    with make_client(handler) as sc:
        sc.set_mail_item_tags(["m1", "m2"], [("t1", True), ("t2", False)])
    assert seen["path"] == "/v1/mail-items/tags"
    assert seen["body"] == {
        "mailItemIds": ["m1", "m2"],
        "tags": [{"id": "t1", "isApplied": True}, {"id": "t2", "isApplied": False}],
    }


def test_create_tags_body_and_normalized_response():
    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content) == {"tags": [{"name": "IRS"}]}
        # response wrapped in {tags: [...]}
        return httpx.Response(200, json={"tags": [{"id": "t9", "name": "IRS"}]})

    with make_client(handler) as sc:
        out = sc.create_tags(["IRS"])
    assert out == [{"id": "t9", "name": "IRS"}]


def test_list_tags_accepts_bare_array():
    def handler(req):
        return httpx.Response(200, json=[{"id": "t1", "name": "A"}])

    with make_client(handler) as sc:
        assert sc.list_tags() == [{"id": "t1", "name": "A"}]


def test_download_uses_signed_url_without_auth_header():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["has_key"] = "x-api-key" in req.headers
        return httpx.Response(200, content=b"PDFBYTES")

    with make_client(handler) as sc:
        data = sc.download("https://api.usestable.com/signed/x.pdf")
    assert data == b"PDFBYTES"
    assert seen["has_key"] is False


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def test_parse_when_relative():
    assert fmt.parse_when("2026-01-02T03:04:05Z") == "2026-01-02T03:04:05Z"
    iso = fmt.parse_when("7d")
    assert iso.endswith("Z") and "T" in iso  # produced an ISO timestamp


def test_recipient_name_prefers_business():
    node = _mail_node("x")
    assert fmt.recipient_name(node) == "Acme Inc"
    node2 = _mail_node("y", recipients={"line1": {"text": "PO Box 9"}})
    assert fmt.recipient_name(node2) == "PO Box 9"


def test_mail_line_and_summary_render():
    mid = "6e3426ff-0a9d-4296-86ae-35f266d52bd5"
    node = _mail_node(mid, checks=[{"amount": 50.0, "currency": "USD", "payer": "Bob", "status": "completed"}])
    line = fmt.mail_line(node)
    # full id must appear verbatim — it is the handle fed back into `mail get`
    assert mid in line and "IRS" in line and "unread" in line and "1✓" in line
    block = fmt.mail_summary_block(node)
    assert "Tax notice CP2000" in block and "Check $50.00 USD from Bob" in block


# --------------------------------------------------------------------------- #
# CLI wiring (monkeypatched client)
# --------------------------------------------------------------------------- #
def test_cli_mail_list_json(monkeypatch, capsys):
    import stable_cli  # scripts/stable_cli.py

    def handler(req):
        return httpx.Response(200, json=_connection([_mail_node("id1")]))

    monkeypatch.setattr(stable_cli, "_make_client", lambda args: make_client(handler))
    rc = stable_cli.main(["mail", "list", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data[0]["id"] == "id1"


def test_cli_unread_filter(monkeypatch, capsys):
    import stable_cli

    nodes = [_mail_node("read1", readAt="2026-06-30T00:00:00Z"), _mail_node("unread1", readAt=None)]

    def handler(req):
        return httpx.Response(200, json=_connection(nodes))

    monkeypatch.setattr(stable_cli, "_make_client", lambda args: make_client(handler))
    rc = stable_cli.main(["mail", "list", "--unread", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    ids = [n["id"] for n in json.loads(out)]
    assert ids == ["unread1"]


def test_cli_checks_totals(monkeypatch, capsys):
    import stable_cli

    node = _mail_node("m1", checks=[
        {"amount": 100.0, "currency": "USD", "payer": "X", "status": "completed"},
        {"amount": 25.5, "currency": "USD", "payer": "Y", "status": "processing"},
    ])

    def handler(req):
        return httpx.Response(200, json=_connection([node]))

    monkeypatch.setattr(stable_cli, "_make_client", lambda args: make_client(handler))
    rc = stable_cli.main(["checks", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "total $125.50" in out
