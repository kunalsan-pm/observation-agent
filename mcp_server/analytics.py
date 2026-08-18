"""
Pattern detectors over the observations dataset.

The scaffold server shipped list/get tools only, which pushes every pattern
question ("who is sustainedly low", "who is past due", "whose scores dropped")
into the model's context: thousands of rows in, arithmetic done by a language
model, no audit trail. These functions move that work server-side. Each one:

* takes its judgement rule as **parameters with defended defaults**, so the
  rule is visible and adjustable rather than hardcoded to one question;
* judges against the **project's own policy** (``score_threshold``,
  ``cadence_days``) rather than a global constant, because a 72 is a pass in
  PRJ-1 and a fail in PRJ-5;
* returns the **evidence** — observation ids, note ids, per-window scores —
  so every claim the agent makes is traceable to a record;
* returns the **rule it applied** alongside the findings, so the answer can
  state its own definition.

Where a rule has a genuine judgement call, the docstring says what the call is
and why. Those notes are surfaced to the agent through the tool descriptions.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from dataset import (
    COACH_NOTES,
    OBS_DF,
    PROJECTS_BY_ID,
    RESP_DF,
    TOPIC_NAMES,
    cadence_for,
    day,
    days_between,
    filter_frame,
    iter_associates,
    project_policy,
    question_lookup,
    r2,
    resolve_window,
    threshold_for,
    today,
    user_brief,
)

# ---------------------------------------------------------------------------
# Q6 — sustained low scoring on a topic
# ---------------------------------------------------------------------------


def sustained_low_topic_scores(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    window: int = 4,
    min_below: int = 2,
    threshold: float | None = None,
    min_observations: int = 3,
    include_watchlist: bool = True,
) -> dict[str, Any]:
    """Associates whose recent scores on one topic keep landing below the bar.

    Definition, and why:

    * The unit is an **(associate, topic) pair**, scored per observation as the
      mean of that topic's question scores. A weak topic inside an otherwise
      fine observation is exactly the pattern a coach can act on, and it is
      invisible in ``overall_score``.
    * **"Sustained" = at least ``min_below`` of the last ``window``
      observations below the bar** (default 2 of 4). One bad day cannot reach
      two, which is the whole point; requiring a strict consecutive run would
      instead miss the common real shape of an intermittent lapse.
    * The bar is the **project's own ``score_threshold``** unless overridden.
    * Pairs with fewer than ``min_observations`` in the window are skipped —
      there is no trend in one or two data points.

    ``consecutive_below`` (the run ending at the most recent observation) is
    reported so a still-deteriorating case can be told apart from one that has
    recovered. The watchlist holds pairs one observation short of the rule.
    """
    window = max(2, int(window))
    min_below = max(1, int(min_below))

    resp = filter_frame(
        RESP_DF, org_id=org_id, project_id=project_id, subject_user_id=subject_user_id
    )
    if resp.empty:
        return {
            "rule": _sustained_rule(window, min_below, threshold, min_observations),
            "findings": [],
            "watchlist": [],
            "pairs_evaluated": 0,
        }

    # One row per (subject, topic, observation): the topic score for that visit.
    topic_scores = (
        resp.groupby(
            ["subject_user_id", "project_id", "topic_id", "observation_id"],
            as_index=False,
        )
        .agg(
            topic_score=("score", "mean"),
            completed_dt=("completed_dt", "first"),
            questions=("question_id", "size"),
            worst_score=("score", "min"),
        )
        .sort_values("completed_dt")
    )

    findings: list[dict] = []
    watchlist: list[dict] = []
    pairs = 0

    for (subject, proj, topic), grp in topic_scores.groupby(
        ["subject_user_id", "project_id", "topic_id"], sort=False
    ):
        pairs += 1
        recent = grp.tail(window)
        if len(recent) < min_observations:
            continue

        bar = threshold_for(proj, threshold)
        below_mask = recent["topic_score"] < bar
        n_below = int(below_mask.sum())
        if n_below == 0:
            continue

        # Longest run of below-bar observations ending at the most recent one.
        consecutive = 0
        for flag in reversed(below_mask.tolist()):
            if flag:
                consecutive += 1
            else:
                break

        record = {
            "subject": user_brief(subject),
            "project_id": proj,
            "topic_id": topic,
            "topic_name": TOPIC_NAMES.get(topic, topic),
            "threshold": r2(bar),
            "observations_in_window": len(recent),
            "observations_below_threshold": n_below,
            "consecutive_below_latest": consecutive,
            "window_mean": r2(recent["topic_score"].mean()),
            "window_min": r2(recent["topic_score"].min()),
            "worst_single_question_score": r2(recent["worst_score"].min()),
            "gap_below_threshold": r2(bar - recent["topic_score"].mean()),
            "first_in_window": day(recent["completed_dt"].min()),
            "latest_in_window": day(recent["completed_dt"].max()),
            "evidence": [
                {
                    "observation_id": row.observation_id,
                    "completed_at": day(row.completed_dt),
                    "topic_score": r2(row.topic_score),
                    "below_threshold": bool(row.topic_score < bar),
                }
                for row in recent.itertuples()
            ],
        }

        if n_below >= min_below:
            findings.append(record)
        elif include_watchlist and n_below == min_below - 1:
            watchlist.append(record)

    findings.sort(key=lambda f: (f["window_mean"] if f["window_mean"] is not None else 0))
    watchlist.sort(key=lambda f: (f["window_mean"] if f["window_mean"] is not None else 0))

    return {
        "rule": _sustained_rule(window, min_below, threshold, min_observations),
        "scope": {"org_id": org_id, "project_id": project_id, "subject_user_id": subject_user_id},
        "pairs_evaluated": pairs,
        "findings": findings,
        "watchlist": watchlist[:10],
    }


def _sustained_rule(window, min_below, threshold, min_observations) -> dict:
    return {
        "unit": "(associate, topic) pair",
        "topic_score": "mean of that topic's question scores within one observation",
        "sustained_when": f"at least {min_below} of the last {window} observations score below the threshold",
        "threshold": "project score_threshold" if threshold is None else float(threshold),
        "min_observations_in_window": min_observations,
        "rationale": (
            f"{min_below}-of-{window} excludes a single bad day (which cannot reach "
            f"{min_below}) without demanding a strict consecutive run, which would miss "
            "intermittent lapses. The threshold is per-project because the same score "
            "passes in one project and fails in another."
        ),
    }


# ---------------------------------------------------------------------------
# Q7 — critical-question failures
# ---------------------------------------------------------------------------

# Severity for a failed critical question is banded on the **absolute** score,
# not on the distance below the project threshold. A safety manager reading
# "lockout-tagout: 43" reacts to the 43; that it happens to sit 22 points under
# a 65 bar is secondary, and a gap-only band would rate the same 43 as merely
# "high" in a lenient project and "severe" in a strict one.
CRITICAL_SEVERITY_BANDS = [(50.0, "severe"), (65.0, "high")]


def critical_failures(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    threshold: float | None = None,
    lookback_days: int | None = 60,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Critical-question responses that scored below the bar.

    ``is_critical`` lives only on the response rows, so this is the one
    question that cannot be answered from observation headers at all.

    Defaults: the bar is the **project's own ``score_threshold``** (a critical
    question is held to at least the standard the project sets for everything
    else), and the window is the **last 60 days** — long enough to catch a
    repeat pattern, short enough that "recent" still means recent. Pass
    ``lookback_days=None`` for the full history.

    Findings are sorted worst-first and annotated with ``repeat_count``: the
    number of times that same associate has failed that same critical question
    inside the window. A repeat is the difference between an incident and an
    unaddressed hazard.
    """
    since_ts, until_ts = resolve_window(since, until, lookback_days)
    resp = filter_frame(
        RESP_DF,
        org_id=org_id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        since=since_ts,
        until=until_ts,
    )
    crit = resp[resp["is_critical"]].copy()
    if crit.empty:
        return {
            "rule": _critical_rule(threshold, since_ts, until_ts),
            "critical_responses_evaluated": 0,
            "findings": [],
            "repeat_offenders": [],
        }

    crit["bar"] = crit["project_id"].map(lambda p: threshold_for(p, threshold))
    failures = crit[crit["score"] < crit["bar"]].copy()

    evaluated = int(len(crit))
    if failures.empty:
        return {
            "rule": _critical_rule(threshold, since_ts, until_ts),
            "critical_responses_evaluated": evaluated,
            "findings": [],
            "repeat_offenders": [],
            "note": "No critical-question response scored below its project threshold in this window.",
        }

    repeat_counts = (
        failures.groupby(["subject_user_id", "question_id"]).size().to_dict()
    )
    obs_lookup = OBS_DF.set_index("observation_id")

    findings = []
    for row in failures.sort_values("score").itertuples():
        header = (
            obs_lookup.loc[row.observation_id]
            if row.observation_id in obs_lookup.index
            else None
        )
        gap = float(row.bar) - float(row.score)
        findings.append(
            {
                "observation_id": row.observation_id,
                "completed_at": day(row.completed_dt),
                "days_ago": days_between(row.completed_dt, today()),
                "subject": user_brief(row.subject_user_id),
                "observer": user_brief(
                    header["observer_user_id"] if header is not None else None
                ),
                "manager": user_brief(_manager_of(row.subject_user_id)),
                "project_id": row.project_id,
                "topic_id": row.topic_id,
                "topic_name": TOPIC_NAMES.get(row.topic_id, row.topic_id),
                "question_id": row.question_id,
                "question_text": row.question_text,
                "score": r2(row.score),
                "threshold": r2(row.bar),
                "points_below_threshold": r2(gap),
                "severity": _severity(float(row.score)),
                "observation_overall_score": r2(
                    header["overall_score"] if header is not None else None
                ),
                "comment": row.comment,
                "repeat_count": int(
                    repeat_counts.get((row.subject_user_id, row.question_id), 1)
                ),
            }
        )

    repeats = [
        {
            "subject": user_brief(subject),
            "question_id": qid,
            "question_text": question_lookup().get(qid, {}).get("question_text"),
            "failures": int(count),
            "observation_ids": sorted(
                failures[
                    (failures["subject_user_id"] == subject)
                    & (failures["question_id"] == qid)
                ]["observation_id"].tolist()
            ),
        }
        for (subject, qid), count in sorted(
            repeat_counts.items(), key=lambda kv: -kv[1]
        )
        if count > 1
    ]

    return {
        "rule": _critical_rule(threshold, since_ts, until_ts),
        "critical_responses_evaluated": evaluated,
        "failures_found": len(findings),
        "findings": findings[: int(limit)],
        "repeat_offenders": repeats,
    }


