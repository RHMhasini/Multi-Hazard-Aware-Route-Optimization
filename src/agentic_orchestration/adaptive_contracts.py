"""M1 dependency summaries, separate from v1 actions and route safety contracts.

These immutable records are supplied by trusted host wiring, not a model/tool
claim. A verification reference is an audit binding, NOT proof of verification.
There is no registry, adapter, persistence, policy approval or execution here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .contracts import VerificationStatus, finite_number


class Availability(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Coverage(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class SourceClass(str, Enum):
    SYNTHETIC = "synthetic"
    STATIC_SUSCEPTIBILITY = "static_susceptibility"
    OBSERVED_EVENT_DATA = "observed_event_data"
    ROAD_NETWORK = "road_network"


class MissionPurpose(str, Enum):
    SYNTHETIC_RESEARCH = "synthetic_research"
    REVIEW_ONLY = "review_only"


class Tool(str, Enum):
    # Stable report order, not an execution sequence or priority.
    PREPARE_HAZARD = "prepare_hazard_snapshot"
    PREPARE_NETWORK = "prepare_network_snapshot"
    GENERATE = "generate_alternatives"
    EVALUATE = "evaluate_alternatives"


class Dependency(str, Enum):
    GRAPH = "graph"
    HAZARD = "hazard"
    ROUTING_CONTEXT = "routing_context"
    ALTERNATIVES = "alternatives"


def _ref(value):
    if (type(value) is not str or not value or len(value) > 128
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError("References must be nonempty printable ASCII without whitespace")


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("state_revision must be a nonnegative integer")


def _timestamp(value):
    # Fixed-offset datetime.timezone is immutable; arbitrary mutable tzinfo and
    # datetime subclasses are deliberately outside this minimum contract.
    if type(value) is not datetime or type(value.tzinfo) is not timezone:
        raise ValueError("Use an aware datetime with an immutable fixed-offset timezone")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("Timestamp cannot be represented in UTC") from None


def _verification(status, reference):
    if type(status) is not VerificationStatus:
        raise ValueError("Expected VerificationStatus")
    if reference is not None:
        _ref(reference)
    if status is VerificationStatus.VERIFIED and reference is None:
        raise ValueError("Independent verification requires a host verification reference")


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str
    source_class: SourceClass
    built_at: datetime
    observed_at: datetime | None = None
    valid_until: datetime | None = None

    def __post_init__(self):
        _ref(self.snapshot_id)
        if type(self.source_class) is not SourceClass:
            raise ValueError("Expected SourceClass")
        built = _timestamp(self.built_at)
        observed = _timestamp(self.observed_at) if self.observed_at is not None else None
        if observed is not None and observed > built:
            raise ValueError("Observation cannot follow artifact construction")
        if self.valid_until is not None:
            if observed is None or _timestamp(self.valid_until) <= observed:
                raise ValueError("Validity interval requires an observation and a later expiry")


@dataclass(frozen=True, slots=True)
class EvidenceStatus:
    availability: Availability
    freshness: Freshness = Freshness.UNKNOWN
    coverage: Coverage = Coverage.UNKNOWN
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    snapshot: Snapshot | None = None
    verification_ref: str | None = None

    def __post_init__(self):
        for value, kind in ((self.availability, Availability), (self.freshness, Freshness),
                            (self.coverage, Coverage)):
            if type(value) is not kind:
                raise ValueError("Evidence status requires exact enum types")
        _verification(self.verification, self.verification_ref)
        if self.availability is Availability.AVAILABLE:
            if type(self.snapshot) is not Snapshot:
                raise ValueError("Available evidence requires snapshot metadata")
            self.snapshot.__post_init__()
            if (self.freshness in (Freshness.FRESH, Freshness.STALE)
                    and self.snapshot.observed_at is None):
                raise ValueError("Fresh/stale claims require source observation time, not build time")
            if self.freshness is Freshness.NOT_APPLICABLE and self.snapshot.observed_at is not None:
                raise ValueError("Observed evidence cannot claim freshness is not applicable")
        elif (self.snapshot is not None or self.freshness is not Freshness.UNKNOWN
              or self.coverage is not Coverage.UNKNOWN
              or self.verification is not VerificationStatus.UNVERIFIED
              or self.verification_ref is not None):
            raise ValueError("Unavailable evidence cannot carry available-evidence claims")


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    """Host-supplied validity requirements; does not approve a domain policy.

    Time is checked against observed_at only. No freshness limits, allowed source
    classes or scientific interpretation are inferred or supplied as defaults.
    """

    graph_max_age_sec: float
    hazard_max_age_sec: float
    graph_source_classes: frozenset[SourceClass]
    hazard_source_classes: frozenset[SourceClass]

    def __post_init__(self):
        for limit in (self.graph_max_age_sec, self.hazard_max_age_sec):
            if type(limit) not in (int, float) or not finite_number(limit) or limit < 0:
                raise ValueError("Maximum ages must be finite nonnegative numbers")
        for allowed in (self.graph_source_classes, self.hazard_source_classes):
            if type(allowed) is not frozenset or any(type(s) is not SourceClass for s in allowed):
                raise ValueError("Source allowlists must be frozensets of SourceClass")


@dataclass(frozen=True, slots=True)
class SnapshotBinding:
    """Exact immutable mission/policy/OD and graph/hazard identities."""

    mission_ref: str
    policy_ref: str
    od_ref: str
    graph_snapshot_id: str
    hazard_snapshot_id: str

    def __post_init__(self):
        for value in (self.mission_ref, self.policy_ref, self.od_ref,
                      self.graph_snapshot_id, self.hazard_snapshot_id):
            _ref(value)


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Host assessment of OD, coordinate alignment and vehicle-input suitability.

    VERIFIED means those checks apply to exactly this binding. The checks and
    any actual artifact/vehicle verification are not implemented by M1.
    """

    binding: SnapshotBinding
    verification: VerificationStatus
    verification_ref: str | None = None

    def __post_init__(self):
        if type(self.binding) is not SnapshotBinding:
            raise ValueError("Expected SnapshotBinding")
        self.binding.__post_init__()
        _verification(self.verification, self.verification_ref)


