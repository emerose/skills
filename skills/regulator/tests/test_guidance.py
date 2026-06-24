from regulator.sources import guidance

# DataTables-style feed: rows as objects keyed by column name. The Document cell
# is HTML holding the title + the /media/<id>/download link.
FEED = {
    "data": [
        {
            "document": '<a href="/media/85393/download">Expedited Programs for Serious Conditions</a>',
            "issue_date": "05/2014",
            "fda_org": "CDER",
            "topic": "Procedural",
            "status": "Final",
            "docket_number": "FDA-2013-D-0575",
            "guidance_type": "Final",
        },
        {
            "document": '<a href="https://www.fda.gov/media/167945/download">Rare Diseases: Natural History Studies</a>',
            "issue_date": "03/2019",
            "fda_org": "CDER, CBER",
            "topic": "Clinical/Medical",
            "status": "Draft",
            "docket_number": "FDA-2019-D-1098",
            "guidance_type": "Draft",
        },
    ]
}


def test_parse_rows_objects():
    recs = guidance.parse_rows(FEED)
    assert len(recs) == 2
    a = recs[0]
    assert a["doc_type"] == "guidance"
    assert a["title"] == "Expedited Programs for Serious Conditions"
    assert a["source_url"] == "https://www.fda.gov/media/85393/download"
    assert a["pdf_url"].endswith("/media/85393/download")
    assert a["guidance_id"] == "media-85393"
    assert a["status"] == "Final"
    assert a["docket_number"] == "FDA-2013-D-0575"


def test_parse_rows_array_form():
    arr = {"data": [[
        '<a href="/media/1/download">Title X</a>', "01/2020", "CDRH", "Topic", "Final",
        "No", "", "FDA-2020-D-0001", "Final",
    ]]}
    recs = guidance.parse_rows(arr)
    assert recs[0]["title"] == "Title X"
    assert recs[0]["fda_org"] == "CDRH"
    assert recs[0]["docket_number"] == "FDA-2020-D-0001"


def test_media_pdf_url():
    recs = guidance.parse_rows(FEED)
    assert guidance.media_pdf_url(recs[1]).endswith("/media/167945/download")


def test_search_corpus():
    recs = guidance.parse_rows(FEED)
    hits = guidance.search_corpus(recs, "rare diseases")
    assert len(hits) == 1 and "Rare Diseases" in hits[0]["title"]
    assert guidance.search_corpus(recs, "expedited cder") == [recs[0]]


def test_save_load_roundtrip(tmp_path):
    recs = guidance.parse_rows(FEED)
    guidance.save_corpus(tmp_path, recs)
    again = guidance.load_corpus(tmp_path)
    assert [r["title"] for r in again] == [r["title"] for r in recs]
