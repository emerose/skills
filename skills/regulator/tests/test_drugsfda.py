from regulator.sources import drugsfda

# A trimmed openFDA drug/drugsfda record (real shape, two submissions).
APP = {
    "application_number": "NDA205834",
    "sponsor_name": "GILEAD SCIENCES INC",
    "products": [{
        "brand_name": "SOVALDI",
        "active_ingredients": [{"name": "SOFOSBUVIR", "strength": "400MG"}],
        "route": "ORAL", "marketing_status": "Prescription",
    }],
    "openfda": {"generic_name": ["SOFOSBUVIR"], "pharm_class_epc": ["Hepatitis C Virus NS5B Polymerase Inhibitor [EPC]"]},
    "submissions": [
        {"submission_type": "ORIG", "submission_number": "1", "submission_status": "AP",
         "submission_status_date": "20131206", "submission_class_code_description": "Type 1 - New Molecular Entity",
         "application_docs": [
             {"id": "1", "type": "Letter", "date": "20131206",
              "url": "http://www.accessdata.fda.gov/drugsatfda_docs/appletter/2013/205834Orig1s000ltr.pdf"},
             {"id": "2", "type": "Review", "date": "20140101",
              "url": "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2014/205834Orig1s000MedR.pdf"},
             {"id": "3", "type": "Review", "date": "20140101",
              "url": "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2014/205834Orig1s000ClinPharmR.pdf"},
             {"id": "4", "type": "Label", "date": "20131206",
              "url": "https://www.accessdata.fda.gov/drugsatfda_docs/label/2013/205834s000lbl.pdf"},
         ]},
        {"submission_type": "SUPPL", "submission_number": "17", "submission_status": "AP",
         "submission_status_date": "20170101",
         "application_docs": [
             {"id": "5", "type": "Summary Review", "date": "20170101",
              "url": "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2017/205834Orig1s017SumR.pdf"},
         ]},
    ],
}


def test_classify_doc_by_filename():
    assert drugsfda.classify_doc("x/205834Orig1s000MedR.pdf")[0] == "medical"
    assert drugsfda.classify_doc("x/205834Orig1s000ClinPharmR.pdf")[0] == "clinpharm"
    assert drugsfda.classify_doc("x/205834Orig1s000StatR.pdf")[0] == "statistical"
    assert drugsfda.classify_doc("x/205834Orig1s000MultidisciplineR.pdf")[0] == "multidiscipline"
    assert drugsfda.classify_doc("x/205834Orig1s000ltr.pdf")[0] == "letter"
    assert drugsfda.classify_doc("x/205834s000lbl.pdf")[0] == "label"


def test_classify_doc_fallback_to_type():
    assert drugsfda.classify_doc("x/weird12345.pdf", "Summary Review")[0] == "summary"


def test_parse_application():
    s = drugsfda.parse_application(APP)
    assert s["application_number"] == "NDA205834"
    assert s["brand_names"] == ["SOVALDI"]
    assert s["active_ingredients"] == ["SOFOSBUVIR"]
    assert s["first_approval"] == "20131206"
    assert s["latest_approval"] == "20170101"


def test_enumerate_docs():
    docs = drugsfda.enumerate_docs(APP)
    assert len(docs) == 5
    med = next(d for d in docs if d["review_type"] == "medical")
    assert med["application_number"] == "NDA205834"
    assert med["submission"] == "s000"
    assert med["brand_name"] == "SOVALDI"
    assert med["generic_name"] == "SOFOSBUVIR"
    assert med["application_kind"] == "NDA"
    assert med["doc_url"].endswith("205834Orig1s000MedR.pdf")
    # SUPPL/17 -> s017
    summ = next(d for d in docs if d["review_type"] == "summary")
    assert summ["submission"] == "s017"
    assert summ["approval_date"] == "20170101"


def test_enumerate_docs_all_pdf():
    docs = drugsfda.enumerate_docs(APP)
    assert all(d["is_pdf"] for d in docs)
    # citekey field components are present for downstream make_citekey
    from regulator import meta
    cks = {meta.make_citekey(d) for d in docs}
    assert "NDA205834-s000-medical" in cks
