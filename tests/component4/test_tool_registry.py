"""M2 local synthetic execution and independent-authority regressions only."""

from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch
import unittest

from src.agentic_orchestration.adaptive_contracts import Coverage, Tool, ToolCapability, ToolOption
from src.agentic_orchestration.contracts import VerificationStatus
from src.agentic_orchestration.ranking import RankingStatus
from src.agentic_orchestration.synthetic_tools import AlternativesClaim, EdgeKey, Fault, PreparedClaim
from src.agentic_orchestration.tool_registry import FailureKind, ToolFailure, ToolRejected, ToolResult
from synthetic_oracles import dispatch, enumerate_truth, environment, fixture


def options(registry):
    return tuple(o.tool for o in registry.options())


class SyntheticExecutionPaths(unittest.TestCase):
    def test_A_generate_then_existing_gate_without_rebuilding(self):
        registry, tools, authority = environment()
        self.assertEqual(options(registry), (Tool.GENERATE,))
        generated = dispatch(registry, Tool.GENERATE)
        self.assertTrue(generated.ok)
        self.assertEqual(generated.next_revision, 1)
        self.assertEqual(registry.state.alternatives.verification, VerificationStatus.VERIFIED)
        self.assertEqual(options(registry), (Tool.EVALUATE,))
        evaluated = dispatch(registry, Tool.EVALUATE)
        self.assertTrue(evaluated.ok)
        self.assertEqual(evaluated.update.payload.status, RankingStatus.SELECTED)
        self.assertEqual(evaluated.update.payload.selected_route_id, "route-1")
        self.assertEqual(tuple(t for t, _ in tools.calls), (Tool.GENERATE, Tool.EVALUATE))
        self.assertEqual(len(registry.records), 2)
        self.assertTrue(authority.get_authoritative_metrics(
            generated.update.payload[0].ranking_metric_evidence.route_result_id))

    def test_B_missing_hazard_is_prepared_independently(self):
        registry, tools, _ = environment(hazard=None)
        original = registry.state.graph
        self.assertEqual(options(registry), (Tool.PREPARE_HAZARD,))
        self.assertTrue(dispatch(registry, Tool.PREPARE_HAZARD).ok)
        self.assertEqual(registry.state.graph, original)
        self.assertEqual(options(registry), (Tool.GENERATE,))
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
        self.assertEqual(len(tools.calls), 2)

    def test_C_stale_graph_is_prepared_without_hazard_recalculation(self):
        registry, tools, _ = environment(graph="graph-old")
        hazard = registry.state.hazard
        self.assertEqual(options(registry), (Tool.PREPARE_NETWORK,))
        self.assertTrue(dispatch(registry, Tool.PREPARE_NETWORK).ok)
        self.assertEqual(registry.state.graph.snapshot.snapshot_id, "graph-1")
        self.assertEqual(registry.state.hazard, hazard)
        self.assertEqual(options(registry), (Tool.GENERATE,))
        self.assertEqual(tools.calls, ((Tool.PREPARE_NETWORK, 0),))

    def test_D_either_independent_preparation_order_has_identical_outcome(self):
        outcomes = []
        for order in ((Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK),
                      (Tool.PREPARE_NETWORK, Tool.PREPARE_HAZARD)):
            registry, tools, _ = environment(graph=None, hazard=None)
            self.assertEqual(set(options(registry)), set(order))
            for tool in order:
                self.assertTrue(dispatch(registry, tool).ok)
            self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
            result = dispatch(registry, Tool.EVALUATE)
            self.assertTrue(result.ok)
            outcomes.append((registry.state, result.update.payload))
            self.assertEqual(len(tools.calls), 4)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_E_self_verified_corrupt_artifact_cannot_grant_authority(self):
        def corrupt(tool, state, claim):
            artifact = claim.artifact
            sample = replace(artifact.samples[0], value=0.0)
            return PreparedClaim(replace(artifact, samples=(sample,) + artifact.samples[1:]),
                                 VerificationStatus.VERIFIED)
        registry, _, _ = environment(hazard=None, mutate=corrupt)
        before = registry.state
        result = dispatch(registry, Tool.PREPARE_HAZARD)
        self.assertEqual(result.failure.kind, FailureKind.CONTRADICTORY)
        self.assertEqual(registry.state, before)
        self.assertIsNone(result.update)

    def test_F_graph_event_preserves_old_alternatives_and_requires_new_generation(self):
        registry, _, authority = environment(graph_target="graph-2")
        generated = dispatch(registry, Tool.GENERATE)
        old_binding = registry.state.alternatives.binding
        old_option, = registry.options()
        new_graph = authority.fixture.graph("graph-2")
        new = replace(registry.state, graph=authority.evidence(new_graph, registry.state),
                      state_revision=registry.state.state_revision + 1)
        registry.adopt_host_state(authority.contextualize(new))
        self.assertEqual(registry.state.alternatives.binding, old_binding)
        self.assertEqual(registry.execute(old_option).failure.kind, FailureKind.STALE_BINDING)
        self.assertEqual(options(registry), (Tool.GENERATE,))
        regenerated = dispatch(registry, Tool.GENERATE)
        self.assertTrue(regenerated.ok)
        self.assertNotEqual(regenerated.update.artifact_refs, generated.update.artifact_refs)
        self.assertEqual(registry.state.alternatives.binding.graph_snapshot_id, "graph-2")
        self.assertEqual(len(regenerated.update.payload), 2)
        self.assertTrue(dispatch(registry, Tool.EVALUATE).ok)

    def test_G_transient_and_permanent_are_distinct_and_never_auto_retry(self):
        for kind in (FailureKind.TRANSIENT, FailureKind.PERMANENT):
            registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, kind),))
            before = registry.state
            result = dispatch(registry, Tool.GENERATE)
            self.assertEqual(result.failure.kind, kind)
            self.assertEqual(result.failure.retryable, kind is FailureKind.TRANSIENT)
            self.assertEqual(len(tools.calls), 1)
            self.assertEqual(registry.state, before)
            if kind is FailureKind.TRANSIENT:
                explicit = dispatch(registry, Tool.GENERATE)
                self.assertTrue(explicit.ok)
                self.assertEqual(result.idempotency_key, explicit.idempotency_key)
                self.assertNotEqual(result.call_id, explicit.call_id)
            else:
                explicit = dispatch(registry, Tool.GENERATE)
                self.assertEqual(explicit.failure.kind, FailureKind.PERMANENT)
                self.assertEqual(len(tools.calls), 2)

    def test_metric_values_and_all_keyed_paths_match_independent_enumerator(self):
        data = fixture()
        registry, tools, _ = environment(data=data)
        # Collect producer paths for correspondence only; truth comes from inputs.
        claim = tools.invoke(Tool.GENERATE, registry.state)
        expected = enumerate_truth(data)
        self.assertEqual({r.edges for r in claim.routes}, set(expected))
        self.assertEqual(len(expected), 3)  # Includes both parallel A->B keys.
        for route in claim.routes:
            actual = (route.distance_m, route.travel_time_sec, route.exposure_haz_m,
                      route.max_hazard, route.vehicles)
            self.assertEqual(actual, expected[route.edges])
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)

    def test_explicit_sequence_replays_exact_records_and_artifacts(self):
        traces = []
        for _ in range(2):
            registry, tools, _ = environment(hazard=None,
                faults=(Fault(Tool.PREPARE_HAZARD, 1, FailureKind.TRANSIENT),))
            for tool in (Tool.PREPARE_HAZARD, Tool.PREPARE_HAZARD, Tool.GENERATE, Tool.EVALUATE):
                dispatch(registry, tool)
            traces.append((registry.state, registry.records, tools.calls))
        self.assertEqual(traces[0], traces[1])


