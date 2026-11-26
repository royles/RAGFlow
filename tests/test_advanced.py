"""Tests for the advanced overrides: saved, validated, and restored into the environment."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import advanced, mcp_server, vectors
from backend.settings import audit_retention_days, max_chunks, max_upload_bytes
from backend.vectors import close_client
from tests.helpers import shared_app_server

ROOT = Path(__file__).resolve().parent.parent
ENVS = [item["env"] for item in advanced.SETTINGS]


class AdvancedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.before = {name: os.environ.get(name) for name in ENVS}
        for name in ENVS:
            os.environ.pop(name, None)
        self.http = httpx.Client(base_url=self.app.url, timeout=30)

    def tearDown(self):
        keys = [item["key"] for item in advanced.SETTINGS]
        advanced.update({"values": {key: "" for key in keys}, "clear": keys})
        self.http.close()
        close_client()
        self.tmp.cleanup()
        for name, value in self.before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def save(self, values, clear=()):
        response = self.http.put("/api/advanced", json={"values": values, "clear": list(clear)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def setting(self, payload, key):
        return next(item for group in payload["groups"] for item in group["settings"] if item["key"] == key)

    def test_settings_are_grouped_with_an_explanation_each(self):
        payload = self.http.get("/api/advanced").json()
        self.assertEqual(
            [group["name"] for group in payload["groups"]],
            ["Uploads and indexing", "Audit log", "Vector store", "Agents (MCP)"],
        )
        for group in payload["groups"]:
            for item in group["settings"]:
                self.assertTrue(item["help"], item["key"])
                self.assertTrue(item["env"], item["key"])
                self.assertEqual(item["source"], "default")
        self.assertEqual({item["env"] for item in payload["environment"]}, {"RAGFLOW_DATA_DIR", "CDSW_APP_PORT", "CDSW_MASTER_IP"})

    def test_a_saved_value_is_used_and_written_to_the_environment_and_disk(self):
        payload = self.save({"max_chunks": "123", "max_upload_mb": "7", "audit_retention_days": "30"})
        self.assertEqual(self.setting(payload, "max_chunks")["source"], "saved")
        self.assertEqual((max_chunks(), max_upload_bytes(), audit_retention_days()), (123, 7 * 1024 * 1024, 30))
        self.assertEqual(os.environ["RAGFLOW_MAX_CHUNKS"], "123")
        self.assertEqual(os.environ["RAGFLOW_MAX_UPLOAD_MB"], "7")
        on_disk = json.loads((Path(self.tmp.name) / "advanced_settings.json").read_text())
        self.assertEqual(on_disk["max_chunks"], "123")

    def test_a_saved_value_beats_the_environment_and_removing_it_restores_the_environment(self):
        os.environ["RAGFLOW_MAX_CHUNKS"] = "50"
        self.assertEqual(max_chunks(), 50)
        payload = self.save({"max_chunks": "10"})
        item = self.setting(payload, "max_chunks")
        self.assertEqual((max_chunks(), item["source"], item["env_value"]), (10, "saved", "50"))
        self.assertEqual(os.environ["RAGFLOW_MAX_CHUNKS"], "10")
        payload = self.save({"max_chunks": ""})
        self.assertEqual(self.setting(payload, "max_chunks")["source"], "environment")
        self.assertEqual((max_chunks(), os.environ["RAGFLOW_MAX_CHUNKS"]), (50, "50"))

    def test_removing_an_override_unsets_a_variable_that_was_never_set(self):
        self.save({"max_chunks": "10"})
        self.save({"max_chunks": ""})
        self.assertNotIn("RAGFLOW_MAX_CHUNKS", os.environ)
        self.assertEqual(max_chunks(), 0)

    def test_saved_values_are_restored_into_the_environment_on_restart(self):
        self.save({"max_chunks": "321", "qdrant_url": "http://qdrant.example:6333", "qdrant_api_key": "key-1"})
        # What a restart does: a new process has no memory, only the file and its own environment.
        advanced._originals.clear()
        for name in ENVS:
            os.environ.pop(name, None)
        advanced.apply()
        self.assertEqual(os.environ["RAGFLOW_MAX_CHUNKS"], "321")
        self.assertEqual(os.environ["QDRANT_URL"], "http://qdrant.example:6333")
        self.assertEqual(os.environ["QDRANT_API_KEY"], "key-1")

    def test_a_real_restart_reads_the_saved_file(self):
        self.save({"max_chunks": "777", "max_upload_mb": "9"})
        code = (
            "import os, backend.main;"
            "print(os.environ['RAGFLOW_MAX_CHUNKS'], os.environ['RAGFLOW_MAX_UPLOAD_MB'])"
        )
        env = {key: value for key, value in os.environ.items() if key not in ENVS}
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split()[-2:], ["777", "9"])

    def test_the_file_is_private(self):
        self.save({"qdrant_api_key": "key-1"})
        mode = stat.S_IMODE(os.stat(Path(self.tmp.name) / "advanced_settings.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_secrets_are_never_returned_and_a_blank_keeps_them(self):
        payload = self.save({"qdrant_api_key": "super-secret-key"})
        item = self.setting(payload, "qdrant_api_key")
        self.assertTrue(item["is_set"])
        self.assertEqual((item["value"], item["env_value"]), ("", ""))
        self.assertNotIn("super-secret-key", self.http.get("/api/advanced").text)
        self.save({"qdrant_api_key": ""})
        self.assertEqual(os.environ["QDRANT_API_KEY"], "super-secret-key")
        payload = self.save({}, clear=["qdrant_api_key"])
        self.assertFalse(self.setting(payload, "qdrant_api_key")["is_set"])
        self.assertNotIn("QDRANT_API_KEY", os.environ)

    def test_invalid_values_are_rejected_and_nothing_is_saved(self):
        for values in (
            {"max_chunks": "-1"},
            {"max_chunks": "lots"},
            {"max_upload_mb": "1.5"},
            {"audit_retention_days": "999999"},
            {"qdrant_url": "ftp://x"},
            {"qdrant_url": "qdrant"},
            {"mcp_allowed_hosts": "bad host!"},
        ):
            response = self.http.put("/api/advanced", json={"values": values})
            self.assertEqual(response.status_code, 400, values)
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"nope": "1"}}).status_code, 400)
        self.assertEqual(self.http.put("/api/advanced", json={"values": []}).status_code, 400)
        self.assertFalse((Path(self.tmp.name) / "advanced_settings.json").exists())

    def test_an_invalid_environment_value_falls_back_to_the_default(self):
        os.environ["RAGFLOW_MAX_CHUNKS"] = "abc"
        self.assertEqual(max_chunks(), 0)

    def test_the_upload_limit_applies_from_the_saved_value(self):
        self.save({"max_upload_mb": "1"})
        kb = self.http.post("/api/knowledge-bases", json={"name": "Limits"}).json()
        url = f"/api/knowledge-bases/{kb['id']}/documents"
        body = b"Dublin warehouse. " * 120000
        self.assertEqual(self.http.post(url, files={"file": ("big.txt", body)}).status_code, 400)
        self.save({"max_upload_mb": "0"})
        self.assertEqual(self.http.post(url, files={"file": ("big.txt", body)}).status_code, 201)

    def test_qdrant_url_switches_the_store_and_is_normalised(self):
        payload = self.save({"qdrant_url": "http://qdrant.example:6333/"})
        self.assertEqual(self.setting(payload, "qdrant_url")["value"], "http://qdrant.example:6333")
        self.assertTrue(vectors._remote())
        self.save({"qdrant_url": ""})
        self.assertFalse(vectors._remote())

    def test_allowed_hosts_feed_the_mcp_transport_security(self):
        self.save({"mcp_allowed_hosts": " ragflow.example.com , other.example.com "})
        self.assertEqual(advanced.hosts(), ["ragflow.example.com", "other.example.com"])
        settings = mcp_server.transport_security()
        self.assertTrue(settings.enable_dns_rebinding_protection)
        self.assertIn("ragflow.example.com:*", settings.allowed_hosts)


if __name__ == "__main__":
    unittest.main()
