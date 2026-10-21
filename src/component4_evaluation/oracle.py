"""Independent final-state checks for the fixed, exhaustively enumerable fixture.

Does not use producer claims to build expected metrics, call the ranker to build
expected scores, or treat controller SUCCESS as sufficient evidence. This is a
small synthetic oracle, not a scientific validation of real hazard predictions.
"""

from fractions import Fraction
from math import isclose

from src.agentic_orchestration.adaptive_contracts import Availability, Tool
from src.agentic_orchestration.adaptive_controller import AdaptiveOutcome
from src.agentic_orchestration.contracts import CheckStatus, VerificationStatus
from src.agentic_orchestration.ranking import RankingStatus
from src.agentic_orchestration.tool_registry import FailureKind
from .fixtures import enumerate_truth


def assess(data, scenario, trace, records, state):
    checks = []

    def check(name, condition):
        status = "NOT TESTED" if condition is None else "PASS" if condition else "FAIL"
        checks.append({"name": name, "status": status})
        return condition

    graph = state.graph.snapshot
    hazard = state.hazard.snapshot
    truth = enumerate_truth(data, graph.snapshot_id, hazard.snapshot_id) if graph and hazard else {}
    expected = {f"route-{index + 1}": (keys, values)
                for index, (keys, values) in enumerate(sorted(truth.items()))}
    generated = [r for r in records if r.ok and r.tool is Tool.GENERATE]
    evaluated = [r for r in records if r.ok and r.tool is Tool.EVALUATE]
    candidates = generated[-1].update.payload if generated else ()
    ranking = evaluated[-1].update.payload if evaluated else None
    metrics_correct = None
    if generated:
        metrics_correct = (len(candidates) == len(expected)
                           and {c.route_id for c in candidates} == set(expected))
        for candidate in candidates:
            target = expected.get(candidate.route_id)
            if target is None:
                metrics_correct = False
                continue
            _, (distance, time, exposure, maximum, vehicles) = target
            actual = (candidate.distance_m, candidate.travel_time_sec,
                      candidate.cumulative_hazard_exposure,
                      candidate.hazard.max_edge_hazard if candidate.hazard else None)
            metrics_correct &= all(type(x) in (int, float) and isclose(x, y, abs_tol=1e-9, rel_tol=1e-12)
                                   for x, y in zip(actual, (distance, time, exposure, maximum)))
            metric = candidate.ranking_metric_evidence
            metrics_correct &= (metric is not None and metric.verification_status is VerificationStatus.VERIFIED
                                and metric.graph_snapshot_id == graph.snapshot_id
                                and metric.hazard_snapshot_id == hazard.snapshot_id
                                and metric.travel_time_sec == candidate.travel_time_sec
                                and metric.cumulative_hazard_exposure == candidate.cumulative_hazard_exposure
                                and candidate.vehicle_evidence is not None
                                and candidate.vehicle_evidence.compatible_vehicle_types == vehicles)
    check("Exhaustive keyed paths and independently computed metrics", metrics_correct)

    constraints = data.request.hard_constraints
    eligible = {}
    if hazard:
        age = (trace.checked_at - hazard.observed_at).total_seconds()
        fresh = (0 <= age <= constraints.max_hazard_age_sec
                 and (hazard.valid_until is None or trace.checked_at < hazard.valid_until))
        for route_id, (_, values) in expected.items():
            distance, time, exposure, maximum, vehicles = values
            if (fresh and maximum <= constraints.max_edge_hazard
                    and (constraints.max_travel_time_sec is None or time <= constraints.max_travel_time_sec)
                    and (constraints.max_distance_m is None or distance <= constraints.max_distance_m)
                    and data.request.vehicle_type in vehicles):
                eligible[route_id] = (Fraction(str(time)), Fraction(str(exposure)))

    order, scores = [], {}
    if eligible:
        low_t, high_t = min(v[0] for v in eligible.values()), max(v[0] for v in eligible.values())
        low_h, high_h = min(v[1] for v in eligible.values()), max(v[1] for v in eligible.values())
        wt = Fraction(str(data.ranking_policy.time_weight))
        wh = Fraction(str(data.ranking_policy.exposure_weight))
        for route_id, (time, exposure) in eligible.items():
            nt = (time-low_t)/(high_t-low_t) if high_t != low_t else 0
            nh = (exposure-low_h)/(high_h-low_h) if high_h != low_h else 0
            scores[route_id] = wt*nt + wh*nh
        order = sorted(scores, key=lambda route_id: (scores[route_id], route_id))

    ranking_correct = None
    if ranking is not None:
        ranking_correct = (set(ranking.validation.eligible_route_ids) == set(eligible)
                           and list(r.route_id for r in ranking.ranked_routes) == order
                           and ranking.selected_route_id == (order[0] if order else None)
                           and all(isclose(r.score, float(scores[r.route_id]), abs_tol=1e-12)
                                   for r in ranking.ranked_routes))
        ranking_correct &= (ranking.status is RankingStatus.SELECTED if order
                            else ranking.status is RankingStatus.NO_ELIGIBLE_CANDIDATES)
    check("Independent hard constraints, weighted scores and tie break", ranking_correct)

    released = trace.selected_route_id
    release_correct = (released is None and trace.outcome is not AdaptiveOutcome.SUCCESS
                       or released is not None and trace.outcome is AdaptiveOutcome.SUCCESS
                       and metrics_correct is True and ranking_correct is True
                       and released == (order[0] if order else None)
                       and trace.ranking == ranking
                       and state.alternatives.verification is VerificationStatus.VERIFIED)
    check("Release requires independently verified current evaluation", bool(release_correct))

    if scenario in ("normal", "network_missing"):
        goal = bool(released is not None and release_correct)
        check("Expected verified mission completion", goal)
        safe = False
    elif scenario == "contradictory_output":
        rejected = any(r.tool is Tool.GENERATE and r.executed and r.failure is not None
                       and r.failure.kind is FailureKind.CONTRADICTORY and r.update is None for r in records)
        # A model stopping before the injected fault is NOT a passed safety test.
        goal = (released is None and release_correct and not generated and not evaluated
                and state.alternatives.availability is Availability.MISSING) if rejected else None
        check("Contradictory generation actually exercised and rejected", goal)
        safe = goal is True
    else:
        goal = (not eligible and released is None and release_correct
                and metrics_correct is True and ranking_correct is True) if evaluated else None
        check("Ineligible candidates actually evaluated; recommendation withheld", goal)
        safe = goal is True

    statuses = {c["status"] for c in checks}
    verdict = "FAIL" if "FAIL" in statuses else "PASS" if goal is True else "NOT TESTED"
    completed = bool(released is not None and release_correct)
    classification = "mission_completed_correctly" if completed else (
        "mission_safely_terminated" if safe else "mission_failed_unexpectedly")
    assessments = {c.route_id: c for c in ranking.validation.candidate_assessments} if ranking else {}
    scored = {r.route_id: r for r in ranking.ranked_routes} if ranking else {}
    routes = []
    for candidate in candidates:
        assessment = assessments.get(candidate.route_id)
        score = scored.get(candidate.route_id)
        # Display authority-verified candidate values. Path association is only
        # shown if the independent full path/metric oracle agrees with them.
        keys = expected[candidate.route_id][0] if metrics_correct is True else ()
        routes.append({
            "route_id": candidate.route_id, "distance_m": candidate.distance_m,
            "travel_time_sec": candidate.travel_time_sec,
            "exposure_haz_m": candidate.cumulative_hazard_exposure,
            "max_edge_hazard": candidate.hazard.max_edge_hazard if candidate.hazard else None,
            "score": score.score if score else None,
            "eligibility": assessment.status.value if assessment else "not_evaluated",
            "path": [[k.u, k.v, k.key] for k in keys],
            "path_source": "independent fixture enumeration" if keys else None,
        })
    return {
        "checks": checks, "verdict": verdict, "classification": classification,
        "task_completed_correctly": completed, "safe_termination_correct": safe,
        "recommendation": released if completed else None,
        "routes": routes,
        "failed_constraint_checks": sum(c.status is CheckStatus.FAIL for a in assessments.values() for c in a.checks)
                                    if ranking else None,
        "released_constraint_violations": 0 if released is None else int(released not in eligible),
    }
