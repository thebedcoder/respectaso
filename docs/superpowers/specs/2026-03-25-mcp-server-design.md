# MCP Server for RespectASO — Design Spec

**Date:** 2026-03-25
**Status:** Approved

---

## Overview

Add a Model Context Protocol (MCP) server directly into the existing RespectASO Django application. This allows other locally-hosted projects to provide RespectASO as a tool to LLM API calls (OpenAI, Claude, etc.), enabling AI agents to perform ASO keyword research, access scores and history, and manage apps/keywords programmatically.

Both RespectASO and the consuming project run in Docker locally and communicate over a shared Docker network.

---

## Architecture

The MCP layer is built into the `aso` Django app. No new containers or process supervisors are needed.

### New files

| File | Purpose |
|------|---------|
| `aso/mcp_server.py` | MCP server instance + all 12 tool definitions, calls Django ORM and `services.py` directly |
| `aso/mcp_views.py` | Thin Django view (`@csrf_exempt`) that wraps the MCP SDK's async `handle_request` coroutine using `asgiref.sync.async_to_sync`, so it can be called from Gunicorn's synchronous worker |

### Modified files

| File | Change |
|------|--------|
| `core/urls.py` | Add `path("mcp/", mcp_views.mcp_handler)` directly (no method restriction — the MCP SDK dispatches GET and POST internally) |
| `requirements.txt` | Add `mcp[cli]` — pin to tested minor version, e.g. `mcp[cli]>=1.6,<2.0` |
| `docker-compose.yml` | Add `container_name: respectaso-web`; add named attachable network `respectaso_net`; keep existing port 80 mapping |

### Dependency

```
mcp[cli]>=1.6,<2.0
```

Pin to the minor version tested. The `mcp` SDK is the official Python MCP implementation. It handles JSON-RPC framing, SSE transport, tool schema generation, and protocol negotiation.

---

## Transport

- **Protocol:** MCP over Streamable HTTP (HTTP + SSE)
- **Endpoint:** `/mcp/` — handles both `GET` (manifest/discovery) and `POST` (tool calls). Do **not** apply `@require_POST`.
- **CSRF:** The view must be decorated with `@csrf_exempt` — LLM callers do not send Django CSRF tokens.
- **Port:** Same as the main app — `8080` inside the container, `80` on the host

---

## MCP Tools

### Read tools (no iTunes API calls)

| Tool | Inputs | Returns |
|------|--------|---------|
| `list_apps` | *(none)* | Array of `{id, name, bundle_id, track_id, store_url, icon_url, seller_name}` |
| `list_keywords` | `app_id?` | Array of `{id, keyword, app_id, app_name, results: [{country, popularity_score, difficulty_score, difficulty_label, searched_at}]}` — one entry per keyword, with their latest result per country |
| `get_keyword_scores` | `keyword_id`, `country?` | Latest `{popularity_score, difficulty_score, difficulty_label, targeting_advice: {label, description}, competitors_data, app_rank, searched_at}` |
| `get_keyword_trend` | `keyword_id`, `country?` | Array of `{date, popularity, difficulty, rank, country}` data points |
| `get_search_history` | `app_id?`, `country?`, `page?` | `{results: [{keyword, app_name, country, popularity_score, difficulty_score, difficulty_label, app_rank, searched_at}], page, total_pages, total_count}` — 25 results per page |

**Note on `targeting_advice`:** The `SearchResult.targeting_advice` model property returns a 4-tuple `(icon, label, css_classes, description)`. The MCP tool serializes only `{label, description}` — `icon` and `css_classes` are presentation-layer concerns irrelevant to LLM callers.

### Search tools (trigger iTunes API, rate-limited)

| Tool | Inputs | Returns |
|------|--------|---------|
| `search_keywords` | `keywords` (string, comma-sep, max 20), `countries` (array, max 5), `app_id?` | Results grouped by country with full scores, competitors, download estimates. Worst case: 20 keywords × 5 countries = ~3 min (100 iTunes calls at 2s each). SSE keeps connection alive. |
| `opportunity_search` | `keyword` (string), `app_id?` | 30-country opportunity ranking sorted by opportunity score. Runtime: ~60 seconds (30 iTunes calls at 2s each). SSE keeps connection alive. |
| `refresh_keyword` | `keyword_id`, `country?` | Updated scores for that keyword+country |
| `bulk_refresh_keywords` | `app_id?`, `country?` | If `app_id` is provided: refreshes all keywords for that app. If `app_id` is omitted: refreshes only keywords with **no associated app**. To refresh all keywords across all apps, the caller must call once per app. |

### Management tools

