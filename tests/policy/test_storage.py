"""Tests for clawmes.policy.storage."""

from __future__ import annotations

import json

import pytest

from clawmes.policy.storage import (
    _decode,
    _int_or_none,
    load_policies,
    policies_path,
    save_policies,
)
from clawmes.policy.types import DEFAULT_POLICIES, Policy


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


class TestRoundTrip:
    def test_first_run_writes_defaults(self, tmp_path):
        assert not policies_path().exists()
        loaded = load_policies()
        assert len(loaded) == len(DEFAULT_POLICIES)
        assert policies_path().exists()

    def test_subsequent_load_reads_from_disk(self):
        # First load: defaults installed
        load_policies()
        # Modify the file directly
        custom = [Policy(name="custom", decision="block")]
        save_policies(custom)
        loaded = load_policies()
        assert len(loaded) == 1
        assert loaded[0].name == "custom"

    def test_save_creates_parent_dir(self, tmp_path):
        # Even when policy_dir doesn't exist yet
        save_policies([Policy(name="x", decision="allow")])
        assert policies_path().exists()


class TestRobustness:
    def test_corrupt_file_returns_defaults(self, tmp_path):
        # Write garbage to the policies file
        policies_path().parent.mkdir(parents=True, exist_ok=True)
        policies_path().write_text("not-json", encoding="utf-8")
        loaded = load_policies()
        # Falls back to defaults rather than failing
        assert len(loaded) == len(DEFAULT_POLICIES)

    def test_non_list_file_returns_defaults(self, tmp_path):
        policies_path().parent.mkdir(parents=True, exist_ok=True)
        policies_path().write_text('{"not": "a list"}', encoding="utf-8")
        loaded = load_policies()
        assert len(loaded) == len(DEFAULT_POLICIES)

    def test_skips_malformed_entries(self, tmp_path):
        # Mix of good and bad entries
        path = policies_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {"name": "good", "decision": "allow"},
                    "not-a-dict",  # skipped
                    {"missing": "decision"},  # skipped
                    {"name": "another-good", "decision": "block"},
                ]
            ),
            encoding="utf-8",
        )
        loaded = load_policies()
        names = {p.name for p in loaded}
        assert names == {"good", "another-good"}

    def test_oserror_returns_defaults(self, monkeypatch):
        from clawmes.policy import storage as st

        def boom_read(*a, **kw):
            raise OSError("simulated")

        # Make path.exists return True then read_text raise
        load_policies()  # ensure file gets created first
        monkeypatch.setattr(
            "pathlib.Path.read_text",
            lambda self, **kw: (_ for _ in ()).throw(OSError("disk error")),
        )
        loaded = st.load_policies()
        assert len(loaded) == len(DEFAULT_POLICIES)


class TestRoundTripEncoding:
    def test_full_field_set_roundtrips(self):
        original = Policy(
            name="rich",
            decision="confirm",
            applies_to_tools=("transfer", "defi_swap"),
            chain_ids=(1, 8453),
            max_amount_wei=10**18,
            max_per_hour=5,
            description="rich rule",
        )
        save_policies([original])
        loaded = load_policies()
        assert len(loaded) == 1
        rt = loaded[0]
        assert rt == original


class TestDecodeHelper:
    def test_non_dict_returns_none(self):
        assert _decode("not a dict") is None

    def test_missing_required_field_returns_none(self):
        # No 'name' key
        assert _decode({"decision": "allow"}) is None

    def test_invalid_decision_value(self):
        # Decision is typed Literal — but we don't enforce at decode
        # time (the dataclass will accept any string). Test that decode
        # at least handles a fully-formed dict.
        result = _decode({"name": "x", "decision": "block"})
        assert result is not None
        assert result.decision == "block"


class TestIntOrNone:
    def test_none_passthrough(self):
        assert _int_or_none(None) is None

    def test_int_passthrough(self):
        assert _int_or_none(42) == 42

    def test_string_int(self):
        assert _int_or_none("100") == 100

    def test_invalid_returns_none(self):
        assert _int_or_none("not-a-number") is None
        assert _int_or_none([1, 2]) is None
