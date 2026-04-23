---
name: job-finder
description: Find relevant jobs based on a resume or skill profile. Parses resumes, builds weighted skill profiles, compares against market demand, and searches the Skillenai job index for matching positions.
user-invocable: true
argument-hint: [resume <path>|search|refine] <details>
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# Skillenai Job Finder Skill

Invoke as `/skillenai:job-finder` (when installed via `/plugin install skillenai`).

This skill helps users find relevant job postings by parsing their resume, building a weighted skill profile, and running multi-signal searches against the Skillenai job index.

## Credentials

Every API call in this skill goes through the shared wrapper at `${CLAUDE_PLUGIN_ROOT}/scripts/api.py`, which resolves the API key in its own process. **The key is never visible to the agent's shell, never in `curl` argv, and never in the conversation transcript.**

Before running any flow, verify credentials exist:

```bash
[ -n "$API_KEY" ] || [ -f ~/.skillenai/.env ]
```

If neither is set, **stop and tell the user**:

> No Skillenai API key found. Run `/skillenai:api setup` to authorize — it'll open a browser, you sign in or create an account, and the key gets saved automatically. Takes about 30 seconds.

All `curl`/API examples below use the wrapper:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"
python "$WRAP" POST /v1/jobs/search '{"query": "...", "size": 20}'
```

See the security rules in the `/skillenai:api` skill — same rules apply here (never `cat ~/.skillenai/.env`, never `echo $API_KEY`, never `curl -v`, etc.).

## Overview

The job finder operates in three phases:

1. **Resume Parsing** (agentic) — Extract skills, titles, location, seniority from a resume
2. **Profile Building** (agentic + API) — Resolve skills to entity IDs, compare against market demand, ask user for preferences
3. **Job Search** (API) — Single hybrid search call with all ranking signals baked in

---

## Phase 1: Resume Parsing

This phase is fully agentic — the LLM reads the resume and extracts structured data. No API calls needed.

### Input

The user provides a resume as a file path (PDF, markdown, or plain text). Read it with the Read tool.

### Extraction Instructions

Parse the resume and extract:

```json
{
  "skills": [
    {"name": "Python", "emphasis": 10, "evidence": "Listed in skills, used across 4 roles"},
    {"name": "PyTorch", "emphasis": 7, "evidence": "Listed in skills, used in 2 roles"}
  ],
  "job_titles": [
    {"title": "Senior Manager, AI Engineering", "company": "Example Corp", "years": "2025-present"},
    {"title": "Principal NLP Data Scientist", "company": "Example Corp", "years": "2023-2025"}
  ],
  "target_titles": ["AI Engineer", "ML Engineer", "NLP Engineer", "Data Scientist"],
  "seniority": "senior",
  "years_experience": 10,
  "location": {"country": "US", "region": "CA", "city": "San Francisco"},
  "work_model_preference": "remote",
  "education": ["MBA", "BS Physics"],
  "domain_keywords": ["NLP", "LLM", "agents", "RAG", "search", "information retrieval"]
}
```

### Emphasis Scoring (1-10)

Score each skill based on how prominently it appears:

| Score | Criteria |
|:-----:|:---------|
| 9-10 | Core identity skill — listed in skills section AND used across 3+ roles AND central to job descriptions |
| 7-8 | Strong skill — listed in skills AND used in 2+ roles |
| 5-6 | Moderate — listed in skills OR mentioned in 2+ roles |
| 3-4 | Light — mentioned once in a job description or listed but not emphasized |
| 1-2 | Peripheral — mentioned in passing, listed as a minor tool |

### Seniority Classification

Seniority uses the LLM-extracted `seniorityLevel` field on job documents. It follows a dual-track ladder that diverges after "senior" into IC and management tracks. Equivalent levels across tracks share the same ordinal rank.

```
Common:     intern → entry → mid → senior
                                     ↙         ↘
