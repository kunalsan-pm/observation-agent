---
description: Answer a question about the observations dataset
argument-hint: <question>
---

Delegate to the `observation-agent` subagent. Pass the user's question verbatim.

The subagent will investigate using the `observations` MCP server and write a
full markdown report to `outputs/`. Return its answer without modification or
added commentary — do not re-summarise, re-verify, or editorialise.

Question: $ARGUMENTS
