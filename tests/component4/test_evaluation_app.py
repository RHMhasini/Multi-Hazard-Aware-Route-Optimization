"""Application integration tests: real controller and authority, no live requests."""

from dataclasses import asdict, replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from src.agentic_orchestration.adaptive_contracts import Tool
from src.agentic_orchestration.adaptive_controller import AdaptiveController
from src.agentic_orchestration.execution import AdaptiveExecutionBudget, AdaptiveLimits, HostExecutionBudget
from src.agentic_orchestration.rule_planner import ActionKind, AdaptiveAction, Reason, RulePlanner
from src.component4_evaluation import fixtures
from src.component4_evaluation.harness import (
    EvaluationError, SCENARIOS, compare_runs, export_result, run_evaluation, _make_planner,
)
from src.component4_evaluation.oracle import assess
import synthetic_oracles


class EvaluationIntegrationTests(unittest.TestCase):
    def offline(self, scenario="normal", **kwargs):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch(
            "src.component4_evaluation.harness.GroqModelClient", side_effect=AssertionError("live client forbidden")
        ):
            return run_evaluation(scenario, **kwargs)

    def test_fixture_and_oracle_match_preexisting_tested_inputs(self):
        self.assertEqual(fixtures.fixture(), synthetic_oracles.fixture())
        for graph in ("graph-1", "graph-2", "graph-old"):
            self.assertEqual(fixtures.enumerate_truth(fixtures.fixture(), graph),
                             synthetic_oracles.enumerate_truth(synthetic_oracles.fixture(), graph))

    def test_normal_runs_actual_controller_with_independent_verified_result(self):
        original = AdaptiveController.run
        calls = []

        def run(controller, run_id):
            calls.append(run_id)
            return original(controller, run_id)

        with patch.object(AdaptiveController, "run", run):
            result = self.offline()
        self.assertEqual(calls, [result.run_id])
        self.assertEqual(result.assessment["verdict"], "PASS")
        self.assertTrue(result.assessment["task_completed_correctly"])
        self.assertFalse(result.assessment["safe_termination_correct"])
        self.assertEqual(result.assessment["recommendation"], "route-1")
        self.assertEqual(result.metrics["decisions"], 5)
        self.assertEqual(result.metrics["independently_verified_tools"], 4)
        self.assertEqual(result.metrics["model_calls"], 0)
        self.assertIsNone(result.metrics["input_tokens"])
        self.assertEqual(result.source, "offline_rule")
        self.assertEqual({d["tool"] for d in result.decisions if d["tool"]}, {t.value for t in Tool})
        self.assertEqual(set(result.decisions[0]["eligible_tools"]),
                         {Tool.PREPARE_HAZARD.value, Tool.PREPARE_NETWORK.value})
        self.assertEqual(result.assessment["routes"][0]["travel_time_sec"], 20)
        self.assertEqual(result.assessment["routes"][0]["exposure_haz_m"], 60)

    def test_dependency_contrast_reuses_hazard_and_changes_actual_choices(self):
        result = self.offline("network_missing")
        self.assertEqual(result.assessment["verdict"], "PASS")
        self.assertEqual(result.decisions[0]["eligible_tools"], [Tool.PREPARE_NETWORK.value])
        self.assertNotIn(Tool.PREPARE_HAZARD.value, [d["tool"] for d in result.decisions])
        self.assertEqual(result.metrics["tool_executions"], 3)
        self.assertTrue(result.decisions[1]["responded_to_changed_evidence"])
        self.assertEqual(result.initial_evidence["hazard"]["snapshot"], result.final_evidence["hazard"]["snapshot"])

    def test_safety_fault_really_executes_and_is_rejected_without_recommendation(self):
        result = self.offline("contradictory_output")
        self.assertEqual(result.assessment["verdict"], "PASS")
        self.assertFalse(result.assessment["task_completed_correctly"])
        self.assertTrue(result.assessment["safe_termination_correct"])
        self.assertEqual(result.metrics["tool_executions"], 1)
        self.assertEqual(result.metrics["rejected_tool_attempts"], 1)
        self.assertEqual(result.metrics["independently_verified_tools"], 0)
        self.assertEqual(result.decisions[0]["failure"], "contradictory_evidence")
        self.assertEqual(result.final_evidence["alternatives"], "missing")
        self.assertIsNone(result.assessment["recommendation"])
        self.assertEqual(result.assessment["routes"], [])
        self.assertIsNone(result.metrics["failed_constraint_checks"])

    def test_no_feasible_route_is_safety_success_not_task_completion(self):
        result = self.offline("no_feasible")
        self.assertEqual(result.assessment["verdict"], "PASS")
        self.assertEqual(result.actual_outcome, "abstained")
        self.assertFalse(result.assessment["task_completed_correctly"])
        self.assertTrue(result.assessment["safe_termination_correct"])
        self.assertIsNone(result.assessment["recommendation"])
        self.assertEqual(len(result.assessment["routes"]), 3)
        self.assertEqual(result.metrics["failed_constraint_checks"], 3)
        self.assertEqual(result.metrics["released_constraint_violations"], 0)
        self.assertEqual(result.mission["constraints"]["max_edge_hazard"], 0)

    def test_different_actual_rule_choices_are_not_replaced_by_hardcoded_sequence(self):
        with patch("src.component4_evaluation.harness._make_planner",
                   return_value=RulePlanner(preparation_first=Tool.PREPARE_NETWORK)):
            result = self.offline()
        self.assertEqual(result.decisions[0]["tool"], Tool.PREPARE_NETWORK.value)
        self.assertEqual(result.decisions[1]["tool"], Tool.PREPARE_HAZARD.value)
        self.assertEqual(result.assessment["verdict"], "PASS")

    def test_invalid_unissued_action_is_blocked_in_real_host(self):
        class InvalidPlanner:
            def choose(self, observation):
                option = observation.options[0]
                return AdaptiveAction(ActionKind.CALL, Reason.HAZARD, replace(option))

        with patch("src.component4_evaluation.harness._make_planner", return_value=InvalidPlanner()):
            result = self.offline()
        self.assertEqual(result.metrics["invalid_actions_blocked"], 1)
        self.assertEqual(result.metrics["tool_executions"], 0)
        self.assertEqual(result.assessment["verdict"], "FAIL")
        self.assertIsNone(result.assessment["recommendation"])

    def test_stopping_before_fault_does_not_pass_untested_safety_case(self):
        class StopPlanner:
            def choose(self, observation):
                return AdaptiveAction(ActionKind.ABSTAIN, Reason.NO_ACTION)

        with patch("src.component4_evaluation.harness._make_planner", return_value=StopPlanner()):
            result = self.offline("contradictory_output")
        self.assertEqual(result.assessment["verdict"], "NOT TESTED")
        self.assertEqual(result.metrics["tool_executions"], 0)
        self.assertFalse(result.assessment["safe_termination_correct"])

    def test_sessions_have_fresh_ledgers_and_no_cross_run_failure_state(self):
        failed = self.offline("contradictory_output")
        first, second = self.offline(), self.offline()
        self.assertEqual(first.assessment["verdict"], second.assessment["verdict"])
        self.assertNotEqual(first.session_scope, second.session_scope)
        self.assertNotEqual(failed.session_scope, first.session_scope)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.metrics["tool_attempts"], 4)
        self.assertEqual(first.metrics["provider_operations"], 3)
        self.assertEqual(first.comparison_key, second.comparison_key)
        self.assertEqual(first.mission["limits"], second.mission["limits"])

    def test_live_mode_requires_explicit_true_before_client_creation(self):
        with patch("src.component4_evaluation.harness.GroqModelClient") as client:
            for authorization in (False, None, 1, "yes"):
                with self.subTest(authorization=authorization), self.assertRaises(EvaluationError):
                    run_evaluation(mode="groq", authorize_live=authorization)
            client.assert_not_called()

    def test_live_wiring_reuses_actual_planner_settings_and_shared_budget_without_call(self):
        budget = AdaptiveExecutionBudget(HostExecutionBudget("test-live-wiring", 3, 120,
            max_model_calls=5), AdaptiveLimits(5, 4, 0, 0, 1))
        with patch("src.component4_evaluation.harness.GroqModelClient") as client:
            planner = _make_planner("groq", budget)
            self.assertTrue(planner.uses_budget(budget))
            self.assertEqual(planner._settings.max_output_tokens, 2048)
            self.assertEqual(planner._settings.temperature, 0)
            self.assertIsNone(planner._settings.seed)
            client.return_value.complete.assert_not_called()
        self.assertEqual(budget.snapshot.host.model_calls, 0)

    def test_report_excludes_private_exception_text_and_callback_cannot_edit_state(self):
        observed = []

        def observer(event):
            observed.append(event["event"])
            event.clear()
            raise RuntimeError("PRIVATE_SENTINEL")

        result = self.offline(observer=observer)
        self.assertEqual(result.assessment["verdict"], "PASS")
        encoded = json.dumps(asdict(result))
        self.assertNotIn("PRIVATE_SENTINEL", encoded)
        self.assertNotIn("Authorization", encoded)
        self.assertNotIn("GROQ_API_KEY", encoded)
        self.assertNotIn("raw_output", encoded)
        self.assertEqual(observed.count("observation"), 5)
        self.assertEqual(observed.count("proposal"), 5)

    def test_setup_error_is_sanitized_without_fabricated_result(self):
        with patch("src.component4_evaluation.harness._make_planner", side_effect=RuntimeError("PRIVATE_SENTINEL")):
            with self.assertRaisesRegex(EvaluationError, "evaluation_unavailable") as raised:
                self.offline()
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))

    def test_untrusted_tool_exception_never_enters_report_or_recommendation(self):
        with patch("src.agentic_orchestration.synthetic_tools.SyntheticTools.invoke",
                   side_effect=RuntimeError("PRIVATE_TOOL_SENTINEL")):
            result = self.offline()
        self.assertEqual(result.assessment["verdict"], "FAIL")
        self.assertIsNone(result.assessment["recommendation"])
        self.assertEqual(result.metrics["rejected_tool_attempts"], 1)
        self.assertNotIn("PRIVATE_TOOL_SENTINEL", json.dumps(asdict(result)))

    def test_comparison_requires_two_real_sources_equivalent_state_and_versions(self):
        a = self.offline()
        b = self.offline("network_missing")
        self.assertEqual(compare_runs(a, None)["status"], "NOT TESTED")
        self.assertEqual(compare_runs(a, a)["status"], "NOT TESTED")
        self.assertEqual(compare_runs(a, b)["status"], "NOT TESTED")
        # Negative contract tests with labelled in-memory test objects; never
        # presented/exported as live evidence and no model request is performed.
        fake_label = replace(b, source="live_groq")
        self.assertEqual(compare_runs(a, fake_label)["status"], "NOT TESTED")
        fake_version = replace(a, source="live_groq", versions=a.versions | {"commit": "different"})
        self.assertEqual(compare_runs(a, fake_version)["status"], "NOT TESTED")

    def test_topology_uses_existing_edges_without_inventing_coordinates(self):
        result = self.offline()
        self.assertFalse(result.graph["geographical_coordinates"])
        expected = fixtures.fixture().graph("graph-1")
        self.assertEqual({(e["u"], e["v"], e["key"]) for e in result.graph["edges"]},
                         {(e.identity.u, e.identity.v, e.identity.key) for e in expected.edges})
        self.assertEqual(result.graph["nodes"], ["A", "B", "C", "D"])
        selected = next(r for r in result.assessment["routes"] if r["route_id"] == result.assessment["recommendation"])
        self.assertEqual(selected["path"], [["A", "B", "fast"], ["B", "D", "main"]])

    def test_all_offline_scenarios_keep_constraints_and_budgets(self):
        for scenario in SCENARIOS:
            result = self.offline(scenario)
            self.assertEqual(result.assessment["verdict"], "PASS")
            self.assertLessEqual(result.metrics["decisions"], 5)
            self.assertLessEqual(result.metrics["tool_attempts"], 4)
            self.assertEqual(result.metrics["model_calls"], 0)
            self.assertEqual(result.metrics["released_constraint_violations"], 0)
            self.assertEqual(result.metrics["non_useful_tool_executions"], 0)
            self.assertEqual(result.metrics["valid_action_rate"], 1)


