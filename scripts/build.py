#!/usr/bin/env python3
"""Precompute a paginated static catalog; this runs on GitHub Actions, never iOS."""
import hashlib
import shutil
import json
import os
import pathlib
import re
import sqlite3
import tempfile
import unicodedata
import urllib.request

try:
    from scripts.stream_json import iter_apps
    from scripts.dedupe import rank
except ModuleNotFoundError:
    from stream_json import iter_apps
    from dedupe import rank
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "api"
PAGE_SIZE = 50
MAX_APPS_PER_SOURCE = 2_000_000
MAX_BYTES_PER_SOURCE = 750 * 1024 * 1024


def clean(value, limit=300):
    return value.strip()[:limit] if isinstance(value, str) else ""


def safe_url(value):
    value = clean(value, 4096)
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.netloc else ""


# Remove only known, generated listing boilerplate. Preserve genuine app descriptions,
# licensing notices, and original source provenance in sourceId/sourceURL.
_LISTING_BOILERPLATE = (
    re.compile(r"^\s*[^\n]{1,180}?\.\s+Listed by (?:IPAOMTK|iOSVizor)\.\s*$", re.I),
    re.compile(r"^\s*Listed by (?:IPAOMTK|iOSVizor)\.\s*$", re.I),
    re.compile(r"^\s*Original app page:\s*https?://(?:www\.)?(?:ipaomtk\.com|iosvizor\.com)/\S*\s*$", re.I),
)


