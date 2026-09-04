"""Tests for the headless-browser fallback adapter.

The tests use a mocked ``render_url`` so the existing test suite
remains browser-free. They cover the diagnostic-routing logic in
``cmd_search`` via the headless_browser module's public surface
(status codes, structured result shape, BROWSER_UNAVAILABLE).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search import headless_browser as hb  # noqa: E402


def test_browser_available_detection() -> None:
    """`is_browser_available` returns True if the find helper returns a path,
    else False. We don't assert the actual result (system-dependent); we
    just assert the call is safe and returns a bool."""
    out = hb.is_browser_available()
    assert isinstance(out, bool)


def test_find_browser_executable_returns_str_or_none() -> None:
    out = hb._find_browser_executable()
    assert out is None or isinstance(out, str)


def test_status_constants_defined() -> None:
    """The status-code taxonomy must be present so callers can switch on them."""
    for name in ("BROWSER_UNAVAILABLE", "JS_RENDER_FAILED", "JS_RENDER_TIMEOUT", "JS_RENDER_SUCCESS"):
        assert hasattr(hb, name), f"missing status constant: {name}"


def test_render_url_returns_dict_with_status_on_browser_missing() -> None:
    """If the system has no browser, render_url returns a dict with
    status=BROWSER_UNAVAILABLE and a reason, instead of raising."""
    with patch.object(hb, "_find_browser_executable", return_value=None):
        res = hb.render_url("https://example.com", timeout_s=2, wait_after_load_s=0)
    assert isinstance(res, dict)
    assert res["status"] == hb.BROWSER_UNAVAILABLE
    assert "reason" in res and isinstance(res["reason"], str)
    assert "elapsed_s" in res


def test_render_url_returns_dict_with_status_on_pychrome_missing() -> None:
    """If the executable exists but pychrome is not installed, render_url
    still returns a structured BROWSER_UNAVAILABLE result instead of raising."""
    with patch.object(hb, "_find_browser_executable", return_value="/fake/chrome"), \
         patch.object(hb, "_import_pychrome", return_value=None):
        res = hb.render_url("https://example.com", timeout_s=2, wait_after_load_s=0)
    assert res["status"] == hb.BROWSER_UNAVAILABLE
    assert "pychrome" in res["reason"].lower() or "not installed" in res["reason"].lower()


def test_render_url_handles_subprocess_error() -> None:
    """If launching Chrome raises a SubprocessError, render_url returns
    JS_RENDER_FAILED, never propagates an exception."""
    import subprocess
    with patch.object(hb, "_find_browser_executable", return_value="/fake/chrome"), \
         patch.object(hb, "_import_pychrome", return_value=object()), \
         patch.object(hb.subprocess, "Popen", side_effect=subprocess.SubprocessError("boom")):
        res = hb.render_url("https://example.com", timeout_s=2, wait_after_load_s=0)
    assert res["status"] == hb.JS_RENDER_FAILED
    assert "boom" in res["reason"]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


class _Skip(Exception):
    pass


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
