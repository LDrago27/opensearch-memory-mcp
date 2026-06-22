# opensearch-memory-mcp

Persistent, semantically searchable memory for AI agents (Kiro CLI, Claude Code, etc.) backed by OpenSearch.

## What it does

- **Saves** every conversation turn (user messages, agent responses, tool calls) to OpenSearch
- **Recalls** past interactions via semantic search ("what did we discuss about auth?")
- **Time queries** — "what did I do yesterday?"
- **Session browsing** — list and replay full conversation histories
- **Workflow analytics** — usage patterns, frequent topics, tool usage, time distribution
- **Agent-side reasoning** — analysis tools return structured data so the calling agent can suggest optimizations

## Architecture

```
AI Agent (Kiro/Claude Code/etc.)
    │ stdio MCP
    ▼
opensearch-memory-mcp (Python FastMCP)
    │
    ▼
OpenSearch (local or managed)
    ├── {prefix}-interactions index (k-NN + text)
    ├── {prefix}-sessions index
    ├── {prefix}-nlp-pipeline (text_embedding ingest)
    └── {prefix}-search-pipeline (hybrid normalization)
```

Where `{prefix}` defaults to `memory` (configurable via `INDEX_PREFIX`).

## Prerequisites

- Python 3.10+
- OpenSearch 2.12+ (local via Docker or managed)

## Quickstart

### 1. Start OpenSearch locally

```bash
docker compose up -d
```

This starts a single-node OpenSearch on `http://localhost:9200` with security disabled (no auth needed).

### 2. Install the MCP server

```bash
pip install -e .
```

### 3. Configure your agent

Both integrations save user prompts, tool calls, and assistant replies
automatically via **lifecycle hooks** — no model cooperation required. Hook
failures never block the agent: if OpenSearch is unreachable, the hook logs to
stderr and exits 0.

**Kiro CLI:**
```bash
python -m opensearch_memory_mcp setup kiro
```

This:
1. Registers the `opensearch-memory` MCP server globally in `~/.kiro/settings/mcp.json`.
2. Writes a memory-enabled agent to `~/.kiro/agents/kiro-memory.json` — a full-toolset agent (`"tools": ["*"]`, `"includeMcpJson": true`) with `userPromptSubmit`, `postToolUse`, and `stop` hooks that call `python -m opensearch_memory_mcp hook --agent kiro`.
3. Runs `kiro-cli agent set-default kiro-memory` so **every** `kiro-cli chat` auto-logs. Revert anytime with `kiro-cli agent set-default kiro_default`.
4. Installs slim recall/analyze guidance at `~/.kiro/steering/memory-recall.md` (replacing the older `memory-auto-logging.md`, since saves are no longer prompt-driven).

> **Kiro hooks are per-agent.** Unlike Claude Code, Kiro has no global hooks
> file, and the built-in `kiro_default` agent can't be edited. Setup therefore
> attaches logging to the `kiro-memory` agent and makes it the default.
> Conversations you explicitly start under *another* agent
> (`kiro-cli chat --agent <other>`) won't log unless you add the same hooks
> block to that agent's config (see [Manual Kiro hook wiring](#manual-kiro-hook-wiring)).

**Claude Code:**
```bash
python -m opensearch_memory_mcp setup claude-code
```

This:
1. Registers the `opensearch-memory` MCP server with Claude Code at **user scope** (`claude mcp add --scope user`), so it's available from every directory — not just where you ran the setup.
2. Installs **lifecycle hooks** in `~/.claude/settings.json` for `UserPromptSubmit`, `PostToolUse` (all tools), `Stop`, and `SubagentStop`.
3. Writes recall/analyze guidance to `CLAUDE.md` in the current directory.

The server auto-creates `~/.opensearch-memory/config.json` with defaults pointing to `http://localhost:9200` (no auth). Edit this file if you need to point to a different cluster.

### Manual Claude Code hook wiring

If `setup claude-code` can't write `~/.claude/settings.json` (permissions, custom paths), add this block by hand:

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/abs/path/to/python -m opensearch_memory_mcp hook"}]}],
    "PostToolUse":      [{"matcher": ".*", "hooks": [{"type": "command", "command": "/abs/path/to/python -m opensearch_memory_mcp hook"}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "/abs/path/to/python -m opensearch_memory_mcp hook"}]}],
    "SubagentStop":     [{"hooks": [{"type": "command", "command": "/abs/path/to/python -m opensearch_memory_mcp hook"}]}]
  }
}
```

### Manual Kiro hook wiring

Kiro hooks live in an **agent config**, not a global settings file. To log a
different agent (or to wire logging by hand), add this `hooks` block to that
agent's `~/.kiro/agents/<name>.json` (a ready-to-copy example agent is in
[`config/kiro-agent.json`](config/kiro-agent.json)):

```json
{
  "hooks": {
    "userPromptSubmit": [{ "command": "/abs/path/to/python -m opensearch_memory_mcp hook --agent kiro" }],
    "postToolUse":      [{ "matcher": "*", "command": "/abs/path/to/python -m opensearch_memory_mcp hook --agent kiro" }],
    "stop":             [{ "command": "/abs/path/to/python -m opensearch_memory_mcp hook --agent kiro" }]
  }
}
```

Kiro delivers the hook payload as JSON on stdin and exposes the session id via
the `KIRO_SESSION_ID` environment variable (the `stop` payload also carries the
assistant's reply inline). The hooks may also point at the convenience wrapper
[`hooks/kiro-hook.sh`](hooks/kiro-hook.sh).

## Configuration Reference

On first run, the server creates a config file at:

```
~/.opensearch-memory/config.json
```

Edit this file to point to your OpenSearch cluster:

```json
{
  "opensearch_url": "http://localhost:9200",
  "opensearch_user": "",
  "opensearch_password": "",
  "opensearch_verify_certs": false,
  "opensearch_model_id": "",
  "embedding_dimension": 768,
  "index_prefix": "memory"
}
```

| Field | Default | Description |
|---|---|---|
| `opensearch_url` | `http://localhost:9200` | OpenSearch cluster endpoint |
| `opensearch_user` | *(empty)* | Auth username (leave empty if security is disabled) |
| `opensearch_password` | *(empty)* | Auth password |
| `opensearch_verify_certs` | `false` | Verify TLS certificates |
| `opensearch_model_id` | *(empty)* | Deployed embedding model ID for neural search |
| `embedding_dimension` | `768` | Embedding vector dimension (must match your model) |
| `index_prefix` | `memory` | Prefix for all index and pipeline names |

