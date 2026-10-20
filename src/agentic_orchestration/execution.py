"""Host-owned identities, clock and cooperative execution budget interfaces."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Protocol

from .contracts import finite_number


@dataclass(frozen=True, slots=True)
class DependencyIdentity:
    name: str
    version: str

    def __post_init__(self) -> None:
        if any(type(v) is not str or not v.strip() for v in (self.name, self.version)):
            raise ValueError("Dependency names and versions must be nonempty strings")


@dataclass(frozen=True, slots=True)
class RunDependencies:
    """Audit labels supplied by trusted host wiring, NEVER proof of authenticity."""

    provider: DependencyIdentity
    verifier: DependencyIdentity
    metric_verifier: DependencyIdentity
    evidence_bundle: DependencyIdentity
    clock: DependencyIdentity | None = None

    def __post_init__(self) -> None:
        required = (self.provider, self.verifier, self.metric_verifier, self.evidence_bundle)
        if any(type(v) is not DependencyIdentity for v in required):
            raise ValueError("Versioned identities are required for all dependencies")
        if self.clock is not None and type(self.clock) is not DependencyIdentity:
            raise ValueError("clock must be a DependencyIdentity")


class EvaluationClock(Protocol):
    """Trusted host time source; do not obtain this object from an AI request."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    scope_id: str
    max_provider_calls: int
    provider_calls: int
    max_duration_sec: float
    elapsed_sec: float
    exhausted: bool
    reason: str | None
    max_model_calls: int = 0
    model_calls: int = 0
    max_model_output_bytes: int = 4096


class ExecutionBudget(Protocol):
    """Host retains one budget across runs; reserve atomically BEFORE provider I/O."""

    def checkpoint(self, *, reserve_provider_call: bool = False,
                   reserve_model_call: bool = False) -> BudgetSnapshot: ...


class BudgetGuard:
    """Validate a host budget's immutable responses across a workflow boundary."""

    def __init__(self, budget: ExecutionBudget):
        self._budget = budget
        self.checks: list[BudgetSnapshot] = []

    def checkpoint(self, *, reserve_provider_call: bool = False,
                   reserve_model_call: bool = False) -> bool:
        kwargs = {"reserve_provider_call": reserve_provider_call}
        if reserve_model_call:
            kwargs["reserve_model_call"] = True
        snapshot = self._budget.checkpoint(**kwargs)
        if type(snapshot) is not BudgetSnapshot:
            raise ValueError("Invalid budget response")
        # All fields are checked before retaining the record; no arbitrary payload.
        if (type(snapshot.exhausted) is not bool
                or type(snapshot.scope_id) is not str or not snapshot.scope_id.strip()
                or type(snapshot.provider_calls) is not int or snapshot.provider_calls < 0
                or type(snapshot.max_provider_calls) is not int
                or snapshot.max_provider_calls < snapshot.provider_calls
                or type(snapshot.model_calls) is not int or snapshot.model_calls < 0
                or type(snapshot.max_model_calls) is not int
                or snapshot.max_model_calls < snapshot.model_calls
                or type(snapshot.max_model_output_bytes) is not int or snapshot.max_model_output_bytes < 1
                or type(snapshot.elapsed_sec) not in (int, float)
                or not finite_number(snapshot.elapsed_sec) or snapshot.elapsed_sec < 0
                or type(snapshot.max_duration_sec) not in (int, float)
                or not finite_number(snapshot.max_duration_sec) or snapshot.max_duration_sec <= 0
                or (snapshot.reason is not None and type(snapshot.reason) is not str)
                or (not snapshot.exhausted and snapshot.elapsed_sec >= snapshot.max_duration_sec)):
            raise ValueError("Invalid budget snapshot")
        if self.checks:
            previous = self.checks[-1]
            if ((snapshot.scope_id, snapshot.max_provider_calls, snapshot.max_duration_sec,
                 snapshot.max_model_calls, snapshot.max_model_output_bytes)
                    != (previous.scope_id, previous.max_provider_calls, previous.max_duration_sec,
                        previous.max_model_calls, previous.max_model_output_bytes)
                    or snapshot.provider_calls < previous.provider_calls
                    or snapshot.model_calls < previous.model_calls
                    or snapshot.elapsed_sec < previous.elapsed_sec
                    or (previous.exhausted and not snapshot.exhausted)
                    or (reserve_provider_call and not snapshot.exhausted
                        and snapshot.provider_calls <= previous.provider_calls)
                    or (reserve_model_call and not snapshot.exhausted
                        and snapshot.model_calls != previous.model_calls + 1)):
                raise ValueError("Budget scope or counters changed")
        self.checks.append(BudgetSnapshot(
            snapshot.scope_id, snapshot.max_provider_calls, snapshot.provider_calls,
            snapshot.max_duration_sec, snapshot.elapsed_sec, snapshot.exhausted, snapshot.reason,
            snapshot.max_model_calls, snapshot.model_calls, snapshot.max_model_output_bytes,
        ))
        return not snapshot.exhausted