| Tool | Inputs | Returns |
|------|--------|---------|
| `add_app` | `name`, `bundle_id?`, `track_id?`, `store_url?`, `icon_url?`, `seller_name?` | Created `{id, name}`. Note: `track_id` is unique — duplicate `track_id` returns `{"error": "An app with this track_id already exists"}`. |
| `delete_keyword` | `keyword_id` | `{success: true, deleted: "<keyword>"}` |
| `delete_app` | `app_id` | `{success: true, deleted: "<app name>"}` |

---

## Data Flow

```
LLM API call (tools/call JSON-RPC)
  → POST /mcp/ (Django view, @csrf_exempt)
    → async_to_sync(mcp_handler)(request)  ← sync view calls async SDK
      → mcp_server.py tool handler
        → services.py / Django ORM (sync, direct — no HTTP hop)
          → iTunes API (search/refresh tools only)
            → JSON result → SSE stream → LLM caller
```

**Async bridging:** `asgiref.sync.async_to_sync` wraps the MCP SDK's async `handle_request` coroutine so it can be called from the synchronous Django view that Gunicorn dispatches. The sync view calls `async_to_sync(mcp_handler)(request)`. This is the standard Django pattern — no ASGI server switch required.

---

## Docker Networking

**Startup order:** RespectASO must be started first (`docker compose up -d`) to create the `respectaso_net` network. If the consuming project starts first, Docker will error with "network not found" on the `external: true` reference.

RespectASO's `docker-compose.yml`:

```yaml
networks:
  respectaso_net:
    name: respectaso_net
    driver: bridge
    attachable: true

services:
  web:
    container_name: respectaso-web   # explicit — required for DNS resolution
    networks:
      - respectaso_net
    # existing config unchanged
```

The consuming project's `docker-compose.yml`:

```yaml
networks:
  respectaso_net:
    external: true

services:
  your-service:
    networks:
      - respectaso_net
    environment:
      - RESPECTASO_MCP_URL=http://respectaso-web:8080/mcp/
```

The MCP endpoint is reachable at `http://respectaso-web:8080/mcp/` from any container on `respectaso_net`. The hostname `respectaso-web` resolves because `container_name: respectaso-web` is explicitly set.

---

## Error Handling

All errors are returned as structured MCP error responses so the LLM can reason about them — not raw HTTP 500s.

| Scenario | Response |
|----------|----------|
| iTunes API timeout / failure | `{"error": "iTunes API unavailable: <message>"}` |
| Invalid keyword or app ID | `{"error": "Keyword 42 not found"}` |
| Duplicate `track_id` in `add_app` | `{"error": "An app with this track_id already exists"}` |
| Validation failure (too many keywords, bad country code) | Descriptive error string |
| Malformed JSON-RPC / missing fields | HTTP 400 (handled by MCP SDK) |

Rate limiting (2s between iTunes calls) is handled inside the tool handlers, same as existing views.

---

## Authentication

No authentication for the initial implementation — consistent with the rest of the app (local-only, trusted Docker network). A simple `MCP_API_KEY` environment variable can be added as an opt-in check in `mcp_views.py` in a future iteration.

---

## Documentation

A `docs/mcp.md` file is produced as part of the implementation. It is the primary reference for anyone integrating the MCP server into another project.

### Contents of `docs/mcp.md`

**1. Prerequisites & startup order**
- Confirm RespectASO is running (`docker compose up -d`) before starting the consuming project
- The `respectaso_net` Docker network is created automatically on first `docker compose up`

**2. Connecting the consuming project**
- The compose snippet to join `respectaso_net` (from the Docker Networking section above)
- The MCP endpoint URL: `http://respectaso-web:8080/mcp/`
- A note that the host machine can also reach it at `http://localhost/mcp/`

**3. Integration examples**
- **Claude SDK (Python)** — how to pass the MCP server URL when creating an `anthropic.Anthropic` client with MCP tool use
- **OpenAI SDK (Python)** — how to include the MCP server as a tool source in a `client.chat.completions.create()` call
- Both examples should show a minimal end-to-end call (e.g., `search_keywords` for "fitness tracker" in the US)

**4. Tool reference**
One section per tool with:
- Description (one sentence)
- Input parameters (name, type, required/optional, constraints)
- Example call (JSON)
- Example response (JSON, truncated where large)
- Notes (rate limiting, runtime, edge cases)

**5. Error reference**
Table of all error strings an LLM caller may receive and what they mean.

**6. Limitations**
- Rate limiting: 2s between iTunes API calls
- SQLite is single-writer — avoid concurrent tool calls that trigger searches
- No authentication (local use only)

---

## Out of Scope

- ASGI server migration (Gunicorn sync workers are sufficient)
- MCP tool for CSV export (use the existing HTTP endpoint directly)
- Streaming partial results during long searches (full result returned on completion)
- Authentication / API keys (future opt-in)
