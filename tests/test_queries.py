"""
Retrieval and aggregation tests.

This is the layer the agent leans on hardest, and the layer where a quiet bug
produces a confidently wrong answer rather than a crash — a date window that
silently applies, a page mistaken for a whole result, a name-keyed grouping that
merges two people.
"""

from __future__ import annotations

import queries as Q


# --- Q4: the hydrated single-record read -----------------------------------


def test_get_observation_resolves_every_foreign_key_in_one_call():
    o = Q.get_observation("OBS-000042")
    assert o["subject"]["user_id"] == "USR-004"
    assert o["subject"]["name"] == "Drew Davis"
    assert o["observer"]["user_id"] == "OBS-002"
    assert o["subject_manager"]["user_id"] == "MGR-001"
    assert o["project"]["project_id"] == "PRJ-1"
    assert o["project"]["score_threshold"] == 65
    assert o["template"]["template_id"] == "TPL-PRJ-1"
    assert o["location"]["name"] and o["work_area"]["name"]
    assert o["overall_score"] == 83.19
    assert o["overall_score_vs_threshold"]["passes"] is True
    assert len(o["responses"]) == 10
    assert sum(1 for r in o["responses"] if r["is_critical"]) == 1


def test_get_observation_states_the_absence_of_coach_notes():
    """Silence reads as 'not checked'. An explicit zero reads as an answer."""
    o = Q.get_observation("OBS-000042")
    assert o["coach_note_count"] == 0
    assert o["coach_notes"] == []
    assert "No coach notes" in o["coach_notes_note"]


def test_get_observation_rejects_an_observer_id_with_a_useful_hint():
    o = Q.get_observation("OBS-002")
    assert o["code"] == "not_found"
    assert "observer" in o["error"]


def test_topic_rollups_are_flagged_against_the_project_threshold():
    o = Q.get_observation("OBS-000042")
    assert o["topic_scores"][0]["mean_score"] <= o["topic_scores"][-1]["mean_score"]
    for t in o["topic_scores"]:
        assert t["below_threshold"] == (t["mean_score"] < 65)


# --- Q5: aggregation -------------------------------------------------------


def test_summarize_answers_the_busiest_observer_question():
    r = Q.summarize_observations(
        group_by="observer_user_id", project_id="PRJ-3", lookback_days=30
    )
    assert r["window"] == {"since": "2026-04-12", "until": "2026-05-12"}
    assert r["total_observations"] == 78
    groups = r["groups"]
    assert len(groups) == 4
    assert groups[0]["observer_user_id"] == "OBS-012"
    assert groups[0]["observations"] == 21
    assert groups[0]["observer_name"] == "Alex Bell"
    assert [g["observations"] for g in groups] == sorted(
        [g["observations"] for g in groups], reverse=True
    )


def test_grouping_is_keyed_on_id_not_display_name():
    """OBS-010 and OBS-011 are both named 'Blair Davis'. Keying on the name
    would merge them and report one observer with 39 observations."""
    r = Q.summarize_observations(
        group_by="observer_user_id", project_id="PRJ-3", lookback_days=30
    )
    davis = [g for g in r["groups"] if g["observer_name"] == "Blair Davis"]
    assert len(davis) == 2
    assert {g["observer_user_id"] for g in davis} == {"OBS-010", "OBS-011"}
    assert sum(g["observations"] for g in davis) == 39


def test_summarize_rejects_an_ungroupable_field():
    r = Q.summarize_observations(group_by="overall_score")
    assert r["code"] == "bad_request"
    assert "groupable" in r["error"]


def test_summarize_accepts_multiple_group_keys():
    r = Q.summarize_observations(group_by=["org_id", "project_id"])
    assert r["group_count"] == 5
    assert r["total_observations"] == 1204
    assert sum(g["observations"] for g in r["groups"]) == 1204


# --- windows and pagination ------------------------------------------------


def test_no_date_argument_means_no_date_filter():
    """resolve_window always yields an `until`; a plain list call must not
    inherit a window the caller never asked for."""
    everything = Q.list_observations(limit=1)
    assert everything["total_matching"] == 1204
    windowed = Q.list_observations(lookback_days=30, limit=1)
    assert windowed["total_matching"] < 1204
    assert windowed["filters"]["since"] == "2026-04-12"


def test_pagination_envelope_never_hides_truncation():
    first = Q.list_observations(project_id="PRJ-1", limit=10)
    assert first["total_matching"] == 410
    assert first["returned"] == 10
    assert first["truncated"] is True

    second = Q.list_observations(project_id="PRJ-1", limit=10, offset=10)
    assert second["offset"] == 10
    ids = {i["observation_id"] for i in first["items"]}
    assert not (ids & {i["observation_id"] for i in second["items"]})


