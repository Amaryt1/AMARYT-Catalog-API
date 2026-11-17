#!/usr/bin/env python3
"""Build small, static catalog pages from AltStore-compatible JSON sources."""
import hashlib
import json
import os
import pathlib
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "api"
PAGE_SIZE = 50

def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "AMARYT-Catalog/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)

def normalize(source, app):
    if not isinstance(app, dict):
        return None
    bundle = str(app.get("bundleIdentifier") or app.get("bundleId") or "").strip()
    name = str(app.get("name") or "").strip()
    if not bundle or not name:
        return None
    identifier = source["id"] + ":" + bundle
    return {
        "id": identifier,
        "sourceId": source["id"],
        "bundleIdentifier": bundle,
        "name": name,
        "subtitle": app.get("subtitle") or "",
        "description": app.get("localizedDescription") or app.get("description") or "",
        "version": app.get("version") or "",
        "versionDate": app.get("versionDate") or "",
        "iconURL": app.get("iconURL") or "",
        "downloadURL": app.get("downloadURL") or "",
        "size": app.get("size") or 0,
        "developerName": app.get("developerName") or "",
        "category": app.get("category") or "",
    }

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()

def main():
    sources = json.loads((ROOT / "sources.json").read_text())["sources"]
    entries = {}
    counts = {}
    for source in sources:
        if not source.get("enabled", True):
            continue
        raw = download(source["url"])
        apps = raw.get("apps") if isinstance(raw, dict) else None
        if not isinstance(apps, list):
            raise ValueError("Source " + source["id"] + " has no apps array")
        counts[source["id"]] = 0
        for app in apps:
            item = normalize(source, app)
            if item:
                entries[item["id"]] = item
                counts[source["id"]] += 1
    ordered = sorted(entries.values(), key=lambda a: (a["name"].casefold(), a["id"]))
    pages_dir = OUT / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("*.json"):
        old.unlink()
    pages = []
    for offset in range(0, len(ordered), PAGE_SIZE):
        page_number = offset // PAGE_SIZE
        name = f"{page_number:06d}.json"
        content = {"items": ordered[offset:offset + PAGE_SIZE], "next": f"{page_number + 1:06d}" if offset + PAGE_SIZE < len(ordered) else None}
        digest = write_json(pages_dir / name, content)
        pages.append({"path": "pages/" + name, "sha256": digest})
    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalApps": len(ordered),
        "pageSize": PAGE_SIZE,
        "pageCount": len(pages),
        "sources": counts,
        "pages": pages,
    }
    write_json(OUT / "manifest.json", manifest)
    print("Built", len(ordered), "apps in", len(pages), "pages")

if __name__ == "__main__":
    main()
