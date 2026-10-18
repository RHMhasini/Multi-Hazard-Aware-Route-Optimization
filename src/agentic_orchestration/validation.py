"""Fail-closed evaluation of supplied synthetic route evidence."""

from collections.abc import Iterable
from datetime import datetime, timezone

from .contracts import (
    CandidateAssessment, CandidateStatus, CheckStatus, ConstraintCheck,
    DecisionResponse, DecisionStatus, HazardEvidence, HazardStatus,
    MissionRequest, RouteCandidate, aware_datetime, finite_number,
)


def _hazard_check(
    request: MissionRequest, candidate: RouteCandidate, as_of: datetime,
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
    if evidence.complete_coverage is not True:
        return unknown("hazard_coverage_unknown")
    if not aware_datetime(evidence.observed_at):
        return unknown("hazard_timestamp_missing_or_invalid")
    observed_at = evidence.observed_at.astimezone(timezone.utc)
    age = (as_of - observed_at).total_seconds()
    if age < 0:
        return unknown("hazard_timestamp_in_future")
    if age > request.hard_constraints.max_hazard_age_sec:
        return unknown("hazard_stale")
    if evidence.valid_until is not None:
        if not aware_datetime(evidence.valid_until):
            return unknown("hazard_expiry_invalid")
        expiry = evidence.valid_until.astimezone(timezone.utc)
        if expiry <= observed_at:
            return unknown("hazard_validity_interval_invalid")
        if as_of >= expiry:
            return unknown("hazard_stale")
    passed = score <= ceiling
    return ConstraintCheck(
        "max_edge_hazard", CheckStatus.PASS if passed else CheckStatus.FAIL,
        "within_hazard_limit" if passed else "hazard_limit_exceeded",
        score, ceiling, "susceptibility",
    )


def _vehicle_check(request: MissionRequest, candidate: RouteCandidate) -> ConstraintCheck:
    supported = candidate.compatible_vehicle_types
    if not isinstance(supported, frozenset) or any(
        not isinstance(item, str) or not item.strip() for item in supported
    ):
        status, reason = CheckStatus.UNKNOWN, "vehicle_compatibility_unknown"
    elif request.vehicle_type in supported:
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
) -> DecisionResponse:
    """Evaluate every hard check without changing requests or candidates.

    The caller supplies time for reproducibility. A definite violation makes a
    candidate ineligible; otherwise any unknown check prevents eligibility.
    No-feasible outcomes refer only to the supplied set, not the road graph.
    """
    if not isinstance(request, MissionRequest):
        raise ValueError("request must be MissionRequest")
    if not aware_datetime(as_of):
        raise ValueError("as_of must be a timezone-aware datetime")
    as_of = as_of.astimezone(timezone.utc)
    candidates = tuple(candidates)
    if any(not isinstance(candidate, RouteCandidate) for candidate in candidates):
        raise ValueError("candidates must contain RouteCandidate instances")
    ids = [candidate.route_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate route_id in candidate set")

    assessments = []
    for candidate in candidates:
        checks = [_hazard_check(request, candidate, as_of), _vehicle_check(request, candidate)]
        limits = request.hard_constraints
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
        assessments.append(CandidateAssessment(candidate.route_id, status, tuple(checks)))

    eligible = tuple(a.route_id for a in assessments if a.status == CandidateStatus.ELIGIBLE)
    if eligible:
        outcome = DecisionStatus.ELIGIBLE_CANDIDATES
    elif not assessments:
        outcome = DecisionStatus.NO_CANDIDATES
    elif any(a.status == CandidateStatus.UNKNOWN for a in assessments):
        outcome = DecisionStatus.INSUFFICIENT_DATA
    else:
        outcome = DecisionStatus.NO_FEASIBLE_CANDIDATE
    return DecisionResponse(request.request_id, request.mission_id, as_of, outcome,
                            eligible, tuple(assessments))
