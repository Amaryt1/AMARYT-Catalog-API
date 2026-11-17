#!/usr/bin/env python3
"""Verify a complete static catalog before uploading a Pages artifact."""
import json
from pathlib import Path

root = Path("site/api")
manifest = json.loads((root / "manifest.json").read_text())
assert manifest["schemaVersion"] == 2
assert manifest["totalApps"] > 0 and manifest["pageCount"] > 0
assert 20 <= manifest["pageSize"] <= 500
assert (root / manifest["homePath"]).is_file()
total = 0
for number in range(manifest["pageCount"]):
    cards = json.loads((root / f"pages/{number:06d}.json").read_text())
    detail = json.loads((root / f"details/{number:06d}.json").read_text())
    assert cards["schemaVersion"] == detail["schemaVersion"] == 2
    assert 0 < len(cards["items"]) <= manifest["pageSize"]
    assert len(cards["items"]) == len(detail["items"])
    assert [a["id"] for a in cards["items"]] == [a["id"] for a in detail["items"]]
    total += len(cards["items"])
assert total == manifest["totalApps"], (total, manifest["totalApps"])
search = json.loads((root / manifest["searchIndexPath"]).read_text())
assert search["schemaVersion"] == 2
for item in search["buckets"].values():
    assert (root / f"search/{item['key']}/000000.json").is_file()
print(f"Validated {total} apps and {manifest['pageCount']} pages.")
