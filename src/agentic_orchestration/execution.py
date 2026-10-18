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


class ExecutionBudget(Protocol):
    """Host retains one budget across runs; reserve atomically BEFORE provider I/O."""

    def checkpoint(self, *, reserve_provider_call: bool = False) -> BudgetSnapshot: ...


class BudgetGuard:
    """Validate a host budget's immutable responses across a workflow boundary."""

    def __init__(self, budget: ExecutionBudget):
        self._budget = budget
        self.checks: list[BudgetSnapshot] = []

    def checkpoint(self, *, reserve_provider_call: bool = False) -> bool:
        snapshot = self._budget.checkpoint(reserve_provider_call=reserve_provider_call)
        if type(snapshot) is not BudgetSnapshot:
            raise ValueError("Invalid budget response")
        # All fields are checked before retaining the record; no arbitrary payload.
        if (type(snapshot.exhausted) is not bool
                or type(snapshot.scope_id) is not str or not snapshot.scope_id.strip()
                or type(snapshot.provider_calls) is not int or snapshot.provider_calls < 0
                or type(snapshot.max_provider_calls) is not int
                or snapshot.max_provider_calls < snapshot.provider_calls
                or type(snapshot.elapsed_sec) not in (int, float)
                or not finite_number(snapshot.elapsed_sec) or snapshot.elapsed_sec < 0
                or type(snapshot.max_duration_sec) not in (int, float)
                or not finite_number(snapshot.max_duration_sec) or snapshot.max_duration_sec <= 0
                or (snapshot.reason is not None and type(snapshot.reason) is not str)
                or (not snapshot.exhausted and snapshot.elapsed_sec >= snapshot.max_duration_sec)):
            raise ValueError("Invalid budget snapshot")
        if self.checks:
            previous = self.checks[-1]
            if ((snapshot.scope_id, snapshot.max_provider_calls, snapshot.max_duration_sec)
                    != (previous.scope_id, previous.max_provider_calls, previous.max_duration_sec)
                    or snapshot.provider_calls < previous.provider_calls
                    or snapshot.elapsed_sec < previous.elapsed_sec
                    or (previous.exhausted and not snapshot.exhausted)
                    or (reserve_provider_call and not snapshot.exhausted
                        and snapshot.provider_calls <= previous.provider_calls)):
                raise ValueError("Budget scope or counters changed")
        self.checks.append(BudgetSnapshot(
            snapshot.scope_id, snapshot.max_provider_calls, snapshot.provider_calls,
            snapshot.max_duration_sec, snapshot.elapsed_sec, snapshot.exhausted, snapshot.reason,
        ))
        return not snapshot.exhausted


class HostExecutionBudget:
    """Shared, sticky, count/deadline budget. Cannot interrupt a blocking callback.

    The host must keep this instance outside controller control and reuse it
    across run IDs. Limits and scope are not supplied by individual run requests.
    """

    def __init__(self, scope_id: str, max_provider_calls: int, max_duration_sec: float,
                 *, clock: Callable[[], float] = monotonic):
        if type(scope_id) is not str or not scope_id.strip():
            raise ValueError("scope_id must be a nonempty string")
        if type(max_provider_calls) is not int or max_provider_calls < 0:
            raise ValueError("max_provider_calls must be a nonnegative integer")
        if not finite_number(max_duration_sec) or max_duration_sec <= 0:
            raise ValueError("max_duration_sec must be finite and positive")
        self._scope_id, self._max_calls = scope_id, max_provider_calls
        self._duration, self._clock = max_duration_sec, clock
        self._start = clock()
        if not finite_number(self._start):
            raise ValueError("Invalid budget clock")
        self._last = self._start
        self._calls, self._elapsed, self._reason = 0, 0.0, None
        self._lock = Lock()

    def checkpoint(self, *, reserve_provider_call: bool = False) -> BudgetSnapshot:
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
                except Exception:
                    self._reason = "budget_clock_failed"
            return BudgetSnapshot(self._scope_id, self._max_calls, self._calls,
                                  self._duration, self._elapsed, self._reason is not None,
                                  self._reason)
