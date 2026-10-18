"""Typed, in-memory prototype contracts; no upstream component dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite

REQUEST_VERSION = "c4.mission-request/1.0.0"
DECISION_VERSION = "c4.mission-decision/1.0.0"


class Urgency(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    CRITICAL = "critical"


class HazardStatus(str, Enum):
    KNOWN = "known"
    MISSING = "missing"
    UNKNOWN = "unknown"
    INVALID = "invalid"
    STALE = "stale"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class CandidateStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class DecisionStatus(str, Enum):
    ELIGIBLE_CANDIDATES = "eligible_candidates"
    NO_FEASIBLE_CANDIDATE = "no_feasible_candidate"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_CANDIDATES = "no_candidates"


def finite_number(value: object) -> bool:
    """Accept finite Python numbers, excluding booleans and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class HardConstraints:
    """Caller-supplied policy; no emergency-operation thresholds are invented."""

    max_edge_hazard: float
    max_hazard_age_sec: float
    max_travel_time_sec: float | None = None
    max_distance_m: float | None = None

    def __post_init__(self) -> None:
        if not finite_number(self.max_edge_hazard) or not 0 <= self.max_edge_hazard <= 1:
            raise ValueError("max_edge_hazard must be finite and in [0, 1]")
        for name in ("max_hazard_age_sec", "max_travel_time_sec", "max_distance_m"):
            value = getattr(self, name)
            if value is None and name != "max_hazard_age_sec":
                continue
            if not finite_number(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class MissionRequest:
    request_id: str
    mission_id: str
    mission_type: str
    vehicle_type: str
    urgency: Urgency
    hard_constraints: HardConstraints
    schema_version: str = REQUEST_VERSION

    def __post_init__(self) -> None:
        for name in ("request_id", "mission_id", "mission_type", "vehicle_type"):
            _nonempty(getattr(self, name), name)
        if self.schema_version != REQUEST_VERSION:
            raise ValueError("Unsupported mission request schema_version")
        if not isinstance(self.urgency, Urgency):
            raise ValueError("urgency must be an Urgency member")
        if not isinstance(self.hard_constraints, HardConstraints):
            raise ValueError("hard_constraints must be HardConstraints")


@dataclass(frozen=True, slots=True)
class HazardEvidence:
    """Untrusted candidate evidence. Invalid values are classified by validation.

    complete_coverage is an upstream assertion about the entire route, not a
    coverage estimate computed by Component 4. NoData must remain explicit.
    """

    status: HazardStatus = HazardStatus.UNKNOWN
    max_edge_hazard: float | None = None
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    complete_coverage: bool = False


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    route_id: str
    hazard: HazardEvidence | None = None
    compatible_vehicle_types: frozenset[str] | None = None
    travel_time_sec: float | None = None
    distance_m: float | None = None

    def __post_init__(self) -> None:
        _nonempty(self.route_id, "route_id")


@dataclass(frozen=True, slots=True)
class ConstraintCheck:
    constraint_id: str
    status: CheckStatus
    reason_code: str
    observed_value: float | str | None
    limit: float | str
    unit: str


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    route_id: str
    status: CandidateStatus
    checks: tuple[ConstraintCheck, ...]


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    """Validation outcome only: eligibility is not route ranking or dispatch."""

    request_id: str
    mission_id: str
    evaluated_at: datetime
    status: DecisionStatus
    eligible_route_ids: tuple[str, ...]
    candidate_assessments: tuple[CandidateAssessment, ...]
    schema_version: str = field(default=DECISION_VERSION, init=False)
