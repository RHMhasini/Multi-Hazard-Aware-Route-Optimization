"""Deterministic synthetic mission workflow, not an LLM agent or C3 adapter."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from .contracts import (
    DecisionStatus, EvidenceVerifier, MissionRequest, RankingMetricVerifier,
    RouteCandidate, utc_datetime,
)
from .ranking import RankingPolicy, RankingResult, RankingStatus, rank_candidates


class RunState(str, Enum):
    CREATED = "created"
    ACQUIRING = "acquiring"
    RETRYING = "retrying"
    EVALUATING = "evaluating"
    FINISHED = "finished"


class RunOutcome(str, Enum):
    SELECTED = "selected"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    PROVIDER_FAILURE = "provider_failure"
    RETRY_EXHAUSTED = "retry_exhausted"
    INVALID_INPUT = "invalid_input"
    EVALUATION_FAILURE = "evaluation_failure"


class ProviderError(Exception):
    """Permanent by default; only TransientProviderError authorizes a retry."""


class TransientProviderError(ProviderError):
    """Explicit transient classification supplied by a provider implementation."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2  # Includes the initial call; no sleeping in this runner.

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")


@dataclass(frozen=True, slots=True)
class AlternativesCall:
    run_id: str
    call_id: str
    attempt: int
    request: MissionRequest
    as_of: datetime


@dataclass(frozen=True, slots=True)
class RouteAlternatives:
    """C4 provider envelope; does not assert that a C3 API exists."""

    response_id: str
    candidates: tuple[RouteCandidate, ...]
    schema_version: str = "c4.route-alternatives/1.0.0"


class RouteAlternativesProvider(Protocol):
    def get_alternatives(self, call: AlternativesCall) -> RouteAlternatives: ...


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    call: AlternativesCall
    response: RouteAlternatives | None
    error: RunError | None


@dataclass(frozen=True, slots=True)
class StateTransition:
    sequence: int
    from_state: RunState | None
    to_state: RunState
    reason: str


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    call_id: str
    alternatives: RouteAlternatives
    ranking: RankingResult | None
    error: RunError | None


@dataclass(frozen=True, slots=True)
class MissionRunTrace:
    run_id: str
    request: MissionRequest
    policy: RankingPolicy
    as_of: datetime
    retry_policy: RetryPolicy
    transitions: tuple[StateTransition, ...]
    provider_calls: tuple[ProviderCallRecord, ...]
    evaluation: EvaluationRecord | None
    outcome: RunOutcome
    schema_version: str = field(default="c4.mission-run/1.0.0", init=False)
    runner_version: str = field(default="synthetic-baseline/1.0.0", init=False)

    @property
    def selected_route_id(self) -> str | None:
        ranking = self.evaluation.ranking if self.evaluation is not None else None
        if self.outcome == RunOutcome.SELECTED and ranking is not None:
            return ranking.selected_route_id
        return None


def _valid_response(response: object) -> bool:
    return (
        isinstance(response, RouteAlternatives)
        and response.schema_version == "c4.route-alternatives/1.0.0"
        and isinstance(response.response_id, str) and bool(response.response_id.strip())
        and isinstance(response.candidates, tuple)
        and all(isinstance(candidate, RouteCandidate) for candidate in response.candidates)
    )


