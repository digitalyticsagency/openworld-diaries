import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ["ELEVENLABS_API_KEY"] = "test-key"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import voice  # noqa: E402
from app.server import app  # noqa: E402


class Resp:
    def __init__(self, code=200, data=None, text=""):
        self.status_code, self._data, self.text, self.content = code, data or {}, text, b"x"

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code), response=self)


SUB = {"character_count": 230987, "character_limit": 231000, "next_character_count_reset_unix": 1791788430}


class CreditsTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(voice, "CHECK_CREDITS", True)
        p.start()
        self.addCleanup(p.stop)

    def test_the_credits_are_read_from_the_account(self):
        with mock.patch("app.voice.requests.get", return_value=Resp(200, SUB)):
            c = voice.credits()
        self.assertEqual((c["used"], c["limit"], c["remaining"]), (230987, 231000, 13))
        self.assertEqual(c["resets"], 1791788430)

    def test_an_unreadable_account_is_none_not_a_crash(self):
        for bad in (Resp(500), Resp(200, {"x": 1})):
            with mock.patch("app.voice.requests.get", return_value=bad):
                self.assertIsNone(voice.credits())
        with mock.patch("app.voice.requests.get", side_effect=requests.exceptions.ConnectTimeout("t")):
            self.assertIsNone(voice.credits())

    def test_the_message_says_what_to_do_and_when_they_reset(self):
        m = voice.credit_message({"remaining": 13, "limit": 231000, "resets": 1791788430})
        self.assertIn("The ElevenLabs credits are used up (13 of 231000 characters left).", m)
        self.assertIn("They reset on", m)
        self.assertIn("press Voice and build again", m)
        self.assertIn("every voice already made is kept", m)
        self.assertIn("used up", voice.credit_message(None))

    def test_a_refusal_for_credits_is_named_not_shown_as_a_bare_401(self):
        out = Path(tempfile.mkdtemp()) / "c.mp3"
        quota = Resp(401, text='{"detail":{"status":"quota_exceeded","message":"You have 13 credits remaining"}}')
        with mock.patch("app.voice.requests.post", return_value=quota), mock.patch("app.voice.requests.get", return_value=Resp(200, SUB)):
            with self.assertRaises(voice.OutOfCredits) as cm:
                voice.tts("Hello there.", "V", "calm", out)
        self.assertIn("credits are used up", str(cm.exception))
        with mock.patch("app.voice.requests.post", return_value=Resp(401, text='{"detail":{"status":"invalid_api_key"}}')), \
                self.assertRaises(requests.exceptions.HTTPError):
            voice.tts("Hello there.", "V", "calm", out)                                          # a wrong key is still a wrong key

    def lines(self):
        return [{"start": 10.0, "line": "He left.", "persona": "character", "emotion": "calm", "max_duration": 9.0}]

    def test_voicing_does_not_start_when_almost_nothing_is_left(self):
        with mock.patch("app.voice.requests.get", return_value=Resp(200, SUB)), mock.patch("app.voice.tts") as tts, \
                self.assertRaises(voice.OutOfCredits):
            voice.make_clips(self.lines(), [], "sys", Path(tempfile.mkdtemp()), pack="western")
        tts.assert_not_called()

    def test_voicing_goes_ahead_with_enough_credits_or_when_the_account_cannot_be_read(self):
        for sub in (Resp(200, {**SUB, "character_count": 1000}), Resp(500)):
            with mock.patch("app.voice.requests.get", return_value=sub), mock.patch("app.voice.tts", side_effect=lambda t, v, e, o, **k: Path(o).write_bytes(b"x")), \
                    mock.patch("app.voice.level_clip"), mock.patch("app.voice.duration", return_value=2.0), mock.patch("app.voices.resolve", return_value="V"):
                self.assertEqual(len(voice.make_clips(self.lines(), [], "sys", Path(tempfile.mkdtemp()), pack="western")), 1)

    def test_a_video_with_only_silent_thoughts_does_not_ask_the_account(self):
        silent = [{"start": 10.0, "line": "Hm.", "persona": "character", "silent": True, "max_duration": 4.0}]
        with mock.patch("app.voice.requests.get") as get:
            voice.make_clips(silent, [], "sys", Path(tempfile.mkdtemp()), pack="western")
        get.assert_not_called()

    def test_the_endpoint_and_the_page_show_the_credits(self):
        c = TestClient(app)
        with mock.patch("app.voice.requests.get", return_value=Resp(200, SUB)):
            self.assertEqual(c.get("/api/credits").json()["remaining"], 13)
        with mock.patch("app.voice.requests.get", return_value=Resp(500)):
            self.assertEqual(c.get("/api/credits").status_code, 502)
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn('id="credits"', html)
        self.assertIn("ElevenLabs credits:", html)


class TestsNeverReachTheRealAccountTests(unittest.TestCase):
    def test_by_default_under_test_the_account_is_not_asked(self):
        self.assertFalse(voice.CHECK_CREDITS)
        with mock.patch("app.voice.requests.get") as get:
            self.assertIsNone(voice.credits())
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
