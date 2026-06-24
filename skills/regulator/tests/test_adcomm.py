from regulator.sources import adcomm

PAGE = """
<html><body>
<h1>July 25, 2024: Meeting of the Oncologic Drugs Advisory Committee</h1>
<ul>
  <li><a href="/media/180266/download">FDA Briefing Document</a></li>
  <li><a href="https://www.fda.gov/media/180270/download">Applicant Briefing Document</a></li>
  <li><a href="/media/180300/download">Committee Roster</a></li>
  <li><a href="/media/180310/download">Meeting Agenda</a></li>
  <li><a href="/media/180400/download">Transcript</a></li>
  <li><a href="/some/other/page">Not a document</a></li>
  <li><a href="/media/180266/download">FDA Briefing Document (dup link)</a></li>
</ul>
</body></html>
"""


def test_classify_material():
    assert adcomm.classify_material("FDA Briefing Document") == "briefing"
    assert adcomm.classify_material("Committee Roster") == "roster"
    assert adcomm.classify_material("Meeting Transcript") == "transcript"
    assert adcomm.classify_material("Meeting Agenda") == "agenda"
    assert adcomm.classify_material("Slide Presentation") == "presentation"


def test_guess_committee():
    name, abbr = adcomm.guess_committee("/advisory-committees/oncologic-drugs-advisory-committee/2024-...")
    assert abbr == "ODAC"


def test_guess_meeting_date():
    assert adcomm.guess_meeting_date("Meeting of July 25, 2024 was held") == "2024-07-25"


def test_clean_title():
    raw = "06. September 26, 2024 Meeting of the Oncologic Drugs Advisory Committee- AM- FDA Briefing Document"
    assert adcomm._clean_title(raw) == "AM- FDA Briefing Document"
    assert adcomm._clean_title("Final Agenda") == "Final Agenda"


HUB = """
<a href="/advisory-committees/oncologic-drugs-advisory-committee/2024-meeting-materials-oncologic-drugs-advisory-committee">2024</a>
<a href="/advisory-committees/advisory-committee-calendar/september-26-2024-meeting-odac-announcement-09262024">Sept 26</a>
<a href="/advisory-committees/advisory-committee-calendar/march-14-2024-meeting-odac-announcement-03142024">Mar 14</a>
<a href="/advisory-committees/oncologic-drugs-advisory-committee/oncologic-drugs-advisory-committee-roster">Roster</a>
<a href="/safety">Safety</a>
"""


def test_extract_meeting_links():
    links = adcomm.extract_meeting_links(HUB)
    assert len(links) == 2
    assert all("advisory-committee-calendar" in u for u in links)
    assert all(u.startswith("https://www.fda.gov") for u in links)


def test_extract_materials():
    mats = adcomm.extract_materials(PAGE, page_url="https://www.fda.gov/advisory-committees/oncologic-drugs/x")
    # 5 unique document links (dup collapsed, non-media skipped)
    assert len(mats) == 5
    types = {m["material_type"] for m in mats}
    assert {"briefing", "roster", "agenda", "transcript"} <= types
    brief = next(m for m in mats if m["title"].startswith("FDA Briefing"))
    assert brief["committee_abbr"] == "ODAC"
    assert brief["meeting_date"] == "2024-07-25"
    assert brief["media_id"] == "180266"
    assert brief["doc_url"] == "https://www.fda.gov/media/180266/download"
