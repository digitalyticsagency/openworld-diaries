import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.server import _local_host, app  # noqa: E402


class LocalOnlyTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_which_hosts_count_as_this_computer(self):
        for h in ("localhost:8000", "127.0.0.1:8000", "localhost", "127.0.0.1:9000", "[::1]:8000", "http://localhost:8000", "testserver"):
            self.assertTrue(_local_host(h), h)
        for h in ("evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.evil.example:8000", "", None, "http://evil.example",
                  "192.168.1.5:8000", "localhost@evil.example"):
            self.assertFalse(_local_host(h), h)

    def test_the_apps_own_page_works_as_before(self):
        self.assertEqual(self.c.get("/", headers={"host": "localhost:8000"}).status_code, 200)
        self.assertEqual(self.c.get("/api/state", headers={"host": "127.0.0.1:8000"}).status_code, 200)
        r = self.c.post("/api/settings", json={"series_name": "x"}, headers={"origin": "http://localhost:8000", "host": "localhost:8000"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/api/settings", json={"series_name": "x"}).status_code, 200)         # no Origin: a script or curl on this machine

    def test_a_dns_rebinding_host_is_refused_even_for_reading(self):
        r = self.c.get("/api/state", headers={"host": "evil.example:8000"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("only answers on this computer", r.json()["detail"])

    def test_another_websites_page_cannot_change_anything(self):
        db.put("series_name", "keep me")
        for headers in ({"origin": "http://evil.example"}, {"origin": "null"}, {"origin": "https://localhost.evil.example"},
                        {"sec-fetch-site": "cross-site"}):
            r = self.c.post("/api/settings", json={"series_name": "hacked"}, headers=headers)
            self.assertEqual(r.status_code, 403, headers)
        self.assertEqual(db.get("series_name"), "keep me")

    def test_uploads_and_actions_are_covered_too(self):
        evil = {"origin": "http://evil.example"}
        self.assertEqual(self.c.post("/api/upload?name=x.mp4", content=b"x" * 2000, headers=evil).status_code, 403)
        self.assertEqual(self.c.post("/api/music?name=x.mp3", content=b"x" * 2000, headers=evil).status_code, 403)
        self.assertEqual(self.c.post("/api/analyze", headers=evil).status_code, 403)
        self.assertEqual(self.c.delete("/api/videos/1", headers=evil).status_code, 403)
        self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY", json={"value": "x"}, headers=evil).status_code, 403)

    def test_reading_with_a_foreign_origin_header_is_not_a_write(self):
        self.assertEqual(self.c.get("/api/state", headers={"origin": "http://evil.example"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
