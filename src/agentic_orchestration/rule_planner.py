"""M3 deterministic next-action choice, separate from dispatch and authority.

Preparation preference breaks a tie only when both independent repairs are
eligible. It is not a fixed component workflow. No model or tools are called.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .adaptive_contracts import DependencyState, EligibilityReport, Tool, ToolOption
from .contracts import DecisionStatus
from .execution import AdaptiveBudgetSnapshot
from .ranking import RankingStatus
from .tool_registry import ToolFailure


class ActionKind(str, Enum):
    CALL = "call_tool"
    COMPLETE = "complete"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"


class Reason(str, Enum):
    REUSE_EVALUATION = "reuse_verified_evaluation"
    EVALUATE = "evaluate_compatible_alternatives"
    GENERATE = "generate_missing_alternatives"
    HAZARD = "repair_hazard_dependency"
    NETWORK = "repair_network_dependency"
    RETRY = "explicit_transient_recovery"
    MISSING_DEPENDENCY = "missing_or_unsupported_dependency"
    EVIDENCE_REVIEW = "evidence_requires_review"
    NO_FEASIBLE = "no_eligible_supplied_candidates"
    NO_ACTION = "no_useful_authorized_action"
    INVALID_ACTION = "invalid_planner_action"
    NO_PROGRESS = "no_meaningful_progress"
    BUDGET = "execution_budget_exhausted"
    BUSY = "execution_scope_busy"
    BOUNDARY = "host_boundary_failure"
    INTERFERENCE = "unexpected_registry_dispatch"


@dataclass(frozen=True, slots=True)
class ToolFeedback:
    call_id: str
    tool: Tool
    ok: bool
    executed: bool
    input_identity: str
    recovery: bool
    failure: ToolFailure | None
    evaluation_status: RankingStatus | None
    reason_codes: tuple[str, ...]
    changed_dependencies: tuple[str, ...]
    decision_status: DecisionStatus | None = None


@dataclass(frozen=True, slots=True)
class AdaptiveObservation:
    state: DependencyState
    eligibility: EligibilityReport
    options: tuple[ToolOption, ...]
    retry_tools: tuple[Tool, ...]
    evaluation_ready: bool
    feedback: ToolFeedback | None
    no_progress: int
    resources: AdaptiveBudgetSnapshot


@dataclass(frozen=True, slots=True)
class AdaptiveAction:
    kind: ActionKind
    reason: Reason
    option: ToolOption | None = None

    def __post_init__(self):
        if type(self.kind) is not ActionKind or type(self.reason) is not Reason:
            raise ValueError("Expected closed action and reason enums")
        if ((self.kind is ActionKind.CALL and type(self.option) is not ToolOption)
                or (self.kind is not ActionKind.CALL and self.option is not None)):
            raise ValueError("Invalid adaptive action branch")
        if self.option is not None:
            self.option.__post_init__()


class AdaptivePlanner(Protocol):
    def choose(self, observation: AdaptiveObservation) -> AdaptiveAction: ...


class RulePlanner:
    def __init__(self, *, preparation_first: Tool = Tool.PREPARE_HAZARD):
        if preparation_first not in (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK) or type(preparation_first) is not Tool:
            raise ValueError("Choose a supported preparation tie preference")
        self._first = preparation_first

    def choose(self, observation: AdaptiveObservation) -> AdaptiveAction:
        if observation.evaluation_ready:
            return AdaptiveAction(ActionKind.COMPLETE, Reason.REUSE_EVALUATION)
        preference = (Tool.EVALUATE, Tool.GENERATE, self._first,
                      Tool.PREPARE_NETWORK if self._first is Tool.PREPARE_HAZARD else Tool.PREPARE_HAZARD)
        for tool in preference:
            option = next((o for o in observation.options if o.tool is tool), None)
            if option is not None:
                reason = Reason.RETRY if tool in observation.retry_tools else {
                    Tool.EVALUATE: Reason.EVALUATE, Tool.GENERATE: Reason.GENERATE,
                    Tool.PREPARE_HAZARD: Reason.HAZARD, Tool.PREPARE_NETWORK: Reason.NETWORK,
                }[tool]
                return AdaptiveAction(ActionKind.CALL, reason, option)
        feedback = observation.feedback
        if feedback is not None:
            if feedback.decision_status is DecisionStatus.INSUFFICIENT_DATA:
                return AdaptiveAction(ActionKind.CLARIFY, Reason.EVIDENCE_REVIEW)
            if feedback.evaluation_status is RankingStatus.NO_ELIGIBLE_CANDIDATES:
                return AdaptiveAction(ActionKind.ABSTAIN, Reason.NO_FEASIBLE)
            if feedback.failure is not None or feedback.evaluation_status is RankingStatus.INSUFFICIENT_RANKING_DATA:
                return AdaptiveAction(ActionKind.CLARIFY, Reason.EVIDENCE_REVIEW)
        if observation.eligibility.clarification_required or observation.eligibility.required_dependencies:
            return AdaptiveAction(ActionKind.CLARIFY, Reason.MISSING_DEPENDENCY)
        return AdaptiveAction(ActionKind.ABSTAIN, Reason.NO_ACTION)
