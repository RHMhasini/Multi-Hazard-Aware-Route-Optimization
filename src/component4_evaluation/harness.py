"""Real C4 controller evaluation with bounded, deliberately projected results.

No core controller/ranker/verifier changes, automatic persistence, raw response
retention, or live requests on import. A new run always creates a fresh fixture,
authority, registry and shared execution ledger. Observer events are reports of
real observations/proposals; final acceptance comes from the controller trace.
"""

from dataclasses import asdict, dataclass, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import os
import subprocess
import tempfile
from time import monotonic
from uuid import uuid4

from src.agentic_orchestration.adaptive_contracts import Tool
from src.agentic_orchestration.adaptive_controller import AdaptiveController
from src.agentic_orchestration.adaptive_planner import AdaptiveLLMPlanner, PROMPT_SHA256
from src.agentic_orchestration.execution import AdaptiveExecutionBudget, AdaptiveLimits, HostExecutionBudget
from src.agentic_orchestration.groq_client import GROQ_MODEL_ID, GroqModelClient
from src.agentic_orchestration.llm_planner import (
    ADAPTIVE_ACTION_VERSION, ADAPTIVE_DECODER_VERSION, ADAPTIVE_PROMPT_VERSION, GenerationSettings,
)
from src.agentic_orchestration.rule_planner import ActionKind, AdaptiveAction, Reason, RulePlanner
from src.agentic_orchestration.tool_registry import FailureKind
from src.agentic_orchestration.synthetic_tools import Fault
from .fixtures import NOW, environment, fixture
from .oracle import assess


VERSION = "c4.evaluation/1.0.0"
SCENARIOS = {
    "normal": ("A | Missing graph and hazard", "Verified recommendation after both evidence preparations."),
    "network_missing": ("B | Reuse fresh hazard; graph missing", "Repair the graph without recomputing usable hazard evidence."),
    "contradictory_output": ("C | Reject contradictory tool output", "Existing generation fault must be rejected; no recommendation."),
    "no_feasible": ("D | No eligible supplied candidates", "Existing zero-hazard-limit test: evaluate, reject and withhold recommendation."),
}
LIMITATIONS = (
    "Synthetic four-node keyed-edge fixture, not real Sabaragamuwa roads or emergency data.",
    "Hazard values are synthetic edge samples, not calibrated probabilities or live forecasts.",
    "Topology layout is schematic; fixture has no geographical coordinates or edge geometry.",
    "No C1-C3 integration, human continuation, field dispatch or M5.1 execution restoration.",
    "One trial cannot establish reliability or LLM superiority; repeated live trials need separate authorization.",
    "Synchronous deadlines are cooperative; operating-system/network blocking may overrun.",
    "Non-useful calls are measured against host eligibility, not global plan optimality.",
)
REPOSITORY = Path(__file__).resolve().parents[2]


class EvaluationError(ValueError):
    """Fixed public category; never attach provider messages or observations."""


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: str
    run_id: str
    session_scope: str
    scenario_id: str
    expected_outcome: str
    source: str
    planner_id: str
    comparison_key: str
    versions: dict
    initial_evidence: dict
    final_evidence: dict
    actual_outcome: str
    terminal_reason: str
    assessment: dict
    metrics: dict
    decisions: tuple[dict, ...]
    model_diagnostics: tuple[dict, ...]
    graph: dict
    mission: dict
    limitations: tuple[str, ...] = LIMITATIONS


def _evidence(state):
    def summary(value):
        return {"availability": value.availability.value, "freshness": value.freshness.value,
                "coverage": value.coverage.value, "verification": value.verification.value,
                "snapshot": value.snapshot.snapshot_id if value.snapshot else None}
    return {"revision": state.state_revision, "graph": summary(state.graph), "hazard": summary(state.hazard),
            "alternatives": state.alternatives.availability.value,
            "alternatives_verification": state.alternatives.verification.value}


def _emit(observer, event):
    if observer is not None:
        try:
            # Detach reports so a UI callback cannot mutate the planner's state.
            observer(json.loads(json.dumps(event, allow_nan=False)))
        except Exception:
            pass  # A display callback never changes mission execution or grants permissions.


