"""
Command-line runner for the observation agent.

    python -m agent.runner "Which observers in PRJ-3 ..."
    python -m agent.runner --question-set demo          # sweep the 11 demo questions
    python -m agent.runner --list-backends

Same event stream the web UI consumes, rendered to a terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from agent.backends import available_backends, make_backend
from agent.questions import DEMO_QUESTIONS, question_by_number

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _c(text: str, colour: str, plain: bool) -> str:
    return text if plain else f"{colour}{text}{RESET}"


async def ask(
    question: str,
    backend_name: str | None = None,
    model: str | None = None,
    plain: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    backend = make_backend(backend_name, model=model)
    summary: dict[str, Any] = {"question": question, "reports": [], "tool_calls": []}
    at_line_start = True

    async for event in backend.run(question):
        kind = event.get("type")

        if as_json:
            print(json.dumps(event, default=str), flush=True)
        elif kind == "start":
            print(
                _c(f"▸ {event['backend']} backend · {event['model']}\n", DIM, plain),
                flush=True,
            )
        elif kind == "text":
            sys.stdout.write(event["delta"])
            sys.stdout.flush()
            at_line_start = event["delta"].endswith("\n")
        elif kind == "tool_call":
            if not at_line_start:
                print()
            args = ", ".join(
                f"{k}={v!r}" for k, v in (event.get("input") or {}).items() if v is not None
            )
            if len(args) > 130:
                args = args[:130] + "…"
            print(_c(f"  ⚙ {event['name']}({args})", CYAN, plain), flush=True)
            at_line_start = True
        elif kind == "tool_result":
            mark = "✓" if event.get("ok") else "✗"
            colour = GREEN if event.get("ok") else RED
            ms = event.get("duration_ms") or 0
            print(
                _c(f"    {mark} {ms}ms", colour, plain)
                + _c(f"  {event.get('preview','')[:150]}", DIM, plain),
                flush=True,
            )
        elif kind == "report":
            print(_c(f"  📄 wrote {event['path']}", YELLOW, plain), flush=True)
        elif kind == "error":
            print(_c(f"\n✗ {event['message']}", RED, plain), file=sys.stderr, flush=True)
            summary["error"] = event["message"]
        elif kind == "done":
            usage = event.get("usage") or {}
            bits = [
                f"{event.get('turns', 0)} turns",
                f"{event.get('tool_calls', 0)} tool calls",
                f"{(event.get('elapsed_ms') or 0) / 1000:.1f}s",
            ]
            if usage.get("input_tokens"):
                bits.append(f"{usage['input_tokens']}in/{usage.get('output_tokens', 0)}out tok")
            if usage.get("cost_usd"):
                bits.append(f"${usage['cost_usd']:.4f}")
            print(_c(f"\n▪ {' · '.join(bits)}\n", DIM, plain), flush=True)

        if kind == "report":
            summary["reports"].append(event["path"])
        elif kind == "tool_call":
            summary["tool_calls"].append(event["name"])
        elif kind == "done":
            summary.update(
                {
                    "turns": event.get("turns"),
                    "elapsed_ms": event.get("elapsed_ms"),
                    "usage": event.get("usage"),
                }
            )

    return summary


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Ask the observation agent a question.")
    parser.add_argument("question", nargs="*", help="The question to ask.")
    parser.add_argument("--backend", choices=["auto", "api", "sdk"], default="auto")
    parser.add_argument("--model", default=None)
    parser.add_argument("--json", action="store_true", help="Emit raw events as JSON lines.")
    parser.add_argument("--plain", action="store_true", help="Disable colour.")
    parser.add_argument("--list-backends", action="store_true")
    parser.add_argument(
        "--question-set",
        choices=["demo"],
        help="Run the built-in demo questions in sequence.",
    )
    parser.add_argument(
        "--only",
        help="With --question-set, restrict to these numbers, e.g. 1,4,7",
    )
    args = parser.parse_args()

    if args.list_backends:
        for name, info in available_backends().items():
            mark = "✓" if info["available"] else "·"
            print(f" {mark} {name:4s} {info['label']:20s} {info['reason']}")
        return 0

    if args.question_set:
        wanted = (
            [int(n) for n in args.only.split(",")]
            if args.only
            else [q["number"] for q in DEMO_QUESTIONS]
        )
        for number in wanted:
            spec = question_by_number(number)
            if not spec:
                print(f"no question {number}", file=sys.stderr)
                continue
            header = f"Q{spec['number']} · {spec['group']}"
            print(f"\n{'=' * 78}\n{header}\n{spec['question']}\n{'=' * 78}")
            await ask(
                spec["question"],
                backend_name=args.backend,
                model=args.model,
                plain=args.plain,
                as_json=args.json,
            )
        return 0

    if not args.question:
        parser.error("give a question, or use --question-set / --list-backends")

    result = await ask(
        " ".join(args.question),
        backend_name=args.backend,
        model=args.model,
        plain=args.plain,
        as_json=args.json,
    )
    return 1 if result.get("error") else 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
