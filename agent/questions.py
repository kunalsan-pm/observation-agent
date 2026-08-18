"""
The eleven exercise questions, in the three groups from the brief.

Kept in code rather than prose so the UI can offer them as one-click prompts
and the CLI can run them as a regression set.
"""

from __future__ import annotations

EXERCISE_QUESTIONS: list[dict] = [
    # --- Lookups -----------------------------------------------------------
    {
        "number": 1,
        "group": "Lookups",
        "short": "Organizations and industries",
        "question": "How many organizations are in the system, and what industries do they cover?",
    },
    {
        "number": 2,
        "group": "Lookups",
        "short": "ORG-AAA project policies",
        "question": "What cadence policy and score threshold does each project under ORG-AAA use?",
    },
    {
        "number": 3,
        "group": "Lookups",
        "short": "Managers in PRJ-1",
        "question": "Who are the managers responsible for associates in PRJ-1?",
    },
    # --- Connections -------------------------------------------------------
    {
        "number": 4,
        "group": "Connections",
        "short": "Everything about OBS-000042",
        "question": (
            "Show me everything about observation OBS-000042: who was observed, by whom, "
            "when, the overall score, and any coach notes attached to it."
        ),
    },
    {
        "number": 5,
        "group": "Connections",
        "short": "Busiest PRJ-3 observers",
        "question": "Which observers in PRJ-3 have completed the most observations in the last 30 days?",
    },
    # --- Patterns ----------------------------------------------------------
    {
        "number": 6,
        "group": "Patterns",
        "short": "Sustained low topic scores",
        "question": (
            "In ORG-AAA, which associates have a sustained pattern of low scoring on a "
            "specific topic over their last several observations? Pick a reasonable "
            "definition of 'sustained' and defend it, and back the answer with specific "
            "observation IDs."
        ),
    },
    {
        "number": 7,
        "group": "Patterns",
        "short": "Critical-question failures",
        "question": (
            "Across the whole dataset, are there any recent critical-question failures "
            "(i.e. a critical-question score that should worry a safety manager)? List the "
            "associate, the question, the observation, and the date."
        ),
    },
    {
        "number": 8,
        "group": "Patterns",
        "short": "Recurring concerns in notes",
        "question": (
            "In ORG-CCC, find any associates whose coach notes suggest a recurring concern "
            "— not a single bad day, but a pattern. Cite the specific notes."
        ),
    },
    {
        "number": 9,
        "group": "Patterns",
        "short": "Overdue against cadence",
        "question": (
            "Which associates haven't been observed in longer than their project's cadence "
            "policy allows, and how far past due are they?"
        ),
    },
    {
        "number": 10,
        "group": "Patterns",
        "short": "Decline vs own baseline",
        "question": (
            "Are there any associates whose recent observation scores have declined "
            "meaningfully compared to their own earlier baseline (not the cohort)? Be "
            "specific about what 'meaningfully' means."
        ),
    },
    {
        "number": 11,
        "group": "Patterns",
        "short": "Coaching proposal",
        "question": (
            "Pick one flagged associate from question 6, 7, 8, 9, or 10 and draft a short "
            "coaching-action proposal for their manager — what the pattern is, the evidence "
            "(observation IDs, scores, note IDs), and a concrete next step. Write it to outputs/."
        ),
    },
]

GROUPS = ["Lookups", "Connections", "Patterns"]


def question_by_number(number: int) -> dict | None:
    return next((q for q in EXERCISE_QUESTIONS if q["number"] == int(number)), None)


def grouped() -> dict[str, list[dict]]:
    return {g: [q for q in EXERCISE_QUESTIONS if q["group"] == g] for g in GROUPS}
