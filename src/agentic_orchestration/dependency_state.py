"""Pure M1 eligibility, not planning, tool execution or evidence authentication.

Only host-supplied summaries are assessed. Useful means addresses a current unmet
dependency (or evaluates compatible alternatives), not empirically optimal. All
technically permitted tools appear in assessments; only permitted AND useful tools
appear in options. Required dependencies do not impose a preparation order.
"""

from .adaptive_contracts import (
    Availability, Coverage, Dependency, DependencyState, EligibilityReport,
    EvidenceStatus, Freshness, MissionPurpose, SnapshotBinding, SourceClass,
    Tool, ToolAssessment, ToolOption,
)
from .contracts import VerificationStatus


def _usable(evidence: EvidenceStatus, allowed: frozenset[SourceClass], purpose: MissionPurpose) -> bool:
    return (
        evidence.availability is Availability.AVAILABLE
        and evidence.freshness is Freshness.FRESH
        and evidence.coverage is Coverage.COMPLETE
        and evidence.verification is VerificationStatus.VERIFIED
        and evidence.snapshot.source_class in allowed
        and (evidence.snapshot.source_class is not SourceClass.SYNTHETIC
             or purpose is MissionPurpose.SYNTHETIC_RESEARCH)
    )


def _matches(binding: SnapshotBinding | None, state: DependencyState) -> bool:
    return (
        binding is not None and state.od_ref is not None
        and state.graph.snapshot is not None and state.hazard.snapshot is not None
        and (binding.mission_ref, binding.policy_ref, binding.od_ref,
             binding.graph_snapshot_id, binding.hazard_snapshot_id)
        == (state.mission_ref, state.policy_ref, state.od_ref,
            state.graph.snapshot.snapshot_id, state.hazard.snapshot.snapshot_id)
    )


def derive_eligibility(state: DependencyState) -> EligibilityReport:
    """Return stable revision-bound options; read no clock, files or services.

    Revalidate even frozen input records (in-process Python can bypass freezing).
    Unknown freshness remains blocked even if timestamps alone look recent.
    Build time is never used as observation time. No v1 contracts are modified.
    """
    if type(state) is not DependencyState:
        raise ValueError("Expected DependencyState")
    state.__post_init__()  # Validation only: constructors/validators do not mutate.
    graph_ok = _usable(state.graph, state.policy.graph_source_classes, state.purpose)
    hazard_ok = _usable(state.hazard, state.policy.hazard_source_classes, state.purpose)
    alternatives_ok = (
        state.alternatives.availability is Availability.AVAILABLE
        and state.alternatives.verification is VerificationStatus.VERIFIED
        and _matches(state.alternatives.binding, state)
    )
    context_ok = (
        state.routing_context is not None
        and state.routing_context.verification is VerificationStatus.VERIFIED
        and _matches(state.routing_context.binding, state)
    )
    capabilities = {cap.tool: cap for cap in state.capabilities}
    assessments = []
    options = []
    for tool in Tool:
        cap = capabilities.get(tool)
        blockers = []
        if cap is None or not cap.supported:
            blockers.append("unsupported_tool")
        if cap is not None:
            if not cap.authorized:
                blockers.append("tool_not_authorized")
            if not cap.inputs_ready:
                blockers.append("tool_inputs_not_ready")
        if tool in (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK):
            sources = (state.policy.hazard_source_classes if tool is Tool.PREPARE_HAZARD
                       else state.policy.graph_source_classes)
            if not any(source is not SourceClass.SYNTHETIC
                       or state.purpose is MissionPurpose.SYNTHETIC_RESEARCH for source in sources):
                blockers.append("no_approved_source_class")
        if tool in (Tool.GENERATE, Tool.EVALUATE):
            if not graph_ok:
                blockers.append("graph_evidence_not_usable")
            if not hazard_ok:
                blockers.append("hazard_evidence_not_usable")
            if state.od_ref is None:
                blockers.append("od_reference_missing")
        if tool is Tool.GENERATE and not context_ok:
            blockers.append("routing_context_unverified_or_mismatched")
        if tool is Tool.EVALUATE and not alternatives_ok:
            blockers.append("alternatives_unverified_or_mismatched")
        useful = {
            Tool.PREPARE_HAZARD: not hazard_ok,
            Tool.PREPARE_NETWORK: not graph_ok,
            Tool.GENERATE: not alternatives_ok,
            Tool.EVALUATE: alternatives_ok,
        }[tool]
        permitted = not blockers
        assessments.append(ToolAssessment(tool, permitted, useful, tuple(blockers)))
        if permitted and useful:
            options.append(ToolOption(tool, state.mission_ref, state.policy_ref, state.state_revision))

    eligible = {option.tool for option in options}
    required = []
    blocked = []
    for dependency, ready, repair in (
        (Dependency.GRAPH, graph_ok, Tool.PREPARE_NETWORK),
        (Dependency.HAZARD, hazard_ok, Tool.PREPARE_HAZARD),
    ):
        if not ready:
            required.append(dependency)
            if repair not in eligible:
                blocked.append(dependency)
    if not alternatives_ok:
        required.append(Dependency.ALTERNATIVES)
        if not context_ok:
            required.append(Dependency.ROUTING_CONTEXT)
            # Preparing parents changes the binding; assess routing context again
            # afterward rather than request premature human input for old parents.
            if graph_ok and hazard_ok:
                blocked.append(Dependency.ROUTING_CONTEXT)
        if graph_ok and hazard_ok and context_ok and Tool.GENERATE not in eligible:
            blocked.append(Dependency.ALTERNATIVES)
    return EligibilityReport(tuple(options), tuple(assessments), tuple(required), tuple(blocked))
