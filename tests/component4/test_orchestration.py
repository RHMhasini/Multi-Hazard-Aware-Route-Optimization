"""End-to-end synthetic workflow fixtures, not production upstream adapters."""

from dataclasses import asdict, replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from src.agentic_orchestration import (
    HazardEvidence, HazardStatus, ProviderError, RankingMetricEvidence, RankingStatus,
    RetryPolicy, RouteAlternatives, RunOutcome, RunState, TransientProviderError,
    VehicleEvidence, VerificationStatus, replay_evaluation, run_mission,
    DependencyIdentity, EvaluationMode, HostExecutionBudget, RunDependencies,
    BudgetSnapshot,
)
from test_ranking import candidate, policy
from test_validation import NOW, mission


class SyntheticAuthority:
    """Fixed expected records built separately, NEVER from provider candidates.

    This fixture simulates trusted application wiring only. It proves nothing
    about real hazard coverage, vehicle access or upstream data authenticity.
    """

    def __init__(self):
        self.hazards = {}
        self.vehicles = {}
        self.metrics = {}
        for route_id, time, exposure in (("fast", 100, 100), ("safe", 300, 0)):
            self.hazards[route_id] = HazardEvidence(
                status=HazardStatus.KNOWN, max_edge_hazard=0.3,
                observed_at=NOW - timedelta(seconds=60), valid_until=NOW + timedelta(seconds=60),
                complete_coverage=True, route_id=route_id, graph_snapshot_id="graph-1",
                hazard_snapshot_id="hazard-1", evidence_source="fixture/hazard",
                verification_status=VerificationStatus.VERIFIED,
            )
            self.vehicles[route_id] = VehicleEvidence(
                compatible_vehicle_types=frozenset({"van"}), route_id=route_id,
                graph_snapshot_id="graph-1", evidence_source="fixture/vehicle",
                verification_status=VerificationStatus.VERIFIED,
            )
            self.metrics[f"result/{route_id}"] = RankingMetricEvidence(
                route_id=route_id, graph_snapshot_id="graph-1", hazard_snapshot_id="hazard-1",
                route_result_id=f"result/{route_id}", travel_time_sec=time,
                cumulative_hazard_exposure=exposure, travel_time_unit="s", exposure_unit="hazard*m",
                travel_time_model_version="synthetic-time/1", exposure_formula_version="synthetic-exposure/1",
                evidence_source="fixture/results", verification_status=VerificationStatus.VERIFIED,
            )

    def verify_hazard(self, evidence):
        return self.hazards.get(evidence.route_id) == evidence

    def verify_vehicle(self, evidence):
        return self.vehicles.get(evidence.route_id) == evidence

    def get_authoritative_metrics(self, route_result_id):
        return self.metrics.get(route_result_id)


class ScriptedProvider:
    """Finite response/failure script for tests only; no C3 API is assumed."""

    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def get_alternatives(self, call):
        response = self.responses[len(self.calls)]
        self.calls.append(call)
        if isinstance(response, Exception):
            raise response
        return response


def alternatives(*candidates):
    return RouteAlternatives("synthetic-response-1", tuple(candidates))


def dependencies(with_clock=False):
    return RunDependencies(
        DependencyIdentity("ScriptedProvider", "1"), DependencyIdentity("SyntheticAuthority", "1"),
        DependencyIdentity("SyntheticAuthority-metrics", "1"), DependencyIdentity("fixed-test-records", "1"),
        DependencyIdentity("fixture-clock", "1") if with_clock else None,
    )


def host_budget():
    return HostExecutionBudget("synthetic-session", 10, 60, clock=lambda: 0.0)


def run(provider, *, authority=None, as_of=NOW, retry_policy=RetryPolicy(),
        budget=None, mode=EvaluationMode.HISTORICAL, clock=None):
    authority = authority if authority is not None else SyntheticAuthority()
    return run_mission("synthetic-run-1", mission(), policy(), provider, as_of=as_of,
                       verifier=authority, metric_verifier=authority, retry_policy=retry_policy,
                       budget=budget if budget is not None else host_budget(),
                       dependencies=dependencies(clock is not None), mode=mode, clock=clock)


