"""Exact M2 tested fixture and independent permutation oracle.

Mirrors tests/component4/synthetic_oracles.py; regression tests assert parity.
No production adapter, ranking or safety implementation is replaced.
"""

from dataclasses import replace

from datetime import datetime, timedelta, timezone

from fractions import Fraction

from itertools import permutations

from src.agentic_orchestration.adaptive_contracts import (
    AlternativeSet, Availability, DependencyPolicy, DependencyState,
    EvidenceStatus, MissionPurpose, Snapshot, SourceClass, Tool, ToolCapability,
)

from src.agentic_orchestration.contracts import HardConstraints, MissionRequest, Urgency

from src.agentic_orchestration.ranking import RankingPolicy

from src.agentic_orchestration.synthetic_tools import (
    SYNTHETIC_CRS, EdgeKey, GraphArtifact, HazardArtifact, HazardSample, RoadEdge,
    SyntheticAuthority, SyntheticFixture, SyntheticTools,
)

from src.agentic_orchestration.tool_registry import ToolRegistry, _canonical

import hashlib

import json

from dataclasses import asdict

NOW = datetime(2026, 10, 21, 0, tzinfo=timezone.utc)

def fixture():
    van = frozenset({"van"})
    roads = (
        RoadEdge(EdgeKey("A", "B", "fast"), 100, 10, van),
        RoadEdge(EdgeKey("A", "B", "quiet"), 150, 20, van),
        RoadEdge(EdgeKey("B", "D", "main"), 100, 10, van),
        RoadEdge(EdgeKey("A", "C", "main"), 200, 30, van),
        RoadEdge(EdgeKey("C", "D", "main"), 100, 20, van),
    )
    def snapshot(name, age=30):
        return Snapshot(name, SourceClass.SYNTHETIC, NOW,
                        NOW - timedelta(seconds=age), NOW + timedelta(seconds=600))
    graphs = (GraphArtifact(snapshot("graph-1"), SYNTHETIC_CRS, roads),
              GraphArtifact(snapshot("graph-2", 10), SYNTHETIC_CRS, roads[1:]),
              GraphArtifact(snapshot("graph-old", 7200), SYNTHETIC_CRS, roads))
    samples = tuple(HazardSample(e.identity, h) for e, h in zip(roads, (0.5, 0.1, 0.1, 0.2, 0.2)))
    hazard = HazardArtifact(snapshot("hazard-1"), SYNTHETIC_CRS, samples,
                            frozenset(e.identity for e in roads))
    request = MissionRequest("synthetic-request", "synthetic-mission", "relief_delivery", "van",
                             Urgency.ROUTINE, HardConstraints(0.6, 300, 100, 1000))
    policy = RankingPolicy("synthetic-test-policy", "1.0.0", "relief_delivery", 0.75, 0.25)
    return SyntheticFixture(graphs, (hazard,), request, policy, "A", "D", "od-v1",
                            SYNTHETIC_CRS, "mission-v1", "policy-v1")

def environment(*, data=None, graph="graph-1", hazard="hazard-1", graph_target="graph-1",
                faults=(), supported=frozenset(Tool), capabilities=None):
    data = data or fixture()
    authority = SyntheticAuthority(data, graph_target, "hazard-1")
    state = DependencyState(
        data.mission_ref, data.policy_ref, 0, NOW, MissionPurpose.SYNTHETIC_RESEARCH,
        DependencyPolicy(3600, 300, frozenset({SourceClass.SYNTHETIC}), frozenset({SourceClass.SYNTHETIC})),
        EvidenceStatus(Availability.MISSING), EvidenceStatus(Availability.MISSING),
        tuple(ToolCapability(t, True, True, True) for t in Tool) if capabilities is None else capabilities,
        data.od_ref, alternatives=AlternativeSet(Availability.MISSING))
    if graph:
        state = replace(state, graph=authority.evidence(data.graph(graph), state))
    if hazard:
        state = replace(state, hazard=authority.evidence(data.hazard(hazard), state))
    state = authority.contextualize(state)
    tools = SyntheticTools(data, graph_target, "hazard-1", authority, faults)
    # Registration identity binds all fixed host inputs and producer versions,
    # including preparation targets. Runtime observations are hashed separately.
    identity = hashlib.sha256(json.dumps(_canonical(asdict(data)), sort_keys=True).encode()
                              + graph_target.encode()).hexdigest()
    registry = ToolRegistry(state, tools, authority, supported, registration_id=identity)
    return registry, tools, authority

def enumerate_truth(data, graph_id="graph-1", hazard_id="hazard-1"):
    """Return independent metrics by exact keyed path, never by response route ID."""
    graph = data.graph(graph_id)
    hazard = {s.edge: s.value for s in data.hazard(hazard_id).samples}
    count = len({n for e in graph.edges for n in (e.identity.u, e.identity.v)})
    answers = {}
    for size in range(1, count):
        for path in permutations(graph.edges, size):
            if path[0].identity.u != data.origin or path[-1].identity.v != data.destination:
                continue
            if any(left.identity.v != right.identity.u for left, right in zip(path, path[1:])):
                continue
            nodes = [path[0].identity.u] + [edge.identity.v for edge in path]
            if len(set(nodes)) != len(nodes):
                continue
            keys = tuple(e.identity for e in path)
            values = [hazard.get(k) for k in keys]
            if any(v is None for v in values) or any(e.vehicles is None for e in path):
                continue
            rational = lambda value: Fraction(str(value))
            answers[keys] = (
                float(sum(rational(e.length_m) for e in path)),
                float(sum(rational(e.travel_time_sec) for e in path)),
                float(sum(rational(e.length_m) * rational(h) for e, h in zip(path, values))),
                max(values), frozenset.intersection(*(e.vehicles for e in path)),
            )
    return answers
