"""Проверки эксплуатационной команды без iTerm2, сети и macOS API."""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


COMMAND = Path(__file__).resolve().parents[1] / "ops/iterm2/repair_shift_arrows.py"
SPEC = importlib.util.spec_from_file_location("repair_shift_arrows", COMMAND)
operation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operation)


def keymap():
    return {
        "0xf702-0x220000": {"Action": 10, "Text": "[D"},
        "0xf703-0x220000": {"Action": 10, "Text": "[C"},
        "other": {"Action": 42, "Text": "private-test-text"},
    }


class FakeProfile:
    def __init__(self, owner, guid, parent=None):
        self.owner = owner
        self.guid = guid
        self.original_guid = parent
        self.mapping = keymap()

    @property
    def key_mappings(self):
        return deepcopy(self.mapping)

    async def async_set_key_mappings(self, value):
        self.owner.writes.append(self.guid)
        old = self.mapping
        self.mapping = deepcopy(value)
        if not self.original_guid and self.owner.propagate:
            for session in self.owner.sessions.values():
                if session.profile.original_guid == self.guid and session.profile.mapping == old:
                    session.profile.mapping = deepcopy(value)
        if self.owner.on_write:
            await self.owner.on_write(self, value)


class FakeAPI:
    def __init__(self):
        self.writes = []
        self.on_write = None
        self.on_profiles = None
        self.propagate = False
        self.profiles = {f"profile-{number}": FakeProfile(self, f"profile-{number}")
                         for number in range(5)}
        self.sessions = {}
        self.add_session("session-current", "profile-0")
        owner = self

        class Profile:
            @staticmethod
            async def async_get(connection, guids=None):
                if owner.on_profiles:
                    await owner.on_profiles(guids)
                return [profile for guid, profile in owner.profiles.items()
                        if guids is None or guid in guids]

        self.Profile = Profile

    def add_session(self, name, parent):
        profile = FakeProfile(self, name, parent)

        async def get_profile():
            return profile

        self.sessions[name] = SimpleNamespace(
            session_id=name, profile=profile, async_get_profile=get_profile)

    async def async_get_app(self, connection):
        return SimpleNamespace(buried_sessions=list(self.sessions.values()),
                               terminal_windows=[],
                               get_session_by_id=self.sessions.get)


class OperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.api = FakeAPI()
        self.output = io.StringIO()
        self.env = patch.dict(os.environ, {"ITERM_SESSION_ID": "session-current"})
        self.env.start()
        self.addCleanup(self.env.stop)
        await self.run_action("snapshot")
        self.snapshot = operation.load_snapshot(self.directory / "snapshot.json")

    async def run_action(self, action):
        with redirect_stdout(self.output):
            await operation.run(self.api, None, SimpleNamespace(action=action), self.directory)

    def journal(self):
        return json.loads((self.directory / "journal.json").read_text())

    def assert_original(self, name):
        self.assertEqual(self.api.profiles[name].mapping, keymap())

    async def test_second_profile_error_preserves_unstarted_external_fix(self):
        async def fail(profile, value):
            if profile.guid == "profile-1":
                untouched = self.api.profiles["profile-4"]
                untouched.mapping = operation.plan_keymap(untouched.mapping)
                self.api.on_write = None
                raise RuntimeError("private exception text")

        self.api.on_write = fail
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assert_original("profile-0")
        self.assert_original("profile-1")
        self.assertEqual(self.api.profiles["profile-4"].mapping,
                         operation.plan_keymap(keymap()))
        self.assertNotIn("profile-4", self.api.writes)
        self.assertNotIn({"kind": "profiles", "index": 5}, self.journal()["started"])
        self.assertNotIn("private", self.output.getvalue())
        self.assertEqual(self.journal()["rollback_conflicts"], [])

    async def test_lost_response_uses_durable_started_marker(self):
        async def fail_once(profile, value):
            if profile.guid == "profile-0":
                self.assertIn({"kind": "profiles", "index": 1}, self.journal()["started"])
                self.api.on_write = None
                raise ConnectionError("response lost")

        self.api.on_write = fail_once
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assert_original("profile-0")
        self.assertEqual(self.journal()["rollback_conflicts"], [])

    async def test_journal_failure_after_first_write_still_attempts_rollback(self):
        save_private = operation.save_private
        attempts = []
        stderr = io.StringIO()

        def unavailable_after_write(path, value, **kwargs):
            attempts.append(value.get("status"))
            if self.api.writes:
                raise OSError("private-test-storage-error")
            save_private(path, value, **kwargs)

        with patch.object(operation, "save_private", unavailable_after_write), redirect_stderr(stderr):
            with self.assertRaises(operation.RepairError) as caught:
                await self.run_action("apply")
        self.assert_original("profile-0")
        self.assertEqual(self.api.writes, ["profile-0", "profile-0"])
        self.assertIn("rolling_back", attempts)
        self.assertEqual(attempts[-1], "failed_and_rolled_back")
        self.assertIn("не удалось сохранить", str(caught.exception))
        self.assertIn("отката", str(caught.exception))
        self.assertNotIn("private-test-storage-error", str(caught.exception) + stderr.getvalue())
        self.assertEqual(self.journal()["status"], "writing")
        self.assertNotIn("отката в локальном журнале", str(caught.exception))

    async def test_intermediate_journal_failure_allows_saving_rollback_result(self):
        self.api.profiles["profile-0"].mapping = operation.plan_keymap(keymap())
        journal = {"schema": 1, "snapshot": operation.snapshot_digest(self.snapshot),
                   "status": "writing", "started": [{"kind": "profiles", "index": 1}]}
        save_private = operation.save_private
        attempts = 0
        stderr = io.StringIO()

        def fail_first(path, value, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("private-test-storage-error")
            save_private(path, value, **kwargs)

        with patch.object(operation, "save_private", fail_first), redirect_stderr(stderr):
            failures = await operation.finish_rollback(
                self.api, None, self.snapshot, journal, self.directory / "journal.json", "rolled_back")
        self.assert_original("profile-0")
        self.assertEqual(failures, [])
        self.assertEqual(attempts, 2)
        self.assertEqual(self.journal()["status"], "rolled_back")
        self.assertIn("был недоступен", stderr.getvalue())
        self.assertIn("сохранён", stderr.getvalue())
        self.assertNotIn("private-test-storage-error", stderr.getvalue())

    async def test_saved_profile_propagation_is_in_started_scope(self):
        self.api.propagate = True

        async def fail_once(profile, value):
            if profile.guid == "profile-0":
                self.assertIn({"kind": "sessions", "index": 1}, self.journal()["started"])
                self.assertEqual(self.api.sessions["session-current"].profile.mapping,
                                 operation.plan_keymap(keymap()))
                self.api.on_write = None
                raise ConnectionError("response lost")

        self.api.on_write = fail_once
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assert_original("profile-0")
        self.assertEqual(self.api.sessions["session-current"].profile.mapping, keymap())
        self.assertNotIn("profile-1", self.api.writes)

    async def test_cancellation_and_repeated_cancellation_finish_limited_rollback(self):
        wrote = asyncio.Event()
        rollback_started = asyncio.Event()
        allow_rollback = asyncio.Event()

        async def block(profile, value):
            if profile.guid != "profile-0":
                return
            if value == operation.plan_keymap(keymap()):
                wrote.set()
                await asyncio.Event().wait()
            else:
                rollback_started.set()
                await allow_rollback.wait()

        self.api.on_write = block
        task = asyncio.create_task(self.run_action("apply"))
        await wrote.wait()
        task.cancel()
        await rollback_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        allow_rollback.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_original("profile-0")
        self.assertEqual(self.api.writes, ["profile-0", "profile-0"])
        self.assertEqual(self.journal()["status"], "failed_and_rolled_back")
        self.assertEqual(self.journal()["rollback_conflicts"], [])

    async def test_keyboard_interrupt_after_write_finishes_rollback(self):
        async def interrupt_once(profile, value):
            self.api.on_write = None
            raise KeyboardInterrupt()

        self.api.on_write = interrupt_once
        with self.assertRaises(KeyboardInterrupt):
            await self.run_action("apply")
        self.assert_original("profile-0")
        self.assertEqual(self.journal()["status"], "failed_and_rolled_back")

    async def test_manual_rollback_without_known_journal_refuses(self):
        for state in (None, {"status": "applied"}):
            if state is not None:
                operation.save_private(self.directory / "journal.json", state)
            with self.assertRaises(operation.RepairError):
                await self.run_action("rollback")
            self.assertEqual(self.api.writes, [])

    async def test_success_repeat_and_manual_rollback(self):
        await self.run_action("apply")
        self.assertEqual(self.journal()["status"], "applied")
        journal_text = (self.directory / "journal.json").read_text()
        for private_value in ("profile-", "session-current", "private-test-text"):
            self.assertNotIn(private_value, journal_text)
        writes = list(self.api.writes)
        await self.run_action("apply")
        self.assertEqual(self.api.writes, writes)
        self.assertIn('"status": "already_applied"', self.output.getvalue())
        await self.run_action("rollback")
        for name in self.api.profiles:
            self.assert_original(name)
        self.assertEqual(self.api.sessions["session-current"].profile.mapping, keymap())
        self.assertEqual(self.journal()["rollback_conflicts"], [])
        self.assertEqual((self.directory / "journal.json").stat().st_mode & 0o777, 0o600)

    async def test_preflight_rejects_changed_profile_set_and_map(self):
        self.api.profiles["profile-4"].mapping["other"]["Text"] = "changed"
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assertEqual(self.api.writes, [])
        self.api.profiles["profile-4"].mapping = keymap()
        self.api.profiles.pop("profile-4")
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assertEqual(self.api.writes, [])

    async def test_changed_linked_session_is_checked_before_saved_write(self):
        calls = 0

        async def mutate(guids):
            nonlocal calls
            if guids is not None:
                calls += 1
                if calls == 1:
                    self.api.sessions["session-current"].profile.mapping["other"]["Text"] = "external"

        self.api.on_profiles = mutate
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.journal()["started"], [])

    async def test_local_override_is_not_marked_for_profile_propagation(self):
        self.api.sessions["session-current"].profile.mapping["other"]["Text"] = "local override"
        self.snapshot = await operation.inventory(self.api, None)
        operation.save_private(self.directory / "snapshot.json", self.snapshot)
        self.api.propagate = True

        async def fail_once(profile, value):
            self.api.on_write = None
            raise ConnectionError("response lost")

        self.api.on_write = fail_once
        with self.assertRaises(operation.RepairError):
            await self.run_action("apply")
        self.assert_original("profile-0")
        self.assertNotIn({"kind": "sessions", "index": 1}, self.journal()["started"])
        self.assertEqual(self.api.sessions["session-current"].profile.mapping["other"]["Text"],
                         "local override")

    async def test_manual_rollback_rejects_journal_for_another_snapshot(self):
        await self.run_action("apply")
        journal = self.journal()
        journal["snapshot"] = "unrelated digest"
        operation.save_private(self.directory / "journal.json", journal)
        writes = list(self.api.writes)
        with self.assertRaises(operation.RepairError):
            await self.run_action("rollback")
        self.assertEqual(self.api.writes, writes)


