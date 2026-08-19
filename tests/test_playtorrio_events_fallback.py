import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import playtorrio


class PlayTorrioFallbackTests(unittest.TestCase):
    def test_load_cached_events_from_snapshot(self):
        snapshot = {
            "events": [
                {
                    "title": "Club vs Rival",
                    "league": "League",
                    "time": "19:00",
                    "timestamp": 1710000000000,
                    "logo": "https://example.com/logo.png",
                    "sources": [
                        {
                            "name": "Channel One",
                            "channel": "Channel One",
                            "country": "🇪🇸 Spain",
                            "logo": "https://example.com/channel.png",
                            "url": "https://example.com/stream.m3u8",
                        }
                    ],
                    "live": True,
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "playtorrio_events.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

            extractor = playtorrio.PlayTorrioEventsExtractor()
            self.assertTrue(extractor.load_cached_events(str(snapshot_path)))
            self.assertEqual(len(extractor.events), 1)
            self.assertEqual(extractor.events[0]["title"], "Club vs Rival")


if __name__ == "__main__":
    unittest.main()
