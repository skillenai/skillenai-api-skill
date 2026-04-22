"""Generate a comprehensive EDA report from the Skillenai Data Products API.

Queries all major endpoints and produces a markdown report with insights
about dataset composition, topic trends, entity co-occurrence, and more.

Usage:
    python scripts/eda_report.py
    python scripts/eda_report.py --output reports/eda-2026-04-17.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
        print("ERROR: API_KEY not set. Put it in ~/.skillenai/.env or export API_KEY.")
        sys.exit(1)
    return url, key


def api_get(url: str, key: str, path: str, params: dict | None = None,
            timeout: int = 60) -> dict:
    """GET request, return parsed JSON or error dict."""
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{url}{path}",
            headers={"X-API-Key": key},
            params=params or {},
            timeout=timeout,
        )
        elapsed = round((time.monotonic() - start) * 1000)
        if resp.ok:
            return {"ok": True, "body": resp.json(), "ms": elapsed}
        return {"ok": False, "status": resp.status_code,
                "error": f"HTTP {resp.status_code}",
                "detail": resp.text[:500], "ms": elapsed}
    except Exception as e:
        elapsed = round((time.monotonic() - start) * 1000)
        return {"ok": False, "error": str(e), "ms": elapsed}


def api_post(url: str, key: str, path: str, body: dict,
             timeout: int = 60) -> dict:
    """POST request, return parsed JSON or error dict."""
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{url}{path}",
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        elapsed = round((time.monotonic() - start) * 1000)
        if resp.ok:
            return {"ok": True, "body": resp.json(), "ms": elapsed}
        return {"ok": False, "status": resp.status_code,
                "error": f"HTTP {resp.status_code}",
                "detail": resp.text[:500], "ms": elapsed}
    except Exception as e:
        elapsed = round((time.monotonic() - start) * 1000)
        return {"ok": False, "error": str(e), "ms": elapsed}


def require(resp: dict, label: str) -> dict:
    """Unwrap response or print error and return empty dict."""
    if not resp["ok"]:
        print(f"  ERROR on {label}: {resp.get('error', 'unknown')} "
              f"{resp.get('detail', '')}")
        return {}
    return resp["body"]


def collect_data(url: str, key: str) -> dict:
    """Call all major API endpoints and collect raw data."""
    data: dict = {}

    print("Collecting data from API...")

    # Health & version
    print("  /v1/health")
    data["health"] = require(api_get(url, key, "/v1/health"), "health")
    print("  /v1/version")
    data["version"] = require(api_get(url, key, "/v1/version"), "version")

    # Analytics: document counts
    print("  /v1/analytics/counts")
    data["counts"] = require(
        api_get(url, key, "/v1/analytics/counts"), "counts")

    # Analytics: entity co-occurrence
    print("  /v1/analytics/entity-cooccurrence")
    data["cooccurrence"] = require(
        api_get(url, key, "/v1/analytics/entity-cooccurrence",
                {"limit": "50"}),
        "cooccurrence")

    # Analytics: topic trends
    print("  /v1/analytics/topic-trends")
    data["topic_trends"] = require(
        api_get(url, key, "/v1/analytics/topic-trends", {"limit": "50"}),
        "topic_trends")

    # Analytics: skills by role (top roles)
    for role in ["Data Scientist", "ML Engineer", "AI Engineer",
                 "Software Engineer", "Data Engineer"]:
        print(f"  /v1/analytics/skills-by-role?role={role}")
        data[f"skills_{role}"] = require(
            api_get(url, key, "/v1/analytics/skills-by-role",
                    {"role": role}),
            f"skills-by-role({role})")

    # Entity counts by type via SQL
    print("  /v1/query/sql (entity counts)")
    data["entity_counts"] = require(
        api_post(url, key, "/v1/query/sql", {
            "sql": ("SELECT entity_type, count(*) AS n "
                    "FROM skillenai.entities "
                    "GROUP BY entity_type ORDER BY n DESC"),
        }),
        "entity_counts")

    # Sample job search
    print("  /v1/jobs/search (sample)")
    data["sample_jobs"] = require(
        api_post(url, key, "/v1/jobs/search", {
            "query": "machine learning engineer",
            "size": 10,
        }),
        "sample_jobs")

    # Catalog
    print("  /v1/catalog")
    data["catalog"] = require(api_get(url, key, "/v1/catalog"), "catalog")

    print("  Done collecting data.\n")
    return data


def generate_report(data: dict) -> str:
    """Analyze collected data and produce a markdown report."""
    lines: list[str] = []

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    version_info = data.get("version", {})

    lines.append("# Skillenai Data Products -- Exploratory Data Analysis")
    lines.append("")
    lines.append(f"**Generated:** {now_str}  ")
    lines.append(f"**API Version:** {version_info.get('service', '?')} "
                 f"{version_info.get('version', '?')}  ")
    lines.append("**Data source:** Skillenai Data Products API")
    lines.append("")

    # --- Section 1: Dataset Overview ---
    lines.append("## 1. Dataset Overview")
    lines.append("")

    counts = data.get("counts", {})
    count_buckets = counts.get("buckets", [])
    total_docs = counts.get("total",
                            sum(b["count"] for b in count_buckets))

    lines.append(f"The platform currently indexes **{total_docs:,} documents** "
                 f"across {len(count_buckets)} source types.")
    lines.append("")

    lines.append("### Documents by Source Type")
    lines.append("")
    lines.append("| Source Type | Documents | Share |")
    lines.append("|------------|----------:|------:|")
    for bucket in sorted(count_buckets, key=lambda b: -b["count"]):
        pct = bucket["count"] / max(total_docs, 1) * 100
        lines.append(f"| {bucket['source_type']} | {bucket['count']:,} "
                     f"| {pct:.1f}% |")
    lines.append(f"| **Total** | **{total_docs:,}** | **100%** |")
    lines.append("")

    # Entity counts
    entity_rows = data.get("entity_counts", {}).get("rows", [])
    if entity_rows:
        total_entities = sum(r.get("n", 0) for r in entity_rows)
        lines.append("### Entities by Type")
        lines.append("")
        lines.append("| Entity Type | Count | Share |")
        lines.append("|------------|------:|------:|")
        for row in entity_rows:
            etype = row.get("entity_type", "?")
            n = row.get("n", 0)
            pct = n / max(total_entities, 1) * 100
            lines.append(f"| {etype} | {n:,} | {pct:.1f}% |")
        lines.append(f"| **Total** | **{total_entities:,}** | **100%** |")
        lines.append("")

    # --- Section 2: Topic Trends ---
    lines.append("## 2. Topic Trends")
    lines.append("")

    trends = data.get("topic_trends", {}).get("trends", [])
    if trends:
        by_period: dict[str, list] = defaultdict(list)
        for point in trends:
            by_period[point["period"]].append(
                (point["topic"], point["count"]))

        periods = sorted(by_period.keys())

        topic_trajectory: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for period in periods:
            for topic, count in by_period[period]:
                topic_trajectory[topic].append((period, count))

        # Growth analysis
        growth_scores: dict[str, float] = {}
        for topic, traj in topic_trajectory.items():
            if len(traj) >= 2:
                first_half = sum(c for _, c in traj[:len(traj) // 2])
                second_half = sum(c for _, c in traj[len(traj) // 2:])
                if first_half > 0:
                    growth_scores[topic] = (
                        (second_half - first_half) / first_half * 100
                    )

        if growth_scores:
            rising = sorted(growth_scores.items(), key=lambda x: -x[1])

            lines.append("### Rising Topics")
            lines.append("")
            lines.append("| Topic | Growth | Recent Trajectory |")
            lines.append("|-------|-------:|-------------------|")
            for topic, score in rising[:10]:
                if score > 0:
                    traj = topic_trajectory[topic]
                    spark = " -> ".join(f"{c}" for _, c in traj[-4:])
                    lines.append(
                        f"| {topic} | +{score:.0f}% | {spark} |")
            lines.append("")

            declining = sorted(growth_scores.items(), key=lambda x: x[1])
            lines.append("### Cooling Topics")
            lines.append("")
            lines.append("| Topic | Change | Recent Trajectory |")
            lines.append("|-------|-------:|-------------------|")
            for topic, score in declining[:10]:
                if score < 0:
                    traj = topic_trajectory[topic]
                    spark = " -> ".join(f"{c}" for _, c in traj[-4:])
                    lines.append(
                        f"| {topic} | {score:.0f}% | {spark} |")
            lines.append("")

        # Latest period
        if periods:
            latest = periods[-1]
            lines.append(f"### Top Topics in {latest}")
            lines.append("")
            lines.append("| Rank | Topic | Mentions |")
            lines.append("|-----:|-------|--------:|")
            ranked = sorted(by_period[latest], key=lambda x: -x[1])
            for i, (topic, count) in enumerate(ranked[:15], 1):
                lines.append(f"| {i} | {topic} | {count:,} |")
            lines.append("")
    else:
        lines.append("Topic trend data was not available.")
        lines.append("")

    # --- Section 3: Entity Co-occurrence ---
    lines.append("## 3. Entity Co-occurrence")
    lines.append("")

    pairs = data.get("cooccurrence", {}).get("pairs", [])
    if pairs:
        lines.append("### Top Entity Pairs")
        lines.append("")
        lines.append("| Entity A | Entity B | Co-occurrences |")
        lines.append("|----------|----------|---------------:|")
        for pair in pairs[:25]:
            lines.append(
                f"| {pair['entity_a_name']} | {pair['entity_b_name']} "
                f"| {pair['count']:,} |")
        lines.append("")

        entity_pair_counts: Counter = Counter()
        entity_total: Counter = Counter()
        for pair in pairs:
            entity_pair_counts[pair["entity_a_name"]] += 1
            entity_pair_counts[pair["entity_b_name"]] += 1
            entity_total[pair["entity_a_name"]] += pair["count"]
            entity_total[pair["entity_b_name"]] += pair["count"]

        lines.append("### Most Connected Entities (Hub Nodes)")
        lines.append("")
        lines.append("| Entity | Unique Partners | Total Co-mentions |")
        lines.append("|--------|----------------:|------------------:|")
        for entity, count in entity_pair_counts.most_common(15):
            lines.append(
                f"| {entity} | {count} | {entity_total[entity]:,} |")
        lines.append("")
    else:
        lines.append("Entity co-occurrence data was not available.")
        lines.append("")

    # --- Section 4: Skills by Role ---
    lines.append("## 4. Skills by Role")
    lines.append("")

    roles_data = []
    for role in ["Data Scientist", "ML Engineer", "AI Engineer",
                 "Software Engineer", "Data Engineer"]:
        role_data = data.get(f"skills_{role}", {})
        role_entries = role_data.get("roles", [])
        if role_entries:
            roles_data.append(role_entries[0])

    if roles_data:
        for role_entry in roles_data:
            role_name = role_entry.get("role", "?")
            total_jobs = role_entry.get("total_jobs", 0)
            skills = role_entry.get("skills", [])

            lines.append(f"### {role_name} ({total_jobs:,} jobs)")
            lines.append("")
            lines.append("| Rank | Skill | Job Count | Prevalence |")
            lines.append("|-----:|-------|----------:|-----------:|")
            for i, s in enumerate(skills[:15], 1):
                pct = (s["count"] / max(total_jobs, 1) * 100)
                lines.append(
                    f"| {i} | {s['skill']} | {s['count']:,} "
                    f"| {pct:.0f}% |")
            lines.append("")
    else:
        lines.append("Skills-by-role data was not available.")
        lines.append("")

    # --- Section 5: Sample Jobs ---
    lines.append("## 5. Sample Job Postings")
    lines.append("")

    sample_jobs = data.get("sample_jobs", {})
    hits = sample_jobs.get("hits", [])
    total_jobs = sample_jobs.get("total", 0)

    if hits:
        lines.append(f"Search for 'machine learning engineer' returned "
                     f"**{total_jobs:,} total results**. Top hits:")
        lines.append("")
        lines.append("| Title | Company | Location | Salary Range |")
        lines.append("|-------|---------|----------|-------------|")
        for hit in hits[:10]:
            title = hit.get("title", "?")[:50]
            company = hit.get("company", "?")
            location = hit.get("location", "?")
            sal_min = hit.get("salaryMin")
            sal_max = hit.get("salaryMax")
            sal = "N/A"
            if sal_min and sal_max:
                sal = f"${sal_min:,}-${sal_max:,}"
            elif sal_max:
                sal = f"Up to ${sal_max:,}"
            lines.append(f"| {title} | {company} | {location} | {sal} |")
        lines.append("")
    else:
        lines.append("No sample jobs were returned.")
        lines.append("")

    # --- Summary ---
    lines.append("---")
    lines.append("")
    lines.append("*This report was generated programmatically using only the "
                 "Skillenai Data Products API. No direct database access was "
                 "required. Reproduce and extend this analysis using the same "
                 "public API.*")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate an EDA report from the Skillenai API")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for the markdown report "
             "(default: reports/eda-YYYYMMDD.md)")
    args = parser.parse_args()

    url, key = get_config()
    print(f"Using API: {url}\n")

    data = collect_data(url, key)
    report = generate_report(data)

    output_path = args.output
    if not output_path:
        output_path = (f"reports/eda-report-"
                       f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.md")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"\nReport written to {out}")
    print(f"Report size: {len(report):,} chars, "
          f"{report.count(chr(10)):,} lines")


if __name__ == "__main__":
    main()
