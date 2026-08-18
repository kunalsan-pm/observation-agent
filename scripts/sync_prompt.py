#!/usr/bin/env python
"""
Generate the Claude Code artifacts from the single source of truth.

``agent/prompt.py`` holds the system prompt. The web agent imports it directly;
the Claude Code subagent needs it as markdown with frontmatter. Generating one
from the other means the two ways of running this agent cannot drift apart.

    python scripts/sync_prompt.py          # write
    python scripts/sync_prompt.py --check  # fail if stale (for CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.prompt import SYSTEM_PROMPT  # noqa: E402

AGENT_FILE = ROOT / ".claude" / "agents" / "observation-agent.md"
COMMAND_FILE = ROOT / ".claude" / "commands" / "answer.md"

BANNER = (
    "<!-- GENERATED FILE — do not edit by hand.\n"
    "     Source: agent/prompt.py · regenerate: python scripts/sync_prompt.py -->"
)

AGENT_FRONTMATTER = """\
---
name: observation-agent
description: >-
  Answers natural-language questions about the observations dataset — lookups,
  cross-record connections, and pattern detection (sustained low scores,
  critical-question failures, cadence breaches, score decline, recurring coach-note
  concerns). Use for any question about organizations, projects, users, observations,
  responses or coach notes. Invoked by the /answer slash command.
tools: mcp__observations, Write, Read
---
"""

ENV_NOTE = """\

## Tooling in this environment

The `observations` MCP server is registered in `.mcp.json` and its tools appear
as `mcp__observations__<tool>`. Write reports with the `Write` tool to
`outputs/<slug>.md`, relative to the repository root. Write nothing outside
`outputs/`.
"""

COMMAND_BODY = """\
---
description: Answer a question about the observations dataset
argument-hint: <question>
---

Delegate to the `observation-agent` subagent. Pass the user's question verbatim.

The subagent will investigate using the `observations` MCP server and write a
full markdown report to `outputs/`. Return its answer without modification or
added commentary — do not re-summarise, re-verify, or editorialise.

Question: $ARGUMENTS
"""


def agent_markdown() -> str:
    return f"{AGENT_FRONTMATTER}\n{BANNER}\n\n{SYSTEM_PROMPT.rstrip()}\n{ENV_NOTE}"


def command_markdown() -> str:
    return COMMAND_BODY


TARGETS = [(AGENT_FILE, agent_markdown), (COMMAND_FILE, command_markdown)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Exit non-zero if stale.")
    args = parser.parse_args()

    stale = []
    for path, builder in TARGETS:
        want = builder()
        have = path.read_text() if path.exists() else None
        if have == want:
            print(f"  = {path.relative_to(ROOT)}")
            continue
        stale.append(path)
        if args.check:
            print(f"  ! {path.relative_to(ROOT)} is out of date")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want)
        print(f"  → wrote {path.relative_to(ROOT)}")

    if args.check and stale:
        print("\nRun: python scripts/sync_prompt.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
