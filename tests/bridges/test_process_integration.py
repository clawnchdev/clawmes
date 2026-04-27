"""Integration test — spawns the real Node WC bridge and round-trips a request.

Gated on ``RUN_BRIDGE_INTEGRATION=1`` so the default test run stays
fast and Node-free. CI can set this when Node is available; the
default ``test`` job in ``.github/workflows/ci.yml`` does not.

The bridge must be built first (``npm run build`` under
``clawmes/bridges/sources/wc/``). The test skips if the entry file
isn't present.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from clawmes.bridges.process import BridgeError, BridgeProcess

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BRIDGE_INTEGRATION") != "1",
    reason="set RUN_BRIDGE_INTEGRATION=1 to run real-subprocess tests",
)


_WC_ENTRY = (
    Path(__file__).resolve().parent.parent.parent
    / "clawmes"
    / "bridges"
    / "sources"
    / "wc"
    / "dist"
    / "index.mjs"
)


def _node_available() -> bool:
    return shutil.which("node") is not None


def _bridge_built() -> bool:
    return _WC_ENTRY.exists()


@pytest.fixture
def real_bridge():
    if not _node_available():
        pytest.skip("node binary not in PATH")
    if not _bridge_built():
        pytest.skip(
            f"bridge not built at {_WC_ENTRY} — run `npm run build` in clawmes/bridges/sources/wc/"
        )
    proc = BridgeProcess(name="wc", entry=_WC_ENTRY)
    proc.start()
    yield proc
    proc.stop()


class TestRealBridge:
    def test_health_round_trip(self, real_bridge):
        result = real_bridge.call("health", {}, timeout=5.0)
        assert isinstance(result, dict)
        assert "version" in result
        assert "node_version" in result
        assert "uptime_s" in result

    def test_unknown_method_returns_error(self, real_bridge):
        with pytest.raises(BridgeError) as exc_info:
            real_bridge.call("nonexistent_method", {}, timeout=5.0)
        assert exc_info.value.code == "method_not_implemented"

    def test_multiple_concurrent_requests(self, real_bridge):
        """Stress-test the request-id matching logic with parallel calls."""
        import threading

        results: list[dict] = []
        errors: list[BridgeError] = []

        def caller():
            try:
                results.append(real_bridge.call("health", {}, timeout=5.0))
            except BridgeError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=caller, daemon=True) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 10
        assert errors == []
