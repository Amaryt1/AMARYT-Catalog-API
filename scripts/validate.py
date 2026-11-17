#!/usr/bin/env python3
"""Validate the generated API contract before publishing to GitHub Pages."""
import json
from pathlib import Path

root = Path("site/api")
manifest = json.loads((root / "manifest.json").read_text())
assert manifest["schemaVersion"] == 1
assert manifest["totalApps"] > 0
assert manifest["pageCount"] > 0
assert len(manifest["pages"]) == manifest["pageCount"]
page = json.loads((root / manifest["pages"][0]["path"]).read_text())
assert len(page["items"]) > 0
assert len(page["items"]) <= manifest["pageSize"]
details = json.loads((root / page["items"][0]["detailPath"]).read_text())
assert page["items"][0]["id"] in {x["id"] for x in details["items"]}
assert (root / manifest["homePath"]).exists()
search = json.loads((root / manifest["searchIndexPath"]).read_text())
assert search["schemaVersion"] == 1
for item in search["buckets"].values():
    assert (root / f"search/{item['key']}/000000.json").exists()
print(f"Validated {manifest['totalApps']} apps; first page {len(page['items'])} cards.")