IC track:              staff    →    principal
Mgmt track:     lead/manager   →    director   →  vp  →  c-level
```

Classify the **user's** seniority from their most recent title + years of experience, using the same taxonomy:

| Pattern | Level | Track |
|:--------|:------|:------|
| `intern, internship, co-op` | intern | common |
| `entry-level, associate, new grad, junior` | entry | common |
| `(no seniority markers, 2-5 years)` | mid | common |
| `senior, sr, 5-10 years` | senior | common |
| `staff` | staff | IC |
| `principal, distinguished` | principal | IC |
| `lead, tech lead, team lead` | lead | mgmt |
| `manager, engineering manager` | manager | mgmt |
| `director, senior director` | director | mgmt |
| `vp, vice president` | vp | mgmt |
| `cto, ceo, chief, head of, executive` | c-level | mgmt |

**Important:** Use these exact values when passing `seniority` to the search endpoint — they must match the LLM taxonomy.

### Additional User Inputs

After extracting the profile, ask the user for:
- **Target salary** (optional): minimum acceptable salary in USD
- **Work model preference**: remote, hybrid, onsite, or any
- **Location preference**: confirm extracted location; always use `locationCountry` filter (ISO code, e.g. `"US"`) to restrict results to the user's country
- **Any companies to exclude**

Save the profile to `reports/job-search-profile.json` (or a path the user specifies) and present it to the user for confirmation.

---

## Phase 2: Profile Building & Skill Resolution

### Step 2a: Resolve Skills to Entity IDs

The job search endpoint boosts jobs by skill entity IDs. Resolve the user's skill names to entity IDs in a single call:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Resolve all skills at once (max 50 per request)
python "$WRAP" POST /v1/resolution/entities \
  '{
    "names": [
      {"name": "Python", "entity_type": "skill"},
      {"name": "PyTorch", "entity_type": "skill"},
      {"name": "LangChain", "entity_type": "skill"},
      {"name": "RAG", "entity_type": "skill"}
    ],
    "mode": "auto",
    "limit": 1
  }'
```

**Response:**
```json
{
  "results": [
    {"name": "Python", "entity_type": "skill", "candidates": [
      {"entity_id": "175c2b707caa6eb1", "canonical_name": "Python", "match_score": 1.0, "match_method": "exact"}
    ]},
    {"name": "PyTorch", "entity_type": "skill", "candidates": [
      {"entity_id": "f0109bbf0cbac010", "canonical_name": "PyTorch", "match_score": 1.0, "match_method": "exact"}
    ]}
  ]
}
```

**Modes:** `auto` tries exact lookup first, falls back to full-text search for misses. Use `exact` for speed when names are likely canonical, `fts` for fuzzy matching.

Build `skill_boosts` from the resolved IDs, weighting by resume emphasis:

```json
[
  {"entity_id": "175c2b707caa6eb1", "weight": 10.0},
  {"entity_id": "f0109bbf0cbac010", "weight": 7.0}
]
```

Skills that fail to resolve (no candidates) should be omitted from `skill_boosts` — they'll still contribute via BM25 text matching in the query string.

### Step 2b: Skill Demand Check

Call the skills-by-role endpoint to compare against market demand. Role names are resolved via entity resolution (fuzzy OK), and comma-separated aliases are merged into a single profile:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# Single role — entity-resolved (fuzzy matching)
python "$WRAP" GET "/v1/analytics/skills-by-role?role=Data+Scientist"