class RegistryBoundaryTests(unittest.TestCase):
    def test_equal_but_forged_option_does_not_execute(self):
        registry, tools, _ = environment()
        issued, = registry.options()
        self.assertEqual(registry.execute(replace(issued)).failure.kind, FailureKind.FORGED_OPTION)
        self.assertEqual(tools.calls, ())
        self.assertTrue(registry.execute(issued).ok)

    def test_tampered_issued_option_cannot_be_repurposed(self):
        registry, tools, _ = environment(graph=None, hazard=None)
        hazard, _ = registry.options()
        object.__setattr__(hazard, "tool", Tool.PREPARE_NETWORK)
        result = registry.execute(hazard)
        self.assertEqual(result.failure.kind, FailureKind.FORGED_OPTION)
        self.assertEqual(tools.calls, ())

    def test_authoritative_preflight_rejects_forged_complete_coverage_before_tool_call(self):
        from src.agentic_orchestration.adaptive_contracts import RoutingContext
        data = fixture()
        hazard = data.hazards[0]
        data = replace(data, hazards=(replace(hazard, samples=hazard.samples[1:]),))
        registry, tools, _ = environment(data=data)
        initial = registry.state
        forged_context = RoutingContext(data_binding(registry), VerificationStatus.VERIFIED, "forged-check")
        forged_state = replace(initial, hazard=replace(initial.hazard, coverage=Coverage.COMPLETE),
                               routing_context=forged_context, state_revision=1)
        registry.adopt_host_state(forged_state)
        result = dispatch(registry, Tool.GENERATE)
        self.assertEqual(result.failure.code, "dependency_summary_mismatch")
        self.assertFalse(result.executed)
        self.assertEqual(tools.calls, ())

    def test_invalid_input_type_returns_structured_rejection(self):
        registry, tools, _ = environment()
        for value in (None, {}, "generate_alternatives"):
            result = registry.execute(value)
            self.assertEqual(result.failure.kind, FailureKind.FORGED_OPTION)
            self.assertFalse(result.executed)
        self.assertEqual(tools.calls, ())

    def test_stale_option_and_same_revision_wrong_scope_reject(self):
        registry, tools, _ = environment()
        option, = registry.options()
        for change in ({"state_revision": 8}, {"mission_ref": "other"}, {"policy_ref": "other"}):
            result = registry.execute(replace(option, **change))
            self.assertFalse(result.ok)
            self.assertFalse(result.executed)
        self.assertEqual(tools.calls, ())

    def test_consumed_failure_option_cannot_execute_twice(self):
        registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, FailureKind.TRANSIENT),))
        option, = registry.options()
        self.assertFalse(registry.execute(option).ok)
        self.assertEqual(registry.execute(option).failure.kind, FailureKind.FORGED_OPTION)
        self.assertEqual(len(tools.calls), 1)

    def test_permissions_support_and_readiness_are_checked_at_dispatch(self):
        for field, kind in (("authorized", FailureKind.UNAUTHORIZED),
                            ("supported", FailureKind.UNSUPPORTED),
                            ("inputs_ready", FailureKind.MISSING_INPUT)):
            caps = tuple(ToolCapability(t, **({"supported": True, "authorized": True,
                          "inputs_ready": True} | ({field: False} if t is Tool.GENERATE else {}))) for t in Tool)
            registry, tools, _ = environment(capabilities=caps)
            state = registry.state
            forged = ToolOption(Tool.GENERATE, state.mission_ref, state.policy_ref, state.state_revision)
            self.assertEqual(registry.execute(forged).failure.kind, kind)
            self.assertEqual(tools.calls, ())
            self.assertNotIn(Tool.GENERATE, options(registry))

    def test_unregistered_tool_is_not_exposed_despite_state_support(self):
        registry, tools, _ = environment(hazard=None, supported=frozenset({Tool.PREPARE_NETWORK}))
        self.assertEqual(options(registry), ())
        self.assertFalse(next(c for c in registry.state.capabilities if c.tool is Tool.PREPARE_HAZARD).supported)
        state = registry.state
        forged = ToolOption(Tool.PREPARE_HAZARD, state.mission_ref, state.policy_ref, state.state_revision)
        self.assertEqual(registry.execute(forged).failure.kind, FailureKind.UNSUPPORTED)
        self.assertEqual(tools.calls, ())

    def test_forged_generation_cannot_bypass_missing_dependencies(self):
        registry, tools, _ = environment(graph=None, hazard=None)
        state = registry.state
        forged = ToolOption(Tool.GENERATE, state.mission_ref, state.policy_ref, state.state_revision)
        self.assertEqual(registry.execute(forged).failure.kind, FailureKind.MISSING_INPUT)
        self.assertEqual(tools.calls, ())

    def test_missing_routing_context_remains_a_precondition(self):
        registry, tools, _ = environment()
        new = replace(registry.state, routing_context=None, state_revision=1)
        registry.adopt_host_state(new)
        self.assertEqual(options(registry), ())
        forged = ToolOption(Tool.GENERATE, new.mission_ref, new.policy_ref, 1)
        self.assertEqual(registry.execute(forged).failure.kind, FailureKind.MISSING_INPUT)
        self.assertEqual(tools.calls, ())

    def test_permission_revocation_invalidates_previously_issued_option(self):
        registry, tools, _ = environment()
        option, = registry.options()
        caps = tuple(replace(c, authorized=False) if c.tool is Tool.GENERATE else c
                     for c in registry.state.capabilities)
        registry.adopt_host_state(replace(registry.state, capabilities=caps, state_revision=1))
        self.assertEqual(registry.execute(option).failure.kind, FailureKind.STALE_BINDING)
        self.assertEqual(tools.calls, ())

    def test_payload_rejection_never_echoes_adapter_exception(self):
        def crash(*args):
            raise RuntimeError("untrusted text that must not escape")
        registry, tools, _ = environment(mutate=crash)
        result = dispatch(registry, Tool.GENERATE)
        self.assertEqual(result.failure.code, "tool_boundary_error")
        self.assertNotIn("untrusted text", repr(result))
        self.assertEqual(len(tools.calls), 1)

    def test_no_network_files_clock_reads_and_frozen_artifacts(self):
        registry, _, authority = environment(hazard=None)
        original = authority.fixture
        with patch("builtins.open", side_effect=AssertionError("I/O")), patch(
                "socket.socket", side_effect=AssertionError("network")), patch(
                "time.time", side_effect=AssertionError("clock")):
            self.assertTrue(dispatch(registry, Tool.PREPARE_HAZARD).ok)
            self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
            self.assertTrue(dispatch(registry, Tool.EVALUATE).ok)
        self.assertEqual(authority.fixture, original)
        with self.assertRaises(FrozenInstanceError):
            original.graphs[0].crs = "changed"


