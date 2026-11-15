"""Storage tests are independent of model/service imports and external Redis."""
import json
import threading
import unittest
from unittest.mock import patch

import session_store as storage

PAIR = [("human", "question"), ("ai", "answer")]


class FakeRedis:
    """Small command model; actual Lua execution is verified against Redis separately."""

    def __init__(self):
        self.data = {}
        self.deadlines = {}
        self.now = 0
        self.mutex = threading.RLock()
        self.renewed = threading.Event()
        self.fail = False
        self.writes = 0

    def _check(self):
        if self.fail:
            raise OSError("redis://secret:password@example.invalid")
        for key in list(self.deadlines):
            if self.deadlines[key] <= self.now:
                self.data.pop(key, None)
                self.deadlines.pop(key, None)

    def ping(self):
        self._check()
        return True

    def get(self, key):
        with self.mutex:
            self._check()
            return self.data.get(key)

    def set(self, key, value, nx=False, ex=None):
        with self.mutex:
            self._check()
            if nx and key in self.data:
                return False
            self.data[key] = value
            if ex:
                self.deadlines[key] = self.now + ex
            return True

    def eval(self, script, nkeys, *args):
        with self.mutex:
            self._check()
            keys, argv = args[:nkeys], args[nkeys:]
            if script.startswith("-- session_renew"):
                self.renewed.set()
                if self.data.get(keys[0]) != argv[0]:
                    return 0
                self.deadlines[keys[0]] = self.now + int(argv[1])
                return 1
            if script.startswith("-- session_release"):
                if self.data.get(keys[0]) != argv[0]:
                    return 0
                self.data.pop(keys[0], None)
                self.deadlines.pop(keys[0], None)
                return 1
            if len(keys) == 3 and argv[0] and self.data.get(keys[2]) != argv[0]:
                return -1
            if script.startswith("-- session_delete"):
                for key in keys[:2]:
                    self.data.pop(key, None)
                    self.deadlines.pop(key, None)
                return 1
            raw = self.data.get(keys[0])
            data = storage._decode(raw) if raw is not None else []
            count = int(self.data.get(keys[1], len(data) // 2)) if raw is not None else 0
            if script.startswith("-- session_count"):
                if raw is None:
                    self.data.pop(keys[1], None)
                else:
                    self.data[keys[1]] = str(count)
                    if keys[0] in self.deadlines:
                        self.deadlines[keys[1]] = self.deadlines[keys[0]]
                return count
            if script.startswith("-- session_write"):
                count = count + 1 if argv[3] == "commit" else max(count, int(argv[4]))
                self.data[keys[0]] = argv[1]
                self.data[keys[1]] = str(count)
                for key in keys[:2]:
                    self.deadlines[key] = self.now + int(argv[2])
                self.writes += 1
                return count
            raise AssertionError("Unexpected script")


def redis_store(fake=None, **kwargs):
    fake = fake or FakeRedis()
    with patch("redis.Redis.from_url", return_value=fake):
        store = storage.RedisSessionStore("redis://secret:password@localhost", **kwargs)
    return store, fake


class CommonContract:
    def test_complete_windows_and_monotonic_counts(self):
        with self.store.session_guard("s"):
            self.assertEqual(self.store.commit_turn("s", PAIR), 1)
            for count in range(2, 8):
                self.assertEqual(self.store.commit_turn("s", PAIR), count)
            self.store.save("s", [])
            self.assertEqual(self.store.count_turns("s"), 7)
            self.assertEqual(self.store.load("s"), [])
            self.assertEqual(self.store.commit_turn("s", PAIR), 8)
        self.assertEqual(self.store.load("s"), PAIR)
        self.assertEqual(self.store.count_turns("s"), 8)

    def test_commit_requires_guard_on_current_thread(self):
        with self.assertRaises(storage.SessionLeaseLostError):
            self.store.commit_turn("s", PAIR)
        errors = []
        done = threading.Event()
        def other():
            try:
                self.store.commit_turn("s", PAIR)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        with self.store.session_guard("s"):
            worker = threading.Thread(target=other)
            worker.start()
            self.assertTrue(done.wait(2))
            worker.join()
        self.assertIsInstance(errors[0], storage.SessionLeaseLostError)

    def test_guard_excludes_concurrent_thread_and_releases_after_exception(self):
        results = []
        done = threading.Event()
        def competing():
            try:
                with self.store.session_guard("s"):
                    results.append("claimed")
            except storage.SessionBusyError:
                results.append("busy")
            finally:
                done.set()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            with self.store.session_guard("s"):
                worker = threading.Thread(target=competing)
                worker.start()
                self.assertTrue(done.wait(2))
                worker.join()
                with self.store.session_guard("independent"):
                    self.store.commit_turn("independent", PAIR)
                raise ValueError("cancelled")
        self.assertEqual(results, ["busy"])
        with self.store.session_guard("s"):
            self.store.commit_turn("s", PAIR)

    def test_delete_preserves_active_guard(self):
        with self.store.session_guard("s"):
            self.store.commit_turn("s", PAIR)
            self.store.delete("s")
            with self.assertRaises(storage.SessionBusyError):
                with self.store.session_guard("s"):
                    pass
            self.assertEqual(self.store.count_turns("s"), 0)
            self.assertEqual(self.store.commit_turn("s", PAIR), 1)

    def test_schema_validation(self):
        for invalid in ({}, "history", [["human"]], [[1, "text"]], [["a", "b", "c"]]):
            with self.assertRaises(storage.SessionStoreUnavailable):
                self.store.save("s", invalid)
        self.assertEqual(self.store.load("s"), [])


class MemoryTests(CommonContract, unittest.TestCase):
    def setUp(self):
        self.store = storage.MemorySessionStore(ttl=10)

    def test_lazy_expiry_and_legacy_migration(self):
        with patch.object(storage.time, "monotonic", return_value=100):
            self.store.save("s", PAIR * 3)
            self.store._counts.clear()
            self.assertEqual(self.store.count_turns("s"), 3)
        with patch.object(storage.time, "monotonic", return_value=111):
            self.assertEqual(self.store.count_turns("s"), 0)
            self.assertEqual(self.store.load("s"), [])
            self.assertEqual(self.store.stats()["sessions"], 0)
            with self.store.session_guard("s"):
                self.assertEqual(self.store.commit_turn("s", PAIR), 1)


class RedisTests(CommonContract, unittest.TestCase):
    def setUp(self):
        self.store, self.fake = redis_store(ttl=10, lock_ttl=1)

    def test_heartbeat_renews_with_token(self):
        with self.store.session_guard("s"):
            self.assertTrue(self.fake.renewed.wait(2))
            self.assertIn("slope:sess:s:lock", self.fake.data)
        self.assertNotIn("slope:sess:s:lock", self.fake.data)

    def test_expired_writer_cannot_write_or_delete_successor_lock(self):
        with self.assertRaises(storage.SessionLeaseLostError):
            with self.store.session_guard("s"):
                old = self.fake.get("slope:sess:s:lock")
                self.fake.now += 2
                self.fake.set("slope:sess:s:lock", "successor", nx=True, ex=10)
                for action in (lambda: self.store.commit_turn("s", PAIR),
                               lambda: self.store.save("s", PAIR),
                               lambda: self.store.delete("s")):
                    with self.assertRaises(storage.SessionLeaseLostError):
                        action()
                # Even restoration of the old token must not clear the loss flag.
                self.fake.set("slope:sess:s:lock", old, ex=10)
                with self.assertRaises(storage.SessionLeaseLostError):
                    self.store.commit_turn("s", PAIR)
                self.fake.set("slope:sess:s:lock", "successor", ex=10)
        self.assertEqual(self.fake.get("slope:sess:s:lock"), "successor")
        self.assertEqual(self.fake.writes, 0)

    def test_successful_body_rejects_expired_lease_on_exit(self):
        with self.assertRaises(storage.SessionLeaseLostError):
            with self.store.session_guard("s"):
                self.fake.now += 2

    def test_legacy_count_migration_and_aligned_expiry(self):
        key = "slope:sess:s"
        self.fake.set(key, json.dumps(PAIR * 3), ex=10)
        self.assertEqual(self.store.count_turns("s"), 3)
        self.assertEqual(self.fake.deadlines[key], self.fake.deadlines[key + ":meta"])
        self.fake.now = 9.8
        self.assertEqual(self.store.count_turns("s"), 3)
        self.fake.now = 11
        self.assertEqual(self.store.count_turns("s"), 0)
        self.assertEqual(self.store.load("s"), [])
        with self.store.session_guard("s"):
            self.assertEqual(self.store.commit_turn("s", PAIR), 1)

    def test_corrupt_history_fails_closed(self):
        for raw in ('{', '{}', '[1]', '[["a", 2]]'):
            self.fake.data["slope:sess:s"] = raw
            with self.assertRaises(storage.SessionStoreUnavailable):
                self.store.load("s")
            with self.assertRaises(storage.SessionStoreUnavailable):
                self.store.count_turns("s")
            with self.assertRaises(storage.SessionStoreUnavailable):
                self.store.save("s", PAIR)

    def test_redis_failures_are_not_empty_success(self):
        self.fake.fail = True
        for operation in (lambda: self.store.load("s"), lambda: self.store.save("s", PAIR),
                          lambda: self.store.count_turns("s"), lambda: self.store.delete("s"),
                          self.store.healthy, self.store.stats):
            with self.assertRaises(storage.SessionStoreUnavailable) as caught:
                operation()
            self.assertNotIn("password", str(caught.exception))

    def test_renewal_failure_rejects_commit_and_exit(self):
        with self.assertRaises(storage.SessionStoreUnavailable):
            with self.store.session_guard("s"):
                lease = self.store._owners()["s"]
                self.fake.fail = True
                self.store._renew("slope:sess:s:lock", lease)
                self.fake.fail = False
                with self.assertRaises(storage.SessionStoreUnavailable):
                    self.store.commit_turn("s", PAIR)


class BuilderTests(unittest.TestCase):
    def test_explicit_redis_failure_never_falls_back_or_logs_credentials(self):
        secret = "redis://user:private-password@example.invalid"
        with patch("redis.Redis.from_url", side_effect=OSError(secret)):
            with self.assertLogs("slope.session", level="WARNING") as logs:
                with self.assertRaises(storage.SessionStoreUnavailable) as caught:
                    storage.build_session_store(prefer="redis", url=secret)
        self.assertNotIn("private-password", str(caught.exception) + str(logs.output))

    def test_explicit_memory_uses_configured_ttl(self):
        store, note = storage.build_session_store(prefer="memory", ttl=42)
        self.assertIsInstance(store, storage.MemorySessionStore)
        self.assertEqual(store._ttl, 42)
        self.assertNotIn("redis://", note)

    def test_success_note_does_not_disclose_url(self):
        with patch("redis.Redis.from_url", return_value=FakeRedis()):
            _, note = storage.build_session_store(url="redis://user:secret@localhost")
        self.assertNotIn("secret", note)
        self.assertNotIn("redis://", note)


if __name__ == "__main__":
    unittest.main()
