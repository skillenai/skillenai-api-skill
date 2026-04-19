"""Canonicalize skill surface forms from the Skillenai Job Index.

The entity resolver currently emits duplicate canonical skill names for case,
punctuation, and acronym variants (see SKI-165). This utility detects those
variants in any per-job dataset and builds a merge map.

The duplicates fragment counts in market reports and produce misleading
regressions when two "different" skills are really the same concept
(e.g. `RAG` vs `Retrieval-Augmented Generation (RAG)`).

Usage:
    python canonicalize_skills.py <input.csv> --skill-col skills \\
        [--output-map merge_map.json] [--output-csv merged.csv]

    # Input CSV must have a pipe-separated skill column.
    # If --output-csv is provided, writes a copy of the input with skills remapped.

Strategy:
  1. Normalize: lowercase, collapse whitespace/hyphens/slashes/underscores,
     strip trailing parenthetical suffixes, strip non-word punctuation.
     Preserves "+" and "#" so "C", "C++", "C#" stay distinct.
  2. Group surface forms by normalized key. Canonical = most-frequent surface.
  3. Acronym-expansion: if a canonical ends with "(ACRONYM)" and the ACRONYM
     exists as another standalone canonical, merge into the standalone (shorter
     form — more readable in tables).
  4. Meta-tag stripping: "evaluation (evals)" -> "evaluation" if the stripped
     form exists as a canonical.

Protected skills never merged: single-char language names (C, C++, C#, R, Go, ...).

Blocklist for acronym collisions (extend as needed):
  - React (frontend) vs ReAct (reasoning-and-acting agent pattern)
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict

PROTECTED = {
    "C", "C++", "C#", "R", "Go", "Rust", "Java", "JavaScript", "TypeScript",
    "Swift", "Ruby", "Scala", "Python", "PHP", "Perl", "Lua", "Kotlin",
    "Dart", "Julia", "Haskell", "F#", ".NET",
}

ACRONYM_BLOCKLIST = {
    frozenset({"React", "Reasoning and Acting (ReAct)"}),
    frozenset({"React", "ReAct"}),
}


def normalize(s: str) -> str:
    s = s.strip()
    s_no_paren = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    base = s_no_paren if len(s_no_paren) >= 2 else s
    t = base.lower()
    t = re.sub(r"[-_/]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"[^\w\s+#]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_parens_acronym(s: str):
    m = re.search(r"\(([A-Za-z][A-Za-z0-9]{1,})\)\s*$", s)
    return m.group(1) if m else None


def build_merge_map(counts: Counter) -> dict:
    """Given a Counter of surface_form -> count, return {surface: canonical}.

    Canonical forms map to themselves (implicit — any surface not in the dict
    is its own canonical).
    """
    groups = defaultdict(list)
    for sk, c in counts.items():
        if sk in PROTECTED:
            groups[f"__PROTECTED__{sk}"].append((sk, c))
            continue
        n = normalize(sk)
        if not n:
            continue
        groups[n].append((sk, c))

    merge_map: dict[str, str] = {}
    for members in groups.values():
        if len(members) == 1:
            continue
        members_sorted = sorted(members, key=lambda x: (-x[1], x[0]))
        canonical = members_sorted[0][0]
        for surface, _ in members_sorted[1:]:
            merge_map[surface] = canonical

    # Effective canonical counts after first-pass merges
    canonical_counts: Counter = Counter()
    for sk, c in counts.items():
        canonical_counts[merge_map.get(sk, sk)] += c

    # Acronym-expansion pass: collapse "Foo (BAR)" into standalone "BAR"
    canon_lc = {c.lower(): c for c in canonical_counts}
    for canon in list(canonical_counts.keys()):
        acro = extract_parens_acronym(canon)
        if not acro:
            continue
        standalone = canon_lc.get(acro.lower())
        if (
            standalone
            and standalone != canon
            and standalone not in PROTECTED
            and frozenset({standalone, canon}) not in ACRONYM_BLOCKLIST
        ):
            winner, loser = standalone, canon
            merge_map[loser] = winner
            for s, t in list(merge_map.items()):
                if t == loser:
                    merge_map[s] = winner
            canonical_counts[winner] += canonical_counts.pop(loser)

    # Meta-tag stripping pass
    for surface in list(counts):
        if surface in merge_map or surface in PROTECTED:
            continue
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", surface).strip()
        if stripped and stripped != surface and stripped in canonical_counts:
            merge_map[surface] = merge_map.get(stripped, stripped)

    return merge_map


def apply_merge_map(skills: list[str], merge_map: dict) -> list[str]:
    """Apply merge map to a skill list and dedupe while preserving order."""
    seen = set()
    out = []
    for s in skills:
        canon = merge_map.get(s, s)
        if canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input_csv")
    p.add_argument("--skill-col", default="skills",
                   help="column containing pipe-separated skill list (default: skills)")
    p.add_argument("--output-map", default="skill_merge_map.json")
    p.add_argument("--output-csv", default=None,
                   help="if set, write input CSV with skill column remapped")
    args = p.parse_args()

    counts: Counter = Counter()
    rows = []
    with open(args.input_csv) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)
            for sk in (row.get(args.skill_col) or "").split("|"):
                if sk:
                    counts[sk] += 1

    print(f"Unique skill surface forms: {len(counts)}", file=sys.stderr)
    merge_map = build_merge_map(counts)
    print(f"Merge map size: {len(merge_map)}", file=sys.stderr)

    with open(args.output_map, "w") as f:
        json.dump(merge_map, f, indent=2, sort_keys=True)
    print(f"Wrote {args.output_map}", file=sys.stderr)

    if args.output_csv:
        with open(args.output_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                skills = [s for s in (row.get(args.skill_col) or "").split("|") if s]
                row[args.skill_col] = "|".join(apply_merge_map(skills, merge_map))
                w.writerow(row)
        after = set()
        for row in rows:
            for s in (row.get(args.skill_col) or "").split("|"):
                if s:
                    after.add(s)
        print(f"Unique skills after merge: {len(after)}", file=sys.stderr)
        print(f"Wrote {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
