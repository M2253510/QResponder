"""Website connector (Phase 10 B) — bounded, SSRF-guarded crawler.

Fetches pages from a user-given URL, extracts main text, and ingests with
provenance. Bounded by --depth / --max-pages, same-domain by default, with a
per-request timeout. An SSRF guard rejects localhost, private/link-local ranges,
and cloud-metadata IPs unless explicitly allowed. The HTTP fetcher is injectable
so tests run fully offline. Runs only via explicit `connect website`.
"""

from __future__ import annotations

import ipaddress
import re
from collections import deque
from urllib.parse import urljoin, urlparse

from .base import Connector, ConnectorError, SourceDoc

_TAG_STRIP = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href=["\']([^"\'#]+)["\']', re.IGNORECASE)
_META_IP = "169.254.169.254"


def _host_blocked(host: str) -> bool:
    if not host:
        return True
    h = host.lower()
    if h in ("localhost",) or h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False  # a public hostname (DNS resolution deferred to the real fetch)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or str(ip) == _META_IP)


def ssrf_ok(url: str, allow_private: bool = False) -> bool:
    if allow_private:
        return True
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        return False
    return not _host_blocked(parts.hostname or "")


def _extract_text(html: str) -> str:
    no_scripts = _TAG_STRIP.sub(" ", html)
    return re.sub(r"\s+", " ", _TAGS.sub(" ", no_scripts)).strip()


def _filename(url: str) -> str:
    p = urlparse(url)
    slug = re.sub(r"[^a-z0-9]+", "-", (p.netloc + p.path).lower()).strip("-") or "page"
    return slug[:80] + ".txt"


def _default_fetch(url: str, timeout: int) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "qresponder-connector"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit connect
        return resp.read().decode("utf-8", errors="replace")


class WebsiteConnector(Connector):
    def __init__(self, url: str, depth: int = 1, max_pages: int = 20, same_domain: bool = True,
                 allow_private: bool = False, timeout: int = 10, tags=None, fetch=None):
        self.url = url
        self.depth = max(0, depth)
        self.max_pages = max(1, max_pages)
        self.same_domain = same_domain
        self.allow_private = allow_private
        self.timeout = timeout
        self.tags = list(tags or [])
        self._fetch = fetch or (lambda u: _default_fetch(u, self.timeout))

    def fetch(self) -> list[SourceDoc]:
        if not ssrf_ok(self.url, self.allow_private):
            raise ConnectorError(f"Blocked by SSRF guard (localhost/private/metadata): {self.url}")
        domain = urlparse(self.url).netloc
        seen: set[str] = set()
        out: list[SourceDoc] = []
        q: deque = deque([(self.url, 0)])
        while q and len(out) < self.max_pages:
            url, d = q.popleft()
            if url in seen:
                continue
            seen.add(url)
            if not ssrf_ok(url, self.allow_private):
                continue  # skip a blocked discovered link (don't abort the crawl)
            try:
                html = self._fetch(url)
            except Exception:  # noqa: BLE001 - one bad page shouldn't sink the crawl
                continue
            text = _extract_text(html)
            if text:
                out.append(SourceDoc(source_name=_filename(url), content=text.encode("utf-8"),
                                     origin=url, tags=self.tags))
            if d < self.depth:
                for href in _HREF.findall(html):
                    nxt = urljoin(url, href)
                    if nxt in seen:
                        continue
                    if self.same_domain and urlparse(nxt).netloc != domain:
                        continue
                    q.append((nxt, d + 1))
        return out
