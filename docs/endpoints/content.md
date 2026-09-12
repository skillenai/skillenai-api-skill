# Content Endpoints

The Skillenai blog is served by content endpoints under `skillenai.com/api/backend/content`. The same `X-API-Key` that authenticates the data products API also authenticates these endpoints, so users can author and manage posts from inside Claude (via `/skillenai:blog`) without a separate login round-trip.

## Host

```
https://skillenai.com/api/backend
```

The data products API lives on `api.skillenai.com`; content CRUD lives on `skillenai.com/api/backend`. Same key, two hosts.

## Authentication

```
X-API-Key: $API_KEY
```

All mutation endpoints additionally accept `Authorization: Bearer <JWT>` (browser flow). Agents should default to `X-API-Key`.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/content/posts` | optional | List posts. Anonymous → published only. Authenticated → drafts visible when `mine=true` or `author_id=<self>`. |
| GET | `/content/posts/{slug}` | optional | Read one post. Drafts visible to owner / moderator only. |
| POST | `/content/posts` | required | Create a draft (or `pending_review` for first-time / pending authors). |
| PATCH | `/content/posts/{post_id}` | required (owner) | Partial update. |
| POST | `/content/posts/{post_id}/publish` | required (owner) | Publish a draft. |
| POST | `/content/posts/{post_id}/unpublish` | required (owner) | Move a published post back to draft. |
| DELETE | `/content/posts/{post_id}` | required (owner) | Soft delete. |
| POST | `/content/preview` | required | Render markdown to sanitised HTML without persisting. |
| POST | `/content/uploads/presign` | required | Request a one-shot presigned PUT URL for a cover image. |
| GET | `/content/categories` | none | Category tree. |
| GET | `/content/terms?taxonomy=tag` | none | Tag suggestions / typeahead. |
| GET | `/content/users/by-username/{username}` | none | Public author profile lookup. |

## Authoring lifecycle

Three statuses you'll see in `PostDetail.status`:

- `draft` — visible only to the author and moderators. Approved authors land here after `POST /content/posts`.
- `pending_review` — visible only to the author and moderators; sits in the moderation queue. First-time authors and pending-status authors land here automatically. Cannot be self-published; a moderator must `approve` or `reject`.
- `published` — public.

A fourth status `archived` exists for legacy WP imports but is not reachable via this skill.

## GET /content/posts

```
GET /content/posts?mine=true&status=draft&limit=20&offset=0
GET /content/posts?author_id=<uuid>&status=published
GET /content/posts?q=tiptap&category=engineering&tag=react
```

Query parameters:

| Param | Type | Notes |
|---|---|---|
| `mine` | bool | Scope to authenticated caller's posts. Requires X-API-Key or JWT; 401 otherwise. |
| `author_id` | UUID | Filter by author. |
| `status` | str | One of `draft`, `pending_review`, `published`, `archived`. Anonymous callers always get `published`. |
| `tag` | str | Tag slug filter. |
| `category` | str | Category slug filter. |
| `q` | str | Full-text search over title, excerpt and body. English-stemmed (`hiring` matches `hire`) and weighted so title matches rank above body-only matches. Supports quoted phrases (`"peer review"`) and `-negation`. When set, results are ordered by relevance instead of date. |
| `limit` | int | 1–100 (default 20). |
| `offset` | int | ≥ 0. |

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "slug": "post-slug",
      "title": "Post title",
      "status": "draft",
      "cover_image_url": "https://media.skillenai.com/...",
      "excerpt": "Short summary",
      "author": {"id": "...", "name": "...", "username": "..."},
      "category": {"slug": "engineering", "name": "Engineering", "taxonomy": "category"},
      "tags": [{"slug": "tiptap", "name": "tiptap", "taxonomy": "tag"}],
      "published_at": null,
      "created_at": "2026-04-29T20:00:00Z",
      "updated_at": "2026-04-29T20:00:00Z"
    }
  ],
  "total": 7,
  "limit": 20,
  "offset": 0
}
```

## POST /content/posts

```json
{
  "title": "Why Tiptap beat my Lexical fork",
  "body_md": "## TL;DR\n\nAfter three weeks…",
  "excerpt": "Notes on shipping a WYSIWYG editor without owning the schema.",
  "category": "engineering",
  "tags": ["tiptap", "react", "editors"],
  "cover_image_url": "https://media.skillenai.com/uploads/<user>/<file>.jpg"
}
```

Required: `title`, `category`. `body_md` defaults to empty if omitted (pointless but allowed).

Response: 201 with the full `PostDetail` (markdown body, rendered HTML, full author + taxonomy expansion). The `status` field tells the agent how to set expectations:

