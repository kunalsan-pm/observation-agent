"""
Agent-loop integration tests, with the model replaced by a script.

The point is to verify the plumbing that sits between the model and the data —
schema translation, tool dispatch into the real MCP server, tool_result
construction, report writing, and the event stream the UI renders — without
needing model credentials. The fake client returns a fixed sequence of
tool_use / text turns; everything downstream of it is the real code path,
including a live stdio connection to the real MCP server.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from agent import reports
from agent.backends import ApiBackend
from agent.mcp_bridge import ObservationTools, _clean_schema


# ---------------------------------------------------------------------------
# A scripted stand-in for anthropic.AsyncAnthropic
# ---------------------------------------------------------------------------


class _Delta:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Usage:
    input_tokens = 1000
    output_tokens = 200


class _Final:
    def __init__(self, stop_reason):
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _FakeStream:
    def __init__(self, turn):
        self._turn = turn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for item in self._turn["blocks"]:
            if item["kind"] == "text":
                yield _Event("content_block_start", content_block=_Delta(type="text"))
                yield _Event(
                    "content_block_delta", delta=_Delta(type="text_delta", text=item["text"])
                )
                yield _Event("content_block_stop")
            else:
                yield _Event(
                    "content_block_start",
                    content_block=_Delta(type="tool_use", id=item["id"], name=item["name"]),
                )
                yield _Event(
                    "content_block_delta",
                    delta=_Delta(
                        type="input_json_delta", partial_json=json.dumps(item["input"])
                    ),
                )
                yield _Event("content_block_stop")

    async def get_final_message(self):
        return _Final(self._turn["stop_reason"])


class _FakeMessages:
    def __init__(self, script, recorder):
        self._script = script
        self._recorder = recorder

    def stream(self, **kwargs):
        # Deep-copy: the loop mutates `messages` in place, so storing the
        # reference would make every recorded call show the final state.
        self._recorder["calls"].append(copy.deepcopy(kwargs))
        return _FakeStream(self._script.pop(0))


class FakeAnthropic:
    """Replays a scripted conversation. `seen` records what the loop sent."""

    def __init__(self, script, recorder):
        self.messages = _FakeMessages(script, recorder)


def run_backend(monkeypatch, script, question="test question"):
    recorder = {"calls": []}
    import anthropic

    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda *a, **k: FakeAnthropic(script, recorder)
    )

    async def collect():
        events = []
        async for event in ApiBackend(model="fake-model").run(question):
            events.append(event)
        return events

    return asyncio.run(collect()), recorder


# ---------------------------------------------------------------------------
# Schema translation
# ---------------------------------------------------------------------------


def test_optional_params_collapse_to_a_concrete_type():
    """FastMCP emits anyOf:[{type:X},{type:'null'}] for optionals; the
    Anthropic tools API is happier with the plain type."""
    cleaned = _clean_schema(
        {
            "type": "object",
            "properties": {
                "org_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "limit": {"type": "integer", "default": 50},
            },
            "$defs": {"junk": {}},
        }
    )
    assert cleaned["properties"]["org_id"]["type"] == "string"
    assert "anyOf" not in cleaned["properties"]["org_id"]
    assert "default" not in cleaned["properties"]["org_id"]
    assert cleaned["properties"]["limit"]["default"] == 50
    assert "$defs" not in cleaned


def test_every_mcp_tool_survives_translation():
    async def check():
        async with ObservationTools() as tools:
            assert len(tools.schemas) >= 20
            for schema in tools.schemas:
                assert schema["name"]
                assert schema["description"], f"{schema['name']} has no description"
                assert schema["input_schema"]["type"] == "object"
                json.dumps(schema)  # must be serialisable for the API

    asyncio.run(check())


# ---------------------------------------------------------------------------
# Tool dispatch against the real server
# ---------------------------------------------------------------------------


def test_bridge_dispatches_to_the_real_server():
    async def check():
        async with ObservationTools() as tools:
            result = await tools.call("find_cadence_violations", {})
            assert result["ok"]
            data = result["data"]
            assert data["overdue_count"] == 1
            assert data["overdue"][0]["subject"]["user_id"] == "USR-042"

    asyncio.run(check())


def test_bridge_returns_failures_as_content_not_exceptions():
    """A tool error has to reach the model as text so it can recover."""

    async def check():
        async with ObservationTools() as tools:
            result = await tools.call("get_observation", {"observation_id": "OBS-999999"})
            # Not found is a normal payload, not a transport failure.
            assert result["ok"]
            assert "not found" in json.dumps(result["data"]).lower()

            bad = await tools.call("no_such_tool", {})
            assert bad["ok"] is False
            assert "no_such_tool" in bad["content"]

    asyncio.run(check())


# ---------------------------------------------------------------------------
# The loop end to end
# ---------------------------------------------------------------------------


def test_loop_calls_a_tool_then_answers(monkeypatch):
    script = [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {"kind": "text", "text": "Checking cadence.\n"},
                {
                    "kind": "tool",
                    "id": "tu_1",
                    "name": "find_cadence_violations",
                    "input": {},
                },
            ],
        },
        {
            "stop_reason": "end_turn",
            "blocks": [{"kind": "text", "text": "One associate is overdue: USR-042."}],
        },
    ]
    events, recorder = run_backend(monkeypatch, script)
    kinds = [e["type"] for e in events]

    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "tool_call" in kinds and "tool_result" in kinds
    # Ordering matters for the UI: the call renders before its result.
    assert kinds.index("tool_call") < kinds.index("tool_result")

    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "find_cadence_violations"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] and result["id"] == call["id"]
    assert "USR-042" in result["preview"]

    done = events[-1]
    assert done["tool_calls"] == 1
    assert done["turns"] == 2
    assert done["usage"]["input_tokens"] == 2000  # accumulated across both turns

    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "USR-042" in text


def test_loop_feeds_tool_results_back_as_a_user_turn(monkeypatch):
    """The second request must carry the assistant's tool_use plus a matching
    tool_result, or the model has no idea what came back."""
    script = [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {"kind": "tool", "id": "tu_9", "name": "list_organizations", "input": {}}
            ],
        },
        {"stop_reason": "end_turn", "blocks": [{"kind": "text", "text": "Three orgs."}]},
    ]
    _, recorder = run_backend(monkeypatch, script)
    assert len(recorder["calls"]) == 2

    second = recorder["calls"][1]["messages"]
    assistant = second[-2]
    user = second[-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0]["type"] == "tool_use"
    assert user["role"] == "user"
    assert user["content"][0]["type"] == "tool_result"
    assert user["content"][0]["tool_use_id"] == "tu_9"
    assert "Acme Logistics" in user["content"][0]["content"]

    # The system prompt and the full tool surface go on every request.
    assert "2026-05-12" in recorder["calls"][0]["system"]
    names = {t["name"] for t in recorder["calls"][0]["tools"]}
    assert "find_critical_failures" in names
    assert "write_report" in names


def test_write_report_produces_a_file_and_a_report_event(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "OUTPUT_DIR", tmp_path)
    script = [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "kind": "tool",
                    "id": "tu_r",
                    "name": "write_report",
                    "input": {
                        "filename": "q09-cadence.md",
                        "title": "Cadence breaches",
                        "markdown": "## Answer\nUSR-042 is 14.5 days past due.",
                    },
                }
            ],
        },
        {"stop_reason": "end_turn", "blocks": [{"kind": "text", "text": "Written."}]},
    ]
    events, _ = run_backend(monkeypatch, script)

    report = next(e for e in events if e["type"] == "report")
    assert report["path"] == "outputs/q09-cadence.md"

    written = (tmp_path / "q09-cadence.md").read_text()
    assert written.startswith("# Cadence breaches")
    assert "USR-042" in written
    assert "data as of 2026-05-12" in written


def test_loop_surfaces_a_model_error_and_stops(monkeypatch):
    import anthropic

    class Boom:
        class messages:
            @staticmethod
            def stream(**kw):
                raise RuntimeError("upstream exploded")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda *a, **k: Boom())

    async def collect():
        return [e async for e in ApiBackend().run("q")]

    events = asyncio.run(collect())
    assert events[-1]["type"] == "error"
    assert "upstream exploded" in events[-1]["message"]


def test_turn_limit_terminates_a_runaway_loop(monkeypatch):
    """A model that only ever calls tools must still terminate."""
    script = [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {"kind": "tool", "id": f"tu_{i}", "name": "list_organizations", "input": {}}
            ],
        }
        for i in range(4)
    ]
    recorder = {"calls": []}
    import anthropic

    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda *a, **k: FakeAnthropic(script, recorder)
    )

    async def collect():
        return [e async for e in ApiBackend(max_turns=3).run("q")]

    events = asyncio.run(collect())
    done = events[-1]
    assert done["type"] == "done"
    assert done["turns"] == 3
    assert "3-turn limit" in done["note"]


# ---------------------------------------------------------------------------
# Report writing safety
# ---------------------------------------------------------------------------


def test_report_paths_cannot_escape_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "OUTPUT_DIR", tmp_path)
    out = reports.write_report("../../etc/passwd", "nope")
    assert out["ok"]
    assert out["path"] == "outputs/passwd.md"
    assert (tmp_path / "passwd.md").exists()
    assert not (tmp_path.parent.parent / "etc" / "passwd").exists()


def test_report_filenames_are_slugged(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "OUTPUT_DIR", tmp_path)
    assert reports.write_report("My Report!.md", "x")["path"] == "outputs/My-Report-.md"
    assert reports.write_report("noext", "x")["path"] == "outputs/noext.md"


def test_report_listing_reads_titles_back(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "OUTPUT_DIR", tmp_path)
    reports.write_report("a.md", "# Critical failures\n\nbody")
    listed = reports.list_reports()
    assert listed[0]["title"] == "Critical failures"
    assert listed[0]["path"] == "outputs/a.md"
