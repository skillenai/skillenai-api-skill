#!/usr/bin/env python3
"""Employer-concentration gate for role/segment buckets.

Answers the question the Skillenai analysis playbook keeps asking: *is this bucket
actually a market rate, or is it one company's internal ladder?* Publishing a
"Mission Software Engineer pays $222K" leaderboard row is a category error when
85% of those postings come from a single employer.

Two things this does that a naive `value_counts()` does not:

1. **Normalises employer names first.** Canonical company names fragment in the
   index -- the same employer shows up as "Anduril", "Anduril Industries" and
   "andurilindustries", plus unresolved ATS slugs like "ngc" / "bah". Concentration
   measured on raw names is biased DOWNWARD, because one employer's share splits
   across several keys. Measured 2026-09: normalising flipped three role titles
   from "passes a 25% gate" to "fails it" (Technical Program Manager 18% -> 31%,
   Mission Software Engineer 50% -> 86%, Firmware Engineer 29% -> 54%).
2. **Reports the de-concentrated statistic**, so you can see whether the bucket's
   headline number actually depends on the dominant employer.

Usage
-----
  # From a CSV with one row per posting
  python employer_concentration.py postings.csv \
      --group-col role --employer-col companyCanonicalName --value-col salary_mid

  # Tighten or loosen the gate
  python employer_concentration.py postings.csv --group-col role \
      --employer-col company --min-n 60 --min-employers 10 --max-top-share 25

  # Emit only the buckets that pass, as a newline-separated list
  python employer_concentration.py postings.csv --group-col role \
      --employer-col company --print-passing

As a library
------------
  from employer_concentration import normalize_employer, concentration_report
  df["emp"] = df["companyCanonicalName"].map(normalize_employer)
  report = concentration_report(df, group_col="role", employer_col="emp",
                                value_col="salary_mid")

Exit code is 1 if any bucket fails the gate, so this can be used as a guard in a
pipeline.
"""
from __future__ import annotations

import argparse
import re
import sys

import numpy as np
import pandas as pd

# ATS slugs and abbreviations that string normalisation alone cannot resolve.
SLUG_ALIASES = {
    "ngc": "northropgrumman",
    "bah": "boozallenhamilton",
    "lmco": "lockheedmartin",
    "gd": "generaldynamics",
    "jpl": "nasajpl",
}

# Stripped only when something meaningful remains, so "Systems Inc" does not
# collapse to nothing and distinct firms sharing a stem are not over-merged.
CORP_SUFFIXES = [
    "industries", "technologies", "technology", "incorporated", "corporation",
    "company", "holdings", "group", "global", "systems", "labs", "laboratories",
    "solutions", "services", "inc", "llc", "ltd", "corp", "plc", "co",
]

MIN_STEM = 4


def normalize_employer(name: object, aliases: dict | None = None) -> str:
    """Collapse employer-name variants to a single comparison key.

    Case-folds, strips non-alphanumerics, applies the alias map, then removes at
    most one trailing corporate suffix. Returns "(unknown)" for blank input.
    """
    if not isinstance(name, str) or not name.strip():
        return "(unknown)"
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    if not key:
        return "(unknown)"
    table = {**SLUG_ALIASES, **(aliases or {})}
    key = table.get(key, key)
    for suffix in CORP_SUFFIXES:
        if key.endswith(suffix) and len(key) - len(suffix) >= MIN_STEM:
            return key[: -len(suffix)]
    return key


def _hhi(shares: np.ndarray) -> float:
    return float((shares ** 2).sum())


