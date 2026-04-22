"""Tests for clawmes.policy.confirm_store."""

from __future__ import annotations

import time as _time
from dataclasses import dataclass

from clawmes.policy.confirm_store import ConfirmStore


@dataclass
class FakeAction:
    tool_name: str
    args: dict


class TestIssueAndConsume:
    def test_round_trip(self):
        store = ConfirmStore()
        ctx = FakeAction(tool_name="transfer", args={"to": "alice.eth", "amount": "1"})
        nonce = store.issue(ctx)
        assert isinstance(nonce, str)
        assert len(nonce) >= 16  # token_urlsafe(16) is at least 22 chars
        assert store.consume(ctx, nonce) is True

    def test_consume_only_once(self):
        store = ConfirmStore()
        ctx = FakeAction(tool_name="transfer", args={"to": "alice.eth"})
        nonce = store.issue(ctx)
        assert store.consume(ctx, nonce) is True
        assert store.consume(ctx, nonce) is False  # already consumed

    def test_wrong_nonce_rejected(self):
        store = ConfirmStore()
        ctx = FakeAction(tool_name="transfer", args={})
        store.issue(ctx)
        assert store.consume(ctx, "wrong-nonce") is False

    def test_nonce_for_different_action_rejected(self):
        """Issuing a nonce for one action and trying to consume for another fails."""
        store = ConfirmStore()
        ctx_a = FakeAction(tool_name="transfer", args={"to": "alice.eth"})
        ctx_b = FakeAction(tool_name="transfer", args={"to": "bob.eth"})  # different recipient
        nonce = store.issue(ctx_a)
        assert store.consume(ctx_b, nonce) is False

    def test_same_action_different_nonce_field(self):
        """The policyConfirmationNonce field itself is excluded from fingerprint."""
        store = ConfirmStore()
        ctx_with_old = FakeAction(
            tool_name="transfer",
            args={"to": "alice.eth", "policyConfirmationNonce": "stale"},
        )
        ctx_no_nonce = FakeAction(
            tool_name="transfer",
            args={"to": "alice.eth"},
        )
        nonce = store.issue(ctx_with_old)
        # The retry comes back without the policyConfirmationNonce in args (it's
        # passed as the second arg). Fingerprint should still match.
        assert store.consume(ctx_no_nonce, nonce) is True


class TestExpiry:
    def test_expired_nonce_not_consumable(self, monkeypatch):
        store = ConfirmStore(ttl_seconds=1)
        ctx = FakeAction(tool_name="transfer", args={})
        nonce = store.issue(ctx)

        # Fast-forward monotonic
        real_monotonic = _time.monotonic
        offset = [0.0]
        monkeypatch.setattr(
            "clawmes.policy.confirm_store.time.monotonic",
            lambda: real_monotonic() + offset[0],
        )

        offset[0] = 5.0  # well past TTL
        assert store.consume(ctx, nonce) is False

    def test_expired_nonces_gced_on_issue(self, monkeypatch):
        store = ConfirmStore(ttl_seconds=1)
        ctx = FakeAction(tool_name="transfer", args={})
        store.issue(ctx)
        store.issue(ctx)

        real_monotonic = _time.monotonic
        offset = [0.0]
        monkeypatch.setattr(
            "clawmes.policy.confirm_store.time.monotonic",
            lambda: real_monotonic() + offset[0],
        )

        offset[0] = 5.0
        # Issuing a new one triggers GC of the two expired entries
        store.issue(ctx)
        # internal pending list should now have just the one new entry
        assert len(store._pending) == 1


class TestThreadSafety:
    def test_concurrent_issue_and_consume(self):
        """Smoke test — many threads issue and consume without explosions."""
        import threading

        store = ConfirmStore()
        ctx = FakeAction(tool_name="t", args={})
        results = []

        def worker():
            n = store.issue(ctx)
            results.append(store.consume(ctx, n))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert len(results) == 20


class TestFingerprintEdgeCases:
    def test_arg_order_does_not_matter(self):
        store = ConfirmStore()
        ctx_a = FakeAction(tool_name="t", args={"a": 1, "b": 2})
        ctx_b = FakeAction(tool_name="t", args={"b": 2, "a": 1})
        nonce = store.issue(ctx_a)
        assert store.consume(ctx_b, nonce) is True

    def test_different_tool_name_rejected(self):
        store = ConfirmStore()
        nonce = store.issue(FakeAction(tool_name="transfer", args={}))
        assert store.consume(FakeAction(tool_name="defi_swap", args={}), nonce) is False
