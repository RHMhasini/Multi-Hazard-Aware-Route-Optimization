"""Controller boundary tests with independently held SYNTHETIC authority records."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from src.agentic_orchestration import (
    ActionKind, ClarificationTopic, ControllerOutcome, ControllerState,
    DependencyIdentity, EvaluationMode, HostExecutionBudget, PlannerAction,
    ProviderError, RetryPolicy, RunOutcome, ScriptedPlanner, TransientProviderError,
    replay_evaluation, run_controller,
)
from test_orchestration import (
    ScriptedProvider, SyntheticAuthority, alternatives, dependencies, host_budget,
)
from test_ranking import candidate, policy
from test_validation import NOW, mission


class ProposalScript:
    def __init__(self, *actions):
        self.actions = actions
        self.observations = []

    def propose(self, observation):
        self.observations.append(observation)
        action = self.actions[len(self.observations) - 1]
        if isinstance(action, BaseException):
            raise action
        return action


def run(provider, planner=None, *, request=None, ranking_policy=None, budget=None,
        authority=None, run_id="controller-test", **kwargs):
    authority = SyntheticAuthority() if authority is None else authority
    return run_controller(
        run_id, mission() if request is None else request,
        policy() if ranking_policy is None else ranking_policy,
        provider, ScriptedPlanner() if planner is None else planner,
        planner_identity=DependencyIdentity("synthetic-scripted-planner", "1.0.0"),
        as_of=NOW, budget=host_budget() if budget is None else budget,
        dependencies=dependencies(kwargs.get("clock") is not None),
        verifier=authority, metric_verifier=authority, **kwargs,
    )


def provider():
    return ScriptedProvider(alternatives(candidate("fast", 100, 100), candidate("safe", 300, 0)))


class ControllerTests(unittest.TestCase):
    def test_scripted_selection_only_after_acquire_and_evaluate(self):
        source = provider()
        trace = run(source)
        self.assertEqual(trace.schema_version, "c4.agent-controller/1.0.0")
        self.assertEqual(trace.outcome, ControllerOutcome.SELECTED)
        self.assertEqual(trace.selected_route_id, "fast")
        self.assertEqual(len(source.calls), 1)
        self.assertEqual([a.proposed.kind for a in trace.actions],
                         [ActionKind.ACQUIRE.value, ActionKind.EVALUATE.value])
        self.assertTrue(all(a.accepted for a in trace.actions))
        self.assertEqual(trace.actions[0].tool_result.outcome, RunOutcome.ALTERNATIVES_AVAILABLE)
        self.assertIsNone(trace.actions[0].tool_result.evaluation)
        self.assertIsNone(trace.actions[0].tool_result.selected_route_id)
        self.assertEqual(trace.actions[1].tool_result.provider_calls, ())
        self.assertEqual(trace.budget_checks[-1].provider_calls, 1)
        self.assertEqual(trace.actions[1].observation.candidate_count, 2)
        self.assertEqual(trace.actions[1].next_state, ControllerState.FINISHED)
        replay = replay_evaluation(trace.evaluation, verifier=SyntheticAuthority(),
                                   metric_verifier=SyntheticAuthority())
        self.assertEqual(replay.ranking, trace.evaluation.evaluation.ranking)

    def test_missing_or_unverified_evidence_abstains_without_repair_or_retry(self):
        for changes in ({"hazard": None}, {"vehicle_evidence": None},
                        {"ranking_metric_evidence": None}):
            with self.subTest(changes=changes):
                source = ScriptedProvider(alternatives(replace(candidate("fast", 100, 100), **changes)))
                trace = run(source)
                self.assertEqual(trace.outcome, ControllerOutcome.INSUFFICIENT_DATA)
                self.assertIsNone(trace.selected_route_id)
                self.assertEqual(len(source.calls), 1)

    def test_missing_snapshot_prompts_clarification_without_acquisition(self):
        source = provider()
        trace = run(source, request=replace(mission(), expected_graph_snapshot_id=None))
        self.assertEqual(trace.outcome, ControllerOutcome.UNRESOLVED_INPUT)
        self.assertEqual(trace.actions[0].proposed.clarification, ClarificationTopic.GRAPH_SNAPSHOT.value)
        self.assertEqual(source.calls, [])
        self.assertIsNone(trace.selected_route_id)

    def test_explicit_clarification_and_stop_are_unresolved_not_selection(self):
        for action in (PlannerAction(ActionKind.CLARIFY, ClarificationTopic.HOST_REVIEW),
                       PlannerAction(ActionKind.STOP)):
            source = provider()
            trace = run(source, ProposalScript(action))
            self.assertEqual(trace.outcome, ControllerOutcome.UNRESOLVED_INPUT)
            self.assertTrue(trace.actions[0].accepted)
            self.assertEqual(source.calls, [])

    def test_invalid_actions_and_modification_payloads_are_rejected(self):
        for action in (
            {"kind": "acquire_alternatives", "hard_constraints": {"max_edge_hazard": 1}},
            {"kind": "evaluate_alternatives", "time_weight": 1, "exposure_weight": 0},
            {"kind": "select", "selected_route_id": "fast"},
            {"kind": "acquire_alternatives", "budget": "reset", "graph_snapshot_id": "other"},
            PlannerAction("select"), PlannerAction("acquire_alternatives"),
            PlannerAction(ActionKind.ACQUIRE, ClarificationTopic.HOST_REVIEW),
            PlannerAction(ActionKind.CLARIFY), object(), None,
        ):
            with self.subTest(action=type(action)):
                source = provider()
                trace = run(source, ProposalScript(action))
                self.assertEqual(trace.outcome, ControllerOutcome.INVALID_ACTION)
                self.assertFalse(trace.actions[0].accepted)
                self.assertEqual(source.calls, [])
                self.assertEqual(trace.request.hard_constraints, mission().hard_constraints)
                self.assertEqual(trace.policy, policy())
                self.assertIsNone(trace.selected_route_id)

    def test_evaluate_before_acquire_is_rejected(self):
        source = provider()
        trace = run(source, ProposalScript(PlannerAction(ActionKind.EVALUATE)))
        self.assertEqual(trace.outcome, ControllerOutcome.INVALID_ACTION)
        self.assertEqual(source.calls, [])

    def test_missing_input_cannot_be_bypassed_by_acquire_action(self):
        source = provider()
        trace = run(source, ProposalScript(PlannerAction(ActionKind.ACQUIRE)),
                    request=replace(mission(), expected_hazard_snapshot_id=None))
        self.assertEqual(trace.outcome, ControllerOutcome.INVALID_ACTION)
        self.assertEqual(source.calls, [])

    def test_repeated_acquire_stops_before_second_provider_call(self):
        source = provider()
        planner = ProposalScript(PlannerAction(ActionKind.ACQUIRE), PlannerAction(ActionKind.ACQUIRE))
        trace = run(source, planner)
        self.assertEqual(trace.outcome, ControllerOutcome.REPEATED_ACTION)
        self.assertEqual(len(source.calls), 1)
        self.assertFalse(trace.actions[-1].accepted)
        self.assertIsNone(trace.selected_route_id)

    def test_action_limit_does_not_implicitly_evaluate(self):
        trace = run(provider(), max_actions=1)
        self.assertEqual(trace.outcome, ControllerOutcome.ACTION_LIMIT)
        self.assertIsNone(trace.evaluation)
        self.assertIsNone(trace.selected_route_id)

    def test_provider_call_budget_is_shared_across_controller_runs(self):
        budget = HostExecutionBudget("session", 1, 60, clock=lambda: 0)
        first_source, second_source = provider(), provider()
        self.assertEqual(run(first_source, budget=budget).outcome, ControllerOutcome.SELECTED)
        trace = run(second_source, budget=budget, run_id="new-id")
        self.assertEqual(trace.outcome, ControllerOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(second_source.calls, [])
        self.assertIsNone(trace.selected_route_id)

    def test_planner_overrun_prevents_provider_call(self):
        elapsed = [0]
        class SlowPlanner:
            def propose(self, observation):
                elapsed[0] = 5
                return PlannerAction(ActionKind.ACQUIRE)
        source = provider()
        trace = run(source, SlowPlanner(), budget=HostExecutionBudget("session", 1, 5, clock=lambda: elapsed[0]))
        self.assertEqual(trace.outcome, ControllerOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(source.calls, [])
        self.assertFalse(trace.actions[0].accepted)

    def test_provider_overrun_prevents_evaluation(self):
        elapsed = [0]
        class SlowProvider:
            def get_alternatives(self, call):
                elapsed[0] = 5
                return alternatives(candidate("fast", 100, 100))
        trace = run(SlowProvider(), budget=HostExecutionBudget("session", 1, 5, clock=lambda: elapsed[0]))
        self.assertEqual(trace.outcome, ControllerOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(len(trace.actions), 1)
        self.assertIsNone(trace.evaluation)

    def test_planner_exception_is_audited_including_broken_formatting(self):
        class BrokenError(Exception):
            def __str__(self):
                raise RuntimeError("cannot format")
        for error in (RuntimeError("planner unavailable"), BrokenError()):
            source = provider()
            trace = run(source, ProposalScript(error))
            self.assertEqual(trace.outcome, ControllerOutcome.PLANNER_FAILURE)
            self.assertEqual(trace.actions[0].error.code, "planner_failed")
            self.assertEqual(source.calls, [])

    def test_process_termination_is_not_swallowed(self):
        with self.assertRaises(KeyboardInterrupt):
            run(provider(), ProposalScript(KeyboardInterrupt()))

    def test_snapshot_mismatch_cannot_be_repaired_by_planner(self):
        route = candidate("fast", 100, 100)
        route = replace(route, hazard=replace(route.hazard, hazard_snapshot_id="other"))
        trace = run(ScriptedProvider(alternatives(route)))
        self.assertEqual(trace.outcome, ControllerOutcome.INSUFFICIENT_DATA)
        self.assertEqual(trace.request.expected_hazard_snapshot_id, "hazard-1")
        self.assertIsNone(trace.selected_route_id)

    def test_planner_receives_no_host_safety_context_and_observation_is_frozen(self):
        planner = ProposalScript(PlannerAction(ActionKind.STOP))
        trace = run(provider(), planner)
        observation = planner.observations[0]
        for name in ("request", "policy", "budget", "verifier", "provider", "clock", "candidates"):
            self.assertFalse(hasattr(observation, name))
        with self.assertRaises(FrozenInstanceError):
            observation.state = ControllerState.FINISHED
        self.assertEqual(trace.actions[0].observation, observation)

    def test_input_and_invalid_action_mutation_do_not_change_trace(self):
        unsupported = {"kind": "evaluate", "time_weight": [1]}
        trace = run(provider(), ProposalScript(unsupported))
        recorded = asdict(trace)
        unsupported["time_weight"].append(0)
        self.assertEqual(asdict(trace), recorded)
        self.assertEqual(trace.actions[0].proposed.argument_names, ("kind", "time_weight"))

    def test_identical_host_observations_and_inputs_produce_identical_traces(self):
        self.assertEqual(run(provider()), run(provider()))

    def test_unavailable_budget_prevents_even_planning(self):
        class BrokenBudget:
            def checkpoint(self, **kwargs):
                raise RuntimeError("budget unavailable")
        planner, source = ProposalScript(), provider()
        trace = run(source, planner, budget=BrokenBudget())
        self.assertEqual(trace.outcome, ControllerOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(trace.error.code, "budget_unavailable")
        self.assertEqual(planner.observations, [])
        self.assertEqual(source.calls, [])

    def test_invalid_host_action_limits_are_rejected_before_planning(self):
        for limit in (0, -1, True, 1.5):
            planner, source = ProposalScript(), provider()
            with self.assertRaises(ValueError):
                run(source, planner, max_actions=limit)
            self.assertEqual(planner.observations, [])
            self.assertEqual(source.calls, [])

    def test_current_selection_uses_existing_completion_gate(self):
        class Clock:
            def now(self):
                return NOW
        trace = run(provider(), mode=EvaluationMode.CURRENT, clock=Clock())
        self.assertEqual(trace.outcome, ControllerOutcome.SELECTED)
        self.assertEqual(trace.evaluation.completed_at, NOW)
        self.assertEqual(len(trace.evaluation.completion_freshness), 2)

    def test_transient_retries_stay_inside_acquisition_and_shared_budget(self):
        source = ScriptedProvider(TransientProviderError("retry"), alternatives(candidate("fast", 100, 100)))
        trace = run(source, retry_policy=RetryPolicy(2))
        self.assertEqual(trace.outcome, ControllerOutcome.SELECTED)
        self.assertEqual(len(trace.actions), 2)
        self.assertEqual(trace.budget_checks[-1].provider_calls, 2)
        self.assertEqual(len(trace.actions[0].tool_result.provider_calls), 2)

    def test_permanent_malformed_and_exhausted_acquisition_are_terminal(self):
        for response, expected in (
            (ProviderError("no"), ControllerOutcome.PROVIDER_FAILURE),
            ({"candidates": []}, ControllerOutcome.PROVIDER_FAILURE),
            (TransientProviderError("retry"), ControllerOutcome.RETRY_EXHAUSTED),
        ):
            source = ScriptedProvider(response)
            trace = run(source, retry_policy=RetryPolicy(1))
            self.assertEqual(trace.outcome, expected)
            self.assertEqual(len(trace.actions), 1)
            self.assertIsNone(trace.selected_route_id)

    def test_no_eligible_and_duplicate_ids_preserve_gate_behavior(self):
        for response, expected in (
            (alternatives(), ControllerOutcome.NO_ELIGIBLE_CANDIDATES),
            (alternatives(candidate("fast", 901, 100)), ControllerOutcome.NO_ELIGIBLE_CANDIDATES),
            (alternatives(candidate("fast"), candidate("fast")), ControllerOutcome.INVALID_INPUT),
        ):
            trace = run(ScriptedProvider(response))
            self.assertEqual(trace.outcome, expected)
            self.assertIsNone(trace.selected_route_id)

    def test_current_time_is_checked_after_acquisition_and_planner_delay(self):
        class Clock:
            value = NOW
            def now(self):
                return self.value
        clock = Clock()
        class DelayedPlanner(ScriptedPlanner):
            def propose(self, observation):
                if observation.candidate_count is not None:
                    clock.value = NOW + timedelta(seconds=60)  # exclusive expiry boundary
                return super().propose(observation)
        historical = run(provider(), DelayedPlanner())
        current = run(provider(), DelayedPlanner(), mode=EvaluationMode.CURRENT, clock=clock)
        self.assertEqual(historical.outcome, ControllerOutcome.SELECTED)
        self.assertEqual(current.outcome, ControllerOutcome.INSUFFICIENT_DATA)
        self.assertEqual(current.evaluation.evaluation.evaluated_at, clock.value)
        self.assertIsNone(current.selected_route_id)

    def test_invalid_policy_fails_before_planning_or_provider(self):
        planner, source = ProposalScript(), provider()
        with self.assertRaises(ValueError):
            run(source, planner, ranking_policy=policy(mission_type="other"))
        self.assertEqual(planner.observations, [])
        self.assertEqual(source.calls, [])

    def test_missing_authority_cannot_select(self):
        trace = run(provider(), authority=object())
        self.assertEqual(trace.outcome, ControllerOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)

    def test_unexpected_tool_exception_is_audited(self):
        with patch("src.agentic_orchestration.agent_controller.acquire_mission",
                   side_effect=RuntimeError("tool failed")):
            trace = run(provider())
        self.assertEqual(trace.outcome, ControllerOutcome.TOOL_FAILURE)
        self.assertEqual(trace.error.code, "controller_tool_failed")
        self.assertIsNone(trace.selected_route_id)

    def test_unexpected_ranking_exception_is_terminal(self):
        with patch("src.agentic_orchestration.orchestration.rank_candidates",
                   side_effect=RuntimeError("rank failed")):
            trace = run(provider())
        self.assertEqual(trace.outcome, ControllerOutcome.EVALUATION_FAILURE)
        self.assertEqual(trace.evaluation.evaluation.error.code, "evaluation_failed")
        self.assertIsNone(trace.selected_route_id)


if __name__ == "__main__":
    unittest.main()
