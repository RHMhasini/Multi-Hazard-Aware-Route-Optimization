"""Experimental weighted baseline over freshly validated eligible candidates."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isclose
import re

from .contracts import (
    DecisionResponse, EvidenceVerifier, MissionRequest, RouteCandidate, finite_number,
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


def rank_candidates(
    request: MissionRequest,
    candidates: Iterable[RouteCandidate],
    policy: RankingPolicy,
    *,
    as_of: datetime,
    verifier: EvidenceVerifier | None = None,
) -> RankingResult:
    """Validate the exact supplied records, then rank only eligible candidates.

    Both metrics must be finite and nonnegative for EVERY eligible candidate,
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
        for name in ("travel_time_sec", "cumulative_hazard_exposure"):
            value = getattr(candidate, name)
            if not finite_number(value) or value < 0:
                issues.append(RankingMetricIssue(
                    route_id, name, "metric_missing" if value is None else "metric_invalid",
                ))
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
                    time_term, exposure_term, score)
        for index, (candidate, time, exposure, time_term, exposure_term, score)
        in enumerate(scored, start=1)
    )
    return result(RankingStatus.SELECTED, ranked=ranked, bounds=bounds)
