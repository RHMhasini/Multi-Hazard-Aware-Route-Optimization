"""M5.1 explicit local SQLite historical checkpoint store.

No automatic capture, resume, dispatch, artifact reuse or human continuation.
No integrity key is provisioned: load_history is NOT authenticated restoration.
ACLs/backups are deployment responsibilities. An ordinary fingerprint does not
detect a validly rewritten record or rollback of a whole database. Protected
restoration fails closed, including when a caller claims to have checked hashes.

Paths must be explicit, outside this repository, on D:. SQLite uses rollback
journaling, synchronous EXTRA and foreign keys. No DB is opened on import.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from functools import wraps
from pathlib import Path
import sqlite3

from .execution import AdaptiveExecutionBudget
from .session_contracts import (
    MAX_BYTES, CheckpointBindings, SessionCheckpoint, _digest, _id, _require,
    decode_checkpoint, encode_checkpoint, fingerprint, utc_text,
)
from .tool_registry import ToolRegistry


class CheckpointError(ValueError):
    """Fixed host category; never retain database errors or record contents."""


_ERRORS = frozenset({"checkpoint_unavailable", "checkpoint_conflict", "checkpoint_missing",
                    "checkpoint_incompatible", "checkpoint_busy", "protected_restore_unavailable"})


def _boundary(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        failed = None
        try:
            return method(*args, **kwargs)
        except CheckpointError as exc:
            failed = str(exc) if str(exc) in _ERRORS else "checkpoint_unavailable"
        except Exception:
            failed = "checkpoint_unavailable"
        # Outside the handler: SQLite/JSON exceptions may contain private input.
        raise CheckpointError(failed) from None
    return wrapped


def bindings_for(registry, *, mission_digest, policy_digest, configuration_digest):
    """Trusted host supplies full external configuration digests (see contracts).

    Bound observation/registry data augment these labels. They never substitute
    for independently verified artifacts or for authenticating the store.
    """
    _require(type(registry) is ToolRegistry)
    for value in (mission_digest, policy_digest, configuration_digest):
        _digest(value)
    state = registry.state
    state.__post_init__()
    return CheckpointBindings(
        fingerprint((state.mission_ref, state.purpose, mission_digest)),
        fingerprint((state.policy_ref, state.policy, policy_digest)),
        fingerprint((registry.checkpoint_configuration, state.capabilities)),
        configuration_digest, fingerprint(state),
    )


@_boundary
def capture_checkpoint(budget, registry, *, session_id, storage_version=0,
                       utc_clock, mission_digest, policy_digest, configuration_digest,
                       previous=None):
    """Explicit quiescent capture; no automatic hooks into nonpersistent behavior.

    Snapshot only between runs/dispatches. Uncertain in-flight execution cannot
    be checkpointed through this API; journaling is a later milestone. Updates
    require the previously loaded/captured history to carry conservative elapsed
    time forward even when an exhausted live clock snapshot has become sticky.
    This can expire the historical ledger; it never grants live allowances.
    """
    _require(type(budget) is AdaptiveExecutionBudget and type(registry) is ToolRegistry)
    if not budget.claim_run():
        raise CheckpointError("checkpoint_busy")
    control = None
    try:
        control = registry.claim_control()
        # Timestamp first: the small capture interval is conservatively counted
        # again by elapsed_at, never excluded from the allowance.
        captured = utc_text(utc_clock())
        bindings = bindings_for(registry, mission_digest=mission_digest,
                                policy_digest=policy_digest, configuration_digest=configuration_digest)
        ledger = budget.export_history(registry)
        if previous is not None:
            _require(type(previous) is SessionCheckpoint)
            previous.__post_init__()
            elapsed = max(ledger.host.elapsed_sec, previous.elapsed_at(datetime.fromisoformat(captured)))
            host_reason = ledger.host.reason
            if elapsed >= ledger.host.max_duration_sec:
                host_reason = host_reason or "duration_exhausted"
            ledger = replace(ledger, host=replace(ledger.host, elapsed_sec=elapsed, reason=host_reason),
                             reason=ledger.reason or host_reason)
        else:
            _require(storage_version == 0)
        result = SessionCheckpoint(session_id, storage_version, registry.state.state_revision,
                                   bindings, ledger, captured)
        if previous is not None:
            _forward(previous, result)
        # Detach nested records, revalidate and enforce the wire-size limit now.
        return decode_checkpoint(encode_checkpoint(result))
    finally:
        if control is not None:
            registry.release_control(control)
        budget.release_run()


def _forward(old, new):
    """CAS may advance a history, never silently allocate fresh allowances."""
    a, b = old.ledger, new.ledger
    _require(old.session_id == new.session_id
             and new.storage_version == old.storage_version + 1
             and new.dependency_revision >= old.dependency_revision
             and new.captured_at_utc >= old.captured_at_utc)
    _require((old.bindings.mission, old.bindings.policy, old.bindings.registry, old.bindings.configuration)
             == (new.bindings.mission, new.bindings.policy, new.bindings.registry, new.bindings.configuration))
    _require(a.limits == b.limits and
             (a.host.budget_scope_id, a.host.max_provider_calls, a.host.max_model_calls,
              a.host.max_model_output_bytes, a.host.max_duration_sec) ==
             (b.host.budget_scope_id, b.host.max_provider_calls, b.host.max_model_calls,
              b.host.max_model_output_bytes, b.host.max_duration_sec))
    _require(all(y >= x for x, y in ((a.steps, b.steps), (a.tool_attempts, b.tool_attempts),
                                   (a.recovery_attempts, b.recovery_attempts),
                                   (a.host.provider_calls, b.host.provider_calls),
                                   (a.host.model_calls, b.host.model_calls))))
    # All downtime consumes time, even if a new in-memory budget was substituted.
    _require(b.host.elapsed_sec >= old.elapsed_at(datetime.fromisoformat(new.captured_at_utc)))
    _require((a.reason is None or b.reason == a.reason)
             and (a.host.reason is None or b.host.reason == a.host.reason)
             and (not a.model_transport_stopped or b.model_transport_stopped)
             and (not a.session_bound or b.session_bound)
             and set(a.evaluated_inputs) <= set(b.evaluated_inputs))
    if a.progress_identity == b.progress_identity:
        _require(b.no_progress >= a.no_progress)
    previous = {x.identity: x for x in a.attempts}
    current = {x.identity: x for x in b.attempts}
    _require(previous.keys() <= current.keys())
    for identity, before in previous.items():
        after = current[identity]
        _require(after.attempts >= before.attempts and after.recoveries >= before.recoveries)


class MissionStore:
    def __init__(self, path):
        # Resolve existing parent links before opening; deployments must also
        # protect the directory from hostile replacement (not a Python sandbox).
        _require(type(path) in (str, type(Path())))
        candidate = Path(path)
        _require(candidate.is_absolute())
        self.path = candidate.resolve(strict=False)
        repository = Path(__file__).resolve().parents[2]
        _require(self.path.drive.lower() == "d:" and not self.path.is_relative_to(repository))
        _require(self.path.parent.is_dir())

    @classmethod
    @_boundary
    def initialize(cls, path):
        """EXPLICIT new-store creation only. Never overwrite an existing store."""
        store = cls(path)
        # Exclusive creation distinguishes a new store from a missing expected
        # store. Normal opens always use mode=rw and never create a replacement.
        with store.path.open("xb"):
            pass
        try:
            with store._connection(check_schema=False) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE checkpoints (session_id TEXT PRIMARY KEY, "
                           "budget_scope_id TEXT NOT NULL UNIQUE, storage_version INTEGER NOT NULL "
                           "CHECK(storage_version >= 0), payload BLOB NOT NULL "
                           "CHECK(length(payload) <= 131072))")
                db.execute("PRAGMA user_version=1")
                db.commit()
        except Exception:
            store.path.unlink(missing_ok=True)
            raise
        return store

    @classmethod
    @_boundary
    def open_existing(cls, path):
        store = cls(path)
        with store._connection():
            pass
        return store

    @contextmanager
    def _connection(self, *, check_schema=True):
        if not self.path.is_file():
            raise CheckpointError("checkpoint_missing")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=1,
                             isolation_level=None)
        try:
            # Do not switch an unexpected WAL store into rollback mode silently.
            _require(db.execute("PRAGMA journal_mode").fetchone()[0] == "delete")
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA foreign_keys=ON")
            _require(db.execute("PRAGMA synchronous").fetchone()[0] == 3
                     and db.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
            if check_schema:
                _require(db.execute("PRAGMA user_version").fetchone()[0] == 1)
                _require(db.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok")
                columns = tuple(row[1] for row in db.execute("PRAGMA table_info(checkpoints)"))
                _require(columns == ("session_id", "budget_scope_id", "storage_version", "payload"))
            yield db
        finally:
            try:
                if db.in_transaction:
                    db.rollback()
            finally:
                db.close()

    def _read(self, db, session_id):
        _id(session_id)
        row = db.execute("SELECT budget_scope_id, storage_version, "
                         "CASE WHEN length(payload) <= ? THEN payload ELSE NULL END "
                         "FROM checkpoints WHERE session_id=?", (MAX_BYTES, session_id)).fetchone()
        if row is None:
            raise CheckpointError("checkpoint_missing")
        result = decode_checkpoint(row[2])
        _require(result.session_id == session_id and result.storage_version == row[1]
                 and result.ledger.host.budget_scope_id == row[0])
        return result

    @_boundary
    def create(self, checkpoint):
        data = encode_checkpoint(checkpoint)
        _require(checkpoint.storage_version == 0)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO checkpoints VALUES (?,?,?,?)",
                       (checkpoint.session_id, checkpoint.ledger.host.budget_scope_id, 0, data))
            db.commit()

    @_boundary
    def load_history(self, session_id, *, expected_bindings, now):
        """Historical data only. Expected bindings come from CURRENT trusted host.

        Mandatory clock/binding checks detect accidental incompatibility; they do
        not authenticate the DB, reconstruct evidence authority or grant rights.
        Expired/exhausted histories remain inspectable with their original limits.
        """
        _require(type(expected_bindings) is CheckpointBindings)
        expected_bindings.__post_init__()
        with self._connection() as db:
            result = self._read(db, session_id)
        if result.bindings != expected_bindings:
            raise CheckpointError("checkpoint_incompatible")
        result.elapsed_at(now)
        return result

    @_boundary
    def compare_and_swap(self, checkpoint, *, expected_version):
        data = encode_checkpoint(checkpoint)
        _require(type(expected_version) is int and expected_version >= 0)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._read(db, checkpoint.session_id)
            if old.storage_version != expected_version:
                raise CheckpointError("checkpoint_conflict")
            _forward(old, checkpoint)
            changed = db.execute("UPDATE checkpoints SET storage_version=?, payload=? "
                                 "WHERE session_id=? AND storage_version=?",
                                 (checkpoint.storage_version, data, checkpoint.session_id,
                                  expected_version)).rowcount
            _require(changed == 1)
            db.commit()

    def restore_protected(self, *args, **kwargs):
        """No provisioned integrity key/recovery gate; fail before touching data.

        Key provisioning, rollback detection and trusted execution rehydration
        are explicit open decisions. Never use GROQ_API_KEY for store integrity.
        """
        raise CheckpointError("protected_restore_unavailable")
