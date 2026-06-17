"""Offline tests for the resolver helpers (no network — pure parsing/logic)."""

import asyncio
import json

import _resolvers as R

_PDF = b"%PDF-1.5\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
_HTML_CHALLENGE = (
    b"<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    b"<body>Checking your browser before accessing.</body></html>"
)


class _FakeResp:
    def __init__(self, status, content=b"", content_type="application/pdf"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": content_type}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient driven by a (url, params)->resp fn."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    async def get(self, url, params=None, headers=None, follow_redirects=False, **kw):
        self.calls.append(url)
        return self._handler(url, params or {})

    async def aclose(self):
        pass


def test_classify_identifier():
    assert R.classify_identifier("10.1038/nphys1170") == ("doi", "10.1038/nphys1170")
    assert R.classify_identifier("doi:10.1/x") == ("doi", "10.1/x")
    assert R.classify_identifier("arXiv:1706.03762v5") == ("arxiv", "1706.03762v5")
    assert R.classify_identifier("https://arxiv.org/abs/1706.03762") == ("arxiv", "1706.03762")
    assert R.classify_identifier("PMC9283931") == ("pmcid", "PMC9283931")
    assert R.classify_identifier("PMID:17284678") == ("pmid", "17284678")
    assert R.classify_identifier("something else") == ("unknown", "something else")


def test_sniff_filename_reconstructions():
    assert ("doi", "10.1038/s41598-024-55666-6") in R.sniff_filename("s41598_024_55666_6.pdf")
    assert ("doi", "10.1038/nbt.3779") in R.sniff_filename("nbt_3779.pdf")
    # \b would miss PMC next to an underscore — the filename path uses a tolerant regex
    assert ("pmcid", "PMC9283931") in R.sniff_filename("317_pmc9283931.pdf")
    assert ("doi", "10.1038/nphys1170") in R.sniff_filename("10.1038_nphys1170.pdf")
    assert R.sniff_filename("rudick1982.pdf") == []  # no false positives


def test_reference_doi_is_distrusted():
    """A DOI that appears only in the reference list must not pass content check."""
    text = ("ETHICAL PUBLICATION STATEMENT. ORCID Jane Doe. "
            "REFERENCES 1. Smith J. Some cited paper about widgets. doi:10.1234/cited")
    before, after = R._split_at_references(text)
    assert R._extract_ids(before) == []
    assert ("doi", "10.1234/cited") in R._extract_ids(after)
    # the cited title's words aren't in the pre-references content -> low overlap
    assert R._title_overlap("Some cited paper about widgets", before) < 0.34


def test_title_overlap_matches_real_content():
    title = "Quantitative electrophysiological biomarker of duplication syndrome"
    content = "we report a quantitative electrophysiological biomarker of the duplication syndrome ..."
    assert R._title_overlap(title, content) >= 0.8


def test_from_crossref_strips_title_markup():
    cr = R._from_crossref({"title": ["A <i>gene</i> study"], "DOI": "10.1/X",
                           "type": "journal-article", "issued": {"date-parts": [[2020, 3]]}})
    assert "<" not in cr["title"] and "gene study" in cr["title"]
    assert cr["year"] == 2020 and cr["bibtex_type"] == "article"


def test_from_semantic_scholar_normalizes():
    s2 = R._from_semantic_scholar({
        "title": "T", "paperId": "p1", "year": 2019,
        "externalIds": {"DOI": "10.2/Y", "PubMedCentral": 9283931, "ArXiv": "1234.5678"},
        "authors": [{"name": "Sam Quigley"}],
        "openAccessPdf": {"url": "https://example.org/x.pdf"},
    })
    assert s2["doi"] == "10.2/y"  # lowercased
    assert s2["pmcid"] == "PMC9283931"  # PMC-prefixed
    assert s2["authors"][0] == {"family": "Quigley", "given": "Sam"}
    assert s2["oa_pdf_url"] == "https://example.org/x.pdf"


# --------------------------------------------------------------------------- #
# OA PDF acquisition (mocked network) — the bib-fetch robustness path
# --------------------------------------------------------------------------- #
def _no_cache(monkeypatch):
    """Force resolver helpers to bypass the on-disk cache for deterministic tests."""
    monkeypatch.setattr(R, "_resolver_cache_obj", None)


def test_download_pdf_rejects_html_challenge(monkeypatch, tmp_path):
    """A 200 HTML/Cloudflare challenge (even mislabeled application/pdf) is rejected."""
    dest = tmp_path / "out.pdf"
    # content-type claims PDF, body is an HTML challenge page — magic bytes win.
    client = _FakeClient(lambda url, params: _FakeResp(200, _HTML_CHALLENGE, "application/pdf"))
    assert asyncio.run(R.download_pdf("https://publisher/x.pdf", dest, client)) is False
    assert not dest.exists()

    client = _FakeClient(lambda url, params: _FakeResp(200, _PDF, "application/octet-stream"))
    assert asyncio.run(R.download_pdf("https://repo/x.pdf", dest, client)) is True
    assert dest.read_bytes().startswith(b"%PDF-")


def test_unpaywall_returns_all_pdf_locations(monkeypatch):
    """Both best_oa_location and oa_locations[*] PDF urls are surfaced, de-duped."""
    _no_cache(monkeypatch)
    body = json.dumps({
        "best_oa_location": {"url_for_pdf": "https://publisher/landing.pdf"},
        "oa_locations": [
            {"url_for_pdf": "https://publisher/landing.pdf"},  # dup of best
            {"url_for_pdf": "https://repo/mirror.pdf"},
            {"url_for_pdf": None},
        ],
    }).encode()
    client = _FakeClient(lambda url, params: _FakeResp(200, body, "application/json"))
    urls = asyncio.run(R.fetch_unpaywall_pdf_urls("10.1/x", client))
    assert urls == ["https://publisher/landing.pdf", "https://repo/mirror.pdf"]


def test_acquire_oa_falls_back_to_europepmc_render(monkeypatch, tmp_path):
    """Xu/Weeber repro: publisher URL serves HTML, PMCID is derived, ?pdf=render wins.

    The stub record carries only a DOI (no PMCID). Unpaywall's best location is a
    publisher landing page that answers HTML; the fetcher must derive the PMCID
    via NCBI idconv and fall back to Europe PMC's render endpoint.
    """
    _no_cache(monkeypatch)
    dest = tmp_path / "out.pdf"

    def handler(url, params):
        if "api.unpaywall.org" in url:
            return _FakeResp(200, json.dumps({
                "best_oa_location": {"url_for_pdf": "https://www.nature.com/articles/cr2017132.pdf"},
            }).encode(), "application/json")
        if "nature.com" in url:  # publisher landing page — HTML for any UA
            return _FakeResp(200, _HTML_CHALLENGE, "text/html")
        if "idconv" in url:
            return _FakeResp(200, json.dumps({
                "records": [{"doi": "10.1038/cr.2017.132", "pmcid": "PMC5752837"}],
            }).encode(), "application/json")
        if "pdf=render" in url:  # Europe PMC OA render — the route that works
            return _FakeResp(200, _PDF, "application/pdf")
        return _FakeResp(404, b"", "text/plain")

    rec = {"doi": "10.1038/cr.2017.132"}  # citation-only stub: no pmcid stored
    src = asyncio.run(R.acquire_oa_pdf(rec, dest, _FakeClient(handler)))
    assert src == "europepmc"
    assert dest.read_bytes().startswith(b"%PDF-")


def test_acquire_oa_honest_miss_for_non_oa(monkeypatch, tmp_path):
    """Yi/Smith repro: genuinely non-OA paper yields None (no false success)."""
    _no_cache(monkeypatch)
    dest = tmp_path / "out.pdf"

    def handler(url, params):
        if "api.unpaywall.org" in url:  # is_oa == false → no PDF locations
            return _FakeResp(200, json.dumps({"best_oa_location": None, "oa_locations": []}).encode(),
                             "application/json")
        if "idconv" in url:  # author-manuscript stub exists, but no free full text
            return _FakeResp(200, json.dumps({
                "records": [{"doi": "10.1/x", "pmcid": "PMC4537845"}],
            }).encode(), "application/json")
        if "pdf=render" in url or "ptpmcrender" in url:
            return _FakeResp(500, b'{"error":"no full text"}', "application/json")
        if "oa.fcgi" in url:  # PMC OA service: not in the OA subset
            return _FakeResp(200, b"<OA><error code='idDoesNotExist'/></OA>", "application/xml")
        if "semanticscholar.org" in url:
            return _FakeResp(200, json.dumps({"paperId": "p", "openAccessPdf": None}).encode(),
                             "application/json")
        return _FakeResp(404, b"", "text/plain")

    rec = {"doi": "10.1/x"}
    src = asyncio.run(R.acquire_oa_pdf(rec, dest, _FakeClient(handler)))
    assert src is None
    assert not dest.exists()


def test_acquire_oa_uses_pmc_oa_service_fallback(monkeypatch, tmp_path):
    """When Europe PMC render declines, the NCBI PMC OA service href is used."""
    _no_cache(monkeypatch)
    dest = tmp_path / "out.pdf"

    def handler(url, params):
        if "api.unpaywall.org" in url:
            return _FakeResp(200, json.dumps({"best_oa_location": None, "oa_locations": []}).encode(),
                             "application/json")
        if "pdf=render" in url or "ptpmcrender" in url:
            return _FakeResp(500, b"err", "application/json")
        if "oa.fcgi" in url:
            return _FakeResp(200,
                             b'<OA><records><record><link format="pdf" '
                             b'href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/x.pdf"/>'
                             b"</record></records></OA>", "application/xml")
        if url.startswith("https://ftp.ncbi.nlm.nih.gov/"):  # ftp:// rewritten to https
            return _FakeResp(200, _PDF, "application/pdf")
        return _FakeResp(404, b"", "text/plain")

    rec = {"doi": "10.1/x", "pmcid": "PMC6742065"}
    src = asyncio.run(R.acquire_oa_pdf(rec, dest, _FakeClient(handler)))
    assert src == "pmc_oa"
    assert dest.read_bytes().startswith(b"%PDF-")


def _pow_page(challenge, difficulty, cookie="cloudpmc-viewer-pow"):
    return (
        '<html><head></head><body><script type="module">\n'
        f'const POW_CHALLENGE = "{challenge}"\n'
        f'const POW_DIFFICULTY = "{difficulty}"\n'
        f'const POW_COOKIE_NAME = "{cookie}"\n'
        "window.ncbi.pmc.pow.init(POW_CHALLENGE, POW_DIFFICULTY);\n"
        "</script></body></html>"
    ).encode()


def test_parse_pmc_pow():
    chal, diff, cookie = R._parse_pmc_pow(_pow_page("aZ9:wq", 4).decode())
    assert (chal, diff, cookie) == ("aZ9:wq", 4, "cloudpmc-viewer-pow")
    assert R._parse_pmc_pow("<html>no challenge here</html>") is None


def test_solve_pmc_pow_meets_difficulty():
    import hashlib
    nonce = R._solve_pmc_pow("some-challenge:token", 2)
    assert nonce is not None
    assert hashlib.sha256(("some-challenge:token" + nonce).encode()).hexdigest().startswith("00")
    # Difficulty outside the safe band is refused rather than ground forever.
    assert R._solve_pmc_pow("x", 0) is None
    assert R._solve_pmc_pow("x", R._POW_MAX_DIFFICULTY + 1) is None


def test_fetch_pmc_authorms_pdf_solves_pow(tmp_path):
    """Landing page → scrape PDF name → PoW challenge → solve → real PDF on retry."""
    dest = tmp_path / "out.pdf"
    state = {"pdf_hits": 0}
    landing = (
        '<html><body><a href="/articles/PMC8976688/pdf/nihms-1785194.pdf">PDF</a>'
        "</body></html>"
    ).encode()

    def handler(url, params):
        if url.endswith("/articles/PMC8976688/"):
            return _FakeResp(200, landing, "text/html")
        if url.endswith("nihms-1785194.pdf"):
            state["pdf_hits"] += 1
            if state["pdf_hits"] == 1:  # first hit: the proof-of-work page
                return _FakeResp(200, _pow_page("chal:abc", 2), "text/html")
            return _FakeResp(200, _PDF, "application/pdf")  # after cookie set
        return _FakeResp(404, b"", "text/plain")

    ok = asyncio.run(R.fetch_pmc_authorms_pdf("PMC8976688", dest, _FakeClient(handler)))
    assert ok is True
    assert dest.read_bytes().startswith(b"%PDF-")
    assert state["pdf_hits"] == 2  # challenge, then solved retry


def test_acquire_oa_uses_authorms_pow_when_not_in_oa_subset(monkeypatch, tmp_path):
    """Shao repro: not in the OA subset, so only the direct-NCBI PoW route works."""
    _no_cache(monkeypatch)
    dest = tmp_path / "out.pdf"
    state = {"pdf_hits": 0}
    landing = b'<a href="/articles/PMC8976688/pdf/nihms-1785194.pdf">PDF</a>'

    def handler(url, params):
        if "api.unpaywall.org" in url:  # is_oa false
            return _FakeResp(200, json.dumps({"best_oa_location": None, "oa_locations": []}).encode(),
                             "application/json")
        if "pdf=render" in url or "ptpmcrender" in url:
            return _FakeResp(500, b'{"error":"no full text"}', "application/json")
        if "oa.fcgi" in url:  # author manuscript: absent from OA subset
            return _FakeResp(200, b"<OA><error code='idDoesNotExist'/></OA>", "application/xml")
        if url.endswith("/articles/PMC8976688/"):
            return _FakeResp(200, landing, "text/html")
        if url.endswith("nihms-1785194.pdf"):
            state["pdf_hits"] += 1
            if state["pdf_hits"] == 1:
                return _FakeResp(200, _pow_page("chal:abc", 2), "text/html")
            return _FakeResp(200, _PDF, "application/pdf")
        return _FakeResp(404, b"", "text/plain")

    rec = {"doi": "10.1126/scitranslmed.aaz7785", "pmcid": "PMC8976688"}
    src = asyncio.run(R.acquire_oa_pdf(rec, dest, _FakeClient(handler)))
    assert src == "pmc_authorms"
    assert dest.read_bytes().startswith(b"%PDF-")