class _ObservedPlanner:
    def __init__(self, delegate, observer):
        self.delegate, self.observer, self.observations = delegate, observer, []

    def uses_budget(self, budget):
        return self.delegate.uses_budget(budget) if hasattr(self.delegate, "uses_budget") else True

    def choose(self, observation):
        feedback = observation.feedback
        view = {"number": len(self.observations)+1, "evidence": _evidence(observation.state),
                "eligible": [o.tool.value for o in observation.options],
                "evaluation_ready": observation.evaluation_ready,
                "useful": {a.tool.value: a.useful for a in observation.eligibility.assessments},
                "previous_tool": feedback.tool.value if feedback else None,
                "previous_verified": feedback.ok if feedback else None,
                "previous_failure": feedback.failure.kind.value if feedback and feedback.failure else None}
        self.observations.append(view)
        _emit(self.observer, {"event": "observation", **view})
        action = self.delegate.choose(observation)
        # A proposal is not acceptance. Never expose arbitrary invalid fields.
        if type(action) is AdaptiveAction and type(action.kind) is ActionKind:
            tool = action.option.tool if action.kind is ActionKind.CALL and action.option else None
            _emit(self.observer, {"event": "proposal", "number": view["number"],
                                 "action": action.kind.value,
                                 "tool": tool.value if type(tool) is Tool else None,
                                 "status": "pending_host_validation"})
        return action


def _make_planner(mode, budget):
    if mode == "rule":
        return RulePlanner()
    return AdaptiveLLMPlanner(GroqModelClient(), budget=budget,
                              settings=GenerationSettings(0, 2048, None))


def _versions():
    commit, dirty = None, None
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY,
                                capture_output=True, text=True, timeout=2)
        value = result.stdout.strip()
        if result.returncode == 0 and len(value) == 40 and all(c in "0123456789abcdef" for c in value):
            commit = value
        result = subprocess.run(["git", "status", "--porcelain"], cwd=REPOSITORY,
                                capture_output=True, text=True, timeout=2)
        dirty = bool(result.stdout) if result.returncode == 0 else None
    except Exception:
        pass
    return {"application": VERSION, "fixture": "c4.tested-synthetic-fixture/1",
            "commit": commit, "working_tree_dirty": dirty, "prompt": ADAPTIVE_PROMPT_VERSION,
            "action_schema": ADAPTIVE_ACTION_VERSION, "decoder": ADAPTIVE_DECODER_VERSION,
            "prompt_sha256": PROMPT_SHA256, "tool_version": "m2.synthetic/1"}


def run_evaluation(scenario_id="normal", *, mode="rule", authorize_live=False, observer=None):
    """One independent run, at most five model decisions/calls, never retried.

    authorize_live must be exactly True for Groq. The desktop asks explicitly for
    each run. No model client is constructed in offline mode. No fallback exists.
    """
    if scenario_id not in SCENARIOS or mode not in ("rule", "groq"):
        raise EvaluationError("unsupported_evaluation_selection")
    if mode == "groq" and authorize_live is not True:
        raise EvaluationError("live_run_requires_explicit_authorization")
    failed = False
    try:
        return _run(scenario_id, mode, observer)
    except Exception:
        failed = True
    if failed:
        raise EvaluationError("evaluation_unavailable") from None


