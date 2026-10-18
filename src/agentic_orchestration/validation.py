"""Fail-closed evaluation of supplied synthetic route evidence."""

from collections.abc import Iterable
from datetime import datetime

from .contracts import (
    CandidateAssessment, CandidateStatus, CheckStatus, ConstraintCheck,
    CoverageResult, DecisionResponse, DecisionStatus, EvidenceVerifier,
    FreshnessResult, HazardEvidence, HazardStatus, MissionRequest,
    RouteCandidate, VehicleEvidence, VerificationStatus, finite_number, utc_datetime,
)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_check(
    request: MissionRequest, candidate: RouteCandidate,
    evidence: HazardEvidence | VehicleEvidence | None,
    verifier: EvidenceVerifier | None, *, hazard: bool,
) -> ConstraintCheck:
    kind = "hazard" if hazard else "vehicle"
    reason = None
    expected_type = HazardEvidence if hazard else VehicleEvidence
    if not isinstance(evidence, expected_type):
        reason = "evidence_missing_or_invalid"
    else:
        bindings = [(evidence.route_id, candidate.route_id),
                    (evidence.graph_snapshot_id, request.expected_graph_snapshot_id)]
        if hazard:
            bindings.append((evidence.hazard_snapshot_id, request.expected_hazard_snapshot_id))
        if any(not _identifier(actual) or not _identifier(expected)
               for actual, expected in bindings):
            reason = "evidence_identifier_missing_or_invalid"
        elif any(actual != expected for actual, expected in bindings):
            reason = "evidence_binding_mismatch"
        elif not _identifier(evidence.evidence_source):
            reason = "evidence_source_missing"
        elif evidence.verification_status is not VerificationStatus.VERIFIED:
            reason = "evidence_not_verified"
        elif verifier is None:
            reason = "trusted_verifier_missing"
        else:
            try:
                verified = (verifier.verify_hazard(evidence) if hazard
                            else verifier.verify_vehicle(evidence))
                if verified is not True:
                    reason = "evidence_unsupported"
            except Exception:
                # A failing adapter cannot grant eligibility or stop other routes.
                reason = "evidence_verifier_error"
    return ConstraintCheck(
        f"{kind}_evidence", CheckStatus.UNKNOWN if reason else CheckStatus.PASS,
        reason or "evidence_supported", None, "supported_route_snapshot_evidence", "evidence",
    )


def evaluate_freshness(
    evidence: HazardEvidence | None, as_of: datetime | None, max_age: float,
    supported: bool,
) -> FreshnessResult:
    observed = utc_datetime(evidence.observed_at) if isinstance(evidence, HazardEvidence) else None
    raw_expiry = evidence.valid_until if isinstance(evidence, HazardEvidence) else None
    expiry = utc_datetime(raw_expiry)
    age = (as_of - observed).total_seconds() if as_of is not None and observed is not None else None
    if as_of is None:
        reason = "evaluation_time_invalid"
    elif observed is None:
        reason = "hazard_timestamp_missing_or_invalid"
    elif raw_expiry is not None and expiry is None:
        reason = "hazard_expiry_invalid"
    elif expiry is not None and expiry <= observed:
        reason = "hazard_validity_interval_invalid"
    elif age < 0:
        reason = "hazard_timestamp_in_future"
    elif age > max_age or (expiry is not None and as_of >= expiry):
        reason = "hazard_stale"
    elif not supported:
        reason = "hazard_freshness_unverified"
    else:
        reason = "hazard_fresh"
    return FreshnessResult(
        CheckStatus.PASS if reason == "hazard_fresh" else CheckStatus.UNKNOWN,
        reason, observed, age, expiry, max_age,
    )


def _coverage(evidence: HazardEvidence | None, supported: bool) -> CoverageResult:
    value = evidence.complete_coverage if isinstance(evidence, HazardEvidence) else None
    complete = value if type(value) is bool else None
    if complete is not True:
        reason = "hazard_coverage_unknown"
    elif not supported:
        reason = "hazard_coverage_unverified"
    else:
        reason = "hazard_coverage_supported"
    return CoverageResult(
        CheckStatus.PASS if reason == "hazard_coverage_supported" else CheckStatus.UNKNOWN,
        reason, complete,
    )


def _hazard_check(
    request: MissionRequest, candidate: RouteCandidate, supported: bool,
    freshness: FreshnessResult, coverage: CoverageResult,
) -> ConstraintCheck:
    ceiling = request.hard_constraints.max_edge_hazard

    def unknown(reason: str) -> ConstraintCheck:
        return ConstraintCheck("max_edge_hazard", CheckStatus.UNKNOWN, reason,
                               None, ceiling, "susceptibility")

    evidence = candidate.hazard
    if evidence is None:
        return unknown("hazard_missing")
    if not isinstance(evidence, HazardEvidence):
        return unknown("hazard_invalid")
    if not isinstance(evidence.status, HazardStatus):
        return unknown("hazard_status_invalid")
    if evidence.status != HazardStatus.KNOWN:
        return unknown(f"hazard_{evidence.status.value}")
    score = evidence.max_edge_hazard
    if not finite_number(score) or not 0 <= score <= 1:
        return unknown("hazard_value_invalid")
    if not supported:
        return unknown("hazard_evidence_unsupported")
    if coverage.status != CheckStatus.PASS:
        return unknown(coverage.reason_code)
    if freshness.status != CheckStatus.PASS:
        return unknown(freshness.reason_code)
    passed = score <= ceiling
    return ConstraintCheck(
        "max_edge_hazard", CheckStatus.PASS if passed else CheckStatus.FAIL,
        "within_hazard_limit" if passed else "hazard_limit_exceeded",
        score, ceiling, "susceptibility",
    )