# Merge aliases into one profile
python "$WRAP" GET "/v1/analytics/skills-by-role?role=ML+Engineer,Machine+Learning+Engineer"
```

The response includes `queried_role` (original input), `resolved_roles` (canonical names matched), and a merged skill profile with job counts.

### Step 2c: Weighted Coverage Calculation

For each target role, compute:

```
coverage = sum(demand_weight_i * emphasis_weight_i) / sum(demand_weight_i)
```

Where:
- `demand_weight_i` = job_count for skill i / max job_count in that role
- `emphasis_weight_i` = resume emphasis (1-10) / 10, or 0 if not on resume

Present to the user:
- Coverage % per role
- Top matched skills per role
- Top gaps per role (high demand, missing from resume)

### Step 2d: Build Search Query Text

Compose a natural-language summary for the hybrid search query:

```
"Senior AI/ML engineer. NLP, LLMs, RAG, AI agents, fine-tuning LoRA PEFT,
production ML systems. Python, PyTorch, LangChain, LangGraph, OpenSearch,
vector databases, embeddings, evaluation frameworks."
```

This text drives both the BM25 keyword matching and the server-side embedding for vector similarity. Emphasize the highest-weighted skills.

### Step 2e: Build Title Boosts

From the parsed `job_titles` and `target_titles`, build the `title_boosts` parameter. The weighting strategy depends on whether the user is continuing their career trajectory or pivoting:

**Career pivot** (target_titles differ significantly from job history):
- Use ONLY `target_titles` in `title_boosts`, each at weight 10.0
- Do NOT include past job titles — the user wants to move away from those roles
- Lean heavily on `skill_boosts` as the primary matching signal (skills transfer across roles)

**Career continuation** (target_titles similar to job history):
- Include `target_titles` at weight 10.0
- Include past titles with exponential decay: current title 5.0, previous 2.5, two-back 1.25 (floor at 1.0)

Example (continuation):
```json
"title_boosts": [
    {"title": "AI Engineering Manager", "weight": 10.0},
    {"title": "ML Engineer", "weight": 10.0},
    {"title": "Senior Manager, AI Engineering", "weight": 5.0},
    {"title": "Principal NLP Data Scientist", "weight": 2.5},
    {"title": "Senior NLP Data Scientist", "weight": 1.25}
]
```

### Step 2f: Build Text Boosts

Join `domain_keywords` from the profile into a `text_boosts` entry against `["extractedText"]` with weight 2.0. This catches keyword co-occurrence patterns the constructed query and skill entity boosts might miss:

```json
"text_boosts": [
    {"text": "NLP LLM agents RAG search information retrieval vector search embeddings", "fields": ["extractedText"], "weight": 2.0}
]
```

---

## Phase 3: Job Search

### Step 3a: Hybrid Search

The hybrid search endpoint handles all ranking in a single call — BM25 + vector via RRF, plus skill boosts, seniority scoring, recency decay, and filters:

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

python "$WRAP" POST /v1/jobs/search \
  '{
    "query": "<profile summary from step 2d>",
    "size": 30,
    "k": 50,
    "skill_boosts": [
      {"entity_id": "abc123", "weight": 10.0},
      {"entity_id": "def456", "weight": 7.0}
    ],
    "title_boosts": [
      {"title": "AI Engineer", "weight": 10.0},
      {"title": "Senior Manager, AI Engineering", "weight": 5.0}
    ],
    "text_boosts": [
      {"text": "NLP LLM agents RAG embeddings vector search", "fields": ["extractedText"], "weight": 2.0}
    ],
    "seniority": "senior",
    "location_country": "US",
    "recency_decay": "30d",
    "min_salary": 180000,
    "filters": {
      "workModel": "remote"
    }
  }'
```

### Ranking Signals (all handled server-side)

| Signal | How it works |
|:-------|:------------|
| **BM25 + Vector (RRF)** | Base relevance — text match and semantic similarity combined via Reciprocal Rank Fusion |
| **Skill entity boosts** | Nested query: jobs requiring a user's skill get boosted by that skill's emphasis weight |
| **Title boosts** | `match_phrase` queries on `title` field with slop 1. Each title phrase boosted by its weight. |
| **Text boosts** | `multi_match` queries on specified fields (default: `extractedText`). For injecting resume text or domain keywords. |
| **Seniority (ordinal, dual-track)** | Uses `seniorityLevel` (LLM-extracted). Exact match → boost 5; cross-track equivalent (e.g. staff ↔ lead) → boost 4; ±1 rank → boost 2; ±2+ → filtered out |
| **Recency decay** | Exponential decay on `postedAt` (falls back to `ingestedAt` when missing) — 30d scale means 30-day-old jobs score ~50% of new ones |
| **Salary** | Jobs with `salaryMax` >= `min_salary` get boosted. Missing salary data is neutral (no penalty). |
| **Location country** | `location_country` parameter filters by ISO code on `locationCountry` field. Uses `match` query to work with both text and keyword mappings. |

