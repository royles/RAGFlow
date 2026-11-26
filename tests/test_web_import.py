"""Tests for adding documents from a web address or pasted text."""

import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import google_drive, web_import
from backend.errors import BadRequest
from backend.extract import extract, read_html
from backend.vectors import close_client
from tests.helpers import shared_app_server

PAGE = b"""<!doctype html><html><head><title>  Returns   policy </title><style>p{color:red}</style>
<script>var secret = "tracking";</script></head><body><nav>Home | Pricing</nav>
<h1>Returns policy</h1><p>Refunds over 500 pounds need a <b>manager's</b> approval.</p>
<ul><li>Keep the receipt</li><li>Within 30 days</li></ul><footer>Copyright</footer></body></html>"""

ADDRESSES = {
    "docs.example.test": "93.184.216.34",
    "internal.example.test": "10.1.2.3",
    "loop.example.test": "127.0.0.1",
}


def resolver(host, *args, **kwargs):
    ip = ADDRESSES.get(host) or host
    return [(2, 1, 6, "", (ip, 0))]


@contextmanager
def web(handler):
    """Serve pages from `handler` as if the names in ADDRESSES were on the internet."""
    with mock.patch.object(web_import, "_transport", httpx.MockTransport(handler)), mock.patch.object(
        web_import.socket, "getaddrinfo", resolver
    ):
        yield


def respond(body=b"", status=200, **headers):
    return lambda request: httpx.Response(status, content=body, headers=headers)


class AddressTests(unittest.TestCase):
    def test_addresses_are_cleaned_and_unsafe_ones_refused(self):
        self.assertEqual(web_import.normalise(" example.com/a "), "https://example.com/a")
        for bad in ("", "ftp://example.com/x", "file:///etc/passwd", "https://user:pw@example.com/", "https://"):
            with self.assertRaises(BadRequest, msg=bad):
                web_import.normalise(bad)

    def test_private_loopback_and_metadata_addresses_are_not_fetched(self):
        for host in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9", "169.254.169.254", "100.64.0.1", "::1", "::ffff:127.0.0.1", "0.0.0.0"):
            with web(respond(b"x")):
                with self.assertRaises(BadRequest, msg=host) as caught:
                    web_import.fetch(f"http://{host}/secret" if ":" not in host else f"http://[{host}]/secret")
            self.assertIn("private network", str(caught.exception), host)

    def test_a_public_address_is_allowed_and_the_setting_opens_private_ones(self):
        with web(respond(b"hello", **{"content-type": "text/plain"})):
            self.assertEqual(web_import.fetch("https://docs.example.test/a.txt").data, b"hello")
            with self.assertRaises(BadRequest):
                web_import.fetch("https://internal.example.test/a.txt")
            with mock.patch.dict(os.environ, {"RAGFLOW_URL_ALLOW_PRIVATE": "1"}):
                self.assertEqual(web_import.fetch("https://internal.example.test/a.txt").data, b"hello")

    def test_a_redirect_to_a_private_address_is_refused(self):
        def handler(request):
            if request.url.host == "docs.example.test":
                return httpx.Response(302, headers={"location": "http://loop.example.test/admin"})
            return httpx.Response(200, content=b"internal")

        with web(handler):
            with self.assertRaises(BadRequest) as caught:
                web_import.fetch("https://docs.example.test/go")
        self.assertIn("private network", str(caught.exception))

    def test_redirects_are_followed_a_few_times_but_not_forever(self):
        def handler(request):
            step = int(request.url.params.get("n", "0"))
            if request.url.path == "/loop" or step < 3:
                return httpx.Response(302, headers={"location": f"/hop?n={step + 1}" if request.url.path != "/loop" else "/loop"})
            return httpx.Response(200, content=b"arrived", headers={"content-type": "text/plain"})

        with web(handler):
            self.assertEqual(web_import.fetch("https://docs.example.test/hop").data, b"arrived")
            with self.assertRaises(BadRequest) as caught:
                web_import.fetch("https://docs.example.test/loop")
        self.assertIn("redirects", str(caught.exception))

    def test_an_error_status_is_explained(self):
        with web(respond(b"nope", status=404)):
            with self.assertRaises(BadRequest) as caught:
                web_import.fetch("https://docs.example.test/missing")
        self.assertIn("HTTP 404", str(caught.exception))