class OrchestrationTests(unittest.TestCase):
    def test_success_records_inputs_tool_calls_states_and_ranking(self):
        provider = ScriptedProvider(alternatives(candidate("fast", 100, 100), candidate("safe", 300, 0)))
        trace = run(provider)
        self.assertEqual(trace.schema_version, "c4.mission-run/2.0.0")
        self.assertEqual(trace.outcome, RunOutcome.SELECTED)
        self.assertEqual(trace.selected_route_id, "fast")
        self.assertEqual(trace.request.request_id, "request-1")
        self.assertEqual(trace.policy.policy_id, "synthetic-weighted")
        self.assertEqual(trace.policy.policy_version, "1.0.0")
        self.assertEqual(trace.as_of, NOW)
        self.assertEqual([t.to_state for t in trace.transitions],
                         [RunState.CREATED, RunState.ACQUIRING, RunState.EVALUATING, RunState.FINISHED])
        self.assertEqual([t.sequence for t in trace.transitions], list(range(4)))
        self.assertEqual(trace.transitions[-1].from_state, RunState.EVALUATING)
        self.assertEqual(trace.provider_calls[0].call, provider.calls[0])
        self.assertEqual(trace.provider_calls[0].call.call_id, "synthetic-run-1:alternatives:1")
        self.assertEqual(trace.evaluation.call_id, "synthetic-run-1:evaluate")
        self.assertEqual(trace.evaluation.ranking.status, RankingStatus.SELECTED)
        self.assertIsNone(trace.evaluation.error)

    def test_no_eligible_candidates_is_distinct_from_insufficient_data(self):
        for response in (alternatives(), alternatives(candidate("fast", 901, 0))):
            with self.subTest(response=response):
                trace = run(ScriptedProvider(response))
                self.assertEqual(trace.outcome, RunOutcome.NO_ELIGIBLE_CANDIDATES)
                self.assertIsNone(trace.selected_route_id)
                self.assertEqual(len(trace.provider_calls), 1)

    def test_missing_evidence_is_terminal_without_retry(self):
        for missing in (replace(candidate("fast"), hazard=None),
                        replace(candidate("fast"), vehicle_evidence=None),
                        replace(candidate("fast"), ranking_metric_evidence=None)):
            with self.subTest(missing=missing):
                provider = ScriptedProvider(alternatives(missing))
                trace = run(provider)
                self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
                self.assertIsNone(trace.selected_route_id)
                self.assertEqual(len(provider.calls), 1)
                self.assertFalse(any(t.to_state == RunState.RETRYING for t in trace.transitions))

    def test_snapshot_mismatch_does_not_change_the_expected_snapshot(self):
        original = candidate("fast", 100, 100)
        mismatched = replace(original, hazard=replace(original.hazard, hazard_snapshot_id="other"))
        trace = run(ScriptedProvider(alternatives(mismatched)))
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(trace.request.expected_hazard_snapshot_id, "hazard-1")
        self.assertEqual(trace.evaluation.ranking.validation.candidate_assessments[0].checks[2].reason_code,
                         "evidence_binding_mismatch")

    def test_stale_hazard_data_cannot_select_or_trigger_refresh(self):
        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                    as_of=NOW + timedelta(seconds=301))
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(trace.evaluation.ranking.validation.candidate_assessments[0].freshness.reason_code,
                         "hazard_stale")
        self.assertEqual(len(trace.provider_calls), 1)

    def test_separately_held_authority_rejects_tampered_metric_claims(self):
        original = candidate("fast", 100, 100)
        tampered = replace(original, cumulative_hazard_exposure=0, ranking_metric_evidence=replace(
            original.ranking_metric_evidence, cumulative_hazard_exposure=0))
        trace = run(ScriptedProvider(alternatives(tampered)))
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(trace.evaluation.ranking.metric_issues[0].reason_code, "metric_authority_mismatch")

    def test_transient_failure_recovers_with_unchanged_request_policy_and_time(self):
        provider = ScriptedProvider(TransientProviderError("temporarily unavailable"),
                                    alternatives(candidate("fast", 100, 100)))
        trace = run(provider)
        self.assertEqual(trace.outcome, RunOutcome.SELECTED)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(trace.provider_calls[0].error.code, "provider_transient")
        self.assertIsNone(trace.provider_calls[1].error)
        self.assertEqual([call.attempt for call in provider.calls], [1, 2])
        self.assertEqual(provider.calls[0].request, provider.calls[1].request)
        self.assertEqual(provider.calls[0].as_of, provider.calls[1].as_of)
        self.assertEqual(trace.policy, policy())
        self.assertEqual([t.to_state for t in trace.transitions], [
            RunState.CREATED, RunState.ACQUIRING, RunState.RETRYING,
            RunState.ACQUIRING, RunState.EVALUATING, RunState.FINISHED,
        ])

    def test_retry_exhaustion_includes_initial_call_and_stops_at_budget(self):
        for max_attempts in (1, 2, 3):
            with self.subTest(max_attempts=max_attempts):
                provider = ScriptedProvider(*[TransientProviderError("temporary") for _ in range(4)])
                trace = run(provider, retry_policy=RetryPolicy(max_attempts))
                self.assertEqual(trace.outcome, RunOutcome.RETRY_EXHAUSTED)
                self.assertEqual(len(provider.calls), max_attempts)
                self.assertIsNone(trace.evaluation)
                self.assertIsNone(trace.selected_route_id)
                self.assertEqual(trace.transitions[-1].to_state, RunState.FINISHED)

    def test_permanent_and_unclassified_failures_are_never_retried(self):
        for error, code in ((ProviderError("missing dataset"), "provider_permanent"),
                            (RuntimeError("unexpected"), "provider_unexpected"),
                            (ValueError("invalid request"), "provider_unexpected")):
            with self.subTest(error=error):
                provider = ScriptedProvider(error, alternatives(candidate("fast", 100, 100)))
                trace = run(provider)
                self.assertEqual(trace.outcome, RunOutcome.PROVIDER_FAILURE)
                self.assertEqual(trace.provider_calls[0].error.code, code)
                self.assertEqual(len(provider.calls), 1)
                self.assertIsNone(trace.selected_route_id)

    def test_invalid_response_schemas_fail_without_evaluation_or_retry(self):
        for response in (None, {}, RouteAlternatives("", ()),
                         RouteAlternatives("id", [], "c4.route-alternatives/1.0.0"),
                         RouteAlternatives("id", (None,)), RouteAlternatives("id", (), "future/2.0.0")):
            with self.subTest(response=response):
                provider = ScriptedProvider(response)
                trace = run(provider)
                self.assertEqual(trace.outcome, RunOutcome.PROVIDER_FAILURE)
                self.assertEqual(trace.provider_calls[0].error.code, "provider_invalid_response")
                self.assertEqual(len(provider.calls), 1)
                self.assertIsNone(trace.evaluation)

    def test_duplicate_ids_use_existing_gate_and_do_not_retry(self):
        provider = ScriptedProvider(alternatives(candidate("fast", 100, 100), candidate("fast", 100, 100)))
        trace = run(provider)
        self.assertEqual(trace.outcome, RunOutcome.INVALID_INPUT)
        self.assertIn("Duplicate route_id", trace.evaluation.error.detail)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(len(provider.calls), 1)

    def test_missing_verifiers_are_not_replaced_by_fixture_claims(self):
        trace = run_mission("run", mission(), policy(),
                            ScriptedProvider(alternatives(candidate("fast", 100, 100))), as_of=NOW,
                            budget=host_budget(), dependencies=dependencies())
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)

    def test_replay_evaluates_recorded_inputs_without_calling_provider(self):
        provider = ScriptedProvider(TransientProviderError("temporary"),
                                    alternatives(candidate("safe", 300, 0), candidate("fast", 100, 100)))
        authority = SyntheticAuthority()
        trace = run(provider, authority=authority)
        replay = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(replay.ranking, trace.evaluation.ranking)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(replay.ranking.selected_route_id, "fast")
        self.assertEqual(replay.mode, EvaluationMode.HISTORICAL)
        # Recorded claims/outcomes alone cannot authorize a replayed selection.
        unsupported = replay_evaluation(trace)
        self.assertEqual(unsupported.ranking.status, RankingStatus.NO_ELIGIBLE_CANDIDATES)
        self.assertIsNone(unsupported.ranking.selected_route_id)
        authority.metrics.clear()
        changed = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(changed.ranking.status, RankingStatus.INSUFFICIENT_RANKING_DATA)
        self.assertIsNone(changed.ranking.selected_route_id)

    def test_identical_inputs_and_fixture_observations_produce_identical_traces(self):
        response = alternatives(candidate("fast", 100, 100))
        self.assertEqual(run(ScriptedProvider(response)), run(ScriptedProvider(response)))

    def test_invalid_run_configuration_and_missing_replay_are_rejected(self):
        for attempts in (0, -1, True, 1.5):
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                RetryPolicy(attempts)
        provider = ScriptedProvider(alternatives())
        with self.assertRaises(ValueError):
            run(provider, as_of=NOW.replace(tzinfo=None))
        self.assertEqual(provider.calls, [])
        trace = run(ScriptedProvider(ProviderError("unavailable")))
        with self.assertRaises(ValueError):
            replay_evaluation(trace)


