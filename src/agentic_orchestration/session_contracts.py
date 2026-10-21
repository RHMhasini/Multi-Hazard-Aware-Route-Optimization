"""M5.1 historical checkpoints. No executable budgets, handles or authorization.

Fingerprints identify host configuration; they are NOT authentication or evidence
verification. The protected-local-store trust boundary is explicit: historical
loads are unauthenticated, and protected restoration is unavailable in M5.1.
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import re

from . import adaptive_contracts as ac
from .contracts import VerificationStatus
from .execution import AdaptiveLimits


SCHEMA = "c4.session-checkpoint/1"
MAX_BYTES = 131072
HOST_REASONS = frozenset({"duration_exhausted", "provider_calls_exhausted",
                          "model_calls_exhausted", "budget_clock_failed"})
ADAPTIVE_REASONS = HOST_REASONS | frozenset({
    "host_budget_exhausted", "host_budget_unavailable", "steps_exhausted",
    "tool_attempts_exhausted", "recovery_attempts_exhausted",
})


def _require(condition):
    if not condition:
        raise ValueError("invalid_checkpoint")


def _integer(value, maximum=2147483647):
    _require(type(value) is int and 0 <= value <= maximum)


def _number(value, *, positive=False):
    _require(type(value) in (float, int) and 0 <= value <= 1e15)
    if positive:
        _require(value > 0)


def _digest(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def _id(value):
    _require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None)


def _utc(value):
    _require(type(value) is str and len(value) <= 32)
    result = datetime.fromisoformat(value)
    _require(result.tzinfo == timezone.utc and result.isoformat() == value)
    return result


def utc_text(value):
    return ac._timestamp(value).isoformat()


def fingerprint(value):
    """Bounded canonical hash of ONLY known host records; never serialize objects.

    Used for progress equality and configuration binding, not tamper protection.
    Free-form errors, observations or model messages have no checkpoint fields.
    """
    records = (ac.Snapshot, ac.SnapshotBinding, ac.EvidenceStatus, ac.RoutingContext,
               ac.AlternativeSet, ac.ToolCapability, ac.DependencyPolicy, ac.DependencyState)
    enums = (ac.Availability, ac.Freshness, ac.Coverage, ac.SourceClass,
             ac.MissionPurpose, ac.Tool, VerificationStatus)

    def project(item, depth=0):
        _require(depth <= 16)
        kind = type(item)
        if kind in enums:
            return [kind.__name__, item.value]
        if kind in (str, int, float, bool, type(None)):
            if kind is str:
                _require(len(item) <= 1024)
            return item
        if kind is datetime:
            return ["datetime", utc_text(item)]
        if kind in (tuple, frozenset):
            _require(len(item) <= 512)
            values = [project(x, depth + 1) for x in item]
            return sorted(values, key=canonical) if kind is frozenset else values
        _require(kind in records)
        item.__post_init__()
        return [kind.__name__, {f.name: project(getattr(item, f.name), depth + 1)
                               for f in fields(item)}]

    return hashlib.sha256(canonical(project(value))).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


@dataclass(frozen=True, slots=True)
class CheckpointBindings:
    """Versioned host-supplied immutable mission/policy/config digests.

    Host must include the FULL mission, hard constraints, ranking policy,
    permissions, adapter/verifier versions and model/prompt/settings as applicable
    in its respective supplied digests. No fingerprint proves current permission.
    Registry and evidence digests are captured from the actual bound registry.
    """

    mission: str
    policy: str
    registry: str
    configuration: str
    evidence: str
    version: str = "c4.session-bindings/1"

    def __post_init__(self):
        _require(type(self.version) is str and self.version == "c4.session-bindings/1")
        for value in (self.mission, self.policy, self.registry, self.configuration, self.evidence):
            _digest(value)


@dataclass(frozen=True, slots=True)
class HostLedger:
    budget_scope_id: str
    max_provider_calls: int
    provider_calls: int
    max_model_calls: int
    model_calls: int
    max_model_output_bytes: int
    max_duration_sec: float
    elapsed_sec: float
    reason: str | None

    def __post_init__(self):
        _id(self.budget_scope_id)
        for value in (self.max_provider_calls, self.provider_calls, self.max_model_calls,
                      self.model_calls, self.max_model_output_bytes):
            _integer(value)
        _require(self.provider_calls <= self.max_provider_calls
                 and self.model_calls <= self.max_model_calls and self.max_model_output_bytes > 0)
        _number(self.max_duration_sec, positive=True)
        _number(self.elapsed_sec)
        _require(self.reason is None or type(self.reason) is str and self.reason in HOST_REASONS)
        _require(self.reason is not None or self.elapsed_sec < self.max_duration_sec)
        if self.reason == "provider_calls_exhausted":
            _require(self.provider_calls == self.max_provider_calls)
        if self.reason == "model_calls_exhausted":
            _require(self.model_calls == self.max_model_calls)
        if self.reason == "duration_exhausted":
            _require(self.elapsed_sec >= self.max_duration_sec)


@dataclass(frozen=True, slots=True)
class InputAttempt:
    identity: str
    attempts: int
    recoveries: int
    retryable: bool

    def __post_init__(self):
        _digest(self.identity)
        _integer(self.attempts, 512)
        _integer(self.recoveries, 512)
        _require(self.attempts > 0 and self.recoveries == self.attempts - 1
                 and type(self.retryable) is bool)


@dataclass(frozen=True, slots=True)
class ExecutionLedger:
    host: HostLedger
    limits: AdaptiveLimits
    steps: int
    tool_attempts: int
    recovery_attempts: int
    reason: str | None
    attempts: tuple[InputAttempt, ...]
    no_progress: int
    progress_identity: str | None
    evaluated_inputs: tuple[str, ...]
    session_bound: bool
    model_transport_stopped: bool

    def __post_init__(self):
        _require(type(self.host) is HostLedger and type(self.limits) is AdaptiveLimits)
        self.host.__post_init__()
        self.limits.__post_init__()
        for value in (self.steps, self.tool_attempts, self.recovery_attempts):
            _integer(value, 512)
        _integer(self.no_progress)
        _require(self.steps <= self.limits.max_steps
                 and self.tool_attempts <= self.limits.max_tool_attempts
                 and self.recovery_attempts <= self.limits.max_recovery_attempts)
        _require(self.reason is None or type(self.reason) is str and self.reason in ADAPTIVE_REASONS)
        if self.host.reason is not None:
            _require(self.reason is not None)
        for reason, count, ceiling in (
            ("steps_exhausted", self.steps, self.limits.max_steps),
            ("tool_attempts_exhausted", self.tool_attempts, self.limits.max_tool_attempts),
            ("recovery_attempts_exhausted", self.recovery_attempts, self.limits.max_recovery_attempts),
        ):
            if self.reason == reason:
                _require(count == ceiling)
        _require(type(self.attempts) is tuple and len(self.attempts) <= 512)
        for attempt in self.attempts:
            _require(type(attempt) is InputAttempt)
            attempt.__post_init__()
            _require(attempt.recoveries <= self.limits.max_retries_per_input)
        ids = tuple(a.identity for a in self.attempts)
        _require(ids == tuple(sorted(set(ids))))
        _require(sum(a.attempts for a in self.attempts) == self.tool_attempts
                 and sum(a.recoveries for a in self.attempts) == self.recovery_attempts)
        _require(type(self.evaluated_inputs) is tuple and len(self.evaluated_inputs) <= 512)
        for identity in self.evaluated_inputs:
            _digest(identity)
        _require(self.evaluated_inputs == tuple(sorted(set(self.evaluated_inputs))))
        _require(type(self.session_bound) is bool and type(self.model_transport_stopped) is bool)
        _require(self.session_bound == (self.progress_identity is not None))
        if self.progress_identity is not None:
            _digest(self.progress_identity)

    @property
    def exhausted(self):
        return self.reason is not None or self.host.reason is not None

    @property
    def model_blocked(self):
        return (self.exhausted or self.model_transport_stopped
                or self.no_progress >= self.limits.max_no_progress
                or self.host.model_calls >= self.host.max_model_calls)


@dataclass(frozen=True, slots=True)
class SessionCheckpoint:
    session_id: str
    storage_version: int
    dependency_revision: int
    bindings: CheckpointBindings
    ledger: ExecutionLedger
    captured_at_utc: str
    schema_version: str = SCHEMA

    def __post_init__(self):
        _require(type(self.schema_version) is str and self.schema_version == SCHEMA)
        _id(self.session_id)
        _integer(self.storage_version)
        _integer(self.dependency_revision)
        _require(type(self.bindings) is CheckpointBindings and type(self.ledger) is ExecutionLedger)
        self.bindings.__post_init__()
        self.ledger.__post_init__()
        _require(self.session_id != self.ledger.host.budget_scope_id)
        _utc(self.captured_at_utc)

    def elapsed_at(self, now):
        """Conservative historical estimate: ALL restart downtime consumes time.

        Caller supplies trusted UTC. A regressing/unavailable wall clock fails
        closed. No persisted monotonic origin or renewed allowance is returned.
        UTC accuracy cannot be authenticated by this local checkpoint layer.
        """
        self.__post_init__()
        delta = (ac._timestamp(now) - _utc(self.captured_at_utc)).total_seconds()
        _require(delta >= 0)
        elapsed = self.ledger.host.elapsed_sec + delta
        _number(elapsed)
        return elapsed


def encode_checkpoint(value):
    _require(type(value) is SessionCheckpoint)
    value.__post_init__()
    data = canonical(asdict(value))
    _require(len(data) <= MAX_BYTES)
    return data


def _object(kind, data, **replacements):
    _require(type(data) is dict and set(data) == {f.name for f in fields(kind)})
    return kind(**(data | replacements))


def decode_checkpoint(data):
    """Strict bounded JSON. Never import types named by a stored record."""
    _require(type(data) is bytes and len(data) <= MAX_BYTES)

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("invalid_checkpoint")

    # Never attach JSONDecodeError (which retains its input) to public errors.
    failed = False
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
        ledger = value["ledger"]
        _require(type(ledger["attempts"]) is list and type(ledger["evaluated_inputs"]) is list)
        ledger = _object(ExecutionLedger, ledger,
                         host=_object(HostLedger, ledger["host"]),
                         limits=_object(AdaptiveLimits, ledger["limits"]),
                         attempts=tuple(_object(InputAttempt, a) for a in ledger["attempts"]),
                         evaluated_inputs=tuple(ledger["evaluated_inputs"]))
        result = _object(SessionCheckpoint, value, ledger=ledger,
                         bindings=_object(CheckpointBindings, value["bindings"]))
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise ValueError("invalid_checkpoint") from None
    return result
