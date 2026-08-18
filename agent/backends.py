"""
Two ways to drive the agent, behind one event stream.

* :class:`ApiBackend` — the Anthropic Messages API with an explicit tool-use
  loop. Needs ``ANTHROPIC_API_KEY``. Portable: anyone who clones the repo and
  has a key can run it.
* :class:`AgentSdkBackend` — the Claude Agent SDK, which reuses a local Claude
  Code login instead of an API key and lets Claude Code own the MCP connection.

Both yield the same event dicts, so the web layer and the CLI never learn which
one is running:

    {"type": "start",       "backend", "model"}
    {"type": "text",        "delta"}
    {"type": "thinking",    "delta"}
    {"type": "tool_call",   "id", "name", "input"}
    {"type": "tool_result", "id", "name", "ok", "duration_ms", "preview", "truncated"}
    {"type": "report",      "path", "title"}
    {"type": "done",        "usage", "turns", "tool_calls", "elapsed_ms"}
    {"type": "error",       "message"}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

from agent import reports
from agent.mcp_bridge import SERVER_SCRIPT, ObservationTools
from agent.prompt import system_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_API_MODEL = os.environ.get("OBSERVATION_AGENT_MODEL", "claude-opus-5")
DEFAULT_MAX_TURNS = int(os.environ.get("OBSERVATION_AGENT_MAX_TURNS", "24"))
PREVIEW_CHARS = 600


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def available_backends() -> dict[str, dict[str, Any]]:
    """What this machine can actually run, and why."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import claude_agent_sdk  # noqa: F401

        sdk_installed = True
    except ImportError:
        sdk_installed = False
    cli = _find_claude_cli()

    return {
        "api": {
            "available": has_key,
            "label": "Anthropic API",
            "model": DEFAULT_API_MODEL,
            "reason": "ANTHROPIC_API_KEY is set" if has_key else "ANTHROPIC_API_KEY is not set",
        },
        "sdk": {
            "available": sdk_installed and bool(cli),
            "label": "Claude Agent SDK",
            "model": os.environ.get("OBSERVATION_AGENT_SDK_MODEL") or "default (Claude Code)",
            "reason": (
                "claude-agent-sdk installed and the claude CLI is on PATH"
                if sdk_installed and cli
                else "claude-agent-sdk not installed"
                if not sdk_installed
                else "the claude CLI is not on PATH"
            ),
        },
    }


def _find_claude_cli() -> str | None:
    import shutil

    return shutil.which("claude")


def resolve_backend(preferred: str | None = None) -> str:
    """Pick a backend. An API key wins when present because it needs no local
    Claude Code session; otherwise fall back to the SDK."""
    backends = available_backends()
    if preferred and preferred != "auto":
        if not backends.get(preferred, {}).get("available"):
            raise RuntimeError(
                f"backend '{preferred}' is unavailable: "
                f"{backends.get(preferred, {}).get('reason', 'unknown backend')}"
            )
        return preferred
    for name in ("api", "sdk"):
        if backends[name]["available"]:
            return name
    raise RuntimeError(
        "No backend available. Either set ANTHROPIC_API_KEY, or install the "
        "Claude Code CLI and log in (`claude`) to use the Agent SDK backend."
    )


def _preview(text: str) -> str:
    text = (text or "").strip()
    return text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")


def _report_event(name: str, tool_input: dict, result: Any) -> dict | None:
    """Turn a successful write_report call into a UI-visible report event."""
    if name != "write_report":
        return None
    path = None
    if isinstance(result, dict):
        path = result.get("path")
    if not path:
        raw = tool_input.get("filename") or ""
        path = f"outputs/{Path(str(raw)).name}"
    return {
        "type": "report",
        "path": path,
        "title": tool_input.get("title") or Path(path).stem.replace("-", " ").title(),
    }


# ---------------------------------------------------------------------------
# Anthropic API backend
# ---------------------------------------------------------------------------


