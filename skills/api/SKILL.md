---
name: api
description: Query the Skillenai Data Products API for labor market intelligence — skills, jobs, trends, and entity analytics
user-invocable: true
argument-hint: [setup|query|jobs|skills|trends|eda|report] <details>
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# Skillenai Data Products API Skill

Invoke as `/skillenai:api` (when installed via `/plugin install skillenai`).

This skill queries the Skillenai Data Products API for labor market intelligence. It covers skills, jobs, trends, entity analytics, and knowledge graph traversal.

## API Surface

Two hosts, one API key:

- **`https://api.skillenai.com`** — read-only data products (search, analytics, SQL, graph, resolution).
- **`https://app.skillenai.com`** — user-facing account surface. Alerts CRUD lives here; the scheduled queries it runs hit `api.skillenai.com` with the same key.

The data products API has six endpoint groups:

| Group | Key Endpoints | Purpose |
|-------|-----------|---------|
| Service | `GET /v1/health`, `GET /v1/version` | Health checks and metadata |
| Analytics | `GET /v1/analytics/counts`, `topic-trends`, `entity-cooccurrence`, `skills-by-role` | Pre-built aggregate views |
| Jobs | `POST /v1/jobs/search` | Multi-signal job search (hybrid BM25+vector, skill boosts, seniority, salary, geo, recency) |
| Resolution | `POST /v1/resolution/entities` | Resolve free-text names to canonical entity IDs |
| Catalog | `GET /v1/catalog`, `GET /v1/catalog/{projection}` | Schema introspection |
| Query | `POST /v1/query/sql`, `athena`, `graph`, `search` | Direct query against 4 data projections |

`app.skillenai.com` adds one more group (see Flow 10):

| Group | Key Endpoints | Host | Purpose |
|-------|-----------|------|---------|
| Alerts | `POST /alerts/preview`, `POST /alerts`, `GET /alerts`, `POST /alerts/{id}/run`, `PATCH /alerts/{id}`, `DELETE /alerts/{id}` | `app.skillenai.com` | Create and manage scheduled email alerts that run any data products query on a cadence |

## Credentials

The skill calls the API through a wrapper script (`scripts/api.py`) that loads credentials in its own process. **The API key is never visible to the agent's shell, never appears in `curl` argv, and never enters the conversation transcript.** This is intentional and load-bearing — see the Security section at the end.

### First-run check

Before running any flow other than `setup`, verify credentials exist:

```bash
[ -n "$API_KEY" ] || [ -f ~/.skillenai/.env ]
```

If neither is set, **stop and tell the user**:

> No Skillenai API key found. Run `/skillenai:api setup` to authorize — it'll open a browser, you sign in or create an account, and the key gets saved automatically. Takes about 30 seconds.

Do not attempt the requested query. Do not offer to write the key for the user. The setup flow is the only documented automated path.

### Setup flow

When `$ARGUMENTS` starts with `setup` (or is just `setup`):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/oauth_setup.py"
```

This opens a browser to `app.skillenai.com/activate`, the user clicks Allow, and the script writes the issued key to `~/.skillenai/.env` with mode 0600. On exit 0, suggest a next step (e.g. "Try `/skillenai:api jobs <query>` to test it."). On exit 1, surface the script's stderr — it is already sanitized and will not contain the key.

### If the user pastes a key in chat

If the user's message contains a string matching `skn_live_[A-Za-z0-9_-]{10,}`, do **not** save it via any agent-mediated flow. Respond:

> Looks like you pasted an API key in chat. For your security, run `/skillenai:api setup` instead — that authorizes via browser without putting the key in the conversation transcript. If you specifically want to use the key you just pasted, save it to `~/.skillenai/.env` yourself in a terminal (one line: `API_KEY=skn_live_…`, then `chmod 600`) — that's a manual one-time step that keeps the key out of chat logs.

## Calling the API

Every API call goes through the wrapper. The wrapper resolves the API key, picks the right host, and signs the request:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/api.py" GET /v1/analytics/counts | python3 -m json.tool
```

POST with a JSON body (pass it as the third argument):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/api.py" POST /v1/query/sql \
  '{"sql": "SELECT count(*) AS n FROM skillenai.entities"}' | python3 -m json.tool
