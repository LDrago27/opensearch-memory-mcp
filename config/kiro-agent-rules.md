# Memory Instructions for Kiro CLI

The `opensearch-memory` MCP server is registered for Kiro CLI. User prompts,
tool calls, and assistant replies are persisted automatically by Kiro CLI
lifecycle hooks (`userPromptSubmit`, `postToolUse`, `stop`) configured on the
`kiro-memory` agent — you do **not** need to call `save_memory` yourself.

These hooks are installed by:

```bash
python -m opensearch_memory_mcp setup kiro
```

which writes `~/.kiro/agents/kiro-memory.json` and makes it the default agent
(`kiro-cli agent set-default kiro-memory`). Revert with
`kiro-cli agent set-default kiro_default`.

## Recall

Use `recall` to search past context before complex tasks.
Use `recall_timeframe` for time-based queries ("what did I do yesterday?").

## Analysis

Use `analyze_workflow` when asked to optimize workflow — reason over the returned
data to suggest new agents, skills, or improvements.
