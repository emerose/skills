"""Tests for the file organizer (sanitization, author tree, collision handling)."""

import _fileorg


def test_sanitize():
    assert _fileorg.sanitize('a/b:c?d') == "a b c d"      # illegal chars -> space, collapsed
    assert _fileorg.sanitize("Title.") == "Title"          # no trailing dot
    assert _fileorg.sanitize("   ") == "untitled"          # empty -> placeholder
    assert len(_fileorg.sanitize("x " * 200, maxlen=50)) <= 50


def test_author_dir():
    assert _fileorg.author_dir({"authors": [{"family": "Vaswani", "given": "Ashish"}]}) == "Vaswani, Ashish"
    assert _fileorg.author_dir({"authors_text": "Doe, Jane; Smith, J"}) == "Doe, Jane"
    assert _fileorg.author_dir({}) == "Unknown"


def test_filename_format():
    fn = _fileorg.filename({"authors_text": "Vaswani, Ashish", "year": 2017, "title": "Attention"}, ".pdf")
    assert fn == "Vaswani (2017) - Attention.pdf"
    # no year -> n.d.
    assert _fileorg.filename({"authors_text": "Doe, J", "title": "X"}, ".pdf") == "Doe (n.d.) - X.pdf"


def test_plan_path_collision(tmp_path):
    rec = {"citekey": "v2017", "authors": [{"family": "V", "given": "A"}], "year": 2017, "title": "T"}
    p1 = _fileorg.plan_path(tmp_path, rec, ".pdf")
    assert p1.parent.name == "V, A"
    p1.parent.mkdir(parents=True)
    p1.write_text("x")
    p2 = _fileorg.plan_path(tmp_path, rec, ".pdf")
    assert p2 != p1 and "v2017" in p2.name  # collision -> citekey appended


def test_place_copy_and_move(tmp_path):
    src = tmp_path / "incoming.pdf"
    src.write_bytes(b"%PDF-x")
    rec = {"citekey": "v2017", "authors": [{"family": "V", "given": "A"}], "year": 2017, "title": "T"}
    dest = _fileorg.place(tmp_path, rec, src, move=False)
    assert dest.exists() and src.exists()  # copy leaves the original
    src2 = tmp_path / "incoming2.pdf"
    src2.write_bytes(b"%PDF-y")
    rec2 = {**rec, "citekey": "v2017b", "title": "U"}
    dest2 = _fileorg.place(tmp_path, rec2, src2, move=True)
    assert dest2.exists() and not src2.exists()  # move consumes the original
