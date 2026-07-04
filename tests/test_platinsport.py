import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

playwright_stub = types.ModuleType("playwright")
sync_api_stub = types.ModuleType("playwright.sync_api")

class _DummySyncPlaywright:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

sync_api_stub.sync_playwright = _DummySyncPlaywright
sys.modules.setdefault("playwright", playwright_stub)
sys.modules.setdefault("playwright.sync_api", sync_api_stub)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platinsport


class PlatinsportFallbackTests(unittest.TestCase):
    def test_build_source_list_urls_uses_multiple_hosts(self):
        urls = platinsport.build_source_list_urls(datetime(2026, 7, 4, tzinfo=timezone.utc))
        self.assertEqual(len(urls), 2)
        self.assertIn("https://platinsport.com/link/source-list.php?key=", urls[0])
        self.assertIn("https://www.platinsport.com/link/source-list.php?key=", urls[1])

    def test_load_fallback_playlist_uses_existing_file_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playlist_path = Path(tmpdir) / "lista.m3u"
            playlist_path.write_text("#EXTM3U\n#EXTINF:-1,Existing\nhttp://example.com\n", encoding="utf-8")
            content = platinsport.load_fallback_playlist(str(playlist_path))
            self.assertIn("Existing", content)

    def test_load_playtorrio_fallback_uses_local_playlist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playtorrio_path = Path(tmpdir) / "playtorrio.m3u"
            playtorrio_path.write_text("#EXTM3U\n#EXTINF:-1,PlayTorrio\nhttp://example.com\n", encoding="utf-8")
            output_path = Path(tmpdir) / "lista.m3u"
            cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                self.assertTrue(platinsport.load_playtorrio_fallback(str(output_path)))
                self.assertIn("PlayTorrio", output_path.read_text(encoding="utf-8"))
            finally:
                os.chdir(cwd)

    def test_load_playtorrio_fallback_merges_multiple_lists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playtorrio_path = Path(tmpdir) / "playtorrio.m3u"
            playtorrio_path.write_text("#EXTM3U\n#EXTINF:-1,Eventos\nhttp://sports.example\n", encoding="utf-8")
            channels_path = Path(tmpdir) / "playtorrio_canales.m3u"
            channels_path.write_text("#EXTM3U\n#EXTINF:-1,Canales\nhttp://channels.example\n", encoding="utf-8")
            output_path = Path(tmpdir) / "lista.m3u"
            cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                self.assertTrue(platinsport.load_playtorrio_fallback(str(output_path)))
                content = output_path.read_text(encoding="utf-8")
                self.assertIn("Eventos", content)
                self.assertIn("Canales", content)
                self.assertIn("http://sports.example", content)
                self.assertIn("http://channels.example", content)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
