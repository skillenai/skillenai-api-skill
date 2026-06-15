#!/usr/bin/env python3
"""Monthly mention-share trend of a set of concepts on a Skillenai content index.

The time-series companion to `phrase_prevalence.py` (which gives cumulative
prevalence). For each calendar month (bucketed by a date field, default
`publishedAt`) this counts how many documents mention any phrase in each named
concept, plus the month's total document count, so you can plot mention *share*
over time. Share normalizes the crawl-volume / backfill bias that distorts
absolute monthly counts — when a crawler ramps up, absolute counts jump for every
concept, but the share of documents mentioning each concept stays comparable.

Each concept is a bool.should of `match_phrase` on the text field; concepts are
chunked across requests to stay under the WAF ~8KiB body limit. Documents with
`publishedAtSource=ingested_fallback` are excluded (their date is a crawl-time
guess, not a real publication date), and the date is clamped to [--start, --end).

Concept definitions come from a JSON file mapping label -> phrase | [phrases]:

    {
      "MCP": ["Model Context Protocol", "MCP server", "MCP tools"],
      "Code execution": ["code execution", "code interpreter", "code sandbox"],
      "LangGraph": "LangGraph"
    }

Usage:
    python scripts/mention_trends.py concepts.json --index prod-enriched-news \
        --start 2025-01-01 --end 2026-06-01 --out trends_news.csv
    python scripts/mention_trends.py concepts.json --index prod-enriched-blog --chunk 4

Output: CSV (month, total, <one column per concept>) to stdout and, with --out, a file.
Compute shares downstream as concept / total (per-10k or %). Pre-ramp months with
small `total` are noisy — drop months with total < ~100 before trusting the share.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


def get_config() -> tuple[str, str]:
    """Load API_URL and API_KEY. Precedence: env > ~/.skillenai/.env > $CLAUDE_PLUGIN_ROOT/.env > cwd .env."""
    load_dotenv(Path.home() / ".skillenai" / ".env")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        load_dotenv(Path(plugin_root) / ".env")
    load_dotenv()
    url = os.getenv("API_URL", "https://api.skillenai.com").rstrip("/")
    key = os.getenv("API_KEY", "")
    if not key:
        print("ERROR: API_KEY not set. Put it in ~/.skillenai/.env or export API_KEY.", file=sys.stderr)
        sys.exit(1)
    return url, key


def should_clause(phrases, field):
    if isinstance(phrases, str):
        phrases = [phrases]
    return {"bool": {"should": [{"match_phrase": {field: p}} for p in phrases],
                     "minimum_should_match": 1}}


def post(url, key, index, body, tries=6):
    for i in range(tries):
        r = requests.post(
            f"{url}/v1/query/search",
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            json={"query": body, "indices": [index]}, timeout=90)
        if r.status_code == 429:
            time.sleep(2 ** i)
            continue
        r.raise_for_status()
        d = r.json()
        if "aggregations" not in d:  # over-large body silently drops aggs
            time.sleep(2 ** i)
            continue
        return d
    raise RuntimeError("no aggregations after retries (body too large? chunk smaller)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("concepts", help="JSON file: {label: phrase | [phrases]}")
    ap.add_argument("--index", default="prod-enriched-news")
    ap.add_argument("--field", default="extractedText", help="text field to match_phrase on")
    ap.add_argument("--date-field", default="publishedAt")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-06-01")
    ap.add_argument("--chunk", type=int, default=5, help="concepts per request (lower if aggs drop out)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    url, key = get_config()
    concepts = json.loads(Path(a.concepts).read_text())
    labels = list(concepts)

    base = {"bool": {"filter": [{"range": {a.date_field: {"gte": a.start, "lt": a.end}}}],
                     "must_not": [{"term": {"publishedAtSource": "ingested_fallback"}}]}}
    histo = {"date_histogram": {"field": a.date_field, "calendar_interval": "month"}}

    # month totals
    d = post(url, key, a.index,
             {"size": 0, "track_total_hits": True, "query": base,
              "aggs": {"by_month": histo}})
    totals = {b["key_as_string"][:7]: b["doc_count"]
              for b in d["aggregations"]["by_month"]["buckets"]}

    counts = {lab: {} for lab in labels}
    for i in range(0, len(labels), a.chunk):
        chunk = labels[i:i + a.chunk]
        aggs = {lab: {"filter": should_clause(concepts[lab], a.field),
                      "aggs": {"m": histo}} for lab in chunk}
        d = post(url, key, a.index, {"size": 0, "query": base, "aggs": aggs})
        for lab in chunk:
            for b in d["aggregations"][lab]["m"]["buckets"]:
                counts[lab][b["key_as_string"][:7]] = b["doc_count"]
        time.sleep(1.0)

    months = sorted(totals)
    rows = [["month", "total"] + labels]
    for m in months:
        rows.append([m, totals[m]] + [counts[lab].get(m, 0) for lab in labels])
    out = "\n".join(",".join(str(x) for x in r) for r in rows)
    if a.out:
        Path(a.out).write_text(out + "\n")
        print(f"wrote {a.out} ({len(months)} months)", file=sys.stderr)
    print(out)


if __name__ == "__main__":
    main()
