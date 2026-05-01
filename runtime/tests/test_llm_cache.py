from __future__ import annotations

from pathlib import Path

from local_agent_runtime.provider.cache import LLMCache
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _make_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "runtime.sqlite3"))


class TestHashPrompt:
    def test_same_inputs_same_hash(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        tools = [{"type": "function", "name": "read_file"}]
        h1 = LLMCache.hash_prompt(messages, tools, model="gpt-4")
        h2 = LLMCache.hash_prompt(messages, tools, model="gpt-4")
        assert h1 == h2

    def test_different_inputs_different_hash(self) -> None:
        h1 = LLMCache.hash_prompt([{"role": "user", "content": "hello"}])
        h2 = LLMCache.hash_prompt([{"role": "user", "content": "world"}])
        assert h1 != h2

    def test_model_affects_hash(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        h1 = LLMCache.hash_prompt(msgs, model="gpt-4")
        h2 = LLMCache.hash_prompt(msgs, model="claude")
        assert h1 != h2

    def test_none_defaults(self) -> None:
        h = LLMCache.hash_prompt([{"role": "user", "content": "test"}])
        assert isinstance(h, str) and len(h) == 64


class TestCacheHitMiss:
    def test_put_then_get(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            key = LLMCache.hash_prompt([{"role": "user", "content": "hello"}])
            cache.put(key, '{"message": "hi"}')
            assert cache.get(key) == '{"message": "hi"}'
        finally:
            store.close()

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            assert cache.get("nonexistent_hash") is None
        finally:
            store.close()

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            key = "testkey"
            cache.put(key, "v1")
            cache.put(key, "v2")
            assert cache.get(key) == "v2"
        finally:
            store.close()


class TestExpiry:
    def test_expired_entry_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            key = "expiring_key"
            cache.put(key, "data", ttl=0)

            # TTL=0 means expires_at = now, so it's immediately expired
            assert cache.get(key) is None
        finally:
            store.close()


class TestInvalidate:
    def test_invalidate_removes_entry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            key = "to_delete"
            cache.put(key, "data")
            cache.invalidate(key)
            assert cache.get(key) is None
        finally:
            store.close()


class TestCleanupExpired:
    def test_cleanup_removes_old_entries(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            cache = LLMCache(store)
            cache.put("old1", "data1", ttl=0)
            cache.put("old2", "data2", ttl=0)
            cache.put("fresh", "data3", ttl=3600)

            removed = cache.cleanup_expired()
            assert removed >= 2
            assert cache.get("old1") is None
            assert cache.get("fresh") == "data3"
        finally:
            store.close()
