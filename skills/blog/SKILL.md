---
name: blog
description: Author and manage Skillenai blog posts from inside Claude — draft, edit, upload cover images, publish, and list your own posts using your existing Skillenai API key
user-invocable: true
argument-hint: [list|get|create|update|publish|unpublish|delete|upload-cover|categories|tags] <details>
allowed-tools: Bash, Read, Write, Glob, Grep, WebFetch, Agent
---

# Skillenai Blog Authoring Skill

Invoke as `/skillenai:blog` (when installed via `/plugin install skillenai`).

This skill lets the user write, edit, and publish posts on the Skillenai blog through Claude. Drafting, image generation, web research, and citation flow naturally inside the conversation, and the skill posts the result back through the same Skillenai API key already authorised for the data products API.

The dashboard editor at `skillenai.com/dashboard/posts` remains the canonical UI for human authors. This skill is a parallel surface for users who prefer to author through Claude.

## Host

The content endpoints live at:

```
https://skillenai.com/api/backend
```

Same host as the alerts surface. Calls go through the shared wrapper with `--host app`. The same `X-API-Key` authenticates both this host and `api.skillenai.com` — one key, two hosts.

## Credentials

Every API call goes through the shared wrapper at `${CLAUDE_PLUGIN_ROOT}/scripts/api.py`, which loads the API key in its own process. **The key is never visible to the agent's shell, never in `curl` argv, and never in the conversation transcript.** See [Security](../../README.md#security) in the API skill for the full hard-rule list.

### First-run check

Before running any flow, verify credentials exist:

```bash
[ -n "$API_KEY" ] || [ -f ~/.skillenai/.env ]
```

If neither is set, **stop and tell the user**:

> No Skillenai API key found. Run `/skillenai:api setup` to authorize — it'll open a browser, you sign in or create an account, and the key gets saved automatically.

## Authoring lifecycle

The blog uses a moderation flow the agent should be aware of so it can set expectations correctly:

- **First-time authors** (no prior posts) — first submission is auto-promoted to `pending_review` and forwarded to the moderation queue. The user gets an email when it's approved or rejected. The agent cannot bypass this; ship the draft, tell the user it's queued, and stop.
- **Approved authors** — drafts land in `draft`, the agent can `publish` directly. Daily cap: 3 newly created posts per 24h.
- **Pending authors** (one rejected/queued draft) — at most 1 active draft at a time. The user must edit or delete the existing one before submitting another.
- **Rejected authors** — 403 on every mutation. Surface the error verbatim; re-onboarding is a manual support flow.

## `$ARGUMENTS` parsing

Parse user intent from `$ARGUMENTS`:

- `list` / `mine` / "show my drafts" → Flow 1 (list)
- `get <slug>` / "open my post on …" → Flow 2 (get)
- `create` / `draft` / "write a post about …" → Flow 3 (create)
- `update <slug-or-id>` / "edit …" → Flow 4 (update)
- `publish <slug-or-id>` → Flow 5 (publish)
- `unpublish <slug-or-id>` → Flow 5 (unpublish)
- `delete <slug-or-id>` → Flow 6 (delete)
- `upload-cover <local-path-or-url>` → Flow 7 (cover image)
- `categories` → Flow 8 (list categories)
- `tags <q>` → Flow 8 (tag suggestions)
- If unclear, ask the user what they want to do.

---

## Flow 1: List my posts (`list`)

```bash
WRAP="${CLAUDE_PLUGIN_ROOT}/scripts/api.py"

# All posts I've authored, any status (drafts + published + pending_review)
python "$WRAP" GET "/content/posts?mine=true&limit=50" --host app | python3 -m json.tool

# Filter by status
python "$WRAP" GET "/content/posts?mine=true&status=draft" --host app
python "$WRAP" GET "/content/posts?mine=true&status=pending_review" --host app
python "$WRAP" GET "/content/posts?mine=true&status=published" --host app

# Full-text search (title, excerpt and body; most relevant first)
python "$WRAP" GET "/content/posts?mine=true&q=tiptap" --host app
```

`mine=true` scopes the listing to the authenticated caller's posts. Without it, the endpoint returns published posts (anonymous view).

---

## Flow 2: Read one post (`get`)

```bash
python "$WRAP" GET "/content/posts/<slug>" --host app | python3 -m json.tool
```

