# Skillenai API Skill

A Claude Code skill and Python toolkit for the [Skillenai Data Products API](https://api.skillenai.com) — labor market intelligence covering skills, jobs, trends, and entity analytics across the AI/ML ecosystem.

## What Skillenai Provides

Skillenai indexes and enriches content from across the AI/ML landscape — job postings, blog posts, research papers, news, and social media — then extracts entities, resolves them to a canonical knowledge graph, and exposes the results through a structured API.

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

If you're using the Claude Code plugin, you don't need to copy a key by hand — run `/skillenai:api setup` and authorize in the browser. The Credentials section below explains both paths.

## Use with Claude Code

This repo is packaged as a Claude Code plugin. The recommended install is through the plugin marketplace:

```
/plugin marketplace add skillenai/skillenai-api-skill
```

```
/plugin install skillenai@skillenai-api
```

Run these as two separate commands — Claude Code slash commands are one per line. Restart Claude Code (or `/reload-plugins`) after installing. The skill registers as **`/skillenai:api`** — note the colon, which is standard namespacing for plugin skills.

### Credentials

The recommended path is the in-skill OAuth flow:

```
/skillenai:api setup
```

This opens `app.skillenai.com/activate` in your browser, you sign in (or sign up) and click **Allow**, and the issued key is written to `~/.skillenai/.env` with mode 0600. The key is never printed to the terminal or to the conversation transcript — the agent only sees a `✓ Authorized` confirmation.

Behind the scenes the skill calls the API through `scripts/api.py`, which loads `~/.skillenai/.env` in its own process and signs requests with `X-API-Key`. The agent's shell never sees the key, so it can't be accidentally echoed, logged, or shown in `ps`.

If you'd rather set it up by hand (e.g. you already have a key from the dashboard, or you're scripting against the API outside the plugin):

```bash
mkdir -p ~/.skillenai
printf 'API_KEY=skn_live_your_key_here\n' > ~/.skillenai/.env
chmod 600 ~/.skillenai/.env
```

Or export it in your shell profile: `export API_KEY=skn_live_...`. Credentials resolve with the same precedence in every entry point: shell env → `~/.skillenai/.env` → plugin-local `.env` → cwd `.env`.

Get an API key by registering at [app.skillenai.com](https://app.skillenai.com).

### Asking questions

Once installed, try things like:
- "What skills do Data Scientists need?"
- "Search for remote ML engineer jobs paying over $200k"
- "Show me topic trends for the last 6 months"
- "Run a full EDA report"

### Alternative: clone directly as a directory skill

If you prefer not to use the plugin system, you can register the skill manually by cloning the inner `skills/api/` directory into a skills folder Claude Code scans:

```bash
git clone https://github.com/skillenai/skillenai-api-skill.git
ln -s "$(pwd)/skillenai-api-skill/skills/api" ~/.claude/skills/skillenai-api
```

Invocation in that layout is `/skillenai-api` (bare — directory skills aren't namespaced). The same `~/.skillenai/.env` credentials work.

### Alternative: use the repo standalone

Everything in this repo — [skills/api/SKILL.md](skills/api/SKILL.md), [scripts/](scripts/), and [docs/](docs/) — is just files. Clone it anywhere and point your own agent at the SKILL.md or run the Python scripts directly with `python scripts/eda_report.py` etc.

## Helper Scripts

Python scripts in `scripts/` provide CLI access to common workflows. Install dependencies:

```bash
pip install requests python-dotenv
```

| Script | Purpose |
|--------|---------|
| `scripts/oauth_setup.py` | Browser-based OAuth setup that writes `~/.skillenai/.env` |
| `scripts/api.py` | Authenticated request wrapper used by the skill (keeps the key out of the agent's shell) |
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
