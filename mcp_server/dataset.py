"""
Dataset layer for the observations MCP server.

Loads the fixture data once at import time and exposes it as indexed
in-memory structures plus two pandas frames (observations, responses).

Everything above this module — the MCP tools — is a thin, typed shell over
the helpers here. Keeping the joins and the pandas work in one place is what
lets the tools stay cheap: an aggregation runs here, and only the aggregate
crosses the wire into the model's context.

Known quirks in the fixture data, handled deliberately:

* ``projects.json`` has no ``template_id`` column even though the exercise
  brief says it does. We derive project -> template from ``templates.json``
  (each template carries ``project_id``) and cross-check it against the
  ``template_id`` denormalized onto every observation.
* Observer ids (``OBS-001``) and observation ids (``OBS-000042``) share a
  prefix and differ only in width. :func:`classify_id` disambiguates so a
  question naming "OBS-002" resolves to a person, not an observation.
* Observer display names are not unique (two different observers in PRJ-3 are
  both named "Blair Davis"), so every user-facing row carries the id.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# ---------------------------------------------------------------------------
# Paths and the "today" anchor
# ---------------------------------------------------------------------------

DATA_DIR = Path(
    os.environ.get(
        "OBSERVATIONS_DATA_DIR",
        Path(__file__).resolve().parent.parent / "data",
    )
)

# The fixture data is anchored to a fixed date, so "last 30 days" and "past
# due" must be computed against it rather than the wall clock. Overridable for
# tests and for pointing the server at freshly generated data.
TODAY_STR = os.environ.get("OBSERVATIONS_TODAY", "2026-05-12")


def today() -> datetime:
    """End of the anchor day, UTC. Inclusive upper bound for date windows."""
    d = datetime.fromisoformat(TODAY_STR).replace(tzinfo=timezone.utc)
    return d + timedelta(days=1) - timedelta(microseconds=1)


def today_date() -> str:
    return TODAY_STR


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text())


ORGS: list[dict] = _load_json("organizations.json")
PROJECTS: list[dict] = _load_json("projects.json")
USERS: list[dict] = _load_json("users.json")
LOCATIONS: list[dict] = _load_json("locations.json")
WORK_AREAS: list[dict] = _load_json("work_areas.json")
TEMPLATES: list[dict] = _load_json("templates.json")
OBSERVATIONS: list[dict] = _load_json("observations.json")
COACH_NOTES: list[dict] = _load_json("coach_notes.json")

ORGS_BY_ID = {o["org_id"]: o for o in ORGS}
PROJECTS_BY_ID = {p["project_id"]: p for p in PROJECTS}
USERS_BY_ID = {u["user_id"]: u for u in USERS}
LOCATIONS_BY_ID = {l["location_id"]: l for l in LOCATIONS}
WORK_AREAS_BY_ID = {w["work_area_id"]: w for w in WORK_AREAS}
TEMPLATES_BY_ID = {t["template_id"]: t for t in TEMPLATES}
OBSERVATIONS_BY_ID = {o["observation_id"]: o for o in OBSERVATIONS}
NOTES_BY_ID = {n["note_id"]: n for n in COACH_NOTES}

# project_id -> template_id, derived because projects.json omits it.
TEMPLATE_ID_BY_PROJECT = {t["project_id"]: t["template_id"] for t in TEMPLATES}

# Notes grouped by the observation they hang off, for cheap hydration.
NOTES_BY_OBSERVATION: dict[str, list[dict]] = {}
for _n in COACH_NOTES:
    NOTES_BY_OBSERVATION.setdefault(_n["observation_id"], []).append(_n)

NOTES_BY_SUBJECT: dict[str, list[dict]] = {}
for _n in COACH_NOTES:
    NOTES_BY_SUBJECT.setdefault(_n["subject_user_id"], []).append(_n)


# --- question metadata from the templates ----------------------------------

# (template_id, question_id) -> {topic_id, topic_name, text, is_critical}
QUESTION_META: dict[tuple[str, str], dict] = {}
for _t in TEMPLATES:
    for _topic in _t["topics"]:
        for _q in _topic["questions"]:
            QUESTION_META[(_t["template_id"], _q["question_id"])] = {
                "topic_id": _topic["topic_id"],
                "topic_name": _topic["name"],
                "question_text": _q["text"],
                "is_critical": _q["is_critical"],
            }

TOPIC_NAMES: dict[str, str] = {
    topic["topic_id"]: topic["name"] for t in TEMPLATES for topic in t["topics"]
}

CRITICAL_QUESTIONS: dict[str, dict] = {}
for (_tid, _qid), _meta in QUESTION_META.items():
    if _meta["is_critical"]:
        CRITICAL_QUESTIONS[_qid] = {"template_id": _tid, "question_id": _qid, **_meta}


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def _observations_frame() -> pd.DataFrame:
    df = pd.DataFrame(OBSERVATIONS)
    df["completed_dt"] = pd.to_datetime(df["completed_at"], utc=True)
    df["overall_score"] = df["overall_score"].astype(float)
    return df.sort_values("completed_dt").reset_index(drop=True)


def _responses_frame() -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / "observation_responses.parquet")
    df["completed_dt"] = pd.to_datetime(df["completed_at"], utc=True)
    df["score"] = df["score"].astype(float)
    df["is_critical"] = df["is_critical"].astype(bool)
    return df.sort_values("completed_dt").reset_index(drop=True)


OBS_DF: pd.DataFrame = _observations_frame()
RESP_DF: pd.DataFrame = _responses_frame()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO date or datetime into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve_window(
    since: str | None = None,
    until: str | None = None,
    lookback_days: int | None = None,
) -> tuple[pd.Timestamp | None, pd.Timestamp]:
    """Turn loose date arguments into a concrete [since, until] window.

    ``until`` defaults to the end of the anchor day. ``lookback_days`` is
    measured back from ``until`` and only applies when ``since`` is absent.
    """
    until_dt = parse_dt(until) or today()
    since_dt = parse_dt(since)
    if since_dt is None and lookback_days is not None:
        since_dt = until_dt - timedelta(days=lookback_days)
    return (
        pd.Timestamp(since_dt) if since_dt else None,
        pd.Timestamp(until_dt),
    )


def r2(value: Any) -> Any:
    """Round floats to 2dp. Raw scores carry 14 digits of noise that cost
    tokens and imply precision the data does not have."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, bool)):
        return value
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value


