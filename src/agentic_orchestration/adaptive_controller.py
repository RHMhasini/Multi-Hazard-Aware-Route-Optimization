"""M3 observe/choose/dispatch/feedback loop. No LLM, persistence or real adapters.

The host retains the registry, controller and adaptive budget across run IDs.
Clarification is terminal feedback only, not an authenticated resume mechanism.
Only trusted fixture events may adopt new source state; planners receive no
registry, lease, mutable budget, authority or event callback.
"""

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from enum import Enum

from .adaptive_contracts import (
    Availability, DependencyState, Freshness, Tool, ToolOption, _fresh_at, _ref, _timestamp,
)
from .contracts import VerificationStatus
from .dependency_state import derive_eligibility
from .execution import AdaptiveBudgetSnapshot, AdaptiveExecutionBudget, EvaluationClock
from .ranking import RankingResult, RankingStatus
from .rule_planner import (
    ActionKind, AdaptiveAction, AdaptiveObservation, AdaptivePlanner, Reason, ToolFeedback,
)
from .tool_registry import FailureKind, ToolRegistry, ToolRejected


class AdaptiveOutcome(str, Enum):
    SUCCESS = "verified_recommendation"
    CLARIFICATION = "clarification_required"
    ABSTAINED = "abstained"
    NO_PROGRESS = "no_progress"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUSY = "busy"
    FAILED = "host_boundary_failure"


@dataclass(frozen=True, slots=True)
class StateView:
    revision: int
    as_of: datetime
    graph_snapshot_id: str | None
    hazard_snapshot_id: str | None
    alternatives_ref: str | None


@dataclass(frozen=True, slots=True)
class AdaptiveStep:
    sequence: int
    host_before: StateView
    before: StateView
    available_tools: tuple[Tool, ...]
    eligible_tools: tuple[Tool, ...]
    dispatchable_tools: tuple[Tool, ...]
    action: AdaptiveAction | None
    feedback: ToolFeedback | None
    after: StateView
    resources: AdaptiveBudgetSnapshot
    reason: Reason


@dataclass(frozen=True, slots=True)
class AdaptiveTrace:
    run_id: str
    outcome: AdaptiveOutcome
    reason: Reason
    steps: tuple[AdaptiveStep, ...]
    final_state: StateView
    resources: AdaptiveBudgetSnapshot
    checked_at: datetime
    ranking: RankingResult | None = None

    @property
    def selected_route_id(self):
        return self.ranking.selected_route_id if self.outcome is AdaptiveOutcome.SUCCESS and self.ranking else None


def _view(state):
    return StateView(state.state_revision, state.as_of,
                     state.graph.snapshot.snapshot_id if state.graph.snapshot else None,
                     state.hazard.snapshot.snapshot_id if state.hazard.snapshot else None,
                     state.alternatives.alternatives_ref)


def _meaning(state):
    # Advancing time/revision alone does not constitute new evidence or progress.
    return (state.mission_ref, state.policy_ref, state.od_ref, state.graph, state.hazard,
            state.routing_context, state.alternatives, state.capabilities)


def _progress_key(state):
    """Accepted evidence/mission identity, excluding cosmetic observation churn.

    Only independently verified artifacts count as new available evidence.
    Observation/build times, freshness aging, audit references, tuple order and
    state revisions cannot replenish the consecutive no-progress allowance.
    Capabilities are trusted host declarations, normalized to executability.
    """
    def evidence(value):
        if value.availability is not Availability.AVAILABLE or value.verification is not VerificationStatus.VERIFIED:
            return None
        return value.snapshot.snapshot_id, value.snapshot.source_class, value.coverage
    context = state.routing_context
    alternatives = state.alternatives
    caps = {c.tool: c.supported and c.authorized and c.inputs_ready for c in state.capabilities}
    return (state.mission_ref, state.policy_ref, state.od_ref, evidence(state.graph), evidence(state.hazard),
            context.binding if context is not None and context.verification is VerificationStatus.VERIFIED else None,
            (alternatives.alternatives_ref, alternatives.binding)
            if alternatives.availability is Availability.AVAILABLE
            and alternatives.verification is VerificationStatus.VERIFIED else None,
            tuple((tool, caps.get(tool, False)) for tool in Tool))


