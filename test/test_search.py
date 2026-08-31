"""Phase 2b — reverse-image search (offline tests + owner-scoped + fetcher).

The live Vision call requires a GCP service-account key + billing (integration-
tested via `veritrace search` against Google Cloud Vision); the allow-list
filtering, the fetcher, and the consent/owner-domain wiring below are pure
or use isolated temp registries and run offline with no credentials.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search import filter_allowed_urls, default_allowed_domains, fetch_candidate  # noqa: E402
from src.search.web import _host_allowed  # noqa: E402
from src.consent.registry import (  # noqa: E402
    enroll, check_consent, load_registry, reset_registry, get_subject,
)

SAMPLES = ROOT / "test" / "samples"
EXTS = (".jpg", ".jpeg", ".png")


class Skip(Exception):
    """Raised by a test to signal 'skipped' (not a failure)."""


def _find(names):
    for n in names:
        for ext in EXTS:
            f = SAMPLES / f"{n}{ext}"
            if f.is_file():
                return f
    return None


ALLOWED = ["wikipedia.org", "commons.wikimedia.org", "unsplash.com", "pexels.com"]
OWNER_A_DOMAINS = ["a.example", "b.example"]
OWNER_B_DOMAINS = ["c.example", "d.example"]


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


def test_per_owner_domains_filter_is_isolation() -> None:
    """Each owner's scope is their own list, never a shared global."""
    matches = [
        {"url": "https://a.example/p", "host": "a.example", "domain": "a.example"},
        {"url": "https://b.example/p", "host": "b.example", "domain": "b.example"},
        {"url": "https://c.example/p", "host": "c.example", "domain": "c.example"},
        {"url": "https://d.example/p", "host": "d.example", "domain": "d.example"},
    ]
    a = filter_allowed_urls(matches, OWNER_A_DOMAINS)
    b = filter_allowed_urls(matches, OWNER_B_DOMAINS)
    assert {m["domain"] for m in a} == {"a.example", "b.example"}
    assert {m["domain"] for m in b} == {"c.example", "d.example"}
    # Owner A must NOT see owner B's domains and vice-versa.
    assert "c.example" not in {m["domain"] for m in a}
    assert "a.example" not in {m["domain"] for m in b}


def test_get_subject_returns_owner_record_with_domains() -> None:
    reg = {
        "version": "0.2.0", "match_threshold": 0.6,
        "subjects": [
            {"subject_id": "ownerA", "allowed_domains": ["a.example", "b.example"],
             "embedding_b64": "", "embedding_dim": 512},
            {"subject_id": "ownerB", "allowed_domains": ["c.example", "d.example"],
             "embedding_b64": "", "embedding_dim": 512},
        ],
    }
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    tmp.write_text(json.dumps(reg))
    a = get_subject("ownerA", path=tmp)
    b = get_subject("ownerB", path=tmp)
    none = get_subject("ghost", path=tmp)
    assert a is not None and a["allowed_domains"] == ["a.example", "b.example"]
    assert b is not None and b["allowed_domains"] == ["c.example", "d.example"]
    # Records are scope-isolated: A's domains never leak into B's.
    assert a["allowed_domains"] != b["allowed_domains"]
    assert none is None


def test_fetch_candidate_rejects_unreachable() -> None:
    # A closed local port -> deterministic, fast connection refusal (no network).
    path, _ct, reason = fetch_candidate("http://127.0.0.1:1/veritrace-nope", timeout=3)
    assert path is None
    assert reason  # a non-empty reason, never an exception


# ---- Face-dependent tests (use isolated temp registries; skip if no samples) ----

def test_enroll_persists_per_owner_allowed_domains() -> None:
    enroll_p = _find(["your_photo1", "same_a"])
    if not enroll_p:
        raise Skip("need test/samples/your_photo1.jpeg (or same_a) to enroll an owner")
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    rec = enroll("ownerA", str(enroll_p), path=tmp,
                 allowed_domains=["a.example", "b.example"])
    subj = get_subject("ownerA", path=tmp)
    assert subj is not None, "owner persisted"
    assert subj["allowed_domains"] == ["a.example", "b.example"], "per-owner scope stored"
    assert rec["subject_id"] == "ownerA"


def test_check_consent_returns_owner_allowed_domains() -> None:
    """The gate must surface the matched OWNER's domains, not a global default."""
    enroll_p = _find(["your_photo1", "same_a"])
    same_p = _find(["your_photo2", "same_b"])
    if not (enroll_p and same_p):
        raise Skip("need your_photo1 + your_photo2 in test/samples/")
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    enroll("ownerA", str(enroll_p), path=tmp,
           allowed_domains=["owner-a-only.example"])
    res = check_consent(str(same_p), path=tmp)
    assert res.granted, f"gate should grant same owner (score={res.best_score})"
    assert res.allowed_domains == ["owner-a-only.example"], \
        f"expected per-owner scope, got {res.allowed_domains!r}"
    # And it must NOT be the global default list.
    assert res.allowed_domains != default_allowed_domains()


def test_two_owners_search_scope_is_isolated() -> None:
    """Two owners with distinct domains: each check_consent returns only its own."""
    enroll_p = _find(["your_photo1", "same_a"])
    diff_p = _find(["teammate_photo", "diff_a"])
    if not (enroll_p and diff_p):
        raise Skip("need your_photo1 + teammate_photo in test/samples/")
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    enroll("alice", str(enroll_p), path=tmp, allowed_domains=["alice.example"])
    enroll("bob", str(diff_p), path=tmp, allowed_domains=["bob.example"])
    # Alice's input -> Alice's scope only.
    ra = check_consent(str(enroll_p), path=tmp)
    assert ra.granted and ra.allowed_domains == ["alice.example"]
    # Bob's input -> Bob's scope only.
    rb = check_consent(str(diff_p), path=tmp)
    assert rb.granted and rb.allowed_domains == ["bob.example"]
    assert ra.allowed_domains != rb.allowed_domains


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures, skipped = 0, 0

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

    import inspect

    for t in tests:
        m = _Monkey()
        try:
            params = inspect.signature(t).parameters
            t(monkeypatch=m) if "monkeypatch" in params else t()
            print(f"PASS  {t.__name__}")
        except Skip as s:
            skipped += 1
            print(f"SKIP  {t.__name__}: {s}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        finally:
            m.undo()
    print(f"\n{len(tests) - failures - skipped}/{len(tests)} passed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
