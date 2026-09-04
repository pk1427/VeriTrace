"""Headless-browser fallback for JS-rendered pages.

This module is **optional** and only invoked by the search pipeline
when:

  1. the user explicitly passes ``--allow-js-render`` to ``veritrace search``;
  2. a candidate URL was classified by the static fetcher as
     :data:`JS_RENDER_REQUIRED`;
  3. the per-run render budget has not been exhausted
     (``--max-js-renders``, default 3);
  4. a compatible Chrome/Chromium is available on the system.

If any of those fail, the page is reported as
``JS_RENDER_REQUIRED_DEFERRED`` (or ``BROWSER_UNAVAILABLE`` when the
system has no compatible browser) and the pipeline continues without
crashing.

The main pipeline must work normally without this dependency. The
``pychrome`` package is the *only* Python-side import (CDP client) and
is loaded lazily so a missing install never affects static-only runs.

Browser detection
-----------------
The module walks a small allowlist of executable names and locations
to find a usable Chrome/Chromium. It does **not** download any
browser — it only uses one already installed on the system.

Render flow
-----------
For each candidate URL:

  1. ``Page.navigate`` to the URL.
  2. Wait for ``Page.loadEventFired`` (with a hard timeout).
  3. ``Page.enable`` + ``Page.getResourceTree`` so we can capture
     subresource URLs (some sites load the canonical image as a
     sub-resource, not in the DOM).
  4. ``Runtime.evaluate(document.documentElement.outerHTML)`` to get
     the rendered DOM.
  5. ``DOM.getDocument`` + a walk to collect ``<img>``/``<source>``
     /``<link rel=image_src|preload[as=image]>`` URLs.
  6. Return:

       {
         "url":          <final URL after redirects>,
         "rendered_url": <same>,
         "html":         <rendered HTML string>,
         "dom_image_urls": <list[str] of img/picture/source/link hrefs>,
         "elapsed_s":    <float>,
       }

Diagnostics
-----------
On any failure mode the function returns
``{"status": "<one of the documented codes>", "reason": "..."}`` so
the caller can route through the diagnostic without raising.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Optional

log = logging.getLogger(__name__)

# Status codes — kept in lock-step with the diagnostic taxonomy.
BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
JS_RENDER_FAILED = "JS_RENDER_FAILED"
JS_RENDER_TIMEOUT = "JS_RENDER_TIMEOUT"
JS_RENDER_SUCCESS = "JS_RENDER_SUCCESS"

# Default Chrome/Chromium executable locations we probe.
_CANDIDATE_BROWSER_PATHS: list = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]
_CANDIDATE_BROWSER_NAMES: list = [
    "google-chrome", "chromium", "chromium-browser",
    "microsoft-edge", "chrome", "brave-browser",
]

# Persistent Chrome user-data dir so consecutive renders don't fight.
_USER_DATA_DIR = "/tmp/veritrace_chrome_userdata"
_CDP_PORT = 9333

# How long we let Chrome sit before talking to it.
_CHROME_STARTUP_WAIT = 2.5


# --------------------------------------------------------------------------- #
# Browser detection
# --------------------------------------------------------------------------- #


def _find_browser_executable() -> Optional[str]:
    """Return the absolute path of a usable browser, or None."""
    for p in _CANDIDATE_BROWSER_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    for name in _CANDIDATE_BROWSER_NAMES:
        p = shutil.which(name)
        if p:
            return p
    return None


def is_browser_available() -> bool:
    """Return True if a usable Chrome/Chromium is on the system."""
    return _find_browser_executable() is not None


# --------------------------------------------------------------------------- #
# pychrome import gate
# --------------------------------------------------------------------------- #


def _import_pychrome():
    """Import pychrome lazily; return None if not installed."""
    try:
        import pychrome  # type: ignore
        return pychrome
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #


def render_url(
    url: str,
    timeout_s: float = 15.0,
    wait_after_load_s: float = 2.0,
) -> dict:
    """Render ``url`` in a headless Chrome and return the rendered DOM.

    Returns a dict; on success the keys are:

        {
            "status":         JS_RENDER_SUCCESS,
            "url":            <final URL after redirects>,
            "rendered_html":  <document.documentElement.outerHTML>,
            "dom_image_urls": <list[str] — images from the rendered DOM>,
            "elapsed_s":      <float>,
        }

    On any failure mode:

        {"status": <code>, "reason": "...", "elapsed_s": <float>}

    The function never raises. The caller (cmd_search) is expected to
    inspect ``status`` and route the result into the diagnostic.
    """
    t0 = time.time()
    browser_exe = _find_browser_executable()
    if not browser_exe:
        return {
            "status": BROWSER_UNAVAILABLE,
            "reason": "no Chrome/Chromium executable found on the system",
            "elapsed_s": round(time.time() - t0, 2),
        }
    pychrome = _import_pychrome()
    if pychrome is None:
        return {
            "status": BROWSER_UNAVAILABLE,
            "reason": "pychrome is not installed (pip install pychrome)",
            "elapsed_s": round(time.time() - t0, 2),
        }

    # Launch Chrome with CDP on a private port + private user-data dir.
    os.makedirs(_USER_DATA_DIR, exist_ok=True)
    cdp_url = f"http://127.0.0.1:{_CDP_PORT}"
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(
            [
                browser_exe,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                f"--remote-debugging-port={_CDP_PORT}",
                f"--user-data-dir={_USER_DATA_DIR}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give Chrome time to bind the port.
        time.sleep(_CHROME_STARTUP_WAIT)
        # CDP probe with a small retry loop.
        browser = _connect_with_retry(pychrome, cdp_url, attempts=5, delay=1.0)
        if browser is None:
            return _fail(JS_RENDER_FAILED, "could not connect to CDP", t0)
        tab = browser.new_tab("about:blank")
        tab.start()
        try:
            # Navigate and wait for the load event.
            tab.call_method("Page.enable")
            load_done = {"done": False}
            tab.set_listener("Page.loadEventFired", lambda *a, **k: load_done.update(done=True))
            tab.call_method("Page.navigate", url=url)
            # Hard timeout: wait up to timeout_s for loadEventFired.
            waited = 0.0
            step = 0.25
            while not load_done["done"] and waited < timeout_s:
                tab.wait(step)
                waited += step
            if not load_done["done"]:
                return _fail(JS_RENDER_TIMEOUT,
                             f"load did not fire within {timeout_s}s", t0)
            # Give the page a moment for lazy-loaded images.
            tab.wait(wait_after_load_s)
            # Final URL.
            final_url = url
            try:
                nav = tab.call_method("Runtime.evaluate",
                                      expression="location.href")
                final_url = nav.get("result", {}).get("value", final_url) or final_url
            except Exception:
                pass
            # Rendered DOM.
            html = ""
            try:
                res = tab.call_method("Runtime.evaluate",
                                      expression="document.documentElement.outerHTML")
                html = res.get("result", {}).get("value", "") or ""
            except Exception:
                pass
            # DOM image URLs (img[src], source[srcset], link[href]).
            dom_imgs = _collect_dom_image_urls(tab)
            return {
                "status": JS_RENDER_SUCCESS,
                "url": final_url,
                "rendered_url": final_url,
                "rendered_html": html,
                "dom_image_urls": dom_imgs,
                "elapsed_s": round(time.time() - t0, 2),
            }
        finally:
            try:
                tab.stop()
            except Exception:
                pass
    except subprocess.SubprocessError as exc:
        return _fail(JS_RENDER_FAILED, f"subprocess error: {exc}", t0)
    except Exception as exc:  # noqa: BLE001
        return _fail(JS_RENDER_FAILED,
                     f"{type(exc).__name__}: {exc}", t0)
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _fail(status: str, reason: str, t0: float) -> dict:
    return {"status": status, "reason": reason,
            "elapsed_s": round(time.time() - t0, 2)}


def _connect_with_retry(pychrome, cdp_url: str, attempts: int, delay: float):
    """Try ``attempts`` times to connect to CDP, then return the browser."""
    import urllib.request as _ur
    last_exc: Optional[Exception] = None
    for _ in range(attempts):
        try:
            with _ur.urlopen(f"{cdp_url}/json/version", timeout=2) as r:
                # CDP is up.
                pass
            return pychrome.Browser(url=cdp_url)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(delay)
    log.warning("headless_browser: CDP not reachable: %s", last_exc)
    return None


# --------------------------------------------------------------------------- #
# DOM image URL collection
# --------------------------------------------------------------------------- #


_JS_COLLECT_IMG_URLS = r"""
(function() {
    var out = [];
    function add(u) { if (u) out.push(u); }
    // <img src=...>
    var imgs = document.querySelectorAll('img');
    for (var i = 0; i < imgs.length; i++) {
        if (imgs[i].src) add(imgs[i].src);
        var ss = imgs[i].getAttribute('srcset');
        if (ss) {
            var first = ss.split(',')[0].trim().split(/\s+/)[0];
            if (first) add(first);
        }
    }
    // <picture><source srcset=...>
    var sources = document.querySelectorAll('picture source');
    for (var j = 0; j < sources.length; j++) {
        var s = sources[j].getAttribute('srcset');
        if (s) {
            var first2 = s.split(',')[0].trim().split(/\s+/)[0];
            if (first2) add(first2);
        }
    }
    // <link rel="image_src" href=...>
    var links = document.querySelectorAll('link[rel~="image_src"]');
    for (var k = 0; k < links.length; k++) {
        if (links[k].href) add(links[k].href);
    }
    // <link rel="preload" as="image" href=...>
    var preloads = document.querySelectorAll('link[rel~="preload"][as~="image"]');
    for (var p = 0; p < preloads.length; p++) {
        if (preloads[p].href) add(preloads[p].href);
    }
    // og:image / twitter:image
    var metas = document.querySelectorAll('meta[property="og:image:secure_url"], meta[property="og:image"], meta[name="twitter:image"]');
    for (var m = 0; m < metas.length; m++) {
        var c = metas[m].getAttribute('content');
        if (c) add(c);
    }
    return out;
})()
"""


def _collect_dom_image_urls(tab) -> list:
    """Return the list of absolute image URLs found in the rendered DOM."""
    try:
        res = tab.call_method(
            "Runtime.evaluate",
            expression=_JS_COLLECT_IMG_URLS,
            returnByValue=True,
        )
        # The result is a JSON-ish blob; pull the value.
        v = res.get("result", {}).get("value", [])
        if isinstance(v, list):
            return [str(u) for u in v if isinstance(u, (str,))]
    except Exception as exc:  # noqa: BLE001
        log.debug("headless_browser: dom_img collect failed: %s", exc)
    return []
