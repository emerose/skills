"""Unit tests for _generate: the Files-on-disk view (the only thing scientist
renders into prose-free output now)."""

from scientist.store import _generate

FILES = [
    {"path": "K1-1/README.md", "role": "readme", "file_type": "md"},
    {"path": "K1-1/data/kd.csv", "role": "data", "file_type": "csv"},
    {"path": "K1-1/raw/plate.eds", "role": "raw", "file_type": "eds"},
    {"path": "K1-1/reports/report.pdf", "role": "report", "file_type": "pdf"},
]


def test_files_on_disk_table_grouped():
    t = _generate.files_on_disk_table(FILES)
    assert "`kd.csv`" in t                       # basenames, grouped by role
    assert "**data** (1)" in t
    # readme sorts before data before raw before report
    assert t.index("README.md") < t.index("kd.csv") < t.index("plate.eds") < t.index("report.pdf")


def test_files_on_disk_table_summarizes_large_roles():
    many = [{"path": f"K1-1/raw/m{i:03d}.csv", "role": "raw", "file_type": "csv"}
            for i in range(50)]
    t = _generate.files_on_disk_table(many, list_threshold=12)
    assert "**raw** (50)" in t
    assert "50×csv" in t                          # summarised, not 50 rows
    assert "e.g." in t and t.count("`") <= 8      # only a few examples shown


def test_files_on_disk_table_notes_duplicates():
    files = [{"path": "K1-1/protocol/a.docx", "role": "protocol", "file_type": "docx",
              "other_paths": ["K1-1/reports/copy.docx"]}]
    t = _generate.files_on_disk_table(files)
    assert "duplicate copies also on disk" in t
    assert "`copy.docx`" in t


def test_files_on_disk_empty():
    assert "No files" in _generate.files_on_disk_table([])
