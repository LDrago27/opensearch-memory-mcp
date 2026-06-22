"""Entry point: python -m opensearch_memory_mcp [setup kiro|claude-code | hook]"""

import json
import sys
from pathlib import Path

# Steering content for Kiro. Saves are handled by lifecycle hooks installed on
# the memory-enabled agent (userPromptSubmit / postToolUse / stop), so the model
# only needs guidance on when to recall and analyze.
KIRO_RECALL_STEERING = """\
---
inclusion: always
---
# Memory

The `opensearch-memory` MCP server is available. User prompts, tool calls, and
assistant replies are persisted automatically by Kiro CLI lifecycle hooks — you
do NOT need to call `save_memory` yourself.

## Recall

Before starting complex tasks, call `recall` to search past sessions for
relevant context. Use `recall_timeframe` for questions like "what did I do
yesterday?".

## Analysis

When asked to optimize workflow or suggest improvements, call `analyze_workflow`
and reason over the returned data to suggest new agents, skills, or process
improvements.
"""

# CLAUDE.md content for Claude Code. Saves are handled by lifecycle hooks
# (UserPromptSubmit / PostToolUse / Stop), so the model only needs guidance
# on when to recall and analyze.
CLAUDE_CODE_STEERING = """\
# Memory

This project has the `opensearch-memory` MCP server registered. User prompts,
tool calls, and assistant replies are persisted automatically by Claude Code
lifecycle hooks — you do NOT need to call `save_memory` yourself.

## Recall

Before starting complex tasks, call `recall` to search past sessions for
relevant context. Use `recall_timeframe` for questions like "what did I do
yesterday?".

## Analysis

When asked to optimize workflow or suggest improvements, call `analyze_workflow`
and reason over the returned data to suggest new agents, skills, or process
improvements.
"""


def _server_command() -> str:
    """Absolute path to the python executable running this package."""
    return str(Path(sys.executable))


def _install_claude_hooks(python_path: str) -> Path:
    """Add UserPromptSubmit / PostToolUse / Stop hooks to ~/.claude/settings.json."""
    settings_dir = Path.home() / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "settings.json"

    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text() or "{}")
        except json.JSONDecodeError:
            print(f"  ⚠ {settings_file} is not valid JSON; refusing to overwrite.")
            print(f"     Add the hooks block manually (see README).")
            return settings_file
    else:
        settings = {}

    command = f"{python_path} -m opensearch_memory_mcp hook"
    hook_entry = {"type": "command", "command": command}

    hooks = settings.setdefault("hooks", {})
    for event in ("UserPromptSubmit", "PostToolUse", "Stop", "SubagentStop"):
        matchers = hooks.setdefault(event, [])
        # Idempotent: skip if our command is already registered for this event.
        already = any(
            any(h.get("command") == command for h in (m.get("hooks") or []))
            for m in matchers
            if isinstance(m, dict)
        )
        if already:
            continue
        # PostToolUse uses a matcher (regex over tool name); the others don't.
        if event == "PostToolUse":
            matchers.append({"matcher": ".*", "hooks": [hook_entry]})
        else:
            matchers.append({"hooks": [hook_entry]})

    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_file


def _kiro_memory_agent(agent_name: str, python_path: str) -> dict:
    """Build a memory-enabled Kiro agent config.

    Mirrors the built-in default agent (full toolset via ``*`` and all MCP
    servers from ``~/.kiro/settings/mcp.json`` via ``includeMcpJson``) and adds
    the three lifecycle hooks that persist the conversation. ``allowedTools`` is
    left empty so the agent prompts for tool approval exactly like the default.
    """
    command = f"{python_path} -m opensearch_memory_mcp hook --agent kiro"
    return {
        "name": agent_name,
        "description": "Default Kiro agent with automatic OpenSearch memory logging via lifecycle hooks.",
        "tools": ["*"],
        "allowedTools": [],
        "resources": [],
        "includeMcpJson": True,
        "hooks": {
            "userPromptSubmit": [{"command": command}],
            "postToolUse": [{"matcher": "*", "command": command}],
            "stop": [{"command": command}],
        },
    }