class MainTests(unittest.TestCase):
    def test_public_runner_dispatches_callback_and_sanitizes_errors(self):
        for error, expected_code in ((None, 0), (RuntimeError("private-test-text"), 1),
                                     (KeyboardInterrupt("private-test-text"), 130)):
            with self.subTest(error_type=type(error).__name__), TemporaryDirectory() as directory:
                callbacks = []

                def run_until_complete(callback):
                    callbacks.append(callback)
                    # Если callback выпустит исключение, публичный runner
                    # напечатал бы traceback. Тест требует нормального возврата.
                    asyncio.run(callback("fake connection"))

                fake = SimpleNamespace(run_until_complete=run_until_complete)
                output = io.StringIO()
                fake_run = AsyncMock(side_effect=error)
                with patch.dict("sys.modules", {"iterm2": fake}), patch.object(
                        operation, "run", fake_run), patch("sys.argv", [
                            "repair_shift_arrows.py", "snapshot", "--state-dir", directory]), \
                        redirect_stdout(output), redirect_stderr(output):
                    self.assertEqual(operation.main(), expected_code)
                self.assertEqual(len(callbacks), 1)
                fake_run.assert_awaited_once()
                self.assertNotIn("private-test-text", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())

    def test_signal_handlers_cancel_callback_and_restore_previous_handlers(self):
        for number in (operation.signal.SIGINT, operation.signal.SIGTERM):
            with self.subTest(signal=number):
                handlers = {}
                previous = object()

                async def cancelled(*args):
                    handlers[number](number, None)
                    await asyncio.sleep(0)

                def run_until_complete(callback):
                    asyncio.run(callback(None))

                fake = SimpleNamespace(run_until_complete=run_until_complete)
                with TemporaryDirectory() as directory, patch.dict("sys.modules", {"iterm2": fake}), \
                        patch.object(operation, "run", cancelled), \
                        patch.object(operation.signal, "getsignal", return_value=previous), \
                        patch.object(operation.signal, "signal", side_effect=lambda signum, handler: handlers.update({signum: handler})), \
                        patch("sys.argv", ["repair_shift_arrows.py", "apply", "--state-dir", directory]), \
                        redirect_stderr(io.StringIO()):
                    self.assertEqual(operation.main(), 130)
                self.assertEqual(set(handlers), {operation.signal.SIGINT, operation.signal.SIGTERM})
                self.assertTrue(all(handler is previous for handler in handlers.values()))


if __name__ == "__main__":
    unittest.main()