def _manager_of(user_id: str) -> str | None:
    from dataset import USERS_BY_ID

    u = USERS_BY_ID.get(user_id) or {}
    return u.get("manager_id")


def _severity(score: float) -> str:
    """Severity band for a critical-question score that already failed."""
    for cutoff, label in CRITICAL_SEVERITY_BANDS:
        if score < cutoff:
            return label
    return "moderate"


def _critical_rule(threshold, since_ts, until_ts) -> dict:
    return {
        "population": "responses where the template marks the question is_critical",
        "threshold": "project score_threshold" if threshold is None else float(threshold),
        "window": {"since": day(since_ts), "until": day(until_ts)},
        "severity_bands": {
            "severe": "critical-question score below 50",
            "high": "score 50-65",
            "moderate": "score 65+ but still below the project threshold",
        },
    }


# ---------------------------------------------------------------------------
# Q9 — cadence compliance
# ---------------------------------------------------------------------------


def cadence_violations(
    org_id: str | None = None,
    project_id: str | None = None,
    grace_days: int = 0,
    include_compliant: bool = False,
) -> dict[str, Any]:
    """Associates not observed within their project's ``cadence_days``.

    ``days_overdue = days_since_last_observation - cadence_days - grace_days``,
    measured from the end of the anchor day so an observation completed today
    counts as zero days old rather than a fraction.

    Cadence is per project (7 days in PRJ-3, 30 in PRJ-5), so this is computed
    against each associate's own project policy. Associates with **no**
    observation at all are reported separately with
    ``never_observed: true`` — they are a coverage gap, not a late one, and
    collapsing them into a day count would hide that.
    """
    anchor = today()
    last_obs = (
        OBS_DF.sort_values("completed_dt")
        .groupby("subject_user_id")
        .agg(
            last_completed=("completed_dt", "max"),
            last_observation_id=("observation_id", "last"),
            last_score=("overall_score", "last"),
            total_observations=("observation_id", "size"),
        )
    )

    overdue, compliant, never = [], [], []
    for assoc in iter_associates(org_id=org_id, project_id=project_id):
        proj = assoc["project_id"]
        cadence = cadence_for(proj)
        uid = assoc["user_id"]

        if uid not in last_obs.index:
            never.append(
                {
                    "subject": user_brief(uid),
                    "project_id": proj,
                    "cadence_days": cadence,
                    "never_observed": True,
                    "manager": user_brief(assoc.get("manager_id")),
                }
            )
            continue

        row = last_obs.loc[uid]
        days_since = days_between(row["last_completed"], anchor)
        allowed = (cadence or 0) + int(grace_days)
        record = {
            "subject": user_brief(uid),
            "manager": user_brief(assoc.get("manager_id")),
            "project_id": proj,
            "project_name": (PROJECTS_BY_ID.get(proj) or {}).get("name"),
            "cadence_days": cadence,
            "grace_days": int(grace_days),
            "last_observed": day(row["last_completed"]),
            "last_observation_id": row["last_observation_id"],
            "last_overall_score": r2(row["last_score"]),
            "total_observations": int(row["total_observations"]),
            "days_since_last": days_since,
            "days_overdue": r2((days_since or 0) - allowed),
            "cadences_missed": r2((days_since or 0) / cadence) if cadence else None,
        }
        if record["days_overdue"] > 0:
            overdue.append(record)
        else:
            compliant.append(record)

    overdue.sort(key=lambda r: -(r["days_overdue"] or 0))

    total = len(overdue) + len(compliant) + len(never)
    return {
        "rule": {
            "measured_from": day(anchor),
            "formula": "days_overdue = days_since_last_observation - cadence_days - grace_days",
            "cadence_source": "each associate's project cadence_days",
            "grace_days": int(grace_days),
        },
        "associates_evaluated": total,
        "overdue_count": len(overdue),
        "compliance_rate": r2(100.0 * (total - len(overdue) - len(never)) / total) if total else None,
        "overdue": overdue,
        "never_observed": never,
        "compliant": compliant if include_compliant else [],
    }