def clean_listing_description(value, limit=30000):
    description = clean(value, limit)
    if not description:
        return ""
    lines = description.splitlines()
    # Upstream generators sometimes place a title + attribution on one line.
    cleaned = []
    for line in lines:
        line = re.sub(r"\s+Listed by (?:IPAOMTK|iOSVizor)\.(?=\s|$)", "", line, flags=re.I)
        if any(pattern.fullmatch(line) for pattern in _LISTING_BOILERPLATE):
            continue
        if re.fullmatch(r"\s*Original app page:\s*https?://(?:www\.)?(?:ipaomtk\.com|iosvizor\.com)/\S*\s*", line, re.I):
            continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def normalize_word(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


def normalize(source, app):
    if not isinstance(app, dict):
        return None
    bundle = clean(app.get("bundleIdentifier") or app.get("bundleId"), 512)
    name = clean(app.get("name"), 256)
    if not name:
        return None
    versions = app.get("versions") if isinstance(app.get("versions"), list) else []
    latest = next((v for v in versions if isinstance(v, dict) and v.get("downloadURL")), {})
    ipa = safe_url(app.get("downloadURL")) or safe_url(latest.get("downloadURL"))
    if not ipa:
        return None
    sid = source["id"]
    # Missing bundle IDs: keep separate IPA variants rather than merging by name.
    if not bundle:
        if sid not in ("ipaomtk", "iosvizor"):
            return None
        bundle = "unknown." + hashlib.sha256(ipa.encode()).hexdigest()[:24]
    # Conservative identity: never collapse explicitly different mod variants.
    # Version/date and IPA URL are used for ranking updates, not as proof of identity.
    # An absent bundle uses the IPA-derived fallback above, preventing name-only merges.
    variant_text = " ".join(clean(v.get("localizedDescription"), 300) for v in versions if isinstance(v, dict))
    variant_text += " " + clean(app.get("subtitle"), 300)
    variant_match = re.search(r"(?i)\b(?:upstream variant|variant|mod(?:ded)?|tweak(?:ed)?)\s*[:=-]\s*([^;\n]{1,70})", variant_text)
    variant = normalize_word(variant_match.group(1).strip()) if variant_match else ""
    identity = sid + "\0" + bundle.casefold() + "\0" + normalize_word(name)
    if variant:
        identity += "\0variant:" + variant
    app_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    icon = safe_url(app.get("iconURL"))
    version = clean(app.get("version") or latest.get("version"), 100)
    raw_screens = app.get("screenshotURLs") or []
    if not isinstance(raw_screens, list):
        raw_screens = []
    raw_category = app.get("category")
    category = clean(raw_category, 80) if isinstance(raw_category, str) else ""
    summary = {
        "id": app_id, "sourceId": sid, "name": name,
        "bundleIdentifier": bundle, "iconURL": icon,
        "developerName": clean(app.get("developerName"), 256),
        "subtitle": (clean_listing_description(app.get("subtitle"), 300) if sid in ("ipaomtk", "iosvizor") else clean(app.get("subtitle"), 300)),
        "version": version, "versionDate": clean(app.get("versionDate") or latest.get("date"), 100),
        "category": category
    }
    detail = {
        **summary, "sourceURL": source["url"], "downloadURL": ipa,
        "localizedDescription": (clean_listing_description(app.get("localizedDescription") or app.get("description"), 30000) if sid in ("ipaomtk", "iosvizor") else clean(app.get("localizedDescription") or app.get("description"), 30000)),
        "screenshotURLs": [u for x in raw_screens[:30] if (u := safe_url(x))],
        "minOSVersion": clean(app.get("minOSVersion"), 100),
        "size": app.get("size") if type(app.get("size")) is int and 0 <= app["size"] <= 10**12 else None,
        "versions": [
            {"version": clean(v.get("version"), 100),
             "date": clean(v.get("date") or v.get("versionDate"), 100),
             "downloadURL": safe_url(v.get("downloadURL"))}
            for v in versions[:30] if isinstance(v, dict)
        ]
    }
    return app_id, normalize_word(name), summary, detail


def emit(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


class LimitedReader:
    def __init__(self, stream):
        self.stream, self.n = stream, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.n += len(data)
        if self.n > MAX_BYTES_PER_SOURCE:
            raise ValueError("Source exceeded byte limit")
        return data


def source_apps(source, fixture=None):
    """Yield one app at a time without loading full source JSON."""
    if fixture is not None:
        stream = open(fixture, "rb")
    else:
        req = urllib.request.Request(source["url"], headers={
            "User-Agent": "AMARYT-Catalog/2.0", "Accept": "application/json"
        })
        stream = urllib.request.urlopen(req, timeout=120)
    with stream:
        yield from iter_apps(LimitedReader(stream))


def validate_source(source):
    if not isinstance(source, dict):
        raise ValueError("Source must be an object")
    if not isinstance(source.get("id"), str) or not re.fullmatch("[a-z0-9][a-z0-9-]{0,62}", source["id"]):
        raise ValueError("Invalid source ID")
    url = source.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
        raise ValueError("Sources require HTTPS URLs")


def build(config_path=None, output=None, fixtures=None):
    config_path = pathlib.Path(config_path or ROOT / "sources.json")
    output = pathlib.Path(output or OUT)
    sources = json.loads(config_path.read_text(encoding="utf-8"))["sources"]
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    sources = [s for s in sources if s.get("enabled", True)]
    if not sources or len({s.get("id") for s in sources}) != len(sources):
        raise ValueError("At least one source and unique source IDs required")
    with tempfile.TemporaryDirectory() as tmp:
        db = sqlite3.connect(os.path.join(tmp, "catalog.db"))
        db.execute("CREATE TABLE apps(id TEXT PRIMARY KEY, title TEXT NOT NULL, card TEXT NOT NULL, detail TEXT NOT NULL)")
        counts = {}
        failures = {}
        audit = {}
        for source in sources:
            validate_source(source)
            used = 0
            raw_seen = 0
            before_count = db.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
            source_fixture = pathlib.Path(fixtures) / (source["id"] + ".json") if fixtures else None
            db.execute("SAVEPOINT source")
            try:
                for n, raw in enumerate(source_apps(source, source_fixture), start=1):
                    raw_seen = n
                    if n > MAX_APPS_PER_SOURCE:
                        raise ValueError("Source exceeded app count limit")
                    app = normalize(source, raw)
                    if app:
                        ident, title, card, detail = app
                        previous = db.execute("SELECT card,detail FROM apps WHERE id=?", (ident,)).fetchone()
                        if previous:
                            old_card, old_detail = (json.loads(x) for x in previous)
                            if rank(card, detail) <= rank(old_card, old_detail):
                                used += 1
                                continue
                        db.execute("INSERT OR REPLACE INTO apps VALUES(?,?,?,?)", (
                            ident, title, json.dumps(card, ensure_ascii=False),
                            json.dumps(detail, ensure_ascii=False)))
                        used += 1
                if used == 0:
                    raise ValueError("Source has no usable apps: " + source["id"])
                db.execute("RELEASE source")
            except Exception as exc:
                db.execute("ROLLBACK TO SAVEPOINT source")
                db.execute("RELEASE SAVEPOINT source")
                if source["id"] == "apptesters":
                    raise
                failures[source["id"]] = str(exc)[:300]
                print(f"WARNING: skipped {source['id']}: {exc}", flush=True)
                continue
            unique_count = db.execute("SELECT COUNT(*) FROM apps").fetchone()[0] - before_count
            print(f"{source['id']}: raw={raw_seen}, valid={used}, unique={unique_count}", flush=True)
            counts[source["id"]] = unique_count
            audit[source["id"]] = {"raw": raw_seen, "valid": used, "unique": unique_count, "deduplicatedWithinSource": used - unique_count}
        db.commit()
        emit(output / "source-status.json", {"schemaVersion": 2, "active": counts, "failed": failures, "audit": audit, "totalUnique": sum(counts.values())})
        total = db.execute("SELECT count(*) FROM apps").fetchone()[0]
        if total == 0:
            raise ValueError("No usable apps from any configured source; refusing empty catalog publication")
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        cursor = db.execute("SELECT card,detail FROM apps ORDER BY title,id")
        for page in range(pages):
            rows = cursor.fetchmany(PAGE_SIZE)
            cards, details = [], []
            detail_path = f"details/{page:06d}.json"
            for card_raw, detail_raw in rows:
                card = json.loads(card_raw)
                card["detailPath"] = detail_path
                cards.append(card)
                details.append(json.loads(detail_raw))
            emit(output / f"pages/{page:06d}.json", {
                "schemaVersion": 2, "page": page, "items": cards,
                "next": f"{page+1:06d}" if page+1 < pages else None})
            emit(output / detail_path, {"schemaVersion": 2, "page": page, "items": details})
            if page == 0:
                emit(output / "home.json", {"schemaVersion": 2, "items": cards})

        # Static candidate search: group by first 3 characters of each name word.
        # This is not a dynamic server-side search API.
        db.execute("CREATE TABLE tokens(prefix TEXT, title TEXT, id TEXT, PRIMARY KEY(prefix,id)) WITHOUT ROWID")
        for ident, title in db.execute("SELECT id,title FROM apps"):
            for word in re.findall(r"\w+", title, flags=re.UNICODE):
                if len(word) >= 3:
                    db.execute("INSERT OR IGNORE INTO tokens VALUES(?,?,?)", (word[:3], title, ident))
        db.execute("CREATE INDEX tokens_title ON tokens(prefix,title,id)")
        search_index = {}
        for prefix, count in db.execute("SELECT prefix,count(*) FROM tokens GROUP BY prefix"):
            key = prefix.encode("utf-8").hex()
            search_index[prefix] = {"key": key, "count": count, "pages": (count+PAGE_SIZE-1)//PAGE_SIZE}
            results = db.execute("SELECT a.card FROM tokens t JOIN apps a ON a.id=t.id WHERE t.prefix=? ORDER BY t.title,t.id", (prefix,))
            for page in range((count+PAGE_SIZE-1)//PAGE_SIZE):
                emit(output / f"search/{key}/{page:06d}.json", {
                    "schemaVersion": 2, "prefix": prefix, "page": page,
                    "items": [json.loads(row[0]) for row in results.fetchmany(PAGE_SIZE)]})
        emit(output / "search/index.json", {
            "schemaVersion": 2, "minQueryLength": 3,
            "type": "static-name-prefix-candidates", "buckets": search_index})
        manifest = {
            "schemaVersion": 2, "generatedAt": datetime.now(timezone.utc).isoformat(),
            "totalApps": total, "pageSize": PAGE_SIZE, "pageCount": pages,
            "homePath": "home.json", "pagePattern": "pages/{page:06d}.json",
            "detailPattern": "details/{page:06d}.json",
            "searchIndexPath": "search/index.json", "sources": counts}

        # One-request bootstrap payload, keeping the existing v2 endpoints intact.
        emit(output / "bootstrap.json", {
            "schemaVersion": 2, "generatedAt": manifest["generatedAt"],
            "totalApps": total, "pageSize": PAGE_SIZE, "pageCount": pages,
            "items": json.loads((output / "home.json").read_text(encoding="utf-8"))["items"]
        })
        # Independent paginated source catalogs.
        for sid, count in counts.items():
            source_dir = output / "sources" / sid
            source_cursor = db.execute(
                "SELECT card FROM apps WHERE json_extract(card, '$.sourceId')=? ORDER BY title,id", (sid,))
            source_pages = (count + PAGE_SIZE - 1) // PAGE_SIZE
            for page in range(source_pages):
                cards = [json.loads(row[0]) for row in source_cursor.fetchmany(PAGE_SIZE)]
                emit(source_dir / f"{page:06d}.json", {
                    "schemaVersion": 2, "sourceId": sid, "page": page,
                    "items": cards,
                    "next": f"{page+1:06d}" if page+1 < source_pages else None
                })
            emit(source_dir / "manifest.json", {
                "schemaVersion": 2, "sourceId": sid, "totalApps": count,
                "pageSize": PAGE_SIZE, "pageCount": source_pages,
                "firstPagePath": "000000.json"
            })
        emit(output / "manifest.json", manifest)
        (output.parent / ".nojekyll").touch()
        db.close()
        print("Built", total, "apps /", pages, "pages /", len(search_index), "search buckets")
        return manifest


if __name__ == "__main__":
    build()
