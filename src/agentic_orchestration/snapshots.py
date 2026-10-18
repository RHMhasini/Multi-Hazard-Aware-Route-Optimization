"""Copy known data contracts without retaining arbitrary Python payload objects."""

from dataclasses import dataclass, fields
from datetime import datetime

from . import contracts as c
from .execution import BudgetSnapshot, DependencyIdentity, RunDependencies
from .ranking import (
    NormalizationBounds, RankedRoute, RankingMetricIssue, RankingPolicy,
    RankingResult, RankingStatus,
)


@dataclass(frozen=True, slots=True)
class InvalidValue:
    """Immutable marker, never a substitute for a usable measurement or identity."""

    reason: str = "unsupported_or_invalid_value"


_RECORDS = (
    c.HardConstraints, c.MissionRequest, c.HazardEvidence, c.VehicleEvidence,
    c.RankingMetricEvidence, c.RouteCandidate, c.ConstraintCheck, c.FreshnessResult,
    c.CoverageResult, c.CandidateAssessment, c.DecisionResponse,
    RankingPolicy, RankingResult, RankedRoute, RankingMetricIssue, NormalizationBounds,
    DependencyIdentity, RunDependencies, BudgetSnapshot, InvalidValue,
)
_ENUMS = (c.Urgency, c.HazardStatus, c.VerificationStatus, c.CheckStatus,
          c.CandidateStatus, c.DecisionStatus, RankingStatus)


def immutable_snapshot(value, *, extra_types=(), _depth=0):
    """Rebuild allowlisted records; reject mutable/unknown values without repr.

    Invalid lists/dicts become markers, NOT tuples that might accidentally pass
    validation. Never invoke deepcopy/repr or retain custom tzinfo/subclass state.
    This is data isolation, not a sandbox for hostile in-process Python code.
    """
    if _depth > 32:
        return InvalidValue("nesting_limit")
    kind = type(value)
    if kind in (str, int, float, bool, type(None)) or kind in _ENUMS:
        return value
    if kind is datetime:
        return c.utc_datetime(value) or InvalidValue("invalid_datetime")
    if kind in (tuple, frozenset):
        return kind(immutable_snapshot(item, extra_types=extra_types, _depth=_depth + 1)
                    for item in value)
    if kind in _RECORDS + extra_types:
        return kind(**{
            f.name: immutable_snapshot(getattr(value, f.name), extra_types=extra_types,
                                       _depth=_depth + 1)
            for f in fields(value) if f.init
        })
    return InvalidValue()
