"""Tests for clawmes.policy.usage_counter."""

from __future__ import annotations

import time

import pytest

from clawmes.policy import usage_counter as uc_module
from clawmes.policy.usage_counter import UsageCounter, get_usage_counter


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(uc_module, "_instance", None)


class TestRecord:
    def test_increments_count(self):
        c = UsageCounter()
        assert c.count("u", "transfer") == 0
        c.record("u", "transfer")
        assert c.count("u", "transfer") == 1
        c.record("u", "transfer")
        assert c.count("u", "transfer") == 2

    def test_separate_keys_independent(self):
        c = UsageCounter()
        c.record("alice", "transfer")
        c.record("alice", "transfer")
        c.record("bob", "transfer")
        c.record("alice", "defi_swap")
        assert c.count("alice", "transfer") == 2
        assert c.count("bob", "transfer") == 1
        assert c.count("alice", "defi_swap") == 1
        assert c.count("bob", "defi_swap") == 0


class TestEviction:
    def test_old_entries_evicted(self, monkeypatch):
        c = UsageCounter(window_seconds=10)
        # Manually populate with old timestamps
        c._buckets[("u", "t")].extend([0.0, 1.0, 2.0])
        # Now monotonic returns 100 — all entries past their TTL
        monkeypatch.setattr(time, "monotonic", lambda: 100.0)
        assert c.count("u", "t") == 0

    def test_partial_eviction(self, monkeypatch):
        c = UsageCounter(window_seconds=10)
        # Stash entries at varying ages
        c._buckets[("u", "t")].extend([0.0, 50.0, 95.0])
        monkeypatch.setattr(time, "monotonic", lambda: 100.0)
        # Threshold = 100 - 10 = 90; only 95.0 survives
        assert c.count("u", "t") == 1

    def test_record_evicts_before_appending(self, monkeypatch):
        c = UsageCounter(window_seconds=10)
        c._buckets[("u", "t")].extend([0.0, 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: 100.0)
        c.record("u", "t")
        # The two old entries are evicted; only the new one remains
        assert c.count("u", "t") == 1


class TestReset:
    def test_clears_all(self):
        c = UsageCounter()
        c.record("u", "t")
        c.record("v", "x")
        c.reset()
        assert c.count("u", "t") == 0
        assert c.count("v", "x") == 0


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_usage_counter()
        b = get_usage_counter()
        assert a is b


class TestThreadSafety:
    def test_concurrent_records(self):
        import threading

        c = UsageCounter()

        def worker():
            for _ in range(50):
                c.record("u", "t")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert c.count("u", "t") == 500