def _observation_copy(value):
    """Isolate validated host records, even from Python freeze-bypass mutation.

    M1 validation admits only known immutable records/containers and fixed-offset
    datetimes here. Option handles are intentionally separate and M2 checks their
    original values. This is data isolation, not a sandbox for arbitrary Python.
    """
    if is_dataclass(value):
        return replace(value, **{f.name: _observation_copy(getattr(value, f.name))
                                 for f in fields(value) if f.init})
    if type(value) is tuple:
        return tuple(_observation_copy(item) for item in value)
    if type(value) is frozenset:
        return frozenset(_observation_copy(item) for item in value)
    return value


class AdaptiveController:
    def __init__(self, registry: ToolRegistry, budget: AdaptiveExecutionBudget,
                 planner: AdaptivePlanner, *, clock: EvaluationClock,
                 host_event: Callable[[DependencyState, int], DependencyState | None] | None = None):
        if type(registry) is not ToolRegistry or type(budget) is not AdaptiveExecutionBudget:
            raise ValueError("Expected trusted registry and adaptive execution budget")
        # Metered planner adapters must use this very ledger, not a new scope.
        if hasattr(planner, "uses_budget") and planner.uses_budget(budget) is not True:
            raise ValueError("Planner must share the controller execution budget")
        self._registry, self._budget, self._planner = registry, budget, planner
        self._clock, self._host_event = clock, host_event
        self._last_time = registry.state.as_of
        self._feedback = None
        first_binding = self._budget.bind_session(registry, _progress_key(registry.state))
        self._evaluation = None
        # Bootstrap only from registry-accepted evaluation records, never a
        # caller-supplied selected-route string or an adapter's own claim.
        for result in registry.records:
            if result.ok and result.tool is Tool.EVALUATE:
                self._evaluation = result
                if first_binding:
                    self._budget.remember_evaluation(registry.operation_identity(Tool.EVALUATE, result.update.state))

    def _accept_progress(self):
        """Called only on registry-owned state, never planner observations/flags."""
        return self._budget.observe_progress(_progress_key(self._registry.state))

    def _now(self):
        try:
            value = _timestamp(self._clock.now())
            if value < self._last_time or value < self._registry.state.as_of:
                raise ValueError("Regressing evidence clock")
        except Exception:
            raise ToolRejected(FailureKind.PERMANENT, "evidence_clock_invalid") from None
        self._last_time = value
        return value

    def _refresh(self, control):
        state = self._registry.state
        now = self._now()
        if now == state.as_of:
            state.__post_init__()
            return
        self._registry.adopt_host_state(self._at_time(state, now), control=control)

    def _at_time(self, state, now):
        def age(evidence, maximum):
            if evidence.freshness not in (Freshness.FRESH, Freshness.STALE):
                return evidence
            return replace(evidence, freshness=Freshness.FRESH if _fresh_at(evidence.snapshot, now, maximum)
                           else Freshness.STALE)
        return replace(state, as_of=now, state_revision=state.state_revision + 1,
                       graph=age(state.graph, state.policy.graph_max_age_sec),
                       hazard=age(state.hazard, state.policy.hazard_max_age_sec))

    def _cached(self, state):
        result = self._evaluation
        if result is None or type(result.update.payload) is not RankingResult:
            return False
        ranking = result.update.payload
        if ranking.status is not RankingStatus.SELECTED or ranking.selected_route_id is None:
            return False
        old = result.update.state
        if self._registry.operation_identity(Tool.EVALUATE, old) != self._registry.operation_identity(Tool.EVALUATE, state):
            return False
        if not next(a for a in derive_eligibility(state).assessments if a.tool is Tool.EVALUATE).permitted:
            return False
        # Retain the v1 hazard-age/expiry gate, which may be stricter than M1's
        # dependency policy. Reuse covers the entire ranked comparison population.
        for assessment in ranking.validation.candidate_assessments:
            if assessment.route_id not in ranking.comparison_route_ids:
                continue
            fresh = assessment.freshness
            if fresh.observed_at is None:
                return False
            age = (state.as_of - fresh.observed_at).total_seconds()
            if (age < 0 or age > fresh.max_age_sec
                    or fresh.valid_until is not None and state.as_of >= fresh.valid_until):
                return False
        return True

    def _checkpoint(self):
        if not self._budget.checkpoint():
            raise ToolRejected(FailureKind.PERMANENT, "adaptive_budget_exhausted")
        self._now()  # Validate host clock even if elapsed-budget clock is healthy.

    def _observation(self, control):
        self._refresh(control)
        self._accept_progress()
        state = self._registry.state
        eligibility = derive_eligibility(state)
        options, retries = [], []
        for option in self._registry.options(control=control):
            allowed, recovery = self._budget.attempt_status(self._registry.operation_identity(option.tool))
            if allowed:
                options.append(option)
                if recovery:
                    retries.append(option.tool)
        return AdaptiveObservation(_observation_copy(state), eligibility, tuple(options), tuple(retries),
                                   self._cached(state), _observation_copy(self._feedback), self._budget.no_progress,
                                   _observation_copy(self._budget.snapshot))

    def _feedback_for(self, result, identity, recovery, before):
        status, decision_status, codes = None, None, ()
        if result.ok and result.tool is Tool.EVALUATE:
            ranking = result.update.payload
            status = ranking.status
            decision_status = ranking.validation.status
            codes = tuple(sorted({issue.reason_code for issue in ranking.metric_issues}
                                 | {check.reason_code for a in ranking.validation.candidate_assessments
                                    for check in a.checks if check.status.value != "pass"}))[:16]
            self._evaluation = result
        after = self._registry.state
        changed = tuple(name for name in ("graph", "hazard", "routing_context", "alternatives")
                        if getattr(before, name) != getattr(after, name))
        return ToolFeedback(result.call_id, result.tool, result.ok, result.executed, identity,
                            recovery, result.failure, status, codes, changed, decision_status)

    def run(self, run_id: str) -> AdaptiveTrace:
        _ref(run_id)
        records = []
        control = None
        def finish(outcome, reason, ranking=None):
            return AdaptiveTrace(run_id, outcome, reason, tuple(records), _view(self._registry.state),
                                 self._budget.snapshot, self._last_time, ranking)
        if not self._budget.claim_run():
            return finish(AdaptiveOutcome.BUSY, Reason.BUSY)
        try:
            try:
                control = self._registry.claim_control()
            except ToolRejected:
                return finish(AdaptiveOutcome.BUSY, Reason.BUSY)
            while True:
                if not self._budget.reserve_step():
                    return finish(AdaptiveOutcome.BUDGET_EXHAUSTED, Reason.BUDGET)
                host_before = _view(self._registry.state)
                self._checkpoint()
                if self._host_event is not None:
                    event = self._host_event(self._registry.state, len(records))
                    if event is not None:
                        self._registry.adopt_host_state(event, control=control)
                self._checkpoint()
                observation = self._observation(control)
                before = self._registry.state
                available = tuple(c.tool for c in before.capabilities if c.supported and c.authorized and c.inputs_ready)
                # Capture host values BEFORE calling the planner. Identity alone
                # cannot establish unchanged fields on a Python option object.
                offered = tuple((o, replace(o)) for o in observation.options)
                eligible_tools = tuple(o.tool for o in observation.eligibility.options)
                dispatchable_tools = tuple(o.tool for o in observation.options)
                action = feedback = audit_action = None
                reason = Reason.INVALID_ACTION
                def record():
                    records.append(AdaptiveStep(len(records), host_before, _view(before), available,
                        eligible_tools, dispatchable_tools, _observation_copy(audit_action),
                        _observation_copy(feedback),
                        _view(self._registry.state), self._budget.snapshot, reason))
                if self._budget.no_progress >= self._budget.limits.max_no_progress:
                    reason = Reason.NO_PROGRESS
                    record()
                    return finish(AdaptiveOutcome.NO_PROGRESS, reason)
                try:
                    action = self._planner.choose(observation)
                    if type(action) is not AdaptiveAction:
                        raise ValueError("Invalid planner response")
                    action.__post_init__()
                    # Every record path uses this detached, canonical projection,
                    # including exceptions before dispatch/option revalidation.
                    if action.kind is not ActionKind.CALL:
                        audit_action = AdaptiveAction(action.kind, action.reason)
                    else:
                        canonical = next((original for issued, original in offered
                                          if action.option is issued and action.option == original), None)
                        if canonical is not None:
                            audit_action = AdaptiveAction(ActionKind.CALL, action.reason, replace(canonical))
                    self._checkpoint()
                    # Mere passage of time must not invalidate every live-clock
                    # choice. Adopt/invalidate here only if evidence status changes;
                    # normal observation refresh still creates a new revision.
                    current = self._registry.state
                    timed = self._at_time(current, self._last_time)
                    if _meaning(timed) != _meaning(current):
                        self._registry.adopt_host_state(timed, control=control)
                    # Options issued before a planner delay/event never become
                    # executable merely by copying their fields into a new object.
                    if action.kind is ActionKind.CALL:
                        if (not any(action.option is o for o in observation.options)
                                or self._registry.state != before):
                            raise ValueError("Unissued or stale planner choice")
                    elif action.kind is ActionKind.COMPLETE and not self._cached(timed):
                        raise ValueError("No reusable verified evaluation")
                except ToolRejected:
                    reason = Reason.BUDGET if self._budget.snapshot.exhausted else Reason.BOUNDARY
                    record()
                    return finish(AdaptiveOutcome.BUDGET_EXHAUSTED if self._budget.snapshot.exhausted
                                  else AdaptiveOutcome.FAILED, reason)
                except Exception:
                    # Omit invalid objects/narrative from the audit record.
                    action = None
                    self._budget.record_progress_outcome(progress=False)
                    record()
                    if self._budget.no_progress >= self._budget.limits.max_no_progress:
                        return finish(AdaptiveOutcome.NO_PROGRESS, Reason.NO_PROGRESS)
                    continue
                reason = action.reason
                if action.kind is not ActionKind.CALL:
                    record()
                    if action.kind is ActionKind.COMPLETE:
                        return finish(AdaptiveOutcome.SUCCESS, reason, self._evaluation.update.payload)
                    return finish(AdaptiveOutcome.CLARIFICATION if action.kind is ActionKind.CLARIFY
                                  else AdaptiveOutcome.ABSTAINED, reason)
                identity = self._registry.operation_identity(action.option.tool)
                allowed, recovery = self._budget.attempt_status(identity)
                if not allowed:
                    self._budget.record_progress_outcome(progress=False)
                    reason = Reason.NO_PROGRESS
                    record()
                    return finish(AdaptiveOutcome.NO_PROGRESS, reason)
                if not self._budget.reserve_tool(identity):
                    reason = Reason.BUDGET
                    record()
                    return finish(AdaptiveOutcome.BUDGET_EXHAUSTED, reason)
                count = len(self._registry.records)
                def before_invoke():
                    self._checkpoint()
                    if _meaning(self._at_time(self._registry.state, self._last_time)) != _meaning(self._registry.state):
                        raise ToolRejected(FailureKind.STALE_BINDING, "evidence_changed_before_dispatch")
                    if action.option.tool is not Tool.EVALUATE and not self._budget.reserve_provider():
                        raise ToolRejected(FailureKind.PERMANENT, "adaptive_provider_budget_exhausted")
                result = self._registry.execute(action.option, control=control,
                    before_invoke=before_invoke, before_adopt=self._checkpoint)
                self._budget.finish_tool(identity, retryable=bool(result.failure and result.failure.retryable))
                feedback = self._feedback_for(result, identity, recovery, before)
                self._feedback = feedback
                if len(self._registry.records) != count + 1:
                    reason = Reason.INTERFERENCE
                    record()
                    return finish(AdaptiveOutcome.FAILED, reason)
                if not self._budget.checkpoint():
                    reason = Reason.BUDGET
                    record()
                    return finish(AdaptiveOutcome.BUDGET_EXHAUSTED, reason)
                try:
                    self._refresh(control)
                except ToolRejected:
                    reason = Reason.BOUNDARY
                    record()
                    return finish(AdaptiveOutcome.FAILED, reason)
                progress = result.ok and self._accept_progress()
                if result.ok and result.tool is Tool.EVALUATE:
                    # Count new authoritative evaluation feedback, not another
                    # timestamp/revision of the same evaluated input identity.
                    new_evaluation = self._budget.remember_evaluation(identity)
                    progress = progress or new_evaluation
                self._budget.record_progress_outcome(progress=progress)
                record()
                if self._budget.no_progress >= self._budget.limits.max_no_progress:
                    return finish(AdaptiveOutcome.NO_PROGRESS, Reason.NO_PROGRESS)
        except ToolRejected:
            return finish(AdaptiveOutcome.BUDGET_EXHAUSTED if self._budget.snapshot.exhausted
                          else AdaptiveOutcome.FAILED, Reason.BUDGET if self._budget.snapshot.exhausted else Reason.BOUNDARY)
        except Exception:
            return finish(AdaptiveOutcome.FAILED, Reason.BOUNDARY)
        finally:
            if control is not None:
                self._registry.release_control(control)
            self._budget.release_run()
