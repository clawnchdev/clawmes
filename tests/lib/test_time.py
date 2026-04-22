"""Tests for clawmes.lib.time (schedule parser)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from clawmes.lib.time import (
    Schedule,
    humanize_seconds,
    parse_schedule,
)


class TestParseHuman:
    @pytest.mark.parametrize(
        "text,seconds",
        [
            ("every 1s", 1),
            ("every 5s", 5),
            ("every 1m", 60),
            ("every 30m", 30 * 60),
            ("every 1h", 60 * 60),
            ("every 12h", 12 * 60 * 60),
            ("every 1d", 24 * 60 * 60),
        ],
    )
    def test_intervals(self, text, seconds):
        s = parse_schedule(text)
        assert s.kind == "interval"
        assert s.seconds == seconds

    def test_case_insensitive(self):
        assert parse_schedule("EVERY 1H").seconds == 3600
        assert parse_schedule("Every 1m").seconds == 60

    def test_extra_whitespace_tolerated(self):
        assert parse_schedule("   every    1h    ").seconds == 3600

    def test_zero_period_rejected(self):
        with pytest.raises(ValueError, match="must be > 0"):
            parse_schedule("every 0h")

    def test_unknown_unit_rejected(self):
        with pytest.raises(ValueError, match="Could not parse schedule"):
            parse_schedule("every 1y")


class TestParseCron:
    def test_5_field(self):
        s = parse_schedule("0 9 * * *")
        assert s.kind == "cron"
        assert s.cron_expr == "0 9 * * *"

    def test_4_fields_rejected(self):
        with pytest.raises(ValueError):
            parse_schedule("0 9 * *")

    def test_6_fields_rejected(self):
        with pytest.raises(ValueError):
            parse_schedule("0 9 * * * *")


class TestParseEdgeCases:
    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="Empty schedule"):
            parse_schedule("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError):
            parse_schedule("   ")


class TestNextAfter:
    def test_interval(self):
        s = Schedule(kind="interval", seconds=300)
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert s.next_after(now) == now + timedelta(seconds=300)

    def test_interval_default_now(self):
        # Should use UTC now
        s = Schedule(kind="interval", seconds=10)
        result = s.next_after()
        assert result.tzinfo is not None  # tz-aware

    def test_unknown_kind_raises(self):
        s = Schedule(kind="bogus", seconds=10)
        with pytest.raises(ValueError, match="Unknown schedule kind"):
            s.next_after(datetime.now(tz=UTC))


class TestHumanizeSeconds:
    @pytest.mark.parametrize(
        "s,want",
        [
            (0, "0s"),
            (45, "45s"),
            (60, "1m"),
            (90, "1m"),  # truncates
            (3600, "1h"),
            (3700, "1h"),
            (86400, "1d"),
            (90061, "1d"),
        ],
    )
    def test_humanize(self, s, want):
        assert humanize_seconds(s) == want
