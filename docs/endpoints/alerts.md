# Alerts Endpoints

User alert subscriptions run scheduled Data Products API queries and email the results. Alerts CRUD is served from `app.skillenai.com/api/backend`, not `api.skillenai.com`. The same `X-API-Key` authenticates both hosts, and the scheduled query runs with the caller's key so billing and endpoint authorization behave the same as direct API calls.

## Host

```
https://app.skillenai.com/api/backend
```

The data products API lives on `api.skillenai.com`; alerts CRUD lives on `app.skillenai.com/api/backend`. Do not confuse the two hosts.

## Authentication

Pass the same API key you would use for `api.skillenai.com`:

```
X-API-Key: $API_KEY
```

All alerts endpoints also accept `Authorization: Bearer <JWT>` (browser flow). Agents should default to `X-API-Key` — no login round-trip required.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/alerts/preview` | Run the query synchronously and render the would-be email without saving a row or sending email. |
| POST | `/alerts` | Create a scheduled alert. |
| GET | `/alerts` | List the caller's alerts. |
| GET | `/alerts/{id}` | Read one alert, including `last_run_at` / `last_error`. |
| PATCH | `/alerts/{id}` | Partial update (name, schedule, query, delivery, active flag). |
| DELETE | `/alerts/{id}` | Delete an alert. |
| POST | `/alerts/{id}/run` | Trigger an ad-hoc run now (advances `next_run_at`). |

## Source query

`source_query` is **any** Data Products API endpoint the caller's key can hit. The scheduled run authorises and bills against that key exactly like a direct call would.

```json
{
  "endpoint": "/v1/jobs/search",
  "payload": {
    "query": "machine learning engineer",
    "seniority": "senior",
    "min_salary": 180000,
    "size": 20
  }
}
```

Good choices: `/v1/jobs/search`, `/v1/query/search`, `/v1/analytics/skills-by-role`, `/v1/query/sql`. Anything returning a list of items the caller wants delivered as email.

## Delivery config

Only `email` is supported today:

```json
{
  "channel": "email",
  "target": "alerts@example.com"
}
```

## POST /alerts/preview

Iterate on a query with no side effects — no row, no email, no schedule. Preview **does** count against credits because it runs the query.

### Request

```json
{
  "name": "Senior ML jobs",
  "source_query": {
    "endpoint": "/v1/jobs/search",
    "payload": {"query": "machine learning engineer", "seniority": "senior", "size": 10}
  },
  "delivery_config": {"channel": "email", "target": "alerts@example.com"}
}
```

### Response (200)

```json
{
  "item_count": 10,
  "items": [{"id": "...", "title": "..."}],
  "rendered": {
    "subject": "Skillenai alert: 10 new results for Senior ML jobs",
    "html": "<!DOCTYPE html>...",
    "text": "Senior ML jobs...",
    "unsubscribe_url": "...",
    "manage_url": "..."
  },
  "credits": {
    "used": 17,
    "remaining": 983,
    "formula": "3 + ceil(2700ms / 100) = 17"
  }
}
```

Key fields:

- `item_count` — rows the query returned (drives the subject line).
- `rendered.subject` — the exact subject the recipient will see. Give the alert a `name` so the subject reads "Skillenai alert: N new results for <name>".
- `credits.used` — credits this single query consumed. **Multiply by cadence to estimate monthly burn** — e.g. a query that costs 17 credits running hourly burns `17 × 24 × 30 ≈ 12,240` credits per month.
- `credits.remaining` — balance after the preview.
- `credits.formula` — human-readable cost breakdown (fixed + latency components).

## POST /alerts

Finalize the alert. The response is an `AlertInfo` row.

### Request

```json
{
  "name": "Senior ML jobs",
  "source_query": {
    "endpoint": "/v1/jobs/search",
    "payload": {"query": "machine learning engineer", "seniority": "senior", "size": 10}
  },
  "delivery_config": {"channel": "email", "target": "alerts@example.com"},
  "schedule_cadence_seconds": 86400,
  "is_active": true
}
```

- `name` — required for a readable email subject. Empty `name` works but the subject will fall back to the raw query.
- `schedule_cadence_seconds` — interval between runs. Minimum is tier-gated.
- `is_active` — defaults to `true`. Set `false` to pause.

### Response (201)

Same `AlertInfo` schema used by `GET /alerts/{id}` — includes `id`, `name`, `source_query`, `delivery_config`, `schedule_cadence_seconds`, `next_run_at`, `last_run_at`, `last_error`, `created_at`, `updated_at`.

## POST /alerts/{id}/run

Trigger an ad-hoc run. Returns immediately — the query runs asynchronously. Use this to smoke-test delivery without waiting for the schedule.

- Response is the updated `AlertInfo` with `next_run_at` advanced to `now + schedule_cadence_seconds`.
- `last_run_at` updates once the run finishes (typically 30–60s). Poll `GET /alerts/{id}` to see when it advances.
- Returns `502` if the alert is already running — retry in a few seconds.
- Returns `400` if `is_active` is `false`.

## GET /alerts, GET /alerts/{id}, PATCH /alerts/{id}, DELETE /alerts/{id}

Standard CRUD on the caller's alerts. PATCH accepts any subset of the create fields. DELETE returns `204`.

## Error shape

All errors follow the standard envelope:

```json
{"error": {"code": "unauthorized", "message": "...", "details": null}}
```

Common cases:

- `401 unauthorized` — missing or invalid API key.
- `403 forbidden` — the caller has no active API key. Message: *"An active API key is required before creating alerts."*
- `400 bad_request` — schema violations, unsupported delivery channel.
- `502 bad_gateway` — upstream data API query failed (preview) or the alert is currently running (run).
