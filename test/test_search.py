"""Phase 2b — reverse-image search (offline tests for the allow-list logic).

The live Vision call requires a GCP service-account key + billing (integration-
tested via `veritrace search` against Google Cloud Vision); the allow-list
filtering below is pure and runs offline with no credentials.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search import filter_allowed_urls, default_allowed_domains  # noqa: E402
from src.search.web import _host_allowed  # noqa: E402

ALLOWED = ["wikipedia.org", "commons.wikimedia.org", "unsplash.com", "pexels.com"]


def test_filter_keeps_only_allowed_domains() -> None:
    matches = [
        {"url": "https://en.wikipedia.org/wiki/Foo", "host": "en.wikipedia.org", "domain": "en.wikipedia.org"},
        {"url": "https://unsplash.com/photos/abc", "host": "unsplash.com", "domain": "unsplash.com"},
        {"url": "https://evil.example/x", "host": "evil.example", "domain": "evil.example"},
        {"url": "https://commons.wikimedia.org/wiki/File:Bar", "host": "commons.wikimedia.org", "domain": "commons.wikimedia.org"},
        {"url": "", "host": "", "domain": ""},
    ]
    keep = filter_allowed_urls(matches, ALLOWED)
    urls = [m["url"] for m in keep]
    assert "https://en.wikipedia.org/wiki/Foo" in urls
    assert "https://unsplash.com/photos/abc" in urls
    assert "https://commons.wikimedia.org/wiki/File:Bar" in urls
    assert "https://evil.example/x" not in urls
    assert "" not in urls


def test_host_allowed_suffix_and_subdomain() -> None:
    assert _host_allowed("en.wikipedia.org", ["wikipedia.org"]) is True
    assert _host_allowed("commons.wikimedia.org", ["commons.wikimedia.org"]) is True
    assert _host_allowed("www.unsplash.com", ["unsplash.com"]) is True
    assert _host_allowed("notwikipedia.org", ["wikipedia.org"]) is False
    assert _host_allowed("evil.com", ["wikipedia.org", "unsplash.com"]) is False


def test_default_allowed_domains_parses_env(monkeypatch) -> None:
    monkeypatch.setenv("VERIFACE_ALLOWED_DOMAINS", "a.com, B.com , ,c.com")
    out = default_allowed_domains()
    assert out == ["a.com", "b.com", "c.com"]


def test_default_allowed_domains_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("VERIFACE_ALLOWED_DOMAINS", raising=False)
    out = default_allowed_domains()
    assert "wikipedia.org" in out and "unsplash.com" in out


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    import inspect

    class _Monkey:
        def __init__(self):
            self._orig = []
        def setattr(self, obj, name, value, raising=True):
            self._orig.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        def undo(self):
            for obj, name, original in reversed(self._orig):
                if obj == "env":
                    if original is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = original
                else:
                    setattr(obj, name, original)
            self._orig.clear()
        def setenv(self, name, value):
            self._orig.append(("env", name, os.environ.get(name)))
            os.environ[name] = value
        def delenv(self, name, raising=True):
            self._orig.append(("env", name, os.environ.get(name)))
            os.environ.pop(name, None)

    for t in tests:
        m = _Monkey()
        try:
            params = inspect.signature(t).parameters
            t(monkeypatch=m) if "monkeypatch" in params else t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        finally:
            m.undo()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