class IndependentOracleTests(unittest.TestCase):
    def scenario(self):
        data = fixtures.fixture()
        registry, _, _ = fixtures.environment(data=data, graph=None, hazard=None)
        budget = AdaptiveExecutionBudget(HostExecutionBudget("oracle-test", 3, 120),
                                         AdaptiveLimits(5, 4, 0, 0, 1))

        class Clock:
            def now(self):
                return fixtures.NOW

        trace = AdaptiveController(registry, budget, RulePlanner(), clock=Clock()).run("oracle-run")
        return data, registry, trace

    def test_success_label_alone_does_not_pass_without_verified_artifacts(self):
        data, registry, trace = self.scenario()
        result = assess(data, "normal", trace, (), registry.state)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIsNone(result["recommendation"])

    def test_altered_verified_candidate_metrics_are_caught_independently(self):
        data, registry, trace = self.scenario()
        records = list(registry.records)
        index = next(i for i, record in enumerate(records) if record.tool is Tool.GENERATE)
        record = records[index]
        candidates = record.update.payload
        candidates = (replace(candidates[0], travel_time_sec=1),) + candidates[1:]
        records[index] = replace(record, update=replace(record.update, payload=candidates))
        result = assess(data, "normal", trace, tuple(records), registry.state)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["routes"][0]["path"], [])

    def test_wrong_selected_route_cannot_be_presented_as_verified(self):
        data, registry, trace = self.scenario()
        forged = replace(trace, ranking=replace(trace.ranking, selected_route_id="route-3"))
        result = assess(data, "normal", forged, registry.records, registry.state)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIsNone(result["recommendation"])


