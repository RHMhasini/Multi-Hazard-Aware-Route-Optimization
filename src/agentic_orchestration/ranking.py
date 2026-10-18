"""Experimental weighted baseline over freshly validated eligible candidates."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isclose
import re

from .contracts import (
    DecisionResponse, EvidenceVerifier, MissionRequest, RankingMetricEvidence,
    RankingMetricVerifier, RouteCandidate, VerificationStatus, finite_number,
)
from .validation import validate_candidates


class RankingStatus(str, Enum):
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    INSUFFICIENT_RANKING_DATA = "insufficient_ranking_data"
    SELECTED = "selected"


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """Explicit mission applicability; weights never imply a safety policy."""

    policy_id: str
    policy_version: str
    mission_type: str
    time_weight: float
    exposure_weight: float
    schema_version: str = field(default="c4.ranking-policy/1.0.0", init=False)

    def __post_init__(self) -> None:
        for name in ("policy_id", "mission_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")
        if not isinstance(self.policy_version, str) or not re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", self.policy_version
        ):
            raise ValueError("policy_version must use major.minor.patch")
        weights = (self.time_weight, self.exposure_weight)
        if any(not finite_number(w) or not 0 <= w <= 1 for w in weights):
            raise ValueError("Weights must be finite numbers in [0, 1]")
        if not isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("Weights must sum to one within 1e-12")


@dataclass(frozen=True, slots=True)
class NormalizationBounds:
    time_min_sec: float
    time_max_sec: float
    exposure_min_haz_m: float
    exposure_max_haz_m: float
    method: str = field(default="eligible_set_min_max", init=False)


@dataclass(frozen=True, slots=True)
class RankingMetricIssue:
    route_id: str
    metric: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class RankedRoute:
    rank: int
    route_id: str
    travel_time_sec: float
    cumulative_hazard_exposure: float
    normalized_time: float
    normalized_exposure: float
    time_contribution: float
    exposure_contribution: float
    score: float
    metric_evidence: RankingMetricEvidence


@dataclass(frozen=True, slots=True)
class RankingResult:
    policy: RankingPolicy
    validation: DecisionResponse
    status: RankingStatus
    comparison_route_ids: tuple[str, ...]
    ranked_routes: tuple[RankedRoute, ...]
    metric_issues: tuple[RankingMetricIssue, ...]
    selected_route_id: str | None
    normalization: NormalizationBounds | None
    effective_time_weight: float
    effective_exposure_weight: float
    schema_version: str = field(default="c4.mission-ranking/1.0.0", init=False)


def _normalize(value: float, minimum: float, maximum: float) -> float:
    # A constant objective provides no discrimination, including a singleton set.
    return 0.0 if maximum == minimum else (value - minimum) / (maximum - minimum)


def _metric_claim_problem(
    request: MissionRequest, candidate: RouteCandidate, evidence: RankingMetricEvidence | None,
) -> str | None:
    if not isinstance(evidence, RankingMetricEvidence):
        return "metric_evidence_missing_or_invalid"
    bindings = ((evidence.route_id, candidate.route_id),
                (evidence.graph_snapshot_id, request.expected_graph_snapshot_id),
                (evidence.hazard_snapshot_id, request.expected_hazard_snapshot_id))
    if any(not isinstance(value, str) or not value.strip()
           for pair in bindings for value in pair):
        return "metric_evidence_identifiers_missing"
    if any(actual != expected for actual, expected in bindings):
        return "metric_evidence_binding_mismatch"
    for value in (evidence.route_result_id, evidence.evidence_source,
                  evidence.travel_time_model_version, evidence.exposure_formula_version):
        if not isinstance(value, str) or not value.strip():
            return "metric_evidence_metadata_missing"
    if evidence.verification_status is not VerificationStatus.VERIFIED:
        return "metric_evidence_not_verified"
    if evidence.travel_time_unit != "s" or evidence.exposure_unit != "hazard*m":
        return "metric_evidence_units_mismatch"
    for name in ("travel_time_sec", "cumulative_hazard_exposure"):
        value = getattr(evidence, name)
        if not finite_number(value) or value < 0:
            return "metric_evidence_value_invalid"
        if value != getattr(candidate, name):
            return "metric_evidence_values_mismatch"
    return None


def _metric_evidence_problem(
    request: MissionRequest, candidate: RouteCandidate, verifier: RankingMetricVerifier | None,
) -> str | None:
    evidence = candidate.ranking_metric_evidence
    problem = _metric_claim_problem(request, candidate, evidence)
    if problem is not None:
        return problem
    if verifier is None:
        return "metric_verifier_missing"
    try:
        authoritative = verifier.get_authoritative_metrics(evidence.route_result_id)
        # A boolean approval, source name or digest is not an authoritative record.
        if not isinstance(authoritative, RankingMetricEvidence):
            return "metric_evidence_unsupported"
        if _metric_claim_problem(request, candidate, authoritative) is not None:
            return "metric_authority_mismatch"
        if authoritative != evidence:
            return "metric_authority_mismatch"
    except Exception:
        return "metric_verifier_error"
    return None


def rank_candidates(
    request: MissionRequest,
    candidates: Iterable[RouteCandidate],
    policy: RankingPolicy,
    *,
    as_of: datetime,
    verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
) -> RankingResult:
    """Validate the exact supplied records, then rank only eligible candidates.

    Both metrics must be finite, nonnegative and independently supported for EVERY eligible candidate,
    even at zero weight. Otherwise no partial ranking or selection is returned.
    This avoids silently changing the normalization population due to missing data.
    """
    if not isinstance(request, MissionRequest):
        raise ValueError("request must be MissionRequest")
    if not isinstance(policy, RankingPolicy):
        raise ValueError("policy must be RankingPolicy")
    if policy.mission_type != request.mission_type:
        raise ValueError("Policy mission_type must exactly match request mission_type")

    candidates = tuple(candidates)
    validation = validate_candidates(request, candidates, as_of=as_of, verifier=verifier)
    by_id = {candidate.route_id: candidate for candidate in candidates}
    comparison_ids = tuple(sorted(validation.eligible_route_ids))
    # Remove only floating-point sum drift allowed by the policy's sum tolerance.
    total_weight = policy.time_weight + policy.exposure_weight
    time_weight = policy.time_weight / total_weight
    exposure_weight = policy.exposure_weight / total_weight

    def result(status, ranked=(), issues=(), bounds=None):
        return RankingResult(
            policy, validation, status, comparison_ids, tuple(ranked), tuple(issues),
            ranked[0].route_id if ranked else None, bounds, time_weight, exposure_weight,
        )

    if not comparison_ids:
        return result(RankingStatus.NO_ELIGIBLE_CANDIDATES)

    issues = []
    for route_id in comparison_ids:
        candidate = by_id[route_id]
        candidate_issues = []
        for name in ("travel_time_sec", "cumulative_hazard_exposure"):
            value = getattr(candidate, name)
            if not finite_number(value) or value < 0:
                candidate_issues.append(RankingMetricIssue(
                    route_id, name, "metric_missing" if value is None else "metric_invalid",
                ))
        if not candidate_issues:
            problem = _metric_evidence_problem(request, candidate, metric_verifier)
            if problem is not None:
                candidate_issues.append(RankingMetricIssue(route_id, "ranking_metric_evidence", problem))
        issues.extend(candidate_issues)
    if issues:
        return result(RankingStatus.INSUFFICIENT_RANKING_DATA, issues=issues)

    eligible = [by_id[route_id] for route_id in comparison_ids]
    bounds = NormalizationBounds(
        min(c.travel_time_sec for c in eligible), max(c.travel_time_sec for c in eligible),
        min(c.cumulative_hazard_exposure for c in eligible),
        max(c.cumulative_hazard_exposure for c in eligible),
    )
    scored = []
    for candidate in eligible:
        time = _normalize(candidate.travel_time_sec, bounds.time_min_sec, bounds.time_max_sec)
        exposure = _normalize(candidate.cumulative_hazard_exposure,
                              bounds.exposure_min_haz_m, bounds.exposure_max_haz_m)
        time_term, exposure_term = time_weight * time, exposure_weight * exposure
        scored.append((candidate, time, exposure, time_term, exposure_term,
                       time_term + exposure_term))
    # No rounding or tolerance-based ties: exact float score, then Unicode ID order.
    scored.sort(key=lambda item: (item[5], item[0].route_id))
    ranked = tuple(
        RankedRoute(index, candidate.route_id, candidate.travel_time_sec,
                    candidate.cumulative_hazard_exposure, time, exposure,
                    time_term, exposure_term, score, candidate.ranking_metric_evidence)
        for index, (candidate, time, exposure, time_term, exposure_term, score)
        in enumerate(scored, start=1)
    )
    return result(RankingStatus.SELECTED, ranked=ranked, bounds=bounds)
