"""Aggregate top blog/news authors writing about a topic, with junk-author filtering.

Pattern: query a content index (e.g. prod-enriched-blog) for documents whose
extractedText matches a topic (one phrase or an OR-group of spellings), aggregate
by author keyword, then post-filter out vendor team accounts, email addresses,
HTML fragments, domain-as-author bylines, multi-author blobs, and the 333-domain
synthetic-persona content-farm network identified in SKI-376.

This is the "who writes about X?" primitive used in tutorial-coverage-gap,
influential-tech-bloggers, and any other author-influence ranking.

Usage:
    python author_aggregation.py "Looker" \
        --index prod-enriched-blog \
        --pbn-denylist /Users/jrand/git-repos/skillenai-notebooks/synthetic-breakout-may-2026/network_domains_seed.csv \
        --min-posts 2 \
        --top 20 \
        --json out.json

    # OR-group of phrases:
    python author_aggregation.py "Apache Airflow" "Airflow" --index prod-enriched-blog --top 20
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


# ---------- Junk-author filter ----------
JUNK_GENERIC = {"", "admin", "Admin", "Author", "author", "Unknown", "unknown", "user", "Editor", "editor"}
JUNK_SUFFIXES = ("Team", "team", "Editors", "Staff", "Bot", "News")
JUNK_BOTS = {"BeauHD", "EditorDavid", "msmash", "Soulskill", "Slashdot", "feedfetcher"}
JUNK_REGEXES = [
    re.compile(r"^.+@.+\..+$"),                                       # email
    re.compile(r"<[^>]+>"),                                            # html tags
    re.compile(r"https?://"),                                         # URL
    re.compile(r"gravatar\.com"),                                     # gravatar
    re.compile(r"\.(com|net|org|io|ai|cloud|website|page|top)$", re.I),  # domain-as-author
]


def is_junk_author(name: str) -> bool:
    if not name or name in JUNK_GENERIC or name in JUNK_BOTS:
        return True
    if name.count(",") >= 2:
        return True
    if any(name.endswith(s) for s in JUNK_SUFFIXES):
        return True
    for rgx in JUNK_REGEXES:
        if rgx.search(name):
            return True
    if len(name) < 2 or len(name) > 80:
        return True
    return False


# ---------- API ----------
def get_config() -> tuple[str, str]:
    load_dotenv(Path.home() / ".skillenai" / ".env")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        load_dotenv(Path(plugin_root) / ".env")
    load_dotenv()
    url = os.getenv("API_URL", "https://api.skillenai.com").rstrip("/")
    key = os.getenv("API_KEY") or os.getenv("SKILLENAI_INSIGHTS_API_KEY", "")
    if not key:
        print("ERROR: API_KEY (or SKILLENAI_INSIGHTS_API_KEY) not set.", file=sys.stderr)
        sys.exit(1)
    return url, key


def phrase_clause(phrases: list[str], field: str) -> dict:
    if len(phrases) == 1:
        return {"match_phrase": {field: phrases[0]}}
    return {"bool": {"should": [{"match_phrase": {field: p}} for p in phrases], "minimum_should_match": 1}}


def load_pbn(path: Path) -> list[str]:
    if not path or not path.exists():
        return []
    rdr = csv.reader(path.open())
    next(rdr)
    return [row[0].strip() for row in rdr if row and row[0].strip()]


def fetch_authors(url: str, key: str, index: str, phrases: list[str],
                  denylist: list[str], field: str, agg_size: int) -> tuple[int, list[dict]]:
    must_not = [{"terms": {"domain": denylist}}] if denylist else []
    body = {
        "indices": [index],
        "query": {
            "size": 0,
            "track_total_hits": True,
            "query": {
                "bool": {
                    "must": [phrase_clause(phrases, field), {"exists": {"field": "author"}}],
                    "must_not": must_not,
                }
            },
            "aggs": {
                "authors": {
                    "terms": {"field": "author", "size": agg_size},
                    "aggs": {
                        "avg_auth": {"avg": {"field": "authorAuthority"}},
                        "top_domain": {"terms": {"field": "domain", "size": 1}},
                    }
                }
            }
        }
    }
    for attempt in range(5):
        r = requests.post(f"{url}/v1/query/search",
                          headers={"X-API-Key": key, "Content-Type": "application/json"},
                          json=body, timeout=60)
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        d = r.json()
        return d.get("total", 0), d["aggregations"]["authors"]["buckets"]
    return 0, []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phrases", nargs="+", help="one or more phrases to match (OR-grouped)")
    ap.add_argument("--index", default="prod-enriched-blog")
    ap.add_argument("--field", default="extractedText")
    ap.add_argument("--pbn-denylist", type=Path, default=None,
                    help="CSV file with header row, one domain per line, to exclude")
    ap.add_argument("--extra-denylist", nargs="*", default=[],
                    help="additional domains to exclude (e.g. SKI-274 ATS noise)")
    ap.add_argument("--min-posts", type=int, default=2)
    ap.add_argument("--agg-size", type=int, default=100, help="raw OpenSearch terms agg size before junk filter")
    ap.add_argument("--top", type=int, default=20, help="rows to print/keep after filter")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    url, key = get_config()
    denylist = load_pbn(args.pbn_denylist) + list(args.extra_denylist)
    total, buckets = fetch_authors(url, key, args.index, args.phrases, denylist, args.field, args.agg_size)

    rows = []
    for b in buckets:
        name = b["key"]
        cnt = b["doc_count"]
        if is_junk_author(name) or cnt < args.min_posts:
            continue
        rows.append({
            "author": name,
            "posts": cnt,
            "avg_authority": b["avg_auth"]["value"] or 0.0,
            "top_domain": b["top_domain"]["buckets"][0]["key"] if b["top_domain"]["buckets"] else "",
        })
    rows.sort(key=lambda r: -r["posts"])
    rows = rows[: args.top]

    print(f"# {args.index}: {total:,} matching docs; {len(rows)} clean authors (after junk filter, min_posts={args.min_posts})")
    print(f"{'posts':>5}  {'authority':>9}  {'author':35}  domain")
    print("-" * 80)
    for r in rows:
        print(f"{r['posts']:>5,}  {r['avg_authority']:>9.2f}  {r['author']:35}  {r['top_domain']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"total_docs": total, "authors": rows}, indent=2))
        print(f"\nwrote {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
