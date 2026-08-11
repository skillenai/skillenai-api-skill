#!/usr/bin/env python3
"""
Client for the Skillenai talent-graph API — the supply-side (people/careers)
complement to the demand-side job-postings endpoints.

The talent graph exposes entity-resolved, small-cell-suppressed rollups at
``/v1/talent-graph/*``. Discover them with:

    talent_graph.py endpoints

Key endpoints (all filter-scoped; required filter in parentheses):
    role-transitions-in  (dst_role_id)   feeders INTO a role   -> src_role_id, move_year, n_moves
    role-transitions-out (src_role_id)   exits FROM a role      -> dst_role_id, move_year, n_moves
    skill-prevalence     (role_id)       supply-side skills     -> skill_id, prevalence, role_people_observed
    skill-roles          (skill_id)      roles requiring a skill
    net-flow-by-role     (company_id)    company x role arrivals/departures/net_flow by flow_year
    net-flow-by-company  (role_id)       same, other axis
    company-transitions-in/out, company-signals, company-prestige, school-*, education-facets

The endpoints take/return entity IDs, not names. Forward name->ID resolution uses
``/v1/resolution/entities`` (this client does it for you). Reverse ID->name (naming
the src/dst roles a transition returns) uses the SQL passthrough on
``skillenai.entities`` (chunked); pass exact names, since only exact matches are reliable there.

GOTCHA — roles are fragmented. Seniority and synonym variants are DISTINCT role
entities ("AI Engineer", "Senior AI Engineer", "AI/ML Engineer", "ML Engineer"
vs "Machine Learning Engineer", ...). Discover the family with ``family`` (fts mode
of the resolution endpoint), curate it, then pass the whole family of names to
``transitions`` / ``skill-prevalence`` — it aggregates them and drops moves *within*
the family (a company change inside the family is not a feeder/exit).

Credentials: API_KEY from env, ~/.skillenai/.env, $CLAUDE_PLUGIN_ROOT/.env, or ./.env.
Optional API_URL (default https://api.skillenai.com).

Usage:
    talent_graph.py endpoints
    talent_graph.py resolve --type role --names "AI Engineer,Data Scientist"
    talent_graph.py family  --type role --name "AI Engineer" --limit 20   # fts-expand a fragmented role family
    talent_graph.py transitions --direction in  --roles "AI Engineer,Applied AI Engineer,Generative AI Engineer" --top 12
    talent_graph.py transitions --direction out --roles "AI Engineer" --top 12
    talent_graph.py skill-prevalence --role "Data Scientist" --top 15
    talent_graph.py get role-transitions-in --dst_role_id <id> --limit 1000   # raw passthrough
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv


def get_config() -> tuple[str, str]:
    """Load API_URL and API_KEY. Precedence: env > ~/.skillenai/.env > $CLAUDE_PLUGIN_ROOT/.env > cwd .env."""
    load_dotenv(Path.home() / ".skillenai" / ".env")
    plugin_root = os.getenv("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        load_dotenv(Path(plugin_root) / ".env")
    load_dotenv()
    key = os.getenv("API_KEY") or os.getenv("SKILLENAI_INSIGHTS_API_KEY", "")
    if not key:
        sys.exit("ERROR: API_KEY not set. Put it in ~/.skillenai/.env or export API_KEY.")
    url = os.getenv("API_URL", "https://api.skillenai.com").rstrip("/")
    return url, key


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class TalentGraph:
    def __init__(self):
        self.url, self.key = get_config()
        self.h = {"X-API-Key": self.key, "Content-Type": "application/json"}

    def _get(self, path, params=None, retries=5):
        for attempt in range(retries):
            r = requests.get(f"{self.url}{path}", headers=self.h, params=params or {}, timeout=120)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code >= 400:
                raise RuntimeError(f"GET {path} {params}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"GET {path}: exhausted retries")

    def _sql(self, sql, limit=500):
        r = requests.post(f"{self.url}/v1/query/sql", headers=self.h,
                          data=json.dumps({"sql": sql, "limit": limit}), timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"SQL: {r.status_code} {r.text[:300]}")
        return r.json().get("rows", [])

    # ---- name -> id resolution via the resolution endpoint ----
    def resolve(self, names, entity_type, mode="auto"):
        """{input_name: entity_id} — top candidate per name, via /v1/resolution/entities."""
        r = requests.post(f"{self.url}/v1/resolution/entities", headers=self.h,
                          data=json.dumps({"names": [{"name": n, "entity_type": entity_type} for n in names],
                                           "mode": mode, "limit": 1}), timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f"resolve: {r.status_code} {r.text[:300]}")
        out = {}
        for res in r.json().get("results", []):
            cands = res.get("candidates") or []
            if cands:
                out[res["name"]] = cands[0]["entity_id"]
        return out

    def resolve_family(self, name, entity_type, limit=25):
        """fts-expand a name into its family of related entities (for role families that are
        fragmented into seniority/synonym variants). Returns [(entity_id, canonical_name, score)]."""
        r = requests.post(f"{self.url}/v1/resolution/entities", headers=self.h,
                          data=json.dumps({"names": [{"name": name, "entity_type": entity_type}],
                                           "mode": "fts", "limit": limit}), timeout=90)
        r.raise_for_status()
        res = r.json().get("results", [])
        cands = res[0].get("candidates", []) if res else []
        return [(c["entity_id"], c["canonical_name"], c.get("match_score")) for c in cands]

    def names_for(self, ids):
        """{entity_id: (canonical_name, entity_type)} for a set of ids."""
        out = {}
        ids = list(ids)
        for chunk in _chunks(ids, 60):
            inlist = ",".join("'%s'" % i for i in chunk)
            rows = self._sql(
                f"SELECT entity_id, canonical_name, entity_type FROM skillenai.entities "
                f"WHERE entity_id IN ({inlist})", limit=len(chunk))
            for row in rows:
                out[row["entity_id"]] = (row["canonical_name"], row["entity_type"])
        return out

    def _rows(self, endpoint, **params):
        return self._get(f"/v1/talent-graph/{endpoint}", params).get("rows", [])

    # ---- family-aggregated transitions (the reusable win) ----
    def transitions(self, role_ids, direction="in", top=15):
        """Aggregate feeders (in) or exits (out) across a family of role ids.
        Excludes moves within the family (self-loops). Returns [(name, n_moves, pct)]."""
        family = set(role_ids)
        other_key = "src_role_id" if direction == "in" else "dst_role_id"
        endpoint = "role-transitions-in" if direction == "in" else "role-transitions-out"
        me_filter = "dst_role_id" if direction == "in" else "src_role_id"
        agg = Counter()
        for rid in family:
            for row in self._rows(endpoint, **{me_filter: rid, "limit": 1000}):
                other = row[other_key]
                if other not in family:
                    agg[other] += row.get("n_moves", 0)
        names = self.names_for(agg)
        merged = Counter()
        for i, v in agg.items():
            nm = names.get(i, (i[:8], "?"))[0]
            nm = {"Machine Learning Engineer": "ML Engineer"}.get(nm, nm)
            merged[nm] += v
        tot = sum(merged.values()) or 1
        return [(nm, v, round(100 * v / tot, 1)) for nm, v in merged.most_common(top)], tot

    def skill_prevalence(self, role_id, top=15):
        rows = [r for r in self._rows("skill-prevalence", role_id=role_id, limit=400)
                if r.get("prevalence") is not None]
        ids = [r["skill_id"] for r in rows]
        names = self.names_for(ids)
        out = [(names.get(r["skill_id"], (r["skill_id"][:8], "?"))[0], r["prevalence"],
                r.get("role_people_observed")) for r in rows]
        out.sort(key=lambda x: -x[1])
        return out[:top]


def _role_ids_from_args(tg, roles_arg):
    """Accept comma-separated role NAMES or raw ids; return the resolved id list."""
    items = [s.strip() for s in roles_arg.split(",") if s.strip()]
    names = [s for s in items if " " in s or not all(c in "0123456789abcdef" for c in s.lower())]
    ids = [s for s in items if s not in names]
    if names:
        ids += list(tg.resolve(names, "role").values())
    return ids


def main():
    ap = argparse.ArgumentParser(description="Skillenai talent-graph API client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("endpoints")
    r = sub.add_parser("resolve"); r.add_argument("--type", required=True, choices=["role", "skill", "company"]); r.add_argument("--names", required=True)
    fa = sub.add_parser("family"); fa.add_argument("--type", required=True, choices=["role", "skill", "company"]); fa.add_argument("--name", required=True); fa.add_argument("--limit", type=int, default=25)
    t = sub.add_parser("transitions"); t.add_argument("--direction", choices=["in", "out"], default="in"); t.add_argument("--roles", required=True); t.add_argument("--top", type=int, default=15)
    s = sub.add_parser("skill-prevalence"); s.add_argument("--role", required=True); s.add_argument("--top", type=int, default=15)
    g = sub.add_parser("get"); g.add_argument("endpoint"); g.add_argument("--limit", type=int, default=1000); g.add_argument("filters", nargs="*", help="--filter value pairs, e.g. --dst_role_id <id>")
    args, extra = ap.parse_known_args()
    tg = TalentGraph()

    if args.cmd == "endpoints":
        d = tg._get("/v1/talent-graph/endpoints")
        for e in d.get("endpoints", []):
            print(f"{e['endpoint']:24} required={e.get('required_filters', [])}  filters={e.get('filters', [])}")
    elif args.cmd == "resolve":
        for nm, i in tg.resolve([x.strip() for x in args.names.split(",")], args.type).items():
            print(f"{i}\t{nm}")
    elif args.cmd == "family":
        for eid, nm, score in tg.resolve_family(args.name, args.type, args.limit):
            print(f"{eid}\t{score:>7.2f}\t{nm}")
    elif args.cmd == "transitions":
        ids = _role_ids_from_args(tg, args.roles)
        rows, tot = tg.transitions(ids, args.direction, args.top)
        print(f"{'feeders into' if args.direction=='in' else 'exits from'} role family (total {tot} moves, family={len(ids)} ids):")
        for nm, v, pct in rows:
            print(f"  {nm:30} {v:5d}  {pct:4.1f}%")
    elif args.cmd == "skill-prevalence":
        ids = _role_ids_from_args(tg, args.role)
        if not ids:
            sys.exit("could not resolve role")
        for nm, prev, n in tg.skill_prevalence(ids[0], args.top):
            print(f"  {nm:28} {100*prev:5.1f}%  (n_observed={n})")
    elif args.cmd == "get":
        params = {"limit": args.limit}
        it = iter(extra)
        for tok in it:
            if tok.startswith("--"):
                params[tok[2:]] = next(it, "")
        print(json.dumps(tg._get(f"/v1/talent-graph/{args.endpoint}", params), indent=2)[:4000])


if __name__ == "__main__":
    main()