def concentration_report(
    df: pd.DataFrame,
    group_col: str,
    employer_col: str,
    value_col: str | None = None,
    min_n: int = 60,
    min_employers: int = 10,
    max_top_share: float = 25.0,
    normalize: bool = True,
) -> pd.DataFrame:
    """One row per bucket: size, employer diversity, concentration, gate verdict.

    `max_top_share` is a percentage. When `value_col` is given, also reports the
    bucket median and the median with the dominant employer removed, which is the
    number that tells you whether the headline depends on one company.
    """
    for col in (group_col, employer_col):
        if col not in df.columns:
            raise KeyError(f"column {col!r} not in dataframe")
    if value_col is not None and value_col not in df.columns:
        raise KeyError(f"column {value_col!r} not in dataframe")

    work = df.copy()
    emp = f"__emp_{employer_col}"
    work[emp] = (
        work[employer_col].map(normalize_employer) if normalize else work[employer_col]
    )

    rows = []
    for bucket, grp in work.groupby(group_col, dropna=True):
        n = len(grp)
        if n < min_n:
            continue
        counts = grp[emp].value_counts()
        shares = (counts / n).to_numpy()
        top_emp, top_n = counts.index[0], int(counts.iloc[0])
        top_share = top_n / n * 100.0

        # How many employers to reach half the bucket - 1 means total capture.
        to_half = int(np.searchsorted(shares.cumsum(), 0.5) + 1)

        reasons = []
        if len(counts) < min_employers:
            reasons.append(f"only {len(counts)} employers")
        if top_share > max_top_share:
            reasons.append(f"top employer {top_share:.0f}%")

        row = {
            group_col: bucket,
            "n": n,
            "n_employers": int(len(counts)),
            "top_employer": top_emp,
            "top_n": top_n,
            "top_share_pct": round(top_share, 1),
            "hhi": round(_hhi(shares), 4),
            "employers_to_50pct": to_half,
            "verdict": "PASS" if not reasons else "FAIL",
            "reason": "; ".join(reasons),
        }
        if value_col is not None:
            vals = pd.to_numeric(grp[value_col], errors="coerce").dropna()
            rest = pd.to_numeric(
                grp.loc[grp[emp] != top_emp, value_col], errors="coerce"
            ).dropna()
            row["median"] = round(float(vals.median()), 1) if len(vals) else np.nan
            row["median_ex_top"] = (
                round(float(rest.median()), 1) if len(rest) >= 25 else np.nan
            )
            row["n_ex_top"] = int(len(rest))
            if np.isfinite(row["median"]) and np.isfinite(row["median_ex_top"]):
                row["delta_ex_top"] = round(row["median_ex_top"] - row["median"], 1)
            else:
                row["delta_ex_top"] = np.nan
        rows.append(row)

    if not rows:
        return pd.DataFrame(
            columns=[group_col, "n", "n_employers", "top_employer", "verdict"]
        )
    out = pd.DataFrame(rows).sort_values("top_share_pct", ascending=False)
    return out.reset_index(drop=True)


def normalization_audit(df: pd.DataFrame, employer_col: str, top: int = 20) -> pd.DataFrame:
    """Which raw names merged, and how much volume each merge moved.

    Run this before trusting the gate - it is also how you catch over-merging of
    genuinely distinct companies that share a stem.
    """
    work = df[[employer_col]].copy()
    work["key"] = work[employer_col].map(normalize_employer)
    grouped = (
        work.groupby("key")[employer_col]
        .agg(variants=lambda s: sorted(set(s.dropna())), postings="size")
        .reset_index()
    )
    merged = grouped[grouped["variants"].map(len) > 1]
    return merged.sort_values("postings", ascending=False).head(top)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="CSV with one row per posting/document")
    ap.add_argument("--group-col", required=True, help="bucket column, e.g. role")
    ap.add_argument("--employer-col", required=True, help="employer-name column")
    ap.add_argument("--value-col", default=None,
                    help="numeric column to report median / median-without-top-employer")
    ap.add_argument("--min-n", type=int, default=60)
    ap.add_argument("--min-employers", type=int, default=10)
    ap.add_argument("--max-top-share", type=float, default=25.0)
    ap.add_argument("--no-normalize", action="store_true",
                    help="skip employer-name normalisation (NOT recommended - biases low)")
    ap.add_argument("--audit", action="store_true",
                    help="also print which raw employer names merged")
    ap.add_argument("--print-passing", action="store_true",
                    help="print only passing bucket names, one per line")
    ap.add_argument("--out", default=None, help="write the full report to this CSV")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    rep = concentration_report(
        df, args.group_col, args.employer_col, args.value_col,
        min_n=args.min_n, min_employers=args.min_employers,
        max_top_share=args.max_top_share, normalize=not args.no_normalize,
    )
    if rep.empty:
        print(f"no buckets with n >= {args.min_n}", file=sys.stderr)
        return 0

    if args.print_passing:
        for name in rep.loc[rep.verdict == "PASS", args.group_col]:
            print(name)
        return 0

    if args.audit and not args.no_normalize:
        aud = normalization_audit(df, args.employer_col)
        if not aud.empty:
            print("=== employer-name variants merged ===")
            for _, r in aud.iterrows():
                print(f"  {r['postings']:>6,}  {r['key']:<28} <- {', '.join(r['variants'])}")
            print()

    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(rep.to_string(index=False))

    failed = rep[rep.verdict == "FAIL"]
    print(f"\n{len(rep) - len(failed)} pass / {len(failed)} fail "
          f"(gate: n>={args.min_n}, employers>={args.min_employers}, "
          f"top<={args.max_top_share:.0f}%)")
    if not failed.empty:
        print("\nexcluded:")
        for _, r in failed.iterrows():
            print(f"  {r[args.group_col]}: {r['reason']}")
    if args.out:
        rep.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return 1 if not failed.empty else 0


if __name__ == "__main__":
    raise SystemExit(main())
