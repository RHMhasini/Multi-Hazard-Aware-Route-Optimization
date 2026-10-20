"""Bounded, explicit M2 fixtures, untrusted tools and separate fixture authority.

No GIS, files, wall clock, source downloads or external services. Hazard samples
are keyed-edge fixture values, not a raster processor or calibrated probability.
CRS is an explicitly shared synthetic metre plane; no transformation is inferred.
All times, versions, permissions and faults come from trusted fixture setup.
"""

from dataclasses import dataclass, replace
from math import fsum

from .adaptive_contracts import (
    AlternativeSet, Availability, Coverage, DependencyState, EvidenceStatus,
    Freshness, MissionPurpose, RoutingContext, Snapshot, SnapshotBinding,
    SourceClass, Tool, _fresh_at, _ref,
)
from .contracts import (
    HazardEvidence, HazardStatus, MissionRequest, RankingMetricEvidence,
    RouteCandidate, VehicleEvidence, VerificationStatus, finite_number,
)
from .ranking import RankingPolicy, RankingResult, rank_candidates, validate_mission_policy
from .snapshots import immutable_snapshot
from .tool_registry import FailureKind, ToolRejected, VerifiedUpdate


SYNTHETIC_CRS = "SYNTHETIC:METRES"


def _tuple(value, kind, maximum):
    if type(value) is not tuple or len(value) > maximum or any(type(x) is not kind for x in value):
        raise ValueError("Expected bounded immutable typed tuple")
    for item in value:
        item.__post_init__()


@dataclass(frozen=True, slots=True, order=True)
class EdgeKey:
    u: str
    v: str
    key: str

    def __post_init__(self):
        for item in (self.u, self.v, self.key):
            _ref(item)
        if self.u == self.v:
            raise ValueError("Self loops are outside the small simple-path fixture")


@dataclass(frozen=True, slots=True)
class RoadEdge:
    identity: EdgeKey
    length_m: float
    travel_time_sec: float
    vehicles: frozenset[str] | None

    def __post_init__(self):
        if type(self.identity) is not EdgeKey:
            raise ValueError("Expected exact keyed directed edge")
        self.identity.__post_init__()
        for number in (self.length_m, self.travel_time_sec):
            if type(number) not in (int, float) or not finite_number(number) or number <= 0:
                raise ValueError("Edge metrics must be finite and positive")
        if self.vehicles is not None:
            if type(self.vehicles) is not frozenset:
                raise ValueError("Restrictions must be immutable or explicitly unknown")
            for vehicle in self.vehicles:
                _ref(vehicle)


@dataclass(frozen=True, slots=True)
class GraphArtifact:
    snapshot: Snapshot
    crs: str
    edges: tuple[RoadEdge, ...]

    def __post_init__(self):
        if type(self.snapshot) is not Snapshot or self.snapshot.source_class is not SourceClass.SYNTHETIC:
            raise ValueError("Only labelled synthetic snapshots are supported")
        self.snapshot.__post_init__()
        _ref(self.crs)
        _tuple(self.edges, RoadEdge, 16)
        keys = tuple(e.identity for e in self.edges)
        if len(set(keys)) != len(keys) or len({n for k in keys for n in (k.u, k.v)}) > 6:
            raise ValueError("Duplicate edge or oversized graph")


@dataclass(frozen=True, slots=True)
class HazardSample:
    edge: EdgeKey
    value: float | None

    def __post_init__(self):
        if type(self.edge) is not EdgeKey:
            raise ValueError("Expected edge key")
        self.edge.__post_init__()
        if self.value is not None and (type(self.value) not in (int, float)
                or not finite_number(self.value) or not 0 <= self.value <= 1):
            raise ValueError("Hazard must be in [0,1] or explicit NoData")