class EvaluationExportTests(unittest.TestCase):
    def test_json_export_contains_safe_real_evidence_with_null_missing_data(self):
        with tempfile.TemporaryDirectory(prefix="c4-eval-", dir="D:\\") as directory:
            result = run_evaluation("contradictory_output")
            path = Path(directory) / "evaluation.json"
            export_result(result, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["source"], "offline_rule")
            self.assertEqual(data["assessment"]["verdict"], "PASS")
            self.assertIsNone(data["metrics"]["input_tokens"])
            self.assertIsNone(data["metrics"]["failed_constraint_checks"])
            self.assertIsNone(data["assessment"]["recommendation"])
            self.assertIn("commit", data["versions"])
            self.assertNotIn("raw_model_response", path.read_text())
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["evaluation.json"])

    def test_export_rejects_repository_relative_and_c_drive_paths(self):
        result = run_evaluation()
        for path in ("output.json", "C:\\evaluation.json", Path.cwd() / "evaluation.json"):
            with self.subTest(path=path), self.assertRaises(EvaluationError):
                export_result(result, path)
        self.assertFalse((Path.cwd() / "evaluation.json").exists())


class DesktopIntegrationTests(unittest.TestCase):
    def test_real_run_button_populates_all_offline_scenarios_and_live_denial_makes_no_call(self):
        import tkinter as tk
        from time import monotonic, sleep
        from src.component4_evaluation.desktop import EvaluationApp

        root = tk.Tk()
        root.withdraw()
        app = EvaluationApp(root)
        try:
            with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch(
                "src.component4_evaluation.harness.GroqModelClient", side_effect=AssertionError("live client forbidden")
            ):
                for scenario in SCENARIOS:
                    app.scenario.set(SCENARIOS[scenario][0])
                    app.run_button.invoke()
                    deadline = monotonic() + 10
                    while app.running and monotonic() < deadline:
                        root.update()
                        sleep(.01)
                    self.assertFalse(app.running)
                    self.assertIsNotNone(app.result)
                    self.assertEqual(app.result.scenario_id, scenario)
                    self.assertEqual(app.result.assessment["verdict"], "PASS")
                    self.assertEqual(len(app.decision_table.get_children()), len(app.result.decisions))
                    self.assertEqual(len(app.route_table.get_children()), len(app.result.assessment["routes"]))
                    self.assertIn("OFFLINE_RULE", app.status.get())
                    self.assertIn("NOT TESTED", app.compare_text.get("1.0", "end"))
                previous = app.result
                app.mode.set("Live Groq | openai/gpt-oss-20b")
                with patch("src.component4_evaluation.desktop.messagebox.askyesno", return_value=False) as confirm:
                    app.run_button.invoke()
                confirm.assert_called_once()
                self.assertIs(app.result, previous)
                self.assertFalse(app.running)
        finally:
            app.close()


class RoutePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            cls.normal = run_evaluation()

    def setUp(self):
        import tkinter as tk
        from src.component4_evaluation.desktop import EvaluationApp
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = EvaluationApp(self.root)
        self.addCleanup(self.app.close)

    def render(self, result=None):
        for table in (self.app.decision_table, self.app.route_table, self.app.checks):
            table.delete(*table.get_children())
        self.app.show_result(result or self.normal)

    def highlighted(self):
        # Inspect actual drawn LINE items rather than trusting a renderer helper.
        indices = []
        for item in self.app.canvas.find_withtag("recommended_edge"):
            self.assertEqual(self.app.canvas.type(item), "line")
            self.assertEqual(self.app.canvas.itemcget(item, "fill"), "#087f5b")
            tag = next(t for t in self.app.canvas.gettags(item) if t.startswith("edge-"))
            indices.append(int(tag.removeprefix("edge-")))
        return [self.app.result.graph["edges"][i] for i in indices]

    def test_highlight_matches_independent_keyed_path_not_just_node_pair(self):
        self.render()
        data = synthetic_oracles.fixture()
        paths = sorted(synthetic_oracles.enumerate_truth(data))
        expected = paths[0]  # Canonical route-1 mapping also enforced by authority.
        actual = {(e["u"], e["v"], e["key"]) for e in self.highlighted()}
        self.assertEqual(actual, {(e.u, e.v, e.key) for e in expected})
        self.assertEqual(actual, {("A", "B", "fast"), ("B", "D", "main")})
        self.assertNotIn(("A", "B", "quiet"), actual)
        self.assertNotIn(("A", "C", "main"), actual)

    def test_actual_canvas_edge_text_matches_authoritative_time_and_distance(self):
        import re
        self.render()
        source = {(e.identity.u, e.identity.v, e.identity.key): e
                  for e in synthetic_oracles.fixture().graph("graph-1").edges}
        labels = self.app.canvas.find_withtag("edge_label")
        self.assertEqual(len(labels), len(source))
        for item in labels:
            text = self.app.canvas.itemcget(item, "text")
            match = re.fullmatch(r"(\w+) -> (\w+) \[(\w+)\]\n([0-9.]+) s \| ([0-9.]+) m", text)
            self.assertIsNotNone(match, text)
            edge = source[tuple(match.group(i) for i in (1, 2, 3))]
            self.assertEqual(float(match.group(4)), edge.travel_time_sec)
            self.assertEqual(float(match.group(5)), edge.length_m)

    def test_highlighted_source_sums_match_all_verified_route_metrics_and_table(self):
        from fractions import Fraction
        self.render()
        data = synthetic_oracles.fixture()
        source = {(e.identity.u, e.identity.v, e.identity.key): e for e in data.graph("graph-1").edges}
        hazards = {(s.edge.u, s.edge.v, s.edge.key): s.value for s in data.hazard("hazard-1").samples}
        keys = [(e["u"], e["v"], e["key"]) for e in self.highlighted()]
        values = [source[key] for key in keys]
        # Fraction sums use original fixture records, not the display projection.
        time = sum(Fraction(str(edge.travel_time_sec)) for edge in values)
        distance = sum(Fraction(str(edge.length_m)) for edge in values)
        exposure = sum(Fraction(str(source[key].length_m))*Fraction(str(hazards[key])) for key in keys)
        maximum = max(hazards[key] for key in keys)
        selected = next(r for r in self.normal.assessment["routes"] if r["route_id"] == self.normal.assessment["recommendation"])
        self.assertEqual((time, distance, exposure, maximum), (20, 200, 60, .5))
        self.assertEqual((selected["travel_time_sec"], selected["distance_m"], selected["exposure_haz_m"], selected["max_edge_hazard"]),
                         (float(time), float(distance), float(exposure), maximum))
        recommended = [self.app.route_table.item(item, "values") for item in self.app.route_table.get_children()
                       if "recommended" in self.app.route_table.item(item, "tags")]
        self.assertEqual(len(recommended), 1)
        self.assertEqual(recommended[0][0:2], ("RECOMMENDED", self.normal.assessment["recommendation"]))
        self.assertEqual(tuple(map(float, recommended[0][2:6])), (200, 20, 60, .5))
        text = self.app.route_explanation.get()
        self.assertIn("10 + 10 = 20.0 s", text)
        self.assertIn("100 + 100 = 200.0 m", text)
        self.assertIn("(100 x 0.5) + (100 x 0.1) = 60.0", text)
        self.assertIn("max(0.5, 0.1) = 0.5", text)

    def test_parallel_edges_have_distinct_actual_canvas_lanes_and_labels(self):
        self.render()
        parallel = [(index, edge) for index, edge in enumerate(self.normal.graph["edges"])
                    if (edge["u"], edge["v"]) == ("A", "B")]
        self.assertEqual({e["key"] for _, e in parallel}, {"fast", "quiet"})
        lines = []
        for index, edge in parallel:
            items = self.app.canvas.find_withtag(f"edge-{index}")
            line = next(item for item in items if self.app.canvas.type(item) == "line")
            label = next(item for item in items if self.app.canvas.type(item) == "text")
            self.assertIn(f"[{edge['key']}]", self.app.canvas.itemcget(label, "text"))
            lines.append(self.app.canvas.coords(line))
        self.assertNotEqual(lines[0], lines[1])

    def test_edge_label_rectangles_do_not_overlap_at_presentation_sizes(self):
        self.render()
        for width, height in ((700, 260), (900, 310), (1150, 400)):
            with self.subTest(width=width, height=height), patch.object(
                self.app.canvas, "winfo_width", return_value=width
            ), patch.object(self.app.canvas, "winfo_height", return_value=height):
                self.app.draw_graph()
                boxes = [self.app.canvas.bbox(i) for i in self.app.canvas.find_withtag("edge_label_background")]
                for index, a in enumerate(boxes):
                    for b in boxes[index+1:]:
                        self.assertFalse(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1], (a, b))
                labels = " ".join(self.app.canvas.itemcget(i, "text") for i in self.app.canvas.find_all()
                                  if self.app.canvas.type(i) == "text")
                self.assertIn("not geographic geometry", labels)

    def test_exact_tie_and_tradeoff_explanation_match_real_ranking(self):
        from src.component4_evaluation.desktop import recommendation_text
        text = recommendation_text(self.normal)
        rows = self.normal.assessment["routes"]
        first, second = rows[0], rows[1]
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(self.normal.assessment["recommendation"], min(first["route_id"], second["route_id"]))
        self.assertIn("Exact score tie: route-1, route-2 = 0.25", text)
        self.assertIn("smaller route ID (Unicode order)", text)
        self.assertIn("10.0 s faster", text)
        self.assertIn("35.0 hazard-metres more exposure", text)

    def test_near_tie_is_not_an_exact_tie_and_scores_keep_round_trip_precision(self):
        from copy import deepcopy
        from math import nextafter
        from src.component4_evaluation.desktop import metric_text, recommendation_text
        score = .25
        near = nextafter(score, 1)
        self.assertNotEqual(metric_text(score), metric_text(near))
        assessment = deepcopy(self.normal.assessment)
        assessment["routes"][1]["score"] = near
        result = replace(self.normal, assessment=assessment)
        self.assertIn("No exact score tie", recommendation_text(result))
        self.render(result)
        values = [self.app.route_table.item(item, "values")[-1] for item in self.app.route_table.get_children()]
        self.assertEqual(float(values[0]), score)
        self.assertEqual(float(values[1]), near)
        self.assertNotEqual(values[0], values[1])

    def test_invalid_or_missing_metrics_do_not_become_zero_or_green_recommendations(self):
        from copy import deepcopy
        from src.component4_evaluation.desktop import metric_text, recommended_path
        for value in (None, float("nan"), float("inf"), -1, True, "0"):
            self.assertEqual(metric_text(value), "NOT AVAILABLE")
        self.assertEqual(metric_text(0), "0")  # A genuine measured zero stays zero.
        for field in ("travel_time_sec", "distance_m", "exposure_haz_m", "max_edge_hazard", "score"):
            with self.subTest(field=field):
                assessment = deepcopy(self.normal.assessment)
                assessment["routes"][0][field] = None
                result = replace(self.normal, assessment=assessment)
                self.assertEqual(recommended_path(result), (None, []))
                self.render(result)
                self.assertEqual(self.highlighted(), [])
                values = self.app.route_table.item(self.app.route_table.get_children()[0], "values")
                self.assertIn("NOT AVAILABLE", values)
                self.assertNotIn("RECOMMENDED", values)

    def test_inconsistent_projected_edge_time_is_not_highlighted(self):
        from copy import deepcopy
        from src.component4_evaluation.desktop import recommended_path
        graph = deepcopy(self.normal.graph)
        edge = next(e for e in graph["edges"] if (e["u"], e["v"], e["key"]) == ("B", "D", "main"))
        edge["travel_time_sec"] = 30
        result = replace(self.normal, graph=graph)
        self.assertEqual(recommended_path(result), (None, []))
        self.render(result)
        self.assertEqual(self.highlighted(), [])
        self.assertIn("inconsistent", self.app.route_explanation.get())


if __name__ == "__main__":
    unittest.main()
