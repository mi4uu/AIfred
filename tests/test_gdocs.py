"""Google Docs/Sheets link reader (V29)."""

import aifred.google.gdocs as g


def test_find_links_all_kinds_and_dedup():
    text = (
        "arkusz https://docs.google.com/spreadsheets/d/ABC123/edit?usp=drivesdk\n"
        "doc https://docs.google.com/document/d/DOC9/edit\n"
        "ten sam https://docs.google.com/spreadsheets/d/ABC123/edit\n"
        "plik https://drive.google.com/file/d/FILE7/view"
    )
    assert g.find_links(text) == [("spreadsheets", "ABC123"), ("document", "DOC9"), ("file", "FILE7")]
    assert g.find_links("brak linków") == []


class FakeReq:
    pass


class FakeFiles:
    def __init__(self, name, mime):
        self._name, self._mime = name, mime
        self.exported = None

    def get(self, fileId=None, fields=None):
        files = self

        class E:
            def execute(_):
                return {"name": files._name, "mimeType": files._mime}

        return E()

    def export_media(self, fileId=None, mimeType=None):
        self.exported = mimeType
        return FakeReq()

    def get_media(self, fileId=None):
        return FakeReq()


class FakeDrive:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def test_read_file_sheet_exports_csv(monkeypatch):
    monkeypatch.setattr(g, "_download", lambda req: b"Data,Wydarzenie\n2026-06-09,Dentysta 16:40\n")
    files = FakeFiles("Plan Zosi", "application/vnd.google-apps.spreadsheet")
    out = g.read_file(FakeDrive(files), "spreadsheets", "ABC123")
    assert out["ok"] and out["title"] == "Plan Zosi"
    assert files.exported == "text/csv"          # sheet -> CSV export
    assert "Dentysta 16:40" in out["text"]


def test_read_url_no_link():
    assert g.read_url(FakeDrive(FakeFiles("x", "y")), "zwykły tekst")["ok"] is False


def test_fetch_all_skips_failures(monkeypatch):
    def boom(req):
        raise RuntimeError("403")

    monkeypatch.setattr(g, "_download", boom)
    files = FakeFiles("X", "application/vnd.google-apps.document")
    assert g.fetch_all(FakeDrive(files), "doc https://docs.google.com/document/d/DOC9/edit") == []