class HostExecutionBudget:
    """Shared, sticky, count/deadline budget. Cannot interrupt a blocking callback.

    The host must keep this instance outside controller control and reuse it
    across run IDs. Limits and scope are not supplied by individual run requests.
    """

    def __init__(self, scope_id: str, max_provider_calls: int, max_duration_sec: float,
                 *, clock: Callable[[], float] = monotonic, max_model_calls: int = 0,
                 max_model_output_bytes: int = 4096):
        if type(scope_id) is not str or not scope_id.strip():
            raise ValueError("scope_id must be a nonempty string")
        if type(max_provider_calls) is not int or max_provider_calls < 0:
            raise ValueError("max_provider_calls must be a nonnegative integer")
        if not finite_number(max_duration_sec) or max_duration_sec <= 0:
            raise ValueError("max_duration_sec must be finite and positive")
        if type(max_model_calls) is not int or max_model_calls < 0:
            raise ValueError("max_model_calls must be a nonnegative integer")
        if type(max_model_output_bytes) is not int or max_model_output_bytes < 1:
            raise ValueError("max_model_output_bytes must be a positive integer")
        self._max_model_calls, self._output_bytes = max_model_calls, max_model_output_bytes
        self._model_calls = 0
        self._scope_id, self._max_calls = scope_id, max_provider_calls
        self._duration, self._clock = max_duration_sec, clock
        self._start = clock()
        if not finite_number(self._start):
            raise ValueError("Invalid budget clock")
        self._last = self._start
        self._calls, self._elapsed, self._reason = 0, 0.0, None
        self._lock = Lock()

    def checkpoint(self, *, reserve_provider_call: bool = False,
                   reserve_model_call: bool = False) -> BudgetSnapshot:
        if reserve_model_call and reserve_provider_call:
            raise ValueError("Reserve one tool call at a time")
        with self._lock:
            if self._reason is None:
                try:
                    now = self._clock()
                    if not finite_number(now) or now < self._last:
                        raise ValueError("Invalid or regressing budget clock")
                    self._last = now
                    self._elapsed = now - self._start
                    if not finite_number(self._elapsed) or self._elapsed >= self._duration:
                        self._reason = "duration_exhausted"
                    elif reserve_provider_call:
                        if self._calls >= self._max_calls:
                            self._reason = "provider_calls_exhausted"
                        else:
                            self._calls += 1
                    elif reserve_model_call:
                        if self._model_calls >= self._max_model_calls:
                            self._reason = "model_calls_exhausted"
                        else:
                            self._model_calls += 1
                except Exception:
                    self._reason = "budget_clock_failed"
            return BudgetSnapshot(self._scope_id, self._max_calls, self._calls,
                                  self._duration, self._elapsed, self._reason is not None,
                                  self._reason, self._max_model_calls, self._model_calls,
                                  self._output_bytes)


@dataclass(frozen=True, slots=True)
class AdaptiveLimits:
    """Explicit research-run limits, not operational emergency policy defaults."""

    max_steps: int
    max_tool_attempts: int
    max_recovery_attempts: int
    max_retries_per_input: int
    max_no_progress: int

    def __post_init__(self):
        for value in (self.max_steps, self.max_tool_attempts, self.max_recovery_attempts,
                      self.max_retries_per_input, self.max_no_progress):
            if type(value) is not int or not 0 <= value <= 512:
                raise ValueError("Adaptive limits must be integers in [0,512]")
        if self.max_steps == 0 or self.max_no_progress == 0:
            raise ValueError("Step and no-progress ceilings must be positive")


@dataclass(frozen=True, slots=True)
class AdaptiveBudgetSnapshot:
    host: BudgetSnapshot
    steps: int
    tool_attempts: int
    recovery_attempts: int
    reason: str | None

    @property
    def exhausted(self):
        return self.reason is not None or self.host.exhausted


