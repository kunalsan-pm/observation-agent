"""
Detector regression tests.

These pin each pattern rule to the signals actually present in the fixture
data. They are the guard against a refactor quietly changing a judgement rule:
if a threshold or window default moves, the expected finding changes with it
and the test fails loudly.
"""

import analytics as a


# --- Q6: sustained low topic scores ---------------------------------------


def test_sustained_low_finds_usr_017_safety_in_org_aaa():
    r = a.sustained_low_topic_scores(org_id="ORG-AAA")
    findings = r["findings"]
    assert len(findings) == 1, "default 2-of-4 rule should isolate one pair"
    f = findings[0]
    assert f["subject"]["user_id"] == "USR-017"
    assert f["topic_id"] == "safety_procedures"
    assert f["observations_below_threshold"] == 3
    assert f["threshold"] == 65
    assert f["window_mean"] < 60
    assert len(f["evidence"]) == 4
    assert all(e["observation_id"].startswith("OBS-0001") for e in f["evidence"])


def test_sustained_low_reports_its_own_rule():
    r = a.sustained_low_topic_scores(org_id="ORG-AAA")
    assert "2 of the last 4" in r["rule"]["sustained_when"]
    assert r["rule"]["threshold"] == "project score_threshold"
    assert "single bad day" in r["rule"]["rationale"]


def test_sustained_low_watchlist_holds_near_misses():
    r = a.sustained_low_topic_scores(org_id="ORG-AAA")
    watched = {w["subject"]["user_id"] for w in r["watchlist"]}
    assert "USR-031" in watched
    assert all(w["observations_below_threshold"] == 1 for w in r["watchlist"])


def test_relaxing_the_rule_widens_the_net_monotonically():
    strict = a.sustained_low_topic_scores(org_id="ORG-AAA", min_below=3)
    default = a.sustained_low_topic_scores(org_id="ORG-AAA", min_below=2)
    loose = a.sustained_low_topic_scores(org_id="ORG-AAA", min_below=1)
    assert len(strict["findings"]) <= len(default["findings"]) <= len(loose["findings"])


# --- Q7: critical-question failures ---------------------------------------


def test_critical_failures_finds_the_lockout_tagout_cluster():
    r = a.critical_failures()
    subjects = {f["subject"]["user_id"] for f in r["findings"]}
    assert {"USR-017", "USR-031"} <= subjects
    assert all(f["question_id"] == "Q-101" for f in r["findings"])
    worst = r["findings"][0]
    assert worst["observation_id"] == "OBS-000363"
    assert worst["score"] < 5
    assert worst["severity"] == "severe"
    assert worst["manager"]["user_id"] == "MGR-002"


def test_critical_severity_is_banded_on_absolute_score():
    """A safety manager reacts to the score itself, not to its distance below
    whichever bar the project happens to set."""
    r = a.critical_failures()
    by_obs = {f["observation_id"]: f for f in r["findings"]}
    assert by_obs["OBS-000195"]["score"] < 50
    assert by_obs["OBS-000195"]["severity"] == "severe"
    assert by_obs["OBS-000196"]["severity"] == "high"  # 58.7


def test_critical_repeat_offenders_surface_the_pattern():
    r = a.critical_failures()
    repeats = {x["subject"]["user_id"]: x for x in r["repeat_offenders"]}
    assert "USR-017" in repeats
    assert repeats["USR-017"]["failures"] == 4
    assert len(repeats["USR-017"]["observation_ids"]) == 4


def test_critical_failures_respect_the_window():
    narrow = a.critical_failures(lookback_days=7)
    wide = a.critical_failures(lookback_days=None)
    assert len(narrow["findings"]) < len(wide["findings"])
    assert all(f["days_ago"] <= 7 for f in narrow["findings"])


# --- Q8: recurring coach-note concerns ------------------------------------


def test_recurring_notes_finds_usr_091_care_protocol():
    r = a.recurring_note_concerns(org_id="ORG-CCC")
    assert len(r["findings"]) == 1
    f = r["findings"][0]
    assert f["subject"]["user_id"] == "USR-091"
    assert f["flagged_note_count"] == 4
    assert set(f["author_ids"]) == {"MGR-009", "MGR-010"}
    assert f["distinct_authors"] == 2
    note_ids = {n["note_id"] for n in f["notes"]}
    assert note_ids == {"NOTE-00697", "NOTE-00698", "NOTE-00699", "NOTE-00700"}
    assert all("care protocol" in n["body"].lower() for n in f["notes"])


