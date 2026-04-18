# SkillenAI API Skill

A Claude Code skill and Python toolkit for the [SkillenAI Data Products API](https://api.skillenai.com) — labor market intelligence covering skills, jobs, trends, and entity analytics across the AI/ML ecosystem.

## What SkillenAI Provides

SkillenAI indexes and enriches content from across the AI/ML landscape — job postings, blog posts, research papers, news, and social media — then extracts entities, resolves them to a canonical knowledge graph, and exposes the results through a structured API.

## Key Capabilities

- **Job Search** — Multi-signal ranking combining text relevance, skill matching, seniority, salary, location, and recency
- **Skill Analysis** — What skills does a given role require? Compare skill profiles across roles with fuzzy role name resolution
- **Topic Trends** — Track how topics rise and fall in the AI/ML discourse over time
- **Entity Resolution** — Map free-text names ("PyTorch", "pytorch", "Py Torch") to canonical entity IDs
- **Knowledge Graph** — Traverse relationships between jobs, skills, companies, people, and products via Cypher queries

## Quick Start

1. **Register** at [app.skillenai.com](https://app.skillenai.com) and verify your email
2. **Create an API key** in the dashboard — it will look like `skn_live_...`
3. **Make your first request:**

```bash
curl -s -H "X-API-Key: skn_live_your_key_here" \
  "https://api.skillenai.com/v1/analytics/counts" | python3 -m json.tool
```

See [docs/getting-started.md](docs/getting-started.md) for the full walkthrough.

## Use with Claude Code

Add this repo as a Claude Code skill so your agent can query the SkillenAI API directly.

### Option 1: Install as a project skill

```bash
# Clone the repo
git clone https://github.com/chiefastro/skillenai-api-skill.git

# Add your credentials
cp skillenai-api-skill/.env.example skillenai-api-skill/.env
# Edit .env with your API key

# In your project's .claude/settings.json, add:
# { "permissions": { "allow": ["skill:skillenai-api-skill/SKILL.md"] } }
```

### Option 2: Reference directly

Point Claude Code at the skill file:

```
/skill path/to/skillenai-api-skill/SKILL.md
```

Then ask things like:
- "What skills do Data Scientists need?"
- "Search for remote ML engineer jobs paying over $200k"
- "Show me topic trends for the last 6 months"
- "Run a full EDA report"

## Helper Scripts

Python scripts in `scripts/` provide CLI access to common workflows. Install dependencies:

```bash
pip install requests python-dotenv
```

| Script | Purpose |
|--------|---------|
| `scripts/eda_report.py` | Generate a comprehensive EDA markdown report |
| `scripts/skill_analysis.py` | Analyze skill demand by role, compare roles |
| `scripts/trend_analysis.py` | Topic trend time series and growth analysis |
| `scripts/job_search.py` | Multi-signal job search with formatted output |

Run any script with `--help` for usage.

## Documentation

- [Getting Started](docs/getting-started.md) — Register, get a key, make your first request
- [Authentication](docs/authentication.md) — API key format, headers, .env setup
- [Rate Limits](docs/rate-limits.md) — Tiers, credits, billing headers, error codes
- **Endpoints:**
  - [Analytics](docs/endpoints/analytics.md) — Counts, topic trends, entity co-occurrence, skills by role
  - [Jobs](docs/endpoints/jobs.md) — Multi-signal job search
  - [Resolution](docs/endpoints/resolution.md) — Entity resolution (free-text to canonical)
  - [Catalog](docs/endpoints/catalog.md) — Schema introspection
  - [Query](docs/endpoints/query.md) — SQL, OpenSearch DSL, Cypher graph queries
