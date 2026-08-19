"""
Retrieval and aggregation over observations.

The scaffold exposed no observation tools at all, so this module supplies the
three shapes an agent actually needs:

* **hydrated single-record reads** (:func:`get_observation`,
  :func:`associate_profile`) that resolve every foreign key in one call, so a
  "tell me everything about X" question costs one round trip instead of six;
* **filtered, paginated lists** (:func:`list_observations`) that never hand
  back the whole table;
* **server-side aggregation** (:func:`summarize_observations`,
  :func:`topic_breakdown`) so counting and averaging happen in pandas rather
  than in a language model's head.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from dataset import (
    COACH_NOTES,
    LOCATIONS_BY_ID,
    NOTES_BY_OBSERVATION,
    NOTES_BY_SUBJECT,
    OBS_DF,
    OBSERVATIONS_BY_ID,
    ORGS,
    PROJECTS,
    RESP_DF,
    TEMPLATES,
    TEMPLATES_BY_ID,
    TOPIC_NAMES,
    USERS,
    USERS_BY_ID,
    WORK_AREAS_BY_ID,
    cadence_for,
    classify_id,
    day,
    days_between,
    filter_frame,
    page,
    project_policy,
    r2,
    resolve_window,
    threshold_for,
    today,
    today_date,
    user_brief,
)

def _window_or_none(
    since: str | None, until: str | None, lookback_days: int | None
) -> tuple[Any, Any]:
    """Resolve a date window, or (None, None) when no date argument was given.

    ``resolve_window`` always produces an ``until`` (the anchor day), which is the
    right default for a detector but wrong for a plain list call: it would apply a
    window the caller never asked for.
    """
    if since is None and until is None and lookback_days is None:
        return None, None
    return resolve_window(since, until, lookback_days)


SORT_FIELDS = {
    "completed_at": "completed_dt",
    "overall_score": "overall_score",
    "observation_id": "observation_id",
}


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


def describe_dataset() -> dict[str, Any]:
    """What is in here, how big it is, and where the sharp edges are.

    Cheap first call that saves an agent from probing the shape of the data
    with exploratory queries, and warns it about the two id traps before it
    trips over them.
    """
    return {
        "today": today_date(),
        "today_note": "The dataset is anchored to this date. All 'recent' / 'last N days' / 'past due' logic is measured from the end of this day, not the wall clock.",
        "counts": {
            "organizations": len(ORGS),
            "projects": len(PROJECTS),
            "users": len(USERS),
            "associates": sum(1 for u in USERS if u["role"] == "associate"),
            "observers": sum(1 for u in USERS if u["role"] == "observer"),
            "managers": sum(1 for u in USERS if u["role"] == "manager"),
            "observations": len(OBS_DF),
            "responses": len(RESP_DF),
            "coach_notes": len(COACH_NOTES),
            "templates": len(TEMPLATES),
        },
        "observation_date_range": {
            "earliest": day(OBS_DF["completed_dt"].min()),
            "latest": day(OBS_DF["completed_dt"].max()),
        },
        "projects": [
            {
                **project_policy(p["project_id"]),
                "observations": int((OBS_DF["project_id"] == p["project_id"]).sum()),
                "associates": sum(
                    1
                    for u in USERS
                    if u["role"] == "associate" and u["project_id"] == p["project_id"]
                ),
            }
            for p in PROJECTS
        ],
        "topics": [{"topic_id": k, "name": v} for k, v in sorted(TOPIC_NAMES.items())],
        "critical_questions": [
            {
                "question_id": q["question_id"],
                "template_id": q["template_id"],
                "topic_id": q["topic_id"],
                "text": q["question_text"],
            }
            for q in _critical_question_list()
        ],
        "score_summary": {
            "overall_score": {
                "mean": r2(OBS_DF["overall_score"].mean()),
                "min": r2(OBS_DF["overall_score"].min()),
                "max": r2(OBS_DF["overall_score"].max()),
            },
            "response_score": {
                "mean": r2(RESP_DF["score"].mean()),
                "min": r2(RESP_DF["score"].min()),
                "max": r2(RESP_DF["score"].max()),
            },
            "critical_response_score": {
                "mean": r2(RESP_DF[RESP_DF["is_critical"]]["score"].mean()),
                "min": r2(RESP_DF[RESP_DF["is_critical"]]["score"].min()),
                "count": int(RESP_DF["is_critical"].sum()),
            },
        },
        "gotchas": [
            "Observer ids and observation ids share the OBS- prefix: observers are OBS-001..OBS-020 (3 digits), observations are OBS-000001..OBS-001204 (6 digits). Use resolve_entity if an id is ambiguous.",
            "projects.json carries no template_id column; project -> template is derived from templates.json (each template names its project_id). project_policy exposes the resolved template_id.",
            "is_critical exists only on response rows, not on observation headers, so critical-question questions must go through the response tools.",
            "Observer display names are not unique (two PRJ-3 observers are both named 'Blair Davis'). Always carry the user_id.",
            "Managers own associates via the associate's manager_id. Observers have manager_id = null and are not in that hierarchy.",
        ],
    }


def _critical_question_list() -> list[dict]:
    from dataset import CRITICAL_QUESTIONS

    return sorted(CRITICAL_QUESTIONS.values(), key=lambda q: q["question_id"])


def resolve_entity(entity_id: str) -> dict[str, Any]:
    """Identify what an id refers to and return that record.

    Exists mainly to disambiguate ``OBS-`` ids, which name an observer at three
    digits and an observation at six.
    """
    raw = (entity_id or "").strip()
    kind = classify_id(raw)
    if kind == "observation":
        rec = OBSERVATIONS_BY_ID.get(raw)
        return {"entity_id": raw, "kind": "observation", "found": rec is not None, "record": rec}
    if kind in {"observer", "associate", "manager"}:
        rec = USERS_BY_ID.get(raw)
        return {
            "entity_id": raw,
            "kind": rec["role"] if rec else kind,
            "found": rec is not None,
            "record": rec,
        }
    lookups = {
        "organization": {o["org_id"]: o for o in ORGS},
        "project": {p["project_id"]: project_policy(p["project_id"]) for p in PROJECTS},
        "template": TEMPLATES_BY_ID,
        "location": LOCATIONS_BY_ID,
        "work_area": WORK_AREAS_BY_ID,
        "note": {n["note_id"]: n for n in COACH_NOTES},
    }
    rec = lookups.get(kind, {}).get(raw)
    return {"entity_id": raw, "kind": kind, "found": rec is not None, "record": rec}


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def _observation_row(row: Any, include_names: bool = True) -> dict:
    out = {
        "observation_id": row.observation_id,
        "completed_at": day(row.completed_dt),
        "project_id": row.project_id,
        "org_id": row.org_id,
        "subject_user_id": row.subject_user_id,
        "observer_user_id": row.observer_user_id,
        "overall_score": r2(row.overall_score),
    }
    if include_names:
        out["subject_name"] = (USERS_BY_ID.get(row.subject_user_id) or {}).get("name")
        out["observer_name"] = (USERS_BY_ID.get(row.observer_user_id) or {}).get("name")
    return out


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
    include_names: bool = True,
) -> dict[str, Any]:
    """Observation headers, filtered and paginated.

    ``below_project_threshold`` keeps only observations whose ``overall_score``
    falls under the score floor of their own project, which is a different (and
    usually more useful) filter than a flat ``max_score``.

    Always returns ``total_matching`` next to the page, so a truncated result is
    never mistaken for a complete one.
    """
    # No date argument at all means no date filter — resolve_window would
    # otherwise default `until` to the anchor day and imply a window that was
    # never asked for.
    since_ts, until_ts = _window_or_none(since, until, lookback_days)

    df = filter_frame(
        OBS_DF,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        observer_user_id=observer_user_id,
        since=since_ts,
        until=until_ts,
    )
    if min_score is not None:
        df = df[df["overall_score"] >= float(min_score)]
    if max_score is not None:
        df = df[df["overall_score"] <= float(max_score)]
    if below_project_threshold:
        bars = df["project_id"].map(lambda p: threshold_for(p))
        df = df[df["overall_score"] < bars]

    sort_col = SORT_FIELDS.get(sort_by, "completed_dt")
    df = df.sort_values(sort_col, ascending=not descending)

    items = [_observation_row(r, include_names) for r in df.itertuples()]
    result = page(items, limit, offset)
    result["filters"] = {
        "org_id": org_id,
        "project_id": project_id,
        "subject_user_id": subject_user_id,
        "observer_user_id": observer_user_id,
        "since": day(since_ts),
        "until": day(until_ts),
        "below_project_threshold": below_project_threshold,
    }
    if items:
        scores = [i["overall_score"] for i in items]
        result["aggregate_over_all_matches"] = {
            "count": len(items),
            "mean_overall_score": r2(sum(scores) / len(scores)),
            "min_overall_score": r2(min(scores)),
            "max_overall_score": r2(max(scores)),
        }
    return result


def get_observation(
    observation_id: str,
    include_responses: bool = True,
    include_notes: bool = True,
) -> dict[str, Any]:
    """One observation with every foreign key already resolved.

    Returns the header, the people (subject, observer, and the subject's
    manager), the project policy that applies, the template, location and work
    area, per-question responses with ``is_critical`` and per-topic rollups,
    and any coach notes attached — in a single call.
    """
    obs = OBSERVATIONS_BY_ID.get(observation_id)
    if not obs:
        hint = ""
        if classify_id(observation_id) == "observer":
            hint = " That id looks like an observer (3-digit OBS-), not an observation (6-digit OBS-)."
        return {
            "error": f"observation not found: {observation_id}.{hint}",
            "code": "not_found",
        }

    subject = USERS_BY_ID.get(obs["subject_user_id"], {})
    policy = project_policy(obs["project_id"]) or {}
    template = TEMPLATES_BY_ID.get(obs["template_id"], {})

    out: dict[str, Any] = {
        "observation_id": obs["observation_id"],
        "completed_at": obs["completed_at"],
        "completed_date": day(pd.Timestamp(obs["completed_at"])),
        "days_ago": days_between(pd.Timestamp(obs["completed_at"]), today()),
        "overall_score": r2(obs["overall_score"]),
        "org": {
            "org_id": obs["org_id"],
            "name": policy.get("org_name"),
        },
        "project": {
            "project_id": obs["project_id"],
            "name": policy.get("name"),
            "cadence_days": policy.get("cadence_days"),
            "score_threshold": policy.get("score_threshold"),
        },
        "template": {
            "template_id": obs["template_id"],
            "name": template.get("name"),
            "topic_count": len(template.get("topics", [])),
        },
        "subject": user_brief(obs["subject_user_id"]),
        "observer": user_brief(obs["observer_user_id"]),
        "subject_manager": user_brief(subject.get("manager_id")),
        "location": {
            "location_id": obs.get("location_id"),
            "name": (LOCATIONS_BY_ID.get(obs.get("location_id")) or {}).get("name"),
        },
        "work_area": {
            "work_area_id": obs.get("work_area_id"),
            "name": (WORK_AREAS_BY_ID.get(obs.get("work_area_id")) or {}).get("name"),
        },
    }

    threshold = policy.get("score_threshold")
    if threshold is not None:
        out["overall_score_vs_threshold"] = {
            "threshold": threshold,
            "passes": bool(obs["overall_score"] >= threshold),
            "margin": r2(obs["overall_score"] - threshold),
        }

    rows = RESP_DF[RESP_DF["observation_id"] == observation_id]
    if include_responses and not rows.empty:
        out["responses"] = [
            {
                "response_id": r.response_id,
                "topic_id": r.topic_id,
                "topic_name": TOPIC_NAMES.get(r.topic_id, r.topic_id),
                "question_id": r.question_id,
                "question_text": r.question_text,
                "is_critical": bool(r.is_critical),
                "score": r2(r.score),
                "comment": r.comment,
            }
            for r in rows.sort_values("question_id").itertuples()
        ]
        out["topic_scores"] = [
            {
                "topic_id": t,
                "topic_name": TOPIC_NAMES.get(t, t),
                "mean_score": r2(v),
                "below_threshold": bool(threshold is not None and v < threshold),
            }
            for t, v in rows.groupby("topic_id")["score"].mean().sort_values().items()
        ]
        crit = rows[rows["is_critical"]]
        out["critical_questions"] = {
            "count": int(len(crit)),
            "lowest_score": r2(crit["score"].min()) if not crit.empty else None,
            "any_below_threshold": bool(
                threshold is not None and not crit.empty and (crit["score"] < threshold).any()
            ),
        }
    elif include_responses:
        out["responses"] = []
        out["responses_note"] = "No response rows found for this observation."

    if include_notes:
        notes = NOTES_BY_OBSERVATION.get(observation_id, [])
        out["coach_notes"] = [
            {
                "note_id": n["note_id"],
                "author": user_brief(n["author_user_id"]),
                "created_at": day(pd.Timestamp(n["created_at"])),
                "body": n["body"],
            }
            for n in sorted(notes, key=lambda n: n["created_at"])
        ]
        out["coach_note_count"] = len(notes)
        if not notes:
            out["coach_notes_note"] = "No coach notes are attached to this observation."

    return out


def get_observation_responses(
    observation_id: str | None = None,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    topic_id: str | None = None,
    question_id: str | None = None,
    critical_only: bool = False,
    max_score: float | None = None,
    min_score: float | None = None,
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Question-level response rows. The only place ``is_critical`` per
    response and per-question scores live.

    There are ~9,000 rows, so a filter is effectively mandatory: the envelope
    reports ``total_matching`` and a distribution summary over **all** matches,
    not just the returned page, so a broad query still yields a usable answer
    without shipping every row.
    """
    since_ts, until_ts = _window_or_none(since, until, lookback_days)
    df = RESP_DF
    if observation_id:
        df = df[df["observation_id"] == observation_id]
    df = filter_frame(
        df,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        since=since_ts,
        until=until_ts,
    )
    if topic_id:
        df = df[df["topic_id"] == topic_id]
    if question_id:
        df = df[df["question_id"] == question_id]
    if critical_only:
        df = df[df["is_critical"]]
    if min_score is not None:
        df = df[df["score"] >= float(min_score)]
    if max_score is not None:
        df = df[df["score"] <= float(max_score)]

    df = df.sort_values("completed_dt", ascending=False)
    items = [
        {
            "response_id": r.response_id,
            "observation_id": r.observation_id,
            "completed_at": day(r.completed_dt),
            "subject_user_id": r.subject_user_id,
            "project_id": r.project_id,
            "topic_id": r.topic_id,
            "question_id": r.question_id,
            "question_text": r.question_text,
            "is_critical": bool(r.is_critical),
            "score": r2(r.score),
            "comment": r.comment,
        }
        for r in df.itertuples()
    ]
    result = page(items, limit, offset)
    if not df.empty:
        result["summary_over_all_matches"] = {
            "mean_score": r2(df["score"].mean()),
            "min_score": r2(df["score"].min()),
            "max_score": r2(df["score"].max()),
            "critical_rows": int(df["is_critical"].sum()),
            "distinct_subjects": int(df["subject_user_id"].nunique()),
            "distinct_observations": int(df["observation_id"].nunique()),
        }
    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

