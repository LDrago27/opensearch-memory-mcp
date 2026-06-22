"""Lifecycle hook driver for Kiro CLI and Claude Code.

Reads a single JSON payload on stdin (as defined by each agent's hooks API)
and writes the relevant interaction to OpenSearch via ``save_memory_impl``.

Invoked as::

    <python> -m opensearch_memory_mcp hook [--agent kiro|claude-code]

``--agent`` selects how the payload is interpreted (default ``claude-code`` for
backward compatibility). The event names are matched case-insensitively, so the
same dispatch handles Kiro's camelCase events and Claude's PascalCase events:

    userPromptSubmit / UserPromptSubmit     -> persist the user's prompt
    postToolUse      / PostToolUse          -> persist a tool call
    stop / Stop / SubagentStop              -> persist the assistant's reply

Differences handled between the two agents:

* Session id
    - Claude: ``payload["session_id"]``
    - Kiro:   ``KIRO_SESSION_ID`` environment variable
* Assistant reply text (stop event)
    - Kiro:   ``payload["assistant_response"]`` (provided inline)
    - Claude: parsed from the JSONL transcript at ``payload["transcript_path"]``

Hook failures must never block the agent, so all errors are swallowed and the
process always exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_TRUNCATE = 4000
_ASSISTANT_TRUNCATE = 8000


def _agent_from_argv(argv: list[str]) -> str:
    """Parse ``--agent <name>`` from argv; default ``claude-code`` for back-compat."""
    if "--agent" in argv:
        idx = argv.index("--agent")
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return "claude-code"


def _project_name(cwd: str) -> str:
    return Path(cwd).name if cwd else ""


def _last_assistant_text(transcript_path: str) -> str:
    """Return the text of the most recent assistant turn from a JSONL transcript.

    Used for Claude Code, whose ``Stop`` payload does not include the reply text.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        lines = p.read_text().splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
        elif isinstance(content, str):
            text = content.strip()
        else:
            text = ""
        if text:
            return text
    return ""


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _is_self_write(tool_name: str) -> bool:
    """True if a tool call is our own save_memory write (avoid recursive logging).

    Covers every agent's naming: ``save_memory`` (Kiro builtin alias),
    ``@opensearch-memory/save_memory`` (Kiro MCP), and
    ``mcp__opensearch-memory__save_memory`` (Claude MCP).
    """
    return "save_memory" in (tool_name or "")


def main() -> None:
    agent_type = _agent_from_argv(sys.argv)

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    event = (payload.get("hook_event_name") or "").lower()
    # Claude puts the session id in the payload; Kiro exposes it via the env.
    session_id = payload.get("session_id") or os.environ.get("KIRO_SESSION_ID", "") or ""
    project = _project_name(payload.get("cwd", "") or "")

    # Imported lazily so a misconfigured cluster doesn't crash the hook before
    # it can swallow the error.
    try:
        from .server import save_memory_impl as save_memory
    except Exception as e:
        print(f"opensearch-memory hook: import failed: {e}", file=sys.stderr)
        sys.exit(0)

    try:
        if event == "userpromptsubmit":
            prompt = payload.get("prompt", "") or ""
            if prompt.strip():
                save_memory(
                    content=prompt,
                    role="user",
                    session_id=session_id,
                    agent_type=agent_type,
                    project=project,
                )
        elif event == "posttooluse":
            tool_name = payload.get("tool_name", "") or ""
            # Don't recursively log our own writes.
            if _is_self_write(tool_name):
                sys.exit(0)
            tool_input = payload.get("tool_input", {})
            tool_response = payload.get("tool_response", {})
            save_memory(
                content=f"Used {tool_name}",
                role="assistant",
                session_id=session_id,
                agent_type=agent_type,
                project=project,
                tool_calls=[
                    {
                        "name": tool_name,
                        "input": _truncate(json.dumps(tool_input, default=str), _TRUNCATE),
                        "output": _truncate(json.dumps(tool_response, default=str), _TRUNCATE),
                    }
                ],
            )
        elif event in ("stop", "subagentstop"):
            # Kiro provides the reply inline; Claude requires reading the transcript.
            text = payload.get("assistant_response") or _last_assistant_text(
                payload.get("transcript_path", "") or ""
            )
            if text and text.strip():
                save_memory(
                    content=_truncate(text, _ASSISTANT_TRUNCATE),
                    role="assistant",
                    session_id=session_id,
                    agent_type=agent_type,
                    project=project,
                )
    except Exception as e:
        print(f"opensearch-memory hook: save failed: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