def _run(scenario_id, mode, observer):
    data = fixture()
    if scenario_id == "no_feasible":
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_edge_hazard=0)))
    settings = {
        "normal": dict(graph=None, hazard=None),
        "network_missing": dict(graph=None),
        "contradictory_output": dict(faults=(Fault(Tool.GENERATE, 1, FailureKind.CONTRADICTORY),)),
        "no_feasible": {},
    }[scenario_id]
    registry, tools, authority = environment(data=data, **settings)
    initial = _evidence(registry.state)
    run_id, scope = "eval-" + uuid4().hex, "eval-scope-" + uuid4().hex
    limits = AdaptiveLimits(5, 4, 0, 0, 1)
    key = hashlib.sha256(json.dumps({"scenario": scenario_id,
        "fixture_registration": registry.checkpoint_configuration,
        "initial_evidence": initial, "limits": asdict(limits), "application": VERSION},
        sort_keys=True).encode()).hexdigest()
    start = monotonic()

    class Clock:
        def now(self):
            return NOW + timedelta(seconds=monotonic()-start)

    host = HostExecutionBudget(scope, 3, 120, clock=monotonic,
                               max_model_calls=5, max_model_output_bytes=4096)
    budget = AdaptiveExecutionBudget(host, limits)
    delegate = _make_planner(mode, budget)
    planner = _ObservedPlanner(delegate, observer)
    trace = AdaptiveController(registry, budget, planner, clock=Clock()).run(run_id)
    duration = monotonic()-start
    records = registry.records
    assessment = assess(data, scenario_id, trace, records, registry.state)
    observations = planner.observations
    decisions = []
    for index, step in enumerate(trace.steps):
        before = observations[index] if index < len(observations) else None
        following = observations[index+1]["evidence"] if index+1 < len(observations) else _evidence(registry.state)
        feedback = step.feedback
        accepted = bool(step.action is not None and step.reason not in (
            Reason.INVALID_ACTION, Reason.BUDGET, Reason.BOUNDARY, Reason.INTERFERENCE))
        changed = None
        if index and before:
            previous = observations[index-1]
            old_evidence = {k: v for k, v in previous["evidence"].items() if k != "revision"}
            new_evidence = {k: v for k, v in before["evidence"].items() if k != "revision"}
            changed = bool(accepted and (old_evidence != new_evidence
                           or before["evaluation_ready"] != previous["evaluation_ready"]))
        decisions.append({"number": index+1, "decision_requested": before is not None,
            "before": before["evidence"] if before else None,
            "eligible_tools": list(step.dispatchable_tools[i].value for i in range(len(step.dispatchable_tools))),
            "action": step.action.kind.value if step.action else None,
            "tool": step.action.option.tool.value if step.action and step.action.option else None,
            "host_accepted": accepted, "executed": feedback.executed if feedback else None,
            "verified": feedback.ok if feedback else None,
            "failure": feedback.failure.kind.value if feedback and feedback.failure else None,
            "reason": step.reason.value, "after": following,
            "changed_dependencies": list(feedback.changed_dependencies) if feedback else [],
            "responded_to_changed_evidence": changed,
        })
    model_records = delegate.records if type(delegate) is AdaptiveLLMPlanner else ()
    diagnostics = tuple({
        "decision_call": r.model_calls, "category": r.category, "http_status": r.http_status,
        "error_category": r.error_category, "provider_error_code": r.provider_error_code,
        "finish_reason": r.response_diagnostics.finish_reason if r.response_diagnostics else None,
        "content_shape": r.response_diagnostics.content_shape if r.response_diagnostics else None,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "latency_sec": r.latency_sec,
    } for r in model_records)

    def tokens(field):
        if not model_records or any(getattr(r, field) is None for r in model_records):
            return None
        return sum(getattr(r, field) for r in model_records)

    metrics = {
        "decisions": len(observations), "accepted_actions": sum(d["host_accepted"] for d in decisions),
        "valid_action_rate": sum(d["host_accepted"] for d in decisions)/len(observations) if observations else None,
        "tool_attempts": len(records), "tool_executions": sum(r.executed for r in records),
        "independently_verified_tools": sum(r.ok for r in records),
        "rejected_tool_attempts": sum(r.failure is not None for r in records),
        "invalid_actions_blocked": sum(s.reason is Reason.INVALID_ACTION for s in trace.steps),
        "unauthorized_tool_actions_blocked": sum(r.failure is not None and r.failure.kind in (
            FailureKind.UNAUTHORIZED, FailureKind.FORGED_OPTION) for r in records),
        "non_useful_tool_executions": sum(d["executed"] is True and d["tool"] is not None
            and not observations[i]["useful"].get(d["tool"], False) for i, d in enumerate(decisions)
            if i < len(observations)),
        "model_calls": trace.resources.host.model_calls, "provider_operations": trace.resources.host.provider_calls,
        "input_tokens": tokens("input_tokens"), "output_tokens": tokens("output_tokens"),
        "duration_sec": duration, "failed_constraint_checks": assessment["failed_constraint_checks"],
        "released_constraint_violations": assessment["released_constraint_violations"],
        "reliability_across_repeated_live_trials": None,
    }
    graph = data.graph(registry.state.graph.snapshot.snapshot_id) if registry.state.graph.snapshot else None
    hazard = data.hazard(registry.state.hazard.snapshot.snapshot_id) if registry.state.hazard.snapshot else None
    hazards = {sample.edge: sample.value for sample in hazard.samples} if hazard else {}
    # Fixture topology is approved synthetic data, not a projection of model text.
    topology = {"snapshot": graph.snapshot.snapshot_id if graph else None, "geographical_coordinates": False,
        "nodes": sorted({n for e in graph.edges for n in (e.identity.u, e.identity.v)}) if graph else [],
        "edges": [{"u": e.identity.u, "v": e.identity.v, "key": e.identity.key,
                   "distance_m": e.length_m, "travel_time_sec": e.travel_time_sec,
                   "hazard_value": hazards.get(e.identity),
                   "exposure_haz_m": e.length_m * hazards[e.identity]
                       if hazards.get(e.identity) is not None else None} for e in graph.edges] if graph else []}
    result = EvaluationResult(VERSION, run_id, scope, scenario_id, SCENARIOS[scenario_id][1],
        "offline_rule" if mode == "rule" else "live_groq", "RulePlanner" if mode == "rule" else GROQ_MODEL_ID,
        key, _versions(), initial, _evidence(registry.state), trace.outcome.value, trace.reason.value,
        assessment, metrics, tuple(decisions), diagnostics, topology,
        {"origin": data.origin, "destination": data.destination, "vehicle": data.request.vehicle_type,
         "constraints": asdict(data.request.hard_constraints),
         "time_weight": data.ranking_policy.time_weight, "exposure_weight": data.ranking_policy.exposure_weight,
         "limits": asdict(limits), "duration_limit_sec": 120, "provider_limit": 3, "model_call_limit": 5})
    _emit(observer, {"event": "finished", "outcome": result.actual_outcome, "verdict": assessment["verdict"]})
    return result


