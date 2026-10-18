"""Synthetic policy examples: no GIS data or upstream packages required."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import unittest

from src.agentic_orchestration import (
    CandidateStatus, CheckStatus, DecisionStatus, HardConstraints, HazardEvidence,
    HazardStatus, MissionRequest, RouteCandidate, Urgency, validate_candidates,
)


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def mission(**limits):
    return MissionRequest(
        request_id="request-1", mission_id="mission-1", mission_type="relief_delivery",
        vehicle_type="van", urgency=Urgency.URGENT,
        hard_constraints=HardConstraints(
            **({"max_edge_hazard": 0.6, "max_hazard_age_sec": 300,
                "max_travel_time_sec": 900, "max_distance_m": 10000} | limits)
        ),
    )


def route(route_id="route-1", **changes):
    return replace(RouteCandidate(
        route_id=route_id,
        hazard=HazardEvidence(HazardStatus.KNOWN, 0.3, NOW - timedelta(seconds=60),
                              NOW + timedelta(seconds=60), True),
        compatible_vehicle_types=frozenset({"van"}),
        travel_time_sec=600, distance_m=5000,
    ), **changes)


def assess(candidate, request=None):
    return validate_candidates(request or mission(), [candidate], as_of=NOW)


class ValidationTests(unittest.TestCase):
    def test_valid_candidates_return_versions_checks_and_no_ranking(self):
        response = validate_candidates(mission(), [route("b"), route("a")], as_of=NOW)
        self.assertEqual(mission().schema_version, "c4.mission-request/1.0.0")
        self.assertEqual(response.schema_version, "c4.mission-decision/1.0.0")
        self.assertEqual(response.status, DecisionStatus.ELIGIBLE_CANDIDATES)
        self.assertEqual(response.eligible_route_ids, ("b", "a"))
        self.assertEqual(response.request_id, "request-1")
        self.assertEqual(len(response.candidate_assessments[0].checks), 4)
        self.assertTrue(all(c.status == CheckStatus.PASS
                            for c in response.candidate_assessments[0].checks))

    def test_critical_urgency_never_relaxes_any_hard_limit(self):
        request = replace(mission(), urgency=Urgency.CRITICAL)
        candidates = [
            route("hazard", hazard=replace(route().hazard, max_edge_hazard=0.9)),
            route("time", travel_time_sec=901),
            route("distance", distance_m=10001),
            route("vehicle", compatible_vehicle_types=frozenset({"motorbike"})),
        ]
        response = validate_candidates(request, candidates, as_of=NOW)
        self.assertEqual(response.status, DecisionStatus.NO_FEASIBLE_CANDIDATE)
        self.assertEqual(response.eligible_route_ids, ())
        self.assertTrue(all(a.status == CandidateStatus.INELIGIBLE
                            for a in response.candidate_assessments))

    def test_missing_and_declared_unknown_hazard_cannot_pass(self):
        for evidence in [None, HazardEvidence(), *(
            replace(route().hazard, status=status)
            for status in (HazardStatus.MISSING, HazardStatus.UNKNOWN,
                           HazardStatus.INVALID, HazardStatus.STALE)
        )]:
            with self.subTest(evidence=evidence):
                response = assess(route(hazard=evidence))
                self.assertEqual(response.status, DecisionStatus.INSUFFICIENT_DATA)
                check = response.candidate_assessments[0].checks[0]
                self.assertEqual(check.status, CheckStatus.UNKNOWN)
                self.assertIsNone(check.observed_value)

    def test_invalid_scores_are_unknown_not_clipped_or_coerced(self):
        for score in (None, -9999, -0.01, 1.01, float("nan"), float("inf"),
                      -float("inf"), True, False, "0.2", 10**1000):
            with self.subTest(score=score):
                result = assess(route(hazard=replace(route().hazard, max_edge_hazard=score)))
                self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
                self.assertEqual(result.candidate_assessments[0].checks[0].reason_code,
                                 "hazard_value_invalid")

    def test_observed_zero_is_valid_but_missing_coverage_is_not(self):
        evidence = replace(route().hazard, max_edge_hazard=0.0)
        self.assertEqual(assess(route(hazard=evidence)).status,
                         DecisionStatus.ELIGIBLE_CANDIDATES)
        for coverage in (False, None, 1, "true"):
            with self.subTest(coverage=coverage):
                result = assess(route(hazard=replace(evidence, complete_coverage=coverage)))
                self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)

    def test_freshness_boundaries_and_expiry(self):
        fresh = replace(route().hazard, observed_at=NOW - timedelta(seconds=300))
        self.assertEqual(assess(route(hazard=fresh)).status, DecisionStatus.ELIGIBLE_CANDIDATES)
        for evidence in (
            replace(fresh, observed_at=NOW - timedelta(seconds=300, microseconds=1)),
            replace(fresh, valid_until=NOW),
            replace(fresh, observed_at=NOW + timedelta(seconds=1)),
            replace(fresh, observed_at=None),
            replace(fresh, observed_at=NOW.replace(tzinfo=None)),
            replace(fresh, valid_until=NOW.replace(tzinfo=None)),
            replace(fresh, valid_until=fresh.observed_at),
        ):
            with self.subTest(evidence=evidence):
                self.assertEqual(assess(route(hazard=evidence)).status,
                                 DecisionStatus.INSUFFICIENT_DATA)

    def test_timezone_offsets_describe_the_same_instant(self):
        local = timezone(timedelta(hours=5, minutes=30))
        candidate = route(hazard=replace(
            route().hazard, observed_at=route().hazard.observed_at.astimezone(local),
            valid_until=route().hazard.valid_until.astimezone(local)))
        expected = assess(route())
        actual = validate_candidates(mission(), [candidate], as_of=NOW.astimezone(local))
        self.assertEqual(actual, expected)

    def test_equality_passes_but_unrounded_excess_fails(self):
        candidate = route(hazard=replace(route().hazard, max_edge_hazard=0.6),
                          travel_time_sec=900, distance_m=10000)
        self.assertEqual(assess(candidate).status, DecisionStatus.ELIGIBLE_CANDIDATES)
        candidate = replace(candidate, hazard=replace(candidate.hazard, max_edge_hazard=0.600001))
        self.assertEqual(assess(candidate).status, DecisionStatus.NO_FEASIBLE_CANDIDATE)

    def test_missing_or_invalid_required_metrics_are_unknown(self):
        for field in ("travel_time_sec", "distance_m"):
            for value in (None, -1, float("nan"), float("inf"), True, "100"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(assess(route(**{field: value})).status,
                                     DecisionStatus.INSUFFICIENT_DATA)

    def test_optional_limits_do_not_invent_metric_requirements(self):
        response = assess(route(travel_time_sec=None, distance_m=None),
                          mission(max_travel_time_sec=None, max_distance_m=None))
        self.assertEqual(response.status, DecisionStatus.ELIGIBLE_CANDIDATES)
        self.assertEqual(len(response.candidate_assessments[0].checks), 2)

    def test_vehicle_compatibility_is_explicit_and_case_sensitive(self):
        self.assertEqual(assess(route(compatible_vehicle_types=None)).status,
                         DecisionStatus.INSUFFICIENT_DATA)
        for types in (frozenset(), frozenset({"Van"})):
            self.assertEqual(assess(route(compatible_vehicle_types=types)).status,
                             DecisionStatus.NO_FEASIBLE_CANDIDATE)

    def test_malformed_evidence_is_reported_unknown(self):
        for candidate in (
            route(hazard={"max_edge_hazard": 0.0}),
            route(hazard=replace(route().hazard, status="known")),
            route(compatible_vehicle_types=frozenset({1})),
            route(compatible_vehicle_types=["van"]),
        ):
            with self.subTest(candidate=candidate):
                self.assertEqual(assess(candidate).status, DecisionStatus.INSUFFICIENT_DATA)

    def test_known_violation_is_ineligible_even_with_unknown_hazard(self):
        response = assess(route(hazard=None, travel_time_sec=1000))
        assessment = response.candidate_assessments[0]
        self.assertEqual(response.status, DecisionStatus.NO_FEASIBLE_CANDIDATE)
        self.assertEqual(assessment.status, CandidateStatus.INELIGIBLE)
        self.assertEqual(assessment.checks[0].status, CheckStatus.UNKNOWN)

    def test_mixed_candidate_outcomes_remain_distinct(self):
        candidates = [route("fail", travel_time_sec=1000), route("unknown", hazard=None)]
        result = validate_candidates(mission(), candidates, as_of=NOW)
        self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
        result = validate_candidates(mission(), candidates + [route("pass")], as_of=NOW)
        self.assertEqual(result.eligible_route_ids, ("pass",))
        self.assertEqual(result.status, DecisionStatus.ELIGIBLE_CANDIDATES)
        empty = validate_candidates(mission(), [], as_of=NOW)
        self.assertEqual(empty.status, DecisionStatus.NO_CANDIDATES)

    def test_invalid_policy_and_request_metadata_are_rejected(self):
        for changes in (
            {"max_edge_hazard": -1}, {"max_edge_hazard": 1.1},
            {"max_edge_hazard": float("nan")}, {"max_edge_hazard": True},
            {"max_hazard_age_sec": None}, {"max_hazard_age_sec": -1},
            {"max_travel_time_sec": float("inf")}, {"max_distance_m": "100"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                mission(**changes)
        for changes in ({"schema_version": "c4.mission-request/2.0.0"},
                        {"request_id": " "}, {"vehicle_type": ""},
                        {"urgency": "critical"}, {"hard_constraints": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(mission(), **changes)

    def test_ambiguous_ids_and_invalid_evaluation_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_candidates(mission(), [route(), route()], as_of=NOW)
        with self.assertRaises(ValueError):
            validate_candidates(mission(), [route()], as_of=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            validate_candidates(mission(), [None], as_of=NOW)
        with self.assertRaises(ValueError):
            validate_candidates(None, [], as_of=NOW)
        with self.assertRaises(ValueError):
            route(" ")

    def test_deterministic_and_nonmutating(self):
        request, candidate = mission(), route()
        before = (replace(request), replace(candidate), replace(candidate.hazard))
        first = assess(candidate, request)
        self.assertEqual(first, assess(candidate, request))
        self.assertEqual(before, (request, candidate, candidate.hazard))
        with self.assertRaises(FrozenInstanceError):
            request.vehicle_type = "motorbike"


if __name__ == "__main__":
    unittest.main()
