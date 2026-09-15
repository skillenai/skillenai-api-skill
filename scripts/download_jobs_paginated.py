"""Download per-job data from the Skillenai Job Index with arbitrary filter segments.

Generalizes the per-job downloader used in Skillenai analyses (geography, role,
seniority). Takes a YAML/JSON-like Python dict of segment definitions and pulls
all matching jobs with metadata and resolved skills, writing one row per job.

Handles 429 rate limits with exponential backoff. Default page size 100, sleeps
~1.4s between pages (~43 req/min) to stay under the measured 50 req/min QUERY tier (read
`x-ratelimit-remaining` on any response to confirm the live policy).

Result sets larger than 10,000 are partitioned on the documentId hash: the API
silently returns duplicate pages past `from=10000` rather than erroring, so a
naive walk would quietly truncate. Every fetch asserts unique-row recovery.

Usage (as a library):

    from download_jobs_paginated import download_segments

    segments = {
        "bay_area": {"must": [{"geo_distance": {"distance": "80km", "locationGeocode": {"lat": 37.77, "lon": -122.42}}}]},
        "non_us":   {"must_not": [{"match": {"locationCountry": "US"}}]},
    }
    rows = download_segments(
        segments,
        base_must=[{"terms": {"role.keyword": ["Data Scientist"]}}],
        base_must_not=[{"term": {"companyCanonicalName.keyword": "Speechify"}}],
        source_fields=["documentId", "role", "seniorityLevel", "salaryMin",
                       "salaryMax", "salaryCurrency", "locationCountry", "entities"],
    )
    # rows: list of dicts, one per job, with resolved skills under row["skills"]

Or as a CLI using a JSON config file:

    python download_jobs_paginated.py config.json -o jobs.csv

Where config.json is:
    {
      "segments": { "bay_area": {"must": [...]}, "non_us": {"must_not": [...]} },
      "base_must": [...],
      "base_must_not": [...],
      "source_fields": ["documentId", "role", "entities", ...]
    }
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Iterable


PAGE_SIZE = 100
DEFAULT_SOURCE = [
    "documentId", "role", "seniorityLevel", "locationCountry", "locationCity",
    "remote", "workModel", "salaryMin", "salaryMax", "salaryCurrency",
    "entities", "companyCanonicalName",
]


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def api_search(body: dict, max_retries: int = 8) -> dict:
    url = _env("API_URL") + "/v1/query/search"
    headers = {"X-API-Key": _env("API_KEY"), "Content-Type": "application/json"}
    data = json.dumps({"query": body, "indices": ["prod-enriched-jobs"]}).encode()
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 3 * (2 ** attempt)
                print(f"    Rate limited, waiting {wait}s...", file=sys.stderr, flush=True)
                time.sleep(wait)
            else:
                raise


def extract_skills(entities) -> list[str]:
    if not entities:
        return []
    return sorted({
        e["resolved"]["canonicalName"]
        for e in entities
        if isinstance(e, dict)
        and e.get("resolved", {}).get("entityType") == "skill"
        and e.get("resolved", {}).get("canonicalName")
    })


# OpenSearch `index.max_result_window`. The API does NOT raise past this -- it
# silently returns duplicate pages, so a naive `from`-walk over a larger result
# set yields exactly RESULT_WINDOW unique docs and looks like a clean success.
RESULT_WINDOW = 10_000
HEX = "0123456789abcdef"
# The `query` tier is 50 req/min (read `x-ratelimit-policy` to confirm). Pacing at
# the ceiling trips 429s once the per-shard count queries are added on top, so aim
# at ~43 req/min. Measured 2026-09-14: 1.3s produced ~26 retried 429s on a 258-page
# pull; the retries recover but cost ~40% wall-clock.
PAGE_SLEEP = 1.4


def _hex_shards(n_shards: int = 16) -> list[tuple[str, str]]:
    """Split the documentId hash space into contiguous [lo, hi) hex ranges.

    documentId is md5(sourceUrl), so it is uniform over the hex space: equal-width
    ranges give equal-sized shards regardless of platform, role or date.
    """
    if n_shards <= 1:
        return [("0", "g")]
    step = len(HEX) // n_shards
    edges = [HEX[i * step] for i in range(n_shards)] + ["g"]
    return list(zip(edges[:-1], edges[1:]))


def _walk_shard(query: dict, source_fields: list[str], lo: str, hi: str,
                page_sleep: float) -> list[dict]:
    """Page one documentId shard with a CURSOR, not an offset.

    `from`-based paging silently caps at RESULT_WINDOW (the API returns 200 OK with
    duplicate pages rather than erroring), so we advance a `documentId > last_seen`
    cursor instead. That has no window ceiling and is resumable from the last id.
    """
    hits: list[dict] = []
    cursor = None
    while True:
        rng = {"gte": lo, "lt": hi} if cursor is None else {"gt": cursor, "lt": hi}
        q = json.loads(json.dumps(query))
        q["bool"].setdefault("filter", []).append({"range": {"documentId": rng}})
        page = api_search({"size": PAGE_SIZE, "query": q, "_source": source_fields,
                           "sort": [{"documentId": "asc"}]}).get("hits", [])
        if not page:
            return hits
        hits.extend(page)
        nxt = page[-1].get("documentId") or page[-1].get("_id")
        if not nxt or nxt == cursor:      # no forward progress: bail rather than spin
            return hits
        cursor = nxt
        if len(page) < PAGE_SIZE:
            return hits
        time.sleep(page_sleep)


def fetch_segment(
    segment_name: str,
    segment_def: dict,
    base_must: list,
    base_must_not: list,
    source_fields: list[str],
    page_sleep: float = PAGE_SLEEP,
) -> list[dict]:
    must = base_must + segment_def.get("must", [])
    must_not = base_must_not + segment_def.get("must_not", [])
    query = {"bool": {"must": must, "must_not": must_not}}

    total = api_search({"size": 0, "query": query, "track_total_hits": True}).get("total", 0)
    print(f"  {segment_name}: {total} jobs", file=sys.stderr, flush=True)

    all_hits: list[dict] = []
    shards = _hex_shards(1) if total <= RESULT_WINDOW else _hex_shards(16)
    for lo, hi in shards:
        all_hits.extend(_walk_shard(query, source_fields, lo, hi, page_sleep))
        time.sleep(page_sleep)

    # The API returns 200 on over-window reads, so verify rather than trust.
    unique = {h.get("documentId") or h.get("_id") for h in all_hits}
    unique.discard(None)
    if total and len(unique) < total * 0.99:
        raise RuntimeError(
            f"{segment_name}: recovered {len(unique):,} unique of {total:,} expected "
            f"({100*len(unique)/total:.1f}%). Pagination truncated silently.")
    print(f"    recovered {len(unique):,}/{total:,} unique "
          f"({100*len(unique)/max(total,1):.1f}%)", file=sys.stderr, flush=True)

    seen = set()
    rows = []
    for hit in all_hits:
        src = hit.get("source", hit)
        did = src.get("documentId") or hit.get("_id", "")
        if did in seen:
            continue
        seen.add(did)
        row = {"segment": segment_name, "documentId": did}
        for fld in source_fields:
            if fld == "entities":
                continue  # expanded into skills below
            row[fld] = src.get(fld, "")
        row["skills"] = "|".join(extract_skills(src.get("entities")))
        rows.append(row)
    return rows


def download_segments(
    segments: dict,
    base_must: list | None = None,
    base_must_not: list | None = None,
    source_fields: list[str] | None = None,
    between_segment_sleep: float = 2.0,
) -> list[dict]:
    base_must = base_must or []
    base_must_not = base_must_not or []
    source_fields = source_fields or DEFAULT_SOURCE

    rows = []
    for name, defn in segments.items():
        rows.extend(fetch_segment(name, defn, base_must, base_must_not, source_fields))
        time.sleep(between_segment_sleep)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("config", help="JSON config with segments, base_must, base_must_not, source_fields")
    p.add_argument("-o", "--output", default="jobs.csv")
    args = p.parse_args()

    cfg = json.load(open(args.config))
    rows = download_segments(
        cfg["segments"],
        base_must=cfg.get("base_must"),
        base_must_not=cfg.get("base_must_not"),
        source_fields=cfg.get("source_fields"),
    )
    if not rows:
        print("No rows fetched", file=sys.stderr)
        sys.exit(1)
    fieldnames = list(rows[0].keys())
    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
