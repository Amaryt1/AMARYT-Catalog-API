#!/usr/bin/env python3
"""Build an API-style static catalog without loading huge source feeds into RAM."""
import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from scripts.stream_json import iter_apps
except ModuleNotFoundError:
    from stream_json import iter_apps

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE_SIZE = 50
MAX_BYTES = 750 * 1024 * 1024
MAX_APPS = 2_000_000

def short(value, limit=512):
    return value.strip()[:limit] if isinstance(value, str) else ""

def url(value):
    value = short(value, 4096)
    p = urllib.parse.urlsplit(value)
    return value if p.scheme in ("https", "http") and p.netloc else ""

def norm(value):
    value = unicodedata.normalize("NFKD", value or "").casefold()
    return " ".join("".join(x for x in value if unicodedata.category(x) != "Mn").split())

def encoded(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

def latest(raw):
    versions = raw.get("versions")
    if isinstance(versions, list):
        return next((v for v in versions if isinstance(v, dict) and (v.get("downloadURL") or v.get("version"))), {})
    return {}

def normalize(source, raw):
    if not isinstance(raw, dict):
        return None
    name = short(raw.get("name"), 256)
    bundle = short(raw.get("bundleIdentifier") or raw.get("bundleId"), 512)
    if not name or not bundle:
        return None
    ver = latest(raw)
    ipa = url(raw.get("downloadURL")) or url(ver.get("downloadURL"))
    if not ipa:
        return None
    ident = hashlib.sha256((source["id"] + "\0" + bundle.casefold()).encode()).hexdigest()[:24]
    icon = url(raw.get("iconURL"))
    cat = raw.get("category")
    cat = short(cat, 100) if isinstance(cat, str) else ""
    size = raw.get("size")
    screenshots = raw.get("screenshotURLs") or []
    if not isinstance(screenshots, list):
        screenshots = []
    versions = raw.get("versions") or []
    if not isinstance(versions, list):
        versions = []
    card = {
        "id": ident, "sourceId": source["id"], "name": name,
        "subtitle": short(raw.get("subtitle"), 300),
        "version": short(raw.get("version") or ver.get("version"), 100),
        "versionDate": short(raw.get("versionDate") or ver.get("date") or ver.get("versionDate"), 100),
        "iconURL": icon, "category": cat,
        "developerName": short(raw.get("developerName"), 200),
    }
    details = dict(card, bundleIdentifier=bundle,
                   sourceName=source.get("name") or source["id"],
                   sourceURL=source["url"],
                   localizedDescription=short(raw.get("localizedDescription") or raw.get("description"), 30000),
                   downloadURL=ipa,
                   screenshotURLs=[u for item in screenshots[:30] if (u := url(item))],
                   size=size if isinstance(size, int) and 0 <= size <= 10**12 else None,
                   minOSVersion=short(raw.get("minOSVersion"), 80),
                   versions=[{
                       "version": short(v.get("version"), 100),
                       "date": short(v.get("date") or v.get("versionDate"), 100),
                       "downloadURL": url(v.get("downloadURL")),
                       "localizedDescription": short(v.get("localizedDescription"), 4096)
                   } for v in versions[:30] if isinstance(v, dict)])
    return ident, norm(name), encoded(card), encoded(details)

class SizeLimited:
    def __init__(self, stream):
        self.stream = stream
        self.read_bytes = 0
    def read(self, size=-1):
        data = self.stream.read(size)
        self.read_bytes += len(data)
        if self.read_bytes > MAX_BYTES:
            raise ValueError("Source exceeds 750 MiB limit")
        return data

def create_db(path):
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE apps(id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
            name_sort TEXT NOT NULL, card TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE INDEX idx_names ON apps(name_sort,id);
        CREATE TABLE tokens(prefix TEXT NOT NULL, name_sort TEXT NOT NULL,
            id TEXT NOT NULL, PRIMARY KEY(prefix,id)) WITHOUT ROWID;
    """)
    return db

def ingest(db, source, fixture_dir=None):
    sid = source.get("id", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", sid):
        raise ValueError("Invalid source id")
    address = source.get("url", "")
    if urllib.parse.urlsplit(address).scheme != "https":
        raise ValueError(f"{sid}: only HTTPS source URLs are permitted")
    for attempt in range(3):
        began = False
        try:
            if fixture_dir:
                filename = pathlib.Path(fixture_dir) / (urllib.parse.urlsplit(address).netloc + ".json")
                incoming = filename.open("rb")
            else:
                req = urllib.request.Request(address, headers={
                    "User-Agent": "AMARYT-Catalog/2.0", "Accept": "application/json"
                })
                incoming = urllib.request.urlopen(req, timeout=90)
            with incoming:
                if getattr(incoming, "headers", None):
                    header = incoming.headers.get("Content-Length")
                    if header and int(header) > MAX_BYTES:
                        raise ValueError("Source too large")
                db.execute("SAVEPOINT ingest")
                began = True
                processed = valid = 0
                for raw in iter_apps(SizeLimited(incoming)):
                    processed += 1
                    if processed > MAX_APPS:
                        raise ValueError("Source exceeds maximum app count")
                    row = normalize(source, raw)
                    if row is not None:
                        ident, name_sort, card, detail = row
                        db.execute("INSERT OR REPLACE INTO apps VALUES(?,?,?,?,?)",
                                   (ident, sid, name_sort, card, detail))
                        valid += 1
                if valid == 0:
                    raise ValueError("No valid apps: refusing empty deployment")
                db.execute("RELEASE SAVEPOINT ingest")
                print(f"{sid}: {valid}/{processed} usable apps", flush=True)
                return valid
        except Exception:
            if began:
                db.execute("ROLLBACK TO SAVEPOINT ingest")
                db.execute("RELEASE SAVEPOINT ingest")
            if attempt == 2 or fixture_dir:
                raise
            time.sleep(2 ** attempt)

def output_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = encoded(data).encode("utf-8")
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()

def generate(db, root, status, page_size):
    root = pathlib.Path(root)
    out = root / "api"
    total = db.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
    count = (total + page_size - 1) // page_size
    if count == 0:
        raise ValueError("Refusing to publish an empty catalog")
    pages = []
    cursor = db.execute("SELECT id, card, detail FROM apps ORDER BY name_sort,id")
    for number in range(count):
        rows = cursor.fetchmany(page_size)
        detail_path = f"details/{number:06d}.json"
        cards, details = [], []
        for ident, card_json, detail_json in rows:
            card = json.loads(card_json)
            card["detailPath"] = detail_path
            cards.append(card)
            details.append(json.loads(detail_json))
        name = f"{number:06d}.json"
        digest = output_json(out / "pages" / name, {
            "schemaVersion": 1, "page": number, "pageCount": count,
            "next": f"{number+1:06d}" if number + 1 < count else None,
            "items": cards
        })
        output_json(out / detail_path, {"schemaVersion": 1, "page": number, "items": details})
        if number == 0:
            output_json(out / "home.json", {"schemaVersion": 1, "items": cards})
        pages.append({"path": "pages/" + name, "sha256": digest})

    # Static name-token prefix index. NOT a dynamic server-side search API.
    cursor = db.execute("SELECT id,name_sort,card FROM apps")
    while rows := cursor.fetchmany(1000):
        inserts = []
        for ident, name_sort, card_json in rows:
            name = json.loads(card_json)["name"]
            words = re.findall(r"\w+", norm(name), flags=re.UNICODE)
            for prefix in {w[:3] for w in words if len(w) >= 3}:
                inserts.append((prefix, name_sort, ident))
        db.executemany("INSERT OR IGNORE INTO tokens VALUES(?,?,?)", inserts)
    db.execute("CREATE INDEX idx_tokens ON tokens(prefix,name_sort,id)")
    search_index = {}
    for prefix, hits in db.execute("SELECT prefix,COUNT(*) FROM tokens GROUP BY prefix ORDER BY prefix"):
        key = prefix.encode("utf-8").hex()
        shard_count = (hits + page_size - 1) // page_size
        search_index[prefix] = {"key": key, "count": hits, "pages": shard_count}
        cursor = db.execute("""SELECT a.card FROM tokens t JOIN apps a ON a.id=t.id
                               WHERE t.prefix=? ORDER BY t.name_sort,t.id""", (prefix,))
        for number in range(shard_count):
            rows = [json.loads(row[0]) for row in cursor.fetchmany(page_size)]
            output_json(out / f"search/{key}/{number:06d}.json", {
                "schemaVersion": 1, "prefix": prefix, "page": number,
                "pageCount": shard_count, "items": rows
            })
    output_json(out / "search/index.json", {
        "schemaVersion": 1, "minQueryLength": 3,
        "type": "static-name-token-prefix-candidates", "buckets": search_index
    })
    manifest = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalApps": total, "pageSize": page_size, "pageCount": count,
        "homePath": "home.json", "searchIndexPath": "search/index.json",
        "sources": status, "pages": pages
    }
    output_json(out / "manifest.json", manifest)
    (root / ".nojekyll").touch()
    return manifest

def build(sources_file, output_dir, page_size=PAGE_SIZE, fixture_dir=None):
    conf = json.loads(pathlib.Path(sources_file).read_text(encoding="utf-8"))
    sources = [s for s in conf["sources"] if s.get("enabled", True)]
    if not sources or len({s["id"] for s in sources}) != len(sources):
        raise ValueError("No unique enabled sources")
    with tempfile.TemporaryDirectory() as temp:
        db = create_db(pathlib.Path(temp) / "catalog.db")
        try:
            counts = {s["id"]: ingest(db, s, fixture_dir) for s in sources}
            db.commit()
            manifest = generate(db, output_dir, counts, page_size)
        finally:
            db.close()
    print(f"Built {manifest['totalApps']} apps in {manifest['pageCount']} pages", flush=True)
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default=str(ROOT / "sources.json"))
    parser.add_argument("--output", default=str(ROOT / "site"))
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--fixture-dir", default=None)
    options = parser.parse_args()
    if not 20 <= options.page_size <= 500:
        parser.error("page-size must be in range 20..500")
    build(options.sources, options.output, options.page_size, options.fixture_dir)
