"""Tests for clawmes.plans.triggers (time + price)."""

from __future__ import annotations

from datetime import UTC, datetime

from clawmes.plans.triggers import price_trigger, time_trigger


class TestTimeTrigger:
    def test_fires_when_now_after_next_at(self):
        trigger = {"type": "time", "next_at": "2026-04-27T09:00:00Z"}
        now = datetime(2026, 4, 27, 10, 0, tzinfo=UTC)
        assert time_trigger.evaluate(trigger, now) is True

    def test_does_not_fire_before_next_at(self):
        trigger = {"type": "time", "next_at": "2026-04-27T09:00:00Z"}
        now = datetime(2026, 4, 27, 8, 0, tzinfo=UTC)
        assert time_trigger.evaluate(trigger, now) is False

    def test_fires_at_exact_next_at(self):
        # >= comparison
        trigger = {"type": "time", "next_at": "2026-04-27T09:00:00Z"}
        now = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
        assert time_trigger.evaluate(trigger, now) is True

    def test_missing_next_at(self):
        assert time_trigger.evaluate({"type": "time"}, datetime.now(tz=UTC)) is False

    def test_malformed_next_at(self):
        trigger = {"type": "time", "next_at": "not-a-date"}
        assert time_trigger.evaluate(trigger, datetime.now(tz=UTC)) is False

    def test_empty_next_at(self):
        trigger = {"type": "time", "next_at": ""}
        assert time_trigger.evaluate(trigger, datetime.now(tz=UTC)) is False


class TestPriceTrigger:
    def test_greater_than_fires(self):
        trigger = {"asset": "ETH", "operator": ">", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 2500) is True
        assert price_trigger.evaluate(trigger, 2000) is False
        assert price_trigger.evaluate(trigger, 1999) is False

    def test_greater_or_equal(self):
        trigger = {"operator": ">=", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 2000) is True
        assert price_trigger.evaluate(trigger, 1999) is False

    def test_less_than(self):
        trigger = {"operator": "<", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 1999) is True
        assert price_trigger.evaluate(trigger, 2000) is False

    def test_less_or_equal(self):
        trigger = {"operator": "<=", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 2000) is True
        assert price_trigger.evaluate(trigger, 2001) is False

    def test_equal(self):
        trigger = {"operator": "==", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 2000) is True
        assert price_trigger.evaluate(trigger, 1999.99) is False

    def test_equal_alias(self):
        trigger = {"operator": "=", "threshold": 2000}
        assert price_trigger.evaluate(trigger, 2000) is True

    def test_default_operator_is_gt(self):
        # No operator → defaults to ">"
        trigger = {"threshold": 2000}
        assert price_trigger.evaluate(trigger, 2500) is True
        assert price_trigger.evaluate(trigger, 1999) is False

    def test_none_price_returns_false(self):
        trigger = {"operator": ">", "threshold": 2000}
        assert price_trigger.evaluate(trigger, None) is False

    def test_string_threshold_coerced(self):
        trigger = {"operator": ">", "threshold": "2000"}
        assert price_trigger.evaluate(trigger, 2500) is True

    def test_unparseable_threshold_returns_false(self):
        trigger = {"operator": ">", "threshold": "not-a-number"}
        assert price_trigger.evaluate(trigger, 5000) is False

    def test_unknown_operator_returns_false(self):
        trigger = {"operator": "%", "threshold": 100}
        assert price_trigger.evaluate(trigger, 500) is False