def day(ts: Any) -> str | None:
    """ISO date (no time) for a timestamp-ish value."""
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def days_between(earlier: Any, later: Any) -> float | None:
    if earlier is None or later is None or pd.isna(earlier) or pd.isna(later):
        return None
    delta = pd.Timestamp(later) - pd.Timestamp(earlier)
    return round(delta.total_seconds() / 86400.0, 1)


ID_PATTERNS = [
    ("organization", re.compile(r"^ORG-[A-Z]+$")),
    ("project", re.compile(r"^PRJ-\d+$")),
    ("template", re.compile(r"^TPL-")),
    ("associate", re.compile(r"^USR-\d+$")),
    ("manager", re.compile(r"^MGR-\d+$")),
    ("note", re.compile(r"^NOTE-\d+$")),
    ("response", re.compile(r"^RSP-\d+$")),
    ("location", re.compile(r"^LOC-\d+$")),
    ("work_area", re.compile(r"^WA-\d+$")),
]


def classify_id(value: str) -> str:
    """Best-effort entity type for an id string.

    ``OBS-`` is ambiguous by construction in this dataset: observers are
    ``OBS-001`` (3 digits) and observations are ``OBS-000042`` (6 digits).
    We resolve by checking the actual indexes before falling back on width.
    """
    v = (value or "").strip().upper()
    for name, pattern in ID_PATTERNS:
        if pattern.match(v):
            return name
    if v.startswith("OBS-"):
        if v in OBSERVATIONS_BY_ID:
            return "observation"
        if v in USERS_BY_ID:
            return "observer"
        digits = v.split("-", 1)[1]
        return "observation" if len(digits) >= 5 else "observer"
    return "unknown"


