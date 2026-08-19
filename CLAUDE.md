# Claude Code instructions

Context for working **on** this repo. The agent's own prompt is generated —
see below.

## Layout

| Path | What it is |
| --- | --- |
| `mcp_server/server.py` | The 22 MCP tools. A thin, typed shell — logic lives in the modules below. |
| `mcp_server/dataset.py` | Loading, indexes, per-project policy lookup, id classification. |
| `mcp_server/queries.py` | Retrieval, aggregation, the associate dossier. |
| `mcp_server/analytics.py` | The five pattern detectors. |
| `agent/prompt.py` | **The system prompt. Single source of truth.** |
| `agent/backends.py` | Anthropic API loop and Claude Agent SDK, behind one event stream. |
| `web/` | FastAPI + SSE, and a vanilla JS/CSS frontend with no build step. |
| `data/` | Fixture data. **Read-only.** |
| `outputs/` | Where the agent writes reports. Gitignored. |
| `docs/EXPECTED_ANSWERS.md` | Generated ground truth for the demo question set. Question text comes from `agent/questions.py`. |

## Generated files — do not hand-edit

`.claude/agents/observation-agent.md` and `.claude/commands/answer.md` are
generated from `agent/prompt.py`. To change the agent's behaviour, edit
`agent/prompt.py` and run:

```bash
.venv/bin/python scripts/sync_prompt.py
```

`docs/EXPECTED_ANSWERS.md` is generated from live tool calls by
`scripts/reference_answers.py`. Regenerate it after changing a detector — the
diff shows exactly which numbers moved.

## Conventions

- **New analysis belongs in a tool, not in the prompt.** If answering a question
  needs cross-record computation, add or extend a tool in `mcp_server/` so the
  work happens in pandas and only the result enters context.
- **A detector takes its rule as parameters with defended defaults**, returns the
  `rule` it applied alongside its findings, and judges against the **project's**
  `score_threshold` / `cadence_days` rather than a global constant.
- **Every finding carries citations** — observation ids, note ids, question ids.
- **List tools return the `page()` envelope** (`total_matching`, `truncated`) so a
  partial result is never mistaken for a complete one.
- **Round scores to 2dp** on the way out via `r2()`. Raw floats carry 14 digits of
  noise that cost tokens and imply false precision.
- Anchor date is **2026-05-12** (`OBSERVATIONS_TODAY`), measured to end-of-day.

## Before you claim it works

```bash
make check     # tests + prompt-sync check
```

`tests/test_detectors.py` pins each detector to the signals actually in the
fixture data. If you change a threshold or window default, a test will fail —
that is the point. Update the expectation deliberately and regenerate
`docs/EXPECTED_ANSWERS.md`.

## Data quirks that are already handled

- `projects.json` has **no** `template_id`, despite the dataset's own documentation
  saying it does. It is derived from `templates.json` and cross-checked against
  every observation.
- `OBS-` is overloaded: observers are 3-digit (`OBS-002`), observations 6-digit
  (`OBS-000042`). Use `classify_id` / `resolve_entity`.
- Observer display names are not unique. Always key on `user_id`.
- `is_critical` exists only on response rows, never on observation headers.
- Observers have `manager_id = null` and sit outside the management hierarchy.

## If in doubt

If a request is ambiguous about design or evaluation intent, ask rather than
picking an interpretation.
