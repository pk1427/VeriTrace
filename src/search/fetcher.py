"""Phase 2b: candidate URL fetching for face-similarity verification.

The fetcher handles two kinds of candidate URLs:

1. **Direct image URLs** (``Content-Type: image/*``) — downloaded to a
   temp file, returned immediately.

2. **HTML pages** — the page is fetched, parsed for every plausible image
   URL (``<img src>``, ``<picture><source srcset>``, ``og:image``,
   ``twitter:image``, ``link rel="image_src"``), and the *images* (not
   the page) are returned. Each extracted image is re-fetched
   recursively.

3. **JS-required pages** — if the HTML body is so empty that no
   candidate image is present (e.g. an Instagram login shell), the
   result is explicitly marked ``JS_RENDER_REQUIRED`` so the diagnostic
   can report it. The pipeline does NOT silently fail on these — the
   caller decides whether to add a headless-browser fallback later.

All failure modes are structured (``(path, content_type, reason)``) so
the search flow never has to ``try/except`` around the fetcher.
"""
from __future__ import annotations

import mimetypes
import os
import re
import tempfile
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

MAX_FETCH_BYTES = 5 * 1024 * 1024  # 5 MiB cap per candidate
IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/bmp", "image/gif", "image/avif", "image/tiff",
}
DEFAULT_UA = "veritrace/1.0 (+https://github.com/pk1427/VeriTrace)"

# Explicit sentinel for pages that need JavaScript to render their content.
JS_RENDER_REQUIRED = "JS_RENDER_REQUIRED"


# --------------------------------------------------------------------------- #
# HTML image extraction
# --------------------------------------------------------------------------- #


class _ImageHarvester(HTMLParser):
    """Pull every plausible image URL out of an HTML page.

    Sources (in priority order):
      1. ``<meta property="og:image:secure_url" content>``
      2. ``<meta property="og:image" content>``
      3. ``<meta name="twitter:image" content>``
      4. ``<link rel="image_src" href>``
      5. ``<link rel="preload" as="image" href>``
      6. ``<source srcset=...>`` inside ``<picture>``
      7. ``<img src=...>``
      8. ``<img srcset=...>``

    Tracks the first seen position for each kind so the dedup is
    deterministic and we can debug "where did this URL come from?".
    """

    KIND_PRIORITY = (
        "og_image_secure",
        "og_image",
        "twitter_image",
        "link_image_src",
        "link_preload_image",
        "picture_source_srcset",
        "img_src",
        "img_srcset",
    )

    def __init__(self) -> None:
        super().__init__()
        self.found: List[Tuple[str, str]] = []  # (kind, url) — in encounter order
        self._has_body = False
        self._in_picture = False
        self._in_source = False
        # de-dup by url
        self._seen: set[str] = set()

    def _emit(self, kind: str, url: str) -> None:
        if not url:
            return
        if url in self._seen:
            return
        self._seen.add(url)
        self.found.append((kind, url))

    @property
    def ordered(self) -> List[str]:
        """Return URLs in *priority* order (not encounter order)."""
        order = {k: i for i, k in enumerate(self.KIND_PRIORITY)}
        return [u for _k, u in sorted(self.found, key=lambda kv: order.get(kv[0], 99))]

    # --- HTMLParser hooks -------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D401
        t = tag.lower()
        if t == "body":
            self._has_body = True
            return
        if t == "picture":
            self._in_picture = True
            return
        if t == "source":
            if self._in_picture:
                for name, value in attrs:
                    if name.lower() == "srcset" and value:
                        first = self._first_srcset_url(value)
                        if first:
                            self._emit("picture_source_srcset", first)
            return
        if t == "img":
            for name, value in attrs:
                lname = name.lower()
                if lname == "src" and value:
                    self._emit("img_src", value)
                elif lname == "srcset" and value:
                    first = self._first_srcset_url(value)
                    if first:
                        self._emit("img_srcset", first)
            return
        if t == "link":
            rel = ""
            as_ = ""
            href = None
            for name, value in attrs:
                lname = name.lower()
                if lname == "rel":
                    rel = (value or "").lower()
                elif lname == "as":
                    as_ = (value or "").lower()
                elif lname == "href" and value:
                    href = value
            if href:
                if "image_src" in rel:
                    self._emit("link_image_src", href)
                elif rel == "preload" and as_ == "image":
                    self._emit("link_preload_image", href)
            return
        if t == "meta":
            prop = ""
            content = None
            for name, value in attrs:
                lname = name.lower()
                if lname in ("property", "name"):
                    prop = (value or "").lower()
                elif lname == "content" and value:
                    content = value
            if content:
                if prop == "og:image:secure_url":
                    self._emit("og_image_secure", content)
                elif prop == "og:image":
                    self._emit("og_image", content)
                elif prop == "twitter:image":
                    self._emit("twitter_image", content)
            return

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "picture":
            self._in_picture = False

    @staticmethod
    def _first_srcset_url(srcset: str) -> Optional[str]:
        # srcset syntax:  "url1 1x, url2 2x, ..."  or  "url 480w, url 800w"
        first = srcset.split(",", 1)[0].strip()
        # Drop descriptor (everything after the first whitespace).
        for ws in (" ", "\t"):
            if ws in first:
                first = first.split(ws, 1)[0]
                break
        return first or None


