"""
Observations MCP server.

A read-only tool surface over the observation-platform fixture data, in four
layers. The layering is the point: an agent should be able to answer a lookup
with one cheap call and a pattern question with one analytical call, without
ever pulling the 9,000-row response table into its context.

  1. Catalog      — orgs, projects, users, templates, sites. Small, exact.
  2. Retrieval    — filtered, paginated observations / responses / notes, plus
                    fully hydrated single-record reads.
  3. Aggregation  — group-and-count in pandas; only summary rows travel.
  4. Detectors    — the pattern rules (sustained low scores, critical-question
                    failures, cadence breaches, self-baseline decline,
                    recurring note concerns), each returning both its findings
                    and the rule it applied.

Tool docstrings are the agent's only documentation for choosing between these,
so they state what a tool is *for* and which defaults it applies.

Run:
    python server.py                   # stdio (used by Claude Code)
    python server.py --transport http  # http on :8000 for ad-hoc inspection
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp import FastMCP

import analytics
import dataset as ds
import queries

mcp = FastMCP(
    name="observations",
    instructions=(
        "Read-only access to a multi-org safety/quality observation platform: "
        "organizations, projects (each with its own cadence_days and "
        "score_threshold policy), users (associates / observers / managers), "
        "observation headers, question-level responses (the only place "
        "is_critical lives), free-text coach notes, and templates.\n\n"
        f"Today is {ds.today_date()} — the fixture data is anchored to that "
        "date, so every 'recent', 'last N days' and 'past due' calculation is "
        "measured from it, not from the wall clock.\n\n"
        "Start with describe_dataset() to orient. Prefer the aggregation and "
        "detector tools over pulling rows: they compute server-side and return "
        "both their findings and the rule they applied, so an answer can cite "
        "its own definition. Every list tool reports total_matching, so a "
        "truncated page is never mistaken for a complete result."
    ),
)


# ===========================================================================
# 0. Orientation
# ===========================================================================


@mcp.tool
def describe_dataset() -> dict:
    """Shape of the dataset and the traps in it. Cheap; call this first.

    Returns row counts, the date range, today's anchor date, every project with
    its cadence/threshold policy, the topic list, the critical questions, score
    distributions, and a `gotchas` list covering the id collisions and the
    denormalisation quirks that otherwise cause wrong answers.
    """
    return queries.describe_dataset()


@mcp.tool
def resolve_entity(entity_id: str) -> dict:
    """Identify what an id refers to and return that record.

    Use when an id is ambiguous or its type is unknown. Chiefly for `OBS-`
    ids, which name an **observer** at three digits (`OBS-002`) and an
    **observation** at six (`OBS-000042`).
    """
    return queries.resolve_entity(entity_id)


# ===========================================================================
# 1. Catalog
# ===========================================================================


@mcp.tool
def list_organizations() -> list[dict]:
    """All organizations: org_id, name, industry. Three records."""
    return ds.ORGS


@mcp.tool
def list_projects(org_id: str | None = None, include_stats: bool = False) -> list[dict]:
    """Projects with their governing policy, optionally filtered to one org.

    Each project sets `cadence_days` (how often its associates must be
    observed) and `score_threshold` (the score floor its coaching rules use),
    plus the resolved `template_id`. These policy values are what any "past
    due" or "below threshold" judgement must be measured against — they differ
    per project, so a single global number is always wrong.

    `include_stats` adds observation and associate counts.
    """
    projects = [
        ds.project_policy(p["project_id"])
        for p in ds.PROJECTS
        if org_id is None or p["org_id"] == org_id
    ]
    if include_stats:
        for p in projects:
            pid = p["project_id"]
            p["observations"] = int((ds.OBS_DF["project_id"] == pid).sum())
            p["associates"] = sum(
                1 for u in ds.USERS if u["role"] == "associate" and u["project_id"] == pid
            )
    return projects


@mcp.tool
def list_users(
    org_id: str | None = None,
    project_id: str | None = None,
    role: str | None = None,
    manager_id: str | None = None,
    include_report_counts: bool = False,
) -> dict:
    """Users, filterable by org / project / role / manager.

    Roles are `associate`, `observer`, `manager`. Managers own associates via
    the associate's `manager_id`; observers sit outside that hierarchy and have
    `manager_id = null`, so "who manages the people in project X" means the
    managers named by that project's associates — not everyone with the
    manager role.

    `include_report_counts` adds each manager's direct-report count and roster.
    """
    out = [
        u
        for u in ds.USERS
        if (org_id is None or u["org_id"] == org_id)
        and (project_id is None or u["project_id"] == project_id)
        and (role is None or u["role"] == role)
        and (manager_id is None or u.get("manager_id") == manager_id)
    ]
    items: list[dict] = []
    for u in out:
        rec = dict(u)
        if include_report_counts and u["role"] == "manager":
            reports = [
                a["user_id"]
                for a in ds.USERS
                if a.get("manager_id") == u["user_id"] and a["role"] == "associate"
            ]
            rec["direct_report_count"] = len(reports)
            rec["direct_report_ids"] = reports
        items.append(rec)
    return {
        "total_matching": len(items),
        "filters": {
            "org_id": org_id,
            "project_id": project_id,
            "role": role,
            "manager_id": manager_id,
        },
        "items": items,
    }


@mcp.tool
def get_user(user_id: str, include_activity: bool = True) -> dict:
    """One user, with their manager resolved and (by default) an activity
    summary: observations as subject or observer, mean score, last activity."""
    u = ds.USERS_BY_ID.get(user_id)
    if not u:
        return {"error": f"user not found: {user_id}", "code": "not_found"}

    out: dict[str, Any] = dict(u)
    out["manager"] = ds.user_brief(u.get("manager_id"))
    out["project"] = ds.project_policy(u.get("project_id"))
    if not include_activity:
        return out

    as_subject = ds.OBS_DF[ds.OBS_DF["subject_user_id"] == user_id]
    as_observer = ds.OBS_DF[ds.OBS_DF["observer_user_id"] == user_id]
    if not as_subject.empty:
        out["as_subject"] = {
            "observations": int(len(as_subject)),
            "mean_overall_score": ds.r2(as_subject["overall_score"].mean()),
            "last_observed": ds.day(as_subject["completed_dt"].max()),
            "days_since_last": ds.days_between(as_subject["completed_dt"].max(), ds.today()),
        }
    if not as_observer.empty:
        out["as_observer"] = {
            "observations_completed": int(len(as_observer)),
            "mean_score_given": ds.r2(as_observer["overall_score"].mean()),
            "distinct_subjects": int(as_observer["subject_user_id"].nunique()),
            "last_active": ds.day(as_observer["completed_dt"].max()),
        }
    if u["role"] == "manager":
        reports = [
            a["user_id"] for a in ds.USERS if a.get("manager_id") == user_id
        ]
        out["direct_reports"] = {"count": len(reports), "user_ids": reports}
    return out


@mcp.tool
def list_templates(project_id: str | None = None) -> list[dict]:
    """Template summaries: id, project, name, topic list, question and
    critical-question counts. Use `get_template` for the full question text."""
    out = []
    for t in ds.TEMPLATES:
        if project_id and t["project_id"] != project_id:
            continue
        questions = [q for topic in t["topics"] for q in topic["questions"]]
        out.append(
            {
                "template_id": t["template_id"],
                "project_id": t["project_id"],
                "name": t["name"],
                "topic_count": len(t["topics"]),
                "question_count": len(questions),
                "critical_question_count": sum(1 for q in questions if q["is_critical"]),
                "topics": [
                    {
                        "topic_id": topic["topic_id"],
                        "name": topic["name"],
                        "questions": len(topic["questions"]),
                    }
                    for topic in t["topics"]
                ],
            }
        )
    return out


@mcp.tool
def get_template(template_id: str) -> dict:
    """Full template structure: topics, questions, and per-question
    `is_critical` flags. Accepts a `project_id` in place of a template id."""
    t = ds.TEMPLATES_BY_ID.get(template_id)
    if not t and template_id in ds.TEMPLATE_ID_BY_PROJECT:
        t = ds.TEMPLATES_BY_ID.get(ds.TEMPLATE_ID_BY_PROJECT[template_id])
    if not t:
        return {
            "error": f"template not found: {template_id}",
            "code": "not_found",
            "available": sorted(ds.TEMPLATES_BY_ID),
        }
    return t


@mcp.tool
def list_locations(
    org_id: str | None = None, project_id: str | None = None
) -> dict:
    """Physical locations and their work areas, for site-level questions."""
    locations = [
        l
        for l in ds.LOCATIONS
        if (org_id is None or l["org_id"] == org_id)
        and (project_id is None or l["project_id"] == project_id)
    ]
    return {
        "total_matching": len(locations),
        "items": [
            {
                **l,
                "work_areas": [
                    {"work_area_id": w["work_area_id"], "name": w["name"]}
                    for w in ds.WORK_AREAS
                    if w["location_id"] == l["location_id"]
                ],
            }
            for l in locations
        ],
    }


# ===========================================================================
# 2. Retrieval
# ===========================================================================


@mcp.tool
def list_observations(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    observer_user_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    below_project_threshold: bool = False,
    sort_by: str = "completed_at",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Observation headers, filtered, sorted and paginated.

    Dates are ISO (`2026-04-12`); `lookback_days` counts back from today's
    anchor. `below_project_threshold` keeps only observations scoring under
    their own project's floor — usually what is wanted rather than a flat
    `max_score`, since the floor differs per project.

    Returns `total_matching` alongside the page plus an aggregate over all
    matches. For counting or ranking use `summarize_observations` instead of
    paging through this.
    """
    return queries.list_observations(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        observer_user_id=observer_user_id,
        since=since,
        until=until,
        lookback_days=lookback_days,
        min_score=min_score,
        max_score=max_score,
        below_project_threshold=below_project_threshold,
        sort_by=sort_by,
        descending=descending,
        limit=limit,
        offset=offset,
    )