def test_aggregate_covers_all_matches_not_just_the_returned_page():
    r = Q.list_observations(project_id="PRJ-1", limit=5)
    assert r["returned"] == 5
    assert r["aggregate_over_all_matches"]["count"] == 410


def test_below_project_threshold_uses_each_project_floor():
    """A flat max_score cannot express this: the floor is 65 in PRJ-1/2, 70 in
    PRJ-3/4 and 75 in PRJ-5."""
    r = Q.list_observations(below_project_threshold=True, limit=200)
    thresholds = {"PRJ-1": 65, "PRJ-2": 65, "PRJ-3": 70, "PRJ-4": 70, "PRJ-5": 75}
    for item in r["items"]:
        assert item["overall_score"] < thresholds[item["project_id"]]


def test_response_summary_is_computed_over_all_matches():
    r = Q.get_observation_responses(critical_only=True, lookback_days=None, limit=5)
    assert r["returned"] == 5
    assert r["total_matching"] == 1653
    assert r["summary_over_all_matches"]["critical_rows"] == 1653


# --- the associate dossier -------------------------------------------------


def test_associate_profile_splits_topics_recent_vs_earlier():
    """The whole point: a localised slide that overall_score averages away."""
    p = Q.associate_profile("USR-017")
    safety = next(t for t in p["topics"] if t["topic_id"] == "safety_procedures")
    assert safety["change"] < -20
    assert safety["recent_below_threshold"] is True
    # Every other topic held steady or improved, which is what makes the case.
    assert all(t["change"] > -3 for t in p["topics"] if t["topic_id"] != "safety_procedures")
    # And the header score understates it: -7.7 overall against -22 on the topic.
    assert -8 < p["observations"]["change_vs_earlier"] < -7
    assert p["observations"]["change_vs_earlier"] > safety["change"] + 10


def test_associate_profile_collects_the_evidence_for_a_proposal():
    p = Q.associate_profile("USR-017")
    assert p["manager"]["user_id"] == "MGR-002"
    assert p["project"]["cadence_days"] == 14
    assert p["critical_questions"]["failures_below_threshold"] == 4
    assert all(f["question_id"] == "Q-101" for f in p["critical_questions"]["failures"])
    assert p["cadence"]["status"] == "current"

    # The sharper finding: four of the eight coach notes sit on observations where
    # the critical lockout-tagout question failed (OBS-000194 has two), and every
    # one is positive — two literally say "Nothing flagged."
    assert p["coach_note_count"] == 8
    failing = {f["observation_id"] for f in p["critical_questions"]["failures"]}
    noted_on_failing = [n for n in p["coach_notes"] if n["observation_id"] in failing]
    assert len(noted_on_failing) == 4
    assert len({n["observation_id"] for n in noted_on_failing}) == 3
    assert sum("Nothing flagged" in n["body"] for n in noted_on_failing) == 2
    assert not any(
        w in n["body"].lower()
        for n in noted_on_failing
        for w in ("safety", "lockout", "concern", "issue")
    )


def test_associate_profile_refuses_a_non_associate():
    r = Q.associate_profile("MGR-001")
    assert r["code"] == "bad_request"
    assert "not 'associate'" in r["error"]


# --- orientation -----------------------------------------------------------


def test_describe_dataset_warns_about_the_id_collision():
    d = Q.describe_dataset()
    assert d["today"] == "2026-05-12"
    assert d["counts"]["observations"] == 1204
    assert len(d["projects"]) == 5
    assert any("OBS-" in g for g in d["gotchas"])
    assert any("template_id" in g for g in d["gotchas"])
    assert {q["question_id"] for q in d["critical_questions"]} == {
        "Q-101", "Q-201", "Q-301", "Q-302", "Q-404", "Q-501", "Q-505"
    }


def test_resolve_entity_disambiguates_the_obs_prefix():
    assert Q.resolve_entity("OBS-002")["kind"] == "observer"
    assert Q.resolve_entity("OBS-000042")["kind"] == "observation"
    assert Q.resolve_entity("USR-017")["kind"] == "associate"
    assert Q.resolve_entity("PRJ-3")["record"]["cadence_days"] == 7
    assert Q.resolve_entity("NOTE-00700")["found"] is True
    assert Q.resolve_entity("XYZ-1")["found"] is False