### Filter Options

Pass in the `filters` object. Scalar values become `term` filters, lists become `terms` (OR), and **dict values are passed as-is** to the underlying search engine (enabling `match`, `bool`, `match_phrase`, etc.):

```json
// Remote only
{"workModel": "remote"}

// Multiple work models
{"workModel": ["remote", "hybrid"]}

// Specific company
{"company": "Acme"}
```

### Location Filtering

Use the dedicated `location_country` parameter (not filters) for country-level filtering:

```json
"location_country": "US"
"location_country": ["US", "CA"]
```

This uses a `match` query on `locationCountry` that works regardless of whether the field is mapped as `text` or `keyword`. Remote jobs without explicit country in their location text may be excluded.

**Notes:**
- `location_country` is separate from `filters` — don't duplicate it in both
- The `location`/`location_radius` request fields apply geo-distance hard filters + proximity decay. Use only for hybrid/onsite searches (remote jobs often lack geocodes)
- Do NOT use `geo_distance` in the filter dict passthrough — it fails in the RRF pipeline context

### Response Format

```json
{
  "hits": [
    {
      "id": "abc123...",
      "score": 0.0085,
      "source": {
        "title": "Senior AI Engineer",
        "company": "Acme",
        "location": "Remote, US",
        "seniority": "senior",
        "workModel": "remote",
        "sourceUrl": "https://...",
        "salary": "$180,000-$220,000",
        "topics": ["agents", "nlp"],
        "enrichedAt": "2026-04-01T...",
        "extractedText": "...",
        "canonicalEntityIds": ["eid1", "eid2", "..."]
      }
    }
  ],
  "total": 150,
  "took_ms": 320
}
```

### Step 3b: Evaluate and Refine

After getting initial results, review them against the user's resume and assess relevance using your own judgment. Think like a recruiter: do these results match the user's career goals, seniority, and core strengths?

If results feel off — wrong industry, wrong seniority band, missing the user's core strengths, irrelevant companies — reason about *why* and adjust search parameters accordingly:

- Rewrite the query text to emphasize different aspects of the profile
- Adjust skill boost weights up or down
- Change the seniority parameter
- Add or modify title_boosts
- Broaden or narrow text_boosts
- Adjust filters (workModel, location_country)

Iterate as many times as needed until results are satisfactory or you determine the index simply doesn't have better matches. Each iteration should note what was changed and why.

### Step 3c: Analyze Results

For each returned job, the agentic layer should:

1. **Cross-reference `canonicalEntityIds`** against the user's resolved skill entity IDs to identify exact skill matches and gaps
2. Present top 15-20 results with:
   - Job title, company, location, work model, salary (if available)
   - Source URL (clickable link)
   - Which of the user's top skills this job requires (from entity ID overlap)
   - Skill gaps (what the job wants that the user doesn't have)
   - Seniority and location fit notes

---

## Phase 4: Refinement (Interactive)

After presenting initial results, offer:

- **"More like this"** — Re-run hybrid search with a specific job's title + key terms as the query
- **"Exclude company X"** — Re-run with added filter
- **"Focus on skill X"** — Increase that skill's boost weight
- **"Show skill gaps"** — Aggregate `canonicalEntityIds` across top results, resolve to names, compare to profile
- **"Broader/narrower search"** — Adjust `k`, `size`, seniority range, or recency decay

### Skill Gap Aggregation (via raw DSL)

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

python "$WRAP" POST /v1/query/search \
  '{
    "query": {
      "size": 0,
      "query": {"bool": {"filter": [{"match": {"title": "<target role>"}}]}},
      "aggs": {
        "skill_entities": {
          "nested": {"path": "entities"},
          "aggs": {
            "skills_only": {
              "filter": {"term": {"entities.resolved.entityType": "skill"}},
              "aggs": {
                "top_skills": {
                  "terms": {"field": "entities.resolved.canonicalName.keyword", "size": 50}
                }
              }
            }
          }
        }
      }
    },
    "indices": ["prod-enriched-jobs"]
  }'
