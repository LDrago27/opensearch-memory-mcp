#!/usr/bin/env bash
# Kiro CLI lifecycle hook driver — auto-saves prompts, tool calls, and
# assistant replies to OpenSearch.
#
# `python -m opensearch_memory_mcp setup kiro` installs this for you by writing
# a memory-enabled agent (~/.kiro/agents/kiro-memory.json) whose hooks call
# `python -m opensearch_memory_mcp hook --agent kiro` directly, then setting it
# as the default agent. This script is a convenience for wiring the hook into
# ANOTHER agent's config by hand.
#
# Kiro hooks are per-agent (there is no global settings.json like Claude Code),
# so add this to the `hooks` block of any agent you want to log
# (~/.kiro/agents/<name>.json):
#
#   "hooks": {
#     "userPromptSubmit": [{ "command": "/abs/path/to/this/script" }],
#     "postToolUse":      [{ "matcher": "*", "command": "/abs/path/to/this/script" }],
#     "stop":             [{ "command": "/abs/path/to/this/script" }]
#   }
#
# Kiro passes the hook payload as JSON on stdin and exposes KIRO_SESSION_ID in
# the environment; errors are swallowed by the Python driver so a broken cluster
# never blocks Kiro.

exec python3 -m opensearch_memory_mcp hook --agent kiro
