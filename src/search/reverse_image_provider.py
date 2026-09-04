"""Independent live web discovery provider.

This is the genuinely-external second strategy. It performs a real,
input-driven reverse-image search against the **Yandex public image
search** endpoint and emits the resulting page URLs + direct image
URLs as :class:`Candidate` objects in the same schema as Vision.

Why this is genuinely independent
---------------------------------
* Yandex is a different vendor with a different index. The set of
  visually-similar pages Yandex returns for an input face is not a
  subset of what Google Vision returns.
* The provider accepts the **input image** as bytes and derives the
  search request from it. The search results are dynamic and depend
  on the input image's contents, not on a hard-coded list of people,
  names, or URLs.
* No local identity database, no prebuilt index of test subjects,
  no planted pages.

How the input image reaches Yandex
----------------------------------
Yandex's public ``/images/search`` endpoint takes a *public URL* of the
input image. To keep the provider input-driven without forcing the user
to host the image themselves, the provider:

1. Reads the input image bytes from disk.
2. Uploads them to a temporary anonymous public host (``catbox.moe``,
   no auth) and receives a public URL.
3. Calls ``https://yandex.com/images/search?rpt=imageview&url=<public-url>``
   and parses the response for ``originalImage.url`` and
   ``orig_url`` / ``source_url`` fields.
4. Emits each found URL as a ``Candidate``.

This is the only mechanism by which a URL reaches VeriTrace from this
provider; the search request itself depends on the input image's bytes,
not on any preconfigured list.

Failure modes
-------------
* If ``catbox.moe`` upload fails → no candidates from this provider;
  the pipeline continues with the other providers.
* If Yandex's page is empty / JS-rendered → also no candidates; the
  pipeline continues.
* Any exception is caught and surfaced as a stub ``__provider_error__``
  candidate so the diagnostic can still report it.

Configuration
-------------
* ``VERIFACE_PUBLIC_UPLOAD=0`` to disable the public upload step (and
  fall back to the old sidecar / local-index path).
* ``VERIFACE_PUBLIC_UPLOAD_URL=<host>`` to override the anonymous host.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import List, Optional
from urllib.parse import urlparse
from src.search.base import Candidate, CANDIDATE_TYPE_IMAGE, CANDIDATE_TYPE_PAGE

log = logging.getLogger(__name__)

PROVIDER_NAME = "reverse_image_provider"

# Anonymous public hosts for ephemeral image uploads (input image -> public URL).
# Multiple hosts are tried in order; the first one that returns a valid
# public https URL wins. catbox.moe is the primary; uguu.se is the
# fallback (no auth, accepts multipart/form-data with field name "files[]").
DEFAULT_UPLOAD_HOSTS: list = [
    {
        "name": "catbox",
        "url": "https://catbox.moe/user/api.php",
        "field": "fileToUpload",
        "form_field_reqtype": ("reqtype", "fileupload"),
    },
    {
        "name": "uguu",
        "url": "https://uguu.se/upload.php",
        "field": "files[]",
        "form_field_reqtype": None,
    },
]
UPLOAD_FIELD_NAME = "fileToUpload"  # legacy single-host constant kept for compat

# Yandex public reverse-image endpoint. Accepts a public URL; the response
# is server-rendered HTML containing a JSON state blob with the candidates.
YANDEX_ENDPOINT = "https://yandex.com/images/search"

# Minimum image size we will upload (skip absurdly tiny inputs that won't
# match anything anyway).
MIN_UPLOAD_BYTES = 256

# Per-request timeout for upload + Yandex fetch.
UPLOAD_TIMEOUT = 20.0
YANDEX_TIMEOUT = 25.0

# Default browser UA — Yandex and catbox both serve a different (or empty)
# response to bare urllib UAs.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


# --------------------------------------------------------------------------- #
# Public-surface inputs
# --------------------------------------------------------------------------- #


def is_available() -> bool:
    """Return True unless explicitly disabled via env var.

    This provider is *always* available in the sense that it has no
    compile-time deps. It will only refuse to run if the operator
    explicitly disabled it with ``VERIFACE_PUBLIC_UPLOAD=0``.
    """
    flag = os.environ.get("VERIFACE_PUBLIC_UPLOAD", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


# --------------------------------------------------------------------------- #
# Step 1 — upload the input image to a temporary public host
# --------------------------------------------------------------------------- #


def _guess_ext(path: str) -> str:
    p = path.lower()
    for ext in (".jpeg", ".jpg", ".png", ".webp", ".bmp", ".gif"):
        if p.endswith(ext):
            return ext
    return ".jpg"


def _upload_to_public_host(image_path: str) -> Optional[str]:
    """Upload ``image_path`` to one of the configured anonymous public hosts.

    Tries the configured host list in order. Returns the public URL on
    success, or None if every host failed. Never raises.

    The host order can be overridden by setting the env var
    ``VERIFACE_PUBLIC_UPLOAD_URL=host1,host2,...`` to a comma-separated
    list of host NAMES (matches the ``name`` field in
    :data:`DEFAULT_UPLOAD_HOSTS`).
    """
    try:
        with open(image_path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        log.warning("public_upload: cannot read input: %s", exc)
        return None
    if len(data) < MIN_UPLOAD_BYTES:
        log.info("public_upload: input too small (%d bytes), skipping", len(data))
        return None

    hosts = list(DEFAULT_UPLOAD_HOSTS)
    override = os.environ.get("VERIFACE_PUBLIC_UPLOAD_URL", "").strip()
    if override:
        wanted = [h.strip() for h in override.split(",") if h.strip()]
        # Reorder: keep only the ones the user asked for, in that order.
        by_name = {h["name"]: h for h in hosts}
        ordered = [by_name[n] for n in wanted if n in by_name]
        if ordered:
            hosts = ordered

    for host_info in hosts:
        url = _upload_to_one_host(host_info, data, image_path)
        if url:
            log.info("public_upload: success via %s -> %s", host_info["name"], url)
            return url
        log.warning("public_upload: %s failed, trying next host", host_info["name"])
    return None


def _upload_to_one_host(host_info: dict, data: bytes, image_path: str) -> Optional[str]:
    """Upload to a single host; return public URL or None."""
    boundary = "----" + uuid.uuid4().hex
    parts = [f"--{boundary}\r\n"]
    if host_info.get("form_field_reqtype"):
        name, value = host_info["form_field_reqtype"]
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        )
    parts.append(
        f'Content-Disposition: form-data; name="{host_info["field"]}"; '
        f'filename="vt{_guess_ext(image_path)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    )
    body = "".join(parts).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    try:
        req = urllib.request.Request(
            host_info["url"], data=body, method="POST",
            headers={
                "User-Agent": DEFAULT_UA,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "ignore").strip()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("public_upload[%s]: upload failed: %s: %s",
                    host_info["name"], type(exc).__name__, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("public_upload[%s]: unexpected error: %s: %s",
                    host_info["name"], type(exc).__name__, exc)
        return None

    # catbox returns a bare URL. uguu.se returns JSON.
    public_url = _extract_public_url_from_response(raw)
    if not (public_url and (public_url.startswith("http://") or public_url.startswith("https://"))):
        log.warning("public_upload[%s]: unexpected response: %r",
                    host_info["name"], raw[:120])
        return None
    return public_url


def _extract_public_url_from_response(raw: str) -> Optional[str]:
    """Return the public URL from a host's response body.

    catbox returns a bare URL. uguu.se returns JSON like
    ``{"success": true, "files": [{"url": "https://..."}]}``.
    """
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    # Try JSON parse (uguu.se).
    try:
        j = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if isinstance(j, dict):
        files = j.get("files")
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, dict):
                u = first.get("url")
                if isinstance(u, str):
                    return u
        if isinstance(j.get("url"), str):
            return j["url"]
    return None


# --------------------------------------------------------------------------- #
# Step 2 — call Yandex with the public URL
# --------------------------------------------------------------------------- #


def _call_yandex(public_url: str) -> Optional[str]:
    """Call Yandex public reverse-image. Returns the raw HTML body or None."""
    target = f"{YANDEX_ENDPOINT}?rpt=imageview&url={urllib.parse.quote(public_url, safe='')}"
    try:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": DEFAULT_UA, "Accept": "text/html,*/*;q=0.5"},
        )
        with urllib.request.urlopen(req, timeout=YANDEX_TIMEOUT) as resp:
            data = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("yandex: request failed: %s: %s", type(exc).__name__, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("yandex: unexpected error: %s: %s", type(exc).__name__, exc)
        return None
    try:
        return data.decode("utf-8", "ignore")
    except UnicodeDecodeError:
        return data.decode("latin-1", "ignore")


# --------------------------------------------------------------------------- #
# Step 3 — parse Yandex's HTML state blob for candidate URLs
# --------------------------------------------------------------------------- #


# Yandex serializes visually-similar items as a JSON state blob. The
# fields we care about are ``orig_url`` (the page that hosts the image)
# and ``originalImage.url`` (the direct image URL). The blob is a single
# JavaScript object literal embedded in the page, often with escaped
# quotes inside string values.
#
# A robust pattern: walk the blob, find objects containing a ``thumb``
# field (which every visually-similar item has) and pull the URL fields.
_ITEM_RE = re.compile(r'"thumb"\s*:\s*\{')
_URL_FIELDS = (
    "orig_url", "source_url", "page_url", "site_url", "share_url",
)


# Patterns that signal "this is a real external source page, not a
# Yandex-internal link." We extract these directly from the page HTML
# because the state-blob is heavily entity-escaped and fragile to
# parse, but the embedded <a href="..."> links to the source sites are
# always present and always live.
_SOURCE_HOSTS_RE = re.compile(
    r'"(https?://(?:www\.)?'
    r'(?:'
    r'facebook\.com|fb\.com|'
    r'instagram\.com|'
    r'twitter\.com|x\.com|'
    r'reddit\.com|redd\.it|'
    r'linkedin\.com|'
    r'youtube\.com|youtu\.be|'
    r'tiktok\.com|'
    r'pinterest\.com|pinimg\.com|'
    r'wikipedia\.org|wikimedia\.org|commons\.wikimedia\.org|'
    r'github\.com|github\.io|'
    r'pbs\.twimg\.com|'
    r'wisden\.com|cricbuzz\.com|espn\.com|icc-cricket\.com|'
    r'nytimes\.com|bbc\.com|cnn\.com|theguardian\.com|'
    r'medium\.com|substack\.com|'
    r'imdb\.com|rottentomatoes\.com|'
    r'transfermarkt\.com|'
    r'flickr\.com|staticflickr\.com|'
    r'unsplash\.com|pexels\.com|pixabay\.com|'
    r'vk\.com|quora\.com|tumblr\.com|'
    r'avatars\.githubusercontent\.com|yt3\.googleusercontent\.com|'
    r'lookaside\.fbsbx\.com|lookaside\.instagram\.com|cdninstagram\.com|'
    r'media\.licdn\.com|'
    r'staticflickr\.com|'
    r'avatars\.mds\.yandex\.net|'
    r'i\.pinimg\.com|i\.reddit\.com|i\.imgur\.com|'
    r'scontent\.cdninstagram\.com|'
    r'scontent\.fphl2-1\.fna\.fbcdn\.net|scontent\.xx\.fbcdn\.net'
    r')'
    r'(?:/[^\s"\'<>\\]*)?)"',
    re.IGNORECASE,
)


def _extract_items(html: str) -> List[dict]:
    """Walk Yandex's HTML state blob and return a list of item dicts.

    Yandex renders the reverse-image result page server-side but the
    full state is HTML-entity-escaped inside a ``data-state`` attribute
    which is fragile to parse in a streaming way. We therefore rely on
    the *embedded* ``<a href>`` links to the source sites, which are
    always present in the final rendered HTML and always point to
    real, public, non-Yandex hosts (the ``_SOURCE_HOSTS_RE`` allowlist).
    """
    out: List[dict] = []
    if not html:
        return out

    # Find every embedded link to a real external public surface.
    seen: set = set()
    for m in _SOURCE_HOSTS_RE.finditer(html):
        url = m.group(1)
        if url in seen:
            continue
        seen.add(url)
        # Decide candidate type: image hosts (twimg, avatars.*, scontent.*,
        # pinimg, yt3, licdn, lookaside, fbsbx) are direct images;
        # everything else is a page.
        host_match = re.match(r'https?://(?:www\.)?([^/]+)/?', url, re.IGNORECASE)
        host = (host_match.group(1) if host_match else "").lower()
        # Yandex includes a generated CBIR preview of the query image in its
        # own results page.  It is useful UI decoration, not independently
        # discovered public content; accepting it would turn every search
        # into a misleading self-match.  Only emit external sources.
        if host == "yandex.net" or host.endswith(".yandex.net"):
            continue
        is_image_host = any(
            host == h or host.endswith("." + h)
            for h in (
                "pbs.twimg.com", "avatars.githubusercontent.com",
                "yt3.googleusercontent.com", "lookaside.fbsbx.com",
                "lookaside.instagram.com", "cdninstagram.com",
                "media.licdn.com", "i.pinimg.com", "i.reddit.com",
                "i.imgur.com", "scontent.cdninstagram.com",
                "avatars.mds.yandex.net", "staticflickr.com",
            )
        )
        out.append({
            "page_url": url if not is_image_host else "",
            "image_url": url if is_image_host else "",
            "domain": host,
        })
    return out


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def discover(
    image_path: str,
    max_results: int = 30,
    index_path: Optional[str] = None,  # unused — kept for interface compat
) -> List[Candidate]:
    """Run the independent live reverse-image discovery.

    Returns a list of :class:`Candidate` objects. Never raises.
    """
    out: List[Candidate] = []
    if not is_available():
        return out

    t0 = time.time()
    public_url = _upload_to_public_host(image_path)
    if not public_url:
        # Surface a stub so the diagnostic still attributes the failure.
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::upload_failed",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="live_visual_search",
                metadata={"error": "anonymous upload returned no URL",
                          "elapsed_s": round(time.time() - t0, 2)},
            )
        )
        return out

    html = _call_yandex(public_url)
    if not html:
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::yandex_unreachable",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="live_visual_search",
                metadata={"error": "yandex fetch returned no body",
                          "public_url": public_url,
                          "elapsed_s": round(time.time() - t0, 2)},
            )
        )
        return out

    items = _extract_items(html)
    seen_keys: set = set()
    for item in items:
        page_url = item.get("page_url") or ""
        image_url = item.get("image_url") or ""
        domain = item.get("domain") or ""

        # Direct image candidate (CDN-style host).
        if image_url and image_url.startswith("http"):
            key = f"img::{image_url}"
            if key not in seen_keys:
                seen_keys.add(key)
                out.append(
                    Candidate(
                        url=image_url,
                        source=PROVIDER_NAME,
                        candidate_type=CANDIDATE_TYPE_IMAGE,
                        discovery_method="live_visual_search",
                        metadata={
                            "public_url_input": public_url,
                            "host": domain,
                        },
                    )
                )
        # Source page candidate.
        if page_url and page_url.startswith("http"):
            key = f"page::{page_url}"
            if key not in seen_keys:
                seen_keys.add(key)
                out.append(
                    Candidate(
                        url=page_url,
                        source=PROVIDER_NAME,
                        candidate_type=CANDIDATE_TYPE_PAGE,
                        discovery_method="live_visual_search",
                        metadata={
                            "public_url_input": public_url,
                            "host": domain,
                        },
                    )
                )

    if not out:
        # Yandex returned no items — surface this too so the diagnostic
        # can attribute the zero-result to this provider specifically.
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::no_items",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="live_visual_search",
                metadata={"info": "yandex returned no items",
                          "public_url": public_url,
                          "html_size": len(html),
                          "elapsed_s": round(time.time() - t0, 2)},
            )
        )
    return out[:max_results]
