"""Tests for the upgraded public-surface policy module.

Covers:
  * ActivePolicy tier resolution (owner / run / broad default).
  * host_allowed() behaviour for broad default + explicit tiers.
  * filter_candidates() reports rejected-by-policy (never silent).
  * normalize_url() canonical form.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.policy import (  # noqa: E402
    ActivePolicy,
    PolicyTier,
    default_broad_domains,
    filter_candidates,
    host_allowed,
    resolve_policy,
)
from src.search.candidate_normalizer import normalize_url  # noqa: E402
from src.search.base import Candidate  # noqa: E402


# --------------------------------------------------------------------------- #
# Tier resolution
# --------------------------------------------------------------------------- #


def test_resolve_policy_run_beats_owner() -> None:
    p = resolve_policy(owner_domains=["github.io"], run_domains=["facebook.com"])
    assert p.tier == PolicyTier.EXPLICIT_RUN
    assert p.domains == ["facebook.com"]
    assert p.is_restrictive is True


def test_resolve_policy_owner_when_no_run() -> None:
    p = resolve_policy(owner_domains=["github.io"], run_domains=None)
    assert p.tier == PolicyTier.OWNER_EXPLICIT
    assert p.domains == ["github.io"]


def test_resolve_policy_broad_default_when_neither() -> None:
    p = resolve_policy(owner_domains=None, run_domains=None)
    assert p.tier == PolicyTier.BROAD_DEFAULT
    assert p.is_restrictive is False


def test_resolve_policy_empty_lists_treated_as_unset() -> None:
    p = resolve_policy(owner_domains=[], run_domains=[""])
    assert p.tier == PolicyTier.BROAD_DEFAULT


# --------------------------------------------------------------------------- #
# host_allowed
# --------------------------------------------------------------------------- #


def test_broad_default_accepts_facebook() -> None:
    p = resolve_policy(None, None)
    assert host_allowed("https://www.facebook.com/something", p) is True


def test_broad_default_accepts_reddit() -> None:
    p = resolve_policy(None, None)
    assert host_allowed("https://old.reddit.com/r/foo", p) is True


def test_broad_default_rejects_loopback() -> None:
    p = resolve_policy(None, None)
    assert host_allowed("http://127.0.0.1/x", p) is False
    assert host_allowed("http://localhost/x", p) is False


def test_broad_default_rejects_private_ip() -> None:
    p = resolve_policy(None, None)
    assert host_allowed("http://10.0.0.1/x", p) is False
    assert host_allowed("http://192.168.1.1/x", p) is False


def test_broad_default_rejects_local_tld() -> None:
    p = resolve_policy(None, None)
    assert host_allowed("http://foo.local/x", p) is False
    assert host_allowed("http://foo.test/x", p) is False


def test_owner_explicit_is_restrictive() -> None:
    p = resolve_policy(["github.io"], None)
    assert host_allowed("https://pk1427.github.io/VeriTrace/", p) is True
    assert host_allowed("https://facebook.com/x", p) is False


def test_run_explicit_is_restrictive() -> None:
    p = resolve_policy(owner_domains=["github.io"], run_domains=["facebook.com"])
    assert host_allowed("https://facebook.com/x", p) is True
    assert host_allowed("https://pk1427.github.io/VeriTrace/", p) is False


# --------------------------------------------------------------------------- #
# filter_candidates — must never silently drop
# --------------------------------------------------------------------------- #


def test_filter_candidates_separates_rejected_by_policy() -> None:
    cands = [
        Candidate(url="https://facebook.com/x", source="vision_web_detection"),
        Candidate(url="https://reddit.com/x", source="vision_web_detection"),
        Candidate(url="https://github.io/x", source="vision_web_detection"),
    ]
    p = resolve_policy(["github.io"], None)
    kept, rejected = filter_candidates(cands, p)
    kept_urls = [c.url for c in kept]
    rej_urls = [c.url for c in rejected]
    assert kept_urls == ["https://github.io/x"]
    assert set(rej_urls) == {"https://facebook.com/x", "https://reddit.com/x"}
    # Critical contract: NOTHING was silently dropped.
    assert len(kept) + len(rejected) == len(cands)


def test_filter_candidates_broad_default_keeps_everything_legit() -> None:
    cands = [
        Candidate(url="https://facebook.com/x", source="p"),
        Candidate(url="https://twitter.com/x", source="p"),
        Candidate(url="https://youtube.com/x", source="p"),
        Candidate(url="https://wikipedia.org/x", source="p"),
        Candidate(url="http://127.0.0.1/x", source="p"),
    ]
    p = resolve_policy(None, None)
    kept, rejected = filter_candidates(cands, p)
    assert len(kept) == 4
    assert len(rejected) == 1
    assert rejected[0].url == "http://127.0.0.1/x"


# --------------------------------------------------------------------------- #
# Normalize URL — dedup key
# --------------------------------------------------------------------------- #


def test_normalize_url_strips_tracking() -> None:
    a = "https://Example.com/path?utm_source=x&id=1"
    b = "https://example.com/path?id=1"
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_strips_default_port() -> None:
    a = "https://example.com:443/p"
    b = "https://example.com/p"
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_strips_fragment() -> None:
    a = "https://example.com/p#frag"
    b = "https://example.com/p"
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_orders_query_params() -> None:
    a = "https://example.com/p?b=2&a=1"
    b = "https://example.com/p?a=1&b=2"
    assert normalize_url(a) == normalize_url(b)


def test_candidate_canonical_key_uses_normalize() -> None:
    a = Candidate(url="https://Example.com/p?utm_source=x&id=1", source="p")
    b = Candidate(url="https://example.com/p?id=1", source="p")
    assert a.canonical_key() == b.canonical_key()


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