class IndependentAuthorityTests(unittest.TestCase):
    def assert_corruption_rejected(self, mutate, kind=FailureKind.CONTRADICTORY):
        registry, tools, authority = environment(mutate=mutate)
        before = registry.state
        result = dispatch(registry, Tool.GENERATE)
        self.assertEqual(result.failure.kind, kind)
        self.assertEqual(registry.state, before)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(authority._alternatives, {})
        self.assertEqual(authority._metrics, {})

    def test_forged_route_metrics_reject_even_with_verified_flag(self):
        def corrupt(tool, state, claim):
            bad = replace(claim.routes[0], travel_time_sec=1, exposure_haz_m=0)
            return replace(claim, routes=(bad,) + claim.routes[1:], claimed_verification=VerificationStatus.VERIFIED)
        self.assert_corruption_rejected(corrupt)

    def test_wrong_parallel_edge_key_rejects(self):
        def corrupt(tool, state, claim):
            route = claim.routes[0]
            bad = replace(route, edges=(replace(route.edges[0], key="quiet"),) + route.edges[1:])
            return replace(claim, routes=(bad,) + claim.routes[1:])
        self.assert_corruption_rejected(corrupt)

    def test_missing_edge_and_wrong_direction_reject(self):
        for edge in (EdgeKey("A", "B", "invented"), EdgeKey("B", "A", "fast")):
            def corrupt(tool, state, claim):
                bad = replace(claim.routes[0], edges=(edge,) + claim.routes[0].edges[1:])
                return replace(claim, routes=(bad,) + claim.routes[1:])
            self.assert_corruption_rejected(corrupt)

    def test_vehicle_compatibility_cannot_be_invented(self):
        def corrupt(tool, state, claim):
            bad = replace(claim.routes[0], vehicles=frozenset({"van", "ambulance"}))
            return replace(claim, routes=(bad,) + claim.routes[1:])
        self.assert_corruption_rejected(corrupt)

    def test_arbitrary_route_identity_is_rejected(self):
        def corrupt(tool, state, claim):
            bad = replace(claim.routes[0], route_id="invented-id")
            return replace(claim, routes=(bad,) + claim.routes[1:])
        self.assert_corruption_rejected(corrupt)

    def test_fractional_edge_metrics_do_not_create_rounding_rejections(self):
        data = fixture()
        graph = data.graphs[0]
        graph = replace(graph, edges=tuple(replace(e, length_m=0.1, travel_time_sec=0.2) for e in graph.edges))
        data = replace(data, graphs=(graph,) + data.graphs[1:])
        registry, _, _ = environment(data=data)
        result = dispatch(registry, Tool.GENERATE)
        self.assertTrue(result.ok)
        self.assertTrue(dispatch(registry, Tool.EVALUATE).ok)

    def test_snapshot_mismatch_rejects(self):
        for field in ("graph_snapshot_id", "hazard_snapshot_id"):
            self.assert_corruption_rejected(lambda tool, state, claim: replace(
                claim, binding=replace(claim.binding, **{field: "outdated"})), FailureKind.STALE_BINDING)

    def test_unknown_and_absent_hazard_are_partial_not_zero(self):
        for missing in (False, True):
            data = fixture()
            hazard = data.hazards[0]
            samples = hazard.samples[1:] if missing else (replace(hazard.samples[0], value=None),) + hazard.samples[1:]
            data = replace(data, hazards=(replace(hazard, samples=samples),))
            registry, _, authority = environment(data=data, hazard=None)
            result = dispatch(registry, Tool.PREPARE_HAZARD)
            self.assertTrue(result.ok)
            self.assertEqual(registry.state.hazard.coverage, Coverage.PARTIAL)
            self.assertIsNone(registry.state.routing_context)
            self.assertNotIn(Tool.GENERATE, options(registry))
            # Even forged COMPLETE metadata cannot satisfy independent checks.
            bad = replace(registry.state, hazard=replace(registry.state.hazard, coverage=Coverage.COMPLETE))
            claim = AlternativesClaim("forged", replace(data_binding(registry), graph_snapshot_id="graph-1"), ())
            with self.assertRaises(ToolRejected) as error:
                authority.verify(Tool.GENERATE, bad, claim)
            self.assertEqual(error.exception.failure.kind, FailureKind.CONTRADICTORY)

    def test_coordinate_system_mismatch_blocks_context_and_generation(self):
        data = fixture()
        for changes in ({"od_crs": "EPSG:4326"},
                        {"hazards": (replace(data.hazards[0], crs="EPSG:4326"),)}):
            registry, _, authority = environment(data=replace(data, **changes))
            self.assertIsNone(registry.state.routing_context)
            self.assertNotIn(Tool.GENERATE, options(registry))
            with self.assertRaises(ToolRejected) as error:
                authority.context(registry.state)
            self.assertEqual(error.exception.failure.code, "coordinate_system_mismatch")

    def test_unknown_vehicle_restrictions_block_routing(self):
        data = fixture()
        graph = data.graphs[0]
        graph = replace(graph, edges=(replace(graph.edges[0], vehicles=None),) + graph.edges[1:])
        registry, _, _ = environment(data=replace(data, graphs=(graph,) + data.graphs[1:]))
        self.assertIsNone(registry.state.routing_context)
        self.assertEqual(options(registry), ())

    def test_existing_gate_rejects_known_incompatible_vehicle(self):
        data = fixture()
        graph = data.graphs[0]
        graph = replace(graph, edges=(replace(graph.edges[0], vehicles=frozenset({"bike"})),) + graph.edges[1:])
        registry, _, _ = environment(data=replace(data, graphs=(graph,) + data.graphs[1:]))
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
        result = dispatch(registry, Tool.EVALUATE)
        self.assertTrue(result.ok)
        ranking = result.update.payload
        self.assertNotIn("route-1", ranking.validation.eligible_route_ids)
        self.assertEqual(ranking.selected_route_id, "route-2")

    def test_unapproved_hard_limit_relaxation_rejected_by_authority(self):
        data = fixture()
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_edge_hazard=0.15)))
        def corrupt(tool, state, claim):
            if tool is Tool.EVALUATE:
                return replace(claim, ranking=replace(claim.ranking, selected_route_id="route-1"))
            return claim
        registry, _, _ = environment(data=data, mutate=corrupt)
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
        result = dispatch(registry, Tool.EVALUATE)
        self.assertEqual(result.failure.kind, FailureKind.CONTRADICTORY)

    def test_no_feasible_candidates_remain_no_feasible_not_relaxed(self):
        data = fixture()
        data = replace(data, request=replace(data.request, hard_constraints=replace(
            data.request.hard_constraints, max_edge_hazard=0.0)))
        registry, _, _ = environment(data=data)
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
        result = dispatch(registry, Tool.EVALUATE)
        self.assertTrue(result.ok)  # Tool succeeded; mission did not obtain a route.
        self.assertEqual(result.update.payload.status, RankingStatus.NO_ELIGIBLE_CANDIDATES)
        self.assertIsNone(result.update.payload.selected_route_id)

    def test_malformed_output_is_rejected_without_state_change(self):
        self.assert_corruption_rejected(lambda *args: {"verification": "verified"}, FailureKind.INVALID_OUTPUT)


