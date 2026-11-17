"""Deterministic selection of the latest record for each source/name/bundle."""
import re
from datetime import datetime

def version_key(value):
    parts = re.findall(r"\d+|[a-z]+", str(value).lower())
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts)

def date_key(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, TypeError):
        return 0

def rank(card, detail):
    # Version first, date second; deterministic URL tie-break.
    return (version_key(card.get("version", "")),
            date_key(card.get("versionDate", "")),
            detail.get("downloadURL", ""))
