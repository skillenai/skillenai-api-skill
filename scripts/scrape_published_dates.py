#!/usr/bin/env python3
"""Scrape publication dates from a list of article URLs.

When you need minute/second-level publication times for a set of URLs and
the index `publishedAt` field is missing or rounded, scrape the live pages
for standard date metadata: Open Graph, JSON-LD, schema.org microdata,
HTML5 `<time pubdate>`, and common CMS-specific tags.

Recovery rate in practice (Skillenai blog/news mix): ~92% on first pass.

Usage:
    # one URL per line on stdin, JSONL on stdout
    cat urls.txt | python3 scripts/scrape_published_dates.py > out.jsonl

    # explicit args, pretty-printed
    python3 scripts/scrape_published_dates.py https://example.com/a https://example.com/b

    # tune concurrency + timeout
    cat urls.txt | python3 scripts/scrape_published_dates.py --workers 16 --timeout 30 > out.jsonl

Output JSONL fields per line:
    url           original URL
    final_url     URL after redirects
    status        HTTP status, or "ERR:..." on transport failure
    published     ISO-8601 string if found, else null
    via           pattern_<N> identifier indicating which extractor matched
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import re
import sys
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 Skillenai-Analysis"
)

PATTERNS = [
    # 0  Open Graph (most reliable)
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    # 1  Open Graph, reversed attribute order
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
    # 2  Name= variant of OG (some CMSs)
    r'<meta[^>]+name=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    # 3  schema.org via itemprop
    r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
    # 4  Older CMS conventions
    r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\']publishdate["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\']date["\'][^>]+content=["\']([^"\']+)["\']',
    # 7  OG article variant
    r'<meta[^>]+property=["\']og:article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    # 8  JSON-LD (catches both top-level and nested in @graph)
    r'"datePublished"\s*:\s*"([^"]+)"',
    # 9  HTML5 <time pubdate datetime="...">
    r'<time[^>]+datetime=["\']([^"\']+)["\'][^>]*pubdate',
    r'<time[^>]+pubdate[^>]+datetime=["\']([^"\']+)["\']',
    # 11 Parse.ly tag — used by some news CMSs
    r'name=["\']parsely-pub-date["\'][^>]+content=["\']([^"\']+)["\']',
]


def fetch(url: str, timeout: int = 15) -> tuple[str | None, object, str]:
    """Return (html, status, final_url). html is None on transport error; status is then 'ERR:...'."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Encoding": "gzip, identity"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="ignore"), r.status, r.geturl()
    except Exception as e:
        return None, f"ERR:{type(e).__name__}:{e}", url


def extract_published(html: str) -> tuple[str | None, str | None]:
    """Search HTML head + first 200KB for any published-date pattern."""
    if not html:
        return None, None
    head = html[:200_000]
    for i, pat in enumerate(PATTERNS):
        m = re.search(pat, head, re.IGNORECASE)
        if m:
            return m.group(1).strip(), f"pattern_{i}"
    return None, None


def process(url: str, timeout: int = 15) -> dict:
    html, status, final_url = fetch(url, timeout=timeout)
    if html is None:
        return {
            "url": url,
            "final_url": final_url,
            "status": status,
            "published": None,
            "via": None,
        }
    ts, via = extract_published(html)
    return {
        "url": url,
        "final_url": final_url,
        "status": status,
        "published": ts,
        "via": via,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="*", help="URLs (omit to read one-per-line from stdin)")
    p.add_argument("--workers", type=int, default=8, help="Concurrent workers (default 8)")
    p.add_argument("--timeout", type=int, default=15, help="Per-URL timeout seconds (default 15)")
    p.add_argument("--summary", action="store_true", help="Print a tab-separated summary instead of JSONL")
    args = p.parse_args()

    urls = args.urls or [l.strip() for l in sys.stdin if l.strip()]
    if not urls:
        p.print_help(sys.stderr)
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda u: process(u, args.timeout), urls):
            if args.summary:
                print(f"{r['status']}\t{r['published']}\t{r['via']}\t{r['url']}")
            else:
                print(json.dumps(r), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
