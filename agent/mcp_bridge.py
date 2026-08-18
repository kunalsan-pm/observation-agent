"""
Bridge between the MCP server and the Anthropic tool-use API.

Connects to ``mcp_server/server.py`` over stdio with the FastMCP client,
translates each MCP tool's JSON schema into an Anthropic tool definition, and
dispatches tool calls back. The direct-API backend needs this; the Claude Agent
SDK backend talks to the MCP server itself and does not.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = REPO_ROOT / "mcp_server" / "server.py"

# Cap on a single tool result placed into context. The tools are designed to
# aggregate rather than dump, so this should not normally bite; it is a
# backstop against a pathological filter-free call.
MAX_RESULT_CHARS = 120_000


class ObservationTools:
    """An MCP session plus the Anthropic-shaped tool schemas for it."""

    def __init__(self, server_script: Path | str = SERVER_SCRIPT) -> None:
        self._client = Client(str(server_script))
        self._schemas: list[dict[str, Any]] = []
        self._names: set[str] = set()

    async def __aenter__(self) -> "ObservationTools":
        await self._client.__aenter__()
        tools = await self._client.list_tools()
        self._schemas = [
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "input_schema": _clean_schema(t.inputSchema),
            }
            for t in tools
        ]
        self._names = {t["name"] for t in self._schemas}
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.__aexit__(*exc)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def has(self, name: str) -> bool:
        return name in self._names

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one tool call. Never raises — a failure is returned as
        content so the model can see it and recover."""
        started = time.perf_counter()
        try:
            result = await self._client.call_tool(name, arguments or {})
            payload = _extract(result)
            text = _stringify(payload)
            truncated = False
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + "\n...[truncated: narrow the filter]"
                truncated = True
            return {
                "ok": True,
                "content": text,
                "data": payload,
                "truncated": truncated,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the model
            return {
                "ok": False,
                "content": f"Tool '{name}' failed: {type(exc).__name__}: {exc}",
                "data": None,
                "truncated": False,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }


def _clean_schema(schema: dict | None) -> dict[str, Any]:
    """Normalise a FastMCP input schema for the Anthropic tools API.

    FastMCP emits ``anyOf: [{type: X}, {type: 'null'}]`` for optional
    parameters. That is valid JSON Schema but noisy, so it is collapsed to the
    non-null branch with the optionality carried by ``required`` instead.
    """
    if not schema:
        return {"type": "object", "properties": {}}
    out = json.loads(json.dumps(schema))
    out.pop("$defs", None)
    out.pop("additionalProperties", None)
    for prop in (out.get("properties") or {}).values():
        variants = prop.pop("anyOf", None)
        if variants:
            concrete = [v for v in variants if v.get("type") != "null"]
            if len(concrete) == 1:
                prop.update(concrete[0])
            elif concrete:
                prop["anyOf"] = concrete
        prop.pop("$ref", None)
        if "default" in prop and prop["default"] is None:
            prop.pop("default")
    return out


def _extract(result: Any) -> Any:
    """Pull the useful payload out of a FastMCP CallToolResult."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured:
        return structured.get("result", structured)
    blocks = getattr(result, "content", None) or []
    texts = [getattr(b, "text", None) for b in blocks]
    texts = [t for t in texts if t]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (json.JSONDecodeError, TypeError):
            return texts[0]
    return texts or None


def _stringify(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=None, separators=(",", ":"), default=str)


async def describe_tools() -> None:
    """CLI helper: print the tool surface the agent will see."""
    async with ObservationTools() as tools:
        print(f"{len(tools.schemas)} tools\n")
        for schema in sorted(tools.schemas, key=lambda s: s["name"]):
            params = list((schema["input_schema"].get("properties") or {}))
            print(f"  {schema['name']}({', '.join(params)})")


if __name__ == "__main__":
    import asyncio

    if "--json" in sys.argv:

        async def dump() -> None:
            async with ObservationTools() as tools:
                print(json.dumps(tools.schemas, indent=2))

        asyncio.run(dump())
    else:
        asyncio.run(describe_tools())
