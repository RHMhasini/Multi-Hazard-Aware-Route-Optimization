"""Deterministic synthetic mission workflow, not an LLM agent or C3 adapter."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from .contracts import (
    CheckStatus, DecisionStatus, EvidenceVerifier, FreshnessResult, MissionRequest, RankingMetricVerifier,
    RouteCandidate, finite_number, utc_datetime,
)
from .execution import BudgetSnapshot, EvaluationClock, ExecutionBudget, RunDependencies
from .ranking import RankingPolicy, RankingResult, RankingStatus, rank_candidates, validate_mission_policy
from .snapshots import immutable_snapshot
from .validation import evaluate_freshness


class EvaluationMode(str, Enum):
    HISTORICAL = "historical"
    CURRENT = "current"


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
    BUDGET_EXHAUSTED = "budget_exhausted"


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
    evaluated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CompletionFreshness:
    route_id: str
    freshness: FreshnessResult


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
    mode: EvaluationMode
    dependencies: RunDependencies
    budget_checks: tuple[BudgetSnapshot, ...]
    boundary_error: RunError | None
    completed_at: datetime | None
    completion_freshness: tuple[CompletionFreshness, ...]
    schema_version: str = field(default="c4.mission-run/2.0.0", init=False)
    runner_version: str = field(default="synthetic-baseline/2.0.0", init=False)

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


def _snapshot(value):
    return immutable_snapshot(value, extra_types=(RouteAlternatives, RetryPolicy))


def _error(code: str, exc: Exception) -> RunError:
    try:
        detail = str(exc)[:512]
    except Exception:
        detail = "Exception detail unavailable"
    return RunError(code, detail)


def run_mission(
    run_id: str, request: MissionRequest, policy: RankingPolicy,
    provider: RouteAlternativesProvider, *, as_of: datetime,
    budget: ExecutionBudget, dependencies: RunDependencies,
    verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
    retry_policy: RetryPolicy = RetryPolicy(),
    mode: EvaluationMode = EvaluationMode.HISTORICAL,
    clock: EvaluationClock | None = None,
) -> MissionRunTrace:
    """Acquire once, retry explicit transient failures, then use the ranking gate.

    Configuration errors raise ValueError before acquisition. Returned evaluation
    problems are terminal; no policy, time, evidence, or snapshot is repaired here.
    """
    if type(run_id) is not str or not run_id.strip():
        raise ValueError("run_id must be a nonempty string")
    request, policy = _snapshot(request), _snapshot(policy)
    validate_mission_policy(request, policy)
    if type(retry_policy) is not RetryPolicy:
        raise ValueError("retry_policy must be RetryPolicy")
    retry_policy = _snapshot(retry_policy)
    dependencies = _snapshot(dependencies)
    if type(dependencies) is not RunDependencies:
        raise ValueError("Versioned dependency identities are required")
    if type(mode) is not EvaluationMode:
        raise ValueError("mode must be EvaluationMode")
    if mode == EvaluationMode.CURRENT and (clock is None or dependencies.clock is None):
        raise ValueError("Current evaluation requires a host clock and clock identity")
    evaluation_time = utc_datetime(as_of)
    if evaluation_time is None:
        raise ValueError("A valid timezone-aware as_of is required for a mission run")

    calls = []
    budget_checks = []
    boundary_error = None
    completed_at = None
    completion_checks = []
    transitions = [StateTransition(0, None, RunState.CREATED, "run_created")]

    def transition(state: RunState, reason: str) -> None:
        transitions.append(StateTransition(len(transitions), transitions[-1].to_state, state, reason))

    def finish(outcome: RunOutcome, evaluation: EvaluationRecord | None = None) -> MissionRunTrace:
        transition(RunState.FINISHED, outcome.value)
        return MissionRunTrace(run_id, request, policy, evaluation_time, retry_policy,
                               tuple(transitions), tuple(calls), evaluation, outcome,
                               mode, dependencies, tuple(budget_checks), boundary_error,
                               completed_at, tuple(completion_checks))

    def checkpoint(*, reserve=False) -> bool:
        nonlocal boundary_error
        try:
            snapshot = budget.checkpoint(reserve_provider_call=reserve)
            if type(snapshot) is not BudgetSnapshot:
                raise ValueError("Invalid budget response")
            snapshot = _snapshot(snapshot)
            if (type(snapshot.exhausted) is not bool
                    or type(snapshot.scope_id) is not str or not snapshot.scope_id.strip()
                    or type(snapshot.provider_calls) is not int or snapshot.provider_calls < 0
                    or type(snapshot.max_provider_calls) is not int
                    or snapshot.max_provider_calls < snapshot.provider_calls
                    or not finite_number(snapshot.elapsed_sec) or snapshot.elapsed_sec < 0
                    or not finite_number(snapshot.max_duration_sec) or snapshot.max_duration_sec <= 0
                    or (snapshot.reason is not None and type(snapshot.reason) is not str)
                    or (not snapshot.exhausted and snapshot.elapsed_sec >= snapshot.max_duration_sec)):
                raise ValueError("Invalid budget snapshot")
            if budget_checks:
                previous = budget_checks[-1]
                if ((snapshot.scope_id, snapshot.max_provider_calls, snapshot.max_duration_sec)
                        != (previous.scope_id, previous.max_provider_calls, previous.max_duration_sec)
                        or snapshot.provider_calls < previous.provider_calls
                        or snapshot.elapsed_sec < previous.elapsed_sec
                        or (reserve and not snapshot.exhausted
                            and snapshot.provider_calls <= previous.provider_calls)):
                    raise ValueError("Budget scope or counters changed")
            budget_checks.append(snapshot)
            if snapshot.exhausted:
                boundary_error = RunError("budget_exhausted", snapshot.reason or "Budget unavailable")
                return False
        except Exception as exc:
            boundary_error = _error("budget_unavailable", exc)
            return False
        return True

    def current_time() -> datetime:
        value = utc_datetime(clock.now())
        if value is None or value < evaluation_time:
            raise ValueError("Invalid or regressing authoritative evaluation clock")
        return value

    if not checkpoint():
        return finish(RunOutcome.BUDGET_EXHAUSTED)

    for attempt in range(1, retry_policy.max_attempts + 1):
        if not checkpoint(reserve=True):
            return finish(RunOutcome.BUDGET_EXHAUSTED)
        transition(RunState.ACQUIRING, "request_alternatives")
        call = AlternativesCall(run_id, f"{run_id}:alternatives:{attempt}", attempt,
                                request, evaluation_time)
        try:
            raw_response = provider.get_alternatives(call)
        except TransientProviderError as exc:
            calls.append(ProviderCallRecord(call, None, _error("provider_transient", exc)))
            if not checkpoint():
                return finish(RunOutcome.BUDGET_EXHAUSTED)
            if attempt == retry_policy.max_attempts:
                return finish(RunOutcome.RETRY_EXHAUSTED)
            transition(RunState.RETRYING, "transient_retry_permitted")
            continue
        except ProviderError as exc:
            calls.append(ProviderCallRecord(call, None, _error("provider_permanent", exc)))
            return finish(RunOutcome.PROVIDER_FAILURE)
        except Exception as exc:
            calls.append(ProviderCallRecord(call, None, _error("provider_unexpected", exc)))
            return finish(RunOutcome.PROVIDER_FAILURE)

        try:
            response = _snapshot(raw_response)
            if not _valid_response(response):
                raise ValueError("Unsupported envelope, version, or candidate structure")
        except Exception as exc:
            calls.append(ProviderCallRecord(call, None, _error("provider_invalid_response", exc)))
            return finish(RunOutcome.PROVIDER_FAILURE)
        calls.append(ProviderCallRecord(call, response, None))
        if not checkpoint():
            return finish(RunOutcome.BUDGET_EXHAUSTED)
        transition(RunState.EVALUATING, "rank_candidates")
        evaluation_id = f"{run_id}:evaluate"
        evaluated_at = evaluation_time
        if mode == EvaluationMode.CURRENT:
            try:
                evaluated_at = current_time()
            except Exception as exc:
                boundary_error = _error("evaluation_clock_failed", exc)
                return finish(RunOutcome.EVALUATION_FAILURE, EvaluationRecord(
                    evaluation_id, response, None, boundary_error, None,
                ))
        try:
            if not checkpoint():
                return finish(RunOutcome.BUDGET_EXHAUSTED)
            ranking = _snapshot(rank_candidates(request, response.candidates, policy, as_of=evaluated_at,
                                               verifier=verifier, metric_verifier=metric_verifier))
            if type(ranking) is not RankingResult:
                raise TypeError("Invalid ranking result")
        except ValueError as exc:
            return finish(RunOutcome.INVALID_INPUT, EvaluationRecord(
                evaluation_id, response, None, _error("evaluation_invalid_input", exc), evaluated_at,
            ))
        except Exception as exc:
            return finish(RunOutcome.EVALUATION_FAILURE, EvaluationRecord(
                evaluation_id, response, None, _error("evaluation_failed", exc), evaluated_at,
            ))

        evaluation = EvaluationRecord(evaluation_id, response, ranking, None, evaluated_at)
        if not checkpoint():
            return finish(RunOutcome.BUDGET_EXHAUSTED, evaluation)
        if mode == EvaluationMode.CURRENT and ranking.status == RankingStatus.SELECTED:
            try:
                completed_at = current_time()
                if completed_at < evaluated_at:
                    raise ValueError("Evaluation clock regressed during ranking")
                for candidate in response.candidates:
                    if candidate.route_id in ranking.validation.eligible_route_ids:
                        completion_checks.append(CompletionFreshness(candidate.route_id, evaluate_freshness(
                            candidate.hazard, completed_at, request.hard_constraints.max_hazard_age_sec, True,
                        )))
                if not checkpoint():
                    return finish(RunOutcome.BUDGET_EXHAUSTED, evaluation)
                if any(check.freshness.status != CheckStatus.PASS for check in completion_checks):
                    boundary_error = RunError("completion_freshness_failed", "Evidence expired before release")
                    return finish(RunOutcome.INSUFFICIENT_DATA, evaluation)
            except Exception as exc:
                boundary_error = _error("evaluation_clock_failed", exc)
                return finish(RunOutcome.EVALUATION_FAILURE, evaluation)
        if ranking.status == RankingStatus.SELECTED:
            outcome = RunOutcome.SELECTED
        elif (ranking.status == RankingStatus.INSUFFICIENT_RANKING_DATA
              or ranking.validation.status == DecisionStatus.INSUFFICIENT_DATA):
            outcome = RunOutcome.INSUFFICIENT_DATA
        else:
            outcome = RunOutcome.NO_ELIGIBLE_CANDIDATES
        return finish(outcome, evaluation)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    ranking: RankingResult
    evaluated_at: datetime
    recorded_dependencies: RunDependencies
    mode: EvaluationMode = field(default=EvaluationMode.HISTORICAL, init=False)


def replay_evaluation(
    trace: MissionRunTrace, *, verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
) -> ReplayResult:
    """Re-evaluate recorded inputs with independently supplied verifier dependencies.

    Does not call a provider or trust the recorded outcome as verification. Exact
    reproduction requires the same code and independently retained authority data.
    """
    if (not isinstance(trace, MissionRunTrace) or trace.evaluation is None
            or trace.evaluation.ranking is None):
        raise ValueError("Trace has no completed evaluation to replay")
    if (trace.schema_version != "c4.mission-run/2.0.0"
            or trace.runner_version != "synthetic-baseline/2.0.0"):
        raise ValueError("Unsupported trace or runner version")
    ranking = rank_candidates(trace.request, trace.evaluation.alternatives.candidates,
                              trace.policy, as_of=trace.evaluation.evaluated_at, verifier=verifier,
                              metric_verifier=metric_verifier)
    return ReplayResult(_snapshot(ranking), trace.evaluation.evaluated_at, trace.dependencies)