```

---

## Job Index Schema Reference

The `prod-enriched-jobs` index exposes these fields:

| Field | Type | Description |
|:------|:-----|:------------|
| `documentId` | keyword | MD5 hash of source URL |
| `title` | text | Job title |
| `extractedText` | text | Full job description |
| `company` | keyword | Company name |
| `location` | text | Location string |
| `locationCountry` | keyword | ISO country code (from geocoded location entity) |
| `locationCity` | keyword | City name (from geocoded location entity) |
| `locationGeocode` | geo_point | Lat/lon coordinates (for distance scoring) |
| `salary` | text | Raw salary text |
| `salaryMin` | integer | Parsed minimum salary (USD) |
| `salaryMax` | integer | Parsed maximum salary (USD) |
| `seniority` | keyword | Scraper title-based classification (less reliable) |
| `seniorityLevel` | keyword | LLM-extracted: `intern`, `entry`, `mid`, `senior`, `staff`, `principal`, `lead`, `manager`, `director`, `vp`, `c-level` |
| `postedAt` | date | Job posting date |
| `roles` | keyword[] | Structured role categories (e.g. `Data Scientist`, `ML Engineer`) |
| `workModel` | keyword | `remote`, `hybrid`, `onsite` |
| `department` | keyword | Department name |
| `sourceUrl` | keyword | Original job posting URL |
| `topics` | keyword[] | Coarse taxonomy tags |
| `entities` | nested | NER-extracted entities with resolution (`entityId`, `entityType`, `canonicalName`) |
| `canonicalEntityIds` | keyword[] | Flat list of resolved entity IDs (for quick matching) |
| `embedding` | knn_vector | Dense embedding for semantic search |
| `enrichedAt` | date | When the job was enriched |

---

## API Endpoints Used

| Endpoint | Method | Purpose |
|:---------|:------:|:--------|
| `/v1/jobs/search` | POST | Multi-signal job search (RRF hybrid + skill/title/text boosts + seniority + location_country + recency) |
| `/v1/resolution/entities` | POST | Resolve skill names → entity IDs (exact + FTS fallback) |
| `/v1/analytics/skills-by-role` | GET | Skill distributions by role (entity-resolved, comma-separated aliases merged) |
| `/v1/query/search` | POST | Raw search DSL (for aggregations, skill gap analysis) |
| `/v1/query/graph` | POST | Cypher queries (for custom graph traversal) |

---

## Limitations

- **`locationCountry` coverage is partial** — many remote jobs without an explicit country in the location text will be missing from country-filtered results.
- **`workModel` coverage is partial** — roughly half of jobs have an empty `workModel` and won't match workModel filters.
- **Geographic distance scoring** via `location` + `location_radius` is available, but many remote jobs lack `locationGeocode`, so geo-distance filtering may exclude valid remote roles. Use `location`/`location_radius` only for hybrid/onsite searches.
- **Salary data** is parsed into `salaryMin`/`salaryMax` but coverage is sparse — many job boards don't provide salary data. The `min_salary` boost works when data is present but won't filter out jobs without salary info.
- **Seniority coverage** depends on the LLM-extracted `seniorityLevel` field, which is not populated for every job.

---

## Helper Scripts

The `scripts/job_search.py` helper (shipped with this plugin) wraps a subset of the hybrid search flow for one-shot searches from the CLI:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/job_search.py" \
  "AI engineer NLP LLMs" --seniority senior --min-salary 180000 --remote
```

For the full resume → profile → refined search workflow described above, use the phased flow in this SKILL directly rather than the helper.

---

## Report Output

Write the final report to `reports/job-search-<date>.md` with:
1. Profile summary (skills, seniority, target roles)
2. Market fit analysis (coverage % per role, top gaps)
3. Ranked job list with clickable links and per-job skill overlap analysis
4. Aggregate skill gap analysis across top results
