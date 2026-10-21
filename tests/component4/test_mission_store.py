"""M5.1 checkpoints only. Temporary D: databases; no providers or tool dispatch."""

from contextlib import closing
from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.agentic_orchestration.adaptive_controller import _progress_key
from src.agentic_orchestration.execution import AdaptiveExecutionBudget, AdaptiveLimits, HostExecutionBudget
from src.agentic_orchestration.mission_store import (
    CheckpointError, MissionStore, bindings_for, capture_checkpoint,
)
from src.agentic_orchestration.session_contracts import (
    CheckpointBindings, ExecutionLedger, HostLedger, InputAttempt, SessionCheckpoint,
    decode_checkpoint, encode_checkpoint, fingerprint,
)
from synthetic_oracles import NOW, environment


class DurableCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="c4-m51-", dir="D:\\")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store = MissionStore.initialize(self.path)
        self.seconds = 0.0
        self.registry, self.tools, _ = environment(graph=None, hazard=None)
        self.budget = self.new_budget()
        self.budget.bind_session(self.registry, _progress_key(self.registry.state))
        self.digests = dict(mission_digest="1" * 64, policy_digest="2" * 64,
                            configuration_digest="3" * 64)

    def new_budget(self, scope="scope-one", *, providers=3, models=2, steps=5,
                   tools=4, recoveries=1, retries=1, no_progress=2):
        host = HostExecutionBudget(scope, providers, 120, clock=lambda: self.seconds,
                                   max_model_calls=models, max_model_output_bytes=4096)
        return AdaptiveExecutionBudget(host, AdaptiveLimits(steps, tools, recoveries, retries, no_progress))

    def capture(self, *, budget=None, registry=None, session="session-one", version=0, previous=None):
        return capture_checkpoint(budget or self.budget, registry or self.registry,
                                  session_id=session, storage_version=version,
                                  utc_clock=lambda: NOW + timedelta(seconds=self.seconds),
                                  previous=previous, **self.digests)

    def load(self, checkpoint):
        return self.store.load_history(checkpoint.session_id, expected_bindings=checkpoint.bindings,
                                       now=NOW + timedelta(seconds=self.seconds))

    def write_payload(self, data):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE checkpoints SET payload=?", (data,))

    def test_round_trip_preserves_complete_ledger_and_separate_revisions(self):
        budget = self.budget
        self.assertTrue(budget.reserve_step())
        self.assertTrue(budget.reserve_model())
        self.assertTrue(budget.reserve_provider())
        identity = "a" * 64
        self.assertTrue(budget.reserve_tool(identity))
        budget.finish_tool(identity, retryable=True)
        self.assertTrue(budget.reserve_tool(identity))
        budget.finish_tool(identity, retryable=False)
        budget.remember_evaluation("b" * 64)
        budget.record_progress_outcome(progress=False)
        budget.stop_model_transport()
        self.seconds = 7
        checkpoint = self.capture()
        self.store.create(checkpoint)
        loaded = self.load(checkpoint)
        self.assertEqual(loaded, checkpoint)
        history = loaded.ledger
        self.assertEqual((history.steps, history.tool_attempts, history.recovery_attempts), (1, 2, 1))
        self.assertEqual((history.host.provider_calls, history.host.model_calls, history.host.elapsed_sec), (1, 1, 7))
        self.assertEqual(history.attempts, (InputAttempt(identity, 2, 1, False),))
        self.assertEqual(history.evaluated_inputs, ("b" * 64,))
        self.assertEqual(history.no_progress, 1)
        self.assertEqual(history.progress_identity, fingerprint(_progress_key(self.registry.state)))
        self.assertTrue(history.session_bound)
        self.assertTrue(history.model_transport_stopped)
        self.assertNotEqual(loaded.session_id, history.host.budget_scope_id)
        self.assertEqual(history.limits, budget.limits)
        self.assertEqual(history.host.max_model_output_bytes, 4096)
        self.assertEqual(loaded.dependency_revision, self.registry.state.state_revision)
        self.assertEqual(loaded.storage_version, 0)

    def test_restart_in_fresh_process_retains_guards_without_dispatch(self):
        self.budget.reserve_model()
        self.budget.record_progress_outcome(progress=False)
        self.budget.record_progress_outcome(progress=False)
        self.budget.stop_model_transport()
        self.budget.reserve_tool("a" * 64)
        self.budget.remember_evaluation("b" * 64)
        checkpoint = self.capture()
        self.store.create(checkpoint)
        script = """
import json, sys
from datetime import datetime
from unittest.mock import patch
from src.agentic_orchestration.mission_store import MissionStore
from src.agentic_orchestration.session_contracts import CheckpointBindings
with patch('socket.socket', side_effect=AssertionError('network')), patch(
    'src.agentic_orchestration.groq_client.GroqModelClient.complete', side_effect=AssertionError('model')), patch(
    'src.agentic_orchestration.tool_registry.ToolRegistry.execute', side_effect=AssertionError('tool')):
    value = MissionStore.open_existing(sys.argv[1]).load_history(
        'session-one', expected_bindings=CheckpointBindings(**json.loads(sys.argv[2])),
        now=datetime.fromisoformat(sys.argv[3]))
    x = value.ledger
    assert not hasattr(x, 'reserve_model') and not hasattr(x, 'reserve_tool')
    print(json.dumps([x.host.model_calls, x.no_progress, x.model_transport_stopped,
                      x.model_blocked, len(x.attempts), len(x.evaluated_inputs)]))
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(self.path),
                                 json.dumps(asdict(checkpoint.bindings)), NOW.isoformat()],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [1, 2, True, True, 1, 1])

    def test_all_sticky_budget_reasons_survive(self):
        cases = (
            (dict(models=0), lambda b: b.reserve_model(), "model_calls_exhausted"),
            (dict(providers=0), lambda b: b.reserve_provider(), "provider_calls_exhausted"),
            (dict(steps=1), lambda b: (b.reserve_step(), b.reserve_step()), "steps_exhausted"),
            (dict(tools=0), lambda b: b.reserve_tool("a" * 64), "tool_attempts_exhausted"),
        )
        for index, (settings, operation, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                budget = self.new_budget(scope=f"scope-{index}", **settings)
                operation(budget)
                checkpoint = self.capture(budget=budget, session=f"session-{index}")
                self.store.create(checkpoint)
                loaded = self.load(checkpoint)
                self.assertTrue(loaded.ledger.exhausted)
                self.assertTrue(loaded.ledger.model_blocked)
                self.assertEqual(loaded.ledger.reason, reason)

    def test_duration_exhaustion_survives(self):
        self.seconds = 121
        self.budget.checkpoint()
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.assertEqual(self.load(checkpoint).ledger.host.reason, "duration_exhausted")

    def test_clock_failure_survives(self):
        self.seconds = -1
        self.budget.checkpoint()
        self.seconds = 0
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.assertEqual(self.load(checkpoint).ledger.host.reason, "budget_clock_failed")

    def test_retryable_input_history_survives(self):
        self.budget.reserve_tool("a" * 64)
        self.budget.finish_tool("a" * 64, retryable=True)
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.assertEqual(self.load(checkpoint).ledger.attempts, (InputAttempt("a" * 64, 1, 0, True),))

    def test_independent_sessions_do_not_share_budget_or_guards(self):
        self.budget.reserve_model()
        self.budget.stop_model_transport()
        first = self.capture()
        second = self.capture(budget=self.new_budget("scope-two"), session="session-two")
        self.store.create(first)
        self.store.create(second)
        self.assertEqual(self.load(first).ledger.host.model_calls, 1)
        self.assertEqual(self.load(second).ledger.host.model_calls, 0)
        self.assertFalse(self.load(second).ledger.model_transport_stopped)
        with self.assertRaises(CheckpointError):
            self.store.create(replace(first, session_id="session-three"))
        with self.assertRaises(CheckpointError):
            self.capture(session="scope-one")

    def test_cas_advances_storage_version_without_changing_dependency_revision(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.budget.reserve_model()
        self.budget.record_progress_outcome(progress=False)
        next_checkpoint = self.capture(version=1, previous=checkpoint)
        self.store.compare_and_swap(next_checkpoint, expected_version=0)
        self.assertEqual(self.load(next_checkpoint), next_checkpoint)
        self.assertEqual(checkpoint.dependency_revision, next_checkpoint.dependency_revision)
        with self.assertRaisesRegex(CheckpointError, "checkpoint_conflict"):
            MissionStore.open_existing(self.path).compare_and_swap(next_checkpoint, expected_version=0)
        self.assertEqual(self.load(next_checkpoint).storage_version, 1)

    def test_cas_rejects_counter_limit_guard_and_binding_resets(self):
        self.budget.reserve_step()
        self.budget.reserve_model()
        self.budget.reserve_tool("a" * 64)
        self.budget.remember_evaluation("b" * 64)
        self.budget.record_progress_outcome(progress=False)
        self.budget.stop_model_transport()
        checkpoint = self.capture()
        self.store.create(checkpoint)
        history = checkpoint.ledger
        bad_ledgers = (
            replace(history, steps=0),
            replace(history, host=replace(history.host, model_calls=0)),
            replace(history, host=replace(history.host, max_model_calls=99)),
            replace(history, model_transport_stopped=False),
            replace(history, evaluated_inputs=()),
            replace(history, no_progress=0),
            replace(history, attempts=(), tool_attempts=0),
            replace(history, limits=replace(history.limits, max_steps=99)),
        )
        for ledger in bad_ledgers:
            with self.subTest(ledger=ledger), self.assertRaises(CheckpointError):
                self.store.compare_and_swap(replace(checkpoint, storage_version=1, ledger=ledger), expected_version=0)
        for field in ("mission", "policy", "registry", "configuration"):
            with self.subTest(field=field), self.assertRaises(CheckpointError):
                self.store.compare_and_swap(replace(checkpoint, storage_version=1,
                    bindings=replace(checkpoint.bindings, **{field: "f" * 64})), expected_version=0)
        self.assertEqual(self.load(checkpoint), checkpoint)

    def test_transaction_failure_after_write_rolls_back(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        # Simulate interruption after SQL UPDATE but before commit. The real
        # connection context rolls back; no application test hook is needed.
        with self.assertRaises(RuntimeError):
            with self.store._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE checkpoints SET storage_version=1")
                raise RuntimeError("injected interruption")
        self.assertEqual(self.load(checkpoint), checkpoint)

    def test_sql_update_failure_is_atomic_and_redacted(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON checkpoints "
                       "BEGIN SELECT RAISE(ABORT, 'PRIVATE_SENTINEL'); END")
        with self.assertRaises(CheckpointError) as raised:
            self.store.compare_and_swap(replace(checkpoint, storage_version=1), expected_version=0)
        self.assertEqual(str(raised.exception), "checkpoint_unavailable")
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(self.load(checkpoint), checkpoint)

    def test_corruption_schema_unknown_fields_and_duplicate_keys_reject(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        original = json.loads(encode_checkpoint(checkpoint))
        changes = (
            b"PRIVATE_SENTINEL invalid json",
            json.dumps(original | {"schema_version": "unsupported"}).encode(),
            json.dumps(original | {"raw_output": "PRIVATE_SENTINEL"}).encode(),
            b'{"schema_version":"one","schema_version":"two"}',
            json.dumps(original | {"storage_version": True}).encode(),
            b"[" * 2000,
        )
        for data in changes:
            self.write_payload(data)
            with self.assertRaises(CheckpointError) as raised:
                self.load(checkpoint)
            self.assertEqual(str(raised.exception), "checkpoint_unavailable")
            self.assertIsNone(raised.exception.__context__)

    def test_invalid_typed_fields_and_cross_counter_inconsistencies_reject(self):
        checkpoint = self.capture()
        original = json.loads(encode_checkpoint(checkpoint))
        for field, bad in (("steps", True), ("no_progress", -1), ("reason", "PRIVATE_SENTINEL"),
                           ("model_transport_stopped", 1), ("tool_attempts", 1),
                           ("session_bound", False), ("evaluated_inputs", ["not-a-digest"])):
            with self.subTest(field=field):
                changed = original | {"ledger": original["ledger"] | {field: bad}}
                with self.assertRaises(ValueError):
                    decode_checkpoint(json.dumps(changed).encode())
        for bad in (float("nan"), float("inf"), True, -1, "30"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(checkpoint.ledger.host, elapsed_sec=bad)

    def test_row_identity_and_storage_version_must_match_payload(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE checkpoints SET storage_version=3")
        with self.assertRaises(CheckpointError):
            self.load(checkpoint)

    def test_missing_expected_database_never_creates_replacement(self):
        absent = Path(self.temp.name) / "absent.sqlite3"
        with self.assertRaisesRegex(CheckpointError, "checkpoint_missing"):
            MissionStore.open_existing(absent)
        self.assertFalse(absent.exists())
        self.path.unlink()
        with self.assertRaisesRegex(CheckpointError, "checkpoint_missing"):
            self.store.create(self.capture())
        self.assertFalse(self.path.exists())

    def test_existing_store_is_not_overwritten(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        with self.assertRaises(CheckpointError):
            MissionStore.initialize(self.path)
        self.assertEqual(self.load(checkpoint), checkpoint)

    def test_database_schema_and_non_database_files_reject(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(CheckpointError):
            MissionStore.open_existing(self.path)
        invalid = Path(self.temp.name) / "invalid.sqlite3"
        invalid.write_bytes(b"PRIVATE_SENTINEL")
        with self.assertRaises(CheckpointError) as raised:
            MissionStore.open_existing(invalid)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))

    def test_required_current_bindings_are_checked(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        for name in ("mission", "policy", "registry", "configuration", "evidence"):
            with self.subTest(name=name), self.assertRaisesRegex(CheckpointError, "checkpoint_incompatible"):
                self.store.load_history(checkpoint.session_id, now=NOW,
                    expected_bindings=replace(checkpoint.bindings, **{name: "f" * 64}))
        with self.assertRaises(ValueError):
            CheckpointBindings("wrong", "2" * 64, "3" * 64, "4" * 64, "5" * 64)

    def test_capture_rejects_another_registry_and_releases_locks(self):
        other, _, _ = environment(graph=None, hazard=None)
        with self.assertRaises(CheckpointError):
            self.capture(registry=other)
        self.assertEqual(self.capture().ledger.host.model_calls, 0)

    def test_capture_cannot_overlap_controller_or_dispatch(self):
        self.assertTrue(self.budget.claim_run())
        try:
            with self.assertRaisesRegex(CheckpointError, "checkpoint_busy"):
                self.capture()
        finally:
            self.budget.release_run()
        control = self.registry.claim_control()
        try:
            with self.assertRaises(CheckpointError):
                self.capture()
        finally:
            self.registry.release_control(control)
        self.assertIsInstance(self.capture(), SessionCheckpoint)

    def test_timing_is_conservative_and_rejects_regression_or_naive_clock(self):
        self.seconds = 7
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.assertEqual(checkpoint.elapsed_at(NOW + timedelta(seconds=17)), 17)
        self.assertEqual(checkpoint.elapsed_at(NOW + timedelta(seconds=200)), 200)
        for bad in (NOW, NOW.replace(tzinfo=None), None):
            with self.subTest(bad=bad), self.assertRaises(CheckpointError):
                self.store.load_history(checkpoint.session_id, expected_bindings=checkpoint.bindings, now=bad)
        with self.assertRaises(CheckpointError):
            self.store.compare_and_swap(replace(checkpoint, storage_version=1,
                captured_at_utc=(NOW + timedelta(seconds=10)).isoformat()), expected_version=0)

    def test_private_values_are_absent_from_records_and_failures(self):
        # Unknown runtime data is not reflected into the closed checkpoint schema.
        self.budget.private_debug = "PRIVATE_SENTINEL"
        self.registry.private_debug = "PRIVATE_SENTINEL"
        checkpoint = self.capture()
        self.store.create(checkpoint)
        with closing(sqlite3.connect(self.path)) as db, db:
            stored = db.execute("SELECT payload FROM checkpoints").fetchone()[0]
        for forbidden in (b"PRIVATE_SENTINEL", b"Authorization", b"GROQ_API_KEY", b"reasoning",
                          b"raw_output", b"_start", b"_session_owner", b"verification_ref"):
            self.assertNotIn(forbidden, stored)
        self.assertFalse(self.tools.calls)

    def test_protected_restore_unavailable_even_for_structurally_valid_history(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        self.assertIsInstance(self.load(checkpoint), SessionCheckpoint)
        with self.assertRaisesRegex(CheckpointError, "protected_restore_unavailable"):
            self.store.restore_protected(checkpoint.session_id)
        self.assertFalse(hasattr(checkpoint.ledger, "reserve_model"))
        self.assertFalse(hasattr(checkpoint.ledger, "reserve_tool"))

    def test_restore_is_data_only_and_never_invokes_model_tools_or_network(self):
        checkpoint = self.capture()
        self.store.create(checkpoint)
        with patch("socket.socket", side_effect=AssertionError("network")), patch(
            "src.agentic_orchestration.groq_client.GroqModelClient.complete", side_effect=AssertionError("model")
        ), patch("src.agentic_orchestration.tool_registry.ToolRegistry.execute", side_effect=AssertionError("tool")):
            self.assertEqual(self.load(checkpoint), checkpoint)
        self.assertFalse(self.tools.calls)

    def test_paths_must_be_explicit_external_and_on_d_drive(self):
        for bad in ("relative.sqlite3", "C:\\checkpoint.sqlite3", Path.cwd() / "checkpoint.sqlite3"):
            with self.subTest(path=bad), self.assertRaises(CheckpointError):
                MissionStore.initialize(bad)
        self.assertFalse((Path.cwd() / "checkpoint.sqlite3").exists())

    def test_rollback_journal_extra_durability_and_foreign_keys(self):
        with self.store._connection() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 3)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertFalse(Path(str(self.path) + "-wal").exists())

    def test_mutated_frozen_records_are_revalidated(self):
        checkpoint = self.capture()
        object.__setattr__(checkpoint.ledger.host, "model_calls", True)
        with self.assertRaises(CheckpointError):
            self.store.create(checkpoint)

    def test_updates_carry_all_downtime_and_keep_sticky_exhaustion(self):
        budget = self.new_budget("scope-exhausted", models=0)
        budget.reserve_model()
        original = self.capture(budget=budget)
        self.store.create(original)
        self.seconds = 10
        updated = self.capture(budget=budget, version=1, previous=original)
        self.store.compare_and_swap(updated, expected_version=0)
        self.assertEqual(self.load(updated).ledger.host.elapsed_sec, 10)
        self.assertEqual(self.load(updated).ledger.host.reason, "model_calls_exhausted")
        self.assertTrue(self.load(updated).ledger.model_blocked)
        self.seconds = 20
        final = self.capture(budget=budget, version=2, previous=updated)
        self.store.compare_and_swap(final, expected_version=1)
        self.assertEqual(self.load(final).ledger.host.elapsed_sec, 20)

    def test_compare_and_swap_has_one_winner_across_connections(self):
        from concurrent.futures import ThreadPoolExecutor
        checkpoint = self.capture()
        self.store.create(checkpoint)
        updated = replace(checkpoint, storage_version=1)

        def update(_):
            try:
                MissionStore.open_existing(self.path).compare_and_swap(updated, expected_version=0)
                return "committed"
            except CheckpointError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(update, (1, 2)))
        self.assertCountEqual(results, ["committed", "checkpoint_conflict"])
        self.assertEqual(self.load(updated).storage_version, 1)

    def test_capture_requires_previous_history_for_update(self):
        with self.assertRaises(CheckpointError):
            self.capture(version=1)

    def test_cas_cannot_clear_exhaustion_or_change_session_scope(self):
        budget = self.new_budget("scope-exhausted", models=0)
        budget.reserve_model()
        checkpoint = self.capture(budget=budget)
        self.store.create(checkpoint)
        history = checkpoint.ledger
        cleared = replace(history, reason=None, host=replace(history.host, reason=None))
        with self.assertRaises(CheckpointError):
            self.store.compare_and_swap(replace(checkpoint, storage_version=1, ledger=cleared), expected_version=0)
        changed_scope = replace(history, host=replace(history.host, budget_scope_id="scope-replacement"))
        with self.assertRaises(CheckpointError):
            self.store.compare_and_swap(replace(checkpoint, storage_version=1, ledger=changed_scope), expected_version=0)
        self.assertEqual(self.load(checkpoint), checkpoint)

    def test_oversized_payload_and_unsupported_wal_store_reject(self):
        checkpoint = self.capture()
        with self.assertRaises(ValueError):
            decode_checkpoint(b" " * 131073)
        self.store.create(checkpoint)
        # Simulate an incompatible existing store without enabling WAL anywhere.
        connection = MagicMock()
        connection.in_transaction = False
        connection.execute.return_value.fetchone.return_value = ("wal",)
        with patch("src.agentic_orchestration.mission_store.sqlite3.connect", return_value=connection):
            with self.assertRaises(CheckpointError):
                MissionStore.open_existing(self.path)
        connection.close.assert_called_once()

    def test_failed_initialization_does_not_leave_partial_database(self):
        failed_path = Path(self.temp.name) / "failed.sqlite3"
        with patch("src.agentic_orchestration.mission_store.sqlite3.connect", side_effect=OSError("PRIVATE_SENTINEL")):
            with self.assertRaises(CheckpointError) as raised:
                MissionStore.initialize(failed_path)
        self.assertFalse(failed_path.exists())
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
