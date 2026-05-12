"""Measure phrase prevalence across Skillenai OpenSearch indices.

Given a set of named concepts — each a list of one or more phrases — count how many
documents in each index contain *any* of that concept's phrases (a `match_phrase` on
`extractedText`). This is the standard way to ask "what fraction of job postings /
blog posts / news articles mention X?" for a long list of X at once.

The trick: instead of one query per concept per index (which blows the QUERY rate
limit), each request packs many concepts into a single `filters` aggregation, so
one request returns counts for ~10 concepts. Requests are chunked to stay under the
WAF body-inspection limit.

Concept definitions come from a JSON file mapping label -> phrase or list of phrases:

    {
      "RAGAS": "ragas",
      "LLM-as-a-judge": ["llm as a judge", "llm-as-a-judge", "llm as judge"],
      "LangSmith": ["langsmith"]
    }

Usage:
    python scripts/phrase_prevalence.py concepts.json
    python scripts/phrase_prevalence.py concepts.json --indices prod-enriched-jobs prod-enriched-blog
    python scripts/phrase_prevalence.py concepts.json --field extractedText --chunk 10 --json out.json
    python scripts/phrase_prevalence.py concepts.json --query-phrases "LLM" "large language model"   # restrict denominator

Output: a table with raw counts and per-10k-doc rates per index, sorted by the first
index's count. With --json, also dumps {"totals": {...}, "results": {...}}.
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

DEFAULT_INDICES = ["prod-enriched-jobs", "prod-enriched-blog", "prod-enriched-news"]


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


def phrase_clause(field: str, phrases: list[str]) -> dict:
    if len(phrases) == 1:
        return {"match_phrase": {field: phrases[0]}}
    return {"bool": {"should": [{"match_phrase": {field: p}} for p in phrases], "minimum_should_match": 1}}


def chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def search(url: str, key: str, body: dict, retries: int = 5, backoff: float = 9.0) -> dict:
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{url}/v1/query/search",
                headers={"X-API-Key": key, "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json()
            if "aggregations" in d:
                return d
        except requests.RequestException as e:
            print(f"  request error (attempt {attempt + 1}): {e}", file=sys.stderr)
        time.sleep(backoff)
    return {}


def run_index(url, key, index, concepts, field, chunk, base_query, sleep) -> tuple[int, dict[str, int]]:
    total = 0
    counts: dict[str, int] = {}
    for sub in chunks(list(concepts.items()), chunk):
        body = {
            "query": {
                "size": 0,
                "track_total_hits": True,
                "query": base_query,
                "aggs": {"concepts": {"filters": {"filters": {label: phrase_clause(field, ph) for label, ph in sub}}}},
            },
            "indices": [index],
        }
        d = search(url, key, body)
        if not d:
            print(f"  WARNING: no result for chunk in {index}: {[s[0] for s in sub]}", file=sys.stderr)
            continue
        total = d.get("total", total)
        for label, bucket in d["aggregations"]["concepts"]["buckets"].items():
            counts[label] = bucket["doc_count"]
        time.sleep(sleep)
    return total, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("concepts_file", help="JSON file mapping label -> phrase or list of phrases")
    ap.add_argument("--indices", nargs="+", default=DEFAULT_INDICES, help="OpenSearch indices to query")
    ap.add_argument("--field", default="extractedText", help="text field to match_phrase against (default: extractedText)")
    ap.add_argument("--chunk", type=int, default=10, help="concepts per request (lower if you hit WAF body limits; default 10)")
    ap.add_argument("--sleep", type=float, default=1.8, help="seconds between requests (QUERY tier is ~50/min; default 1.8)")
    ap.add_argument("--query-phrases", nargs="*", default=None,
                    help="if set, restrict the denominator to docs containing ANY of these phrases (e.g. 'LLM' 'large language model')")
    ap.add_argument("--json", dest="json_out", default=None, help="also write {totals, results} to this JSON path")
    args = ap.parse_args()

    raw = json.loads(Path(args.concepts_file).read_text())
    concepts: dict[str, list[str]] = {k: ([v] if isinstance(v, str) else list(v)) for k, v in raw.items()}

    url, key = get_config()
    base_query = {"match_all": {}}
    if args.query_phrases:
        base_query = {"bool": {"should": [{"match_phrase": {args.field: p}} for p in args.query_phrases],
                                "minimum_should_match": 1}}

    totals: dict[str, int] = {}
    results: dict[str, dict[str, int]] = {}
    for idx in args.indices:
        t, c = run_index(url, key, idx, concepts, args.field, args.chunk, base_query, args.sleep)
        totals[idx] = t
        results[idx] = c
        print(f"# {idx}: denominator={t:,}  concepts_resolved={len(c)}", file=sys.stderr)

    # table
    primary = args.indices[0]
    order = sorted(concepts.keys(), key=lambda n: -results[primary].get(n, 0))
    hdr = f"{'concept':40}"
    for idx in args.indices:
        short = idx.replace("prod-enriched-", "")
        hdr += f" | {short:>12} {'/10k':>7}"
    print(hdr)
    print("-" * len(hdr))
    for n in order:
        row = f"{n:40}"
        for idx in args.indices:
            c = results[idx].get(n, 0)
            rate = (c / totals[idx] * 10000) if totals[idx] else 0.0
            row += f" | {c:12d} {rate:7.1f}"
        print(row)
    print("\ndenominators: " + "  ".join(f"{i.replace('prod-enriched-','')}={totals[i]:,}" for i in args.indices))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"totals": totals, "results": results}, indent=2))
        print(f"\nwrote {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
