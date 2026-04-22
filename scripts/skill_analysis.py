"""Analyze skill demand by role using the Skillenai API.

Query the skills-by-role endpoint for one or more roles, display top skills,
and optionally compare skill profiles across roles.

Usage:
    python scripts/skill_analysis.py "Data Scientist"
    python scripts/skill_analysis.py "Data Scientist" "ML Engineer" --compare
    python scripts/skill_analysis.py "ML Engineer,Machine Learning Engineer" --top 20
"""

from __future__ import annotations

import argparse
import os
import sys
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


def fetch_skills(url: str, key: str, role: str) -> dict | None:
    """Fetch skills for a role. Returns the first role entry or None."""
    resp = requests.get(
        f"{url}/v1/analytics/skills-by-role",
        headers={"X-API-Key": key},
        params={"role": role},
        timeout=60,
    )
    if not resp.ok:
        print(f"ERROR fetching skills for '{role}': "
              f"HTTP {resp.status_code} {resp.text[:200]}")
        return None

    roles = resp.json().get("roles", [])
    return roles[0] if roles else None


def print_skills(role_data: dict, top_n: int = 15) -> None:
    """Print skill table for a single role."""
    role_name = role_data.get("role", "?")
    total_jobs = role_data.get("total_jobs", 0)
    skills = role_data.get("skills", [])

    print(f"\n{'=' * 60}")
    print(f"  {role_name}  ({total_jobs:,} jobs)")
    print(f"{'=' * 60}")
    print(f"{'Rank':>4}  {'Skill':<30}  {'Count':>7}  {'Prevalence':>10}")
    print(f"{'----':>4}  {'-' * 30}  {'-----':>7}  {'----------':>10}")

    for i, s in enumerate(skills[:top_n], 1):
        pct = s["count"] / max(total_jobs, 1) * 100
        print(f"{i:>4}  {s['skill']:<30}  {s['count']:>7,}  {pct:>9.0f}%")

    print()


def compare_roles(roles_data: list[dict], top_n: int = 15) -> None:
    """Cross-tabulate skills across roles."""
    # Collect all skills
    all_skills: dict[str, dict[str, int]] = {}
    role_names = []
    for rd in roles_data:
        rn = rd.get("role", "?")
        role_names.append(rn)
        for s in rd.get("skills", []):
            skill_name = s["skill"]
            if skill_name not in all_skills:
                all_skills[skill_name] = {}
            all_skills[skill_name][rn] = s["count"]

    # Rank by total mentions
    ranked = sorted(
        all_skills.items(),
        key=lambda x: sum(x[1].values()),
        reverse=True,
    )[:top_n]

    # Print comparison table
    print(f"\n{'=' * 80}")
    print("  Skill Comparison Across Roles")
    print(f"{'=' * 80}")

    # Header
    header = f"{'Skill':<25}"
    for rn in role_names:
        short = rn[:12]
        header += f"  {short:>12}"
    print(header)
    print("-" * len(header))

    for skill, counts in ranked:
        row = f"{skill:<25}"
        for rn in role_names:
            c = counts.get(rn, 0)
            row += f"  {c:>12,}" if c > 0 else f"  {'--':>12}"
        print(row)

    print()

    # Unique skills per role
    print("Unique/distinctive skills per role:")
    for rd in roles_data:
        rn = rd.get("role", "?")
        other_skills = set()
        for other_rd in roles_data:
            if other_rd.get("role") != rn:
                other_skills.update(
                    s["skill"] for s in other_rd.get("skills", []))

        unique = [
            s["skill"] for s in rd.get("skills", [])[:30]
            if s["skill"] not in other_skills
        ]
        if unique:
            print(f"  {rn}: {', '.join(unique[:8])}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze skill demand by role via the Skillenai API")
    parser.add_argument(
        "roles", nargs="+",
        help="Role name(s) to analyze. Use commas within a single arg "
             "to merge aliases (e.g., 'ML Engineer,Machine Learning Engineer')")
    parser.add_argument(
        "--top", type=int, default=15,
        help="Number of top skills to show (default: 15)")
    parser.add_argument(
        "--compare", action="store_true",
        help="Compare skill profiles across roles (requires 2+ roles)")
    args = parser.parse_args()

    url, key = get_config()

    roles_data = []
    for role in args.roles:
        rd = fetch_skills(url, key, role)
        if rd:
            roles_data.append(rd)
            print_skills(rd, top_n=args.top)
        else:
            print(f"\nNo data found for role: {role}")

    if args.compare and len(roles_data) >= 2:
        compare_roles(roles_data, top_n=args.top)
    elif args.compare and len(roles_data) < 2:
        print("Need at least 2 roles with data for comparison.")


if __name__ == "__main__":
    main()
