from regulator.sources import personnel

# A typical FDA electronic-signature manifestation page (two signers).
SIG_TEXT = """
Some review text ...

---------------------------------------------------------------------
This is a representation of an electronic record that was signed
electronically and this page is the manifestation of the electronic
signature.
---------------------------------------------------------------------
/s/
----------------------------------------------------
JOHN J JENKINS
07/31/2014

ELLIS UNGER
08/01/2014
"""


def test_extract_signatures():
    sigs = personnel.extract_signatures(SIG_TEXT)
    names = {s["name"] for s in sigs}
    assert "John J Jenkins" in names
    assert "Ellis Unger" in names
    jj = next(s for s in sigs if s["name"] == "John J Jenkins")
    assert jj["date"] == "2014-07-31"


def test_extract_signatures_dedup():
    sigs = personnel.extract_signatures(SIG_TEXT + "\n" + SIG_TEXT)
    # same (name, date) pairs collapse
    assert len({(s["name"], s["date"]) for s in sigs}) == len(sigs)


PROXY_TEXT = """
-----
This is a representation of an electronic record that was signed
electronically and this page is the manifestation of the electronic
signature.
-----
/s/
-----
JOHN J FARLEY on behalf of EDWARD M COX
10/10/2014
Acting on behalf of Edward Cox
"""


def test_extract_signatures_proxy():
    sigs = personnel.extract_signatures(PROXY_TEXT)
    assert len(sigs) == 1
    s = sigs[0]
    # the principal is the official of record; the proxy is recorded separately
    assert s["name"] == "Edward M Cox"
    assert s["date"] == "2014-10-10"
    assert s["signed_by"] == "John J Farley"


def test_normalize_name():
    assert personnel.normalize_name("JOHN J JENKINS") == "John J Jenkins"
    assert personnel.person_slug("John J Jenkins") == "john-j-jenkins"


def test_aggregate_and_dossier():
    rows = [
        {"name": "JOHN J JENKINS", "date": "2014-07-31", "application_number": "NDA205834",
         "review_type": "summary", "doc_subtype": "Summary Review", "brand_name": "SOVALDI"},
        {"name": "JOHN J JENKINS", "date": "2015-01-02", "application_number": "NDA206000",
         "review_type": "summary", "doc_subtype": "Summary Review"},
    ]
    people = personnel.aggregate(rows)
    assert "john-j-jenkins" in people
    p = people["john-j-jenkins"]
    assert p["n_signed_reviews"] == 2
    assert p["review_disciplines"] == ["summary"]
    md = personnel.dossier_markdown(p)
    assert "# John J Jenkins" in md
    assert "Signed reviews (2)" in md
    assert "SOVALDI" in md
