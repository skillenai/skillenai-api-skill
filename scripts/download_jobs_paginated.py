"""Download per-job data from the Skillenai Job Index with arbitrary filter segments.

Generalizes the per-job downloader used in Skillenai analyses (geography, role,
seniority). Takes a YAML/JSON-like Python dict of segment definitions and pulls
all matching jobs with metadata and resolved skills, writing one row per job.

Handles 429 rate limits with exponential backoff. Default page size 100, sleeps
~1.8s between pages to stay under the ~10 req/min QUERY tier.

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


def fetch_segment(
    segment_name: str,
    segment_def: dict,
    base_must: list,
    base_must_not: list,
    source_fields: list[str],
    page_sleep: float = 1.8,
) -> list[dict]:
    must = base_must + segment_def.get("must", [])
    must_not = base_must_not + segment_def.get("must_not", [])
    query = {"bool": {"must": must, "must_not": must_not}}
    body = {"size": PAGE_SIZE, "from": 0, "query": query, "_source": source_fields}

    data = api_search(body)
    total = data.get("total", 0)
    print(f"  {segment_name}: {total} jobs", file=sys.stderr, flush=True)

    all_hits = data.get("hits", [])
    offset = PAGE_SIZE
    while offset < total:
        body["from"] = offset
        data = api_search(body)
        all_hits.extend(data.get("hits", []))
        offset += PAGE_SIZE
        time.sleep(page_sleep)

    rows = []
    for hit in all_hits:
        src = hit.get("source", hit)
        row = {"segment": segment_name, "documentId": src.get("documentId") or hit.get("_id", "")}
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