GROUPABLE = {
    "observer_user_id",
    "subject_user_id",
    "project_id",
    "org_id",
    "template_id",
    "location_id",
    "work_area_id",
}


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
) -> dict[str, Any]:
    """Group observations and aggregate them server-side.

    Groupable: ``observer_user_id``, ``subject_user_id``, ``project_id``,
    ``org_id``, ``template_id``, ``location_id``, ``work_area_id``.

    Per group: observation count, mean/min/max overall score, distinct subjects
    and observers, and first/last dates. This is the tool for any "who has done
    the most", "which project scores lowest", "how many per site" question —
    the counting happens in pandas, and only the summary rows travel.
    """
    keys = [group_by] if isinstance(group_by, str) else list(group_by)
    bad = [k for k in keys if k not in GROUPABLE]
    if bad:
        return {
            "error": f"cannot group by {bad}; groupable fields are {sorted(GROUPABLE)}",
            "code": "bad_request",
        }

    since_ts, until_ts = _window_or_none(since, until, lookback_days)
    df = filter_frame(
        OBS_DF,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        observer_user_id=observer_user_id,
        since=since_ts,
        until=until_ts,
    )
    if df.empty:
        return {
            "group_by": keys,
            "window": {"since": day(since_ts), "until": day(until_ts)},
            "total_observations": 0,
            "groups": [],
        }

    agg = df.groupby(keys, as_index=False).agg(
        observations=("observation_id", "size"),
        mean_overall_score=("overall_score", "mean"),
        min_overall_score=("overall_score", "min"),
        max_overall_score=("overall_score", "max"),
        distinct_subjects=("subject_user_id", "nunique"),
        distinct_observers=("observer_user_id", "nunique"),
        first_completed=("completed_dt", "min"),
        last_completed=("completed_dt", "max"),
    )

    sort_col = sort_by if sort_by in agg.columns else "observations"
    agg = agg.sort_values(sort_col, ascending=not descending)

    groups = []
    for row in agg.itertuples():
        entry = {k: getattr(row, k) for k in keys}
        for k in keys:
            if k.endswith("user_id"):
                entry[k.replace("_user_id", "_name")] = (
                    USERS_BY_ID.get(getattr(row, k)) or {}
                ).get("name")
        entry.update(
            {
                "observations": int(row.observations),
                "mean_overall_score": r2(row.mean_overall_score),
                "min_overall_score": r2(row.min_overall_score),
                "max_overall_score": r2(row.max_overall_score),
                "distinct_subjects": int(row.distinct_subjects),
                "distinct_observers": int(row.distinct_observers),
                "first_completed": day(row.first_completed),
                "last_completed": day(row.last_completed),
            }
        )
        groups.append(entry)

    return {
        "group_by": keys,
        "window": {"since": day(since_ts), "until": day(until_ts)},
        "filters": {
            "org_id": org_id,
            "project_id": project_id,
            "subject_user_id": subject_user_id,
            "observer_user_id": observer_user_id,
        },
        "total_observations": int(len(df)),
        "group_count": len(groups),
        "groups": groups[: int(limit)],
    }


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
) -> dict[str, Any]:
    """Mean scores per topic, optionally per associate, with threshold flags.

    Answers "which topic is weakest here" without pulling response rows. With
    ``group_by_subject`` it becomes the associate x topic matrix that makes a
    localised weakness visible where ``overall_score`` hides it.
    """
    since_ts, until_ts = _window_or_none(since, until, lookback_days)
    df = filter_frame(
        RESP_DF,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        since=since_ts,
        until=until_ts,
    )
    if topic_id:
        df = df[df["topic_id"] == topic_id]
    if df.empty:
        return {"rows": [], "total_responses": 0}

    keys = (["subject_user_id"] if group_by_subject else []) + ["topic_id"]
    agg = df.groupby(keys + ["project_id"], as_index=False).agg(
        responses=("response_id", "size"),
        observations=("observation_id", "nunique"),
        mean_score=("score", "mean"),
        min_score=("score", "min"),
        critical_responses=("is_critical", "sum"),
    )
    agg = agg.sort_values("mean_score")

    rows = []
    for row in agg.itertuples():
        bar = threshold_for(row.project_id)
        entry = {
            "topic_id": row.topic_id,
            "topic_name": TOPIC_NAMES.get(row.topic_id, row.topic_id),
            "project_id": row.project_id,
            "responses": int(row.responses),
            "observations": int(row.observations),
            "mean_score": r2(row.mean_score),
            "min_score": r2(row.min_score),
            "critical_responses": int(row.critical_responses),
            "threshold": r2(bar),
            "below_threshold": bool(row.mean_score < bar),
        }
        if group_by_subject:
            entry = {
                "subject_user_id": row.subject_user_id,
                "subject_name": (USERS_BY_ID.get(row.subject_user_id) or {}).get("name"),
                **entry,
            }
        rows.append(entry)

    return {
        "window": {"since": day(since_ts), "until": day(until_ts)},
        "total_responses": int(len(df)),
        "row_count": len(rows),
        "rows": rows[: int(limit)],
    }