Returns the full `PostDetail` (markdown body, rendered HTML, category, tags, author). Drafts are visible to the owner only — the API will return 404 if the slug doesn't belong to the caller and isn't published.

---

## Flow 3: Create a post (`create`)

The standard flow is **draft conversationally → save as draft → publish**. The user usually wants Claude to do the drafting, not just to mechanically POST a payload.

### Step 1 — Draft conversationally

If the user asks for a post on a topic, draft it inside the conversation first. Things to do well:

- **Cite real data** when the post is about labor market trends — pull numbers via `/skillenai:api` (`skills-by-role`, `topic-trends`, `jobs/search`) and quote them inline. Don't invent statistics.
- **Use markdown** — H2/H3 headings, lists, blockquotes, fenced code blocks. The blog renderer is GFM-compatible and bleach-sanitised; HTML embeds may not survive.
- **Pick a tight title** (≤100 chars works best for previews) and write a 1–2 sentence excerpt the user can edit.
- **Pick a category and tags** from the existing taxonomy (Flow 8). Don't invent categories — the moderator-only `POST /content/categories` route is not callable from this skill.
- **Cover image** is optional but improves the post card. Use Flow 7 if the user has an idea or asks for one.

### Step 2 — Confirm with the user before posting

Show the user the drafted markdown, the title, the proposed category and tags, and ask "ship it as a draft?" Don't auto-create — first-time authors burn their one pending-review slot, and the moderation queue is human-staffed.

### Step 3 — POST /content/posts

```bash
python "$WRAP" POST /content/posts --host app \
  '{
    "title": "Why Tiptap beat my Lexical fork",
    "body_md": "## TL;DR\n\nAfter three weeks…",
    "excerpt": "Notes on shipping a WYSIWYG editor without owning the schema.",
    "category": "engineering",
    "tags": ["tiptap", "react", "editors"],
    "cover_image_url": "https://media.skillenai.com/uploads/<user>/<file>.jpg"
  }' | python3 -m json.tool
```

201 returns the full `PostDetail`. `status` will be one of:

- `draft` — approved authors. Tell the user the URL is `skillenai.com/dashboard/posts/<slug>/edit`. They publish from there or via Flow 5.
- `pending_review` — first-time or pending authors. Tell the user the post is in the moderation queue and they'll get an email when it's approved.

If the response is 409, the user already has a pending draft (1-active-draft cap) — surface the message and stop.

### Required fields

| Field | Required | Notes |
|---|---|---|
| `title` | yes | 1–300 chars |
| `category` | yes | Must be a known category slug or display name; auto-resolved |
| `body_md` | no, but pointless without it | Markdown source |
| `excerpt` | no | ≤1000 chars; falls back to a derived snippet |
| `tags` | no | Array of names; missing tags are auto-created (lowercase, slugified) |
| `cover_image_url` | no | Public S3 URL from Flow 7 |

---

## Flow 4: Update a post (`update`)

Only the post's owner (or a moderator) can update it.

```bash
python "$WRAP" PATCH "/content/posts/<post-id>" --host app \
  '{
    "title": "New title",
    "body_md": "## New body",
    "tags": ["tiptap", "react"]
  }' | python3 -m json.tool
```

Patch semantics: only fields you pass are touched. Pass `tags: []` to clear all tags. Pass `excerpt: null` to clear the excerpt. To change the category, pass `category: "<new>"` — empty string would 400.

The post id (UUID) is in the response from Flow 1 / Flow 3 as `id`. The slug is also acceptable on the GET path but **not** on PATCH — PATCH takes the UUID.

---

## Flow 5: Publish / unpublish (`publish` / `unpublish`)

Approved authors self-publish their drafts. `pending_review` posts must clear moderation first — non-mods cannot self-publish them (the API returns 403 with a "awaiting moderator review" message).

```bash
python "$WRAP" POST "/content/posts/<post-id>/publish" --host app | python3 -m json.tool

python "$WRAP" POST "/content/posts/<post-id>/unpublish" --host app | python3 -m json.tool
```

`publish` sets `published_at = now()` if not already set. `unpublish` returns the post to `draft` status — useful when the user spots a typo after publishing and wants to keep editing privately.

---

## Flow 6: Delete (`delete`)