def user_brief(user_id: str | None) -> dict | None:
    """Compact user reference: enough to name a person without a second call."""
    if not user_id:
        return None
    u = USERS_BY_ID.get(user_id)
    if not u:
        return {"user_id": user_id, "name": None, "role": None, "missing": True}
    return {
        "user_id": u["user_id"],
        "name": u["name"],
        "role": u["role"],
        "project_id": u.get("project_id"),
        "org_id": u.get("org_id"),
    }


def project_policy(project_id: str | None) -> dict | None:
    """Project row plus its derived template id. The policy fields
    (cadence_days, score_threshold) are what every 'past due' or 'below
    threshold' judgement has to be measured against."""
    if not project_id:
        return None
    p = PROJECTS_BY_ID.get(project_id)
    if not p:
        return None
    return {
        **p,
        "template_id": TEMPLATE_ID_BY_PROJECT.get(project_id),
        "org_name": (ORGS_BY_ID.get(p["org_id"]) or {}).get("name"),
    }


def threshold_for(project_id: str | None, override: float | None = None) -> float:
    """Score floor to judge against: explicit override wins, else the
    project's own ``score_threshold``, else a neutral 70."""
    if override is not None:
        return float(override)
    p = PROJECTS_BY_ID.get(project_id or "")
    return float(p["score_threshold"]) if p else 70.0


def cadence_for(project_id: str | None, override: int | None = None) -> int | None:
    if override is not None:
        return int(override)
    p = PROJECTS_BY_ID.get(project_id or "")
    return int(p["cadence_days"]) if p else None


def scope_projects(
    org_id: str | None = None, project_id: str | None = None
) -> list[str]:
    """Project ids implied by an org/project scope. Empty list means 'all'."""
    if project_id:
        return [project_id]
    if org_id:
        return [p["project_id"] for p in PROJECTS if p["org_id"] == org_id]
    return []


def filter_frame(
    df: pd.DataFrame,
    org_id: str | None = None,
    project_id: str | None = None,
    subject_user_id: str | None = None,
    observer_user_id: str | None = None,
    since: pd.Timestamp | None = None,
    until: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Apply the common scope filters to an observations/responses frame."""
    out = df
    if org_id:
        out = out[out["org_id"] == org_id]
    if project_id:
        out = out[out["project_id"] == project_id]
    if subject_user_id:
        out = out[out["subject_user_id"] == subject_user_id]
    if observer_user_id and "observer_user_id" in out.columns:
        out = out[out["observer_user_id"] == observer_user_id]
    if since is not None:
        out = out[out["completed_dt"] >= since]
    if until is not None:
        out = out[out["completed_dt"] <= until]
    return out


def page(
    items: list[Any], limit: int | None, offset: int = 0
) -> dict[str, Any]:
    """Uniform pagination envelope.

    Every list-shaped tool returns this so the model can always tell whether
    it is looking at the whole answer or a slice of one — silent truncation is
    how an agent ends up confidently wrong.
    """
    total = len(items)
    offset = max(0, int(offset or 0))
    window = items[offset:] if limit is None else items[offset : offset + int(limit)]
    return {
        "total_matching": total,
        "returned": len(window),
        "offset": offset,
        "truncated": offset + len(window) < total,
        "items": window,
    }


@lru_cache(maxsize=1)
def question_lookup() -> dict[str, dict]:
    """question_id -> metadata, flattened across templates. Question ids are
    unique per project in this dataset, so a flat map is safe."""
    return {qid: meta for (_tid, qid), meta in QUESTION_META.items()}


def observation_topic_scores(observation_id: str) -> dict[str, float]:
    """Mean score per topic within one observation."""
    rows = RESP_DF[RESP_DF["observation_id"] == observation_id]
    return {t: float(v) for t, v in rows.groupby("topic_id")["score"].mean().items()}


def iter_associates(
    org_id: str | None = None, project_id: str | None = None
) -> Iterable[dict]:
    for u in USERS:
        if u["role"] != "associate":
            continue
        if org_id and u["org_id"] != org_id:
            continue
        if project_id and u["project_id"] != project_id:
            continue
        yield u