def data_binding(registry):
    from src.agentic_orchestration.synthetic_tools import current_binding
    return current_binding(registry.state)


class FaultAndContractTests(unittest.TestCase):
    def test_corrupt_payload_faults_are_caught_by_authority_for_every_tool(self):
        for tool in Tool:
            for kind in (FailureKind.INVALID_OUTPUT, FailureKind.CONTRADICTORY, FailureKind.STALE_BINDING):
                with self.subTest(tool=tool, kind=kind):
                    settings = {"hazard": None} if tool is Tool.PREPARE_HAZARD else (
                        {"graph": "graph-old"} if tool is Tool.PREPARE_NETWORK else {})
                    registry, tools, _ = environment(**settings, faults=(Fault(tool, 1, kind),))
                    if tool is Tool.EVALUATE:
                        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
                    before, count = registry.state, len(tools.calls)
                    result = dispatch(registry, tool)
                    self.assertEqual(result.failure.kind, kind)
                    self.assertFalse(result.failure.code.startswith("injected_"))
                    self.assertEqual(registry.state, before)
                    self.assertEqual(len(tools.calls), count + 1)

    def test_all_requested_failure_classes_have_deterministic_feedback(self):
        for kind in (FailureKind.TRANSIENT, FailureKind.PERMANENT, FailureKind.MISSING_INPUT,
                     FailureKind.UNSUPPORTED, FailureKind.INVALID_OUTPUT, FailureKind.CONTRADICTORY,
                     FailureKind.STALE_BINDING):
            registry, tools, _ = environment(faults=(Fault(Tool.GENERATE, 1, kind),))
            result = dispatch(registry, Tool.GENERATE)
            self.assertFalse(result.ok)
            self.assertEqual(result.failure.kind, kind)
            self.assertEqual(result.next_revision, result.consumed_revision)
            self.assertEqual(len(tools.calls), 1)

    def test_fault_position_is_per_tool_attempt_not_global_step(self):
        registry, tools, _ = environment(hazard=None,
            faults=(Fault(Tool.EVALUATE, 1, FailureKind.PERMANENT),))
        self.assertTrue(dispatch(registry, Tool.PREPARE_HAZARD).ok)
        self.assertTrue(dispatch(registry, Tool.GENERATE).ok)
        result = dispatch(registry, Tool.EVALUATE)
        self.assertEqual(result.failure.kind, FailureKind.PERMANENT)
        self.assertEqual(len(tools.calls), 3)

    def test_immutable_input_validation_rejects_malformed_fixture(self):
        data = fixture()
        for changes in ({"graphs": list(data.graphs)}, {"graphs": data.graphs + (data.graphs[0],)},
                        {"origin": "bad ref"}, {"origin": data.destination}):
            with self.assertRaises(ValueError):
                replace(data, **changes)
        for bad in (True, float("nan"), -1, 1.1):
            with self.assertRaises(ValueError):
                replace(data.hazards[0].samples[0], value=bad)
        with self.assertRaises(ValueError):
            replace(data.graphs[0], edges=data.graphs[0].edges * 2)
        with self.assertRaises(ValueError):
            Fault(Tool.GENERATE, True, FailureKind.TRANSIENT)

    def test_result_branches_and_failure_records_are_strict(self):
        with self.assertRaises(ValueError):
            ToolFailure("transient", "bad")
        with self.assertRaises(ValueError):
            ToolResult("call", Tool.GENERATE, 0, 0, "key", False)
        registry, _, _ = environment()
        result = dispatch(registry, Tool.GENERATE)
        with self.assertRaises(ValueError):
            replace(result, failure=ToolFailure(FailureKind.PERMANENT, "failure"))


if __name__ == "__main__":
    unittest.main()
