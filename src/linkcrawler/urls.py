"""URL resolution and normalization.

Two URLs that point at the same resource should normalize to the same string so the
crawler's visited set actually deduplicates them.
"""

from __future__ import annotations

import posixpath
from urllib.parse import urljoin, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Resolve ``url`` against ``base`` and return a canonical form.

    Returns ``None`` for URLs that cannot or should not be crawled (``mailto:``,
    ``javascript:``, empty hrefs, malformed hosts, ...).

    Normalization rules:
    - relative URLs are resolved against ``base``
    - scheme and host are lower-cased; default ports are dropped
    - the fragment is removed
    - ``.`` / ``..`` path segments are collapsed
    - an empty path becomes ``/``; a trailing slash on any other path is removed
    - the query string is kept verbatim (parameter order can be meaningful)
    """
    url = url.strip()
    if not url:
        return None
    if base is not None:
        url = urljoin(base, url)

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or not parts.hostname:
        return None

    host = parts.hostname.lower().rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{userinfo}@{netloc}"

    path = parts.path or "/"
    if "." in path:
        collapsed = posixpath.normpath(path)
        # normpath keeps a leading "//" and drops a trailing slash; both handled below.
        path = "/" + collapsed.lstrip("/") if collapsed != "." else "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    return urlunsplit((scheme, netloc, path, parts.query, ""))


def dedup_key(url: str) -> str:
    """Identity of a normalized URL for "have we crawled this?" checks.

    Sites often mix ``http://`` and ``https://`` links to the same page (e.g. a hardcoded
    header link). Treating them as one page avoids crawling everything twice.
    """
    return url.split(":", 1)[1] if url.startswith(("http:", "https:")) else url


def host_of(url: str) -> str:
    """Return ``host[:port]`` for a normalized URL."""
    return urlsplit(url).netloc.rsplit("@", 1)[-1]


def same_site(url: str, root_host: str) -> bool:
    """True if ``url`` is on ``root_host``, treating ``www.`` as equivalent."""
    return _strip_www(host_of(url)) == _strip_www(root_host)


def _strip_www(host: str) -> str:
    return host.removeprefix("www.")