# ---------------------------------------------------------------------------
# Q10 — decline against the associate's own baseline
# ---------------------------------------------------------------------------


def score_declines(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    recent_window: int = 3,
    min_baseline: int = 3,
    min_drop: float = 5.0,
    min_z: float = 2.0,
    require_both: bool = True,
) -> dict[str, Any]:
    """Associates whose recent observations sit below their **own** baseline.

    The comparison is self-referential on purpose: a strong performer sliding
    from 92 to 84 is a real change that a cohort comparison would bury, since
    84 is still above average.

    "Meaningfully" is deliberately **two tests at once** (``require_both``):

    * an **absolute** drop of at least ``min_drop`` points (default 5) — small
      enough to catch a genuine slide, large enough that nobody is asked to
      act on statistical lint;
    * a drop of at least ``min_z`` baseline standard deviations (default 2.0)
      — scaled to how steady that person normally is, so a 6-point wobble on a
      history that routinely swings 8 points is not called a decline.

    Either test alone misfires: absolute-only flags noisy performers, z-only
    flags a 1.5-point drop on a very consistent one. Both numbers are reported
    so the reader can see which test bound the result. Baseline standard
    deviations below 1 point are floored at 1.0 to stop a near-flat history
    from producing an enormous z on a trivial move.
    """
    recent_window = max(2, int(recent_window))
    obs = filter_frame(
        OBS_DF, org_id=org_id, project_id=project_id, subject_user_id=subject_user_id
    ).sort_values("completed_dt")

    findings, evaluated, skipped = [], 0, 0
    for subject, grp in obs.groupby("subject_user_id", sort=False):
        if len(grp) < recent_window + min_baseline:
            skipped += 1
            continue
        evaluated += 1

        recent = grp.tail(recent_window)
        baseline = grp.iloc[: -recent_window]

        b_mean = float(baseline["overall_score"].mean())
        r_mean = float(recent["overall_score"].mean())
        b_sd = float(baseline["overall_score"].std(ddof=1) or 0.0)
        effective_sd = max(b_sd, 1.0)
        drop = b_mean - r_mean
        z = drop / effective_sd

        passes_abs = drop >= float(min_drop)
        passes_z = z >= float(min_z)
        flagged = (passes_abs and passes_z) if require_both else (passes_abs or passes_z)
        if not flagged:
            continue

        findings.append(
            {
                "subject": user_brief(subject),
                "manager": user_brief(_manager_of(subject)),
                "project_id": grp["project_id"].iloc[-1],
                "total_observations": len(grp),
                "baseline_observations": len(baseline),
                "baseline_mean": r2(b_mean),
                "baseline_sd": r2(b_sd),
                "baseline_period": f"{day(baseline['completed_dt'].min())} to {day(baseline['completed_dt'].max())}",
                "recent_observations": recent_window,
                "recent_mean": r2(r_mean),
                "recent_period": f"{day(recent['completed_dt'].min())} to {day(recent['completed_dt'].max())}",
                "drop_points": r2(drop),
                "drop_sds": r2(z),
                "drop_pct": r2(100.0 * drop / b_mean) if b_mean else None,
                "passes_absolute_test": passes_abs,
                "passes_volatility_test": passes_z,
                "below_project_threshold_now": bool(
                    r_mean < threshold_for(grp["project_id"].iloc[-1])
                ),
                "recent_evidence": [
                    {
                        "observation_id": row.observation_id,
                        "completed_at": day(row.completed_dt),
                        "overall_score": r2(row.overall_score),
                    }
                    for row in recent.itertuples()
                ],
                "worst_topics_recent": _worst_topics(
                    subject, recent["observation_id"].tolist()
                ),
            }
        )

    findings.sort(key=lambda f: f["drop_points"], reverse=True)
    return {
        "rule": {
            "comparison": f"mean of last {recent_window} observations vs mean of all prior (>= {min_baseline})",
            "meaningful_when": (
                f"drop >= {min_drop} points AND drop >= {min_z} baseline SDs"
                if require_both
                else f"drop >= {min_drop} points OR drop >= {min_z} baseline SDs"
            ),
            "baseline_sd_floor": 1.0,
            "rationale": (
                "Self-referential baseline, because a decline from a high level is "
                "invisible against a cohort. Two tests together: the absolute test "
                "keeps the finding worth a manager's time, the volatility test keeps "
                "it distinguishable from that person's normal variation."
            ),
        },
        "associates_evaluated": evaluated,
        "associates_skipped_insufficient_history": skipped,
        "findings": findings,
    }


