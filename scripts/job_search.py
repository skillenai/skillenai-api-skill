"""Search for jobs using the Skillenai API with multi-signal ranking.

Supports text queries, skill boosts, seniority filtering, salary thresholds,
geographic proximity, and recency decay.

Usage:
    python scripts/job_search.py "machine learning engineer"
    python scripts/job_search.py "AI engineer NLP LLMs" --seniority senior --min-salary 180000
    python scripts/job_search.py "software engineer" --remote --size 20
    python scripts/job_search.py "data scientist" --location 37.77,-122.42 --radius 100km
    python scripts/job_search.py "ML engineer" --skills Python,PyTorch --seniority senior
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv


def get_config() -> tuple[str, str]:
    """Load API_URL and API_KEY from .env."""
    load_dotenv()
    url = os.getenv("API_URL", "https://api.skillenai.com").rstrip("/")
    key = os.getenv("API_KEY", "")
    if not key:
        print("ERROR: API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)
    return url, key


def resolve_skills(url: str, key: str,
                   skill_names: list[str]) -> list[dict]:
    """Resolve skill names to entity IDs via the resolution endpoint."""
    names = [{"name": s.strip(), "entity_type": "skill"}
             for s in skill_names]
    resp = requests.post(
        f"{url}/v1/resolution/entities",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        json={"names": names, "mode": "auto", "limit": 1},
        timeout=30,
    )
    if not resp.ok:
        print(f"WARNING: Could not resolve skills: "
              f"HTTP {resp.status_code}")
        return []

    boosts = []
    results = resp.json().get("results", [])
    for result in results:
        matches = result.get("matches", [])
        if matches:
            entity_id = matches[0]["entity_id"]
            name = matches[0].get("canonical_name",
                                  result["query"]["name"])
            boosts.append({
                "entity_id": entity_id,
                "weight": 10.0,
            })
            print(f"  Resolved '{result['query']['name']}' -> "
                  f"{name} ({entity_id})")
        else:
            print(f"  WARNING: Could not resolve "
                  f"'{result['query']['name']}'")

    return boosts


def search_jobs(url: str, key: str, body: dict) -> dict:
    """Execute job search and return response."""
    resp = requests.post(
        f"{url}/v1/jobs/search",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    if not resp.ok:
        print(f"ERROR: HTTP {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    return resp.json()


def format_salary(source: dict) -> str:
    """Format salary range from a job source document."""
    sal_min = source.get("salaryMin")
    sal_max = source.get("salaryMax")
    if sal_min and sal_max:
        return f"${sal_min:,}-${sal_max:,}"
    elif sal_max:
        return f"Up to ${sal_max:,}"
    elif sal_min:
        return f"From ${sal_min:,}"
    return "Not listed"


def print_results(results: dict, verbose: bool = False) -> None:
    """Print formatted job search results."""
    total = results.get("total", 0)
    hits = results.get("hits", [])

    print(f"\n{'=' * 70}")
    print(f"  {total:,} total matches  |  Showing {len(hits)} results")
    print(f"{'=' * 70}")

    for i, hit in enumerate(hits, 1):
        source = hit.get("source", {})
        title = source.get("title", "Untitled")
        company = source.get("company", "Unknown")
        location = source.get("location", "Not specified")
        work_model = source.get("workModel", "")
        salary = format_salary(source)
        posted = source.get("postedAt", "")[:10]
        score = hit.get("score", 0)
        skills = source.get("skills", [])

        print(f"\n  {i}. {title}")
        print(f"     Company:  {company}")
        print(f"     Location: {location}"
              + (f" ({work_model})" if work_model else ""))
        print(f"     Salary:   {salary}")
        print(f"     Posted:   {posted}")
        if skills:
            print(f"     Skills:   {', '.join(skills[:10])}")
        if verbose:
            print(f"     Score:    {score:.4f}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Search for jobs via the Skillenai API")
    parser.add_argument(
        "query",
        help="Search query text")
    parser.add_argument(
        "--skills", type=str, default=None,
        help="Comma-separated skill names to boost "
             "(resolved to entity IDs automatically)")
    parser.add_argument(
        "--seniority", type=str, default=None,
        choices=["entry", "mid", "senior", "staff", "principal",
                 "director", "vp"],
        help="Target seniority level")
    parser.add_argument(
        "--min-salary", type=int, default=None,
        help="Minimum salary threshold")
    parser.add_argument(
        "--remote", action="store_true",
        help="Filter to remote jobs only")
    parser.add_argument(
        "--location", type=str, default=None,
        help="Lat,lon for geo-distance ranking (e.g., 37.77,-122.42)")
    parser.add_argument(
        "--radius", type=str, default="50km",
        help="Radius for geo filtering (default: 50km)")
    parser.add_argument(
        "--recency", type=str, default="30d",
        help="Recency decay half-life (default: 30d)")
    parser.add_argument(
        "--size", type=int, default=10,
        help="Number of results (default: 10, max 100)")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show relevance scores")
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of formatted text")
    args = parser.parse_args()

    url, key = get_config()

    # Build request body
    body: dict = {
        "query": args.query,
        "size": min(args.size, 100),
        "recency_decay": args.recency,
    }

    if args.seniority:
        body["seniority"] = args.seniority

    if args.min_salary:
        body["min_salary"] = args.min_salary

    if args.remote:
        body["filters"] = {"workModel": "remote"}

    if args.location:
        parts = args.location.split(",")
        if len(parts) == 2:
            body["location"] = [float(parts[0]), float(parts[1])]
            body["location_radius"] = args.radius
        else:
            print("ERROR: --location must be lat,lon (e.g., 37.77,-122.42)")
            sys.exit(1)

    # Resolve skill names to entity IDs if provided
    if args.skills:
        skill_names = [s.strip() for s in args.skills.split(",")]
        print(f"Resolving {len(skill_names)} skill(s)...")
        boosts = resolve_skills(url, key, skill_names)
        if boosts:
            body["skill_boosts"] = boosts

    print(f"Searching for: {args.query}")
    results = search_jobs(url, key, body)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_results(results, verbose=args.verbose)


if __name__ == "__main__":
    main()