@dataclass(frozen=True, slots=True)
class HazardArtifact:
    snapshot: Snapshot
    crs: str
    samples: tuple[HazardSample, ...]
    # Coverage domain is explicit and independent of whichever graph is active.
    coverage_edges: frozenset[EdgeKey]

    def __post_init__(self):
        if type(self.snapshot) is not Snapshot or self.snapshot.source_class is not SourceClass.SYNTHETIC:
            raise ValueError("Only labelled synthetic snapshots are supported")
        self.snapshot.__post_init__()
        _ref(self.crs)
        _tuple(self.samples, HazardSample, 16)
        keys = tuple(s.edge for s in self.samples)
        if (len(set(keys)) != len(keys) or type(self.coverage_edges) is not frozenset
                or any(type(k) is not EdgeKey for k in self.coverage_edges)
                or len(self.coverage_edges) > 16 or not set(keys) <= self.coverage_edges):
            raise ValueError("Invalid hazard coverage domain")
        for key in self.coverage_edges:
            key.__post_init__()


@dataclass(frozen=True, slots=True)
class SyntheticFixture:
    """Trusted fixture authority inputs, held separately from adapter claims.

    Version catalogs are immutable; the host selects a supported preparation
    target explicitly. New versions never overwrite artifacts under old IDs.
    mission_ref/policy_ref/OD bindings are supplied by the initial M1 state.
    """

    graphs: tuple[GraphArtifact, ...]
    hazards: tuple[HazardArtifact, ...]
    request: MissionRequest
    ranking_policy: RankingPolicy
    origin: str
    destination: str
    od_ref: str
    od_crs: str
    mission_ref: str
    policy_ref: str

    def __post_init__(self):
        _tuple(self.graphs, GraphArtifact, 8)
        _tuple(self.hazards, HazardArtifact, 8)
        ids = [a.snapshot.snapshot_id for a in self.graphs + self.hazards]
        if not self.graphs or not self.hazards or len(set(ids)) != len(ids):
            raise ValueError("Fixture catalogs need unique immutable snapshot IDs")
        for ref in (self.origin, self.destination, self.od_ref, self.od_crs,
                    self.mission_ref, self.policy_ref):
            _ref(ref)
        if self.origin == self.destination:
            raise ValueError("Fixture OD must be distinct")
        if type(self.request) is not MissionRequest or type(self.ranking_policy) is not RankingPolicy:
            raise ValueError("Expected existing mission and ranking contracts")
        self.request.__post_init__()
        self.request.hard_constraints.__post_init__()
        self.ranking_policy.__post_init__()
        validate_mission_policy(self.request, self.ranking_policy)
        if (immutable_snapshot(self.request) != self.request
                or immutable_snapshot(self.ranking_policy) != self.ranking_policy):
            raise ValueError("Fixture must use immutable existing contracts")

    def graph(self, snapshot_id):
        return next((a for a in self.graphs if a.snapshot.snapshot_id == snapshot_id), None)

    def hazard(self, snapshot_id):
        return next((a for a in self.hazards if a.snapshot.snapshot_id == snapshot_id), None)


@dataclass(frozen=True, slots=True)
class PreparedClaim:
    artifact: GraphArtifact | HazardArtifact
    # A malicious adapter can set this; the authority deliberately ignores it.
    claimed_verification: VerificationStatus = VerificationStatus.UNVERIFIED

    def __post_init__(self):
        if type(self.artifact) not in (GraphArtifact, HazardArtifact):
            raise ValueError("Expected synthetic artifact")
        self.artifact.__post_init__()
        if type(self.claimed_verification) is not VerificationStatus:
            raise ValueError("Expected verification enum")


@dataclass(frozen=True, slots=True)
class RouteClaim:
    route_id: str
    edges: tuple[EdgeKey, ...]
    distance_m: float
    travel_time_sec: float
    exposure_haz_m: float
    max_hazard: float
    vehicles: frozenset[str]

    def __post_init__(self):
        _ref(self.route_id)
        _tuple(self.edges, EdgeKey, 5)
        if not self.edges:
            raise ValueError("Expected nonempty route")
        for number in (self.distance_m, self.travel_time_sec, self.exposure_haz_m, self.max_hazard):
            if type(number) not in (int, float) or not finite_number(number) or number < 0:
                raise ValueError("Invalid route metric")
        if self.max_hazard > 1 or type(self.vehicles) is not frozenset:
            raise ValueError("Invalid route evidence")
        for vehicle in self.vehicles:
            _ref(vehicle)


