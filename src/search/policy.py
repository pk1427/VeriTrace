"""Public-surface policy for VeriTrace candidate discovery.

A result is *never* silently lost because of an invisible / overly-narrow
allow-list. The policy is split into three explicit tiers so the CLI can
always tell the user which tier is active, and so the diagnostic can
attribute rejected candidates to the policy.

Tiers
-----
1. **OWNER_EXPLICIT**  — the matched owner has set ``allowed_domains`` in
   their consent-registry record.  Restrictive per-owner opt-in.
2. **EXPLICIT_RUN**    — caller passed ``--allow-domains`` for one run.
   Highest precedence; explicit run-level opt-in.
3. **BROAD_DEFAULT**   — broad public-surface allow-list. Optimised for
   genuine public discovery, *not* for a handpicked set of hosts. Includes:

   - General public web (no per-host restriction in spirit, but we still
     apply minimal anti-abuse categories below).
   - Major social/web surfaces (Facebook, Reddit, X/Twitter, Instagram,
     YouTube, LinkedIn, Wikipedia/Wikimedia, GitHub, Stack Exchange,
     Medium, Substack, Tumblr, Pinterest, VK, Quora, news outlets, etc.).
   - Public image CDNs (Wikimedia Commons, Unsplash, Pexels, Imgur, etc.).
   - GitHub Pages / static-site hosts (github.io).

The default tier also *blocks* a tiny, well-known category list of hosts
that are useless for face verification or actively harmful: localhost /
loopback, private RFC1918, link-shortener front-ends, parked / non-content
hosts.  This is the *only* place defaults get applied — it is *never* a
per-owner enforcement.

A result that is rejected because the active policy excludes it is counted
in the diagnostic report so the user always knows how many candidates were
lost to the policy.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional
from urllib.parse import urlparse


# --------------------------------------------------------------------------- #
# Tier enum + active-policy descriptor
# --------------------------------------------------------------------------- #


class PolicyTier(str, Enum):
    """Which tier is currently in effect."""

    OWNER_EXPLICIT = "owner_explicit"
    EXPLICIT_RUN = "explicit_run"
    BROAD_DEFAULT = "broad_default"


# --------------------------------------------------------------------------- #
# Default policy
# --------------------------------------------------------------------------- #

# Major public surfaces — broad categories, not handpicked one-offs.
# These are *social / web / image* surfaces where face-bearing photos are
# likely to be discoverable for a public figure.
_BROAD_PUBLIC_SURFACES: list[str] = [
    # Encyclopedic / reference
    "wikipedia.org", "wikimedia.org", "commons.wikimedia.org",
    "en.wikipedia.org",
    # Image libraries
    "unsplash.com", "pexels.com", "pixabay.com", "imgur.com", "flickr.com",
    "staticflickr.com",
    # Major social / web surfaces
    "facebook.com", "fb.com", "m.facebook.com", "web.facebook.com",
    "instagram.com", "cdninstagram.com",
    "reddit.com", "redd.it",
    "twitter.com", "x.com", "t.co",
    "youtube.com", "ytimg.com", "youtu.be",
    "linkedin.com", "licdn.com", "lnkd.in",
    "medium.com", "substack.com", "substackcdn.com",
    "tumblr.com", "pinterest.com", "pinimg.com",
    "vk.com", "quora.com", "tiktok.com",
    "github.com", "github.io", "raw.githubusercontent.com",
    "avatars.githubusercontent.com",
    "stackexchange.com", "stackoverflow.com",
    "nytimes.com", "theguardian.com", "bbc.com", "bbc.co.uk",
    "cnn.com", "washingtonpost.com", "reuters.com", "apnews.com",
    "espn.com", "cricbuzz.com", "wisden.com", "icc-cricket.com",
    "indianexpress.com", "timesofindia.com", "hindustantimes.com",
    "ndtv.com", "thehindu.com",
    "imdb.com", "rottentomatoes.com", "variety.com", "hollywoodreporter.com",
    "forbes.com", "bloomberg.com", "crunchbase.com",
]

# Hosts that are *always* rejected regardless of tier — they don't carry
# face-bearing content or are actively harmful to fetch.
_BLOCKED_HOSTS: list[str] = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
]

# TLD suffixes that are useless (link shorteners, parked domains, etc.).
# We don't try to enumerate them — only the well-known public ones.
_BLOCKED_SUFFIXES: list[str] = [
    # No content at all
    "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "t.co",  # t.co blocked in default
]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ActivePolicy:
    """The policy currently in effect for a search run."""

    tier: PolicyTier
    domains: list[str]  # explicit list, may be empty for "broad default"
    is_restrictive: bool  # True if the list is non-empty (we are filtering)

    def describe(self) -> str:
        if self.tier == PolicyTier.OWNER_EXPLICIT:
            return f"OWNER_EXPLICIT ({len(self.domains)} domain(s)): {','.join(self.domains)}"
        if self.tier == PolicyTier.EXPLICIT_RUN:
            return f"EXPLICIT_RUN ({len(self.domains)} domain(s)): {','.join(self.domains)}"
        return "BROAD_DEFAULT (broad public-surface allow-list; no per-host restriction)"


def resolve_policy(
    owner_domains: Optional[Iterable[str]],
    run_domains: Optional[Iterable[str]] = None,
) -> ActivePolicy:
    """Pick the active policy tier.

    Precedence (highest first):
        1. ``run_domains`` non-empty  → EXPLICIT_RUN
        2. ``owner_domains`` non-empty → OWNER_EXPLICIT
        3. otherwise                    → BROAD_DEFAULT
    """
    if run_domains:
        domains = _clean(run_domains)
        if domains:
            return ActivePolicy(
                tier=PolicyTier.EXPLICIT_RUN,
                domains=domains,
                is_restrictive=True,
            )
    if owner_domains:
        domains = _clean(owner_domains)
        if domains:
            return ActivePolicy(
                tier=PolicyTier.OWNER_EXPLICIT,
                domains=domains,
                is_restrictive=True,
            )
    return ActivePolicy(
        tier=PolicyTier.BROAD_DEFAULT,
        domains=[],
        is_restrictive=False,
    )


def default_broad_domains() -> list[str]:
    """The broad public-surface allow-list (read-only)."""
    return list(_BROAD_PUBLIC_SURFACES)


# --------------------------------------------------------------------------- #
# Filter implementation
# --------------------------------------------------------------------------- #


def _clean(domains: Iterable[str]) -> list[str]:
    out: list[str] = []
    for d in domains:
        if not d:
            continue
        d = str(d).strip().lower().lstrip(".")
        if d:
            out.append(d)
    return out


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_or_loopback(host: str) -> bool:
    if not _is_ip_literal(host):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _host_allowed_default(host: str) -> bool:
    """Apply the BROAD_DEFAULT policy to a single host.

    Returns True if the host is on a known public surface or matches one
    of the broad TLDs. Returns False only if the host is unambiguously
    private/loopback or is on the small blocked list.
    """
    if not host:
        return False
    host = host.lower().lstrip("www.")

    # Hard blocks
    if host in _BLOCKED_HOSTS:
        return False
    if _is_private_or_loopback(host):
        return False
    if any(host == s or host.endswith("." + s) for s in _BLOCKED_SUFFIXES):
        return False

    # Broad allow: any host that ends in a known public surface.
    for surface in _BROAD_PUBLIC_SURFACES:
        if host == surface or host.endswith("." + surface):
            return True

    # Otherwise: by default we *accept* — the policy is broad, not restrictive.
    # We only reject obvious garbage (e.g. ".local", ".test") so we don't
    # burn time fetching nothing.
    if host.endswith(".local") or host.endswith(".test") or host.endswith(".invalid"):
        return False
    return True


def _host_allowed_explicit(host: str, allowed: list[str]) -> bool:
    host = host.lower().lstrip("www.")
    return any(host == d or host.endswith("." + d) for d in allowed)


def host_allowed(url: str, policy: ActivePolicy) -> bool:
    """Return True if the URL's host passes the active policy."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if policy.tier == PolicyTier.BROAD_DEFAULT:
        return _host_allowed_default(host)
    return _host_allowed_explicit(host, policy.domains)


def filter_candidates(candidates, policy: ActivePolicy) -> tuple[list, list]:
    """Split ``candidates`` into (kept, rejected_by_policy).

    Accepts both :class:`src.search.Candidate` and legacy ``{"url": ...}``
    dicts so the policy can be reused across the new + old pipelines.
    ``rejected_by_policy`` is returned separately (NOT silently dropped)
    so the diagnostic report can report its size.
    """
    kept, rejected = [], []
    for c in candidates:
        url = c.get("url") if isinstance(c, dict) else getattr(c, "url", "")
        if host_allowed(url, policy):
            kept.append(c)
        else:
            rejected.append(c)
    return kept, rejected