def _looks_like_js_shell(html: str) -> bool:
    """Return True if the HTML body is too empty to be a real page.

    Used to flag a page as ``JS_RENDER_REQUIRED`` instead of returning
    nothing silently. We only treat very small / script-only payloads
    as JS shells so we don't misclassify genuinely text-only pages.
    """
    if not html:
        return True
    stripped = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    stripped = re.sub(r"<style[\s\S]*?</style>", "", stripped, flags=re.IGNORECASE)
    body_match = re.search(r"<body[\s\S]*?</body>", stripped, flags=re.IGNORECASE)
    if body_match:
        body = body_match.group(0)
    else:
        body = stripped
    text = re.sub(r"<[^>]+>", "", body).strip()
    if len(text) < 60 and len(html) < 4_000:
        return True
    if "<script" in html.lower() and len(text) < 20:
        return True
    return False


# --------------------------------------------------------------------------- #
# Low-level HTTP
# --------------------------------------------------------------------------- #


def _guess_content_type(url: str, declared: str) -> str:
    ct = (declared or "").split(";")[0].strip().lower()
    if not ct:
        ct = mimetypes.guess_type(url)[0] or ""
    return ct


def _write_temp(data: bytes, content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type) or ".bin"
    fd, path = tempfile.mkstemp(suffix=ext, prefix="veritrace_candidate_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def _http_get(url: str, timeout: float, max_bytes: int) -> Tuple[Optional[bytes], str, str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": DEFAULT_UA, "Accept": "image/*,text/html,*/*;q=0.8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            declared = resp.headers.get("Content-Type", "") or ""
            content_type = _guess_content_type(url, declared)
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None, content_type, "payload too large"
        return data, content_type, "ok"
    except urllib.error.HTTPError as exc:
        return None, "", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, "", f"unreachable: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, "", f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


def fetch_candidate(url: str, timeout: float = 20.0,
                    max_bytes: int = MAX_FETCH_BYTES) -> Tuple[Optional[str], str, str]:
    """Fetch a candidate URL and coerce it to a local image file.

    Returns ``(path, content_type, reason)``:
      * On success: ``(temp_file_path, "image/jpeg", "ok")`` — the caller
        owns the temp file and must delete it.
      * On failure: ``(None, "", reason)`` where ``reason`` describes
        why. Never raises.

    For *HTML pages*, the function recurses into the highest-priority
    extracted image. If no image can be extracted and the page looks
    like a JS shell, ``reason`` is :data:`JS_RENDER_REQUIRED`.
    """
    data, content_type, reason = _http_get(url, timeout, max_bytes)
    if data is None:
        return None, "", reason

    if content_type in IMAGE_CONTENT_TYPES:
        return _write_temp(data, content_type), content_type, "ok"

    if content_type.startswith("text/html"):
        html = data.decode("utf-8", "ignore")
        images = extract_image_urls_from_html(html, base_url=url)
        if not images:
            if _looks_like_js_shell(html):
                return None, "", JS_RENDER_REQUIRED
            return None, "", "no <img>/<meta> image in page"
        # Recurse into the highest-priority extracted image.
        for img_url in images:
            path, _ct, r = fetch_candidate(img_url, timeout=timeout, max_bytes=max_bytes)
            if path is not None:
                return path, _ct, r
        return None, "", "all extracted images failed to fetch"

    return None, "", f"non-image content ({content_type or 'unknown'})"


def extract_image_urls_from_html(html: str, base_url: str) -> List[str]:
    """Return absolute image URLs extracted from ``html``, in priority order.

    Public so the search pipeline can report the image-extraction
    diagnostic without re-parsing.
    """
    parser = _ImageHarvester()
    try:
        parser.feed(html)
    except Exception:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for url in parser.ordered:
        abs_url = urljoin(base_url, url)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
    return out