def setup(agent: str) -> None:
    python_path = _server_command()

    if agent == "kiro":
        # 1. Register the MCP server globally so any agent with
        #    `includeMcpJson: true` (including our memory agent) can load it.
        mcp_dir = Path.home() / ".kiro" / "settings"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        mcp_file = mcp_dir / "mcp.json"

        server_entry = {
            "command": python_path,
            "args": ["-m", "opensearch_memory_mcp"],
        }

        if mcp_file.exists():
            existing = json.loads(mcp_file.read_text())
        else:
            existing = {"mcpServers": {}}
        existing.setdefault("mcpServers", {})["opensearch-memory"] = server_entry
        mcp_file.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"✓ MCP server registered in {mcp_file}")

        # 2. Write a memory-enabled agent with lifecycle hooks. Kiro hooks are
        #    per-agent (there is no global settings.json like Claude Code), so
        #    auto-logging is attached to this agent.
        agent_name = "kiro-memory"
        agents_dir = Path.home() / ".kiro" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agents_dir / f"{agent_name}.json"
        agent_file.write_text(
            json.dumps(_kiro_memory_agent(agent_name, python_path), indent=2) + "\n"
        )
        print(f"✓ Memory-enabled agent written to {agent_file}")
        print("    userPromptSubmit → save user prompts")
        print("    postToolUse      → save tool calls")
        print("    stop             → save assistant replies")

        # 3. Make it the default agent so every `kiro-cli chat` auto-logs.
        import subprocess
        result = subprocess.run(
            ["kiro-cli", "agent", "set-default", agent_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"✓ Default agent set to '{agent_name}' — every `kiro-cli chat` now auto-logs")
            print("    Revert anytime with: kiro-cli agent set-default kiro_default")
        else:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"  Could not set default agent automatically: {detail}")
            print(f"  Run manually:        kiro-cli agent set-default {agent_name}")
            print(f"  Or launch on demand: kiro-cli chat --agent {agent_name}")

        # 4. Replace the old "save after every response" steering with slim
        #    recall/analyze guidance — saves are now automatic via hooks.
        steering_dir = Path.home() / ".kiro" / "steering"
        steering_dir.mkdir(parents=True, exist_ok=True)
        old_steering = steering_dir / "memory-auto-logging.md"
        if old_steering.exists():
            old_steering.unlink()
            print(f"✓ Removed obsolete auto-logging steering ({old_steering.name})")
        steering_file = steering_dir / "memory-recall.md"
        steering_file.write_text(KIRO_RECALL_STEERING)
        print(f"✓ Recall/analyze steering installed at {steering_file}")

    elif agent == "claude-code":
        # 1. Register MCP server
        print("Registering MCP server with Claude Code...")
        import subprocess
        result = subprocess.run(
            ["claude", "mcp", "add", "--scope", "user", "opensearch-memory", "--", python_path, "-m", "opensearch_memory_mcp"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("✓ MCP server registered with Claude Code (user scope — available in all projects)")
        else:
            print(f"  Could not auto-register: {result.stderr.strip()}")
            print(f"  Run manually: claude mcp add --scope user opensearch-memory -- {python_path} -m opensearch_memory_mcp")

        # 2. Install lifecycle hooks
        settings_file = _install_claude_hooks(python_path)
        print(f"✓ Lifecycle hooks installed in {settings_file}")
        print("    UserPromptSubmit → save user prompts")
        print("    PostToolUse      → save tool calls")
        print("    Stop / SubagentStop → save assistant replies")

        # 3. Install CLAUDE.md (recall/analyze guidance only — saves are automatic)
        claude_md = Path.cwd() / "CLAUDE.md"
        content = CLAUDE_CODE_STEERING
        if claude_md.exists():
            existing = claude_md.read_text()
            if "opensearch-memory" not in existing:
                claude_md.write_text(existing + "\n\n" + content)
                print(f"✓ Memory guidance appended to {claude_md}")
            else:
                print(f"✓ {claude_md} already references opensearch-memory")
        else:
            claude_md.write_text(content)
            print(f"✓ Memory guidance written to {claude_md}")

    else:
        print(f"Unknown agent: {agent}. Use 'kiro' or 'claude-code'.")
        sys.exit(1)

    print("\n✓ Setup complete.")
    print("  Config: ~/.opensearch-memory/config.json")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "hook":
        from .hook import main as hook_main
        hook_main()
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "setup":
        setup(sys.argv[2])
        return
    from .server import mcp
    mcp.run(transport="stdio")


main()
