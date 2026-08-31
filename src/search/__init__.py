"""Phase 2b: consent-gated reverse-image search (public API)."""
from src.search.fetcher import fetch_candidate  # noqa: F401
from src.search.web import (  # noqa: F401
    default_allowed_domains,
    filter_allowed_urls,
    search,
    web_detection,
)

__all__ = [
    "default_allowed_domains",
    "fetch_candidate",
    "filter_allowed_urls",
    "search",
    "web_detection",
]