@dataclass(frozen=True, slots=True)
class AlternativesClaim:
    artifact_ref: str
    binding: SnapshotBinding
    routes: tuple[RouteClaim, ...]
    source_class: SourceClass = SourceClass.SYNTHETIC
    claimed_verification: VerificationStatus = VerificationStatus.UNVERIFIED

    def __post_init__(self):
        _ref(self.artifact_ref)
        if type(self.binding) is not SnapshotBinding:
            raise ValueError("Expected binding")
        self.binding.__post_init__()
        _tuple(self.routes, RouteClaim, 128)
        if len({r.route_id for r in self.routes}) != len(self.routes):
            raise ValueError("Duplicate route identity")
        if self.source_class is not SourceClass.SYNTHETIC or type(self.claimed_verification) is not VerificationStatus:
            raise ValueError("Invalid synthetic claim metadata")


@dataclass(frozen=True, slots=True)
class EvaluationClaim:
    alternatives_ref: str
    binding: SnapshotBinding
    ranking: RankingResult

    def __post_init__(self):
        _ref(self.alternatives_ref)
        if type(self.binding) is not SnapshotBinding or type(self.ranking) is not RankingResult:
            raise ValueError("Expected binding and existing ranking result")
        self.binding.__post_init__()
        if immutable_snapshot(self.ranking) != self.ranking:
            raise ValueError("Ranking must contain immutable existing records")


def current_binding(state):
    if state.od_ref is None or state.graph.snapshot is None or state.hazard.snapshot is None:
        raise ToolRejected(FailureKind.MISSING_INPUT, "snapshot_or_od_missing")
    return SnapshotBinding(state.mission_ref, state.policy_ref, state.od_ref,
                           state.graph.snapshot.snapshot_id, state.hazard.snapshot.snapshot_id)


def _artifact_ref(binding):
    # All inputs are bounded host IDs, never user paths. Fixed-size content identity.
    import hashlib
    return "synthetic-routes-" + hashlib.sha256(repr(binding).encode()).hexdigest()[:24]


