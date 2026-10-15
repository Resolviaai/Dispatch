"""Tests for Phase 2: YouTube Inbox, OAuth, VTT Parser & Poller Hardening."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch.config import ROOT_DIR, YOUTUBE_TOKEN_PATH
from dispatch.youtube_inbox.poller import parse_iso_datetime
from dispatch.youtube_inbox.vtt_parser import parse_vtt_content
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher, _apply_auth_opts


class TestPhase2YouTube(unittest.TestCase):

    def test_parse_iso_datetime_variants(self):
        """Verify parse_iso_datetime parses fractional seconds, Z, and offsets correctly."""
        # Standard with Z
        dt1 = parse_iso_datetime("2026-10-14T12:30:00Z")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.tzinfo, timezone.utc)
        self.assertEqual(dt1.hour, 12)

        # Fractional seconds with Z
        dt2 = parse_iso_datetime("2026-10-14T12:30:00.123456Z")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2.microsecond, 123456)

        # Explicit offset
        dt3 = parse_iso_datetime("2026-10-14T12:30:00.500+00:00")
        self.assertIsNotNone(dt3)
        self.assertEqual(dt3.microsecond, 500000)

        # Space separated legacy format
        dt4 = parse_iso_datetime("2026-10-14 12:30:00")
        self.assertIsNotNone(dt4)
        self.assertEqual(dt4.minute, 30)

        # None / empty
        self.assertIsNone(parse_iso_datetime(None))
        self.assertIsNone(parse_iso_datetime(""))

    def test_vtt_rolling_caption_prefix_slicing(self):
        """Verify rolling caption inline words are properly sliced rather than matching repeated tokens."""
        vtt_data = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
<00:00:01.000><c> We</c><00:00:01.500><c> are</c><00:00:02.000><c> launching</c>

00:00:02.000 --> 00:00:05.000
<00:00:02.000><c> We</c><00:00:02.300><c> are</c><00:00:02.600><c> launching</c><00:00:03.200><c> the</c><00:00:04.000><c> rocket</c>
"""
        segments = parse_vtt_content(vtt_data)
        self.assertTrue(len(segments) >= 1)
        # The full merged text should contain "We are launching the rocket" without "We are launching" duplicated
        full_text = " ".join(s["text"] for s in segments)
        self.assertEqual(full_text.strip(), "We are launching the rocket")

    def test_apply_auth_opts(self):
        """Verify _apply_auth_opts sets cookiefile or cookiesfrombrowser correctly."""
        ydl_opts = {}
        with patch.dict(os.environ, {"DISPATCH_YTDLP_COOKIES_BROWSER": "chrome"}, clear=False):
            _apply_auth_opts(ydl_opts)
            self.assertEqual(ydl_opts.get("cookiesfrombrowser"), ("chrome",))

    def test_canonical_token_path(self):
        """Verify canonical token path points to root directory youtube_token.json."""
        self.assertEqual(YOUTUBE_TOKEN_PATH, ROOT_DIR / "youtube_token.json")


if __name__ == "__main__":
    unittest.main()
