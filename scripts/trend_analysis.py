"""Analyze topic trends from the Skillenai API.

Query the topic-trends endpoint, display time series data, and identify
rising and declining topics.

Usage:
    python scripts/trend_analysis.py
    python scripts/trend_analysis.py --topic machine-learning --topic agents
    python scripts/trend_analysis.py --limit 100 --top 10
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
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


def fetch_trends(url: str, key: str, limit: int = 50) -> list[dict]:
    """Fetch topic trend data points."""
    resp = requests.get(
        f"{url}/v1/analytics/topic-trends",
        headers={"X-API-Key": key},
        params={"limit": str(limit)},
        timeout=60,
    )
    if not resp.ok:
        print(f"ERROR: HTTP {resp.status_code} {resp.text[:200]}")
        sys.exit(1)

    return resp.json().get("trends", [])


def analyze_trends(trends: list[dict],
                   filter_topics: list[str] | None = None,
                   top_n: int = 10) -> None:
    """Analyze and display topic trends."""
    # Group by topic
    by_topic: dict[str, list[tuple[str, int]]] = defaultdict(list)
    by_period: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for point in trends:
        topic = point["topic"]
        period = point["period"]
        count = point["count"]
        by_topic[topic].append((period, count))
        by_period[period].append((topic, count))

    # Sort trajectories by period
    for topic in by_topic:
        by_topic[topic].sort(key=lambda x: x[0])

    # Filter topics if requested
    if filter_topics:
        filtered = {}
        for ft in filter_topics:
            ft_lower = ft.lower()
            for topic in by_topic:
                if ft_lower in topic.lower():
                    filtered[topic] = by_topic[topic]
        if not filtered:
            print(f"No topics matching: {', '.join(filter_topics)}")
            print(f"Available topics: {', '.join(sorted(by_topic.keys()))}")
            return
        by_topic_display = filtered
    else:
        by_topic_display = by_topic

    # Latest period rankings
    periods = sorted(by_period.keys())
    if periods:
        latest = periods[-1]
        print(f"\n{'=' * 60}")
        print(f"  Top Topics in {latest} (most recent period)")
        print(f"{'=' * 60}")
        print(f"{'Rank':>4}  {'Topic':<30}  {'Mentions':>10}")
        print(f"{'----':>4}  {'-' * 30}  {'--------':>10}")
        ranked = sorted(by_period[latest], key=lambda x: -x[1])
        for i, (topic, count) in enumerate(ranked[:top_n], 1):
            print(f"{i:>4}  {topic:<30}  {count:>10,}")
        print()

    # Growth analysis
    growth_scores: dict[str, float] = {}
    for topic, traj in by_topic_display.items():
        if len(traj) >= 2:
            first_half = sum(c for _, c in traj[:len(traj) // 2])
            second_half = sum(c for _, c in traj[len(traj) // 2:])
            if first_half > 0:
                growth_scores[topic] = (
                    (second_half - first_half) / first_half * 100
                )
            elif second_half > 0:
                growth_scores[topic] = 100.0

    if growth_scores:
        rising = [(t, g) for t, g in sorted(
            growth_scores.items(), key=lambda x: -x[1]) if g > 0]
        declining = [(t, g) for t, g in sorted(
            growth_scores.items(), key=lambda x: x[1]) if g < 0]

        if rising:
            print(f"{'=' * 60}")
            print("  Rising Topics (strongest growth)")
            print(f"{'=' * 60}")
            print(f"{'Topic':<30}  {'Growth':>8}  {'Trajectory'}")
            print(f"{'-' * 30}  {'------':>8}  {'-' * 25}")
            for topic, score in rising[:top_n]:
                traj = by_topic[topic]
                spark = " -> ".join(f"{c}" for _, c in traj[-4:])
                print(f"{topic:<30}  +{score:>6.0f}%  {spark}")
            print()

        if declining:
            print(f"{'=' * 60}")
            print("  Cooling Topics (declining)")
            print(f"{'=' * 60}")
            print(f"{'Topic':<30}  {'Change':>8}  {'Trajectory'}")
            print(f"{'-' * 30}  {'------':>8}  {'-' * 25}")
            for topic, score in declining[:top_n]:
                traj = by_topic[topic]
                spark = " -> ".join(f"{c}" for _, c in traj[-4:])
                print(f"{topic:<30}  {score:>7.0f}%  {spark}")
            print()

    # Time series for filtered topics
    if filter_topics and by_topic_display:
        print(f"{'=' * 60}")
        print("  Time Series (filtered topics)")
        print(f"{'=' * 60}")
        for topic, traj in sorted(by_topic_display.items()):
            print(f"\n  {topic}:")
            for period, count in traj:
                bar = "#" * min(count // 10, 50)
                print(f"    {period}  {count:>6,}  {bar}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze topic trends from the Skillenai API")
    parser.add_argument(
        "--topic", action="append", default=None,
        help="Filter to specific topic(s) — can be repeated. "
             "Matches substring (e.g., --topic ml matches 'machine-learning')")
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Number of trend data points to fetch (default: 50)")
    parser.add_argument(
        "--top", type=int, default=10,
        help="Number of top items to display (default: 10)")
    args = parser.parse_args()

    url, key = get_config()
    print(f"Fetching topic trends from {url}...")

    trends = fetch_trends(url, key, limit=args.limit)
    if not trends:
        print("No trend data returned.")
        sys.exit(0)

    analyze_trends(trends, filter_topics=args.topic, top_n=args.top)


if __name__ == "__main__":
    main()