class TypeTests(unittest.TestCase):
    def fetch(self, body, url="https://docs.example.test/file", **headers):
        with web(respond(body, **headers)):
            return web_import.fetch(url)

    def test_a_web_page_is_named_by_its_title_and_read_without_menus_or_scripts(self):
        fetched = self.fetch(PAGE, **{"content-type": "text/html; charset=utf-8"})
        self.assertEqual(fetched.filename, "Returns policy.html")
        text = extract(fetched.filename, fetched.data).text
        self.assertIn("Refunds over 500 pounds need a manager's approval.", text)
        self.assertIn("- Keep the receipt", text)
        for hidden in ("tracking", "Pricing", "Copyright", "color:red"):
            self.assertNotIn(hidden, text)

    def test_the_title_and_text_of_html_can_be_read_directly(self):
        title, text = read_html(PAGE)
        self.assertEqual(title, "Returns policy")
        self.assertTrue(text.startswith("Returns policy"))

    def test_pdf_by_type_by_suffix_and_by_content(self):
        pdf = b"%PDF-1.4 fake"
        self.assertEqual(self.fetch(pdf, "https://docs.example.test/report", **{"content-type": "application/pdf"}).filename, "report.pdf")
        self.assertEqual(self.fetch(pdf, "https://docs.example.test/a/report.pdf", **{"content-type": "application/octet-stream"}).filename, "report.pdf")
        self.assertEqual(self.fetch(pdf, "https://docs.example.test/download").filename, "download.pdf")

    def test_plain_text_served_for_a_markdown_or_csv_file_keeps_its_suffix(self):
        self.assertEqual(self.fetch(b"# Hi", "https://docs.example.test/README.md", **{"content-type": "text/plain"}).filename, "README.md")
        self.assertEqual(self.fetch(b"a,b", "https://docs.example.test/t.csv", **{"content-type": "text/plain"}).filename, "t.csv")
        self.assertEqual(self.fetch(b"just words", "https://docs.example.test/notes", **{"content-type": "text/plain"}).filename, "notes.txt")

    def test_the_download_name_beats_the_path(self):
        fetched = self.fetch(
            b"x,y", "https://docs.example.test/get?id=7", **{"content-type": "text/csv", "content-disposition": 'attachment; filename="Q3 sales.csv"'}
        )
        self.assertEqual(fetched.filename, "Q3 sales.csv")

    def test_unsupported_types_and_empty_replies_are_explained(self):
        with self.assertRaises(BadRequest) as caught:
            self.fetch(b"\x89PNG....", "https://docs.example.test/logo.png", **{"content-type": "image/png"})
        self.assertIn("image/png", str(caught.exception))
        with self.assertRaises(BadRequest):
            self.fetch(b"", **{"content-type": "text/plain"})

    def test_a_download_over_the_upload_limit_is_stopped(self):
        with mock.patch.dict(os.environ, {"RAGFLOW_MAX_UPLOAD_MB": "1"}):
            with self.assertRaises(BadRequest) as caught:
                self.fetch(b"a" * (1024 * 1024 + 10), **{"content-type": "text/plain"})
        self.assertIn("larger than 1 MB", str(caught.exception))

    def test_names_are_made_safe(self):
        self.assertEqual(web_import.clean_name("a/b:c*?", ".txt"), "a b c.txt")
        self.assertEqual(web_import.clean_name("", ".html"), "document.html")
        self.assertLessEqual(len(web_import.clean_name("x" * 500, ".pdf")), web_import.MAX_NAME)

    def test_google_links_are_recognised(self):
        self.assertEqual(google_drive.id_from_url("https://docs.google.com/document/d/1AbC_d-E/edit"), "1AbC_d-E")
        self.assertEqual(google_drive.id_from_url("https://drive.google.com/file/d/XyZ123/view?usp=sharing"), "XyZ123")
        self.assertEqual(google_drive.id_from_url("https://drive.google.com/open?id=Q9_w"), "Q9_w")
        self.assertEqual(google_drive.id_from_url("https://example.com/document/d/abc"), "")


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=self.app.url, timeout=60)
        self.kb = self.http.post("/api/knowledge-bases", json={"name": "Web", "chunk_size": 100, "chunk_overlap": 0}).json()
        self.base = f"/api/knowledge-bases/{self.kb['id']}"

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def test_a_page_is_added_with_its_address_and_cited_with_it(self):
        with web(respond(PAGE, **{"content-type": "text/html"})):
            response = self.http.post(f"{self.base}/documents/url", json={"url": "docs.example.test/returns", "upload_id": "u1"})
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual((row["filename"], row["source_url"]), ("Returns policy.html", "https://docs.example.test/returns"))
        listed = self.http.get(f"{self.base}/documents").json()["documents"]
        self.assertEqual(listed[0]["source_url"], "https://docs.example.test/returns")
        hit = self.http.post(f"{self.base}/search", json={"question": "manager approval refunds", "top_k": 1}).json()["citations"][0]
        self.assertEqual(hit["source_url"], "https://docs.example.test/returns")
        self.assertEqual(hit["location"], "")
        self.assertEqual(self.http.get("/api/uploads/u1").json()["phase"], "done")

    def test_a_blocked_or_failing_address_is_a_clear_error_and_adds_nothing(self):
        with web(respond(b"x")):
            blocked = self.http.post(f"{self.base}/documents/url", json={"url": "http://internal.example.test/a", "upload_id": "u2"})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("private network", blocked.text)
        state = self.http.get("/api/uploads/u2").json()
        self.assertEqual(state["phase"], "failed")
        self.assertIn("private network", state["error"])
        self.assertEqual(self.http.post(f"{self.base}/documents/url", json={"url": ""}).status_code, 400)
        self.assertEqual(self.http.get(f"{self.base}/documents").json()["documents"], [])

    def test_a_google_docs_link_uses_the_drive_connection(self):
        with mock.patch.object(google_drive, "fetch", return_value=("Plan.txt", b"The launch is in May.")) as fetch:
            response = self.http.post(f"{self.base}/documents/url", json={"url": "https://docs.google.com/document/d/AbC123/edit"})
        self.assertEqual(response.status_code, 201, response.text)
        fetch.assert_called_once_with("AbC123")
        self.assertEqual(response.json()["filename"], "Plan.txt")

    def test_pasted_text_is_added_as_a_text_document_with_lines(self):
        response = self.http.post(f"{self.base}/documents/text", json={"title": "Dock notes: Dublin/Cork", "text": "Dock 4 closes at six.\nDock 5 is for returns."})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["filename"], "Dock notes Dublin Cork.txt")
        hit = self.http.post(f"{self.base}/search", json={"question": "Dock 5 returns", "top_k": 1}).json()["citations"][0]
        self.assertEqual(hit["filename"], "Dock notes Dublin Cork.txt")
        self.assertTrue(hit["location"].startswith("line"))

    def test_untitled_text_gets_a_dated_name_and_empty_text_is_refused(self):
        response = self.http.post(f"{self.base}/documents/text", json={"text": "Something worth keeping."})
        self.assertTrue(response.json()["filename"].startswith("Pasted text "))
        self.assertEqual(self.http.post(f"{self.base}/documents/text", json={"title": "x", "text": "   "}).status_code, 400)

    def test_an_html_file_can_be_uploaded_too(self):
        response = self.http.post(f"{self.base}/documents", files={"file": ("page.html", PAGE)})
        self.assertEqual(response.status_code, 201, response.text)


if __name__ == "__main__":
    unittest.main()