@dataclass(frozen=True, slots=True)
class AlternativeSet:
    availability: Availability
    alternatives_ref: str | None = None
    binding: SnapshotBinding | None = None
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    verification_ref: str | None = None

    def __post_init__(self):
        if type(self.availability) is not Availability:
            raise ValueError("Expected Availability")
        _verification(self.verification, self.verification_ref)
        if self.availability is Availability.AVAILABLE:
            _ref(self.alternatives_ref)
            if type(self.binding) is not SnapshotBinding:
                raise ValueError("Available alternatives require snapshot bindings")
            self.binding.__post_init__()
        elif (self.alternatives_ref is not None or self.binding is not None
              or self.verification is not VerificationStatus.UNVERIFIED
              or self.verification_ref is not None):
            raise ValueError("Unavailable alternatives cannot carry result claims")


@dataclass(frozen=True, slots=True)
class ToolCapability:
    """Host declarations for this state revision, never an executable handle.

    inputs_ready includes approved sources/configuration and adapter-specific
    preconditions. Availability does not imply authorization or readiness.
    """

    tool: Tool
    supported: bool
    authorized: bool
    inputs_ready: bool

    def __post_init__(self):
        if type(self.tool) is not Tool or any(type(v) is not bool for v in (
                self.supported, self.authorized, self.inputs_ready)):
            raise ValueError("Invalid tool capability")


def _fresh_at(snapshot: Snapshot, as_of: datetime, max_age_sec: float) -> bool:
    """Internal time predicate: inclusive age limit, exclusive expiry; no clock I/O."""
    if snapshot.observed_at is None:
        return False
    now = _timestamp(as_of)
    age = (now - _timestamp(snapshot.observed_at)).total_seconds()
    return (0 <= age <= max_age_sec
            and (snapshot.valid_until is None or now < _timestamp(snapshot.valid_until)))


