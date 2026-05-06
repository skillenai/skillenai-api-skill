"""Compute the academic-depth ratio for a list of skills/topics.

For each skill, query the scholarly index and the blog index for
match_phrase counts on extractedText. Outputs scholarly / (scholarly + blog).

High ratio = academic-foundational topic (transformer, fine-tuning, RLHF).
Low ratio = practitioner skill (Kubernetes, Terraform, JAX as a tool).

Rate-limited: ≥9s between phrase queries to avoid 429s on the QUERY tier.
Resumes from existing output file if rerun.

Usage:
  export API_URL=https://api.skillenai.com
  export API_KEY=skn_live_...
  python scholarly_blog_phrase_ratio.py skills.txt -o ratios.csv

  # skills.txt: one skill name per line, blank lines and # comments OK

Or as a library:
  from scholarly_blog_phrase_ratio import phrase_count, compute_ratios
  ratios = compute_ratios(["JAX", "Kubernetes", "fine-tuning"])
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SCHOLARLY_INDEX = "prod-enriched-scholarly"
DEFAULT_BLOG_INDEX = "prod-enriched-blog"
DEFAULT_SLEEP = 9.0
MAX_RETRIES = 6


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def phrase_count(index: str, phrase: str, max_retries: int = MAX_RETRIES) -> int:
    """Return the count of documents containing the phrase via match_phrase.

    Uses track_total_hits=True to bypass the 10K cap. Handles 429s with
    exponential backoff.
    """
    body = {
        "indices": [index],
        "query": {
            "size": 0,
            "track_total_hits": True,
            "query": {"match_phrase": {"extractedText": phrase}},
        },
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{_env('API_URL')}/v1/query/search",
        data=data,
        headers={"X-API-Key": _env("API_KEY"), "Content-Type": "application/json"},
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()).get("total", 0)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 12 * (2 ** attempt)
                print(f"    429 on '{phrase}' in {index}, waiting {wait}s",
                      file=sys.stderr, flush=True)
                time.sleep(wait)
            else:
                raise
    return -1


def compute_ratios(
    skills: list[str],
    scholarly_index: str = DEFAULT_SCHOLARLY_INDEX,
    blog_index: str = DEFAULT_BLOG_INDEX,
    sleep: float = DEFAULT_SLEEP,
    out_path: Path | None = None,
) -> list[dict]:
    """For each skill, return a dict with scholarly_count, blog_count, ratio.

    If out_path is given, writes incrementally every 5 skills and resumes from
    existing rows on rerun.
    """
    fields = ["skill", "scholarly_count", "blog_count", "ratio"]

    done = {}
    if out_path and out_path.exists():
        with open(out_path) as f:
            for r in csv.DictReader(f):
                done[r["skill"]] = r
        print(f"Resuming with {len(done)} skills already done", file=sys.stderr)

    rows = list(done.values())
    todo = [s for s in skills if s not in done]
    print(f"To process: {len(todo)} skills × 2 indices = {2*len(todo)} queries"
          f" → ~{2*len(todo)*sleep/60:.1f} min", file=sys.stderr)

    for i, sk in enumerate(todo, 1):
        try:
            sch = phrase_count(scholarly_index, sk)
            time.sleep(sleep)
            blg = phrase_count(blog_index, sk)
            time.sleep(sleep)
        except Exception as e:
            print(f"  ERR {sk}: {e}", file=sys.stderr)
            sch, blg = -1, -1
        denom = sch + blg
        ratio = sch / denom if denom > 0 else 0.0
        rows.append({
            "skill": sk,
            "scholarly_count": sch,
            "blog_count": blg,
            "ratio": round(ratio, 4),
        })
        print(f"  [{i}/{len(todo)}] {sk:40s} sch={sch:>5d} blog={blg:>6d} ratio={ratio:.3f}",
              file=sys.stderr, flush=True)

        if out_path and (i % 5 == 0 or i == len(todo)):
            with open(out_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)

    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("skills_file", help="Text file: one skill per line (blank/# OK)")
    p.add_argument("-o", "--output", default="scholarly_blog_ratios.csv")
    p.add_argument("--scholarly-index", default=DEFAULT_SCHOLARLY_INDEX)
    p.add_argument("--blog-index", default=DEFAULT_BLOG_INDEX)
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                   help="Seconds between phrase queries (default 9.0)")
    args = p.parse_args()

    skills = []
    for line in Path(args.skills_file).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            skills.append(line)

    if not skills:
        print("No skills found in input file", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)
    rows = compute_ratios(
        skills,
        scholarly_index=args.scholarly_index,
        blog_index=args.blog_index,
        sleep=args.sleep,
        out_path=out_path,
    )

    fields = ["skill", "scholarly_count", "blog_count", "ratio"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
