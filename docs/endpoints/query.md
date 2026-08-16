# Query Endpoints

Direct query access to four data projections. Use these when the pre-built analytics endpoints don't cover your needs.

**Prefer dedicated endpoints first:** `skills-by-role` over Cypher, `jobs/search` over OpenSearch DSL, `resolution/entities` over SQL lookups. Raw queries cost more credits and are rate-limited more aggressively (QUERY tier: 10/min).

---

## POST /v1/query/sql

Execute SQL against the Postgres relational store.

### Request Body

```json
{
  "sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC",
  "params": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sql` | string | SQL query (read-only: SELECT only) |
| `params` | array | Positional parameters referenced as `$1`, `$2`, etc. in the SQL |

### Examples

```bash
source .env

# Count entities by type
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT entity_type, count(*) AS n FROM skillenai.entities GROUP BY entity_type ORDER BY n DESC"}'

# Find entities by name (parameterized)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/sql" \
  -d '{"sql": "SELECT entity_id, canonical_name, entity_type FROM skillenai.entities WHERE canonical_name ILIKE $1 LIMIT 20", "params": ["%OpenAI%"]}'
```

### Available Tables

Use `GET /v1/catalog/postgres` to see the full schema. Key tables:

- `skillenai.entities` — All canonical entities
- `skillenai.documents` — All indexed documents
- `skillenai.document_entity_links` — Document-entity associations
- `skillenai.relationships` — Entity-to-entity relationships

---

## POST /v1/query/athena

Execute SQL against the Athena/S3 analytical store. Suited for large-scale aggregations over historical data.

### Request Body

```json
{
  "sql": "SELECT source_type, COUNT(*) as n FROM documents GROUP BY source_type ORDER BY n DESC"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sql` | string | Athena-compatible SQL (read-only) |

**Note:** Athena queries may take several seconds due to cold-start latency. The API has a 30-second timeout for query execution.

---

## POST /v1/query/graph

Execute Cypher queries against the Apache AGE knowledge graph.

### Request Body

```json
{
  "cypher": "MATCH (j:job)-[:REQUIRES]->(s:skill) WHERE s.name = 'Python' RETURN j.title, j.company LIMIT 20",
  "limit": 20
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cypher` | string | *required* | Read-only Cypher query (MATCH/RETURN only) |
| `limit` | int | 100 | Max rows (capped at 1,000) |

### Constraints

- **Read-only:** Only `MATCH` and `RETURN` clauses are allowed
- **Max 1,000 rows** per query
- **30-second timeout** per query
- **Max 3 hops** in variable-length paths. Use `[*..3]` or fewer; `[*..4]` is rejected.
- **Max 2 comma-separated patterns** in a MATCH. A cartesian-product query with three patterns (e.g. `MATCH (a), (b)-[:R]->(c), (d)`) is rejected. Rewrite with chained relationship patterns instead — e.g. `MATCH (a)<-[:MENTIONS]-(d)-[:MENTIONS]->(b)` rather than `MATCH (a), (d), (b)`.
- **Node-property exposure is minimal:** `document` nodes only expose `id` and `name` via graph. If you need `source_type`, `title`, `published_at` for matched documents, join back to `skillenai.documents` in the Postgres projection via the document ID.

### Examples

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

# Bridge documents: how narratively entangled are two entities?
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (a:product {id: '\''ID_A'\''})<-[:MENTIONS]-(d)-[:MENTIONS]->(b:company {id: '\''ID_B'\''}) RETURN count(DISTINCT d) AS bridge_docs"}'

# Co-required products in job postings (complementary toolkit detection)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (a:product {id: '\''ID_A'\''})<-[:MENTIONS]-(j:job)-[:MENTIONS]->(b:product {id: '\''ID_B'\''}) RETURN count(j) AS co_required_jobs"}'

# Internal hiring stack for a company (product-eng depth signal)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/graph" \
  -d '{"cypher": "MATCH (j:job)-[:POSTED_BY]->(c:company {id: '\''ID'\''}), (j)-[:MENTIONS]->(p:product) RETURN p.name AS product, count(j) AS jobs ORDER BY jobs DESC", "limit": 20}'