def test_concern_gate_rejects_recurring_praise():
    """The two-lexicon rule exists so that recurrence alone cannot flag a note.
    Tested directly, because on this particular corpus no note happens to carry
    recurrence language without also carrying concern language."""
    praise = "Again took feedback well."
    assert a._match_terms(praise, a.RECURRENCE_TERMS) == ["again"]
    assert a._match_terms(praise, a.CONCERN_TERMS) == []

    worry = "Associate is still struggling on the care protocol workflow."
    assert a._match_terms(worry, a.RECURRENCE_TERMS)
    assert a._match_terms(worry, a.CONCERN_TERMS)


def test_concern_gate_is_currently_inert_on_this_corpus():
    """Documents a real property of the fixture data: every note with
    recurrence language also carries concern language, so toggling the gate
    changes nothing here. If a future dataset breaks this, the gate starts
    doing visible work and this test should be updated deliberately."""
    strict = a.recurring_note_concerns(window_days=None, require_concern_language=True)
    loose = a.recurring_note_concerns(window_days=None, require_concern_language=False)
    assert strict["notes_flagged"] == loose["notes_flagged"] == 5


def test_recurring_notes_keeps_single_hits_as_near_misses():
    r = a.recurring_note_concerns()
    singles = {s["subject"]["user_id"] for s in r["single_flagged_note"]}
    assert "USR-058" in singles, "one flagged ORG-BBB note is a near-miss, not a pattern"


def test_note_findings_carry_verifiable_citations():
    r = a.recurring_note_concerns(org_id="ORG-CCC")
    for note in r["findings"][0]["notes"]:
        assert note["note_id"].startswith("NOTE-")
        assert note["observation_id"].startswith("OBS-")
        assert note["body"]
        assert note["recurrence_terms"]


# --- Q9: cadence compliance -----------------------------------------------


def test_cadence_finds_only_usr_042_overdue():
    r = a.cadence_violations()
    assert r["associates_evaluated"] == 100
    assert r["overdue_count"] == 1
    f = r["overdue"][0]
    assert f["subject"]["user_id"] == "USR-042"
    assert f["project_id"] == "PRJ-2"
    assert f["cadence_days"] == 21
    assert 14 < f["days_overdue"] < 15
    assert f["last_observed"] == "2026-04-07"


def test_cadence_uses_each_project_policy_not_a_global_number():
    """PRJ-3 runs a 7-day cadence and PRJ-5 a 30-day one, so a single global
    interval would produce a different (wrong) answer for both."""
    r_all = a.cadence_violations(include_compliant=True)
    cadences = {f["project_id"]: f["cadence_days"] for f in r_all["compliant"]}
    assert cadences["PRJ-3"] == 7
    assert cadences["PRJ-5"] == 30


def test_grace_days_shrinks_the_overdue_list():
    assert a.cadence_violations(grace_days=30)["overdue_count"] == 0


def test_no_associate_is_unobserved_in_this_fixture():
    assert a.cadence_violations()["never_observed"] == []


# --- Q10: decline vs own baseline -----------------------------------------


def test_decline_finds_usr_073_as_the_clear_case():
    r = a.score_declines()
    top = r["findings"][0]
    assert top["subject"]["user_id"] == "USR-073"
    assert top["drop_points"] > 18
    assert top["drop_sds"] > 5
    assert top["passes_absolute_test"] and top["passes_volatility_test"]
    assert len(top["recent_evidence"]) == 3
    assert top["worst_topics_recent"][0]["topic_id"] == "quality_inspection"


def test_decline_requires_both_tests_by_default():
    both = a.score_declines(require_both=True)
    either = a.score_declines(require_both=False)
    assert len(both["findings"]) < len(either["findings"])
    assert all(
        f["passes_absolute_test"] and f["passes_volatility_test"] for f in both["findings"]
    )


def test_decline_baseline_is_self_referential():
    r = a.score_declines(subject_user_id="USR-073")
    f = r["findings"][0]
    assert f["baseline_observations"] == f["total_observations"] - 3
    assert f["recent_mean"] < f["baseline_mean"]
    # Still above the cohort mean of ~83 on the baseline, which is exactly why
    # a cohort comparison would have missed this one.
    assert f["baseline_mean"] > 80


def test_decline_skips_short_histories():
    r = a.score_declines()
    assert r["associates_skipped_insufficient_history"] > 0
    assert r["associates_evaluated"] + r["associates_skipped_insufficient_history"] == 100
