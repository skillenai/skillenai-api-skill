# Getting Started with the Skillenai API

This guide walks you through registering for the API, creating a key, and making your first request.

## Step 1: Register

Go to [app.skillenai.com](https://app.skillenai.com) and create an account. You'll need to provide an email address and set a password.

## Step 2: Verify Your Email

Check your inbox for a verification email from Skillenai. Click the verification link to activate your account. If you don't see it, check your spam folder.

## Step 3: Create an API Key

Once logged in:

1. Navigate to the **API Keys** section in your dashboard
2. Click **Create New Key**
3. Give the key a descriptive name (e.g., "dev-machine" or "claude-agent")
4. Copy the key immediately — it will look like `skn_live_...` and is only shown once

## Step 4: Set Up Your Environment

Create a `.env` file in your project root:

```bash
cp .env.example .env
```

Edit `.env` and paste your API key:

```
API_URL=https://api.skillenai.com
APP_URL=https://app.skillenai.com/api/backend
API_KEY=skn_live_your_actual_key_here
```

`API_URL` is the data products API (search, analytics, SQL). `APP_URL` is the account surface (alerts CRUD). The same `API_KEY` authenticates both.

## Step 5: Make Your First Request

### Using curl

```bash
source .env
curl -s -H "X-API-Key: $API_KEY" "$API_URL/v1/analytics/counts" | python3 -m json.tool
```

This returns document counts across all source types (jobs, blog posts, scholarly papers, etc.).

### Using Python

```python
import os
import requests
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("API_URL")
key = os.getenv("API_KEY")

resp = requests.get(
    f"{url}/v1/analytics/counts",
    headers={"X-API-Key": key},
)
print(resp.json())
```

### Expected Response

```json
{
  "total": 125000,
  "buckets": [
    {"source_type": "jobs", "count": 45000},
    {"source_type": "blog", "count": 32000},
    {"source_type": "scholarly", "count": 28000},
    {"source_type": "news", "count": 12000},
    {"source_type": "social", "count": 8000}
  ]
}
```

## Step 6: Explore

Now that you're connected, try these next:

- **Check what topics exist:** `GET /v1/analytics/topic-trends?limit=10`
- **See the schema:** `GET /v1/catalog`
- **Search for jobs:** `POST /v1/jobs/search` with `{"query": "machine learning engineer", "size": 5}`
- **Run a full EDA:** `python scripts/eda_report.py`

See the [endpoint documentation](endpoints/) for full details on each API call.

## Topics vs Skills

One distinction that trips people up:

- **Topics** are coarse taxonomy tags (~33 categories like `machine-learning`, `nlp`, `agents`) attached to documents. Good for broad filtering and trend analysis.
- **Skills** are fine-grained entities extracted via NER (e.g., "PyTorch", "SQL", "Kubernetes") and linked to jobs in the knowledge graph. Use the `/v1/analytics/skills-by-role` endpoint for skill analysis.

When asking "What skills does a Data Scientist need?" — use the skills-by-role endpoint, not topic aggregation.
