"""Synthetic policy examples: no GIS data or upstream packages required."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone, tzinfo
import unittest

from src.agentic_orchestration import (
    CandidateStatus, CheckStatus, DecisionStatus, HardConstraints, HazardEvidence,
    HazardStatus, MissionRequest, RouteCandidate, Urgency, validate_candidates,
    VehicleEvidence, VerificationStatus,
)


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def mission(**limits):
    return MissionRequest(
        request_id="request-1", mission_id="mission-1", mission_type="relief_delivery",
        vehicle_type="van", urgency=Urgency.URGENT,
        expected_graph_snapshot_id="graph-1", expected_hazard_snapshot_id="hazard-1",
        hard_constraints=HardConstraints(
            **({"max_edge_hazard": 0.6, "max_hazard_age_sec": 300,
                "max_travel_time_sec": 900, "max_distance_m": 10000} | limits)
        ),
    )


def route(route_id="route-1", **changes):
    vehicle_types = changes.pop("compatible_vehicle_types", frozenset({"van"}))
    return replace(RouteCandidate(
        route_id=route_id,
        hazard=HazardEvidence(HazardStatus.KNOWN, 0.3, NOW - timedelta(seconds=60),
                              NOW + timedelta(seconds=60), True,
                              route_id, "graph-1", "hazard-1", "fixture/hazard",
                              VerificationStatus.VERIFIED),
        vehicle_evidence=VehicleEvidence(vehicle_types, route_id, "graph-1",
                                         "fixture/vehicle", VerificationStatus.VERIFIED),
        travel_time_sec=600, distance_m=5000,
    ), **changes)


class FixtureVerifier:
    """Test oracle for exact fixture records; not an artifact verification adapter."""

    def __init__(self, candidates):
        candidates = tuple(candidates)
        self.hazards = tuple(c.hazard for c in candidates if isinstance(c.hazard, HazardEvidence))
        self.vehicles = tuple(c.vehicle_evidence for c in candidates
                              if isinstance(c.vehicle_evidence, VehicleEvidence))

    def verify_hazard(self, evidence):
        return any(record.route_id == evidence.route_id and record == evidence
                   for record in self.hazards)

    def verify_vehicle(self, evidence):
        return any(record.route_id == evidence.route_id and record == evidence
                   for record in self.vehicles)


class BrokenTimezone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("Unusable external timezone")


def evaluate(request, candidates, *, as_of=NOW):
    # These tests explicitly assume their synthetic records were checked by an
    # upstream oracle. Separate regression tests exercise absent/rejecting hooks.
    candidates = tuple(candidates)
    return validate_candidates(request, candidates, as_of=as_of,
                               verifier=FixtureVerifier(c for c in candidates
                                                        if isinstance(c, RouteCandidate)))


def assess(candidate, request=None):
    return evaluate(request or mission(), [candidate])


class ValidationTests(unittest.TestCase):
    def test_valid_candidates_return_versions_checks_and_no_ranking(self):
        response = evaluate(mission(), [route("b"), route("a")], as_of=NOW)
        self.assertEqual(mission().schema_version, "c4.mission-request/1.0.0")
        self.assertEqual(response.schema_version, "c4.mission-decision/1.0.0")
        self.assertEqual(response.status, DecisionStatus.ELIGIBLE_CANDIDATES)
        self.assertEqual(response.eligible_route_ids, ("b", "a"))
        self.assertEqual(response.request_id, "request-1")
        self.assertEqual(len(response.candidate_assessments[0].checks), 6)
        self.assertTrue(all(c.status == CheckStatus.PASS
                            for c in response.candidate_assessments[0].checks))

    def test_critical_urgency_never_relaxes_any_hard_limit(self):
        request = replace(mission(), urgency=Urgency.CRITICAL)
        candidates = [
            route("hazard", hazard=replace(route("hazard").hazard, max_edge_hazard=0.9)),
            route("time", travel_time_sec=901),
            route("distance", distance_m=10001),
            route("vehicle", compatible_vehicle_types=frozenset({"motorbike"})),
        ]
        response = evaluate(request, candidates, as_of=NOW)
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
        actual = evaluate(mission(), [candidate], as_of=NOW.astimezone(local))
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
        self.assertEqual(len(response.candidate_assessments[0].checks), 4)

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
        result = evaluate(mission(), candidates, as_of=NOW)
        self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
        result = evaluate(mission(), candidates + [route("pass")], as_of=NOW)
        self.assertEqual(result.eligible_route_ids, ("pass",))
        self.assertEqual(result.status, DecisionStatus.ELIGIBLE_CANDIDATES)
        empty = evaluate(mission(), [], as_of=NOW)
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
        self.assertEqual(evaluate(mission(), [route()], as_of=NOW.replace(tzinfo=None)).status,
                         DecisionStatus.INSUFFICIENT_DATA)
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


    def test_hazard_identifiers_must_match_route_and_expected_snapshots(self):
        for field in ("route_id", "graph_snapshot_id", "hazard_snapshot_id"):
            for value in (None, "", " ", 12, "different-id"):
                with self.subTest(field=field, value=value):
                    evidence = replace(route().hazard, **{field: value})
                    result = assess(route(hazard=evidence))
                    self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
                    self.assertEqual(result.candidate_assessments[0].coverage.status,
                                     CheckStatus.UNKNOWN)
        for field in ("expected_graph_snapshot_id", "expected_hazard_snapshot_id"):
            for value in (None, "", "different-id"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(assess(route(), replace(mission(), **{field: value})).status,
                                     DecisionStatus.INSUFFICIENT_DATA)

    def test_vehicle_evidence_is_also_bound_to_route_and_graph(self):
        for field in ("route_id", "graph_snapshot_id"):
            for value in (None, "different-id"):
                with self.subTest(field=field, value=value):
                    evidence = replace(route().vehicle_evidence, **{field: value})
                    result = assess(route(vehicle_evidence=evidence))
                    self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)

    def test_source_and_verified_claims_are_not_proof_without_verifier(self):
        result = validate_candidates(mission(), [route()], as_of=NOW)
        self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
        assessment = result.candidate_assessments[0]
        self.assertEqual(assessment.coverage.reason_code, "hazard_coverage_unverified")
        self.assertEqual(assessment.freshness.reason_code, "hazard_freshness_unverified")
        self.assertEqual(assessment.checks[2].reason_code, "trusted_verifier_missing")

    def test_missing_sources_and_unverified_or_rejected_claims_stay_unknown(self):
        for field, value in (
            ("evidence_source", None), ("evidence_source", " "),
            ("verification_status", VerificationStatus.UNVERIFIED),
            ("verification_status", VerificationStatus.REJECTED),
            ("verification_status", "verified"),
        ):
            for kind in ("hazard", "vehicle_evidence"):
                with self.subTest(field=field, value=value, kind=kind):
                    candidate = route()
                    evidence = replace(getattr(candidate, kind), **{field: value})
                    result = assess(replace(candidate, **{kind: evidence}))
                    self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)

    def test_tampered_evidence_is_not_supported_by_exact_fixture_records(self):
        original = route()
        oracle = FixtureVerifier([original])
        for candidate in (
            replace(original, hazard=replace(original.hazard, max_edge_hazard=0.0)),
            replace(original, hazard=replace(original.hazard, evidence_source="invented/source")),
            replace(original, vehicle_evidence=replace(
                original.vehicle_evidence, compatible_vehicle_types=frozenset({"van", "truck"}))),
        ):
            with self.subTest(candidate=candidate):
                result = validate_candidates(mission(), [candidate], as_of=NOW, verifier=oracle)
                self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)

    def test_legacy_compatibility_claim_without_evidence_cannot_pass(self):
        candidate = replace(route(vehicle_evidence=None),
                            compatible_vehicle_types=frozenset({"van"}))
        self.assertEqual(assess(candidate).status, DecisionStatus.INSUFFICIENT_DATA)

    def test_verifier_failure_is_isolated_to_affected_candidate(self):
        class FailingVerifier(FixtureVerifier):
            def verify_hazard(self, evidence):
                if evidence.route_id == "broken":
                    raise RuntimeError("Artifact read failure")
                return super().verify_hazard(evidence)

        candidates = [route("broken"), route("good")]
        result = validate_candidates(mission(), candidates, as_of=NOW,
                                     verifier=FailingVerifier(candidates))
        self.assertEqual(result.eligible_route_ids, ("good",))
        self.assertEqual(result.candidate_assessments[0].checks[2].reason_code,
                         "evidence_verifier_error")

    def test_truthy_verifier_response_is_not_accepted_as_verification(self):
        class TruthyVerifier(FixtureVerifier):
            def verify_hazard(self, evidence):
                return 1

        result = validate_candidates(mission(), [route()], as_of=NOW,
                                     verifier=TruthyVerifier([route()]))
        self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)

    def test_datetime_conversion_failures_do_not_abort_other_candidates(self):
        underflow = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
        overflow = datetime(9999, 12, 31, 23, 59, tzinfo=timezone(timedelta(hours=-1)))
        broken = NOW.replace(tzinfo=BrokenTimezone())
        for field in ("observed_at", "valid_until"):
            for value in (underflow, overflow, broken, "not a timestamp"):
                with self.subTest(field=field, value_type=type(value)):
                    bad = route("bad", hazard=replace(route("bad").hazard, **{field: value}))
                    result = evaluate(mission(), [bad, route("good")])
                    self.assertEqual(result.eligible_route_ids, ("good",))
                    self.assertEqual(result.candidate_assessments[0].freshness.status,
                                     CheckStatus.UNKNOWN)

    def test_invalid_evaluation_clock_yields_structured_unknown(self):
        for value in (None, NOW.replace(tzinfo=None), NOW.replace(tzinfo=BrokenTimezone()),
                      datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))):
            with self.subTest(value_type=type(value)):
                result = evaluate(mission(), [route()], as_of=value)
                self.assertEqual(result.status, DecisionStatus.INSUFFICIENT_DATA)
                self.assertIsNone(result.evaluated_at)
                self.assertEqual(result.evaluation_time_reason, "evaluation_time_invalid")
                self.assertEqual(result.candidate_assessments[0].freshness.reason_code,
                                 "evaluation_time_invalid")

    def test_freshness_and_coverage_are_auditable_for_stale_evidence(self):
        candidate = route(hazard=replace(route().hazard, observed_at=NOW - timedelta(seconds=301)))
        result = assess(candidate)
        freshness = result.candidate_assessments[0].freshness
        self.assertEqual(freshness.observed_at, candidate.hazard.observed_at)
        self.assertEqual(freshness.age_sec, 301)
        self.assertEqual(freshness.valid_until, candidate.hazard.valid_until)
        self.assertEqual(freshness.max_age_sec, 300)
        self.assertEqual(freshness.reason_code, "hazard_stale")
        coverage = result.candidate_assessments[0].coverage
        self.assertIs(coverage.complete_coverage, True)
        self.assertEqual(coverage.status, CheckStatus.PASS)
        self.assertEqual(result.expected_graph_snapshot_id, "graph-1")
        self.assertEqual(result.expected_hazard_snapshot_id, "hazard-1")

    def test_zero_age_and_hazard_extremes_are_inclusive(self):
        for score in (0.0, 1.0):
            with self.subTest(score=score):
                request = mission(max_edge_hazard=score, max_hazard_age_sec=0,
                                  max_travel_time_sec=0, max_distance_m=0)
                candidate = route(hazard=replace(route().hazard, max_edge_hazard=score,
                                                observed_at=NOW, valid_until=None),
                                  travel_time_sec=0, distance_m=0)
                self.assertEqual(assess(candidate, request).status,
                                 DecisionStatus.ELIGIBLE_CANDIDATES)
                candidate = replace(candidate, hazard=replace(
                    candidate.hazard, observed_at=NOW - timedelta(microseconds=1)))
                self.assertEqual(assess(candidate, request).status, DecisionStatus.INSUFFICIENT_DATA)

    def test_optional_expiry_and_subsecond_boundaries(self):
        for expiry in (None, NOW + timedelta(microseconds=1)):
            with self.subTest(expiry=expiry):
                candidate = route(hazard=replace(route().hazard, valid_until=expiry))
                self.assertEqual(assess(candidate).status, DecisionStatus.ELIGIBLE_CANDIDATES)
        for field, value in (("travel_time_sec", 900.000001), ("distance_m", 10000.000001)):
            with self.subTest(field=field):
                self.assertEqual(assess(route(**{field: value})).status,
                                 DecisionStatus.NO_FEASIBLE_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
