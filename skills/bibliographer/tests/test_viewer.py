"""Tests for the HTML viewer generator (pure — no deps, no network)."""

import json
import re

import _viewer


def _embedded(html):
    m = re.search(r"window\.PAPERS = (\[.*?\]);</script>", html, re.S)
    assert m, "embedded PAPERS payload not found"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_render_is_valid_and_embeds_data():
    recs = [
        {"citekey": "a2020x", "title": "Alpha", "authors_text": "Doe, J", "year": 2020,
         "file_path": "papers/Doe, J/x.pdf", "document_id": "zzz", "content_hash": "zzz"},
        {"citekey": "b2019y", "title": "Beta", "year": 2019, "content_state": "stub"},
    ]
    html = _viewer.render(recs, "My Lib")
    assert html.lower().startswith("<!doctype html>")
    assert "<title>My Lib</title>" in html and 'id="q"' in html  # title + search box
    data = _embedded(html)
    assert {d["citekey"] for d in data} == {"a2020x", "b2019y"}
    # internal fields are slimmed away; display fields kept
    assert "document_id" not in data[0] and "content_hash" not in data[0]
    assert any(d.get("file_path") == "papers/Doe, J/x.pdf" for d in data)


def test_render_is_deterministic():
    recs = [{"citekey": "b", "title": "B"}, {"citekey": "a", "title": "A"}]
    # sorted by citekey, no embedded timestamp -> identical output regardless of input order
    assert _viewer.render(recs) == _viewer.render(list(reversed(recs)))


def test_render_escapes_script_breakout():
    recs = [{"citekey": "x", "title": "evil </script><script>alert(1)</script>"}]
    html = _viewer.render(recs)
    payload = html.split("window.PAPERS = ", 1)[1].split("</script>", 1)[0]
    assert "</script>" not in payload  # the raw </script> in the title must be escaped
