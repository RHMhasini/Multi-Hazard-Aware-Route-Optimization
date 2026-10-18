"""Bounded action interpreter with a deterministic, non-LLM scripted planner."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from itertools import islice
from typing import Protocol

from .contracts import EvidenceVerifier, MissionRequest, RankingMetricVerifier, utc_datetime
from .execution import (
    BudgetGuard, BudgetSnapshot, DependencyIdentity, EvaluationClock, ExecutionBudget,
    RunDependencies,
)
from .orchestration import (
    EvaluationMode, MissionRunTrace, RetryPolicy, RouteAlternativesProvider, RunError,
    RunOutcome, _error, _snapshot, acquire_mission, evaluate_mission,
)
from .ranking import RankingPolicy, validate_mission_policy


class ActionKind(str, Enum):
    ACQUIRE = "acquire_alternatives"
    EVALUATE = "evaluate_alternatives"
    CLARIFY = "request_clarification"
    STOP = "stop"


class ClarificationTopic(str, Enum):
    GRAPH_SNAPSHOT = "expected_graph_snapshot_id"
    HAZARD_SNAPSHOT = "expected_hazard_snapshot_id"
    HOST_REVIEW = "host_review"


class ControllerState(str, Enum):
    READY = "ready"
    ALTERNATIVES_AVAILABLE = "alternatives_available"
    FINISHED = "finished"


class ControllerOutcome(str, Enum):
    SELECTED = "selected"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    UNRESOLVED_INPUT = "unresolved_input"
    INVALID_ACTION = "invalid_action"
    REPEATED_ACTION = "repeated_action"
    ACTION_LIMIT = "action_limit"
    PLANNER_FAILURE = "planner_failure"
    TOOL_FAILURE = "tool_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_FAILURE = "provider_failure"
    RETRY_EXHAUSTED = "retry_exhausted"
    INVALID_INPUT = "invalid_input"
    EVALUATION_FAILURE = "evaluation_failure"


@dataclass(frozen=True, slots=True)
class PlannerAction:
    """Closed schema: no policy, time, snapshot, route-ID or tool arguments."""

    kind: ActionKind
    clarification: ClarificationTopic | None = None


@dataclass(frozen=True, slots=True)
class PlannerObservation:
    step: int
    state: ControllerState
    request_id: str
    mission_id: str
    mission_type: str
    policy_id: str
    policy_version: str
    missing_inputs: tuple[ClarificationTopic, ...]
    allowed_actions: tuple[ActionKind, ...]
    candidate_count: int | None
    schema_version: str = field(default="c4.planner-observation/1.0.0", init=False)


class Planner(Protocol):
    """Propose one action from a bounded summary; no host tools are provided."""

    def propose(self, observation: PlannerObservation) -> PlannerAction: ...


class ScriptedPlanner:
    """Deterministic observation-dependent baseline, NOT an LLM or AI model."""

    def propose(self, observation: PlannerObservation) -> PlannerAction:
        if observation.missing_inputs:
            return PlannerAction(ActionKind.CLARIFY, observation.missing_inputs[0])
        if ActionKind.ACQUIRE in observation.allowed_actions:
            return PlannerAction(ActionKind.ACQUIRE)
        if ActionKind.EVALUATE in observation.allowed_actions:
            return PlannerAction(ActionKind.EVALUATE)
        return PlannerAction(ActionKind.STOP)


@dataclass(frozen=True, slots=True)
class RecordedProposal:
    """Sanitized audit projection; arbitrary planner payloads are never retained."""

    kind: str | None
    clarification: str | None
    valid_shape: bool
    argument_names: tuple[str, ...] = ()


def _proposal(raw: object) -> tuple[RecordedProposal, PlannerAction | None]:
    if type(raw) is dict:
        # Record a bounded projection of rejected transport-like payloads only.
        # A future JSON adapter must validate a closed schema before constructing
        # PlannerAction; the controller never coerces dictionaries into actions.
        kind = raw.get("kind")
        names = tuple(key[:64] for key in islice(raw, 16) if type(key) is str)
        return RecordedProposal(kind[:128] if type(kind) is str else None,
                                None, False, names), None
    if type(raw) is not PlannerAction:
        return RecordedProposal(None, None, False), None
    kind, topic = raw.kind, raw.clarification
    valid = (type(kind) is ActionKind and
             ((kind == ActionKind.CLARIFY and type(topic) is ClarificationTopic)
              or (kind != ActionKind.CLARIFY and topic is None)))
    record = RecordedProposal(
        kind.value if type(kind) is ActionKind else kind[:128] if type(kind) is str else None,
        topic.value if type(topic) is ClarificationTopic else topic[:128] if type(topic) is str else None,
        valid,
    )
    return record, PlannerAction(kind, topic) if valid else None


@dataclass(frozen=True, slots=True)
class ActionRecord:
    observation: PlannerObservation
    proposed: RecordedProposal | None
    accepted: bool
    next_state: ControllerState
    tool_result: MissionRunTrace | None = None
    error: RunError | None = None


@dataclass(frozen=True, slots=True)
class ControllerTrace:
    run_id: str
    request: MissionRequest
    policy: RankingPolicy
    planner_identity: DependencyIdentity
    dependencies: RunDependencies
    as_of: datetime
    mode: EvaluationMode
    max_actions: int
    retry_policy: RetryPolicy
    actions: tuple[ActionRecord, ...]
    budget_checks: tuple[BudgetSnapshot, ...]
    outcome: ControllerOutcome
    evaluation: MissionRunTrace | None
    error: RunError | None
    final_state: ControllerState = field(default=ControllerState.FINISHED, init=False)
    schema_version: str = field(default="c4.agent-controller/1.0.0", init=False)
    controller_version: str = field(default="scripted-boundary/1.0.0", init=False)

    @property
    def selected_route_id(self) -> str | None:
        if self.outcome == ControllerOutcome.SELECTED and self.evaluation is not None:
            return self.evaluation.selected_route_id
        return None


def run_controller(
    run_id: str, request: MissionRequest, policy: RankingPolicy,
    provider: RouteAlternativesProvider, planner: Planner, *,
    planner_identity: DependencyIdentity, as_of: datetime,
    budget: ExecutionBudget, dependencies: RunDependencies,
    verifier: EvidenceVerifier | None = None,
    metric_verifier: RankingMetricVerifier | None = None,
    retry_policy: RetryPolicy = RetryPolicy(), max_actions: int = 4,
    mode: EvaluationMode = EvaluationMode.HISTORICAL,
    clock: EvaluationClock | None = None,
) -> ControllerTrace:
    """Host-owned safety context; planner may propose actions, never a decision.

    All calls are synchronous/cooperatively budgeted, including planner calls.
    In-process Python is not a sandbox. Only ordinary Exception is contained.
    """
    request, policy = _snapshot(request), _snapshot(policy)
    validate_mission_policy(request, policy)
    dependencies, planner_identity = _snapshot(dependencies), _snapshot(planner_identity)
    retry_policy = _snapshot(retry_policy)
    timestamp = utc_datetime(as_of)
    if (type(run_id) is not str or not run_id.strip()
            or type(max_actions) is not int or max_actions < 1
            or type(dependencies) is not RunDependencies
            or type(planner_identity) is not DependencyIdentity
            or type(retry_policy) is not RetryPolicy
            or timestamp is None or type(mode) is not EvaluationMode
            or (mode == EvaluationMode.CURRENT and (clock is None or dependencies.clock is None))):
        raise ValueError("Invalid host controller configuration")
    guard = BudgetGuard(budget)
    records = []
    seen = set()
    state = ControllerState.READY
    held = None
    evaluation = None
    boundary_error = None
    missing = tuple(topic for topic, value in (
        (ClarificationTopic.GRAPH_SNAPSHOT, request.expected_graph_snapshot_id),
        (ClarificationTopic.HAZARD_SNAPSHOT, request.expected_hazard_snapshot_id),
    ) if type(value) is not str or not value.strip())

    def checkpoint() -> bool:
        nonlocal boundary_error
        try:
            if not guard.checkpoint():
                boundary_error = RunError("budget_exhausted", guard.checks[-1].reason or "Budget exhausted")
                return False
        except Exception as exc:
            boundary_error = _error("budget_unavailable", exc)
            return False
        return True

    def finish(outcome: ControllerOutcome, error: RunError | None = None) -> ControllerTrace:
        return ControllerTrace(
            run_id, request, policy, planner_identity, dependencies, timestamp, mode,
            max_actions, retry_policy, tuple(records), tuple(guard.checks), outcome,
            evaluation, error,
        )

    for step in range(1, max_actions + 1):
        if not checkpoint():
            return finish(ControllerOutcome.BUDGET_EXHAUSTED, boundary_error)
        allowed = (ActionKind.CLARIFY, ActionKind.STOP)
        if not missing:
            allowed = ((ActionKind.ACQUIRE,) if held is None else (ActionKind.EVALUATE,)) + allowed
        observation = PlannerObservation(
            step, state, request.request_id[:128], request.mission_id[:128], request.mission_type[:128],
            policy.policy_id[:128], policy.policy_version[:128], missing, allowed,
            len(held.candidates) if held is not None else None,
        )
        try:
            proposed, action = _proposal(planner.propose(observation))
        except Exception as exc:
            error = _error("planner_failed", exc)
            records.append(ActionRecord(observation, None, False, ControllerState.FINISHED, error=error))
            return finish(ControllerOutcome.PLANNER_FAILURE, error)
        if not checkpoint():
            records.append(ActionRecord(observation, proposed, False, ControllerState.FINISHED,
                                        error=boundary_error))
            return finish(ControllerOutcome.BUDGET_EXHAUSTED, boundary_error)
        if action is None or action.kind in seen or action.kind not in allowed:
            repeated = action is not None and action.kind in seen
            error = RunError("repeated_action" if repeated else "invalid_action",
                             "Proposal rejected by the host action allowlist")
            records.append(ActionRecord(observation, proposed, False, ControllerState.FINISHED, error=error))
            return finish(ControllerOutcome.REPEATED_ACTION if repeated else ControllerOutcome.INVALID_ACTION, error)
        seen.add(action.kind)
        if action.kind in (ActionKind.CLARIFY, ActionKind.STOP):
            records.append(ActionRecord(observation, proposed, True, ControllerState.FINISHED))
            return finish(ControllerOutcome.UNRESOLVED_INPUT)
        try:
            if action.kind == ActionKind.ACQUIRE:
                result = acquire_mission(
                    f"{run_id}:acquire", request, policy, provider, as_of=timestamp,
                    budget=budget, dependencies=dependencies, retry_policy=retry_policy,
                    mode=mode, clock=clock,
                )
            else:
                result = evaluate_mission(
                    f"{run_id}:evaluate", request, policy, held, as_of=timestamp,
                    budget=budget, dependencies=dependencies, verifier=verifier,
                    metric_verifier=metric_verifier, mode=mode, clock=clock,
                )
                evaluation = result
        except Exception as exc:
            error = _error("controller_tool_failed", exc)
            records.append(ActionRecord(observation, proposed, True, ControllerState.FINISHED, error=error))
            return finish(ControllerOutcome.TOOL_FAILURE, error)
        available = result.outcome == RunOutcome.ALTERNATIVES_AVAILABLE
        tool_error = result.boundary_error
        if not available and result.outcome != RunOutcome.SELECTED:
            if tool_error is None and result.evaluation is not None:
                tool_error = result.evaluation.error
            if tool_error is None and result.provider_calls:
                tool_error = result.provider_calls[-1].error
        state = ControllerState.ALTERNATIVES_AVAILABLE if available else ControllerState.FINISHED
        if not checkpoint():
            records.append(ActionRecord(observation, proposed, True, ControllerState.FINISHED,
                                        result, boundary_error))
            return finish(ControllerOutcome.BUDGET_EXHAUSTED, boundary_error)
        records.append(ActionRecord(observation, proposed, True, state, result, tool_error))
        if not available:
            return finish(ControllerOutcome(result.outcome.value), tool_error)
        held = result.provider_calls[-1].response
    return finish(ControllerOutcome.ACTION_LIMIT, RunError("action_limit", "Host action limit reached"))