```

Calls to the alerts host (`app.skillenai.com`) take `--host app`:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/api.py" GET /alerts --host app | python3 -m json.tool
```

Use `python3 -c "import sys,json; …"` for inline analysis of JSON responses.

## Topics vs Skills — Critical Distinction

**Topics** are coarse taxonomy tags (~33 categories like `machine-learning`, `nlp`, `agents`) stored in the `topics` array on documents. Use topics for broad filtering and trend analysis.

**Skills** are fine-grained NER-extracted entities (`entity_type: "skill"`) linked to jobs via `REQUIRES` edges. Use the **`/v1/analytics/skills-by-role`** endpoint for skill analysis — NOT raw Cypher with `j.name CONTAINS`.

**When the user asks about skills, technologies, or tools in jobs — use the skills-by-role endpoint (Flow 7), NOT topic aggregation or raw Cypher.**

## `$ARGUMENTS` Parsing

Parse the user's intent from `$ARGUMENTS`:

- `setup` → Run the OAuth setup flow described above (`scripts/oauth_setup.py`)
- `eda` or `report` → Full EDA report flow (Flow 1)
- `query <freeform>` → Ad-hoc API query (Flow 2)
- `sql <query>` → SQL against Postgres (Flow 3)
- `search <terms>` → OpenSearch DSL query (Flow 4)
- `trends` → Topic trend analysis (Flow 5)
- `cooccurrence` → Entity co-occurrence analysis (Flow 5)
- `skills <query>` or any question about job skills/roles → **Flow 7 (skills-by-role)**
- `jobs <query>` → Job search (Flow 8)
- `graph <cypher>` → Raw graph traversal (Flow 6 — only when skills-by-role doesn't cover the need)
- `alerts <anything>`, `subscribe <query>`, or "email me when X" → Alerts authoring (Flow 10)
- If unclear, ask the user what they want to explore

For every case other than `setup`, run the first-run check first (see above).

---

## Flow 1: Full EDA Report (`eda` / `report`)

Run the automated EDA script:

```bash
python "${CLAUDE_PLUGIN_ROOT:-.}/scripts/eda_report.py" --output "reports/eda-report-$(date +%Y%m%d).md"
```

After the script completes, read the report, summarize top 3-5 insights, and offer to dig deeper.

If the script fails, fall back to **manual EDA**:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# 1. Document counts
python "$WRAP" GET /v1/analytics/counts

# 2. Topic trends
python "$WRAP" GET "/v1/analytics/topic-trends?limit=50"

# 3. Entity co-occurrence
python "$WRAP" GET "/v1/analytics/entity-cooccurrence?limit=50"

# 4. Skills by role
python "$WRAP" GET /v1/analytics/skills-by-role

# 5. Entity counts by type
python "$WRAP" POST /v1/query/sql \
  '{"sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC"}'
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
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Count entities by type
python "$WRAP" POST /v1/query/sql \
  '{"sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC"}'

# Find entities by name
python "$WRAP" POST /v1/query/sql \
  '{"sql": "SELECT entity_id, canonical_name, entity_type FROM skillenai.entities WHERE canonical_name ILIKE $1 LIMIT 20", "params": ["%OpenAI%"]}'
```

**Postgres tables:** `skillenai.entities`, `skillenai.documents`, `skillenai.document_entity_links`, `skillenai.relationships`

Use `python "$WRAP" GET /v1/catalog/postgres` to see all columns.

---

## Flow 4: OpenSearch Queries (`search`)

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Full-text search
python "$WRAP" POST /v1/query/search \
  '{"query": {"query": {"match": {"title": "machine learning"}}, "size": 20}}'

# Aggregation: top skills in job postings (via nested entity agg)
python "$WRAP" POST /v1/query/search \
  '{"query": {"size": 0, "query": {"term": {"sourceType": "jobs"}}, "aggs": {"skill_entities": {"nested": {"path": "entities"}, "aggs": {"skills_only": {"filter": {"term": {"entities.resolved.entityType": "skill"}}, "aggs": {"top_skills": {"terms": {"field": "entities.resolved.canonicalName.keyword", "size": 50}}}}}}}}, "indices": ["prod-enriched-jobs"]}'
```

---

## Flow 5: Analytics (`trends` / `cooccurrence`)

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Document counts by source
python "$WRAP" GET /v1/analytics/counts

# Topic trends over time
python "$WRAP" GET "/v1/analytics/topic-trends?limit=50"

# Entity co-occurrence
python "$WRAP" GET "/v1/analytics/entity-cooccurrence?limit=50"
```

---

## Flow 6: Graph Queries (`graph`)

Use raw Cypher only when the dedicated endpoints (skills-by-role, jobs/search, resolution) don't cover the query. Common use cases:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Companies posting the most jobs for a skill
python "$WRAP" POST /v1/query/graph \
  '{"cypher": "MATCH (j:job)-[:REQUIRES]->(s:skill), (j)-[:POSTED_BY]->(c:company) WHERE s.name = '\''Python'\'' RETURN c.name AS company, count(*) AS jobs ORDER BY jobs DESC", "limit": 20}'

# Skills co-occurring with a specific skill
python "$WRAP" POST /v1/query/graph \
  '{"cypher": "MATCH (j:job)-[:REQUIRES]->(s1:skill), (j)-[:REQUIRES]->(s2:skill) WHERE s1.name = '\''Python'\'' AND s1.id <> s2.id RETURN s2.name AS co_skill, count(*) AS overlap ORDER BY overlap DESC", "limit": 20}'

# Entity mentions in scholarly papers
python "$WRAP" POST /v1/query/graph \
  '{"cypher": "MATCH (d:document)-[:MENTIONS]->(e:skill) WHERE d.source_type = '\''scholarly'\'' RETURN e.name AS skill, count(*) AS mentions ORDER BY mentions DESC", "limit": 30}'
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
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Single role — resolved via entity resolution (fuzzy OK)
python "$WRAP" GET "/v1/analytics/skills-by-role?role=Data+Scientist"

# Comma-separated aliases — merged into a single skill profile
python "$WRAP" GET "/v1/analytics/skills-by-role?role=ML+Engineer,Machine+Learning+Engineer"
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
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Basic search
python "$WRAP" POST /v1/jobs/search \
  '{"query": "machine learning engineer NLP deep learning", "size": 20}'

# With all ranking signals
python "$WRAP" POST /v1/jobs/search \
  '{
    "query": "AI engineer NLP LLMs RAG Python",
    "seniority": "senior",
    "min_salary": 180000,
    "filters": {"workModel": "remote"},
    "recency_decay": "30d",
    "size": 20
  }'

# With skill entity boosts (resolve names first via /v1/resolution/entities)
python "$WRAP" POST /v1/jobs/search \
  '{
    "query": "AI engineer",
    "skill_boosts": [
      {"entity_id": "175c2b707caa6eb1", "weight": 10.0},
      {"entity_id": "f0109bbf0cbac010", "weight": 7.0}
    ],
    "seniority": "senior",
    "size": 20
  }'

# With geo-distance (lat, lon + radius)
python "$WRAP" POST /v1/jobs/search \
  '{
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
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

python "$WRAP" POST /v1/resolution/entities \
  '{
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

## Flow 10: Alerts (`alerts` / "email me when …")

User alert subscriptions run a saved Data Products query on a cadence and email the results. CRUD lives on **`app.skillenai.com`**, not the data products API — but the same `X-API-Key` authenticates both. Pass `--host app` to the wrapper. Full endpoint reference in `${CLAUDE_PLUGIN_ROOT}/docs/endpoints/alerts.md`.

The agent flow is **preview → create → (optional) run now**. Iterate on the preview until the credit cost and subject line look right, then commit.

### Step 1: Preview the query

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

python "$WRAP" POST /alerts/preview --host app \
  '{
    "name": "Senior ML jobs",
    "source_query": {
      "endpoint": "/v1/jobs/search",
      "payload": {"query": "machine learning engineer", "seniority": "senior", "size": 10}
    },
    "delivery_config": {"channel": "email", "target": "alerts@example.com"}
  }' | python3 -m json.tool
```

The response returns `item_count`, the `rendered.subject` the recipient will see, and a `credits` block. Inspect all three before creating the alert:

- If `item_count == 0`, the query is too narrow — broaden the `payload`.
- If `rendered.subject` reads awkwardly, tweak `name` — the default subject is `"Skillenai alert: N new results for <name>"`.
- `credits.used` is the cost of ONE run. **Multiply by cadence to estimate monthly burn** (`used × runs_per_day × 30`). A 17-credit query running hourly costs ~12,240 credits/month.

### Step 2: Create the alert

```bash
python "$WRAP" POST /alerts --host app \
  '{
    "name": "Senior ML jobs",
    "source_query": {
      "endpoint": "/v1/jobs/search",
      "payload": {"query": "machine learning engineer", "seniority": "senior", "size": 10}
    },
    "delivery_config": {"channel": "email", "target": "alerts@example.com"},
    "schedule_cadence_seconds": 86400
  }' | python3 -m json.tool
```

Returns 201 with an `AlertInfo` row (id, next_run_at, etc.). **Always pass `name`** so the email subject is human-readable. `schedule_cadence_seconds` is the interval between runs.

### Step 3: (Optional) Trigger a run now

```bash
ALERT_ID="<id from step 2>"
python "$WRAP" POST "/alerts/$ALERT_ID/run" --host app
```

The run is asynchronous. Poll `GET /alerts/$ALERT_ID --host app` until `last_run_at` advances (typically 30–60s).

### Step 4: Manage

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# List
python "$WRAP" GET /alerts --host app

# Read one
python "$WRAP" GET "/alerts/$ALERT_ID" --host app

# Partial update — pause, rename, change cadence, etc.
python "$WRAP" PATCH "/alerts/$ALERT_ID" --host app '{"is_active": false}'

# Delete (204)
python "$WRAP" DELETE "/alerts/$ALERT_ID" --host app
```

**Source query scope:** any Data Products API endpoint the caller's key can hit is fair game for `source_query.endpoint`. The scheduled run authorises and bills against that key exactly like a direct call would.

**Channels:** only `email` is supported today. Additional channels will be added as separate `delivery_config.channel` values.

---

## API Reference Quick Summary

### Endpoints

| Endpoint | Method | Host | Description |
|----------|--------|------|-------------|
| `/v1/health` | GET | api | Service health |
| `/v1/version` | GET | api | Service metadata |
| `/v1/analytics/counts` | GET | api | Document counts by source |
| `/v1/analytics/entity-cooccurrence` | GET | api | Top entity pairs |
| `/v1/analytics/topic-trends` | GET | api | Monthly topic trends |
| `/v1/analytics/skills-by-role` | GET | api | **Skill distributions by role (entity-resolved, comma-separated aliases)** |
| `/v1/jobs/search` | POST | api | **Multi-signal job search (RRF + boosts + seniority + geo + recency)** |
| `/v1/resolution/entities` | POST | api | **Resolve names to entity IDs** |
| `/v1/catalog` | GET | api | List all projection schemas |
| `/v1/catalog/{projection}` | GET | api | Schema for one projection |
| `/v1/query/sql` | POST | api | SQL against Postgres |
| `/v1/query/athena` | POST | api | SQL against Athena/S3 |
| `/v1/query/graph` | POST | api | Cypher against knowledge graph |
| `/v1/query/search` | POST | api | OpenSearch Query DSL |
| `/alerts/preview` | POST | app | **Dry-run an alert query; see item_count, rendered email, and credit cost** |
| `/alerts` | POST | app | Create a scheduled alert |
| `/alerts` | GET | app | List the caller's alerts |
| `/alerts/{id}` | GET/PATCH/DELETE | app | Read/update/delete one alert |
| `/alerts/{id}/run` | POST | app | Trigger an ad-hoc run |

### Entity types: `company`, `product`, `person`, `skill`, `location`
### Source types: `blog`, `news`, `scholarly`, `social`, `jobs`, `product`, `company`, `person`

---

## Helper Scripts

The `scripts/` directory (at `${CLAUDE_PLUGIN_ROOT}/scripts/`) contains Python helpers that resolve credentials the same way the wrapper does (env → `~/.skillenai/.env` → plugin `.env` → cwd `.env`). All require `requests` and `python-dotenv` (`pip install requests python-dotenv`).

| Script | Purpose |
|--------|---------|
| `scripts/oauth_setup.py` | Browser-based OAuth setup that writes `~/.skillenai/.env` |
| `scripts/api.py` | Authenticated request wrapper (used by every flow above) |
| `scripts/eda_report.py` | Generate a comprehensive EDA markdown report |
| `scripts/skill_analysis.py` | Analyze skill demand by role, compare roles |
| `scripts/trend_analysis.py` | Topic trend time series, growth analysis |
| `scripts/job_search.py` | Multi-signal job search with formatted output |
| `scripts/download_jobs_paginated.py` | Paginated per-job download with arbitrary filter segments; handles 429 backoff |
| `scripts/canonicalize_skills.py` | Collapse duplicate skill surface forms (case/punct/acronym variants) before aggregating |

Run any script with `--help` for usage details.

**Before aggregating skills by anything** (role, geo, seniority, time), pass per-job data through `canonicalize_skills.py` first. The entity resolver emits duplicate canonical names for skills like `RAG` vs `Retrieval-Augmented Generation (RAG)`, and aggregations will undercount or give contradictory signals otherwise.

---

## Analysis Patterns

**Skill demand by role:** `GET /v1/analytics/skills-by-role?role=X` — resolves role names via entity resolution (fuzzy OK). Use comma-separated aliases to merge (e.g. `?role=ML+Engineer,Machine+Learning+Engineer`).

**Dataset composition:** `GET /v1/analytics/counts` for document volumes, `POST /v1/query/sql` for entity counts by type.

**Entity resolution:** `POST /v1/resolution/entities` to map names to IDs, then use IDs in graph queries or job search `skill_boosts`.

**Job search with profile:** Resolve skills to entity IDs, then `POST /v1/jobs/search` with `skill_boosts`, `seniority`, `min_salary`, `filters`, and `recency_decay`.

**Research-industry gap:** Compare topic distributions from scholarly vs jobs via SQL or Athena.

**Entity ecosystem mapping:** SQL to find entity by name, graph for relationships, SQL for linked documents.

---

## Security

The API key is a long-lived credential. Once leaked, it is valid until the user notices and revokes it on `app.skillenai.com` → API Keys. The skill is structured so the agent **cannot** accidentally leak it — but agents fail in surprising ways, so these are explicit hard rules:

- **NEVER** print the contents of `~/.skillenai/.env`. No `cat`, `head`, `less`, `tail`, `tee`, `od`, `xxd`, or anything else that streams the file. The user can open it in their editor if they need to read it.
- **NEVER** run `echo $API_KEY`, `env | grep -i api`, `printenv API_KEY`, `set | grep API`, or anything else that surfaces an env var named `API_KEY`. The wrapper script is the only place `API_KEY` should be loaded into a process; the agent's bash does not need it.
- **NEVER** invoke `curl` directly with `-H "X-API-Key: …"`. Always go through `scripts/api.py`.
- **NEVER** use `curl -v`, `curl --trace`, `curl --trace-ascii`, or any verbose/trace flag — these echo request headers including `X-API-Key`.
- **NEVER** add `--debug`, `set -x`, `bash -x`, or shell tracing while a flow that touches credentials is running.
- **If the user asks the agent to show them their key**, respond: "For security, I won't print your key. You can read `~/.skillenai/.env` yourself if you need it. To rotate, revoke the old key on app.skillenai.com → API Keys, then run `/skillenai:api setup` for a new one."
- **If the user pastes a `skn_live_…` string into chat**, follow the "If the user pastes a key in chat" guidance above — point them at `setup`, do NOT save the pasted key on their behalf.

---

## Important Notes

1. **Rate limits:** 120 req/min for READ tier, 60/min for SEARCH, 20/min for ANALYTICS, 10/min for QUERY
2. **Credits:** API calls consume credits. Check `X-Credits-Remaining` response header.
3. **Analytics latency:** Athena-backed endpoints may take several seconds
4. **Use python3 for aggregation** — pipe wrapper output to python3 for counting, sorting, and cross-tabulating
5. **Schema discovery:** Check `GET /v1/catalog` before writing queries to verify table/column names
6. **Prefer dedicated endpoints** over raw queries: `skills-by-role` over Cypher, `jobs/search` over DSL, `resolution/entities` over SQL lookups