class ApiBackend:
    """Explicit tool-use loop against the Messages API."""

    name = "api"

    def __init__(self, model: str | None = None, max_turns: int | None = None) -> None:
        self.model = model or DEFAULT_API_MODEL
        self.max_turns = max_turns or DEFAULT_MAX_TURNS

    async def run(
        self, question: str, history: list[dict] | None = None
    ) -> AsyncIterator[dict]:
        from anthropic import AsyncAnthropic

        started = time.perf_counter()
        client = AsyncAnthropic()

        async with ObservationTools() as mcp_tools:
            tool_schemas = mcp_tools.schemas + [reports.WRITE_REPORT_TOOL]
            messages: list[dict] = list(history or [])
            messages.append({"role": "user", "content": question})

            yield {"type": "start", "backend": self.name, "model": self.model}

            usage_total = {"input_tokens": 0, "output_tokens": 0}
            tool_calls = 0

            for turn in range(1, self.max_turns + 1):
                assistant_blocks: list[dict] = []
                current: dict | None = None

                try:
                    stream = client.messages.stream(
                        model=self.model,
                        max_tokens=8192,
                        system=system_prompt(),
                        tools=tool_schemas,
                        messages=messages,
                    )
                    async with stream as events:
                        async for event in events:
                            etype = getattr(event, "type", "")

                            if etype == "content_block_start":
                                block = event.content_block
                                btype = getattr(block, "type", "")
                                if btype == "text":
                                    current = {"type": "text", "text": ""}
                                elif btype == "thinking":
                                    current = {"type": "thinking", "thinking": ""}
                                elif btype == "tool_use":
                                    current = {
                                        "type": "tool_use",
                                        "id": block.id,
                                        "name": block.name,
                                        "input_json": "",
                                    }

                            elif etype == "content_block_delta":
                                delta = event.delta
                                dtype = getattr(delta, "type", "")
                                if dtype == "text_delta" and current is not None:
                                    current["text"] = current.get("text", "") + delta.text
                                    yield {"type": "text", "delta": delta.text}
                                elif dtype == "thinking_delta" and current is not None:
                                    current["thinking"] = (
                                        current.get("thinking", "") + delta.thinking
                                    )
                                    yield {"type": "thinking", "delta": delta.thinking}
                                elif dtype == "input_json_delta" and current is not None:
                                    current["input_json"] = (
                                        current.get("input_json", "") + delta.partial_json
                                    )

                            elif etype == "content_block_stop" and current is not None:
                                assistant_blocks.append(current)
                                current = None

                        final = await events.get_final_message()
                        if final.usage:
                            usage_total["input_tokens"] += final.usage.input_tokens or 0
                            usage_total["output_tokens"] += final.usage.output_tokens or 0
                        stop_reason = final.stop_reason

                except Exception as exc:  # noqa: BLE001
                    yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                    return

                # Rebuild the assistant turn for the next request.
                api_blocks: list[dict] = []
                pending: list[dict] = []
                for block in assistant_blocks:
                    if block["type"] == "text" and block.get("text"):
                        api_blocks.append({"type": "text", "text": block["text"]})
                    elif block["type"] == "tool_use":
                        try:
                            parsed = json.loads(block["input_json"] or "{}")
                        except json.JSONDecodeError:
                            parsed = {}
                        api_blocks.append(
                            {
                                "type": "tool_use",
                                "id": block["id"],
                                "name": block["name"],
                                "input": parsed,
                            }
                        )
                        pending.append(
                            {"id": block["id"], "name": block["name"], "input": parsed}
                        )

                if api_blocks:
                    messages.append({"role": "assistant", "content": api_blocks})

                if stop_reason != "tool_use" or not pending:
                    yield {
                        "type": "done",
                        "usage": usage_total,
                        "turns": turn,
                        "tool_calls": tool_calls,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    }
                    return

                results: list[dict] = []
                for call in pending:
                    tool_calls += 1
                    yield {
                        "type": "tool_call",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["input"],
                    }

                    if call["name"] == "write_report":
                        outcome = reports.write_report(**call["input"])
                        content = json.dumps(outcome)
                        ok = bool(outcome.get("ok"))
                        duration = 0
                        truncated = False
                        data: Any = outcome
                    else:
                        raw = await mcp_tools.call(call["name"], call["input"])
                        content = raw["content"]
                        ok = raw["ok"]
                        duration = raw["duration_ms"]
                        truncated = raw["truncated"]
                        data = raw["data"]

                    yield {
                        "type": "tool_result",
                        "id": call["id"],
                        "name": call["name"],
                        "ok": ok,
                        "duration_ms": duration,
                        "truncated": truncated,
                        "preview": _preview(content),
                    }
                    event = _report_event(call["name"], call["input"], data)
                    if event:
                        yield event

                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": content,
                            **({"is_error": True} if not ok else {}),
                        }
                    )

                messages.append({"role": "user", "content": results})

            yield {
                "type": "done",
                "usage": usage_total,
                "turns": self.max_turns,
                "tool_calls": tool_calls,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "note": f"stopped at the {self.max_turns}-turn limit",
            }


# ---------------------------------------------------------------------------
# Claude Agent SDK backend
# ---------------------------------------------------------------------------