- `draft` — the user can publish at any time.
- `pending_review` — the user must wait for moderator approval. They will receive an email when the post is approved or rejected.

Errors:

- 401 — no/invalid API key or JWT.
- 403 — the user's account is `rejected`. Re-onboarding is a manual support flow.
- 409 — `pending` author already has 1 active draft, OR approved author has hit the daily cap (3 per 24h).
- 400 — missing `category`, or invalid field shape.

## PATCH /content/posts/{post_id}

`{post_id}` is the UUID, not the slug. Pass any subset of fields you want to change:

```json
{
  "title": "New title",
  "body_md": "## New body",
  "excerpt": null,
  "tags": ["tiptap", "react"],
  "cover_image_url": null,
  "category": "engineering"
}
```

Patch semantics: only fields you include are touched. `excerpt: null` clears the excerpt. `tags: []` clears all tags. `category: ""` is a 400 (must be a real category).

`published_at` can be set to a past timestamp to back-date a published post, or to a future timestamp to schedule. Setting `null` on a `published` post unpublishes it back to `draft`.

`author_id` is moderator-only — owners cannot reassign their own posts.

## POST /content/posts/{post_id}/publish

No body. Owners self-publish their drafts; moderators can publish anyone's. `pending_review` posts cannot be self-published — the API returns 403 with the message "This post is awaiting moderator review and cannot be self-published." Surface that verbatim.

If `published_at` is null, it's set to `now()`. If it was already set (back-dated draft), the existing value is preserved.

## POST /content/posts/{post_id}/unpublish

No body. Returns the post to `draft` status. Useful when the user spots a typo after publishing and wants to keep editing privately. The post URL stops resolving on the public blog until republished.

## DELETE /content/posts/{post_id}

204 on success. Soft-delete only — the row stays for audit. The post disappears from public listings, the dashboard, and the API.

## POST /content/uploads/presign

Used by the `blog_upload.py` helper.

```json
{
  "filename": "cover.jpg",
  "content_type": "image/jpeg",
  "content_length": 184320
}
```

Response:

```json
{
  "upload_url": "https://s3.amazonaws.com/...?X-Amz-Signature=...",
  "public_url": "https://media.skillenai.com/uploads/<user>/<file>.jpg",
  "expires_in": 600
}
```

Limits: 10 MB max payload, 10-minute presign window. Allowed extensions: jpg/jpeg/png/webp/gif. The presigned URL is single-use — re-request if you need to retry.

## POST /content/preview

```json
{"body_md": "## Hello\n\n**bold** and a [link](https://example.com)"}
```

Response:

```json
{"body_html": "<h2>Hello</h2>\n<p><strong>bold</strong> and a <a href=\"https://example.com\" rel=\"nofollow\">link</a></p>"}
```

Same renderer as the publish path — bleach-sanitised, raw HTML stripped, `javascript:` URLs blocked.

## GET /content/categories

```json
{
  "items": [
    {
      "id": "uuid",
      "slug": "engineering",
      "name": "Engineering",
      "parent_id": null,
      "children": [
        {"id": "...", "slug": "frontend", "name": "Frontend", "parent_id": "...", "children": []}
      ]
    }
  ]
}
```

Use this to surface a picker before drafting; new categories require moderator approval and are not creatable from this skill.

## GET /content/terms?taxonomy=tag

```
GET /content/terms?taxonomy=tag&q=react&limit=10
GET /content/terms?taxonomy=tag&limit=20            # most-used tags first
```

Response:

```json
{
  "items": [
    {"slug": "react", "name": "React", "taxonomy": "tag"},
    {"slug": "react-native", "name": "React Native", "taxonomy": "tag"}
  ]
}
```

`taxonomy` must be `category` or `tag`. Empty `q` returns the most-used terms first; non-empty `q` is a case-insensitive substring match on name + slug.

## GET /content/users/by-username/{username}

Public author profile lookup, used by `/u/<username>` pages on the blog.

```json
{"id": "uuid", "name": "Author Name", "email": null, "username": "author-name"}
```

Email is omitted for unauthenticated callers.

## Status codes

| Code | Meaning |
|------|---------|
| 200 | Success (read or update). |
| 201 | Post created. |
| 204 | Post deleted. |
| 400 | Missing required field, invalid status, or empty category. |
| 401 | Missing / invalid auth. |
| 403 | Rejected author, non-owner mutation, or self-publish of pending_review. |
| 404 | Post not found, or not visible to the caller. |
| 409 | Pending-author 1-active-draft cap, or approved-author daily cap. |
| 422 | Pydantic validation error (field shape). |
