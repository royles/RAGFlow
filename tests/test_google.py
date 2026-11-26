"""Tests for the Google Drive OAuth connection and importing files from it."""

import os
import stat
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import google_drive
from backend.vectors import close_client
from tests import fake_google
from tests.helpers import Server, shared_app_server

CALLBACK = "http://127.0.0.1:8000/api/google/callback"


class GoogleDriveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()
        cls.google = Server(fake_google.app).start()

    @classmethod
    def tearDownClass(cls):
        cls.google.stop()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "RAGFLOW_MAX_UPLOAD_MB"):
            os.environ.pop(name, None)
        os.environ["GOOGLE_OAUTH_AUTH_URL"] = self.google.url + "/auth"
        os.environ["GOOGLE_OAUTH_TOKEN_URL"] = self.google.url + "/token"
        os.environ["GOOGLE_OAUTH_REVOKE_URL"] = self.google.url + "/revoke"
        os.environ["GOOGLE_DRIVE_API_URL"] = self.google.url + "/drive/v3"
        fake_google.reset()
        self.http = httpx.Client(base_url=self.app.url, timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()
        for name in ("GOOGLE_OAUTH_AUTH_URL", "GOOGLE_OAUTH_TOKEN_URL", "GOOGLE_OAUTH_REVOKE_URL", "GOOGLE_DRIVE_API_URL"):
            os.environ.pop(name, None)

    def configure(self):
        response = self.http.put(
            "/api/google", json={"client_id": fake_google.CLIENT_ID, "client_secret": fake_google.CLIENT_SECRET}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start_sign_in(self, redirect_uri=CALLBACK):
        response = self.http.post("/api/google/connect", json={"redirect_uri": redirect_uri})
        self.assertEqual(response.status_code, 200, response.text)
        url = urlparse(response.json()["url"])
        return {key: values[0] for key, values in parse_qs(url.query).items()}

    def connect(self):
        self.configure()
        query = self.start_sign_in()
        code = fake_google.issue_code(query)
        response = self.http.get("/api/google/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/#/google?connected=1")

    def kb(self):
        return self.http.post("/api/knowledge-bases", json={"name": "Drive base"}).json()

    def test_starts_unconfigured_and_never_returns_the_secret(self):
        status = self.http.get("/api/google").json()
        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])
        saved = self.configure()
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["client_id"], fake_google.CLIENT_ID)
        self.assertTrue(saved["client_secret_set"])
        self.assertNotIn(fake_google.CLIENT_SECRET, self.http.get("/api/google").text)
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp.name, "google_drive.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_blank_secret_keeps_the_saved_one(self):
        self.configure()
        again = self.http.put("/api/google", json={"client_id": fake_google.CLIENT_ID, "client_secret": ""}).json()
        self.assertTrue(again["configured"])

    def test_sign_in_asks_for_read_only_access_with_pkce(self):
        self.configure()
        query = self.start_sign_in()
        self.assertEqual(query["scope"], "https://www.googleapis.com/auth/drive.readonly")
        self.assertEqual(query["client_id"], fake_google.CLIENT_ID)
        self.assertEqual(query["redirect_uri"], CALLBACK)
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["access_type"], "offline")
        self.assertTrue(query["state"] and query["code_challenge"])

    def test_sign_in_needs_credentials_and_a_valid_redirect(self):
        self.assertEqual(self.http.post("/api/google/connect", json={"redirect_uri": CALLBACK}).status_code, 400)
        self.configure()
        for bad in ("", "https://evil.example/steal", "javascript:alert(1)//api/google/callback"):
            self.assertEqual(self.http.post("/api/google/connect", json={"redirect_uri": bad}).status_code, 400, bad)

    def test_callback_connects_and_shows_the_account(self):
        self.connect()
        status = self.http.get("/api/google").json()
        self.assertTrue(status["connected"])
        self.assertEqual(status["account"], {"email": "dana@example.com", "name": "Dana Test"})
        self.assertNotIn("rt-1", self.http.get("/api/google").text)

    def test_callback_rejects_unknown_or_reused_state(self):
        self.configure()
        query = self.start_sign_in()
        code = fake_google.issue_code(query)
        bad = self.http.get("/api/google/callback", params={"code": code, "state": "forged"}, follow_redirects=False)
        self.assertIn("error=", bad.headers["location"])
        self.assertFalse(self.http.get("/api/google").json()["connected"])
        good = self.http.get("/api/google/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)
        self.assertEqual(good.headers["location"], "/#/google?connected=1")
        replay = self.http.get("/api/google/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)
        self.assertIn("error=", replay.headers["location"])

    def test_callback_reports_a_cancelled_sign_in(self):
        self.configure()
        response = self.http.get("/api/google/callback", params={"error": "access_denied"}, follow_redirects=False)
        self.assertIn("cancelled", response.headers["location"])
        self.assertFalse(self.http.get("/api/google").json()["connected"])

    def test_wrong_pkce_verifier_is_refused_by_google(self):
        self.configure()
        query = self.start_sign_in()
        query["code_challenge"] = "tampered"
        code = fake_google.issue_code(query)
        response = self.http.get("/api/google/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)
        self.assertIn("error=", response.headers["location"])
        self.assertFalse(self.http.get("/api/google").json()["connected"])

    def test_browsing_needs_a_connection(self):
        self.assertEqual(self.http.get("/api/google/files").status_code, 400)

    def test_browse_folders_search_and_shared(self):
        self.connect()
        root = self.http.get("/api/google/files").json()["files"]
        by_name = {item["name"]: item for item in root}
        self.assertTrue(by_name["Handbook"]["is_folder"])
        self.assertTrue(by_name["notes.txt"]["importable"])
        self.assertTrue(by_name["Quarterly plan"]["importable"])
        self.assertTrue(by_name["Budget"]["importable"])
        self.assertFalse(by_name["photo.png"]["importable"])
        self.assertFalse(by_name["Survey"]["importable"])
        inside = self.http.get("/api/google/files", params={"parent": "folder1"}).json()["files"]
        self.assertEqual([item["name"] for item in inside], ["policy.md"])
        found = self.http.get("/api/google/files", params={"q": "NOTES"}).json()["files"]
        self.assertEqual([item["name"] for item in found], ["notes.txt"])
        shared = self.http.get("/api/google/files", params={"parent": "shared"}).json()["files"]
        self.assertEqual([item["name"] for item in shared], ["shared.txt"])

    def test_search_text_cannot_break_out_of_the_query(self):
        self.connect()
        self.http.get("/api/google/files", params={"q": "x' or name contains '"})
        query = [call["q"] for call in fake_google.calls if call["path"] == "list"][-1]
        self.assertIn("x\\' or name contains \\'", query)

    def test_import_a_text_file_and_ask_about_it(self):
        self.connect()
        kb = self.kb()
        response = self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": "f_notes"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["filename"], "notes.txt")
        documents = self.http.get(f"/api/knowledge-bases/{kb['id']}/documents").json()["documents"]
        self.assertEqual([item["filename"] for item in documents], ["notes.txt"])
        answer = self.http.post(f"/api/knowledge-bases/{kb['id']}/search", json={"question": "Dublin warehouse code"}).json()
        self.assertIn("DUB-14", answer["citations"][0]["text"])

    def test_google_docs_and_sheets_are_exported_as_text(self):
        self.connect()
        kb = self.kb()
        names = []
        for file_id in ("g_doc", "g_sheet"):
            names.append(self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": file_id}).json()["filename"])
        self.assertEqual(names, ["Quarterly plan.txt", "Budget.csv"])
        exports = {call["mimeType"] for call in fake_google.calls if call["path"] == "export"}
        self.assertEqual(exports, {"text/plain", "text/csv"})

    def test_unsupported_files_are_refused_with_a_reason(self):
        self.connect()
        kb = self.kb()
        for file_id in ("f_photo", "g_form", "folder1"):
            response = self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": file_id})
            self.assertEqual(response.status_code, 400, file_id)
        self.assertEqual(self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": "../etc"}).status_code, 400)
        self.assertEqual(self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": "missing"}).status_code, 400)

    def test_upload_size_limit_applies_before_downloading(self):
        self.connect()
        kb = self.kb()
        os.environ["RAGFLOW_MAX_UPLOAD_MB"] = "1"
        fake_google.FILES["f_big"] = {"name": "big.txt", "mimeType": "text/plain", "parents": ["root"], "body": b"x" * (2 * 1024 * 1024)}
        try:
            response = self.http.post(f"/api/knowledge-bases/{kb['id']}/documents/google-drive", json={"file_id": "f_big"})
        finally:
            del fake_google.FILES["f_big"]
        self.assertEqual(response.status_code, 400)
        self.assertIn("1 MB", response.json()["detail"])
        self.assertFalse([call for call in fake_google.calls if call["path"] == "download"])

    def test_an_expired_access_token_is_refreshed_and_the_request_retried(self):
        self.connect()
        fake_google.expire_access_tokens()
        files = self.http.get("/api/google/files")
        self.assertEqual(files.status_code, 200, files.text)
        self.assertIn("refresh_token", [call["grant"] for call in fake_google.calls if call["path"] == "token"])

    def test_a_revoked_grant_signs_out_with_a_clear_message(self):
        self.connect()
        fake_google.expire_access_tokens()
        fake_google.state["refresh_revoked"] = True
        response = self.http.get("/api/google/files")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Connect again", response.json()["detail"])
        self.assertFalse(self.http.get("/api/google").json()["connected"])

    def test_disconnect_revokes_and_forgets(self):
        self.connect()
        status = self.http.post("/api/google/disconnect").json()
        self.assertFalse(status["connected"])
        self.assertTrue(status["configured"])
        self.assertEqual(len(fake_google.revoked), 1)
        self.assertEqual(self.http.get("/api/google/files").status_code, 400)

    def test_changing_the_client_id_drops_the_old_sign_in(self):
        self.connect()
        self.http.put("/api/google", json={"client_id": "another-client", "client_secret": ""})
        self.assertFalse(self.http.get("/api/google").json()["connected"])

    def test_environment_credentials_are_used_when_nothing_is_saved(self):
        os.environ["GOOGLE_CLIENT_ID"] = fake_google.CLIENT_ID
        os.environ["GOOGLE_CLIENT_SECRET"] = fake_google.CLIENT_SECRET
        try:
            status = self.http.get("/api/google").json()
            self.assertTrue(status["configured"])
            self.assertEqual(status["client_source"], "environment")
            self.assertNotIn(fake_google.CLIENT_SECRET, str(status))
        finally:
            del os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"]

    def test_the_sign_in_window_expires(self):
        self.configure()
        query = self.start_sign_in()
        for item in google_drive._pending.values():
            item["expires"] = 0
        code = fake_google.issue_code(query)
        response = self.http.get("/api/google/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)
        self.assertIn("expired", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