class SyntheticAuthority:
    """Fixture-backed verification and existing v1 verifier implementations.

    Recomputes evidence from fixed graph/hazard records, never from claim metrics.
    Verified candidate records are registered only after complete claim checking.
    Independent test enumeration additionally checks path/metric correctness.
    """

    def __init__(self, fixture: SyntheticFixture, graph_target: str, hazard_target: str):
        if type(fixture) is not SyntheticFixture:
            raise ValueError("Expected fixture")
        fixture.__post_init__()
        if fixture.graph(graph_target) is None or fixture.hazard(hazard_target) is None:
            raise ValueError("Preparation target must be a catalog member")
        self.fixture = fixture
        self.graph_target, self.hazard_target = graph_target, hazard_target
        self._alternatives = {}
        self._metrics = {}

    def _check_scope(self, state):
        if ((state.mission_ref, state.policy_ref, state.od_ref)
                != (self.fixture.mission_ref, self.fixture.policy_ref, self.fixture.od_ref)
                or state.purpose is not MissionPurpose.SYNTHETIC_RESEARCH):
            raise ToolRejected(FailureKind.CONTRADICTORY, "fixture_scope_mismatch")

    def preflight(self, tool, state):
        """Authoritative input checks before a producer is invoked, not just after."""
        self._check_scope(state)
        if tool in (Tool.GENERATE, Tool.EVALUATE):
            self.context(state)
        if tool is Tool.EVALUATE:
            stored = self._alternatives.get(state.alternatives.alternatives_ref)
            if stored is None or stored[0] != current_binding(state):
                raise ToolRejected(FailureKind.STALE_BINDING, "alternatives_not_authoritative")

    def evidence(self, artifact, state):
        """Host summary of a catalog artifact; no adapter assertion is promoted."""
        snapshot = artifact.snapshot
        is_graph = type(artifact) is GraphArtifact
        catalog = self.fixture.graph(snapshot.snapshot_id) if is_graph else self.fixture.hazard(snapshot.snapshot_id)
        if catalog != artifact:
            raise ToolRejected(FailureKind.CONTRADICTORY, "artifact_not_authoritative")
        if is_graph:
            coverage = Coverage.COMPLETE
            max_age = state.policy.graph_max_age_sec
        else:
            values = {s.edge: s.value for s in artifact.samples}
            coverage = (Coverage.COMPLETE if artifact.coverage_edges and all(
                key in values and values[key] is not None for key in artifact.coverage_edges)
                else Coverage.PARTIAL)
            max_age = state.policy.hazard_max_age_sec
        freshness = (Freshness.UNKNOWN if snapshot.observed_at is None else
                     Freshness.FRESH if _fresh_at(snapshot, state.as_of, max_age) else Freshness.STALE)
        return EvidenceStatus(Availability.AVAILABLE, freshness, coverage,
                              VerificationStatus.VERIFIED, snapshot, "synthetic-authority-v1")

    def context(self, state):
        """Independently check the synthetic OD/CRS/coverage/restriction binding."""
        self._check_scope(state)
        binding = current_binding(state)
        graph, hazard = self.fixture.graph(binding.graph_snapshot_id), self.fixture.hazard(binding.hazard_snapshot_id)
        if graph is None or hazard is None:
            raise ToolRejected(FailureKind.STALE_BINDING, "snapshot_not_in_catalog")
        # Summary fields themselves must also match independently held artifacts.
        if state.graph != self.evidence(graph, state) or state.hazard != self.evidence(hazard, state):
            raise ToolRejected(FailureKind.CONTRADICTORY, "dependency_summary_mismatch")
        if graph.crs != SYNTHETIC_CRS or hazard.crs != graph.crs or self.fixture.od_crs != graph.crs:
            raise ToolRejected(FailureKind.CONTRADICTORY, "coordinate_system_mismatch")
        nodes = {n for e in graph.edges for n in (e.identity.u, e.identity.v)}
        if self.fixture.origin not in nodes or self.fixture.destination not in nodes:
            raise ToolRejected(FailureKind.MISSING_INPUT, "od_not_in_graph")
        values = {s.edge: s.value for s in hazard.samples}
        if any(e.identity not in hazard.coverage_edges or values.get(e.identity) is None for e in graph.edges):
            raise ToolRejected(FailureKind.MISSING_INPUT, "hazard_coverage_missing_or_unknown")
        if any(e.vehicles is None for e in graph.edges):
            raise ToolRejected(FailureKind.MISSING_INPUT, "vehicle_restrictions_unknown")
        return RoutingContext(binding, VerificationStatus.VERIFIED, "synthetic-spatial-vehicle-v1")

    def contextualize(self, state):
        if state.graph.snapshot is None or state.hazard.snapshot is None:
            return replace(state, routing_context=None)
        try:
            context = self.context(state)
        except ToolRejected:
            context = None
        return replace(state, routing_context=context)

    def recompute_route(self, route_id, keys, binding):
        graph, hazard = self.fixture.graph(binding.graph_snapshot_id), self.fixture.hazard(binding.hazard_snapshot_id)
        if graph is None or hazard is None:
            raise ToolRejected(FailureKind.STALE_BINDING, "route_snapshot_missing")
        roads = {e.identity: e for e in graph.edges}
        values = {s.edge: s.value for s in hazard.samples}
        node = self.fixture.origin
        visited = {node}
        edges, hazards = [], []
        for key in keys:
            edge = roads.get(key)
            if edge is None or key.u != node or key.v in visited:
                raise ToolRejected(FailureKind.CONTRADICTORY, "invalid_keyed_directed_path")
            if key not in hazard.coverage_edges or values.get(key) is None:
                raise ToolRejected(FailureKind.MISSING_INPUT, "route_hazard_unknown")
            if edge.vehicles is None:
                raise ToolRejected(FailureKind.MISSING_INPUT, "vehicle_restrictions_unknown")
            edges.append(edge)
            hazards.append(values[key])
            node = key.v
            visited.add(node)
        if not edges or node != self.fixture.destination:
            raise ToolRejected(FailureKind.CONTRADICTORY, "route_od_mismatch")
        vehicles = frozenset.intersection(*(e.vehicles for e in edges))
        return RouteClaim(route_id, keys, fsum(e.length_m for e in edges),
                          fsum(e.travel_time_sec for e in edges),
                          fsum(e.length_m * h for e, h in zip(edges, hazards)), max(hazards), vehicles)

    def _candidate(self, route, binding, artifact_ref):
        hazard = self.fixture.hazard(binding.hazard_snapshot_id)
        metric = RankingMetricEvidence(
            route.route_id, binding.graph_snapshot_id, binding.hazard_snapshot_id,
            artifact_ref + ":" + route.route_id, route.travel_time_sec, route.exposure_haz_m,
            "s", "hazard*m", "synthetic-edge-time/1", "synthetic-length-exposure/1",
            "synthetic-authority-v1", VerificationStatus.VERIFIED)
        return RouteCandidate(
            route.route_id,
            HazardEvidence(HazardStatus.KNOWN, route.max_hazard, hazard.snapshot.observed_at,
                           hazard.snapshot.valid_until, True, route.route_id,
                           binding.graph_snapshot_id, binding.hazard_snapshot_id,
                           "synthetic-authority-v1", VerificationStatus.VERIFIED),
            travel_time_sec=route.travel_time_sec, distance_m=route.distance_m,
            vehicle_evidence=VehicleEvidence(route.vehicles, route.route_id, binding.graph_snapshot_id,
                                            "synthetic-authority-v1", VerificationStatus.VERIFIED),
            cumulative_hazard_exposure=route.exposure_haz_m, ranking_metric_evidence=metric)

    def verify_hazard(self, evidence):
        return any(evidence == c.hazard for _, cs in self._alternatives.values() for c in cs)

    def verify_vehicle(self, evidence):
        return any(evidence == c.vehicle_evidence for _, cs in self._alternatives.values() for c in cs)

    def get_authoritative_metrics(self, route_result_id):
        return self._metrics.get(route_result_id)

    def rank(self, state):
        self.context(state)
        stored = self._alternatives.get(state.alternatives.alternatives_ref)
        if stored is None or stored[0] != current_binding(state):
            raise ToolRejected(FailureKind.STALE_BINDING, "alternatives_not_authoritative")
        request = replace(self.fixture.request,
                          expected_graph_snapshot_id=stored[0].graph_snapshot_id,
                          expected_hazard_snapshot_id=stored[0].hazard_snapshot_id)
        return rank_candidates(request, stored[1], self.fixture.ranking_policy,
                               as_of=state.as_of, verifier=self, metric_verifier=self)

    def verify(self, tool, state, claim):
        self._check_scope(state)
        if tool in (Tool.PREPARE_HAZARD, Tool.PREPARE_NETWORK):
            if type(claim) is not PreparedClaim:
                raise ToolRejected(FailureKind.INVALID_OUTPUT, "expected_prepared_claim")
            claim.__post_init__()
            target = (self.fixture.hazard(self.hazard_target) if tool is Tool.PREPARE_HAZARD
                      else self.fixture.graph(self.graph_target))
            if claim.artifact.snapshot.snapshot_id != target.snapshot.snapshot_id:
                raise ToolRejected(FailureKind.STALE_BINDING, "prepared_snapshot_mismatch")
            if claim.artifact != target:
                raise ToolRejected(FailureKind.CONTRADICTORY, "prepared_artifact_mismatch")
            summary = self.evidence(target, state)
            field = "hazard" if tool is Tool.PREPARE_HAZARD else "graph"
            updated = replace(state, **{field: summary}, state_revision=state.state_revision + 1)
            updated = self.contextualize(updated)
            # Historical alternatives retain their original binding; no relabeling.
            return VerifiedUpdate(updated, (target.snapshot.snapshot_id,), summary)
        self.context(state)
        binding = current_binding(state)
        if tool is Tool.GENERATE:
            if type(claim) is not AlternativesClaim:
                raise ToolRejected(FailureKind.INVALID_OUTPUT, "expected_alternatives_claim")
            claim.__post_init__()
            if claim.binding != binding:
                raise ToolRejected(FailureKind.STALE_BINDING, "alternative_snapshot_mismatch")
            if claim.artifact_ref != _artifact_ref(binding):
                raise ToolRejected(FailureKind.CONTRADICTORY, "alternative_identity_mismatch")
            if (tuple(r.edges for r in claim.routes) != tuple(sorted(r.edges for r in claim.routes))
                    or any(r.route_id != f"route-{index + 1}" for index, r in enumerate(claim.routes))):
                raise ToolRejected(FailureKind.CONTRADICTORY, "route_identity_not_canonical")
            canonical = tuple(self.recompute_route(r.route_id, r.edges, binding) for r in claim.routes)
            if canonical != claim.routes or len({r.edges for r in canonical}) != len(canonical):
                raise ToolRejected(FailureKind.CONTRADICTORY, "route_evidence_mismatch")
            candidates = tuple(self._candidate(r, binding, claim.artifact_ref) for r in canonical)
            previous = self._alternatives.get(claim.artifact_ref)
            if previous is not None and previous != (binding, candidates):
                raise ToolRejected(FailureKind.CONTRADICTORY, "immutable_artifact_conflict")
            alt = AlternativeSet(Availability.AVAILABLE, claim.artifact_ref, binding,
                                 VerificationStatus.VERIFIED, "synthetic-routes-check-v1")
            updated = replace(state, alternatives=alt, state_revision=state.state_revision + 1)
            result = VerifiedUpdate(updated, (claim.artifact_ref,), candidates)
            self._alternatives[claim.artifact_ref] = (binding, candidates)
            self._metrics.update((c.ranking_metric_evidence.route_result_id, c.ranking_metric_evidence)
                                 for c in candidates)
            return result
        if tool is Tool.EVALUATE:
            if type(claim) is not EvaluationClaim:
                raise ToolRejected(FailureKind.INVALID_OUTPUT, "expected_evaluation_claim")
            claim.__post_init__()
            if claim.binding != binding or claim.alternatives_ref != state.alternatives.alternatives_ref:
                raise ToolRejected(FailureKind.STALE_BINDING, "evaluation_snapshot_mismatch")
            actual = self.rank(state)
            if claim.ranking != actual:
                raise ToolRejected(FailureKind.CONTRADICTORY, "evaluation_gate_mismatch")
            updated = replace(state, state_revision=state.state_revision + 1)
            return VerifiedUpdate(updated, ("evaluation-" + str(updated.state_revision),), actual)
        raise ToolRejected(FailureKind.UNSUPPORTED, "unsupported_synthetic_tool")