```

### Node Labels and Properties

| Label | Key Properties |
|-------|---------------|
| `job` | `title`, `company`, `roles` (pipe-delimited string), `location`, `salary_min`, `salary_max`, `posted_at` |
| `document` | `title`, `source_type`, `source_url`, `published_at` |
| `skill` | `name`, `id` |
| `company` | `name`, `id` |
| `product` | `name`, `id` |
| `person` | `name`, `id` |
| `location` | `name`, `id` |

### Edge Labels

| Edge | Direction | Meaning |
|------|-----------|---------|
| `REQUIRES` | job -> skill | Job requires this skill |
| `MENTIONS` | document -> entity | Document mentions this entity |
| `POSTED_BY` | job -> company | Job posted by this company |
| `AUTHORED` | person -> document | Person authored this document |

---

## POST /v1/query/search

Execute raw OpenSearch Query DSL against the search index. For full-text search, aggregations, and nested entity queries.

### Request Body

```json
{
  "query": {
    "query": {"match": {"title": "machine learning"}},
    "size": 20
  },
  "indices": ["prod-enriched-content"]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | object | *required* | OpenSearch Query DSL body |
| `indices` | array | `null` | Target indices. If omitted, queries the default content index. |

### Examples

```bash
source .env

# Full-text search
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/search" \
  -d '{"query": {"query": {"match": {"title": "machine learning"}}, "size": 20}}'

# Aggregation: top skills in job postings
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$API_URL/v1/query/search" \
  -d '{"query": {"size": 0, "query": {"term": {"sourceType": "jobs"}}, "aggs": {"skill_entities": {"nested": {"path": "entities"}, "aggs": {"skills_only": {"filter": {"term": {"entities.resolved.entityType": "skill"}}, "aggs": {"top_skills": {"terms": {"field": "entities.resolved.canonicalName.keyword", "size": 50}}}}}}}}, "indices": ["prod-enriched-jobs"]}'
```

### Tips

- Use `"size": 0` with `"aggs"` for pure aggregation queries (no document hits returned)
- Entities are stored in a nested `entities` field — use `nested` queries/aggregations
- Check `GET /v1/catalog/opensearch` for the full index mapping

### Search behaviour worth knowing

A few behaviours here differ from a raw OpenSearch cluster and will otherwise produce
confident but wrong results.

**`random_score` is not applied.** `function_score` with `random_score` returns the same
documents regardless of seed — two different seeds yield identical `_id` lists, matching the
unsorted query. Documents come back in internal index order, which clusters by ingest time
and source, so it is *not* a random sample.

To sample uniformly, sort on `documentId`. It is a hash of the source URL, distributed evenly
and independent of source or date, so any contiguous slice of that order is an unbiased sample:

```json
{
  "indices": ["prod-enriched-jobs"],
  "query": {
    "size": 500,
    "from": 0,
    "sort": [{"documentId": "asc"}],
    "query": {"term": {"sourceType": "jobs"}}
  }
}
```

**`exists` does not mean "has content".** On text fields, a document whose value is an empty
string still matches `exists`, so coverage can read 100% while the field is blank. And a
misspelled or non-existent field simply returns 0% rather than an error — check the field name
against `GET /v1/catalog/opensearch` before concluding the data is missing. For "does this
document actually have prose", match common words instead:

```json
{"match": {"extractedText": {"query": "the and with", "operator": "or"}}}
```

**Measure depth, not just presence.** Coverage and usefulness are different questions: some
documents carry a very short `extractedText` (little more than the job title), which counts as
present. Pair any coverage figure with a length or element-count check on a sample.

**`runtime_mappings` and script queries are rejected.** Compute derived values client-side.

**`total` caps at 10,000.** Set `"track_total_hits": true`, or derive denominators by summing
aggregation buckets — bucket counts stay exact even when `total` saturates.
