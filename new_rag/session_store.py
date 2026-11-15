"""Synchronous, fail-closed session histories and fenced turn transactions."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import threading
import time
import uuid

log = logging.getLogger("slope.session")
DEFAULT_TTL = 7 * 24 * 3600
DEFAULT_PREFIX = "slope:sess:"


class SessionBusyError(RuntimeError):
    """Another request owns this session."""


class SessionLeaseLostError(RuntimeError):
    """The current request can no longer write under its lease."""


class SessionStoreUnavailable(RuntimeError):
    """Storage failed or stored session data is invalid."""


def _items(items):
    if not isinstance(items, (list, tuple)) or any(
        not isinstance(item, (list, tuple)) or len(item) != 2
        or not all(isinstance(value, str) for value in item) for item in items
    ):
        raise SessionStoreUnavailable("Invalid session history schema")
    return [tuple(item) for item in items]


def _decode(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError()
        return _items(data)
    except (ValueError, TypeError, UnicodeError):
        raise SessionStoreUnavailable("Invalid stored session history") from None


class MemorySessionStore:
    """Process-local backend with lazy expiry and per-session exclusion."""

    name = "memory"

    def __init__(self, ttl: int = DEFAULT_TTL):
        self._ttl = int(ttl)
        if self._ttl <= 0:
            raise ValueError("ttl must be positive")
        self._data = {}
        self._counts = {}
        self._expires = {}
        self._locks = {}
        self._mutex = threading.RLock()
        self._local = threading.local()
        self.hits = self.misses = 0

    def _owners(self):
        if not hasattr(self._local, "owners"):
            self._local.owners = {}
        return self._local.owners

    def _expire(self, sid):
        if self._expires.get(sid, float("inf")) <= time.monotonic():
            self._data.pop(sid, None)
            self._counts.pop(sid, None)
            self._expires.pop(sid, None)

    @contextmanager
    def session_guard(self, sid):
        with self._mutex:
            lock = self._locks.setdefault(sid, threading.Lock())
        if not lock.acquire(blocking=False):
            raise SessionBusyError("Session is busy")
        self._owners()[sid] = lock
        try:
            yield
        finally:
            self._owners().pop(sid, None)
            lock.release()

    def load(self, sid):
        with self._mutex:
            self._expire(sid)
            if sid not in self._data:
                self.misses += 1
                return []
            self.hits += 1
            return _items(self._data[sid])

    def count_turns(self, sid):
        with self._mutex:
            self._expire(sid)
            if sid not in self._data:
                self._counts.pop(sid, None)
                return 0
            return self._counts.setdefault(sid, len(_items(self._data[sid])) // 2)

    def save(self, sid, items):
        data = _items(items)
        with self._mutex:
            count = max(self.count_turns(sid), len(data) // 2)
            self._data[sid] = data
            self._counts[sid] = count
            self._expires[sid] = time.monotonic() + self._ttl

    def commit_turn(self, sid, items) -> int:
        if sid not in self._owners():
            raise SessionLeaseLostError("An active session guard is required")
        data = _items(items)
        with self._mutex:
            count = self.count_turns(sid) + 1
            self._data[sid] = data
            self._counts[sid] = count
            self._expires[sid] = time.monotonic() + self._ttl
            return count

    def delete(self, sid):
        with self._mutex:
            self._data.pop(sid, None)
            self._counts.pop(sid, None)
            self._expires.pop(sid, None)

    def healthy(self):
        return True

    def stats(self):
        with self._mutex:
            for sid in list(self._data):
                self._expire(sid)
            return {"backend": self.name, "sessions": len(self._data),
                    "hits": self.hits, "misses": self.misses, "ttl": self._ttl}


# These scripts use Redis 3.2 commands only. All writes fence ownership and
# update the complete caller-supplied history and metadata atomically.
_RENEW = """-- session_renew
if redis.call('GET', KEYS[1]) == ARGV[1] then
 return redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return 0
"""
_RELEASE = """-- session_release
if redis.call('GET', KEYS[1]) == ARGV[1] then
 return redis.call('DEL', KEYS[1]) end
