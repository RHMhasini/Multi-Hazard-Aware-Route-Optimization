"""M3 synthetic feedback loops with explicit clocks and independent M2 truth."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import unittest

from src.agentic_orchestration.adaptive_contracts import (
    Availability, Coverage, EvidenceStatus, Tool, ToolCapability, ToolOption,
)
from src.agentic_orchestration.adaptive_controller import AdaptiveController, AdaptiveOutcome
from src.agentic_orchestration.execution import AdaptiveExecutionBudget, AdaptiveLimits, HostExecutionBudget
from src.agentic_orchestration.rule_planner import ActionKind, AdaptiveAction, Reason, RulePlanner
from src.agentic_orchestration.synthetic_tools import Fault
from src.agentic_orchestration.tool_registry import FailureKind, ToolRejected
from synthetic_oracles import NOW, dispatch, enumerate_truth, environment, fixture


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return NOW + timedelta(seconds=self.value)

    def monotonic(self):
        return self.value


def ledger(clock, *, providers=12, duration=120, steps=20, tools=12, recoveries=4,
           per_input=1, no_progress=2, models=0):
    host = HostExecutionBudget("synthetic-adaptive-budget", providers, duration,
                               clock=clock.monotonic, max_model_calls=models)
    return AdaptiveExecutionBudget(host, AdaptiveLimits(steps, tools, recoveries, per_input, no_progress))


def actions(trace):
    return tuple(step.action.option.tool for step in trace.steps
                 if step.action is not None and step.action.kind is ActionKind.CALL)


def controller(registry, *, clock=None, budget=None, planner=None, event=None):
    clock = clock or Clock()
    budget = budget or ledger(clock)
    return AdaptiveController(registry, budget, planner or RulePlanner(), clock=clock,
                              host_event=event), budget, clock


class AdaptivePaths(unittest.TestCase):
    def test_three_contrasting_sequences_have_independently_expected_actions(self):
        cases = (({}, (Tool.GENERATE, Tool.EVALUATE)),
                 ({"hazard": None}, (Tool.PREPARE_HAZARD, Tool.GENERATE, Tool.EVALUATE)),
                 ({"graph": "graph-old"}, (Tool.PREPARE_NETWORK, Tool.GENERATE, Tool.EVALUATE)))
        for settings, expected in cases:
            with self.subTest(settings=settings):
                registry, tools, _ = environment(**settings)
                runner, budget, _ = controller(registry)
                trace = runner.run("mission-run")
                self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
                self.assertEqual(actions(trace), expected)
                self.assertEqual(tuple(t for t, _ in tools.calls), expected)
                self.assertEqual(trace.selected_route_id, "route-1")
                self.assertEqual(trace.resources.tool_attempts, len(expected))
                self.assertEqual(trace.resources.host.provider_calls, len(expected) - 1)
                self.assertEqual(trace.resources.host.model_calls, 0)

    def test_A_reuses_preexisting_verified_evaluation_without_tool_calls(self):
        registry, tools, _ = environment()
        dispatch(registry, Tool.GENERATE)
        evaluated = dispatch(registry, Tool.EVALUATE)
        count = len(tools.calls)
        runner, _, _ = controller(registry)
        trace = runner.run("reuse")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(actions(trace), ())
        self.assertEqual(len(tools.calls), count)
        self.assertEqual(trace.ranking, evaluated.update.payload)
        self.assertEqual(trace.resources.tool_attempts, 0)

    def test_verified_alternatives_are_evaluated_without_acquisition(self):
        registry, tools, _ = environment()
        dispatch(registry, Tool.GENERATE)
        runner, _, _ = controller(registry)
        trace = runner.run("reuse-candidates")
        self.assertEqual(actions(trace), (Tool.EVALUATE,))
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(trace.resources.host.provider_calls, 0)

    def test_D_both_preparation_orders_agree_with_independent_oracle(self):
        endings = []
        data = fixture()
        expected_metrics = enumerate_truth(data)
        # Independent fixture truth: fast path is time=20/exposure=60;
        # quiet path is 30/25; remaining path is 50/60. Under 0.75/0.25,
        # their normalized scores are 0.25, 0.25, 1; route-ID tie selects 1.
        self.assertEqual(sorted((v[1], v[2]) for v in expected_metrics.values()), [(20,60),(30,25),(50,60)])
        for first, second in ((Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK),
                              (Tool.PREPARE_NETWORK, Tool.PREPARE_HAZARD)):
            registry, tools, _ = environment(data=data, graph=None, hazard=None)
            runner, _, _ = controller(registry, planner=RulePlanner(preparation_first=first))
            trace = runner.run("both")
            self.assertEqual(actions(trace), (first, second, Tool.GENERATE, Tool.EVALUATE))
            self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
            self.assertEqual(trace.selected_route_id, "route-1")
            endings.append((registry.state, trace.ranking))
        self.assertEqual(endings[0], endings[1])

    def test_H_graph_change_invalidates_alternatives_and_replans(self):
        registry, tools, authority = environment()
        changed = False
        def event(state, step):
            nonlocal changed
            if state.alternatives.availability is Availability.AVAILABLE and not changed:
                changed = True
                new = replace(state, graph=authority.evidence(authority.fixture.graph("graph-2"), state),
                              state_revision=state.state_revision + 1)
                return authority.contextualize(new)
        runner, _, _ = controller(registry, event=event)
        trace = runner.run("changed-network")
        self.assertEqual(actions(trace), (Tool.GENERATE, Tool.GENERATE, Tool.EVALUATE))
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(trace.ranking.validation.expected_graph_snapshot_id, "graph-2")
        self.assertEqual(len(trace.ranking.ranked_routes), 2)
        self.assertEqual(len(tools.calls), 3)

    def test_hazard_change_invalidates_alternatives_without_network_preparation(self):
        data = fixture()
        hazard2 = replace(data.hazards[0], snapshot=replace(data.hazards[0].snapshot, snapshot_id="hazard-2"),
                          samples=tuple(replace(s, value=0.05) for s in data.hazards[0].samples))
        data = replace(data, hazards=data.hazards + (hazard2,))
        registry, _, authority = environment(data=data)
        changed = False
        def event(state, step):
            nonlocal changed
            if state.alternatives.availability is Availability.AVAILABLE and not changed:
                changed = True
                return authority.contextualize(replace(state, hazard=authority.evidence(hazard2, state),
                                                       state_revision=state.state_revision + 1))
        runner, _, _ = controller(registry, event=event)
        trace = runner.run("changed-hazard")
        self.assertEqual(actions(trace), (Tool.GENERATE, Tool.GENERATE, Tool.EVALUATE))
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(trace.ranking.validation.expected_hazard_snapshot_id, "hazard-2")

    def test_graph_change_after_evaluation_blocks_release_of_old_recommendation(self):
        registry, _, authority = environment()
        def event(state, step):
            if step == 2:
                graph = authority.fixture.graph("graph-2")
                return authority.contextualize(replace(state, graph=authority.evidence(graph, state),
                                                       state_revision=state.state_revision + 1))
        runner, _, _ = controller(registry, event=event)
        trace = runner.run("changed-before-release")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(actions(trace), (Tool.GENERATE, Tool.EVALUATE, Tool.GENERATE, Tool.EVALUATE))
        self.assertEqual(trace.ranking.validation.expected_graph_snapshot_id, "graph-2")
        self.assertEqual(trace.steps[2].host_before.graph_snapshot_id, "graph-1")
        self.assertEqual(trace.steps[2].before.graph_snapshot_id, "graph-2")

    def test_F_explicit_transient_recovery_is_one_visible_budgeted_action(self):
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),))
        runner, _, _ = controller(registry)
        trace = runner.run("retry")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(actions(trace), (Tool.GENERATE, Tool.GENERATE, Tool.EVALUATE))
        calls = [s for s in trace.steps if s.feedback]
        self.assertFalse(calls[0].feedback.recovery)
        self.assertTrue(calls[1].feedback.recovery)
        self.assertEqual(calls[1].action.reason, Reason.RETRY)
        self.assertEqual(calls[0].feedback.input_identity, calls[1].feedback.input_identity)
        self.assertEqual(trace.resources.recovery_attempts, 1)
        self.assertEqual(trace.resources.host.provider_calls, 2)
        self.assertEqual(len(tools.calls), 3)

    def test_G_permanent_failure_requests_review_without_repeating_operation(self):
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.PERMANENT),))
        runner, _, _ = controller(registry)
        trace = runner.run("permanent")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(actions(trace), (Tool.GENERATE,))
        self.assertEqual(len(tools.calls), 1)
        self.assertIsNone(trace.selected_route_id)

    def test_unsupported_invalid_and_contradictory_failures_have_no_hidden_recovery(self):
        for kind in (FailureKind.UNSUPPORTED, FailureKind.INVALID_OUTPUT, FailureKind.CONTRADICTORY):
            with self.subTest(kind=kind):
                registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, kind),))
                runner, _, _ = controller(registry)
                trace = runner.run("nonretryable")
                self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
                self.assertEqual(len(tools.calls), 1)
                self.assertEqual(trace.resources.recovery_attempts, 0)
                self.assertEqual(trace.steps[0].feedback.failure.kind, kind)

    def test_permanent_failure_does_not_prevent_other_independent_preparation(self):
        registry, tools, _ = environment(graph=None, hazard=None,
            faults=(Fault(Tool.PREPARE_HAZARD, 1, FailureKind.PERMANENT),))
        runner, _, _ = controller(registry)
        trace = runner.run("independent-path")
        self.assertEqual(actions(trace), (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK))
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(len(tools.calls), 2)

    def test_I_evaluation_corruption_returns_feedback_without_constraint_relaxation(self):
        registry, tools, _ = environment(faults=(Fault(Tool.EVALUATE, 1, FailureKind.CONTRADICTORY),))
        runner, _, _ = controller(registry)
        trace = runner.run("rejected-evaluation")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(trace.steps[-2].feedback.failure.code, "evaluation_gate_mismatch")
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(actions(trace), (Tool.GENERATE, Tool.EVALUATE))

    def test_no_eligible_candidates_abstain_without_relaxing_limits(self):
        data = fixture()
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_edge_hazard=0)))
        registry, tools, _ = environment(data=data)
        runner, _, _ = controller(registry)
        trace = runner.run("ineligible")
        self.assertEqual(trace.outcome, AdaptiveOutcome.ABSTAINED)
        self.assertEqual(trace.reason, Reason.NO_FEASIBLE)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(len(tools.calls), 2)
        self.assertIn("hazard_limit_exceeded", trace.steps[-2].feedback.reason_codes)
        self.assertEqual(data.request.hard_constraints.max_edge_hazard, 0)

    def test_missing_unsupported_evidence_requests_clarification(self):
        registry, tools, _ = environment(hazard=None, supported=frozenset({Tool.GENERATE, Tool.EVALUATE}))
        runner, _, _ = controller(registry)
        trace = runner.run("missing-evidence")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(actions(trace), ())
        self.assertEqual(tools.calls, ())

    def test_unknown_hazard_never_becomes_zero_or_success(self):
        data = fixture()
        hazard = data.hazards[0]
        data = replace(data, hazards=(replace(hazard, samples=(replace(hazard.samples[0], value=None),)
                                              + hazard.samples[1:]),))
        registry, tools, _ = environment(data=data, hazard=None)
        runner, _, _ = controller(registry)
        trace = runner.run("nodata")
        self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertNotIn(Tool.GENERATE, actions(trace))
        self.assertEqual(registry.state.hazard.coverage, Coverage.PARTIAL)
        self.assertLessEqual(len(tools.calls), 2)

    def test_deterministic_replay_includes_decisions_resources_and_feedback(self):
        traces = []
        for _ in range(2):
            registry, _, _ = environment(hazard=None,
                faults=(Fault(Tool.PREPARE_HAZARD, 1, FailureKind.TRANSIENT),))
            runner, _, _ = controller(registry)
            traces.append(runner.run("replay"))
        self.assertEqual(traces[0], traces[1])


class BudgetAndTimeTests(unittest.TestCase):
    def test_authoritative_preflight_failure_counts_attempt_but_no_provider_operation(self):
        from src.agentic_orchestration.adaptive_contracts import RoutingContext
        from src.agentic_orchestration.contracts import VerificationStatus
        from src.agentic_orchestration.synthetic_tools import current_binding
        data = fixture()
        data = replace(data, od_crs="EPSG:4326")
        registry, tools, _ = environment(data=data)
        state = registry.state
        fake = RoutingContext(current_binding(state), VerificationStatus.VERIFIED, "fake-spatial-check")
        registry.adopt_host_state(replace(state, routing_context=fake, state_revision=1))
        runner, _, _ = controller(registry)
        trace = runner.run("preflight-rejection")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(trace.resources.tool_attempts, 1)
        self.assertEqual(trace.resources.host.provider_calls, 0)
        self.assertEqual(tools.calls, ())
        self.assertEqual(trace.steps[0].feedback.failure.code, "coordinate_system_mismatch")

    def test_tool_provider_step_and_zero_recovery_limits_are_enforced(self):
        cases = (({"tools": 0}, {}), ({"providers": 0}, {}), ({"steps": 1}, {}),
                 ({"recoveries": 0}, {"faults": (Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),)}))
        for limits, setup in cases:
            with self.subTest(limits=limits):
                clock = Clock()
                budget = ledger(clock, **limits)
                registry, tools, _ = environment(**setup)
                runner, _, _ = controller(registry, clock=clock, budget=budget)
                trace = runner.run("limited")
                self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
                self.assertIsNone(trace.selected_route_id)
                self.assertLessEqual(trace.resources.tool_attempts, budget.limits.max_tool_attempts)
                self.assertLessEqual(trace.resources.host.provider_calls, trace.resources.host.max_provider_calls)
                if limits == {"providers": 0}:
                    self.assertEqual(tools.calls, ())
                    self.assertEqual(trace.resources.tool_attempts, 1)

    def test_budget_and_attempt_history_survive_new_controller_and_run_id(self):
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),))
        clock = Clock()
        budget = ledger(clock, steps=1)
        runner, _, _ = controller(registry, clock=clock, budget=budget)
        first = runner.run("first-id")
        self.assertEqual(first.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        second_runner, _, _ = controller(registry, clock=clock, budget=budget)
        second = second_runner.run("new-id")
        self.assertEqual(second.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(second.resources.tool_attempts, 1)
        self.assertEqual(len(tools.calls), 1)

    def test_unchanged_input_retry_allowance_is_not_reset_by_clock_revisions(self):
        clock = Clock()
        class AdvancingPlanner:
            def choose(self, obs):
                clock.value += 0.1
                return RulePlanner().choose(obs)
        registry, tools, _ = environment(
            faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),
                    Fault(Tool.GENERATE, 2, FailureKind.TRANSIENT)))
        runner, _, _ = controller(registry, clock=clock, planner=AdvancingPlanner())
        trace = runner.run("retry-ceiling")
        self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(len(tools.calls), 2)
        self.assertEqual(trace.resources.recovery_attempts, 1)
        calls = [s for s in trace.steps if s.feedback]
        self.assertNotEqual(calls[0].before.revision, calls[1].before.revision)
        self.assertEqual(calls[0].feedback.input_identity, calls[1].feedback.input_identity)

    def test_expired_deadline_before_operation_prevents_tool_call(self):
        clock = Clock()
        budget = ledger(clock, duration=1)
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, clock=clock, budget=budget)
        clock.value = 1
        trace = runner.run("expired")
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(tools.calls, ())

    def test_late_operation_cannot_adopt_output_or_release_recommendation(self):
        clock = Clock()
        def late(tool, state, claim):
            clock.value += 2
            return claim
        registry, tools, _ = environment(mutate=late)
        before = registry.state
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, duration=1))
        trace = runner.run("late")
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(registry.state, before)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(len(tools.calls), 1)  # Callback ran; it was not interrupted.

    def test_planner_delay_checks_deadline_without_provider_call(self):
        clock = Clock()
        class SlowPlanner:
            def choose(self, observation):
                clock.value = 10
                return RulePlanner().choose(observation)
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, duration=1), planner=SlowPlanner())
        trace = runner.run("slow")
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(len(trace.steps), 1)
        self.assertEqual(trace.steps[0].reason, Reason.BUDGET)
        self.assertEqual(tools.calls, ())

    def test_live_clock_advance_during_planning_does_not_invalidate_every_choice(self):
        clock = Clock()
        class TimedPlanner:
            def choose(self, observation):
                clock.value += 0.01
                return RulePlanner().choose(observation)
        registry, _, _ = environment()
        runner, _, _ = controller(registry, clock=clock, planner=TimedPlanner())
        trace = runner.run("advancing-clock")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)

    def test_stale_hazard_is_rechecked_before_reusing_old_evaluation(self):
        registry, tools, _ = environment()
        dispatch(registry, Tool.GENERATE)
        dispatch(registry, Tool.EVALUATE)
        clock = Clock()
        clock.value = 301
        runner, _, _ = controller(registry, clock=clock)
        trace = runner.run("expired-evidence")
        self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertIsNone(trace.selected_route_id)
        self.assertIn(Tool.PREPARE_HAZARD, actions(trace))

    def test_evaluation_overrun_is_rejected_even_when_earlier_tools_were_timely(self):
        clock = Clock()
        def late_evaluation(tool, state, claim):
            if tool is Tool.EVALUATE:
                clock.value = 2
            return claim
        registry, tools, _ = environment(mutate=late_evaluation)
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, duration=1))
        trace = runner.run("late-evaluation")
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertIsNone(trace.selected_route_id)
        self.assertFalse(registry.records[-1].ok)
        self.assertEqual(trace.resources.tool_attempts, 2)
        self.assertEqual(trace.resources.host.provider_calls, 1)
        self.assertEqual(len(tools.calls), 2)

    def test_completion_delay_cannot_reuse_evidence_past_stricter_v1_age_limit(self):
        data = fixture()
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_hazard_age_sec=40)))
        registry, tools, _ = environment(data=data)
        dispatch(registry, Tool.GENERATE)
        dispatch(registry, Tool.EVALUATE)
        clock = Clock()
        class DelayCompletion:
            def choose(self, observation):
                action = RulePlanner().choose(observation)
                if action.kind is ActionKind.COMPLETE:
                    clock.value = 20
                return action
        runner, _, _ = controller(registry, clock=clock, planner=DelayCompletion())
        trace = runner.run("delayed-release")
        self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertIsNone(trace.selected_route_id)

    def test_bad_evidence_clock_fails_closed_without_dispatch(self):
        class InvalidClock:
            def now(self):
                return NOW.replace(tzinfo=None)
        registry, tools, _ = environment()
        clock = Clock()
        runner, _, _ = controller(registry, clock=InvalidClock(), budget=ledger(clock))
        self.assertEqual(runner.run("bad-clock").outcome, AdaptiveOutcome.FAILED)
        self.assertEqual(tools.calls, ())

    def test_zero_retry_allowance_does_not_execute_recovery(self):
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),))
        clock = Clock()
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, per_input=0))
        trace = runner.run("no-retries")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(trace.resources.recovery_attempts, 0)

    def test_v1_stricter_hazard_age_prevents_stale_cached_recommendation(self):
        data = fixture()
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_hazard_age_sec=40)))
        registry, _, _ = environment(data=data)
        dispatch(registry, Tool.GENERATE)
        dispatch(registry, Tool.EVALUATE)
        clock = Clock()
        clock.value = 20
        runner, _, _ = controller(registry, clock=clock)
        trace = runner.run("strict-v1-freshness")
        self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertIsNone(trace.selected_route_id)

    def test_model_allowance_uses_original_host_budget_and_rules_use_none(self):
        clock = Clock()
        budget = ledger(clock, models=1)
        self.assertTrue(budget.reserve_model())
        self.assertEqual(budget.snapshot.host.model_calls, 1)
        self.assertFalse(budget.reserve_model())
        self.assertTrue(budget.snapshot.exhausted)
        self.assertFalse(budget.checkpoint())

    def test_invalid_limits_are_rejected(self):
        for bad in (True, -1, 513, 1.5):
            with self.assertRaises(ValueError):
                AdaptiveLimits(bad, 12, 4, 1, 2)


class NoProgressScopeRegressionTests(unittest.TestCase):
    class InvalidThenRules:
        def __init__(self):
            self.calls = 0

        def choose(self, obs):
            self.calls += 1
            if self.calls <= 2:
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, replace(obs.options[0]))
            return RulePlanner().choose(obs)

    def test_new_run_id_cannot_call_planner_after_no_progress_ceiling(self):
        registry, tools, _ = environment()
        planner = self.InvalidThenRules()
        runner, budget, _ = controller(registry, planner=planner)
        first = runner.run("first-invalid-run")
        second = runner.run("different-run-id")
        self.assertEqual(first.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(second.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertIsNone(second.steps[0].action)
        self.assertEqual(second.steps[0].reason, Reason.NO_PROGRESS)
        self.assertEqual(second.resources.tool_attempts, first.resources.tool_attempts)
        self.assertEqual(second.resources.host.provider_calls, first.resources.host.provider_calls)
        self.assertEqual(second.resources.steps, first.resources.steps + 1)
        self.assertEqual(tools.calls, ())

    def test_time_revision_option_identity_and_audit_reference_changes_do_not_reset(self):
        registry, tools, _ = environment()
        planner = self.InvalidThenRules()
        runner, _, clock = controller(registry, planner=planner)
        self.assertEqual(runner.run("before-cosmetic-changes").outcome, AdaptiveOutcome.NO_PROGRESS)
        registry.options()  # Reissue new objects, without new evidence.
        clock.value = 0.1
        state = registry.state
        registry.adopt_host_state(replace(state, as_of=clock.now(), state_revision=state.state_revision + 1,
            graph=replace(state.graph, verification_ref="new-audit-reference"),
            capabilities=tuple(reversed(state.capabilities))))
        trace = runner.run("after-cosmetic-changes")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(tools.calls, ())

    def test_cosmetic_host_event_cannot_replenish_ceiling(self):
        registry, tools, _ = environment()
        planner = self.InvalidThenRules()
        enabled = False
        def event(state, step):
            if enabled:
                return replace(state, state_revision=state.state_revision + 1,
                               hazard=replace(state.hazard, verification_ref="new-check-reference"))
        runner, _, _ = controller(registry, planner=planner, event=event)
        self.assertEqual(runner.run("initial-ceiling").outcome, AdaptiveOutcome.NO_PROGRESS)
        enabled = True
        self.assertEqual(runner.run("cosmetic-event").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(tools.calls, ())

    def test_verified_relevant_host_event_allows_continuation_without_budget_reset(self):
        registry, tools, authority = environment()
        planner = self.InvalidThenRules()
        enabled = False
        changed = False
        def event(state, step):
            nonlocal changed
            if enabled and not changed:
                changed = True
                graph = authority.fixture.graph("graph-2")
                return authority.contextualize(replace(state, graph=authority.evidence(graph, state),
                                                       state_revision=state.state_revision + 1))
        runner, _, _ = controller(registry, planner=planner, event=event)
        first = runner.run("exhausted-planner")
        self.assertEqual(first.outcome, AdaptiveOutcome.NO_PROGRESS)
        enabled = True
        continued = runner.run("verified-evidence-update")
        self.assertEqual(continued.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(actions(continued), (Tool.GENERATE, Tool.EVALUATE))
        self.assertEqual(continued.ranking.validation.expected_graph_snapshot_id, "graph-2")
        self.assertEqual(continued.resources.steps, first.resources.steps + len(continued.steps))
        self.assertEqual(continued.resources.tool_attempts, 2)
        self.assertEqual(len(tools.calls), 2)

    def test_unverified_artifact_claim_does_not_count_as_accepted_new_evidence(self):
        from src.agentic_orchestration.contracts import VerificationStatus
        registry, tools, authority = environment(graph=None, hazard=None)
        planner = self.InvalidThenRules()
        runner, _, _ = controller(registry, planner=planner)
        self.assertEqual(runner.run("missing-inputs").outcome, AdaptiveOutcome.NO_PROGRESS)
        state = registry.state
        claim = replace(authority.evidence(authority.fixture.graph("graph-2"), state),
                        verification=VerificationStatus.UNVERIFIED, verification_ref=None)
        registry.adopt_host_state(replace(state, graph=claim, state_revision=state.state_revision + 1))
        trace = runner.run("unverified-claim")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(tools.calls, ())

    def test_failed_recovery_cannot_resume_a_third_planner_call(self):
        class CountingRules:
            calls = 0
            def choose(self, obs):
                self.calls += 1
                return RulePlanner().choose(obs)
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),
                                                Fault(Tool.GENERATE, 2, FailureKind.TRANSIENT)))
        planner = CountingRules()
        runner, _, clock = controller(registry, planner=planner)
        first = runner.run("failed-recovery")
        self.assertEqual(first.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(first.resources.recovery_attempts, 1)
        clock.value = 0.1
        second = runner.run("retry-under-new-run-id")
        self.assertEqual(second.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(len(tools.calls), 2)
        self.assertEqual(second.resources.recovery_attempts, 1)
        self.assertEqual(second.resources.tool_attempts, 2)

    def test_clarification_does_not_reset_prior_invalid_proposal_history(self):
        class ClarifyingPlanner:
            calls = 0
            def choose(self, obs):
                self.calls += 1
                if self.calls == 2:
                    return AdaptiveAction(ActionKind.CLARIFY, Reason.EVIDENCE_REVIEW)
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, replace(obs.options[0]))
        registry, tools, _ = environment()
        planner = ClarifyingPlanner()
        runner, _, _ = controller(registry, planner=planner)
        self.assertEqual(runner.run("clarification").outcome, AdaptiveOutcome.CLARIFICATION)
        self.assertEqual(runner.run("unchanged-continuation").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(runner.run("unchanged-again").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 3)  # Two invalid proposals plus one clarification.
        self.assertEqual(tools.calls, ())

    def test_unchanged_accepted_tool_result_does_not_reset_history(self):
        data = fixture()
        hazard = data.hazards[0]
        data = replace(data, hazards=(replace(hazard, samples=(replace(hazard.samples[0], value=None),)
                                              + hazard.samples[1:]),))
        registry, tools, _ = environment(data=data)
        class OneInvalidThenRules:
            calls = 0
            def choose(self, obs):
                self.calls += 1
                if self.calls == 1:
                    return AdaptiveAction(ActionKind.CALL, Reason.HAZARD, replace(obs.options[0]))
                return RulePlanner().choose(obs)
        planner = OneInvalidThenRules()
        runner, _, _ = controller(registry, planner=planner)
        first = runner.run("unchanged-partial-evidence")
        self.assertEqual(first.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(first.resources.tool_attempts, 1)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(runner.run("same-partial-evidence").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)

    def test_planner_observation_progress_claims_cannot_reset_history(self):
        class FalseProgressPlanner:
            calls = 0
            def choose(self, obs):
                self.calls += 1
                object.__setattr__(obs, "no_progress", 0)
                object.__setattr__(obs.state.graph.snapshot, "snapshot_id", "planner-forged-snapshot")
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, replace(obs.options[0]))
        registry, tools, _ = environment()
        planner = FalseProgressPlanner()
        runner, _, _ = controller(registry, planner=planner)
        self.assertEqual(runner.run("false-progress").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(runner.run("false-progress-again").outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(registry.state.graph.snapshot.snapshot_id, "graph-1")
        self.assertEqual(tools.calls, ())


class AuditBoundaryRegressionTests(unittest.TestCase):
    def test_forged_action_deadline_failure_never_records_unissued_references(self):
        marker = "untrusted_reference_sentinel"
        clock = Clock()
        class ForgedLatePlanner:
            def choose(self, obs):
                clock.value = 2
                option = replace(obs.options[0], mission_ref=marker, policy_ref=marker)
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, option)
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, duration=1),
                                  planner=ForgedLatePlanner())
        trace = runner.run("audit-deadline")
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(len(trace.steps), 1)
        self.assertIsNone(trace.steps[0].action)
        self.assertEqual(trace.steps[0].reason, Reason.BUDGET)
        self.assertEqual(trace.steps[0].before.revision, registry.state.state_revision)
        self.assertTrue(trace.resources.exhausted)
        self.assertEqual(trace.resources.tool_attempts, 0)
        self.assertNotIn(marker, repr((trace, registry.records)))
        self.assertEqual(tools.calls, ())

    def test_forged_action_clock_failure_also_sanitizes_audit(self):
        marker = "untrusted_clock_failure_sentinel"
        clock = Clock()
        class ForgedPlanner:
            def choose(self, obs):
                clock.value = -1  # Evidence clock regresses; duration budget is separate.
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE,
                                      replace(obs.options[0], mission_ref=marker))
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, clock=clock, budget=ledger(Clock()), planner=ForgedPlanner())
        trace = runner.run("audit-clock")
        self.assertEqual(trace.outcome, AdaptiveOutcome.FAILED)
        self.assertEqual(trace.steps[0].reason, Reason.BOUNDARY)
        self.assertIsNone(trace.steps[0].action)
        self.assertNotIn(marker, repr((trace, registry.records)))
        self.assertEqual(tools.calls, ())

    def test_forged_and_altered_actions_are_sanitized_with_and_without_deadline(self):
        for alter_issued in (False, True):
            for expire in (False, True):
                with self.subTest(alter_issued=alter_issued, expire=expire):
                    marker = "untrusted_option_reference_sentinel"
                    clock = Clock()
                    class BadPlanner:
                        def choose(self, obs):
                            option = obs.options[0]
                            if alter_issued:
                                object.__setattr__(option, "mission_ref", marker)
                            else:
                                option = replace(option, mission_ref=marker)
                            if expire:
                                clock.value = 2
                            return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, option)
                    registry, tools, _ = environment()
                    runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, duration=1),
                                              planner=BadPlanner())
                    trace = runner.run("audit-invalid")
                    self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
                    self.assertTrue(all(s.action is None for s in trace.steps))
                    self.assertNotIn(marker, repr((trace, registry.records)))
                    self.assertEqual(tools.calls, ())

    def test_valid_action_remains_auditable_when_provider_budget_blocks_dispatch(self):
        registry, tools, _ = environment()
        clock = Clock()
        runner, _, _ = controller(registry, clock=clock, budget=ledger(clock, providers=0))
        trace = runner.run("audit-provider-budget")
        step = trace.steps[0]
        self.assertEqual(trace.outcome, AdaptiveOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(step.action.option.tool, Tool.GENERATE)
        self.assertEqual(step.action.option.mission_ref, registry.state.mission_ref)
        self.assertFalse(step.feedback.executed)
        self.assertFalse(step.feedback.ok)
        self.assertEqual(trace.resources.tool_attempts, 1)
        self.assertEqual(trace.resources.host.provider_calls, 0)
        self.assertEqual(tools.calls, ())

    def test_audit_actions_do_not_alias_objects_retained_by_planner(self):
        marker = "retained_action_mutation_sentinel"
        retained = []
        class RetainingPlanner:
            def choose(self, obs):
                action = RulePlanner().choose(obs)
                retained.append(action)
                return action
        registry, _, _ = environment()
        runner, _, _ = controller(registry, planner=RetainingPlanner())
        trace = runner.run("audit-detached")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        for action in retained:
            if action.option is not None:
                object.__setattr__(action.option, "mission_ref", marker)
        self.assertNotIn(marker, repr((trace, registry.records)))
        self.assertEqual(trace.steps[0].action.option.mission_ref, registry.state.mission_ref)

    def test_planner_cannot_rewrite_prior_feedback_or_registry_audit(self):
        marker = "feedback_mutation_sentinel"
        class FeedbackMutator:
            def choose(self, obs):
                if obs.feedback is not None and obs.feedback.failure is not None:
                    object.__setattr__(obs.feedback.failure, "code", marker)
                return RulePlanner().choose(obs)
        registry, _, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),))
        runner, _, _ = controller(registry, planner=FeedbackMutator())
        trace = runner.run("audit-feedback")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertNotIn(marker, repr((trace, registry.records)))
        self.assertEqual(trace.steps[0].feedback.failure.code, "injected_transient")

    def test_tool_body_and_exception_narrative_never_enter_audit(self):
        marker = "untrusted_tool_body_sentinel"
        for raises in (False, True):
            def bad_response(tool, state, claim):
                if raises:
                    raise RuntimeError(marker)
                return {"credentials": marker}
            registry, tools, _ = environment(mutate=bad_response)
            runner, _, _ = controller(registry)
            trace = runner.run("audit-tool-output")
            self.assertEqual(trace.outcome, AdaptiveOutcome.CLARIFICATION)
            self.assertNotIn(marker, repr((trace, registry.records)))
            self.assertEqual(len(tools.calls), 1)
            self.assertFalse(trace.steps[0].feedback.ok)
            self.assertTrue(trace.steps[0].feedback.executed)


class PlannerAndDispatchBoundaryTests(unittest.TestCase):
    def test_planner_observation_mutation_cannot_change_host_permissions(self):
        caps = tuple(ToolCapability(t, True, t is not Tool.GENERATE, True) for t in Tool)
        registry, tools, _ = environment(capabilities=caps)
        class MutatingPlanner:
            def choose(self, obs):
                cap = next(c for c in obs.state.capabilities if c.tool is Tool.GENERATE)
                object.__setattr__(cap, "authorized", True)
                state = obs.state
                proposal = ToolOption(Tool.GENERATE, state.mission_ref, state.policy_ref, state.state_revision)
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, proposal)
        runner, _, _ = controller(registry, planner=MutatingPlanner())
        trace = runner.run("permission-mutation")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertFalse(next(c for c in registry.state.capabilities if c.tool is Tool.GENERATE).authorized)
        self.assertEqual(tools.calls, ())

    def test_invalid_repeated_actions_stop_without_dispatch(self):
        class BadPlanner:
            def choose(self, obs):
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, replace(obs.options[0]))
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, planner=BadPlanner())
        trace = runner.run("forged")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(len(trace.steps), 2)
        self.assertEqual(tools.calls, ())
        self.assertEqual(trace.resources.tool_attempts, 0)

    def test_stale_option_from_previous_observation_is_never_dispatched(self):
        class Repeater:
            old = None
            def choose(self, obs):
                if self.old is None:
                    self.old = obs.options[0]
                return AdaptiveAction(ActionKind.CALL, Reason.GENERATE, self.old)
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, planner=Repeater())
        trace = runner.run("stale-option")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertEqual(len(tools.calls), 1)
        self.assertIsNone(trace.selected_route_id)

    def test_fabricated_completion_cannot_release_a_recommendation(self):
        class FalseCompletion:
            def choose(self, obs):
                return AdaptiveAction(ActionKind.COMPLETE, Reason.REUSE_EVALUATION)
        registry, tools, _ = environment()
        runner, _, _ = controller(registry, planner=FalseCompletion())
        trace = runner.run("false-completion")
        self.assertEqual(trace.outcome, AdaptiveOutcome.NO_PROGRESS)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(tools.calls, ())

    def test_reentrant_controller_run_is_rejected_without_resetting_budget(self):
        nested = []
        runner = None
        def reenter(tool, state, claim):
            nested.append(runner.run("nested-run"))
            return claim
        registry, tools, _ = environment(mutate=reenter)
        runner, _, _ = controller(registry)
        trace = runner.run("outer-run")
        self.assertEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertTrue(all(t.outcome is AdaptiveOutcome.BUSY for t in nested))
        self.assertEqual(trace.resources.tool_attempts, 2)
        self.assertEqual(len(tools.calls), 2)

    def test_lease_blocks_uncontrolled_dispatch_and_audits_interference(self):
        registry = None
        old_option = None
        nested = []
        def bypass(tool, state, claim):
            nested.append(registry.execute(old_option))
            return claim
        registry, tools, _ = environment(mutate=bypass)
        old_option, = registry.options()
        runner, _, _ = controller(registry)
        trace = runner.run("interference")
        self.assertEqual(trace.outcome, AdaptiveOutcome.FAILED)
        self.assertEqual(trace.reason, Reason.INTERFERENCE)
        self.assertEqual(nested[0].failure.kind, FailureKind.UNAUTHORIZED)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(len(registry.records), 2)
        self.assertEqual(len({r.call_id for r in registry.records}), 2)
        self.assertIsNone(trace.selected_route_id)

    def test_raw_registry_reentrant_dispatch_guard_also_preserves_audit(self):
        registry = None
        saved = None
        nested = []
        def reenter(tool, state, claim):
            nested.append(registry.execute(saved))
            return claim
        registry, tools, _ = environment(mutate=reenter)
        saved, = registry.options()
        result = registry.execute(saved)
        self.assertTrue(result.ok)
        self.assertEqual(nested[0].failure.code, "registry_dispatch_active")
        self.assertEqual(tuple(r.call_id for r in registry.records), ("call-000001", "call-000002"))
        self.assertEqual(len(tools.calls), 1)

    def test_permission_revocation_by_host_event_prevents_next_tool(self):
        registry, tools, _ = environment()
        def event(state, step):
            if step == 1:
                caps = tuple(replace(c, authorized=False) if c.tool is Tool.EVALUATE else c for c in state.capabilities)
                return replace(state, capabilities=caps, state_revision=state.state_revision + 1)
        runner, _, _ = controller(registry, event=event)
        trace = runner.run("revoked")
        self.assertNotEqual(trace.outcome, AdaptiveOutcome.SUCCESS)
        self.assertEqual(actions(trace), (Tool.GENERATE,))
        self.assertEqual(len(tools.calls), 1)

    def test_audit_is_immutable_bounded_and_excludes_planner_exception_text(self):
        class BrokenPlanner:
            def choose(self, obs):
                raise RuntimeError("SECRET_SENTINEL_MUST_NOT_BE_LOGGED")
        registry, _, _ = environment()
        runner, budget, _ = controller(registry, planner=BrokenPlanner())
        trace = runner.run("redacted")
        self.assertNotIn("SECRET_SENTINEL", repr(trace))
        self.assertLessEqual(len(trace.steps), budget.limits.max_steps)
        with self.assertRaises(FrozenInstanceError):
            trace.reason = Reason.NO_ACTION


if __name__ == "__main__":
    unittest.main()
