"""Phase 2b: candidate URL fetching for face-similarity verification.

:class:`web_detection` in :mod:`src.search.web` returns a mix of page URLs
(`pages_with_matching_images`) and direct image URLs
(`visually_similar_images`). :func:`fetch_candidate` normalizes a URL to a
downloadable on-disk image (following redirects, honoring a size cap) so the
face backend can re-encode it for cosine-similarity verification against the
searched face.

Failures are **never** fatal to the search flow: every failure mode (network
error, non-2xx, non-image content, oversize payload, HTML page with no usable
image) returns a structured reason instead of raising. The caller decides
whether to skip or report a rejected candidate.
"""
from __future__ import annotations

import mimetypes
import os
import tempfile
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Optional, Tuple
from urllib.parse import urljoin

MAX_FETCH_BYTES = 5 * 1024 * 1024  # 5 MiB cap per candidate
IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/bmp", "image/gif", "image/avif", "image/tiff",
}
DEFAULT_UA = "veritrace/1.0 (+https://github.com/pk1427/VeriTrace)"


class _FirstImg(HTMLParser):
    """Tiny HTML parser that grabs the first absolute/relative <img src>."""

    def __init__(self) -> None:
        super().__init__()
        self.src: Optional[str] = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D401 - stdlib hook
        if self.src is not None:
            return
        if tag.lower() == "img":
            for name, value in attrs:
                if name.lower() == "src" and value:
                    self.src = value
                    return


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


def fetch_candidate(url: str, timeout: float = 20.0,
                    max_bytes: int = MAX_FETCH_BYTES) -> Tuple[Optional[str], str, str]:
    """Fetch a candidate URL and coerce it to a local image file.

    Returns ``(path, content_type, reason)``:
      * On success: ``(temp_file_path, "image/jpeg", "ok")`` — the caller owns
        the temp file and must delete it.
      * On failure: ``(None, "", reason)`` where ``reason`` describes why
        (HTTP 403, unreachable, non-image content, payload too large, no image
        found in page, etc.). Never raises.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": DEFAULT_UA, "Accept": "image/*,*/*;q=0.8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            declared = resp.headers.get("Content-Type", "") or ""
            content_type = _guess_content_type(url, declared)
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return None, "", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, "", f"unreachable: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - last-resort, must not crash search
        return None, "", f"{type(exc).__name__}: {exc}"

    if len(data) > max_bytes:
        return None, "", "payload too large"

    if content_type in IMAGE_CONTENT_TYPES:
        return _write_temp(data, content_type), content_type, "ok"

    # Not a direct image: if it's an HTML page, try to extract the first <img>.
    if content_type.startswith("text/html"):
        try:
            parser = _FirstImg()
            parser.feed(data.decode("utf-8", "ignore"))
            if parser.src:
                img_url = urljoin(url, parser.src)
                return fetch_candidate(img_url, timeout=timeout, max_bytes=max_bytes)
        except Exception as exc:  # noqa: BLE001 - keep searching other candidates
            return None, "", f"page parse error: {type(exc).__name__}: {exc}"
        return None, "", "no <img> in page"

    return None, "", f"non-image content ({content_type or 'unknown'})"
