"""M2 in-memory dispatch boundary. No planner, retry loop or durable ledger.

Adapters produce untrusted claims. Only a separately injected host authority can
produce an update. Issued M1 option objects are local handles, not wire tokens.
Host configuration and authority injection are trusted application operations.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from threading import Lock
from collections.abc import Callable
from typing import Protocol

from .adaptive_contracts import DependencyState, EvidenceStatus, Tool, ToolOption, _ref
from .contracts import RouteCandidate
from .dependency_state import derive_eligibility
from .ranking import RankingResult
from .snapshots import immutable_snapshot


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    MISSING_INPUT = "missing_input"
    UNSUPPORTED = "unsupported"
    INVALID_OUTPUT = "invalid_output"
    CONTRADICTORY = "contradictory_evidence"
    STALE_BINDING = "stale_binding"
    UNAUTHORIZED = "unauthorized"
    FORGED_OPTION = "forged_option"


@dataclass(frozen=True, slots=True)
class ToolFailure:
    kind: FailureKind
    code: str

    def __post_init__(self):
        if (type(self.kind) is not FailureKind or type(self.code) is not str
                or not self.code or len(self.code) > 80
                or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in self.code)):
            raise ValueError("Invalid failure record")

    @property
    def retryable(self):
        # Classification only; nothing here performs or authorizes a retry.
        return self.kind is FailureKind.TRANSIENT


class ToolRejected(Exception):
    def __init__(self, kind: FailureKind, code: str):
        self.failure = ToolFailure(kind, code)
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerifiedUpdate:
    """Authority-only return type; never accepted directly from an adapter."""

    state: DependencyState
    artifact_refs: tuple[str, ...]
    payload: EvidenceStatus | tuple[RouteCandidate, ...] | RankingResult

    def __post_init__(self):
        if type(self.state) is not DependencyState:
            raise ValueError("Expected dependency state")
        self.state.__post_init__()
        if (type(self.artifact_refs) is not tuple or not self.artifact_refs
                or any(type(r) is not str or not r or len(r) > 128 for r in self.artifact_refs)):
            raise ValueError("Expected immutable artifact references")
        for reference in self.artifact_refs:
            _ref(reference)
        if type(self.payload) is EvidenceStatus:
            self.payload.__post_init__()
        elif type(self.payload) is RankingResult:
            if immutable_snapshot(self.payload) != self.payload:
                raise ValueError("Invalid ranking payload")
        elif type(self.payload) is tuple and all(type(c) is RouteCandidate for c in self.payload):
            if immutable_snapshot(self.payload) != self.payload:
                raise ValueError("Invalid candidates payload")
        else:
            raise ValueError("Unsupported verified payload")


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    tool: Tool | None
    consumed_revision: int
    next_revision: int
    idempotency_key: str
    executed: bool
    update: VerifiedUpdate | None = None
    failure: ToolFailure | None = None
    tool_version: str = field(default="m2.synthetic/1", init=False)

    def __post_init__(self):
        if (type(self.call_id) is not str or not self.call_id
                or self.tool is not None and type(self.tool) is not Tool
                or any(type(r) is not int or r < 0 for r in (self.consumed_revision, self.next_revision))
                or type(self.idempotency_key) is not str or not self.idempotency_key
                or type(self.executed) is not bool
                or (self.update is None) == (self.failure is None)
                or self.update is not None and type(self.update) is not VerifiedUpdate
                or self.failure is not None and type(self.failure) is not ToolFailure):
            raise ValueError("Invalid tool result")
        if self.update is not None:
            self.update.__post_init__()
            if (not self.executed or self.next_revision != self.consumed_revision + 1
                    or self.update.state.state_revision != self.next_revision):
                raise ValueError("Invalid successful transition")
        elif self.next_revision != self.consumed_revision:
            raise ValueError("Failed attempts cannot adopt state")
        if self.failure is not None:
            self.failure.__post_init__()

    @property
    def ok(self):
        return self.update is not None


class ToolAdapter(Protocol):
    def invoke(self, tool: Tool, state: DependencyState) -> object: ...


class ToolAuthority(Protocol):
    def preflight(self, tool: Tool, state: DependencyState) -> None: ...
    def verify(self, tool: Tool, state: DependencyState, claim: object) -> VerifiedUpdate: ...


def _canonical(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, frozenset):
        return sorted((_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


class ToolRegistry:
    """One synchronous attempt per dispatch; all attempts (including rejects) recorded.

    A call identity is deterministic for the complete observation and tool/version.
    It is an idempotency identity, not a cache or automatic replay mechanism.
    Successful output adoption advances the revision and invalidates all handles.
    """

    def __init__(self, state: DependencyState, adapter: ToolAdapter, authority: ToolAuthority,
                 supported: frozenset[Tool] = frozenset(Tool), *, registration_id: str):
        # registration_id must bind host fixture/configuration and preparation
        # targets. Changing adapters/targets requires a new registry registration.
        if (type(state) is not DependencyState or type(supported) is not frozenset
                or any(type(t) is not Tool for t in supported)):
            raise ValueError("Invalid registry configuration")
        state.__post_init__()
        _ref(registration_id)
        self._registration_id = registration_id
        self._supported = supported
        self._adapter, self._authority = adapter, authority
        self._state = self._with_capabilities(state)
        self._issued = ()
        self._records = []
        self._control = None
        self._dispatch_lock = Lock()
        self._meta_lock = Lock()
        self._sequence = 0

    def _with_capabilities(self, state):
        return replace(state, capabilities=tuple(
            replace(c, supported=c.supported and c.tool in self._supported)
            for c in state.capabilities))

    @property
    def state(self) -> DependencyState:
        return self._state

    @property
    def records(self) -> tuple[ToolResult, ...]:
        return tuple(sorted(self._records, key=lambda r: r.call_id))

    @property
    def checkpoint_configuration(self):
        """Host registration labels only; no adapters, authority or handles.

        These labels must be versioned by host wiring when implementation or
        preparation targets change. They are not restoration authorization.
        """
        return self._registration_id, tuple(sorted(t.value for t in self._supported))

    def claim_control(self):
        """Exclusive in-process M3 lease; never expose it to a planner or tool."""
        with self._meta_lock:
            if self._control is not None or self._dispatch_lock.locked():
                raise ToolRejected(FailureKind.UNAUTHORIZED, "registry_already_controlled")
            self._control = object()
            return self._control

    def release_control(self, control):
        with self._meta_lock:
            self._check_control(control)
            if self._dispatch_lock.locked():
                raise ValueError("Cannot release during dispatch")
            self._control = None

    def _check_control(self, control):
        if self._control is not control:
            raise ToolRejected(FailureKind.UNAUTHORIZED, "registry_control_mismatch")

    def operation_identity(self, tool: Tool, state: DependencyState | None = None) -> str:
        """Stable input identity for recovery, excluding time/revision-only churn.

        Configuration/targets are bound by registration_id. Parent identities
        invalidate descendants. Unlike the M2 observation digest this identity
        deliberately survives clock refresh and new run IDs.
        """
        if type(tool) is not Tool:
            raise ValueError("Expected tool")
        state = self._state if state is None else state
        state.__post_init__()
        graph = state.graph.snapshot.snapshot_id if state.graph.snapshot else None
        hazard = state.hazard.snapshot.snapshot_id if state.hazard.snapshot else None
        parents = ((hazard,) if tool is Tool.PREPARE_HAZARD else (graph,)
                   if tool is Tool.PREPARE_NETWORK else (graph, hazard))
        inputs = (self._registration_id, tool.value, state.mission_ref, state.policy_ref,
                  state.od_ref, parents,
                  state.alternatives.alternatives_ref if tool is Tool.EVALUATE else None)
        return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()

    def options(self, *, control=None) -> tuple[ToolOption, ...]:
        self._check_control(control)
        if self._dispatch_lock.locked():
            raise ToolRejected(FailureKind.UNAUTHORIZED, "registry_dispatch_active")
        options = derive_eligibility(self._state).options
        self._issued = tuple((o, replace(o)) for o in options)
        return options

    def adopt_host_state(self, state: DependencyState, *, control=None):
        """Trusted fixture/permission event, not an agent-callable tool.

        The host must independently validate artifacts before calling this method.
        M2 has no concurrent transitions, storage, remote handles or resume API.
        """
        self._check_control(control)
        if self._dispatch_lock.locked():
            raise ToolRejected(FailureKind.UNAUTHORIZED, "registry_dispatch_active")
        self._adopt_state(state)

    def _adopt_state(self, state):
        if type(state) is not DependencyState:
            raise ValueError("Expected dependency state")
        state.__post_init__()
        old = self._state
        if (state.state_revision != old.state_revision + 1
                or (state.mission_ref, state.policy_ref, state.purpose, state.policy)
                != (old.mission_ref, old.policy_ref, old.purpose, old.policy)
                or state.as_of < old.as_of):
            raise ValueError("Invalid host transition")
        self._state = self._with_capabilities(state)
        self._issued = ()

    def execute(self, option: ToolOption, *, control=None,
                before_invoke: Callable[[], None] | None = None,
                before_adopt: Callable[[], None] | None = None) -> ToolResult:
        state = self._state
        tool = option.tool if type(option) is ToolOption and type(option.tool) is Tool else None
        canonical = json.dumps(_canonical(asdict(state)), sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256((self._registration_id + ":m2.synthetic/1:" + str(tool) + canonical).encode()).hexdigest()
        with self._meta_lock:
            self._sequence += 1
            call_id = f"call-{self._sequence:06d}"
        executed = False
        acquired = False
        update = failure = None
        try:
            self._check_control(control)
            acquired = self._dispatch_lock.acquire(blocking=False)
            if not acquired:
                raise ToolRejected(FailureKind.UNAUTHORIZED, "registry_dispatch_active")
            self._check_control(control)
            if type(option) is not ToolOption:
                raise ToolRejected(FailureKind.FORGED_OPTION, "invalid_option_type")
            option.__post_init__()
            if option.state_revision != state.state_revision:
                raise ToolRejected(FailureKind.STALE_BINDING, "stale_state_revision")
            cap = next((c for c in state.capabilities if c.tool is tool), None)
            if tool not in self._supported or cap is None or not cap.supported:
                raise ToolRejected(FailureKind.UNSUPPORTED, "unsupported_tool")
            if not cap.authorized:
                raise ToolRejected(FailureKind.UNAUTHORIZED, "tool_not_authorized")
            if not cap.inputs_ready:
                raise ToolRejected(FailureKind.MISSING_INPUT, "tool_inputs_not_ready")
            current = derive_eligibility(state).options
            if option not in current:
                raise ToolRejected(FailureKind.MISSING_INPUT, "tool_preconditions_not_met")
            if not any(option is issued and option == original for issued, original in self._issued):
                raise ToolRejected(FailureKind.FORGED_OPTION, "option_not_host_issued")
            # Consume the issuance even on failure; explicit reissue is needed.
            self._issued = ()
            self._authority.preflight(tool, state)
            if before_invoke is not None:
                before_invoke()
            executed = True
            claim = self._adapter.invoke(tool, state)
            update = self._authority.verify(tool, state, claim)
            if type(update) is not VerifiedUpdate:
                raise ToolRejected(FailureKind.INVALID_OUTPUT, "invalid_authority_result")
            update.__post_init__()
            if before_adopt is not None:
                before_adopt()
            self._adopt_state(update.state)
        except ToolRejected as exc:
            update = None
            try:
                if type(exc.failure) is not ToolFailure:
                    raise ValueError("Invalid failure type")
                exc.failure.__post_init__()
                failure = exc.failure
            except Exception:
                failure = ToolFailure(FailureKind.INVALID_OUTPUT, "invalid_failure_record")
        except Exception:
            # Never expose arbitrary exception text/tool narrative as instructions.
            update, failure = None, ToolFailure(FailureKind.INVALID_OUTPUT, "tool_boundary_error")
        except BaseException:
            if acquired:
                self._dispatch_lock.release()
            raise
        try:
            next_revision = update.state.state_revision if update is not None else state.state_revision
            result = ToolResult(call_id, tool, state.state_revision, next_revision,
                                key, executed, update, failure)
            self._records.append(result)
            return result
        finally:
            if acquired:
                self._dispatch_lock.release()
