"""
FastAPI app for the Observation Agent UI.

Local-only by design: it binds to 127.0.0.1 and has no auth, because it drives
an agent with the caller's own credentials.

The single interesting endpoint is ``POST /api/ask``, which streams the agent's
event protocol to the browser over SSE. The UI renders the tool calls as they
happen rather than only the final prose — an agent whose reasoning path is
visible is one you can actually check.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mcp_server"))

from agent import reports  # noqa: E402
from agent.backends import available_backends, make_backend  # noqa: E402
from agent.questions import EXERCISE_QUESTIONS, grouped  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Observation Agent", docs_url="/api/docs")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    backend: str | None = None
    model: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/api/config")
async def config() -> dict[str, Any]:
    """Everything the UI needs to render its shell in one call."""
    backends = available_backends()
    runnable = [name for name, info in backends.items() if info["available"]]

    dataset: dict[str, Any] = {}
    try:
        import queries

        described = queries.describe_dataset()
        dataset = {
            "today": described["today"],
            "counts": described["counts"],
            "date_range": described["observation_date_range"],
            "projects": [
                {
                    "project_id": p["project_id"],
                    "name": p["name"],
                    "org_id": p["org_id"],
                    "cadence_days": p["cadence_days"],
                    "score_threshold": p["score_threshold"],
                    "observations": p["observations"],
                    "associates": p["associates"],
                }
                for p in described["projects"]
            ],
        }
    except Exception as exc:  # noqa: BLE001 - the UI should still load
        dataset = {"error": str(exc)}

    return {
        "backends": backends,
        "runnable": runnable,
        "default_backend": runnable[0] if runnable else None,
        "questions": grouped(),
        "question_count": len(EXERCISE_QUESTIONS),
        "dataset": dataset,
        "reports": reports.list_reports(),
    }


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/api/ask")
async def ask(request: AskRequest) -> StreamingResponse:
    """Run the agent and stream its events as SSE."""

    async def stream() -> AsyncIterator[str]:
        try:
            backend = make_backend(request.backend, model=request.model)
        except RuntimeError as exc:
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done", "usage": {}, "turns": 0, "tool_calls": 0})
            return

        try:
            async for event in backend.run(request.question, history=request.history):
                yield _sse(event)
        except asyncio.CancelledError:  # browser navigated away
            raise
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            yield _sse({"type": "done", "usage": {}, "turns": 0, "tool_calls": 0})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/reports")
async def list_reports() -> dict[str, Any]:
    return {"reports": reports.list_reports()}


@app.get("/api/reports/{name}")
async def get_report(name: str) -> JSONResponse:
    body = reports.read_report(name)
    if body is None:
        raise HTTPException(status_code=404, detail="report not found")
    return JSONResponse({"name": name, "markdown": body})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    backends = available_backends()
    return {
        "ok": True,
        "backends_available": [n for n, i in backends.items() if i["available"]],
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("OBSERVATION_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("OBSERVATION_AGENT_PORT", "8080"))

    backends = available_backends()
    runnable = [n for n, i in backends.items() if i["available"]]
    print("\n  Observation Agent")
    print(f"  → http://{host}:{port}\n")
    for name, info in backends.items():
        print(f"    {'✓' if info['available'] else '·'} {info['label']:20s} {info['reason']}")
    if not runnable:
        print(
            "\n  ⚠ No model backend is available, so the UI will load but cannot answer.\n"
            "    Fix either one:\n"
            "      • export ANTHROPIC_API_KEY=sk-ant-...   (Anthropic API backend)\n"
            "      • run `claude` in a terminal and /login  (Claude Agent SDK backend)\n"
        )
    print()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
