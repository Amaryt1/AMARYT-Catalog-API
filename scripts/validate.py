#!/usr/bin/env python3
"""Validate every generated catalog page and search bucket before deployment."""
import json
from pathlib import Path

root = Path("site/api")

def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

manifest = load(root / "manifest.json")
assert manifest["schemaVersion"] == 2
assert manifest["totalApps"] > 0
assert manifest["pageSize"] == 50
assert manifest["pageCount"] == (manifest["totalApps"] + 49) // 50
assert sum(manifest["sources"].values()) == manifest["totalApps"]
assert all(count > 0 for count in manifest["sources"].values())

ids = set()
total = 0
first_page = None
for number in range(manifest["pageCount"]):
    cards = load(root / f"pages/{number:06d}.json")
    details = load(root / f"details/{number:06d}.json")
    assert cards["schemaVersion"] == details["schemaVersion"] == 2
    assert cards["page"] == details["page"] == number
    assert cards["next"] == (f"{number+1:06d}" if number+1 < manifest["pageCount"] else None)
    assert 0 < len(cards["items"]) <= manifest["pageSize"]
    if number < manifest["pageCount"] - 1:
        assert len(cards["items"]) == manifest["pageSize"]
    assert len(cards["items"]) == len(details["items"])
    if first_page is None:
        first_page = cards["items"]
    for card, detail in zip(cards["items"], details["items"]):
        assert card["id"] == detail["id"]
        assert card["id"] not in ids, f"Duplicate app id: {card['id']}"
        ids.add(card["id"])
        assert card["detailPath"] == f"details/{number:06d}.json"
        assert card["sourceId"] in manifest["sources"]
        assert card["name"] and card["bundleIdentifier"]
        assert detail["downloadURL"].startswith(("https://", "http://"))
    total += len(cards["items"])
assert total == manifest["totalApps"]
home = load(root / manifest["homePath"])
assert home["schemaVersion"] == 2
assert home["items"] == first_page

bootstrap = load(root / "bootstrap.json")
assert bootstrap["schemaVersion"] == 2
assert bootstrap["totalApps"] == total
assert bootstrap["items"] == first_page
for source_id, count in manifest["sources"].items():
    source_dir = root / "sources" / source_id
    source_manifest = load(source_dir / "manifest.json")
    assert source_manifest["totalApps"] == count
    assert source_manifest["pageCount"] == (count + 49) // 50
    seen = set()
    for page in range(source_manifest["pageCount"]):
        payload = load(source_dir / f"{page:06d}.json")
        assert payload["sourceId"] == source_id
        assert payload["page"] == page
        for item in payload["items"]:
            assert item["sourceId"] == source_id
            assert item["id"] in ids and item["id"] not in seen
            seen.add(item["id"])
    assert len(seen) == count
search = load(root / manifest["searchIndexPath"])
assert search["schemaVersion"] == 2
assert search["minQueryLength"] == 3
for prefix, bucket in search["buckets"].items():
    assert bucket["count"] > 0
    assert bucket["pages"] == (bucket["count"] + 49) // 50
    assert bucket["key"] == prefix.encode("utf-8").hex()
    bucket_ids = set()
    for page in range(bucket["pages"]):
        payload = load(root / f"search/{bucket['key']}/{page:06d}.json")
        assert payload["schemaVersion"] == 2
        assert payload["prefix"] == prefix and payload["page"] == page
        assert 0 < len(payload["items"]) <= 50
        for item in payload["items"]:
            assert item["id"] in ids
            assert item["id"] not in bucket_ids
            bucket_ids.add(item["id"])
    assert len(bucket_ids) == bucket["count"], (prefix, len(bucket_ids), bucket["count"])
print(f"Validated {total} unique apps, {manifest['pageCount']} pages, and {len(search['buckets'])} search buckets.")