def compare_runs(left, right):
    """Compare actual paired runs only. No generated or historical baseline."""
    if (type(left) is not EvaluationResult or type(right) is not EvaluationResult
            or left.comparison_key != right.comparison_key or left.source == right.source
            or left.versions != right.versions
            or {left.source, right.source} != {"offline_rule", "live_groq"}):
        return {"status": "NOT TESTED", "reason": "Actual equivalent offline and live runs required."}
    return {"status": "AVAILABLE", "scenario": left.scenario_id,
            "runs": [{"source": r.source, "run_id": r.run_id, "verdict": r.assessment["verdict"],
                      "metrics": r.metrics} for r in (left, right)],
            "limitation": "Descriptive paired observations only; no superiority or reliability inference."}


def export_result(result, path):
    """Explicit atomic JSON export to D:, outside Git; never serialize runtime objects."""
    temporary = None
    failed = False
    try:
        if type(result) is not EvaluationResult:
            raise ValueError()
        target = Path(path)
        if not target.is_absolute():
            raise ValueError()
        target = target.resolve()
        if (target.drive.lower() != "d:" or target.is_relative_to(REPOSITORY)
                or target.suffix.lower() != ".json" or not target.parent.is_dir()):
            raise ValueError()
        # Only the projection is retained: no controller trace, observations,
        # model replies, provider exceptions, authority caches or credentials.
        data = json.dumps(asdict(result), indent=2, ensure_ascii=True, allow_nan=False)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix="c4-export-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        failed = True
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                failed = True
    if failed:
        raise EvaluationError("evaluation_export_failed") from None
