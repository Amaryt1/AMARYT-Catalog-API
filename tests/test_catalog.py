import io
import json
import pathlib
import subprocess
import sys
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
                 "versions": [{"version": "2.0", "downloadURL": "https://example.test/demo.ipa"}]},
                {"name": "Example App Variant", "bundleIdentifier": "com.example.app",
                 "downloadURL": "https://example.test/example-variant.ipa"}
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
            validator = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "validate.py"
            result = subprocess.run([sys.executable, str(validator)], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_multiple_pages_and_duplicate_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apps = [
                {"name": f"Example {n:03d}", "bundleIdentifier": f"org.example.{n}",
                 "downloadURL": f"https://example.test/{n}.ipa"}
                for n in range(103)
            ]
            apps.append(dict(apps[0]))
            (root / "fixture.json").write_text(json.dumps({"apps": apps}))
            config = root / "sources.json"
            config.write_text(json.dumps({"sources": [{"id": "fixture", "url": "https://fixture.test/"}]}))
            manifest = build(config_path=config, output=root / "site/api", fixtures=root)
            self.assertEqual(manifest["totalApps"], 103)
            self.assertEqual(manifest["pageCount"], 3)
            self.assertEqual(manifest["sources"]["fixture"], 103)
            api = root / "site/api"
            self.assertEqual([len(json.loads((api / f"pages/{n:06d}.json").read_text())["items"]) for n in range(3)], [50, 50, 3])
            validator = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "validate.py"
            result = subprocess.run([sys.executable, str(validator)], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_latest_version_wins_even_if_old_record_comes_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apps = [
                {"name": "Demo", "bundleIdentifier": "com.demo",
                 "version": "1.2", "versionDate": "2026-01-01",
                 "downloadURL": "https://example.test/old.ipa"},
                {"name": "Demo", "bundleIdentifier": "com.demo",
                 "version": "2.0", "versionDate": "2026-02-01",
                 "downloadURL": "https://example.test/new.ipa"},
                {"name": "Demo", "bundleIdentifier": "com.demo",
                 "version": "1.0", "versionDate": "2026-03-01",
                 "downloadURL": "https://example.test/older.ipa"},
            ]
            (root / "fixture.json").write_text(json.dumps({"apps": apps}))
            config = root / "sources.json"
            config.write_text(json.dumps({"sources": [{"id": "fixture", "url": "https://fixture.test/"}]}))
            manifest = build(config_path=config, output=root / "site/api", fixtures=root)
            self.assertEqual(manifest["totalApps"], 1)
            detail = json.loads((root / "site/api/details/000000.json").read_text())["items"][0]
            self.assertEqual(detail["downloadURL"], "https://example.test/new.ipa")

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
