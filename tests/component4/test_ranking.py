"""Synthetic ranking regressions using the existing validation test oracle."""

from dataclasses import replace
from math import isfinite
import unittest

from src.agentic_orchestration import (
    CandidateStatus, DecisionStatus, RankingPolicy, RankingStatus, Urgency,
    RankingMetricEvidence, VerificationStatus,
    rank_candidates, validate_candidates,
)
from test_validation import NOW, FixtureVerifier, mission, route


def policy(time_weight=0.75, exposure_weight=0.25, **changes):
    return RankingPolicy(**({
        "policy_id": "synthetic-weighted", "policy_version": "1.0.0",
        "mission_type": "relief_delivery", "time_weight": time_weight,
        "exposure_weight": exposure_weight,
    } | changes))


def candidate(route_id, time=100, exposure=50, **changes):
    return route(route_id, travel_time_sec=time,
                 cumulative_hazard_exposure=exposure,
                 ranking_metric_evidence=RankingMetricEvidence(
                     route_id=route_id, graph_snapshot_id="graph-1", hazard_snapshot_id="hazard-1",
                     route_result_id=f"result/{route_id}", travel_time_sec=time,
                     cumulative_hazard_exposure=exposure, travel_time_unit="s",
                     exposure_unit="hazard*m", travel_time_model_version="synthetic-time/1",
                     exposure_formula_version="synthetic-exposure/1", evidence_source="fixture/results",
                     verification_status=VerificationStatus.VERIFIED,
                 ), **changes)


class FixtureMetricVerifier:
    """Independently held synthetic records, not a real upstream data source."""

    def __init__(self, candidates):
        self.records = {c.ranking_metric_evidence.route_result_id: c.ranking_metric_evidence
                        for c in candidates
                        if isinstance(c.ranking_metric_evidence, RankingMetricEvidence)}

    def get_authoritative_metrics(self, route_result_id):
        return self.records.get(route_result_id)


DEFAULT_METRIC_VERIFIER = object()


def rank(candidates, ranking_policy=None, request=None, *, metric_verifier=DEFAULT_METRIC_VERIFIER):
    candidates = tuple(candidates)
    if metric_verifier is DEFAULT_METRIC_VERIFIER:
        # Positive scoring tests explicitly assume synthetic authoritative records.
        # Negative tests hold originals separately before tampering with claims.
        metric_verifier = FixtureMetricVerifier(candidates)
    return rank_candidates(request or mission(), candidates, ranking_policy or policy(),
                           as_of=NOW, verifier=FixtureVerifier(candidates),
                           metric_verifier=metric_verifier)


