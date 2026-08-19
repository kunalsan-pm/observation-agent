"""
The built-in demo question set.

Eleven questions spanning the three shapes this agent has to handle, used by the
web UI's question library and by ``agent.runner --question-set demo`` as a
smoke-test sweep.

They are phrased the way a manager or safety lead would actually ask, and they
deliberately carry **no** instructions about method: nothing here says "defend
your definition" or "cite observation ids". Those are the agent's standing
obligations from its system prompt, so leaving them out of the question is the
more honest test — an answer that states its own rule unprompted is evidence the
agent works, not evidence the question was leading.
"""

from __future__ import annotations

DEMO_QUESTIONS: list[dict] = [
    # --- Lookups: single-hop reads off the catalog ------------------------
    {
        "number": 1,
        "group": "Lookups",
        "short": "Organizations and industries",
        "question": "How many organizations are on the platform, and what industries do they operate in?",
    },
    {
        "number": 2,
        "group": "Lookups",
        "short": "ORG-AAA project policies",
        "question": "What observation cadence and score threshold is each ORG-AAA project running?",
    },
    {
        "number": 3,
        "group": "Lookups",
        "short": "Managers in PRJ-1",
        "question": "Who manages the associates in PRJ-1?",
    },
    # --- Connections: joins across records --------------------------------
    {
        "number": 4,
        "group": "Connections",
        "short": "Everything about OBS-000042",
        "question": (
            "Give me the full picture on observation OBS-000042 — who was observed, "
            "by whom, when, how it scored, and anything a coach wrote about it."
        ),
    },
    {
        "number": 5,
        "group": "Connections",
        "short": "Busiest PRJ-3 observers",
        "question": "Who are the busiest observers in PRJ-3 over the past 30 days?",
    },
    # --- Patterns: rules over many records --------------------------------
    {
        "number": 6,
        "group": "Patterns",
        "short": "Sustained low topic scores",
        "question": (
            "In ORG-AAA, is anyone repeatedly scoring low on one particular topic "
            "across their recent observations?"
        ),
    },
    {
        "number": 7,
        "group": "Patterns",
        "short": "Critical-question failures",
        "question": (
            "Have there been any recent critical-question failures a safety manager "
            "should know about? I want the associate, the question, the observation "
            "and the date."
        ),
    },
    {
        "number": 8,
        "group": "Patterns",
        "short": "Recurring concerns in notes",
        "question": (
            "In ORG-CCC, do any coach notes point to a concern that keeps coming back "
            "rather than a one-off bad day?"
        ),
    },
    {
        "number": 9,
        "group": "Patterns",
        "short": "Overdue against cadence",
        "question": (
            "Who is overdue for an observation against their project's cadence, and "
            "by how long?"
        ),
    },
    {
        "number": 10,
        "group": "Patterns",
        "short": "Decline vs own baseline",
        "question": (
            "Is anyone's recent scoring meaningfully worse than their own earlier "
            "baseline — measured against themselves, not against their peers?"
        ),
    },
    {
        "number": 11,
        "group": "Patterns",
        "short": "Coaching proposal",
        "question": (
            "Take the most concerning associate you can find and draft a short "
            "coaching-action proposal for their manager: what the pattern is, the "
            "evidence behind it, and one concrete next step."
        ),
    },
]

GROUPS = ["Lookups", "Connections", "Patterns"]


def question_by_number(number: int) -> dict | None:
    return next((q for q in DEMO_QUESTIONS if q["number"] == int(number)), None)


def grouped() -> dict[str, list[dict]]:
    return {g: [q for q in DEMO_QUESTIONS if q["group"] == g] for g in GROUPS}