@dataclass(frozen=True, slots=True)
class DependencyState:
    """M1 host observation, NOT a v1 action, safety decision or source verifier.

    mission_ref and policy_ref must identify immutable host revisions. Old result
    bindings are valid historical metadata, but never compatible by relabeling.
    All future metadata times are rejected, even on unverified artifacts.
    """

    mission_ref: str
    policy_ref: str
    state_revision: int
    as_of: datetime
    purpose: MissionPurpose
    policy: DependencyPolicy
    graph: EvidenceStatus
    hazard: EvidenceStatus
    capabilities: tuple[ToolCapability, ...]
    od_ref: str | None = None
    routing_context: RoutingContext | None = None
    alternatives: AlternativeSet = AlternativeSet(Availability.MISSING)

    def __post_init__(self):
        _ref(self.mission_ref)
        _ref(self.policy_ref)
        _revision(self.state_revision)
        now = _timestamp(self.as_of)
        if self.od_ref is not None:
            _ref(self.od_ref)
        if type(self.purpose) is not MissionPurpose or type(self.policy) is not DependencyPolicy:
            raise ValueError("Expected mission purpose and dependency policy")
        self.policy.__post_init__()
        for evidence, age_limit in ((self.graph, self.policy.graph_max_age_sec),
                                    (self.hazard, self.policy.hazard_max_age_sec)):
            if type(evidence) is not EvidenceStatus:
                raise ValueError("Expected EvidenceStatus")
            evidence.__post_init__()
            if evidence.snapshot is not None:
                if _timestamp(evidence.snapshot.built_at) > now:
                    raise ValueError("Artifact construction time is in the future")
                if evidence.freshness in (Freshness.FRESH, Freshness.STALE):
                    actual = _fresh_at(evidence.snapshot, self.as_of, age_limit)
                    if (evidence.freshness is Freshness.FRESH) != actual:
                        raise ValueError("Freshness claim contradicts observation age or expiry")
        if type(self.capabilities) is not tuple:
            raise ValueError("Capabilities must be an immutable tuple")
        seen = set()
        for capability in self.capabilities:
            if type(capability) is not ToolCapability:
                raise ValueError("Expected ToolCapability")
            capability.__post_init__()
            if capability.tool in seen:
                raise ValueError("Duplicate tool capability")
            seen.add(capability.tool)
        if self.routing_context is not None:
            if type(self.routing_context) is not RoutingContext:
                raise ValueError("Expected RoutingContext")
            self.routing_context.__post_init__()
        if type(self.alternatives) is not AlternativeSet:
            raise ValueError("Expected AlternativeSet")
        self.alternatives.__post_init__()


@dataclass(frozen=True, slots=True)
class ToolAssessment:
    tool: Tool
    permitted: bool
    useful: bool
    blockers: tuple[str, ...]

    def __post_init__(self):
        if (type(self.tool) is not Tool or type(self.permitted) is not bool
                or type(self.useful) is not bool or type(self.blockers) is not tuple
                or any(type(b) is not str or not b for b in self.blockers)
                or self.permitted != (not self.blockers)):
            raise ValueError("Invalid tool assessment")


@dataclass(frozen=True, slots=True)
class ToolOption:
    """Useful permitted operation bound to state; NOT dispatch authorization."""

    tool: Tool
    mission_ref: str
    policy_ref: str
    state_revision: int

    def __post_init__(self):
        if type(self.tool) is not Tool:
            raise ValueError("Expected Tool")
        _ref(self.mission_ref)
        _ref(self.policy_ref)
        _revision(self.state_revision)


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """Required dependencies are unmet conditions, not mandatory tool choices.

    blocked_dependencies have no currently eligible way forward in M1; the host
    may request clarification. M1 does not generate a clarification action.
    """

    options: tuple[ToolOption, ...]
    assessments: tuple[ToolAssessment, ...]
    required_dependencies: tuple[Dependency, ...]
    blocked_dependencies: tuple[Dependency, ...]

    def __post_init__(self):
        for values, kind in ((self.options, ToolOption), (self.assessments, ToolAssessment),
                             (self.required_dependencies, Dependency),
                             (self.blocked_dependencies, Dependency)):
            if type(values) is not tuple or any(type(v) is not kind for v in values):
                raise ValueError("Eligibility report must contain immutable typed tuples")
        if tuple(a.tool for a in self.assessments) != tuple(Tool):
            raise ValueError("Assess every tool exactly once in declaration order")
        expected = tuple(a.tool for a in self.assessments if a.permitted and a.useful)
        if tuple(o.tool for o in self.options) != expected:
            raise ValueError("Options must match permitted useful assessments")
        if (len(set(self.required_dependencies)) != len(self.required_dependencies)
                or len(set(self.blocked_dependencies)) != len(self.blocked_dependencies)
                or not set(self.blocked_dependencies) <= set(self.required_dependencies)):
            raise ValueError("Blocked dependencies must be a unique subset of unmet dependencies")
        if len({(o.mission_ref, o.policy_ref, o.state_revision) for o in self.options}) > 1:
            raise ValueError("Options must share a host state binding")

    @property
    def clarification_required(self) -> bool:
        return bool(self.blocked_dependencies) or not self.options
