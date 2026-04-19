---
name: skillenai-api
description: Query the Skillenai Data Products API for labor market intelligence — skills, jobs, trends, and entity analytics
user-invocable: true
argument-hint: [query|jobs|skills|trends|eda|report] <details>
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# Skillenai Data Products API Skill

This skill queries the Skillenai Data Products API for labor market intelligence. It covers skills, jobs, trends, entity analytics, and knowledge graph traversal.

## API Surface

The API has six endpoint groups:

| Group | Key Endpoints | Purpose |
|-------|-----------|---------|
| Service | `GET /v1/health`, `GET /v1/version` | Health checks and metadata |
| Analytics | `GET /v1/analytics/counts`, `topic-trends`, `entity-cooccurrence`, `skills-by-role` | Pre-built aggregate views |
| Jobs | `POST /v1/jobs/search` | Multi-signal job search (hybrid BM25+vector, skill boosts, seniority, salary, geo, recency) |
| Resolution | `POST /v1/resolution/entities` | Resolve free-text names to canonical entity IDs |
| Catalog | `GET /v1/catalog`, `GET /v1/catalog/{projection}` | Schema introspection |
| Query | `POST /v1/query/sql`, `athena`, `graph`, `search` | Direct query against 4 data projections |

## Credentials

Store your API key in a `.env` file at the project root:

```
API_URL=https://api.skillenai.com
API_KEY=skn_live_your_key_here
```