return 0
"""
_HISTORY_COUNT = """
local raw = redis.call('GET', KEYS[1])
local count = 0
if raw then
 local ok, data = pcall(cjson.decode, raw)
 if not ok or type(data) ~= 'table' or not string.match(raw, '^%s*%[') then
  return redis.error_reply('invalid session history') end
 for _, item in ipairs(data) do
  if type(item) ~= 'table' or #item ~= 2 or type(item[1]) ~= 'string'
    or type(item[2]) ~= 'string' then
   return redis.error_reply('invalid session history') end
 end
 local meta = redis.call('GET', KEYS[2])
 if meta then
  count = tonumber(meta)
  if not count or count < 0 or count ~= math.floor(count) then
   return redis.error_reply('invalid session metadata') end
 else count = math.floor(#data / 2) end
end
"""
_WRITE = """-- session_write
if ARGV[1] ~= '' and redis.call('GET', KEYS[3]) ~= ARGV[1] then return -1 end
""" + _HISTORY_COUNT + """
if ARGV[4] == 'commit' then count = count + 1
else count = math.max(count, tonumber(ARGV[5])) end
redis.call('SETEX', KEYS[1], ARGV[3], ARGV[2])
redis.call('SETEX', KEYS[2], ARGV[3], tostring(count))
return count
"""
_COUNT = """-- session_count
""" + _HISTORY_COUNT + """
if not raw then redis.call('DEL', KEYS[2]); return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl >= 0 then redis.call('PSETEX', KEYS[2], math.max(1, ttl), tostring(count))
elseif ttl == -1 then redis.call('SET', KEYS[2], tostring(count))
else redis.call('DEL', KEYS[2]) end
return count
"""
_DELETE = """-- session_delete
if ARGV[1] ~= '' and redis.call('GET', KEYS[3]) ~= ARGV[1] then return -1 end
return redis.call('DEL', KEYS[1], KEYS[2])
"""


class _Lease:
    def __init__(self):
        self.token = uuid.uuid4().hex
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.unavailable = False

    def check(self):
        if self.lost.is_set():
            if self.unavailable:
                raise SessionStoreUnavailable("Session lease renewal failed")
            raise SessionLeaseLostError("Session lease was lost")


class RedisSessionStore:
    name = "redis"

    def __init__(self, url: str, ttl: int = DEFAULT_TTL,
                 prefix: str = DEFAULT_PREFIX, lock_ttl: int = 30):
        self._ttl, self._lock_ttl = int(ttl), int(lock_ttl)
        if self._ttl <= 0 or self._lock_ttl <= 0:
            raise ValueError("ttl and lock_ttl must be positive")
        self._prefix = prefix
        self._local = threading.local()
        self.hits = self.misses = 0
        try:
            import redis
            self._r = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=3,
                socket_timeout=3, protocol=2)
            if not self._r.ping():
                raise RuntimeError()
        except Exception:
            raise SessionStoreUnavailable("Redis session store is unavailable") from None

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self._r, method)(*args, **kwargs)
        except Exception:
            raise SessionStoreUnavailable("Redis session operation failed") from None

    def _key(self, sid):
        return f"{self._prefix}{sid}"

    def _owners(self):
        if not hasattr(self._local, "owners"):
            self._local.owners = {}
        return self._local.owners

    def _lease(self, sid, required=False):
        lease = self._owners().get(sid)
        if lease is not None:
            lease.check()
        elif required:
            raise SessionLeaseLostError("An active session guard is required")
        return lease

    def _renew(self, key, lease):
        try:
            if not self._call("eval", _RENEW, 1, key, lease.token, self._lock_ttl):
                lease.lost.set()
        except SessionStoreUnavailable:
            lease.unavailable = True
            lease.lost.set()

    @contextmanager
    def session_guard(self, sid):
        if sid in self._owners():
            raise SessionBusyError("Session is busy")
        key = self._key(sid) + ":lock"
        lease = _Lease()
        if not self._call("set", key, lease.token, nx=True, ex=self._lock_ttl):
            raise SessionBusyError("Session is busy")
        self._owners()[sid] = lease

        def heartbeat():
            while not lease.stop.wait(self._lock_ttl / 3):
                self._renew(key, lease)
                if lease.lost.is_set():
                    return

        worker = threading.Thread(target=heartbeat, name="session-lease", daemon=True)
        succeeded = False
        try:
            worker.start()
            yield
            self._renew(key, lease)
            lease.check()
            succeeded = True
        finally:
            lease.stop.set()
            if worker.ident is not None:
                worker.join(timeout=4)
            self._owners().pop(sid, None)
            try:
                released = self._call("eval", _RELEASE, 1, key, lease.token)
                if succeeded and not released:
                    lease.lost.set()
                    lease.check()
            except SessionStoreUnavailable:
                if succeeded:
                    raise

    def load(self, sid):
        raw = self._call("get", self._key(sid))
        if raw is None:
            self.misses += 1
            return []
        data = _decode(raw)
        self.hits += 1
        return data

    def _write(self, sid, items, commit):
        lease = self._lease(sid, required=commit)
        data = _items(items)
        key = self._key(sid)
        result = self._call("eval", _WRITE, 3, key, key + ":meta", key + ":lock",
                            lease.token if lease else "",
                            json.dumps(data, ensure_ascii=False), self._ttl,
                            "commit" if commit else "save", len(data) // 2)
        if result == -1:
            if lease:
                lease.lost.set()
            raise SessionLeaseLostError("Session lease was lost")
        return int(result)

    def save(self, sid, items):
        self._write(sid, items, False)

    def commit_turn(self, sid, items) -> int:
        return self._write(sid, items, True)

    def count_turns(self, sid) -> int:
        key = self._key(sid)
        return int(self._call("eval", _COUNT, 2, key, key + ":meta"))

    def delete(self, sid):
        lease = self._lease(sid)
        key = self._key(sid)
        result = self._call("eval", _DELETE, 3, key, key + ":meta", key + ":lock",
                            lease.token if lease else "")
        if result == -1:
            lease.lost.set()
            raise SessionLeaseLostError("Session lease was lost")

    def healthy(self):
        if not self._call("ping"):
            raise SessionStoreUnavailable("Redis session store is unavailable")
        return True

    def stats(self):
        return {"backend": self.name, "hits": self.hits, "misses": self.misses,
                "ttl": self._ttl, "healthy": self.healthy()}


def build_session_store(prefer: str = "redis", url: str = "redis://127.0.0.1:6379/0",
                        ttl: int = DEFAULT_TTL, prefix: str = DEFAULT_PREFIX,
                        *, lock_ttl: int = 30):
    """Build only the requested backend; never expose connection details."""
    prefer = (prefer or "memory").strip().lower()
    if prefer == "memory":
        return MemorySessionStore(ttl=ttl), "Using process-local memory session storage"
    if prefer != "redis":
        raise ValueError("Unknown session backend")
    try:
        store = RedisSessionStore(url, ttl=ttl, prefix=prefix, lock_ttl=lock_ttl)
    except Exception:
        log.warning("Redis session store initialization failed")
        raise SessionStoreUnavailable("Redis session store is unavailable") from None
    return store, f"Connected to Redis session storage (TTL {ttl}s)"
