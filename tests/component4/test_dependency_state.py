"""M1 synthetic dependency metadata only; no tools, services or model calls."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone, tzinfo
import unittest
from unittest.mock import patch

from src.agentic_orchestration.adaptive_contracts import (
    AlternativeSet, Availability, Coverage, Dependency, DependencyPolicy,
    DependencyState, EligibilityReport, EvidenceStatus, Freshness, MissionPurpose,
    RoutingContext, Snapshot, SnapshotBinding, SourceClass, Tool, ToolAssessment,
    ToolCapability, ToolOption,
)
from src.agentic_orchestration.contracts import VerificationStatus
from src.agentic_orchestration.dependency_state import derive_eligibility


NOW = datetime(2026, 10, 21, 0, tzinfo=timezone.utc)


def evidence(snapshot_id, **changes):
    return replace(EvidenceStatus(
        Availability.AVAILABLE, Freshness.FRESH, Coverage.COMPLETE,
        VerificationStatus.VERIFIED,
        Snapshot(snapshot_id, SourceClass.SYNTHETIC, NOW - timedelta(seconds=30),
                 NOW - timedelta(seconds=60), NOW + timedelta(seconds=60)),
        "independent-fixture-check",
    ), **changes)


def binding(**changes):
    return replace(SnapshotBinding("mission-v1", "policy-v1", "od-v1", "graph-1", "hazard-1"), **changes)


def state(*, with_alternatives=False, **changes):
    alternatives = AlternativeSet(Availability.MISSING)
    if with_alternatives:
        alternatives = AlternativeSet(Availability.AVAILABLE, "routes-1", binding(),
                                      VerificationStatus.VERIFIED, "independent-routes-check")
    return replace(DependencyState(
        "mission-v1", "policy-v1", 3, NOW, MissionPurpose.SYNTHETIC_RESEARCH,
        DependencyPolicy(3600, 300, frozenset({SourceClass.SYNTHETIC, SourceClass.ROAD_NETWORK}),
                         frozenset({SourceClass.SYNTHETIC, SourceClass.OBSERVED_EVENT_DATA})),
        evidence("graph-1"), evidence("hazard-1"),
        tuple(ToolCapability(tool, True, True, True) for tool in Tool),
        "od-v1", RoutingContext(binding(), VerificationStatus.VERIFIED, "independent-od-spatial-vehicle-check"),
        alternatives,
    ), **changes)


def tools(value):
    return tuple(option.tool for option in derive_eligibility(value).options)


def capabilities_without(tool, **changes):
    return tuple(replace(cap, **changes) if cap.tool is tool else cap for cap in state().capabilities)


class DependencyScenarioTests(unittest.TestCase):
    def test_A_verified_compatible_alternatives_need_only_evaluation(self):
        report = derive_eligibility(state(with_alternatives=True))
        self.assertEqual(tuple(o.tool for o in report.options), (Tool.EVALUATE,))
        self.assertEqual(report.required_dependencies, ())
        self.assertEqual(report.blocked_dependencies, ())
        self.assertFalse(report.clarification_required)
        preparation = next(a for a in report.assessments if a.tool is Tool.PREPARE_HAZARD)
        self.assertTrue(preparation.permitted)
        self.assertFalse(preparation.useful)

    def test_B_missing_hazard_exposes_only_supported_hazard_preparation(self):
        value = state(hazard=EvidenceStatus(Availability.MISSING))
        self.assertEqual(tools(value), (Tool.PREPARE_HAZARD,))
        self.assertIn(Dependency.HAZARD, derive_eligibility(value).required_dependencies)

    def test_C_stale_graph_does_not_recompute_fresh_hazard(self):
        graph = evidence("graph-1", freshness=Freshness.STALE,
                         snapshot=Snapshot("graph-1", SourceClass.SYNTHETIC, NOW,
                                           NOW - timedelta(hours=2)))
        self.assertEqual(tools(state(graph=graph)), (Tool.PREPARE_NETWORK,))

    def test_D_both_missing_allow_either_independent_preparation(self):
        value = state(graph=EvidenceStatus(Availability.MISSING), hazard=EvidenceStatus(Availability.MISSING))
        self.assertEqual(tools(value), (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK))
        # Declaration order only serializes options; reversing capability input
        # must neither select a first tool nor alter results.
        self.assertEqual(derive_eligibility(value), derive_eligibility(
            replace(value, capabilities=tuple(reversed(value.capabilities)))))

    def test_E_valid_prerequisites_and_no_alternatives_allow_generation(self):
        self.assertEqual(tools(state()), (Tool.GENERATE,))

    def test_F_unsupported_preparation_is_blocked_with_clarification(self):
        value = state(hazard=EvidenceStatus(Availability.MISSING),
                      capabilities=capabilities_without(Tool.PREPARE_HAZARD, supported=False))
        report = derive_eligibility(value)
        self.assertEqual(report.options, ())
        self.assertEqual(report.blocked_dependencies, (Dependency.HAZARD,))
        self.assertTrue(report.clarification_required)
        assessment = next(a for a in report.assessments if a.tool is Tool.PREPARE_HAZARD)
        self.assertFalse(assessment.permitted)
        self.assertTrue(assessment.useful)
        self.assertIn("unsupported_tool", assessment.blockers)

    def test_G_outdated_alternative_snapshots_allow_regeneration_not_evaluation(self):
        value = state(with_alternatives=True)
        for changes in ({"graph_snapshot_id": "old-graph"}, {"hazard_snapshot_id": "old-hazard"}):
            with self.subTest(changes=changes):
                old = replace(value.alternatives, binding=binding(**changes))
                report = derive_eligibility(replace(value, alternatives=old))
                self.assertEqual(tuple(o.tool for o in report.options), (Tool.GENERATE,))
                self.assertFalse(next(a for a in report.assessments if a.tool is Tool.EVALUATE).permitted)

    def test_H_unverified_partial_unknown_and_rejected_evidence_is_not_usable(self):
        for changes in ({"verification": VerificationStatus.UNVERIFIED},
                        {"verification": VerificationStatus.REJECTED},
                        {"coverage": Coverage.PARTIAL}, {"coverage": Coverage.UNKNOWN},
                        {"freshness": Freshness.UNKNOWN}):
            with self.subTest(changes=changes):
                self.assertEqual(tools(state(hazard=evidence("hazard-1", **changes))), (Tool.PREPARE_HAZARD,))
                self.assertEqual(tools(state(graph=evidence("graph-1", **changes))), (Tool.PREPARE_NETWORK,))

    def test_H_source_class_must_be_explicitly_allowed(self):
        inappropriate = replace(evidence("hazard-1").snapshot, source_class=SourceClass.ROAD_NETWORK)
        self.assertEqual(tools(state(hazard=evidence("hazard-1", snapshot=inappropriate))), (Tool.PREPARE_HAZARD,))

    def test_I_recent_static_build_does_not_supply_missing_observation(self):
        value = state()
        policy = replace(value.policy, hazard_source_classes=frozenset({SourceClass.STATIC_SUSCEPTIBILITY}))
        hazard = evidence("hazard-1", freshness=Freshness.UNKNOWN,
                          snapshot=Snapshot("hazard-1", SourceClass.STATIC_SUSCEPTIBILITY, NOW))
        report = derive_eligibility(replace(value, hazard=hazard, policy=policy))
        self.assertEqual(tuple(o.tool for o in report.options), (Tool.PREPARE_HAZARD,))
        self.assertIsNone(hazard.snapshot.observed_at)
        with self.assertRaises(ValueError):
            replace(hazard, freshness=Freshness.FRESH)

    def test_I_rebuilding_static_artifact_does_not_refresh_old_observation(self):
        value = state()
        policy = replace(value.policy, hazard_source_classes=frozenset({SourceClass.STATIC_SUSCEPTIBILITY}))
        hazard = evidence("hazard-1", freshness=Freshness.STALE, snapshot=Snapshot(
            "hazard-1", SourceClass.STATIC_SUSCEPTIBILITY, NOW, NOW - timedelta(days=1)))
        self.assertEqual(tools(replace(value, hazard=hazard, policy=policy)), (Tool.PREPARE_HAZARD,))
        with self.assertRaises(ValueError):
            replace(value, hazard=replace(hazard, freshness=Freshness.FRESH), policy=policy)

    def test_J_malformed_records_reject_and_conservative_unknowns_block(self):
        with self.assertRaises(ValueError):
            EvidenceStatus(Availability.AVAILABLE)
        with self.assertRaises(ValueError):
            EvidenceStatus(Availability.MISSING, snapshot=evidence("graph-1").snapshot)
        value = state(hazard=evidence("hazard-1", freshness=Freshness.UNKNOWN), capabilities=())
        report = derive_eligibility(value)
        self.assertEqual(report.options, ())
        self.assertIn(Dependency.HAZARD, report.blocked_dependencies)

    def test_at_least_three_states_have_different_meaningful_option_sets(self):
        cases = (state(with_alternatives=True), state(),
                 state(hazard=EvidenceStatus(Availability.MISSING)),
                 state(graph=EvidenceStatus(Availability.MISSING), hazard=EvidenceStatus(Availability.MISSING)))
        self.assertEqual(len({tools(case) for case in cases}), 4)


class DependencyEligibilityTests(unittest.TestCase):
    def test_supported_but_unauthorized_or_unready_tool_is_never_exposed(self):
        for changes, code in (({"authorized": False}, "tool_not_authorized"),
                              ({"inputs_ready": False}, "tool_inputs_not_ready")):
            value = state(hazard=EvidenceStatus(Availability.MISSING),
                          capabilities=capabilities_without(Tool.PREPARE_HAZARD, **changes))
            report = derive_eligibility(value)
            self.assertEqual(report.options, ())
            self.assertIn(code, report.assessments[0].blockers)

    def test_missing_capability_defaults_to_unsupported_not_supported(self):
        self.assertEqual(tools(state(capabilities=())), ())
        report = derive_eligibility(state(with_alternatives=True, capabilities=()))
        self.assertTrue(report.clarification_required)
        self.assertIn("unsupported_tool", report.assessments[-1].blockers)

    def test_missing_and_unverified_routing_context_blocks_generation(self):
        value = state()
        for context in (None, replace(value.routing_context, verification=VerificationStatus.UNVERIFIED),
                        replace(value.routing_context, binding=binding(graph_snapshot_id="old-graph"))):
            report = derive_eligibility(replace(value, routing_context=context))
            self.assertEqual(report.options, ())
            self.assertIn(Dependency.ROUTING_CONTEXT, report.blocked_dependencies)

    def test_verified_existing_alternatives_do_not_require_recreating_routing_context(self):
        self.assertEqual(tools(state(with_alternatives=True, routing_context=None)), (Tool.EVALUATE,))

    def test_missing_od_prevents_generation_and_evaluation(self):
        for with_alternatives in (False, True):
            report = derive_eligibility(state(with_alternatives=with_alternatives, od_ref=None))
            self.assertEqual(report.options, ())
            self.assertTrue(report.clarification_required)
            self.assertIn("od_reference_missing", report.assessments[-1].blockers)

    def test_cross_mission_policy_or_od_alternatives_are_not_reusable(self):
        value = state(with_alternatives=True)
        for changes in ({"mission_ref": "mission-v2"}, {"policy_ref": "policy-v2"}, {"od_ref": "other-od"}):
            old = replace(value.alternatives, binding=binding(**changes))
            self.assertEqual(tools(replace(value, alternatives=old)), (Tool.GENERATE,))

    def test_unverified_alternatives_require_regeneration_or_clarification(self):
        value = state(with_alternatives=True)
        unverified = replace(value.alternatives, verification=VerificationStatus.UNVERIFIED)
        self.assertEqual(tools(replace(value, alternatives=unverified)), (Tool.GENERATE,))
        blocked = replace(value, alternatives=unverified, capabilities=())
        self.assertIn(Dependency.ALTERNATIVES, derive_eligibility(blocked).blocked_dependencies)

    def test_failed_and_unsupported_evidence_never_becomes_zero_or_available(self):
        for availability in (Availability.FAILED, Availability.UNSUPPORTED):
            self.assertEqual(tools(state(hazard=EvidenceStatus(availability))), (Tool.PREPARE_HAZARD,))

    def test_synthetic_evidence_cannot_be_used_in_review_only_purpose(self):
        report = derive_eligibility(state(with_alternatives=True, purpose=MissionPurpose.REVIEW_ONLY))
        self.assertEqual(tuple(o.tool for o in report.options), (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK))
        self.assertNotIn(Tool.EVALUATE, tuple(o.tool for o in report.options))

    def test_no_repair_order_is_inferred_when_other_dependency_is_unsupported(self):
        report = derive_eligibility(state(
            graph=EvidenceStatus(Availability.MISSING), hazard=EvidenceStatus(Availability.MISSING),
            capabilities=capabilities_without(Tool.PREPARE_NETWORK, supported=False)))
        self.assertEqual(tuple(o.tool for o in report.options), (Tool.PREPARE_HAZARD,))
        self.assertEqual(report.blocked_dependencies, (Dependency.GRAPH,))
        self.assertTrue(report.clarification_required)

    def test_option_binding_uses_current_revision_not_a_new_mission(self):
        value = state()
        option, = derive_eligibility(value).options
        self.assertEqual((option.mission_ref, option.policy_ref, option.state_revision),
                         (value.mission_ref, value.policy_ref, 3))
        revised, = derive_eligibility(replace(value, state_revision=4)).options
        self.assertNotEqual(option, revised)

    def test_pure_deterministic_immutable_and_no_io(self):
        value = state()
        before = asdict(value)
        with patch("builtins.open", side_effect=AssertionError("file I/O")), patch(
            "socket.socket", side_effect=AssertionError("network I/O")
        ), patch("time.time", side_effect=AssertionError("wall-clock read")), patch(
            "time.monotonic", side_effect=AssertionError("clock read")
        ):
            first = derive_eligibility(value)
            self.assertEqual(first, derive_eligibility(value))
        self.assertEqual(asdict(value), before)
        self.assertEqual(hash(first), hash(derive_eligibility(value)))
        for obj, name, new in ((value, "state_revision", 4), (value.graph, "freshness", Freshness.STALE),
                              (value.graph.snapshot, "snapshot_id", "changed"),
                              (first.options[0], "tool", Tool.EVALUATE),
                              (first, "options", ())):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, new)


class DependencyValidationTests(unittest.TestCase):
    def test_enums_are_not_coerced_from_strings_or_booleans(self):
        for changes in ({"availability": "available"}, {"freshness": "fresh"},
                        {"coverage": True}, {"verification": "verified"}):
            with self.assertRaises(ValueError):
                evidence("graph-1", **changes)
        for changes in ({"purpose": "synthetic_research"}, {"graph": {}}, {"hazard": None}):
            with self.assertRaises(ValueError):
                state(**changes)

    def test_references_revision_and_nested_metadata_are_strict(self):
        for bad in (None, "", " ", "bad ref", "x\n", 3, "x" * 129):
            with self.assertRaises(ValueError):
                state(mission_ref=bad)
            with self.assertRaises(ValueError):
                Snapshot(bad, SourceClass.SYNTHETIC, NOW)
        for bad in (True, -1, 1.5, "3"):
            with self.assertRaises(ValueError):
                state(state_revision=bad)
        for changes in ({"routing_context": {}}, {"alternatives": []}, {"policy": {}}):
            with self.assertRaises(ValueError):
                state(**changes)

    def test_policy_requires_finite_explicit_ages_and_immutable_enum_allowlists(self):
        original = state().policy
        for bad in (True, -1, float("nan"), float("inf"), "300", 10 ** 1000):
            with self.assertRaises(ValueError):
                replace(original, hazard_max_age_sec=bad)
        for bad in ({SourceClass.SYNTHETIC}, [SourceClass.SYNTHETIC], frozenset({"synthetic"})):
            with self.assertRaises(ValueError):
                replace(original, graph_source_classes=bad)
        policy = replace(original, hazard_source_classes=frozenset())
        report = derive_eligibility(state(policy=policy))
        self.assertEqual(report.options, ())
        self.assertIn("no_approved_source_class", report.assessments[0].blockers)
        self.assertTrue(report.clarification_required)

    def test_verification_requires_host_reference_without_claiming_proof(self):
        with self.assertRaises(ValueError):
            evidence("graph-1", verification_ref=None)
        with self.assertRaises(ValueError):
            RoutingContext(binding(), VerificationStatus.VERIFIED)
        with self.assertRaises(ValueError):
            AlternativeSet(Availability.AVAILABLE, "routes", binding(), VerificationStatus.VERIFIED)

    def test_nonavailable_records_cannot_claim_fresh_complete_or_verified(self):
        for changes in ({"freshness": Freshness.FRESH}, {"coverage": Coverage.COMPLETE},
                        {"verification": VerificationStatus.REJECTED}, {"verification_ref": "check"}):
            with self.assertRaises(ValueError):
                replace(EvidenceStatus(Availability.MISSING), **changes)
        with self.assertRaises(ValueError):
            AlternativeSet(Availability.MISSING, alternatives_ref="routes")

    def test_future_naive_custom_and_invalid_times_reject(self):
        class MutableTimezone(tzinfo):
            def utcoffset(self, dt):
                return timedelta(0)
        for bad in (None, NOW.replace(tzinfo=None), "2026-10-21", NOW.replace(tzinfo=MutableTimezone())):
            with self.assertRaises(ValueError):
                state(as_of=bad)
        with self.assertRaises(ValueError):
            Snapshot("hazard", SourceClass.SYNTHETIC, NOW, NOW + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            state(hazard=evidence("hazard-1", snapshot=Snapshot("hazard-1", SourceClass.SYNTHETIC,
                  NOW + timedelta(seconds=1), NOW - timedelta(seconds=1))))
        with self.assertRaises(ValueError):
            Snapshot("hazard", SourceClass.SYNTHETIC, NOW, NOW, NOW)

    def test_age_limit_is_inclusive_and_expiry_is_exclusive(self):
        for age, freshness, expected in ((300, Freshness.FRESH, (Tool.GENERATE,)),
                                         (300.001, Freshness.STALE, (Tool.PREPARE_HAZARD,))):
            hazard = evidence("hazard-1", freshness=freshness, snapshot=Snapshot(
                "hazard-1", SourceClass.SYNTHETIC, NOW, NOW - timedelta(seconds=age)))
            self.assertEqual(tools(state(hazard=hazard)), expected)
        expired = evidence("hazard-1", freshness=Freshness.STALE, snapshot=Snapshot(
            "hazard-1", SourceClass.SYNTHETIC, NOW, NOW - timedelta(seconds=1), NOW))
        self.assertEqual(tools(state(hazard=expired)), (Tool.PREPARE_HAZARD,))
        with self.assertRaises(ValueError):
            state(hazard=replace(expired, freshness=Freshness.FRESH))

    def test_false_stale_claim_and_not_applicable_observation_reject(self):
        with self.assertRaises(ValueError):
            state(hazard=evidence("hazard-1", freshness=Freshness.STALE))
        with self.assertRaises(ValueError):
            evidence("hazard-1", freshness=Freshness.NOT_APPLICABLE)
        hazard = evidence("hazard-1", freshness=Freshness.NOT_APPLICABLE,
                          snapshot=Snapshot("hazard-1", SourceClass.STATIC_SUSCEPTIBILITY, NOW))
        self.assertEqual(tools(state(hazard=hazard)), (Tool.PREPARE_HAZARD,))

    def test_fixed_offset_timezones_compare_as_instants(self):
        offset_now = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(derive_eligibility(state()), derive_eligibility(state(as_of=offset_now)))

    def test_capabilities_require_unique_immutable_typed_entries(self):
        value = state()
        for bad in (list(value.capabilities), ("generate_alternatives",),
                    value.capabilities + (value.capabilities[0],)):
            with self.assertRaises(ValueError):
                replace(value, capabilities=bad)
        with self.assertRaises(ValueError):
            ToolCapability(Tool.GENERATE, 1, True, True)

    def test_input_type_and_tampered_frozen_records_are_revalidated_without_mutation(self):
        with self.assertRaises(ValueError):
            derive_eligibility({})
        value = state()
        object.__setattr__(value.graph, "coverage", "complete")
        with self.assertRaises(ValueError):
            derive_eligibility(value)
        self.assertEqual(value.graph.coverage, "complete")

    def test_output_contracts_reject_mutable_or_invalid_values(self):
        with self.assertRaises(ValueError):
            ToolOption(Tool.GENERATE, "mission", "policy", True)
        with self.assertRaises(ValueError):
            ToolAssessment(Tool.GENERATE, True, True, ("blocked",))
        with self.assertRaises(ValueError):
            EligibilityReport([], (), (), ())


if __name__ == "__main__":
    unittest.main()
