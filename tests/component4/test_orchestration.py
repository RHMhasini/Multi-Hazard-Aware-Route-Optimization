"""End-to-end synthetic workflow fixtures, not production upstream adapters."""

from dataclasses import replace
from datetime import timedelta
import unittest

from src.agentic_orchestration import (
    HazardEvidence, HazardStatus, ProviderError, RankingMetricEvidence, RankingStatus,
    RetryPolicy, RouteAlternatives, RunOutcome, RunState, TransientProviderError,
    VehicleEvidence, VerificationStatus, replay_evaluation, run_mission,
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


def run(provider, *, authority=None, as_of=NOW, retry_policy=RetryPolicy()):
    authority = authority if authority is not None else SyntheticAuthority()
    return run_mission("synthetic-run-1", mission(), policy(), provider, as_of=as_of,
                       verifier=authority, metric_verifier=authority, retry_policy=retry_policy)


class OrchestrationTests(unittest.TestCase):
    def test_success_records_inputs_tool_calls_states_and_ranking(self):
        provider = ScriptedProvider(alternatives(candidate("fast", 100, 100), candidate("safe", 300, 0)))
        trace = run(provider)
        self.assertEqual(trace.schema_version, "c4.mission-run/1.0.0")
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
                            ScriptedProvider(alternatives(candidate("fast", 100, 100))), as_of=NOW)
        self.assertEqual(trace.outcome, RunOutcome.INSUFFICIENT_DATA)
        self.assertIsNone(trace.selected_route_id)

    def test_replay_evaluates_recorded_inputs_without_calling_provider(self):
        provider = ScriptedProvider(TransientProviderError("temporary"),
                                    alternatives(candidate("safe", 300, 0), candidate("fast", 100, 100)))
        authority = SyntheticAuthority()
        trace = run(provider, authority=authority)
        replay = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(replay, trace.evaluation.ranking)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(replay.selected_route_id, "fast")
        # Recorded claims/outcomes alone cannot authorize a replayed selection.
        unsupported = replay_evaluation(trace)
        self.assertEqual(unsupported.status, RankingStatus.NO_ELIGIBLE_CANDIDATES)
        self.assertIsNone(unsupported.selected_route_id)
        authority.metrics.clear()
        changed = replay_evaluation(trace, verifier=authority, metric_verifier=authority)
        self.assertEqual(changed.status, RankingStatus.INSUFFICIENT_RANKING_DATA)
        self.assertIsNone(changed.selected_route_id)

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


if __name__ == "__main__":
    unittest.main()