@dataclass(frozen=True, slots=True)
class Fault:
    tool: Tool
    attempt: int
    kind: FailureKind

    def __post_init__(self):
        if (type(self.tool) is not Tool or type(self.attempt) is not int or self.attempt < 1
                or type(self.kind) is not FailureKind):
            raise ValueError("Invalid explicit fault schedule entry")


class SyntheticTools:
    """Untrusted synthetic producers. Preparation never changes authority sources.

    No safety filtering/relaxation in route search; the existing gate evaluates
    all returned alternatives. Search is all simple directed keyed paths within
    the bounded fixture, capped at 128 (overflow is explicit failure, not truncation).
    An empty result asserts only that no candidates were returned, not field safety.
    """

    def __init__(self, fixture: SyntheticFixture, graph_target: str, hazard_target: str,
                 evaluator: SyntheticAuthority, faults: tuple[Fault, ...] = ()):
        fixture.__post_init__()
        _tuple(faults, Fault, 64)
        if len({(f.tool, f.attempt) for f in faults}) != len(faults):
            raise ValueError("Duplicate fault schedule position")
        if fixture.graph(graph_target) is None or fixture.hazard(hazard_target) is None:
            raise ValueError("Unknown preparation target")
        self.fixture = fixture
        self.graph_target, self.hazard_target = graph_target, hazard_target
        self._evaluator = evaluator
        self._faults = faults
        self._calls = []

    @property
    def calls(self):
        return tuple(self._calls)

    def invoke(self, tool, state):
        attempt = 1 + sum(t is tool for t, _ in self._calls)
        self._calls.append((tool, state.state_revision))
        # Permanent/unsupported fixture-tool faults stay active after their
        # declared onset. Other failures affect exactly the declared attempt.
        fault = next((f for f in sorted(self._faults, key=lambda f: f.attempt)
                      if f.tool is tool and (f.attempt == attempt or f.attempt < attempt
                      and f.kind in (FailureKind.PERMANENT, FailureKind.UNSUPPORTED))), None)
        if fault is not None:
            if fault.kind is FailureKind.INVALID_OUTPUT:
                return None  # Real malformed output; the host must reject it.
            if fault.kind not in (FailureKind.CONTRADICTORY, FailureKind.STALE_BINDING):
                raise ToolRejected(fault.kind, "injected_" + fault.kind.name.lower())
        claim = self._produce(tool, state)
        if fault is None:
            return claim
        if type(claim) is PreparedClaim:
            artifact = claim.artifact
            if fault.kind is FailureKind.STALE_BINDING:
                names = {a.snapshot.snapshot_id for a in self.fixture.graphs + self.fixture.hazards}
                name = next(f"fault-snapshot-{i}" for i in range(17) if f"fault-snapshot-{i}" not in names)
                artifact = replace(artifact, snapshot=replace(artifact.snapshot, snapshot_id=name))
            else:
                artifact = replace(artifact, crs="SYNTHETIC:WRONG")
            return replace(claim, artifact=artifact, claimed_verification=VerificationStatus.VERIFIED)
        if fault.kind is FailureKind.STALE_BINDING:
            return replace(claim, binding=replace(claim.binding, graph_snapshot_id="fault-stale-graph"))
        if type(claim) is AlternativesClaim:
            return replace(claim, artifact_ref="fault-forged-artifact",
                           claimed_verification=VerificationStatus.VERIFIED)
        return replace(claim, ranking=replace(claim.ranking, effective_time_weight=2.0))

    def _produce(self, tool, state):
        if tool is Tool.PREPARE_HAZARD:
            return PreparedClaim(self.fixture.hazard(self.hazard_target))
        if tool is Tool.PREPARE_NETWORK:
            return PreparedClaim(self.fixture.graph(self.graph_target))
        binding = current_binding(state)
        if tool is Tool.EVALUATE:
            return EvaluationClaim(state.alternatives.alternatives_ref, binding, self._evaluator.rank(state))
        if tool is not Tool.GENERATE:
            raise ToolRejected(FailureKind.UNSUPPORTED, "unsupported_synthetic_tool")
        graph, hazard = self.fixture.graph(binding.graph_snapshot_id), self.fixture.hazard(binding.hazard_snapshot_id)
        if graph is None or hazard is None:
            raise ToolRejected(FailureKind.MISSING_INPUT, "source_artifact_missing")
        values = {s.edge: s.value for s in hazard.samples}
        paths = []

        def visit(node, visited, path):
            if node == self.fixture.destination:
                paths.append(path)
                if len(paths) > 128:
                    raise ToolRejected(FailureKind.PERMANENT, "synthetic_path_cap_exceeded")
                return
            for edge in sorted(graph.edges, key=lambda e: e.identity):
                if edge.identity.u == node and edge.identity.v not in visited:
                    visit(edge.identity.v, visited | {edge.identity.v}, path + (edge,))

        visit(self.fixture.origin, {self.fixture.origin}, ())
        claims = []
        for index, path in enumerate(paths):
            maximum = 0.0
            vehicles = path[0].vehicles
            for edge in path:
                value = values.get(edge.identity)
                if value is None or edge.vehicles is None:
                    raise ToolRejected(FailureKind.MISSING_INPUT, "route_input_unknown")
                maximum = max(maximum, value)
                vehicles = vehicles & edge.vehicles
            # Use the same declared numerical convention (accurate sum) as the
            # authority, so benign accumulation rounding cannot fabricate a fault.
            claims.append(RouteClaim(f"route-{index + 1}", tuple(e.identity for e in path),
                                     fsum(e.length_m for e in path),
                                     fsum(e.travel_time_sec for e in path),
                                     fsum(e.length_m * values[e.identity] for e in path),
                                     maximum, vehicles))
        return AlternativesClaim(_artifact_ref(binding), binding, tuple(claims))