class AgentSdkBackend:
    """Drives the agent through the Claude Agent SDK.

    Claude Code owns the MCP connection here, so the bridge is unused. The
    ``write_report`` capability is provided by allowing the built-in ``Write``
    tool, scoped to ``outputs/`` by the instruction appended below.
    """

    name = "sdk"

    def __init__(self, model: str | None = None, max_turns: int | None = None) -> None:
        self.model = model or os.environ.get("OBSERVATION_AGENT_SDK_MODEL")
        self.max_turns = max_turns or DEFAULT_MAX_TURNS

    async def run(
        self, question: str, history: list[dict] | None = None
    ) -> AsyncIterator[dict]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        started = time.perf_counter()
        instructions = system_prompt() + (
            "\n\n## Writing reports in this environment\n\n"
            "Use the Write tool to create the markdown report, with a path of "
            "the form `outputs/<slug>.md` relative to the repository root. "
            "Write nothing outside `outputs/`."
        )

        options = ClaudeAgentOptions(
            system_prompt=instructions,
            mcp_servers={
                "observations": {
                    "type": "stdio",
                    "command": str(REPO_ROOT / ".venv" / "bin" / "python"),
                    "args": [str(SERVER_SCRIPT)],
                }
            },
            allowed_tools=["mcp__observations", "Write", "Read"],
            permission_mode="bypassPermissions",
            cwd=str(REPO_ROOT),
            max_turns=self.max_turns,
            setting_sources=[],
            **({"model": self.model} if self.model else {}),
        )

        yield {"type": "start", "backend": self.name, "model": self.model or "claude-code-default"}

        tool_calls = 0
        names: dict[str, str] = {}
        auth_failed = False
        prompt = question
        if history:
            prompt = _flatten_history(history) + f"\n\nCurrent question: {question}"

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                if tool_calls == 0 and _looks_like_auth_failure(block.text):
                                    auth_failed = True
                                    yield {
                                        "type": "error",
                                        "message": f"{block.text.strip()} — {SDK_SETUP_HINT}",
                                    }
                                    continue
                                yield {"type": "text", "delta": block.text}
                            elif isinstance(block, ThinkingBlock):
                                thought = getattr(block, "thinking", "")
                                if thought:
                                    yield {"type": "thinking", "delta": thought}
                            elif isinstance(block, ToolUseBlock):
                                tool_calls += 1
                                names[block.id] = block.name
                                yield {
                                    "type": "tool_call",
                                    "id": block.id,
                                    "name": _short_name(block.name),
                                    "input": block.input or {},
                                }
                                event = _sdk_report_event(block.name, block.input or {})
                                if event:
                                    yield event
                    else:
                        for block in getattr(message, "content", None) or []:
                            if isinstance(block, ToolResultBlock):
                                yield {
                                    "type": "tool_result",
                                    "id": block.tool_use_id,
                                    "name": _short_name(
                                        names.get(block.tool_use_id, "tool")
                                    ),
                                    "ok": not bool(getattr(block, "is_error", False)),
                                    "duration_ms": 0,
                                    "truncated": False,
                                    "preview": _preview(_sdk_text(block.content)),
                                }

                    if isinstance(message, ResultMessage):
                        if auth_failed:
                            yield {
                                "type": "done",
                                "usage": {},
                                "turns": 0,
                                "tool_calls": 0,
                                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                                "note": "backend not authenticated",
                            }
                            return
                        usage = getattr(message, "usage", None) or {}
                        yield {
                            "type": "done",
                            "usage": {
                                "input_tokens": (usage or {}).get("input_tokens", 0),
                                "output_tokens": (usage or {}).get("output_tokens", 0),
                                "cost_usd": getattr(message, "total_cost_usd", None),
                            },
                            "turns": getattr(message, "num_turns", 0),
                            "tool_calls": tool_calls,
                            "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        }
                        return

            yield {
                "type": "done",
                "usage": {},
                "turns": 0,
                "tool_calls": tool_calls,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


# The claude CLI reports auth and entitlement problems as ordinary assistant
# text ("Not logged in · Please run /login"), which would otherwise be rendered
# as a legitimate answer. These are matched so they surface as errors with a fix.
AUTH_FAILURE_MARKERS = (
    "not logged in",
    "please run /login",
    "invalid api key",
    "authentication_error",
    "credit balance is too low",
    "oauth token has expired",
    "unauthorized",
)

SDK_SETUP_HINT = (
    "The Claude Agent SDK backend needs a logged-in Claude Code CLI — being signed "
    "in to the Claude desktop app is not enough, since the SDK spawns the CLI, which "
    "has its own login. Run `claude` in this directory, complete /login, and accept "
    "the workspace trust dialog (untrusted workspaces silently drop the permission "
    "allowlist). Alternatively set ANTHROPIC_API_KEY to use the Anthropic API backend."
)


def _looks_like_auth_failure(text: str) -> bool:
    low = (text or "").strip().lower()
    # Only short, standalone messages — a real answer that happens to quote one
    # of these phrases would be much longer.
    return len(low) < 200 and any(m in low for m in AUTH_FAILURE_MARKERS)


def _short_name(name: str) -> str:
    """mcp__observations__find_cadence_violations -> find_cadence_violations"""
    return name.split("__")[-1] if name.startswith("mcp__") else name


def _sdk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _sdk_report_event(name: str, tool_input: dict) -> dict | None:
    if name != "Write":
        return None
    path = str(tool_input.get("file_path") or "")
    if "outputs/" not in path:
        return None
    rel = "outputs/" + path.split("outputs/", 1)[1]
    return {"type": "report", "path": rel, "title": Path(rel).stem.replace("-", " ").title()}


def _flatten_history(history: list[dict]) -> str:
    lines = ["Earlier in this conversation:"]
    for turn in history[-6:]:
        content = turn.get("content")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        text = str(content or "").strip()
        if text:
            lines.append(f"{turn.get('role', 'user')}: {text[:1500]}")
    return "\n".join(lines)


def make_backend(
    preferred: str | None = None, model: str | None = None, max_turns: int | None = None
):
    chosen = resolve_backend(preferred)
    if chosen == "api":
        return ApiBackend(model=model, max_turns=max_turns)
    return AgentSdkBackend(model=model, max_turns=max_turns)