class ManualClock:
    def __init__(self):
        self.seconds = 0.0

    def monotonic(self):
        return self.seconds

    def now(self):
        return NOW + timedelta(seconds=self.seconds)


class BoundaryTests(unittest.TestCase):
    def test_incompatible_policy_fails_before_provider_or_budget_use(self):
        provider = ScriptedProvider(alternatives())
        budget = host_budget()
        with self.assertRaises(ValueError):
            run_mission("run", mission(), policy(mission_type="other"), provider, as_of=NOW,
                        budget=budget, dependencies=dependencies())
        self.assertEqual(provider.calls, [])
        self.assertEqual(budget.checkpoint().provider_calls, 0)

    def test_mutable_invalid_evidence_cannot_change_the_trace(self):
        evidence = {"caller_owned": [1]}
        claim = replace(candidate("fast", 100, 100), hazard=evidence)
        trace = run(ScriptedProvider(alternatives(claim)))
        before = asdict(trace)
        evidence["caller_owned"].append(2)
        evidence["new"] = "changed"
        self.assertEqual(asdict(trace), before)
        self.assertIsNot(trace.provider_calls[0].response.candidates[0].hazard, evidence)
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)

    def test_tool_request_is_detached_from_caller_owned_invalid_fields(self):
        mutable_snapshot = ["graph-1"]
        request = replace(mission(), expected_graph_snapshot_id=mutable_snapshot)
        trace = run_mission("run", request, policy(),
                            ScriptedProvider(alternatives(candidate("fast", 100, 100))), as_of=NOW,
                            budget=host_budget(), dependencies=dependencies())
        before = asdict(trace)
        mutable_snapshot.append("changed")
        self.assertEqual(asdict(trace), before)
        self.assertIsNone(trace.selected_route_id)

    def test_response_inspection_errors_are_terminal_and_audited(self):
        # Simulate a malformed decoded record with missing fields.
        malformed = object.__new__(RouteAlternatives)
        trace = run(ScriptedProvider(malformed))
        self.assertEqual(trace.outcome, RunOutcome.PROVIDER_FAILURE)
        self.assertEqual(trace.provider_calls[0].error.code, "provider_invalid_response")
        self.assertEqual(len(trace.provider_calls), 1)
        self.assertIsNone(trace.selected_route_id)

    def test_error_formatting_failure_is_contained(self):
        class BrokenMessage(ProviderError):
            def __str__(self):
                raise RuntimeError("Cannot format error")

        trace = run(ScriptedProvider(BrokenMessage()))
        self.assertEqual(trace.outcome, RunOutcome.PROVIDER_FAILURE)
        self.assertEqual(trace.provider_calls[0].error.detail, "Exception detail unavailable")

    def test_process_level_termination_is_not_swallowed(self):
        class InterruptedProvider:
            def get_alternatives(self, call):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            run(InterruptedProvider())

    def test_shared_budget_cannot_be_reset_by_a_new_run_id(self):
        budget = HostExecutionBudget("controller-session", 1, 60, clock=lambda: 0.0)
        first = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))), budget=budget)
        self.assertEqual(first.outcome, RunOutcome.SELECTED)
        provider = ScriptedProvider(alternatives(candidate("fast", 100, 100)))
        second = run_mission("another-run", mission(), policy(), provider, as_of=NOW,
                             budget=budget, dependencies=dependencies())
        self.assertEqual(second.outcome, RunOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(provider.calls, [])
        self.assertEqual(second.budget_checks[-1].provider_calls, 1)

    def test_provider_overrun_stops_before_evaluation_without_claiming_interruption(self):
        clock = ManualClock()

        class SlowProvider(ScriptedProvider):
            def get_alternatives(self, call):
                clock.seconds = 60
                return super().get_alternatives(call)

        provider = SlowProvider(alternatives(candidate("fast", 100, 100)))
        trace = run(provider, budget=HostExecutionBudget("session", 2, 60, clock=clock.monotonic))
        self.assertEqual(len(provider.calls), 1)  # Callback completed; it was not interrupted.
        self.assertEqual(trace.outcome, RunOutcome.BUDGET_EXHAUSTED)
        self.assertIsNone(trace.evaluation)
        self.assertIsNone(trace.selected_route_id)

    def test_budget_also_covers_ranking_and_verifier_duration(self):
        from src.agentic_orchestration.ranking import rank_candidates as real_rank
        clock = ManualClock()

        def slow_rank(*args, **kwargs):
            clock.seconds = 60
            return real_rank(*args, **kwargs)

        with patch("src.agentic_orchestration.orchestration.rank_candidates", side_effect=slow_rank):
            trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                        budget=HostExecutionBudget("session", 2, 60, clock=clock.monotonic))
        self.assertEqual(trace.outcome, RunOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(trace.evaluation.ranking.status, RankingStatus.SELECTED)
        self.assertIsNone(trace.selected_route_id)  # Provisional ranking is never released.

    def test_unavailable_budget_fails_closed_before_provider(self):
        class BrokenBudget:
            def checkpoint(self, **kwargs):
                raise RuntimeError("Budget store unavailable")

        provider = ScriptedProvider(alternatives())
        trace = run(provider, budget=BrokenBudget())
        self.assertEqual(trace.outcome, RunOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(trace.boundary_error.code, "budget_unavailable")
        self.assertEqual(provider.calls, [])

    def test_current_mode_uses_time_after_acquisition_not_historical_hint(self):
        def scenario(mode):
            clock = ManualClock()

            class AdvancingProvider(ScriptedProvider):
                def get_alternatives(self, call):
                    clock.seconds = 301
                    return super().get_alternatives(call)

            return run(AdvancingProvider(alternatives(candidate("fast", 100, 100))), mode=mode,
                       clock=clock if mode == EvaluationMode.CURRENT else None,
                       budget=HostExecutionBudget("session", 2, 1000, clock=clock.monotonic))

        historical = scenario(EvaluationMode.HISTORICAL)
        current = scenario(EvaluationMode.CURRENT)
        self.assertEqual(historical.outcome, RunOutcome.SELECTED)
        self.assertEqual(historical.mode, EvaluationMode.HISTORICAL)
        self.assertEqual(current.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertEqual(current.evaluation.evaluated_at, NOW + timedelta(seconds=301))
        self.assertIsNone(current.selected_route_id)

    def test_current_selection_is_rechecked_for_expiry_after_ranking(self):
        clock = ManualClock()

        class SlowAuthority(SyntheticAuthority):
            def get_authoritative_metrics(self, result_id):
                clock.seconds = 60  # Exact expiry boundary, still within the run budget.
                return super().get_authoritative_metrics(result_id)

        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                    authority=SlowAuthority(), mode=EvaluationMode.CURRENT, clock=clock)
        self.assertEqual(trace.evaluation.ranking.status, RankingStatus.SELECTED)
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)
        self.assertEqual(trace.completion_freshness[0].freshness.reason_code, "hazard_stale")

    def test_current_clock_failure_is_audited_without_selection(self):
        class BrokenClock:
            def now(self):
                raise RuntimeError("Authoritative clock unavailable")

        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                    mode=EvaluationMode.CURRENT, clock=BrokenClock())
        self.assertEqual(trace.outcome, RunOutcome.EVALUATION_FAILURE)
        self.assertEqual(trace.boundary_error.code, "evaluation_clock_failed")
        self.assertIsNone(trace.evaluation.evaluated_at)
        self.assertIsNone(trace.selected_route_id)

    def test_current_mode_can_select_with_fresh_supported_evidence(self):
        clock = ManualClock()
        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                    mode=EvaluationMode.CURRENT, clock=clock)
        self.assertEqual(trace.outcome, RunOutcome.SELECTED)
        self.assertEqual(trace.mode, EvaluationMode.CURRENT)
        self.assertEqual(trace.evaluation.evaluated_at, NOW)
        self.assertEqual(trace.completed_at, NOW)
        self.assertEqual(trace.completion_freshness[0].freshness.reason_code, "hazard_fresh")
        authority = SyntheticAuthority()
        replay = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(replay.mode, EvaluationMode.HISTORICAL)
        self.assertEqual(replay.ranking, trace.evaluation.ranking)

    def test_current_clock_regression_during_ranking_cannot_release_selection(self):
        class RegressingClock:
            def __init__(self):
                self.values = iter((NOW + timedelta(seconds=10), NOW + timedelta(seconds=5)))

            def now(self):
                return next(self.values)

        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))),
                    mode=EvaluationMode.CURRENT, clock=RegressingClock())
        self.assertEqual(trace.outcome, RunOutcome.EVALUATION_FAILURE)
        self.assertIsNone(trace.selected_route_id)

    def test_inconsistent_budget_snapshot_fails_closed(self):
        class ExpiredButNotExhausted:
            def checkpoint(self, **kwargs):
                return BudgetSnapshot("scope", 2, 0, 10, 10, False, None)

        provider = ScriptedProvider(alternatives())
        trace = run(provider, budget=ExpiredButNotExhausted())
        self.assertEqual(trace.outcome, RunOutcome.BUDGET_EXHAUSTED)
        self.assertEqual(trace.boundary_error.code, "budget_unavailable")
        self.assertEqual(provider.calls, [])

    def test_current_mode_requires_explicit_host_clock_before_provider(self):
        provider = ScriptedProvider(alternatives())
        with self.assertRaises(ValueError):
            run(provider, mode=EvaluationMode.CURRENT)
        self.assertEqual(provider.calls, [])

    def test_unexpected_ranking_exception_is_audited_without_retry(self):
        with patch("src.agentic_orchestration.orchestration.rank_candidates",
                   side_effect=RuntimeError("Unexpected ranking failure")):
            provider = ScriptedProvider(alternatives(candidate("fast", 100, 100)))
            trace = run(provider)
        self.assertEqual(trace.outcome, RunOutcome.EVALUATION_FAILURE)
        self.assertEqual(trace.evaluation.error.code, "evaluation_failed")
        self.assertEqual(len(provider.calls), 1)
        self.assertIsNone(trace.selected_route_id)

    def test_replay_versions_and_dependency_identities_are_explicit(self):
        trace = run(ScriptedProvider(alternatives(candidate("fast", 100, 100))))
        self.assertEqual(trace.dependencies.evidence_bundle.version, "1")
        self.assertEqual(trace.dependencies.verifier.name, "SyntheticAuthority")
        authority = SyntheticAuthority()
        replay = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(replay.mode, EvaluationMode.HISTORICAL)
        self.assertEqual(replay.evaluated_at, trace.evaluation.evaluated_at)
        for field in ("schema_version", "runner_version"):
            unsupported = replace(trace)
            object.__setattr__(unsupported, field, "unsupported/99")  # Emulate a future decoded trace.
            with self.subTest(field=field), self.assertRaises(ValueError):
                replay_evaluation(unsupported)


if __name__ == "__main__":
    unittest.main()