@mcp.tool
def get_observation(
    observation_id: str,
    include_responses: bool = True,
    include_notes: bool = True,
) -> dict:
    """One observation, fully hydrated — the "tell me everything about X" tool.

    Resolves in a single call: subject, observer, the subject's manager, the
    project policy that applies, template, location and work area, every
    question-level response with `is_critical` and comment, per-topic rollups
    flagged against the threshold, and any attached coach notes. Explicitly
    reports when no coach notes exist, rather than returning silence.

    Pass a 6-digit `OBS-` id. A 3-digit one is an observer — use `get_user`.
    """
    return queries.get_observation(
        observation_id,
        include_responses=include_responses,
        include_notes=include_notes,
    )


@mcp.tool
def get_observation_responses(
    observation_id: str | None = None,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    topic_id: str | None = None,
    question_id: str | None = None,
    critical_only: bool = False,
    min_score: float | None = None,
    max_score: float | None = None,
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Question-level response rows — the only place `is_critical` per response
    and per-question scores live.

    ~9,000 rows total, so filter. The envelope carries `total_matching` and a
    score distribution computed over **all** matches (not just the returned
    page), so a broad query still gives a usable answer cheaply.

    For "are there critical failures" prefer `find_critical_failures`, which
    applies the per-project threshold and severity banding for you.
    """
    return queries.get_observation_responses(
        observation_id=observation_id,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        topic_id=topic_id,
        question_id=question_id,
        critical_only=critical_only,
        min_score=min_score,
        max_score=max_score,
        since=since,
        until=until,
        lookback_days=lookback_days,
        limit=limit,
        offset=offset,
    )


@mcp.tool
def list_coach_notes(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    author_user_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    observation_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Coach notes with full bodies, filtered and paginated (~786 total).

    Notes hang off an observation and name a subject and an author. To hunt for
    a theme use `search_coach_notes`; to find repeated concerns use
    `find_recurring_note_concerns`.
    """
    since_ts, until_ts = ds.resolve_window(since, until)
    items = []
    for n in ds.COACH_NOTES:
        if org_id and n["org_id"] != org_id:
            continue
        if project_id and n["project_id"] != project_id:
            continue
        if subject_user_id and n["subject_user_id"] != subject_user_id:
            continue
        if author_user_id and n["author_user_id"] != author_user_id:
            continue
        if observation_id and n["observation_id"] != observation_id:
            continue
        created = ds.parse_dt(n["created_at"])
        if since and created < since_ts:
            continue
        if until and created > until_ts:
            continue
        items.append(
            {
                "note_id": n["note_id"],
                "subject": ds.user_brief(n["subject_user_id"]),
                "author": ds.user_brief(n["author_user_id"]),
                "observation_id": n["observation_id"],
                "project_id": n["project_id"],
                "org_id": n["org_id"],
                "created_at": ds.day(created),
                "body": n["body"],
            }
        )
    items.sort(key=lambda n: n["created_at"], reverse=True)
    return ds.page(items, limit, offset)


@mcp.tool
def search_coach_notes(
    text: str,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    limit: int = 40,
) -> dict:
    """Free-text search over coach note bodies.

    Space-separated terms are AND-ed; `"quote a phrase"` to match it literally.
    Case-insensitive. Also returns a per-subject hit count, which is often the
    answer on its own ("who does this keep coming up for").
    """
    return analytics.search_coach_notes(
        text=text,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        limit=limit,
    )


# ===========================================================================
# 3. Aggregation
# ===========================================================================


@mcp.tool
def summarize_observations(
    group_by: list[str] | str,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    observer_user_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
    sort_by: str = "observations",
    descending: bool = True,
    limit: int = 50,
) -> dict:
    """Group observations and aggregate them server-side.

    Group by any of `observer_user_id`, `subject_user_id`, `project_id`,
    `org_id`, `template_id`, `location_id`, `work_area_id` (or several at
    once). Each group returns count, mean/min/max overall score, distinct
    subjects and observers, and first/last dates; user ids are resolved to
    names.

    This is the tool for "who completed the most", "which site scores lowest",
    "how many per project" — the counting happens in pandas and only the
    summary rows travel. Sort by any returned metric.
    """
    return queries.summarize_observations(
        group_by=group_by,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        observer_user_id=observer_user_id,
        since=since,
        until=until,
        lookback_days=lookback_days,
        sort_by=sort_by,
        descending=descending,
        limit=limit,
    )


@mcp.tool
def topic_breakdown(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    topic_id: str | None = None,
    group_by_subject: bool = False,
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
    limit: int = 60,
) -> dict:
    """Mean score per topic, flagged against each project's threshold.

    With `group_by_subject` this becomes the associate x topic matrix, which is
    how a weakness confined to one topic becomes visible — `overall_score`
    averages it away. Weakest topics sort first.
    """
    return queries.topic_breakdown(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        topic_id=topic_id,
        group_by_subject=group_by_subject,
        since=since,
        until=until,
        lookback_days=lookback_days,
        limit=limit,
    )


@mcp.tool
def get_associate_profile(
    user_id: str, recent_window: int = 4, include_all_observations: bool = False
) -> dict:
    """Everything about one associate, in a single call.

    Identity and reporting line, project policy, observation history with
    scores and observers, per-topic means split **recent vs earlier** (so a
    localised slide shows up), critical-question failures, cadence standing,
    the lowest individual responses, and every coach note.

    This is the right first call when drafting a coaching proposal or
    investigating a flagged individual: it replaces six or seven separate
    lookups and returns the ids needed to cite the evidence.
    """
    return queries.associate_profile(
        user_id, recent_window=recent_window, include_all_observations=include_all_observations
    )


# ===========================================================================
# 4. Detectors
# ===========================================================================


@mcp.tool
def find_sustained_low_topic_scores(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    window: int = 4,
    min_below: int = 2,
    threshold: float | None = None,
    min_observations: int = 3,
) -> dict:
    """Associates repeatedly scoring low on a **specific topic**.

    Default rule: for each (associate, topic) pair, take the last `window`
    observations (4) and flag the pair when at least `min_below` (2) of them
    score below the project's own `score_threshold`. Two-of-four excludes a
    single bad day, which cannot reach two, without demanding a strict
    consecutive run that would miss intermittent lapses.

    Returns per finding: the window's scores with observation ids, the mean and
    minimum, and `consecutive_below_latest` (the run ending at the most recent
    observation) so a still-deteriorating case is distinguishable from one that
    has recovered. Pairs one observation short of the rule come back as
    `watchlist`. The applied rule and its rationale come back as `rule`, so the
    answer can state its own definition.

    Tune `window` / `min_below` / `threshold` to argue a different definition.
    """
    return analytics.sustained_low_topic_scores(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        window=window,
        min_below=min_below,
        threshold=threshold,
        min_observations=min_observations,
    )


@mcp.tool
def find_critical_failures(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    threshold: float | None = None,
    lookback_days: int | None = 60,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> dict:
    """Critical-question responses that scored below the bar — the safety tool.

    Only questions the template marks `is_critical` are considered, and that
    flag exists only on response rows, so this cannot be done from observation
    headers.

    Defaults: bar = the project's own `score_threshold`; window = last 60 days
    (pass `lookback_days=None` for all history). Each finding carries the
    associate, their manager, the question, the observation id, the date, the
    score, points below the bar, and a severity band from the **absolute**
    score (severe < 50, high 50-65, moderate above) — a safety manager reacts
    to "lockout-tagout: 43", not to how far 43 sits under a particular bar.

    `repeat_count` and the `repeat_offenders` roll-up show where the same person
    has failed the same critical question more than once, which is the
    difference between an incident and an unaddressed hazard.
    """
    return analytics.critical_failures(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        threshold=threshold,
        lookback_days=lookback_days,
        since=since,
        until=until,
        limit=limit,
    )


@mcp.tool
def find_cadence_violations(
    org_id: str | None = None,
    project_id: str | None = None,
    grace_days: int = 0,
    include_compliant: bool = False,
) -> dict:
    """Associates not observed within their project's `cadence_days`.

    `days_overdue = days_since_last_observation - cadence_days - grace_days`,
    measured from the end of today's anchor date. Cadence is per project (7
    days in PRJ-3, 30 in PRJ-5), so each associate is judged against their own
    policy — never a single global interval.

    Sorted most-overdue first, with `cadences_missed` for scale. Associates
    with no observation at all are returned separately as `never_observed`,
    since that is a coverage gap rather than a late one. `compliance_rate` is
    included for the project-health framing.
    """
    return analytics.cadence_violations(
        org_id=org_id,
        project_id=project_id,
        grace_days=grace_days,
        include_compliant=include_compliant,
    )


@mcp.tool
def find_score_declines(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    recent_window: int = 3,
    min_baseline: int = 3,
    min_drop: float = 5.0,
    min_z: float = 2.0,
    require_both: bool = True,
) -> dict:
    """Associates declining against their **own** baseline, not the cohort.

    Compares the mean of the last `recent_window` observations (3) to the mean
    of all earlier ones (at least `min_baseline`). Self-referential on purpose:
    a strong performer sliding from 92 to 84 is a real change that a
    cohort comparison buries, because 84 still looks fine.

    "Meaningfully" is two tests at once: a drop of at least `min_drop` points
    (5) **and** at least `min_z` baseline standard deviations (2.0). Either
    alone misfires — absolute-only flags naturally noisy performers, z-only
    flags a 1.5-point drop on a very steady one. Both figures are returned so
    the reader can see which test bound the result, along with the recent
    observation ids and the topics driving the drop.
    """
    return analytics.score_declines(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        recent_window=recent_window,
        min_baseline=min_baseline,
        min_drop=min_drop,
        min_z=min_z,
        require_both=require_both,
    )


@mcp.tool
def find_recurring_note_concerns(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    min_notes: int = 2,
    window_days: int | None = 120,
    require_concern_language: bool = True,
    extra_terms: list[str] | None = None,
) -> dict:
    """Associates whose **coach notes** show a concern raised more than once.

    Most notes here are routine ("Clean handoff to next shift"). A note is
    flagged when it carries recurrence language ("still", "again", "ongoing",
    "already flagged", "despite", "not improving") and — with
    `require_concern_language` on — negative content too ("struggling",
    "issue", "follow-up"). Both lexicons are needed: *"again took feedback
    well"* recurs but is not a concern.

    An associate is reported once at least `min_notes` (2) flagged notes fall
    within `window_days` (120). One flagged note is a bad day; two dates or two
    authors pointing at the same thing is the pattern.

    Findings carry the full note bodies with `note_id`, author and date, the
    matched terms, and a `score_context` block showing whether the numbers
    agree with what the notes say. Single flagged notes are returned separately
    as near-misses. This is a keyword heuristic over free text —
    `candidates_scanned` shows the sweep's coverage.
    """
    return analytics.recurring_note_concerns(
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        min_notes=min_notes,
        window_days=window_days,
        require_concern_language=require_concern_language,
        extra_terms=extra_terms,
    )


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Observations MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="stdio is what Claude Code uses; http is for ad-hoc inspection.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