def _worst_topics(subject: str, observation_ids: list[str], top: int = 3) -> list[dict]:
    rows = RESP_DF[
        (RESP_DF["subject_user_id"] == subject)
        & (RESP_DF["observation_id"].isin(observation_ids))
    ]
    if rows.empty:
        return []
    agg = rows.groupby("topic_id")["score"].mean().sort_values()
    return [
        {"topic_id": t, "topic_name": TOPIC_NAMES.get(t, t), "mean_score": r2(v)}
        for t, v in agg.head(top).items()
    ]


# ---------------------------------------------------------------------------
# Q8 — recurring concerns in free-text coach notes
# ---------------------------------------------------------------------------

# Two lexicons, kept apart on purpose. Recurrence language is what turns a
# note from an observation into evidence of a pattern the coach has already
# raised; concern language says the content is negative. "Again took feedback
# well" matches recurrence but not concern, and should not be flagged.
RECURRENCE_TERMS = [
    "still", "again", "ongoing", "recurring", "repeat", "persist", "continues",
    "continued", "as before", "same issue", "same problem", "previous",
    "previously", "last week", "last review", "last cycle", "already flagged",
    "raised before", "discussed last", "third time", "second time", "no change",
    # These presuppose an earlier state, so they carry recurrence on their own
    # even with no explicit back-reference ("not improving on the care step").
    "not improving", "no improvement", "not yet improv", "despite", "escalat",
]