class AdaptiveExecutionBudget:
    """Retained M3 ledger around the unchanged host budget.

    Retain THIS instance across run IDs/controllers. Reservations are never
    refunded, including rejected/late operations. Provider/model reservations
    still belong to HostExecutionBudget; local evaluation costs time and one
    tool attempt, but is not a provider operation. M2 has no nested external
    operations: charge preparation/generation once, not again for verification.
    Future adapters with multiple external operations need explicit reservations.
    No durable crash recovery or forced interruption is provided.
    """

    def __init__(self, host: ExecutionBudget, limits: AdaptiveLimits):
        if type(limits) is not AdaptiveLimits:
            raise ValueError("Expected AdaptiveLimits")
        limits.__post_init__()
        self.limits = limits
        self._guard = BudgetGuard(host)
        self._steps = self._tools = self._recoveries = 0
        self._reason = None
        self._attempts = {}  # input -> (attempt count, recovery count, retryable)
        # One retained host session per adaptive ledger, never per run/planner.
        # The owner is a trusted registry object, not a caller-supplied string.
        self._session_owner = self._progress_state = None
        self._no_progress = 0
        self._evaluated_inputs = set()
        self._model_transport_stopped = False
        self._run_lock = Lock()
        self._lock = Lock()
        self.checkpoint()
        if not self._guard.checks:
            raise ValueError("Host budget supplied no valid initial snapshot")

    def claim_run(self):
        return self._run_lock.acquire(blocking=False)

    def release_run(self):
        self._run_lock.release()

    def bind_session(self, registry, progress_key):
        """Host controller binding; reconstruction cannot initialize guards again.

        A different registry requires an independent adaptive ledger. Identical
        textual scope/run IDs do not join otherwise independent ledger objects.
        Progress keys come only from the controller's validated registry state.
        Return True only for the initial binding, for host history bootstrap.
        """
        with self._lock:
            if self._session_owner is None:
                self._session_owner = registry
                self._progress_state = progress_key
                return True
            elif self._session_owner is not registry:
                raise ValueError("Adaptive ledger already belongs to another registry")
            return False

    def observe_progress(self, progress_key):
        """Host-only accepted semantic state, never planner-reported progress."""
        with self._lock:
            if self._session_owner is None:
                raise ValueError("Adaptive session is not bound")
            if progress_key == self._progress_state:
                return False
            self._progress_state = progress_key
            self._no_progress = 0
            return True

    @property
    def no_progress(self):
        with self._lock:
            return self._no_progress

    def record_progress_outcome(self, *, progress: bool):
        """Host reports independently accepted change or a no-progress attempt."""
        if type(progress) is not bool:
            raise ValueError("Expected host progress classification")
        with self._lock:
            self._no_progress = 0 if progress else self._no_progress + 1

    def remember_evaluation(self, identity):
        """Return whether this accepted evaluation input is new to the session."""
        if type(identity) is not str or len(identity) != 64:
            raise ValueError("Expected host input digest")
        with self._lock:
            new = identity not in self._evaluated_inputs
            self._evaluated_inputs.add(identity)
            return new

    def stop_model_transport(self):
        """Terminal for ALL model/provider identities on this retained ledger.

        No reset method or implicit provider fallback. This does not refund
        reservations or exhaust non-model resources: trusted rule planning may
        still use the same tools. A new host-authorized session uses a new ledger.
        """
        with self._lock:
            self._model_transport_stopped = True

    @property
    def model_transport_stopped(self):
        with self._lock:
            return self._model_transport_stopped

    def _check(self, **reservation):
        if self._reason is not None:
            return False
        try:
            if not self._guard.checkpoint(**reservation):
                self._reason = self._guard.checks[-1].reason or "host_budget_exhausted"
        except Exception:
            self._reason = "host_budget_unavailable"
        return self._reason is None

    def checkpoint(self):
        with self._lock:
            return self._check()

    def _stop(self, reason):
        self._reason = self._reason or reason
        return False

    def reserve_step(self):
        with self._lock:
            if not self._check():
                return False
            if self._steps >= self.limits.max_steps:
                return self._stop("steps_exhausted")
            self._steps += 1
            return True

    def attempt_status(self, identity):
        """Return (allowed, is_recovery); time/revision changes cannot reset it."""
        with self._lock:
            previous = self._attempts.get(identity)
            if previous is None:
                return True, False
            _, recoveries, retryable = previous
            return (retryable and recoveries < self.limits.max_retries_per_input
                    and self._recoveries < self.limits.max_recovery_attempts), True

    def reserve_tool(self, identity):
        if type(identity) is not str or len(identity) != 64:
            raise ValueError("Expected host input digest")
        with self._lock:
            if not self._check():
                return False
            if self._tools >= self.limits.max_tool_attempts:
                return self._stop("tool_attempts_exhausted")
            attempts, recoveries, retryable = self._attempts.get(identity, (0, 0, False))
            if attempts:
                if not retryable or recoveries >= self.limits.max_retries_per_input:
                    return False
                if self._recoveries >= self.limits.max_recovery_attempts:
                    return self._stop("recovery_attempts_exhausted")
                self._recoveries += 1
                recoveries += 1
            self._tools += 1
            self._attempts[identity] = (attempts + 1, recoveries, False)
            return True

    def finish_tool(self, identity, *, retryable: bool):
        if type(retryable) is not bool:
            raise ValueError("Expected host retry classification")
        with self._lock:
            attempts, recoveries, _ = self._attempts[identity]
            self._attempts[identity] = (attempts, recoveries, retryable)

    def reserve_provider(self):
        with self._lock:
            return self._check(reserve_provider_call=True)

    def reserve_model(self):
        """One M4 model attempt; a stopped session never reserves another."""
        with self._lock:
            if self._model_transport_stopped or self._no_progress >= self.limits.max_no_progress:
                return False
            return self._check(reserve_model_call=True)

    @property
    def snapshot(self):
        with self._lock:
            # A failed initial host checkpoint has no valid snapshot to expose.
            if not self._guard.checks:
                raise ValueError("Host budget unavailable")
            return AdaptiveBudgetSnapshot(self._guard.checks[-1], self._steps,
                                          self._tools, self._recoveries, self._reason)