class RankingTests(unittest.TestCase):
    def test_weighted_order_and_explanation_terms(self):
        result = rank([candidate("safe", 300, 0), candidate("fast", 100, 100),
                       candidate("middle", 200, 40)])
        self.assertEqual(result.status, RankingStatus.SELECTED)
        self.assertEqual(result.selected_route_id, "fast")
        self.assertEqual([r.route_id for r in result.ranked_routes], ["fast", "middle", "safe"])
        self.assertEqual([r.rank for r in result.ranked_routes], [1, 2, 3])
        self.assertEqual(result.comparison_route_ids, ("fast", "middle", "safe"))
        self.assertEqual(result.schema_version, "c4.mission-ranking/1.0.0")
        self.assertEqual(result.policy.policy_version, "1.0.0")
        self.assertEqual(result.validation.request_id, "request-1")
        self.assertEqual(result.normalization.time_min_sec, 100)
        self.assertEqual(result.normalization.time_max_sec, 300)
        self.assertEqual(result.normalization.exposure_min_haz_m, 0)
        self.assertEqual(result.normalization.exposure_max_haz_m, 100)
        middle = result.ranked_routes[1]
        self.assertEqual(middle.travel_time_sec, 200)
        self.assertEqual(middle.cumulative_hazard_exposure, 40)
        self.assertEqual(middle.normalized_time, 0.5)
        self.assertEqual(middle.normalized_exposure, 0.4)
        self.assertAlmostEqual(middle.time_contribution, 0.375)
        self.assertAlmostEqual(middle.exposure_contribution, 0.1)
        self.assertEqual(middle.score, middle.time_contribution + middle.exposure_contribution)
        self.assertEqual(middle.metric_evidence.route_result_id, "result/middle")

    def test_equal_scores_break_by_route_id_independent_of_input_order(self):
        candidates = [candidate("b", 100, 100), candidate("a", 300, 0)]
        balanced = policy(0.5, 0.5)
        first, second = rank(candidates, balanced), rank(reversed(candidates), balanced)
        self.assertEqual(first.selected_route_id, "a")
        self.assertEqual(first.ranked_routes, second.ranked_routes)
        self.assertEqual([r.score for r in first.ranked_routes], [0.5, 0.5])

    def test_explicit_policy_changes_selection_without_changing_eligibility(self):
        candidates = [candidate("fast", 100, 100), candidate("safe", 300, 0)]
        time_result = rank(candidates)
        exposure_result = rank(candidates, policy(0.2, 0.8, policy_version="2.0.0"))
        self.assertEqual(time_result.selected_route_id, "fast")
        self.assertEqual(exposure_result.selected_route_id, "safe")
        self.assertEqual(time_result.validation, exposure_result.validation)
        self.assertEqual(exposure_result.policy.policy_version, "2.0.0")

    def test_rejected_and_unknown_routes_never_influence_normalization(self):
        rejected = candidate("rejected", 0, 0)
        rejected = replace(rejected, hazard=replace(rejected.hazard, max_edge_hazard=0.99))
        unknown = candidate("unknown", 0, None, hazard=None)
        result = rank([rejected, unknown, candidate("eligible", 100, 50)])
        self.assertEqual(result.comparison_route_ids, ("eligible",))
        self.assertEqual(result.selected_route_id, "eligible")
        self.assertEqual(result.normalization.time_min_sec, 100)
        self.assertEqual(result.metric_issues, ())
        self.assertEqual(result.validation.candidate_assessments[0].status,
                         CandidateStatus.INELIGIBLE)

    def test_ranking_revalidates_exact_candidates_instead_of_reusing_old_eligibility(self):
        original = candidate("changed", 0, 0)
        validated = validate_candidates(mission(), [original], as_of=NOW,
                                        verifier=FixtureVerifier([original]))
        self.assertEqual(validated.eligible_route_ids, ("changed",))
        changed = replace(original, hazard=replace(original.hazard, max_edge_hazard=0.99))
        self.assertEqual(rank([changed]).status, RankingStatus.NO_ELIGIBLE_CANDIDATES)

    def test_no_eligible_candidates_preserves_underlying_validation_reason(self):
        empty = rank([])
        unknown = rank([candidate("unknown", hazard=None)])
        rejected = rank([candidate("late", 901, 0)])
        for result in (empty, unknown, rejected):
            self.assertEqual(result.status, RankingStatus.NO_ELIGIBLE_CANDIDATES)
            self.assertIsNone(result.selected_route_id)
            self.assertIsNone(result.normalization)
            self.assertEqual(result.ranked_routes, ())
        self.assertEqual(empty.validation.status, DecisionStatus.NO_CANDIDATES)
        self.assertEqual(unknown.validation.status, DecisionStatus.INSUFFICIENT_DATA)
        self.assertEqual(rejected.validation.status, DecisionStatus.NO_FEASIBLE_CANDIDATE)

    def test_verifier_is_still_required(self):
        result = rank_candidates(mission(), [candidate("unverified")], policy(), as_of=NOW)
        self.assertEqual(result.status, RankingStatus.NO_ELIGIBLE_CANDIDATES)

    def test_one_missing_metric_blocks_partial_selection_from_eligible_set(self):
        result = rank([candidate("complete"), candidate("missing", exposure=None)])
        self.assertEqual(result.status, RankingStatus.INSUFFICIENT_RANKING_DATA)
        self.assertEqual(result.comparison_route_ids, ("complete", "missing"))
        self.assertEqual(result.validation.eligible_route_ids, ("complete", "missing"))
        self.assertEqual(result.ranked_routes, ())
        self.assertIsNone(result.selected_route_id)
        self.assertIsNone(result.normalization)
        self.assertEqual(result.metric_issues[0].route_id, "missing")
        self.assertEqual(result.metric_issues[0].metric, "cumulative_hazard_exposure")
        self.assertEqual(result.metric_issues[0].reason_code, "metric_missing")

    def test_invalid_ranking_metrics_are_never_coerced_or_replaced(self):
        # Disable the optional hard time limit to isolate ranking-data checks.
        request = mission(max_travel_time_sec=None)
        for metric in ("travel_time_sec", "cumulative_hazard_exposure"):
            for value in (None, -1, -9999, float("nan"), float("inf"), -float("inf"),
                          True, False, "0", 10**1000):
                with self.subTest(metric=metric, value=value):
                    broken = replace(candidate("broken"), **{metric: value})
                    result = rank([broken], request=request)
                    self.assertEqual(result.validation.eligible_route_ids, ("broken",))
                    self.assertEqual(result.status, RankingStatus.INSUFFICIENT_RANKING_DATA)
                    self.assertIsNone(result.selected_route_id)

    def test_both_metrics_required_even_at_zero_weight(self):
        for ranking_policy, broken in (
            (policy(1, 0), candidate("missing-exposure", exposure=None)),
            (policy(0, 1), candidate("missing-time", time=None)),
        ):
            result = rank([broken], ranking_policy, mission(max_travel_time_sec=None))
            self.assertEqual(result.status, RankingStatus.INSUFFICIENT_RANKING_DATA)

    def test_constant_objectives_and_singleton_produce_zero_normalized_terms(self):
        singleton = rank([candidate("single", 0, 0)])
        self.assertEqual(singleton.ranked_routes[0].score, 0)
        self.assertEqual(singleton.selected_route_id, "single")
        identical = rank([candidate("b"), candidate("a")])
        self.assertEqual(identical.selected_route_id, "a")
        self.assertTrue(all(r.normalized_time == 0 and r.normalized_exposure == 0
                            for r in identical.ranked_routes))
        constant_time = rank([candidate("higher", 100, 100), candidate("lower", 100, 0)])
        self.assertEqual(constant_time.selected_route_id, "lower")
        self.assertTrue(all(r.time_contribution == 0 for r in constant_time.ranked_routes))

    def test_invalid_policy_weights_and_identity_are_rejected(self):
        for weights in ((0, 0), (0.4, 0.4), (-0.1, 1.1), (float("nan"), 1),
                        (float("inf"), 0), (True, 0), ("0.5", 0.5), (None, 1),
                        (10**1000, 0)):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                policy(*weights)
        for changes in ({"policy_id": " "}, {"policy_version": ""},
                        {"policy_version": "latest"}, {"policy_version": "01.0.0"},
                        {"policy_version": None}, {"mission_type": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                policy(**changes)

    def test_policy_applicability_is_explicit_and_not_inferred_from_urgency(self):
        candidates = [candidate("fast", 100, 100), candidate("safe", 300, 0)]
        baseline = rank(candidates)
        critical = rank(candidates, request=replace(mission(), urgency=Urgency.CRITICAL))
        self.assertEqual(baseline.ranked_routes, critical.ranked_routes)
        with self.assertRaises(ValueError):
            rank(candidates, policy(mission_type="medical_transport"))

    def test_extreme_finite_metrics_and_weight_sum_drift_stay_finite(self):
        candidates = [candidate("a", 1e308, 1e308), candidate("b", 1.7e308, 0)]
        result = rank(candidates, policy(0.5, 0.5000000000001),
                      mission(max_travel_time_sec=None))
        self.assertAlmostEqual(result.effective_time_weight + result.effective_exposure_weight, 1)
        self.assertTrue(all(isfinite(r.score) and 0 <= r.score <= 1 for r in result.ranked_routes))

    def test_duplicate_ids_are_rejected_and_inputs_are_not_mutated(self):
        candidates = [candidate("b"), candidate("a")]
        before = tuple(replace(c) for c in candidates)
        first = rank(candidates)
        self.assertEqual(tuple(candidates), before)
        self.assertEqual(first, rank(candidates))
        with self.assertRaises(ValueError):
            rank([candidate("same"), candidate("same")])


class MetricEvidenceTests(unittest.TestCase):
    def assert_blocked(self, result, reason):
        self.assertEqual(result.status, RankingStatus.INSUFFICIENT_RANKING_DATA)
        self.assertIsNone(result.selected_route_id)
        self.assertIsNone(result.normalization)
        self.assertEqual(result.ranked_routes, ())
        self.assertIn(reason, [issue.reason_code for issue in result.metric_issues])

    def test_bound_metrics_match_an_independent_record_and_preserve_scoring(self):
        records = [candidate("fast", 100, 100), candidate("safe", 300, 0)]
        result = rank(records, metric_verifier=FixtureMetricVerifier(records))
        self.assertEqual(result.selected_route_id, "fast")
        self.assertEqual([r.score for r in result.ranked_routes], [0.25, 0.75])
        self.assertEqual(result.normalization.time_min_sec, 100)
        self.assertEqual(result.normalization.exposure_max_haz_m, 100)
        self.assertEqual(result.ranked_routes[0].metric_evidence, records[0].ranking_metric_evidence)

    def test_missing_or_malformed_metric_evidence_blocks_the_whole_set(self):
        for evidence in (None, {}, "sha256:claimed-digest"):
            with self.subTest(evidence=evidence):
                missing = replace(candidate("missing"), ranking_metric_evidence=evidence)
                self.assert_blocked(rank([candidate("valid"), missing]),
                                    "metric_evidence_missing_or_invalid")

    def test_route_and_snapshot_identifiers_must_be_present_and_match(self):
        original = candidate("route")
        for field in ("route_id", "graph_snapshot_id", "hazard_snapshot_id"):
            for value in (None, "", "different"):
                with self.subTest(field=field, value=value):
                    claim = replace(original.ranking_metric_evidence, **{field: value})
                    changed = replace(original, ranking_metric_evidence=claim)
                    self.assert_blocked(rank([changed]), "metric_evidence_binding_mismatch"
                                        if value == "different" else "metric_evidence_identifiers_missing")

    def test_values_in_evidence_must_match_values_being_ranked(self):
        original = candidate("route")
        for field in ("travel_time_sec", "cumulative_hazard_exposure"):
            with self.subTest(field=field):
                claim = replace(original.ranking_metric_evidence, **{field: 0})
                self.assert_blocked(rank([replace(original, ranking_metric_evidence=claim)]),
                                    "metric_evidence_values_mismatch")

    def test_tampering_with_both_candidate_and_evidence_does_not_forge_authority(self):
        original = candidate("route")
        authority = FixtureMetricVerifier([original])
        for field in ("travel_time_sec", "cumulative_hazard_exposure"):
            with self.subTest(field=field):
                changed = replace(original, **{field: 0}, ranking_metric_evidence=replace(
                    original.ranking_metric_evidence, **{field: 0}))
                self.assert_blocked(rank([changed], metric_verifier=authority), "metric_authority_mismatch")

    def test_units_and_model_versions_are_checked_against_authority(self):
        original = candidate("route")
        authority = FixtureMetricVerifier([original])
        for field, value, reason in (
            ("travel_time_unit", "min", "metric_evidence_units_mismatch"),
            ("exposure_unit", "probability", "metric_evidence_units_mismatch"),
            ("travel_time_model_version", "different", "metric_authority_mismatch"),
            ("exposure_formula_version", "different", "metric_authority_mismatch"),
            ("evidence_source", "another-store", "metric_authority_mismatch"),
        ):
            with self.subTest(field=field):
                changed = replace(original, ranking_metric_evidence=replace(
                    original.ranking_metric_evidence, **{field: value}))
                self.assert_blocked(rank([changed], metric_verifier=authority), reason)

    def test_missing_metadata_or_unverified_claims_are_not_supported(self):
        original = candidate("route")
        for field, value, reason in (
            ("route_result_id", None, "metric_evidence_metadata_missing"),
            ("evidence_source", " ", "metric_evidence_metadata_missing"),
            ("travel_time_model_version", None, "metric_evidence_metadata_missing"),
            ("exposure_formula_version", "", "metric_evidence_metadata_missing"),
            ("verification_status", VerificationStatus.UNVERIFIED, "metric_evidence_not_verified"),
            ("verification_status", VerificationStatus.REJECTED, "metric_evidence_not_verified"),
            ("verification_status", "verified", "metric_evidence_not_verified"),
        ):
            with self.subTest(field=field, value=value):
                changed = replace(original, ranking_metric_evidence=replace(
                    original.ranking_metric_evidence, **{field: value}))
                self.assert_blocked(rank([changed]), reason)

    def test_flags_and_source_labels_cannot_replace_a_metric_verifier(self):
        self.assert_blocked(rank([candidate("claims-only")], metric_verifier=None),
                            "metric_verifier_missing")

    def test_unsupported_results_and_boolean_approvals_are_not_authoritative_records(self):
        class UnsupportedVerifier:
            def __init__(self, result):
                self.result = result

            def get_authoritative_metrics(self, route_result_id):
                return self.result

        for value in (None, True, 1, "sha256:claimed-digest", {}):
            with self.subTest(value=value):
                self.assert_blocked(rank([candidate("route")], metric_verifier=UnsupportedVerifier(value)),
                                    "metric_evidence_unsupported")

    def test_verifier_exception_prevents_partial_selection(self):
        class BrokenVerifier(FixtureMetricVerifier):
            def get_authoritative_metrics(self, route_result_id):
                if route_result_id == "result/broken":
                    raise OSError("Authoritative artifact unavailable")
                return super().get_authoritative_metrics(route_result_id)

        records = [candidate("valid"), candidate("broken")]
        self.assert_blocked(rank(records, metric_verifier=BrokenVerifier(records)), "metric_verifier_error")

    def test_malformed_or_mismatched_authoritative_records_do_not_pass(self):
        original = candidate("route")
        for changes in ({"travel_time_sec": True}, {"cumulative_hazard_exposure": float("nan")},
                        {"travel_time_unit": "min"}, {"route_id": "wrong"},
                        {"graph_snapshot_id": "wrong"}, {"hazard_snapshot_id": "wrong"},
                        {"route_result_id": "wrong"}, {"travel_time_model_version": "wrong"},
                        {"exposure_formula_version": "wrong"}):
            with self.subTest(changes=changes):
                authority = FixtureMetricVerifier([original])
                authority.records["result/route"] = replace(original.ranking_metric_evidence, **changes)
                self.assert_blocked(rank([original], metric_verifier=authority), "metric_authority_mismatch")

    def test_rejected_candidates_do_not_require_metric_evidence(self):
        rejected = replace(candidate("late", 901, 0), ranking_metric_evidence=None)
        result = rank([rejected, candidate("eligible")])
        self.assertEqual(result.selected_route_id, "eligible")
        self.assertEqual(result.comparison_route_ids, ("eligible",))


if __name__ == "__main__":
    unittest.main()