Soft-delete only. The post is gone from public listings and the dashboard, but the row stays for audit. Owners and moderators can delete; deletion of a `pending_review` post counts toward your daily cap (the audit trail prevents bypass-by-recreate).

```bash
python "$WRAP" DELETE "/content/posts/<post-id>" --host app
```

204 on success.

---

## Flow 7: Upload a cover image (`upload-cover`)

Cover images live in a public S3 bucket fronted by `media.skillenai.com`. The flow is **presign → PUT bytes → use the returned public URL** in `cover_image_url` on create/update. The helper script handles all three steps — pass it a local file path or an HTTPS URL it should fetch first.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/blog_upload.py" /local/path/cover.jpg
# → prints the public URL on success

python "${CLAUDE_PLUGIN_ROOT}/scripts/blog_upload.py" "https://example.com/photo.jpg"
# → fetches the URL, PUTs the bytes, prints the public URL
```

The helper:

- Caps payload at 10 MB (the backend rejects larger).
- Infers `content_type` from the file extension (jpg/jpeg/png/webp/gif).
- Stays inside the wrapper-script process — the API key never enters the agent's shell.
- Prints **only** the public URL on success; pipe directly into `cover_image_url`.

If the user wants Claude to *generate* a cover image (DALL-E, Imagen, etc.), use whatever image-generation tool the host environment provides, save the bytes to a tempfile, then run the helper on the tempfile. Don't try to upload bytes from inside the conversation — the agent shell can't write binary cleanly.

---

## Flow 8: Categories and tag suggestions (`categories` / `tags`)

```bash
# All categories as a parent/children tree
python "$WRAP" GET /content/categories --host app | python3 -m json.tool

# Tag suggestions (fuzzy match)
python "$WRAP" GET "/content/terms?taxonomy=tag&q=react" --host app

# Most-used tags (no q)
python "$WRAP" GET "/content/terms?taxonomy=tag&limit=20" --host app
```

Use these BEFORE drafting to ground the post in the existing taxonomy. Inventing a brand-new category slug is silently allowed (it gets auto-created in `pending_review` flow), but the moderator may merge or rename it later — matching an existing category is friendlier.

---

## Preview rendering (optional)

If the user wants to see what their markdown will render to **before** saving:

```bash
python "$WRAP" POST /content/preview --host app \
  '{"body_md": "## Hello\n\n**bold** and a [link](https://example.com)"}' | python3 -m json.tool
```

Returns sanitised HTML. The same renderer runs at publish time, so what you see is what gets stored.

---

## End-to-end example

User: "Draft a post about why teams underestimate the cost of switching ORMs, with a citation from the latest Skillenai data on SQL skill demand."

1. Run `/skillenai:api skills` for the data point: `python "$WRAP" GET "/v1/analytics/skills-by-role?role=Software+Engineer"` — pull the SQL count.
2. Draft 600–900 words of markdown; cite the SQL number; pick `category: "engineering"`, tags `["databases", "tech-debt"]`.
3. Show the user the title, excerpt, and full draft. Ask "ship as a draft?"
4. On confirm, optionally upload a cover image with Flow 7.
5. POST to `/content/posts`.
6. Report:
   - The post's `slug` and dashboard URL: `https://skillenai.com/dashboard/posts/<slug>/edit`
   - If `status == draft`: tell the user they can publish via Flow 5 or the dashboard.
   - If `status == pending_review`: tell the user it's in the moderation queue and they'll get an email when it lands.

---

## Important notes

1. **The blog is public.** Posts published via this skill appear at `https://skillenai.com/blog/<slug>` for everyone, including search engines. Treat draft → publish as a real publication step.
2. **Author identity.** Posts are attributed to the API key's owner. The byline links to `https://skillenai.com/u/<username>` — that page is generated from the user's display name on first publish, so the user may want to set a clean `name` in their account settings before their first post goes live.
3. **Rate limits.** Approved authors: 3 posts per 24h. Pending: 1 active draft. Both gates raise 409 with a clear message — surface verbatim.
4. **Content sanitisation.** The renderer strips raw HTML tags and `javascript:` URLs. Stick to CommonMark + GFM (tables, fenced code, autolinks). Embeds (Twitter, YouTube) require shortcodes — not currently supported via this skill.