def run_mission(
    run_id: str, request: MissionRequest, policy: RankingPolicy,
    provider: RouteAlternativesProvider, *, as_of: datetime,
    verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
    retry_policy: RetryPolicy = RetryPolicy(),
) -> MissionRunTrace:
    """Acquire once, retry explicit transient failures, then use the ranking gate.

    Configuration errors raise ValueError before acquisition. Returned evaluation
    problems are terminal; no policy, time, evidence, or snapshot is repaired here.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a nonempty string")
    if not isinstance(request, MissionRequest) or not isinstance(policy, RankingPolicy):
        raise ValueError("request and policy must use the Component 4 contracts")
    if not isinstance(retry_policy, RetryPolicy):
        raise ValueError("retry_policy must be RetryPolicy")
    evaluation_time = utc_datetime(as_of)
    if evaluation_time is None:
        raise ValueError("A valid timezone-aware as_of is required for a mission run")

    calls = []
    transitions = [StateTransition(0, None, RunState.CREATED, "run_created")]

    def transition(state: RunState, reason: str) -> None:
        transitions.append(StateTransition(len(transitions), transitions[-1].to_state, state, reason))

    def finish(outcome: RunOutcome, evaluation: EvaluationRecord | None = None) -> MissionRunTrace:
        transition(RunState.FINISHED, outcome.value)
        return MissionRunTrace(run_id, request, policy, evaluation_time, retry_policy,
                               tuple(transitions), tuple(calls), evaluation, outcome)

    for attempt in range(1, retry_policy.max_attempts + 1):
        transition(RunState.ACQUIRING, "request_alternatives")
        call = AlternativesCall(run_id, f"{run_id}:alternatives:{attempt}", attempt,
                                request, evaluation_time)
        try:
            response = provider.get_alternatives(call)
        except TransientProviderError as exc:
            calls.append(ProviderCallRecord(call, None, RunError("provider_transient", str(exc))))
            if attempt == retry_policy.max_attempts:
                return finish(RunOutcome.RETRY_EXHAUSTED)
            transition(RunState.RETRYING, "transient_retry_permitted")
            continue
        except ProviderError as exc:
            calls.append(ProviderCallRecord(call, None, RunError("provider_permanent", str(exc))))
            return finish(RunOutcome.PROVIDER_FAILURE)
        except Exception as exc:
            calls.append(ProviderCallRecord(call, None, RunError("provider_unexpected", str(exc))))
            return finish(RunOutcome.PROVIDER_FAILURE)

        if not _valid_response(response):
            calls.append(ProviderCallRecord(
                call, response if isinstance(response, RouteAlternatives) else None,
                RunError("provider_invalid_response", "Unsupported envelope, version, or candidate structure"),
            ))
            return finish(RunOutcome.PROVIDER_FAILURE)
        calls.append(ProviderCallRecord(call, response, None))
        transition(RunState.EVALUATING, "rank_candidates")
        evaluation_id = f"{run_id}:evaluate"
        try:
            ranking = rank_candidates(request, response.candidates, policy, as_of=evaluation_time,
                                      verifier=verifier, metric_verifier=metric_verifier)
        except ValueError as exc:
            return finish(RunOutcome.INVALID_INPUT, EvaluationRecord(
                evaluation_id, response, None, RunError("evaluation_invalid_input", str(exc)),
            ))
        except Exception as exc:
            return finish(RunOutcome.EVALUATION_FAILURE, EvaluationRecord(
                evaluation_id, response, None, RunError("evaluation_failed", str(exc)),
            ))

        if ranking.status == RankingStatus.SELECTED:
            outcome = RunOutcome.SELECTED
        elif (ranking.status == RankingStatus.INSUFFICIENT_RANKING_DATA
              or ranking.validation.status == DecisionStatus.INSUFFICIENT_DATA):
            outcome = RunOutcome.INSUFFICIENT_DATA
        else:
            outcome = RunOutcome.NO_ELIGIBLE_CANDIDATES
        return finish(outcome, EvaluationRecord(evaluation_id, response, ranking, None))


def replay_evaluation(
    trace: MissionRunTrace, *, verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
) -> RankingResult:
    """Re-evaluate recorded inputs with independently supplied verifier dependencies.

    Does not call a provider or trust the recorded outcome as verification. Exact
    reproduction requires the same code and independently retained authority data.
    """
    if (not isinstance(trace, MissionRunTrace) or trace.evaluation is None
            or trace.evaluation.ranking is None):
        raise ValueError("Trace has no completed evaluation to replay")
    if (trace.schema_version != "c4.mission-run/1.0.0"
            or trace.runner_version != "synthetic-baseline/1.0.0"):
        raise ValueError("Unsupported trace or runner version")
    return rank_candidates(trace.request, trace.evaluation.alternatives.candidates,
                           trace.policy, as_of=trace.as_of, verifier=verifier,
                           metric_verifier=metric_verifier)