def _vehicle_check(
    request: MissionRequest, candidate: RouteCandidate, supported: bool,
) -> ConstraintCheck:
    evidence = candidate.vehicle_evidence
    types = evidence.compatible_vehicle_types if isinstance(evidence, VehicleEvidence) else None
    if not supported:
        status, reason = CheckStatus.UNKNOWN, "vehicle_evidence_unsupported"
    elif not isinstance(types, frozenset) or any(
        not isinstance(item, str) or not item.strip() for item in types
    ):
        status, reason = CheckStatus.UNKNOWN, "vehicle_compatibility_unknown"
    elif request.vehicle_type in types:
        status, reason = CheckStatus.PASS, "vehicle_compatible"
    else:
        status, reason = CheckStatus.FAIL, "vehicle_incompatible"
    return ConstraintCheck("vehicle_type", status, reason, request.vehicle_type,
                           request.vehicle_type, "vehicle_type")


def _numeric_check(name: str, value: object, limit: float, unit: str) -> ConstraintCheck:
    if not finite_number(value) or value < 0:
        return ConstraintCheck(name, CheckStatus.UNKNOWN, "metric_missing_or_invalid",
                               None, limit, unit)
    passed = value <= limit
    return ConstraintCheck(name, CheckStatus.PASS if passed else CheckStatus.FAIL,
                           "within_limit" if passed else "limit_exceeded",
                           value, limit, unit)


def validate_candidates(
    request: MissionRequest,
    candidates: Iterable[RouteCandidate],
    *,
    as_of: datetime,
    verifier: EvidenceVerifier | None = None,
) -> DecisionResponse:
    """Evaluate every hard check without changing requests or candidates.

    The caller supplies time for reproducibility. A definite violation makes a
    candidate ineligible; otherwise any unknown check prevents eligibility.
    No-feasible outcomes refer only to the supplied set, not the road graph.
    """
    if not isinstance(request, MissionRequest):
        raise ValueError("request must be MissionRequest")
    evaluation_time = utc_datetime(as_of)
    candidates = tuple(candidates)
    if any(not isinstance(candidate, RouteCandidate) for candidate in candidates):
        raise ValueError("candidates must contain RouteCandidate instances")
    ids = [candidate.route_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate route_id in candidate set")

    assessments = []
    for candidate in candidates:
        limits = request.hard_constraints
        hazard_evidence = _evidence_check(request, candidate, candidate.hazard, verifier, hazard=True)
        vehicle_evidence = _evidence_check(request, candidate, candidate.vehicle_evidence, verifier, hazard=False)
        hazard_supported = hazard_evidence.status == CheckStatus.PASS
        vehicle_supported = vehicle_evidence.status == CheckStatus.PASS
        freshness = evaluate_freshness(candidate.hazard, evaluation_time, limits.max_hazard_age_sec,
                               hazard_supported)
        coverage = _coverage(candidate.hazard, hazard_supported)
        checks = [_hazard_check(request, candidate, hazard_supported, freshness, coverage),
                  _vehicle_check(request, candidate, vehicle_supported),
                  hazard_evidence, vehicle_evidence]
        for name, value, limit, unit in (
            ("max_travel_time_sec", candidate.travel_time_sec, limits.max_travel_time_sec, "s"),
            ("max_distance_m", candidate.distance_m, limits.max_distance_m, "m"),
        ):
            if limit is not None:
                checks.append(_numeric_check(name, value, limit, unit))
        if any(check.status == CheckStatus.FAIL for check in checks):
            status = CandidateStatus.INELIGIBLE
        elif any(check.status == CheckStatus.UNKNOWN for check in checks):
            status = CandidateStatus.UNKNOWN
        else:
            status = CandidateStatus.ELIGIBLE
        assessments.append(CandidateAssessment(candidate.route_id, status, tuple(checks),
                                               freshness, coverage))

    eligible = tuple(a.route_id for a in assessments if a.status == CandidateStatus.ELIGIBLE)
    if eligible:
        outcome = DecisionStatus.ELIGIBLE_CANDIDATES
    elif not assessments:
        outcome = DecisionStatus.NO_CANDIDATES
    elif any(a.status == CandidateStatus.UNKNOWN for a in assessments):
        outcome = DecisionStatus.INSUFFICIENT_DATA
    else:
        outcome = DecisionStatus.NO_FEASIBLE_CANDIDATE
    return DecisionResponse(
        request.request_id, request.mission_id, evaluation_time, outcome,
        eligible, tuple(assessments),
        evaluation_time_reason=None if evaluation_time is not None else "evaluation_time_invalid",
        expected_graph_snapshot_id=request.expected_graph_snapshot_id,
        expected_hazard_snapshot_id=request.expected_hazard_snapshot_id,
    )
