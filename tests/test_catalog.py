import io
import json
import pathlib
import tempfile
import unittest

from scripts.build import build, normalize, normalize_word
from scripts.stream_json import iter_apps


class CatalogTests(unittest.TestCase):
    def test_streaming_apps(self):
        raw = json.dumps({"name": "Test", "apps": [
            {"name": "First", "bundleIdentifier": "first"},
            {"name": "ثاني", "bundleIdentifier": "second"}
        ]}).encode()
        self.assertEqual(len(list(iter_apps(io.BytesIO(raw)))), 2)

    def test_url_validation(self):
        source = {"id": "fixture", "url": "https://fixture.test/"}
        self.assertIsNone(normalize(source, {
            "name": "Bad", "bundleIdentifier": "com.bad", "downloadURL": "javascript:alert(1)"
        }))
        self.assertEqual(normalize_word("ÁPP Test"), "app test")

    def test_paging_details_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            feed = {"apps": [
                {"name": "Example App", "bundleIdentifier": "com.example.app",
                 "downloadURL": "https://example.test/example.ipa",
                 "localizedDescription": "This description should be in details only"},
                {"name": "Application Demo", "bundleIdentifier": "com.example.demo",
                 "versions": [{"version": "2.0", "downloadURL": "https://example.test/demo.ipa"}]}
            ]}
            (root / "fixture.json").write_text(json.dumps(feed))
            sources = root / "sources.json"
            sources.write_text(json.dumps({"sources": [{
                "id": "fixture", "name": "Fixture", "url": "https://fixture.test/"
            }]}))
            result = build(config_path=sources, output=root / "site/api", fixtures=root)
            self.assertEqual(result["totalApps"], 2)
            api = root / "site/api"
            cards = json.loads((api / "pages/000000.json").read_text())["items"]
            self.assertEqual(len(cards), 2)
            self.assertNotIn("localizedDescription", cards[0])
            details = json.loads((api / "details/000000.json").read_text())["items"]
            self.assertEqual(len(details), 2)
            self.assertTrue(any(item["localizedDescription"] for item in details))
            self.assertTrue((api / "search/617070/000000.json").exists())
            self.assertTrue((api / "home.json").exists())

    def test_bad_source_fails_without_empty_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "fixture.json").write_text('{"apps":[]}')
            sources = root / "sources.json"
            sources.write_text(json.dumps({"sources": [{
                "id": "fixture", "url": "https://fixture.test/"
            }]}))
            with self.assertRaises(ValueError):
                build(config_path=sources, output=root / "site/api", fixtures=root)


if __name__ == "__main__":
    unittest.main()
