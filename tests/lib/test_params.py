"""Tests for clawmes.lib.params."""

from __future__ import annotations

import pytest

from clawmes.lib.params import (
    ParamError,
    read_bool,
    read_enum,
    read_float,
    read_int,
    read_list,
    read_str,
)


class TestReadStr:
    def test_present(self):
        assert read_str({"a": "x"}, "a") == "x"

    def test_missing_optional(self):
        assert read_str({}, "a") is None

    def test_missing_required(self):
        with pytest.raises(ParamError, match="Missing required parameter: 'a'"):
            read_str({}, "a", required=True)

    def test_empty_string_treated_as_missing_required(self):
        with pytest.raises(ParamError):
            read_str({"a": ""}, "a", required=True)

    def test_coerces_non_string(self):
        assert read_str({"a": 42}, "a") == "42"


class TestReadInt:
    def test_int_passthrough(self):
        assert read_int({"a": 5}, "a") == 5

    def test_numeric_string(self):
        assert read_int({"a": "42"}, "a") == 42

    def test_bool_rejected(self):
        with pytest.raises(ParamError, match="must be an integer, got bool"):
            read_int({"a": True}, "a")

    def test_invalid_string(self):
        with pytest.raises(ParamError, match="must be an integer"):
            read_int({"a": "abc"}, "a")

    def test_missing_required(self):
        with pytest.raises(ParamError):
            read_int({}, "a", required=True)

    def test_float_string_rejected(self):
        # "3.14" is not a valid int — should fail
        with pytest.raises(ParamError):
            read_int({"a": "3.14"}, "a")


class TestReadFloat:
    def test_float_passthrough(self):
        assert read_float({"a": 1.5}, "a") == 1.5

    def test_int_to_float(self):
        assert read_float({"a": 5}, "a") == 5.0

    def test_string_to_float(self):
        assert read_float({"a": "3.14"}, "a") == 3.14

    def test_invalid_string(self):
        with pytest.raises(ParamError, match="must be a number"):
            read_float({"a": "xyz"}, "a")


class TestReadBool:
    @pytest.mark.parametrize("value", [True, "true", "TRUE", "1", "yes", "Y", "on"])
    def test_truthy(self, value):
        assert read_bool({"a": value}, "a") is True

    @pytest.mark.parametrize("value", [False, "false", "0", "no", "off", "anything-else"])
    def test_falsy(self, value):
        assert read_bool({"a": value}, "a") is False

    def test_missing_returns_default(self):
        assert read_bool({}, "a") is False
        assert read_bool({}, "a", default=True) is True

    def test_int_one_is_truthy(self):
        assert read_bool({"a": 1}, "a") is True


class TestReadEnum:
    def test_valid(self):
        assert read_enum({"a": "x"}, "a", ["x", "y"]) == "x"

    def test_invalid(self):
        with pytest.raises(ParamError, match="must be one of"):
            read_enum({"a": "z"}, "a", ["x", "y"])

    def test_missing_required(self):
        with pytest.raises(ParamError):
            read_enum({}, "a", ["x"], required=True)

    def test_missing_optional(self):
        assert read_enum({}, "a", ["x"]) is None


class TestReadList:
    def test_passthrough(self):
        assert read_list({"a": [1, 2]}, "a") == [1, 2]

    def test_string_split_on_comma(self):
        assert read_list({"a": "x, y, z"}, "a") == ["x", "y", "z"]

    def test_string_split_strips_whitespace(self):
        assert read_list({"a": "  x  ,  y  "}, "a") == ["x", "y"]

    def test_string_split_drops_empty(self):
        assert read_list({"a": "x,,y,"}, "a") == ["x", "y"]

    def test_missing_returns_default(self):
        assert read_list({}, "a") == []
        assert read_list({}, "a", default=["z"]) == ["z"]

    def test_default_is_copied(self):
        # Mutating the result must not affect the default
        default = ["x"]
        result = read_list({}, "a", default=default)
        result.append("y")
        assert default == ["x"]

    def test_invalid_type_raises(self):
        with pytest.raises(ParamError, match="must be a list"):
            read_list({"a": 42}, "a")