# ---------------------------------------------------------------------------
# Associate dossier
# ---------------------------------------------------------------------------


def associate_profile(
    user_id: str,
    recent_window: int = 4,
    include_all_observations: bool = False,
) -> dict[str, Any]:
    """Everything about one associate, assembled in a single call.

    Identity and reporting line, project policy, observation history with
    scores, per-topic means split **recent vs earlier** (so a localised slide
    is visible), any critical-question failures, cadence standing, and all
    coach notes.

    This is the tool for drafting a coaching proposal: it collects in one round
    trip what would otherwise take six or seven, and it returns the ids needed
    to cite the evidence.
    """
    user = USERS_BY_ID.get(user_id)
    if not user:
        return {"error": f"user not found: {user_id}", "code": "not_found"}
    if user["role"] != "associate":
        return {
            "error": f"{user_id} has role '{user['role']}', not 'associate'. This tool profiles observation subjects.",
            "code": "bad_request",
            "user": user,
        }

    policy = project_policy(user["project_id"]) or {}
    bar = threshold_for(user["project_id"])
    obs = OBS_DF[OBS_DF["subject_user_id"] == user_id].sort_values("completed_dt")
    resp = RESP_DF[RESP_DF["subject_user_id"] == user_id]

    out: dict[str, Any] = {
        "subject": user_brief(user_id),
        "manager": user_brief(user.get("manager_id")),
        "project": {
            "project_id": user["project_id"],
            "name": policy.get("name"),
            "cadence_days": policy.get("cadence_days"),
            "score_threshold": policy.get("score_threshold"),
            "template_id": policy.get("template_id"),
        },
        "organization": {"org_id": user["org_id"], "name": policy.get("org_name")},
    }

    if obs.empty:
        out["observations"] = {"total": 0, "note": "This associate has never been observed."}
        out["coach_notes"] = []
        return out

    recent = obs.tail(int(recent_window))
    earlier = obs.iloc[: -int(recent_window)] if len(obs) > recent_window else obs.iloc[0:0]
    last_dt = obs["completed_dt"].max()
    days_since = days_between(last_dt, today())
    cadence = cadence_for(user["project_id"]) or 0

    out["observations"] = {
        "total": int(len(obs)),
        "first": day(obs["completed_dt"].min()),
        "latest": day(last_dt),
        "mean_overall_score": r2(obs["overall_score"].mean()),
        "recent_window": int(len(recent)),
        "recent_mean_overall_score": r2(recent["overall_score"].mean()),
        "earlier_mean_overall_score": r2(earlier["overall_score"].mean()) if not earlier.empty else None,
        "change_vs_earlier": r2(recent["overall_score"].mean() - earlier["overall_score"].mean())
        if not earlier.empty
        else None,
        "distinct_observers": int(obs["observer_user_id"].nunique()),
        "below_threshold_count": int((obs["overall_score"] < bar).sum()),
        "history": [
            {
                "observation_id": r.observation_id,
                "completed_at": day(r.completed_dt),
                "overall_score": r2(r.overall_score),
                "observer_user_id": r.observer_user_id,
                "observer_name": (USERS_BY_ID.get(r.observer_user_id) or {}).get("name"),
                "below_threshold": bool(r.overall_score < bar),
            }
            for r in (obs if include_all_observations else obs.tail(12)).itertuples()
        ],
        "history_truncated": not include_all_observations and len(obs) > 12,
    }

    out["cadence"] = {
        "cadence_days": cadence,
        "days_since_last_observation": days_since,
        "days_overdue": r2((days_since or 0) - cadence),
        "status": "overdue" if (days_since or 0) > cadence else "current",
    }

    if not resp.empty:
        recent_ids = set(recent["observation_id"])
        recent_resp = resp[resp["observation_id"].isin(recent_ids)]
        earlier_resp = resp[~resp["observation_id"].isin(recent_ids)]
        r_topic = recent_resp.groupby("topic_id")["score"].mean()
        e_topic = earlier_resp.groupby("topic_id")["score"].mean()

        topics = []
        for topic in sorted(set(r_topic.index) | set(e_topic.index)):
            rv = r_topic.get(topic)
            ev = e_topic.get(topic)
            topics.append(
                {
                    "topic_id": topic,
                    "topic_name": TOPIC_NAMES.get(topic, topic),
                    "recent_mean": r2(rv),
                    "earlier_mean": r2(ev),
                    "change": r2(rv - ev) if rv is not None and ev is not None else None,
                    "recent_below_threshold": bool(rv is not None and rv < bar),
                }
            )
        out["topics"] = sorted(
            topics, key=lambda t: (t["recent_mean"] is None, t["recent_mean"])
        )

        crit = resp[resp["is_critical"]]
        failed = crit[crit["score"] < bar]
        out["critical_questions"] = {
            "responses": int(len(crit)),
            "mean_score": r2(crit["score"].mean()) if not crit.empty else None,
            "failures_below_threshold": int(len(failed)),
            "failures": [
                {
                    "observation_id": r.observation_id,
                    "completed_at": day(r.completed_dt),
                    "question_id": r.question_id,
                    "question_text": r.question_text,
                    "score": r2(r.score),
                    "threshold": r2(bar),
                    "comment": r.comment,
                }
                for r in failed.sort_values("completed_dt", ascending=False).itertuples()
            ],
        }

        worst = resp.nsmallest(5, "score")
        out["lowest_responses_all_time"] = [
            {
                "observation_id": r.observation_id,
                "completed_at": day(r.completed_dt),
                "question_id": r.question_id,
                "question_text": r.question_text,
                "is_critical": bool(r.is_critical),
                "score": r2(r.score),
                "comment": r.comment,
            }
            for r in worst.itertuples()
        ]

    notes = sorted(NOTES_BY_SUBJECT.get(user_id, []), key=lambda n: n["created_at"])
    out["coach_notes"] = [
        {
            "note_id": n["note_id"],
            "observation_id": n["observation_id"],
            "author": user_brief(n["author_user_id"]),
            "created_at": day(pd.Timestamp(n["created_at"])),
            "body": n["body"],
        }
        for n in notes
    ]
    out["coach_note_count"] = len(notes)
    return out