CONCERN_TERMS = [
    "struggl", "not improving", "no improvement", "issue", "problem", "concern",
    "fail", "miss", "lapse", "below", "unsafe", "risk", "error", "mistake",
    "incorrect", "deficien", "shortfall", "needs work", "needs improvement",
    "follow-up", "follow up", "escalat", "corrective", "retrain", "coaching",
    "warning", "gap",
]


def _match_terms(body: str, terms: list[str]) -> list[str]:
    low = (body or "").lower()
    return [t for t in terms if t in low]


def recurring_note_concerns(
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    min_notes: int = 2,
    window_days: int | None = 120,
    require_concern_language: bool = True,
    extra_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Associates whose coach notes show a concern raised more than once.

    Most notes in this dataset are routine ("Clean handoff to next shift",
    "Took feedback well"). The signal worth surfacing is a note whose author
    is pointing back at something already raised.

    A note is flagged when it contains **recurrence language** ("still",
    "again", "ongoing", "already flagged", "despite the previous") and — when
    ``require_concern_language`` is on — also **negative-content language**
    ("struggling", "not improving", "issue", "follow-up"). Both lexicons are
    needed: "again took feedback well" recurs but is not a concern.

    An associate is reported when at least ``min_notes`` (default 2) flagged
    notes fall inside ``window_days`` (default 120). One flagged note is a bad
    day; two authors, or two dates, pointing at the same thing is the pattern
    the question is asking for.

    Every finding carries the matched terms and the **full note bodies** with
    ``note_id`` and author, so the citation is verifiable rather than asserted.
    Free-text matching is a heuristic — ``candidates_scanned`` is reported so
    the coverage of the sweep is visible.
    """
    recurrence = RECURRENCE_TERMS + [t.lower() for t in (extra_terms or [])]
    since_ts, until_ts = resolve_window(lookback_days=window_days)

    scanned, flagged = 0, []
    for note in COACH_NOTES:
        if org_id and note["org_id"] != org_id:
            continue
        if project_id and note["project_id"] != project_id:
            continue
        if subject_user_id and note["subject_user_id"] != subject_user_id:
            continue
        created = pd.Timestamp(note["created_at"])
        if since_ts is not None and created < since_ts:
            continue
        if until_ts is not None and created > until_ts:
            continue
        scanned += 1

        rec_hits = _match_terms(note["body"], recurrence)
        if not rec_hits:
            continue
        con_hits = _match_terms(note["body"], CONCERN_TERMS)
        if require_concern_language and not con_hits:
            continue

        flagged.append(
            {
                "note_id": note["note_id"],
                "subject_user_id": note["subject_user_id"],
                "observation_id": note["observation_id"],
                "project_id": note["project_id"],
                "author": user_brief(note["author_user_id"]),
                "created_at": day(created),
                "body": note["body"],
                "recurrence_terms": rec_hits,
                "concern_terms": con_hits,
            }
        )

    by_subject: dict[str, list[dict]] = {}
    for note in flagged:
        by_subject.setdefault(note["subject_user_id"], []).append(note)

    findings = []
    for subject, notes in by_subject.items():
        if len(notes) < int(min_notes):
            continue
        notes.sort(key=lambda n: n["created_at"])
        authors = sorted({n["author"]["user_id"] for n in notes if n["author"]})
        obs_ids = sorted({n["observation_id"] for n in notes})
        findings.append(
            {
                "subject": user_brief(subject),
                "manager": user_brief(_manager_of(subject)),
                "project_id": notes[0]["project_id"],
                "flagged_note_count": len(notes),
                "total_notes_for_subject": sum(
                    1 for n in COACH_NOTES if n["subject_user_id"] == subject
                ),
                "distinct_authors": len(authors),
                "author_ids": authors,
                "first_flagged": notes[0]["created_at"],
                "latest_flagged": notes[-1]["created_at"],
                "span_days": days_between(
                    pd.Timestamp(notes[0]["created_at"]),
                    pd.Timestamp(notes[-1]["created_at"]),
                ),
                "referenced_observation_ids": obs_ids,
                "notes": notes,
                "score_context": _note_score_context(subject, obs_ids),
            }
        )

    findings.sort(key=lambda f: (-f["flagged_note_count"], f["subject"]["user_id"]))
    singletons = [
        {"subject": user_brief(s), "notes": n}
        for s, n in by_subject.items()
        if len(n) < int(min_notes)
    ]

    return {
        "rule": {
            "flagged_note": (
                "body contains recurrence language"
                + (" AND concern language" if require_concern_language else "")
            ),
            "recurrence_lexicon": recurrence,
            "concern_lexicon": CONCERN_TERMS if require_concern_language else [],
            "pattern_when": f"at least {min_notes} flagged notes within {window_days} days",
            "window": {"since": day(since_ts), "until": day(until_ts)},
            "caveat": "keyword heuristic over free text; single flagged notes are returned separately as near-misses",
        },
        "candidates_scanned": scanned,
        "notes_flagged": len(flagged),
        "findings": findings,
        "single_flagged_note": singletons,
    }


def _note_score_context(subject: str, observation_ids: list[str]) -> dict:
    """Do the scores agree with what the notes are saying? A recurring concern
    that also shows in the numbers is a stronger case than one that does not."""
    rows = RESP_DF[RESP_DF["observation_id"].isin(observation_ids)]
    if rows.empty:
        return {}
    topics = rows.groupby("topic_id")["score"].mean().sort_values()
    subj_all = OBS_DF[OBS_DF["subject_user_id"] == subject]
    return {
        "weakest_topic_on_cited_observations": {
            "topic_id": topics.index[0],
            "topic_name": TOPIC_NAMES.get(topics.index[0], topics.index[0]),
            "mean_score": r2(topics.iloc[0]),
        },
        "mean_overall_on_cited_observations": r2(
            OBS_DF[OBS_DF["observation_id"].isin(observation_ids)]["overall_score"].mean()
        ),
        "mean_overall_all_time": r2(subj_all["overall_score"].mean()),
    }


def search_coach_notes(
    text: str,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Free-text search across coach note bodies.

    Space-separated terms are AND-ed; quote a phrase to match it literally.
    Matching is case-insensitive substring, which is the right tool for a
    786-note corpus of short, templated notes.
    """
    phrases = re.findall(r'"([^"]+)"', text or "")
    remainder = re.sub(r'"[^"]+"', " ", text or "")
    terms = [t.lower() for t in remainder.split() if t] + [p.lower() for p in phrases]

    hits = []
    for note in COACH_NOTES:
        if org_id and note["org_id"] != org_id:
            continue
        if project_id and note["project_id"] != project_id:
            continue
        if subject_user_id and note["subject_user_id"] != subject_user_id:
            continue
        low = note["body"].lower()
        if terms and not all(t in low for t in terms):
            continue
        hits.append(
            {
                "note_id": note["note_id"],
                "subject": user_brief(note["subject_user_id"]),
                "author": user_brief(note["author_user_id"]),
                "observation_id": note["observation_id"],
                "project_id": note["project_id"],
                "created_at": day(pd.Timestamp(note["created_at"])),
                "body": note["body"],
            }
        )

    hits.sort(key=lambda h: h["created_at"], reverse=True)
    subjects = {}
    for h in hits:
        subjects[h["subject"]["user_id"]] = subjects.get(h["subject"]["user_id"], 0) + 1

    return {
        "query": {"raw": text, "terms": terms},
        "total_matching": len(hits),
        "notes_per_subject": dict(sorted(subjects.items(), key=lambda kv: -kv[1])),
        "items": hits[: int(limit)],
    }