Copy `.env.example` and fill in your key. Get an API key by registering at [app.skillenai.com](https://app.skillenai.com).

Load credentials before making calls:

```bash
source .env
```

## Calling the API

GET endpoints:
```bash
source .env
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/counts" | python3 -m json.tool
```

POST endpoints:
```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT count(*) AS n FROM skillenai.entities"}' | python3 -m json.tool
```

Use `python3 -c "import sys,json; ..."` for inline analysis of JSON responses.

## Topics vs Skills — Critical Distinction

**Topics** are coarse taxonomy tags (~33 categories like `machine-learning`, `nlp`, `agents`) stored in the `topics` array on documents. Use topics for broad filtering and trend analysis.

**Skills** are fine-grained NER-extracted entities (`entity_type: "skill"`) linked to jobs via `REQUIRES` edges. Use the **`/v1/analytics/skills-by-role`** endpoint for skill analysis — NOT raw Cypher with `j.name CONTAINS`.

**When the user asks about skills, technologies, or tools in jobs — use the skills-by-role endpoint (Flow 7), NOT topic aggregation or raw Cypher.**

## `$ARGUMENTS` Parsing

Parse the user's intent from `$ARGUMENTS`:

- `eda` or `report` → Full EDA report flow (Flow 1)
- `query <freeform>` → Ad-hoc API query (Flow 2)
- `sql <query>` → SQL against Postgres (Flow 3)
- `search <terms>` → OpenSearch DSL query (Flow 4)
- `trends` → Topic trend analysis (Flow 5)
- `cooccurrence` → Entity co-occurrence analysis (Flow 5)
- `skills <query>` or any question about job skills/roles → **Flow 7 (skills-by-role)**
- `jobs <query>` → Job search (Flow 8)
- `graph <cypher>` → Raw graph traversal (Flow 6 — only when skills-by-role doesn't cover the need)
- If unclear, ask the user what they want to explore

---

## Flow 1: Full EDA Report (`eda` / `report`)

Run the automated EDA script:

```bash
source .env
python scripts/eda_report.py --output reports/eda-report-$(date +%Y%m%d).md
```

After the script completes, read the report, summarize top 3-5 insights, and offer to dig deeper.

If the script fails, fall back to **manual EDA**:

```bash
source .env

# 1. Document counts
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/counts"

# 2. Topic trends
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/topic-trends?limit=50"

# 3. Entity co-occurrence
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/entity-cooccurrence?limit=50"

# 4. Skills by role
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/skills-by-role"

# 5. Entity counts by type
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC"}'
```

Write the report to `reports/` as markdown.

---

## Flow 2: Ad-Hoc Query (`query`)

| User intent | Endpoint |
|------------|----------|
| "How many documents?" | `GET /v1/analytics/counts` |
| "What topics are trending?" | `GET /v1/analytics/topic-trends` |
| "Which entities appear together?" | `GET /v1/analytics/entity-cooccurrence` |
| "What skills do X jobs require?" | **`GET /v1/analytics/skills-by-role?role=X`** (fuzzy-resolved) |
| "Compare ML Engineer vs Data Scientist" | Two calls with different `role` params, cross-tabulate in Python |
| "Merge aliases into one profile" | **`GET /v1/analytics/skills-by-role?role=ML+Engineer,Machine+Learning+Engineer`** |
| "Find jobs matching X" | **`POST /v1/jobs/search`** |
| "What entity ID is Python?" | **`POST /v1/resolution/entities`** |
| "Find content about X" | `POST /v1/query/search` with match query |
| "Show me company Y" | `POST /v1/query/sql` against `skillenai.entities` |
| "What's the schema?" | `GET /v1/catalog` |

**For any question about specific skills/technologies in jobs, use `skills-by-role` first.** Only fall back to raw Cypher if the dedicated endpoint doesn't cover the query.

---

## Flow 3: SQL Queries (`sql`)

```bash
source .env

# Count entities by type
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC"}'

# Find entities by name
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT entity_id, canonical_name, entity_type FROM skillenai.entities WHERE canonical_name ILIKE $1 LIMIT 20", "params": ["%OpenAI%"]}'
```

**Postgres tables:** `skillenai.entities`, `skillenai.documents`, `skillenai.document_entity_links`, `skillenai.relationships`

Use `GET /v1/catalog/postgres` to see all columns.

---

## Flow 4: OpenSearch Queries (`search`)

```bash
source .env

# Full-text search
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/search" \
  -d '{"query": {"query": {"match": {"title": "machine learning"}}, "size": 20}}'

# Aggregation: top skills in job postings (via nested entity agg)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/search" \
  -d '{"query": {"size": 0, "query": {"term": {"sourceType": "jobs"}}, "aggs": {"skill_entities": {"nested": {"path": "entities"}, "aggs": {"skills_only": {"filter": {"term": {"entities.resolved.entityType": "skill"}}, "aggs": {"top_skills": {"terms": {"field": "entities.resolved.canonicalName.keyword", "size": 50}}}}}}}}, "indices": ["prod-enriched-jobs"]}'
```

---

## Flow 5: Analytics (`trends` / `cooccurrence`)

```bash
source .env

# Document counts by source
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/counts"

# Topic trends over time
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/topic-trends?limit=50"

# Entity co-occurrence
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/entity-cooccurrence?limit=50"
```

---

## Flow 6: Graph Queries (`graph`)

Use raw Cypher only when the dedicated endpoints (skills-by-role, jobs/search, resolution) don't cover the query. Common use cases:

```bash
source .env

# Companies posting the most jobs for a skill
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (j:job)-[:REQUIRES]->(s:skill), (j)-[:POSTED_BY]->(c:company) WHERE s.name = '\''Python'\'' RETURN c.name AS company, count(*) AS jobs ORDER BY jobs DESC", "limit": 20}'

# Skills co-occurring with a specific skill
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (j:job)-[:REQUIRES]->(s1:skill), (j)-[:REQUIRES]->(s2:skill) WHERE s1.name = '\''Python'\'' AND s1.id <> s2.id RETURN s2.name AS co_skill, count(*) AS overlap ORDER BY overlap DESC", "limit": 20}'

# Entity mentions in scholarly papers
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (d:document)-[:MENTIONS]->(e:skill) WHERE d.source_type = '\''scholarly'\'' RETURN e.name AS skill, count(*) AS mentions ORDER BY mentions DESC", "limit": 30}'
```

**Graph query rules:**
- Read-only Cypher only (MATCH/RETURN)
- Node labels: `job`, `document`, `skill`, `company`, `product`, `person`, `location`
- Edge labels: `REQUIRES` (job->skill), `MENTIONS` (document->entity), `POSTED_BY` (job->company), `AUTHORED` (person->document)
- Job nodes have a `roles` property (pipe-delimited string, e.g. `"Data Scientist|ML Engineer"`). Use `j.roles CONTAINS 'Role Name'` in Cypher WHERE clauses.
- Max 1,000 rows, 30s timeout

---

## Flow 7: Skill Analysis by Role (`skills`)

**This is the preferred way to analyze job skills.** The `skills-by-role` endpoint resolves role names via entity resolution (exact match then fuzzy fallback), so callers don't need exact canonical role labels. Accepts a single role or comma-separated aliases to merge into one aggregated skill profile.

```bash
source .env

# Single role — resolved via entity resolution (fuzzy OK)
curl -s -H "X-API-Key: $API_KEY" \
  "$API_URL/v1/analytics/skills-by-role?role=Data+Scientist"

# Comma-separated aliases — merged into a single skill profile
curl -s -H "X-API-Key: $API_KEY" \
  "$API_URL/v1/analytics/skills-by-role?role=ML+Engineer,Machine+Learning+Engineer"
```

**Response format:**
```json
{
  "roles": [
    {
      "role": "Data Scientist",
      "total_jobs": 1124,
      "skills": [
        {"skill": "Python", "count": 880},
        {"skill": "SQL", "count": 704}
      ]
    }
  ]
}
```

For **comparative role analysis** (e.g., DS vs MLE vs AIE skills), fetch all roles in one call, then use Python to cross-tabulate. Or use `scripts/skill_analysis.py`.

---

## Flow 8: Job Search (`jobs`)

Search job postings with multi-signal ranking:

```bash
source .env

# Basic search
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/jobs/search" \
  -d '{"query": "machine learning engineer NLP deep learning", "size": 20}'

# With all ranking signals
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/jobs/search" \
  -d '{
    "query": "AI engineer NLP LLMs RAG Python",
    "seniority": "senior",
    "min_salary": 180000,
    "filters": {"workModel": "remote"},
    "recency_decay": "30d",
    "size": 20
  }'

# With skill entity boosts (resolve names first via /v1/resolution/entities)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/jobs/search" \
  -d '{
    "query": "AI engineer",
    "skill_boosts": [
      {"entity_id": "175c2b707caa6eb1", "weight": 10.0},
      {"entity_id": "f0109bbf0cbac010", "weight": 7.0}
    ],
    "seniority": "senior",
    "size": 20
  }'

# With geo-distance (lat, lon + radius)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/jobs/search" \
  -d '{
    "query": "software engineer",
    "location": [37.77, -122.42],
    "location_radius": "100km",
    "size": 20
  }'
```

**Ranking signals** (all optional, applied symmetrically to both BM25 and k-NN legs via RRF):

| Signal | Parameter | Description |
|--------|-----------|-------------|
| Text + vector | `query` | BM25 keyword match + k-NN embedding similarity, fused via RRF |
| Skill boosts | `skill_boosts` | Nested entity ID matching with per-skill weights |
| Seniority | `seniority` | Dual-track ordinal scoring (IC + mgmt), +/-2 filtered |
| Salary | `min_salary` | Boost jobs with `salaryMax` >= threshold |
| Recency | `recency_decay` | Exponential decay on `postedAt` (default 30d) |
| Location | `location` + `location_radius` | geo_distance filter + gauss proximity decay |
| Filters | `filters` | Hard term filters (workModel, company, etc.) |

---

## Flow 9: Entity Resolution

Resolve free-text names to canonical entity IDs:

```bash
source .env

curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/resolution/entities" \
  -d '{
    "names": [
      {"name": "Python", "entity_type": "skill"},
      {"name": "Google", "entity_type": "company"},
      {"name": "TensorFlow"}
    ],
    "mode": "auto",
    "limit": 3
  }'
```

**Modes:** `auto` (exact match first, fuzzy FTS fallback), `exact` (exact only), `fts` (full-text search only).

Use this to resolve skill names to entity IDs for `skill_boosts` in `/v1/jobs/search`.

---

## API Reference Quick Summary

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/health` | GET | Service health |
| `/v1/version` | GET | Service metadata |
| `/v1/analytics/counts` | GET | Document counts by source |
| `/v1/analytics/entity-cooccurrence` | GET | Top entity pairs |
| `/v1/analytics/topic-trends` | GET | Monthly topic trends |
| `/v1/analytics/skills-by-role` | GET | **Skill distributions by role (entity-resolved, comma-separated aliases)** |
| `/v1/jobs/search` | POST | **Multi-signal job search (RRF + boosts + seniority + geo + recency)** |
| `/v1/resolution/entities` | POST | **Resolve names to entity IDs** |
| `/v1/catalog` | GET | List all projection schemas |
| `/v1/catalog/{projection}` | GET | Schema for one projection |
| `/v1/query/sql` | POST | SQL against Postgres |
| `/v1/query/athena` | POST | SQL against Athena/S3 |
| `/v1/query/graph` | POST | Cypher against knowledge graph |
| `/v1/query/search` | POST | OpenSearch Query DSL |

### Entity types: `company`, `product`, `person`, `skill`, `location`
### Source types: `blog`, `news`, `scholarly`, `social`, `jobs`, `product`, `company`, `person`

---

## Helper Scripts

The `scripts/` directory contains Python helpers that load credentials from `.env` and call the API. All require `requests` and `python-dotenv` (`pip install requests python-dotenv`).

| Script | Purpose |
|--------|---------|
| `scripts/eda_report.py` | Generate a comprehensive EDA markdown report |
| `scripts/skill_analysis.py` | Analyze skill demand by role, compare roles |
| `scripts/trend_analysis.py` | Topic trend time series, growth analysis |
| `scripts/job_search.py` | Multi-signal job search with formatted output |
| `scripts/download_jobs_paginated.py` | Paginated per-job download with arbitrary filter segments; handles 429 backoff |
| `scripts/canonicalize_skills.py` | Collapse duplicate skill surface forms (case/punct/acronym variants) before aggregating — see SKI-165 |

Run any script with `--help` for usage details.

**Before aggregating skills by anything** (role, geo, seniority, time), pass per-job data through `canonicalize_skills.py` first. The entity resolver emits duplicate canonical names for skills like `RAG` vs `Retrieval-Augmented Generation (RAG)`, and aggregations will undercount or give contradictory signals otherwise. See SKI-165.

---

## Analysis Patterns

**Skill demand by role:** `GET /v1/analytics/skills-by-role?role=X` — resolves role names via entity resolution (fuzzy OK). Use comma-separated aliases to merge (e.g. `?role=ML+Engineer,Machine+Learning+Engineer`).

**Dataset composition:** `GET /v1/analytics/counts` for document volumes, `POST /v1/query/sql` for entity counts by type.

**Entity resolution:** `POST /v1/resolution/entities` to map names to IDs, then use IDs in graph queries or job search `skill_boosts`.

**Job search with profile:** Resolve skills to entity IDs, then `POST /v1/jobs/search` with `skill_boosts`, `seniority`, `min_salary`, `filters`, and `recency_decay`.

**Research-industry gap:** Compare topic distributions from scholarly vs jobs via SQL or Athena.

**Entity ecosystem mapping:** SQL to find entity by name, graph for relationships, SQL for linked documents.

---

## Important Notes

1. **Rate limits:** 120 req/min for READ tier, 60/min for SEARCH, 20/min for ANALYTICS, 10/min for QUERY
2. **Credits:** API calls consume credits. Check `X-Credits-Remaining` response header.
3. **Analytics latency:** Athena-backed endpoints may take several seconds
4. **Always source credentials** before making API calls
5. **Use python3 for aggregation** — pipe curl output to python3 for counting, sorting, and cross-tabulating
6. **Schema discovery:** Check `GET /v1/catalog` before writing queries to verify table/column names
7. **Prefer dedicated endpoints** over raw queries: `skills-by-role` over Cypher, `jobs/search` over DSL, `resolution/entities` over SQL lookups
