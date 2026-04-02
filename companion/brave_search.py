"""Brave Search API integration for real-time web lookups.

Uses only stdlib (urllib, json, re, gzip). Respects a monthly request limit
matching the $5/month Brave Search tier (1 000 requests).
"""

import gzip
import json
import logging
import re
import sqlite3
import urllib.error
import urllib.request

from . import config, db

logger = logging.getLogger(__name__)

_MONTHLY_LIMIT = 1000
_SEARCH_PATTERN = re.compile(r"\[SEARCH:\s*(.+?)\]")


_BOGUS_QUERIES = {"your query", "your query here", "your search query", "your search query here", "query"}

def extract_search_query(text: str) -> str | None:
    """Return the first [SEARCH: query] match in *text*, or None."""
    m = _SEARCH_PATTERN.search(text)
    if not m:
        return None
    query = m.group(1).strip()
    if query.lower() in _BOGUS_QUERIES:
        logger.warning("Ignoring bogus search query: %s", query)
        return None
    return query


def get_monthly_usage(conn: sqlite3.Connection) -> int:
    """Return number of searches used this month."""
    return db.count_monthly_searches(conn)


def search(conn: sqlite3.Connection, query: str, count: int = 5) -> str | None:
    """Call Brave Web Search API and return formatted results.

    Returns None on failure or if the monthly limit has been reached.
    Records usage in the database on success.
    """
    api_key = config.BRAVE_SEARCH_API_KEY
    if not api_key:
        logger.warning("BRAVE_SEARCH_API_KEY not configured")
        return None

    # Check monthly limit
    used = db.count_monthly_searches(conn)
    if used >= _MONTHLY_LIMIT:
        logger.warning("Monthly search limit reached (%d/%d)", used, _MONTHLY_LIMIT)
        return None

    # Build request
    params = urllib.request.quote(query, safe="")
    url = f"https://api.search.brave.com/res/v1/web/search?q={params}&count={count}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        logger.error("Brave Search request failed: %s", exc)
        return None

    # Parse results
    results = data.get("web", {}).get("results", [])
    if not results:
        db.save_search(conn, query, 0)
        return "No web results found."

    lines: list[str] = []
    for r in results[:count]:
        title = r.get("title", "")
        url_str = r.get("url", "")
        desc = r.get("description", "")
        lines.append(f"- {title}\n  {url_str}\n  {desc}")

    db.save_search(conn, query, len(lines))
    return "\n".join(lines)
