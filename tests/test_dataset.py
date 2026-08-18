"""Dataset-layer invariants, including the fixture quirks we work around."""

import dataset as ds


def test_row_counts_match_the_brief():
    assert len(ds.ORGS) == 3
    assert len(ds.PROJECTS) == 5
    assert len(ds.USERS) == 130
    assert len(ds.OBS_DF) == 1204
    assert len(ds.RESP_DF) == 9095
    assert len(ds.COACH_NOTES) == 786


def test_today_anchor_is_the_exercise_date():
    assert ds.today_date() == "2026-05-12"
    # End-of-day, so an observation completed today is 0 days old, not -0.4.
    assert ds.today().hour == 23


def test_project_template_mapping_is_derived():
    """projects.json has no template_id column even though the brief says it
    does; we derive it and it must agree with what observations carry."""
    assert "template_id" not in ds.PROJECTS[0]
    assert ds.TEMPLATE_ID_BY_PROJECT["PRJ-1"] == "TPL-PRJ-1"
    for row in ds.OBS_DF.itertuples():
        assert ds.TEMPLATE_ID_BY_PROJECT[row.project_id] == row.template_id


def test_observer_and_observation_ids_disambiguate():
    """OBS- is overloaded: 3 digits is a person, 6 digits is an observation."""
    assert ds.classify_id("OBS-002") == "observer"
    assert ds.classify_id("OBS-000042") == "observation"
    assert ds.classify_id("USR-017") == "associate"
    assert ds.classify_id("MGR-001") == "manager"
    assert ds.classify_id("NOTE-00700") == "note"


def test_thresholds_and_cadence_come_from_the_project():
    assert ds.threshold_for("PRJ-1") == 65
    assert ds.threshold_for("PRJ-5") == 75
    assert ds.cadence_for("PRJ-3") == 7
    assert ds.cadence_for("PRJ-5") == 30
    assert ds.threshold_for("PRJ-1", override=80) == 80


def test_is_critical_lives_only_on_responses():
    assert "is_critical" not in ds.OBS_DF.columns
    assert "is_critical" in ds.RESP_DF.columns
    assert int(ds.RESP_DF["is_critical"].sum()) == 1653


def test_page_envelope_reports_truncation():
    p = ds.page(list(range(10)), limit=3, offset=0)
    assert (p["total_matching"], p["returned"], p["truncated"]) == (10, 3, True)
    p = ds.page(list(range(10)), limit=20)
    assert p["truncated"] is False
