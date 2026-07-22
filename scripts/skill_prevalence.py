"""Measure structured *skill-requirement* prevalence within a job/document cohort.

The entity-resolved companion to `phrase_prevalence.py`. Instead of `match_phrase` on
free text (which catches incidental prose), this counts documents whose resolved
`entities` actually list a skill — i.e. what a posting genuinely *requires*, not merely
mentions. Use it for "of the postings in cohort C, what fraction also require skill Y?"

A cohort is defined by any combination of:
  - an index and optional `locationCountry` filter,
  - optional exact `role.keyword` titles (--roles),
  - optional *required* skill groups (--require-file): the doc must carry >=1 skill from
    EACH group (AND across groups, OR within a group's spelling variants),
  - optional excluded companies (--exclude-company).

Probe skills (the co-occurrence targets) come from a JSON file mapping label -> canonical
skill name or list of variants (OR-grouped, to absorb entity-resolver duplicates):

    {
      "Python": "Python",
      "Ruby": ["Ruby", "Ruby on Rails"],
      "LangGraph": "LangGraph",
      "LLMs": ["LLMs", "LLM", "Large language models (LLMs)", "large language models"]
    }

The same JSON shape works for --require-file. Each probe/requirement is a nested query on
`entities.resolved.canonicalName.keyword` (entityType == "skill"); prevalence is measured
with a single document-level `filters` aggregation, chunked under the WAF body limit.

IMPORTANT (avoid circularity): if you select the cohort on a skill, do NOT also probe for
that same skill — put the *conceptual* selection skills in --require-file and keep the
*specific* skills you are measuring in the probes file. See the skn-insights notes.

Usage:
    # Of Software-Engineer postings that require a GenAI skill, what languages do they want?
    python scripts/skill_prevalence.py languages.json \
        --roles "Software Engineer" "Backend Engineer" "Full Stack Engineer" \
        --require-file genai_skills.json --country US --exclude-company Speechify

    python scripts/skill_prevalence.py frameworks.json --index prod-enriched-jobs --json out.json

Output: prevalence table (count and % of cohort) sorted by prevalence, plus the cohort N.
With --json, dumps {"cohort_n": N, "counts": {...}}.
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

SKILL_FIELD = "entities.resolved.canonicalName.keyword"
TYPE_FIELD = "entities.resolved.entityType"


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


def has_skill(variants: list[str]) -> dict:
    """Nested query: document has a skill entity whose canonicalName is in variants."""
    return {"nested": {"path": "entities", "query": {"bool": {"must": [
        {"term": {TYPE_FIELD: "skill"}},
        {"terms": {SKILL_FIELD: variants}},
    ]}}}}


def normalize(raw: dict) -> dict[str, list[str]]:
    return {k: ([v] if isinstance(v, str) else list(v)) for k, v in raw.items()}


def chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def cohort_query(country, roles, require_groups, exclude_companies) -> dict:
    filt: list[dict] = []
    must: list[dict] = []
    must_not: list[dict] = []
    if country:
        filt.append({"term": {"locationCountry": country}})
    if roles:
        must.append({"terms": {"role.keyword": roles}})
    for group in require_groups:
        must.append(has_skill(group))
    for c in exclude_companies:
        must_not.append({"term": {"companyCanonicalName.keyword": c}})
    return {"bool": {"filter": filt, "must": must, "must_not": must_not}}


def search(url, key, body, retries=6, backoff=6.5) -> dict:
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{url}/v1/query/search",
                headers={"X-API-Key": key, "Content-Type": "application/json"},
                json=body, timeout=90,
            )
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"  request error (attempt {attempt + 1}): {e}", file=sys.stderr)
            time.sleep(backoff)
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("probes_file", help="JSON file mapping label -> skill canonicalName or list of variants")
    ap.add_argument("--index", default="prod-enriched-jobs", help="OpenSearch index (default: prod-enriched-jobs)")
    ap.add_argument("--country", default=None, help="restrict to this locationCountry ISO-2 code, e.g. US")
    ap.add_argument("--roles", nargs="*", default=None, help="exact role.keyword titles defining the cohort")
    ap.add_argument("--require-file", default=None,
                    help="JSON of required skill groups (AND across labels, OR within variants) the cohort must have")
    ap.add_argument("--exclude-company", nargs="*", default=None,
                    help="companyCanonicalName.keyword values to exclude (e.g. spam employers)")
    ap.add_argument("--chunk", type=int, default=8, help="probes per request; lower if you hit WAF body limits (default 8)")
    ap.add_argument("--sleep", type=float, default=6.5, help="seconds between requests (QUERY tier ~10/min; default 6.5)")
    ap.add_argument("--json", dest="json_out", default=None, help="also write {cohort_n, counts} to this JSON path")
    args = ap.parse_args()

    probes = normalize(json.loads(Path(args.probes_file).read_text()))
    require_groups = list(normalize(json.loads(Path(args.require_file).read_text())).values()) if args.require_file else []
    url, key = get_config()
    cq = cohort_query(args.country, args.roles, require_groups, args.exclude_company or [])

    n = search(url, key, {"query": {"size": 0, "track_total_hits": True, "query": cq}, "indices": [args.index]}).get("total", 0)
    if not n:
        print("Cohort is empty (N=0). Check --roles / --require-file / --country.", file=sys.stderr)
        sys.exit(2)
    time.sleep(args.sleep)

    counts: dict[str, int] = {}
    for sub in chunks(list(probes.items()), args.chunk):
        body = {"query": {"size": 0, "track_total_hits": True, "query": cq,
                          "aggs": {"probes": {"filters": {"filters": {label: has_skill(v) for label, v in sub}}}}},
                "indices": [args.index]}
        d = search(url, key, body)
        aggs = d.get("aggregations", {})
        if "probes" not in aggs:
            print(f"  WARNING: no aggregation for chunk {[s[0] for s in sub]} (body too big? drop --chunk)", file=sys.stderr)
            continue
        for label, bucket in aggs["probes"]["buckets"].items():
            counts[label] = bucket["doc_count"]
        time.sleep(args.sleep)

    print(f"# {args.index}: cohort N = {n:,}")
    print(f"{'skill':28} | {'count':>7} | {'% of cohort':>11}")
    print("-" * 52)
    for label in sorted(probes, key=lambda l: -counts.get(l, 0)):
        c = counts.get(label, 0)
        print(f"{label:28} | {c:7,} | {100*c/n:10.1f}%")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"cohort_n": n, "counts": counts}, indent=2))
        print(f"# wrote {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
