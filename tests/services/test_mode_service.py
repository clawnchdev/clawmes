"""Tests for clawmes.services.mode_service."""

from __future__ import annotations

import pytest

from clawmes.services import mode_service as mode_module
from clawmes.services.mode_service import (
    ModeService,
    get_mode_service,
    is_readonly,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setattr(mode_module, "_instance", None)


class TestModeService:
    def test_default_mode_is_normal(self):
        svc = ModeService()
        assert svc.mode == "normal"

    def test_set_to_readonly(self):
        svc = ModeService()
        svc.set_mode("readonly")
        assert svc.mode == "readonly"
        assert svc.is_readonly() is True

    def test_set_to_danger(self):
        svc = ModeService()
        svc.set_mode("danger")
        assert svc.mode == "danger"
        assert svc.is_danger() is True
        assert svc.is_readonly() is False

    def test_set_back_to_normal(self):
        svc = ModeService()
        svc.set_mode("readonly")
        svc.set_mode("normal")
        assert svc.is_readonly() is False
        assert svc.is_danger() is False

    def test_unknown_mode_raises(self):
        svc = ModeService()
        with pytest.raises(ValueError, match="Unknown mode"):
            svc.set_mode("nonsense")  # type: ignore[arg-type]

    def test_user_id_param_accepted(self):
        # Currently a no-op but the signature must accept it
        svc = ModeService()
        assert svc.is_readonly("alice") is False
        svc.set_mode("readonly")
        assert svc.is_readonly("bob") is True

    def test_start_stop(self):
        svc = ModeService()
        svc.start()
        svc.stop()


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_mode_service()
        b = get_mode_service()
        assert a is b

    def test_module_level_is_readonly(self):
        get_mode_service().set_mode("readonly")
        assert is_readonly() is True
        get_mode_service().set_mode("normal")
        assert is_readonly() is False
