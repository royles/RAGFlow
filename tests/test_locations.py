"""Tests for knowing where a chunk came from: its page in a PDF, its line in a text or CSV file."""

import os
import tempfile
import unittest

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend.chunking import chunk_located, chunk_text
from backend.extract import describe, extract
from backend.vectors import close_client
from tests.helpers import shared_app_server


def make_pdf(pages: list[str | None]) -> bytes:
    """A minimal PDF with one line of text per page. None makes a blank page."""
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for number, text in enumerate(pages):
        page_id, content_id = 4 + number * 2, 5 + number * 2
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode() if text else b""
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out, offsets = b"%PDF-1.4\n", {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += f"{key} 0 obj\n".encode() + objects[key] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for key in sorted(objects):
        out += f"{offsets[key]:010d} 00000 n \n".encode()
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def page_of(word: str) -> str:
    """Exactly 98 characters, so with the blank line between pages a 100-character chunk is one page."""
    return ((word + "-") * 40)[:98]


class ExtractTests(unittest.TestCase):
    def test_a_pdf_keeps_its_page_boundaries_and_skips_blank_pages(self):
        document = extract("a.pdf", make_pdf(["Alpha page one", None, "Gamma page three"]))
        self.assertEqual(document.text, "Alpha page one\n\nGamma page three")
        self.assertEqual([page for _, _, page in document.pages], [1, 3])
        self.assertEqual(document.locate(0, 5)["page_start"], 1)
        where = document.locate(0, len(document.text))
        self.assertEqual((where["page_start"], where["page_end"]), (1, 3))
        third = document.locate(document.text.index("Gamma"), len(document.text))
        self.assertEqual((third["page_start"], third["page_end"]), (3, 3))

    def test_a_csv_reports_physical_lines_with_the_header_as_line_one(self):
        document = extract("t.csv", b"name,city\nAnn,Dublin\nBob,Cork\nCy,Galway\n")
        for needle, line in (("name", 1), ("Ann", 2), ("Bob", 3), ("Galway", 4)):
            start = document.text.index(needle)
            where = document.locate(start, start + len(needle))
            self.assertEqual((where["line_start"], where["line_end"]), (line, line), needle)
        self.assertEqual(document.locate(0, len(document.text))["line_end"], 4)

    def test_leading_blank_lines_and_windows_newlines_still_count(self):
        document = extract("t.txt", b"\r\n\r\nfirst\r\nsecond\r\n")
        self.assertEqual(document.text, "first\nsecond")
        self.assertEqual(document.locate(0, 5)["line_start"], 3)
        self.assertEqual(document.locate(6, 12)["line_start"], 4)

    def test_docx_and_unknown_positions_have_no_page_or_line(self):
        where = extract("n.md", b"# Title").locate(0, 3)
        self.assertEqual(where["line_start"], 1)
        from backend.extract import Extracted

        self.assertEqual(Extracted("abc").locate(0, 3), {"char_start": 0, "char_end": 3})

    def test_labels(self):
        self.assertEqual(describe({"page_start": 12, "page_end": 12}), "p. 12")
        self.assertEqual(describe({"page_start": 12, "page_end": 13}), "pp. 12\u201313")
        self.assertEqual(describe({"line_start": 40, "line_end": 40}), "line 40")
        self.assertEqual(describe({"line_start": 40, "line_end": 52}), "lines 40\u201352")
        self.assertEqual(describe({}), "")

    def test_located_chunks_match_the_plain_chunks_and_their_offsets(self):
        text = "  alpha beta gamma delta epsilon zeta eta theta  "
        located = chunk_located(text, 12, 3)
        self.assertEqual([piece for piece, _, _ in located], chunk_text(text, 12, 3))
        cleaned = text.strip()
        for piece, start, end in located:
            self.assertEqual(cleaned[start:end], piece)


class LocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=self.app.url, timeout=60)
        self.kb = self.http.post("/api/knowledge-bases", json={"name": "Where", "chunk_size": 100, "chunk_overlap": 0}).json()

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def upload(self, name, data):
        response = self.http.post(f"/api/knowledge-bases/{self.kb['id']}/documents", files={"file": (name, data)})
        self.assertEqual(response.status_code, 201, response.text)

    def search(self, question):
        response = self.http.post(f"/api/knowledge-bases/{self.kb['id']}/search", json={"question": question, "top_k": 1})
        return response.json()["citations"][0]

    def test_a_cited_pdf_chunk_says_which_page(self):
        self.upload("guide.pdf", make_pdf([page_of("cats"), page_of("dogs"), page_of("birds")]))
        hit = self.search("dogs")
        self.assertEqual((hit["filename"], hit["page_start"], hit["page_end"], hit["location"]), ("guide.pdf", 2, 2, "p. 2"))
        hit = self.search("birds")
        self.assertEqual(hit["location"], "p. 3")

    def test_a_cited_csv_chunk_says_which_lines(self):
        rows = "\n".join(f"{n},customer {n},city{n}" for n in range(1, 60))
        self.upload("sales.csv", ("id,name,city\n" + rows + "\n").encode())
        hit = self.search("customer 40 city40")
        self.assertEqual(hit["filename"], "sales.csv")
        self.assertLessEqual(hit["line_start"], 41)
        self.assertGreaterEqual(hit["line_end"], 41)
        self.assertTrue(hit["location"].startswith("line"))

    def test_the_answer_prompt_and_audit_log_carry_the_location(self):
        self.upload("guide.pdf", make_pdf([page_of("cats"), page_of("dogs")]))
        answer = self.http.post(
            f"/api/knowledge-bases/{self.kb['id']}/ask", json={"question": "dogs", "source": "ui", "top_k": 1}
        ).json()
        self.assertEqual(answer["citations"][0]["location"], "p. 2")
        saved = self.http.get(f"/api/interactions/{answer['interaction_id']}").json()
        self.assertEqual(saved["citations"][0]["page_start"], 2)

    def test_files_without_a_position_have_an_empty_label(self):
        from docx import Document
        import io

        buffer = io.BytesIO()
        document = Document()
        document.add_paragraph("Penguins live in the south")
        document.save(buffer)
        self.upload("facts.docx", buffer.getvalue())
        hit = self.search("Penguins live in the south")
        self.assertEqual(hit["location"], "")
        self.assertNotIn("page_start", hit)
        self.assertNotIn("line_start", hit)


if __name__ == "__main__":
    unittest.main()
