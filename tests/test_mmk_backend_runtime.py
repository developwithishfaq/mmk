"""Runtime swapping for tests — no broker connection required."""

import mmk_backend.runtime_state as rs


def test_get_runtime_singleton_is_stable():
    a = rs.get_runtime_state()
    b = rs.get_runtime_state()
    assert a is b


def test_configure_runtime_injection():
    original = rs.get_runtime_state()
    fresh = rs.RuntimeState()
    try:
        rs.configure_runtime(fresh)
        assert rs.get_runtime_state() is fresh
    finally:
        rs.configure_runtime(original)