**For a managed cluster**, just edit the config:

```json
{
  "opensearch_url": "https://my-domain.us-east-1.es.amazonaws.com",
  "opensearch_user": "admin",
  "opensearch_password": "your-password",
  "opensearch_verify_certs": true
}
```

## OpenSearch Indices & Pipelines

The server **auto-creates** these on first connection. All names use the `INDEX_PREFIX` (default: `memory`):

### Index: `memory-interactions`

Stores every conversation turn. One document per `save_memory` call.

| Field | Type | Description |
|---|---|---|
| `session_id` | keyword | Groups turns into conversations |
| `agent_type` | keyword | `"kiro"`, `"claude-code"`, etc. |
| `timestamp` | date | When the interaction happened (UTC ISO) |
| `role` | keyword | `"user"` or `"assistant"` |
| `content` | text | The actual message text |
| `content_text` | text | Combined text for embedding (role + content + tool summaries) |
| `content_embedding` | knn_vector | Vector embedding *(only if `OPENSEARCH_MODEL_ID` is set)* |
| `tool_calls` | nested | `[{name, input, output}]` |
| `project` | keyword | Project/workspace name |
| `tags` | keyword[] | User-defined tags |

### Index: `memory-sessions`

Session metadata — one document per session, upserted on each turn.

| Field | Type | Description |
|---|---|---|
| `session_id` | keyword | Matches interactions |
| `agent_type` | keyword | Which agent started the session |
| `started_at` | date | First turn timestamp |
| `last_active` | date | Most recent turn timestamp |
| `project` | keyword | Project name |
| `summary` | text | Session summary (reserved for future use) |
| `turn_count` | integer | Number of turns in session |

### Pipeline: `memory-nlp-pipeline` (ingest)

Attached as default pipeline to `memory-interactions`. Uses OpenSearch `text_embedding` processor to generate vectors from `content_text` → `content_embedding`. **Only created if `OPENSEARCH_MODEL_ID` is set.**

### Pipeline: `memory-search-pipeline` (search)

Hybrid search normalization — combines neural score (weight 0.7) + BM25 keyword score (weight 0.3) using min-max normalization. **Only created if `OPENSEARCH_MODEL_ID` is set.**

### Custom prefix example

Set `INDEX_PREFIX=myteam` and the names become:
- `myteam-interactions`, `myteam-sessions`
- `myteam-nlp-pipeline`, `myteam-search-pipeline`

This lets multiple users/teams share one OpenSearch cluster with isolated data.

## MCP Tools

### `ping`
Check OpenSearch connectivity. Returns cluster health JSON.

### `save_memory`
Store a conversation turn.
```
content: "Implemented JWT auth middleware"
role: "assistant"
session_id: "abc-123"
agent_type: "claude-code"
project: "my-api"
tags: ["auth", "feature"]
tool_calls: [{"name": "write_file", "input": "auth.ts", "output": "created"}]
```

### `recall`
Semantic + keyword search across all past interactions.
```
query: "how did we handle authentication?"
limit: 10
project: "my-api"
```

### `recall_timeframe`
Time-based retrieval with natural language.
```
timeframe: "yesterday"        # or "today", "last 3 days", "last 2 hours", ISO date
query: "database changes"     # optional semantic filter within timeframe
```

### `list_sessions`
Browse past sessions, sorted by most recently active.
```
days: 7
agent_type: "kiro"
```

### `get_session`
Replay a full conversation in chronological order.
```
session_id: "abc-123"
```

### `analyze_usage`
Usage statistics — total interactions, per day/agent/project breakdowns, top tools.
```
days: 7
```

### `analyze_workflow`
Workflow analytics — frequent topics (significant_terms), hourly distribution, tool frequency, active sessions. The calling agent reasons over this data to suggest new agents, skills, or workflow improvements.
```
days: 7
project: "my-api"
```

## Setting up Neural Search

For semantic search, you need a deployed embedding model in OpenSearch:

1. Register and deploy a model (e.g., `huggingface/sentence-transformers/msmarco-distilbert-base-tas-b`)
2. Set `OPENSEARCH_MODEL_ID` to the deployed model ID
3. Set `EMBEDDING_DIMENSION` to match the model (768 for DistilBERT)

See the [OpenSearch neural search tutorial](https://docs.opensearch.org/latest/search-plugins/neural-search-tutorial/) for details.

## Running Tests

```bash
# Start OpenSearch
docker compose up -d

# Run tests (uses config file defaults — localhost:9200, no auth)
pytest tests/ -v
```

## License

Apache-2.0
