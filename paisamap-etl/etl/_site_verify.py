"""
_site_verify.py — prove a company controls the website it says it has.

Two proofs count, and only these two:
  * a <meta name="paisamap-site-verification" content="TOKEN"> tag in the head
    of the site's home page, fetched by us; or
  * a Google Search Console account already connected to the company that is a
    verified OWNER (permission level `siteOwner`) of that domain.
Not accepted, deliberately: Google Analytics access (agencies are routinely
given Viewer/Editor/Administrator on a client's property, so it proves nothing
about the website), and Search Console "full user"/"restricted" access (same
reason).

Everything here that touches the network is written for an attacker who picks
the URL: the host must resolve ONLY to public addresses, we connect to the
address we checked (so DNS can't change between the check and the connection),
only ports 80/443, at most three redirects each re-checked, redirects may only
stay on the domain or its `www.` twin, and the body is capped.
"""

import hmac
import http.client
import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

META_NAME = "paisamap-site-verification"
TOKEN_PREFIX = "pm-verify-"
ALLOWED_PORTS = (80, 443)           # module-level so tests can widen it for a local server
MAX_BODY_BYTES = 256 * 1024
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 6
USER_AGENT = "PaisaMapSiteVerifier/1.0 (+https://paisamaps.com)"


class VerifyFetchError(Exception):
    """A page could not be fetched safely; the message is safe to show a user."""


# ── domain identity ──────────────────────────────────────────────────────────
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domain(value):
    """'https://WWW.Acme.co.in/about?x=1' -> 'acme.co.in'. None if it is not a
    plain public hostname (IP literals, 'localhost', single-label names, bad
    characters, anything over 253 chars). The scheme, path, port, credentials
    and a leading 'www.' never count toward identity."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 2048 or any(c in value for c in " \t\r\n\x00"):
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        host = urlparse(value).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(host) > 253 or "." not in host:
        return None
    labels = host.split(".")
    if not all(_LABEL.match(l) for l in labels):
        return None
    tld = labels[-1]
    if tld.isdigit() or not (tld.isalpha() or tld.startswith("xn--")):
        return None                                   # a dotted number is an IP, not a name
    return host


def new_token():
    import secrets
    return TOKEN_PREFIX + secrets.token_hex(16)


def tag_snippet(token):
    return f'<meta name="{META_NAME}" content="{token}">'


# ── safe fetching ────────────────────────────────────────────────────────────
def _is_public_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


def _resolve_public(host, port):
    """The addresses `host` resolves to — refusing if ANY is not public (a
    hostile DNS answer mixing a public and an internal address would otherwise
    let the internal one be picked on a retry)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise VerifyFetchError("We couldn't find that website (its address doesn't resolve).")
    ips = []
    for family, _t, _p, _c, sockaddr in infos:
        ip = sockaddr[0]
        if not _is_public_ip(ip):
            raise VerifyFetchError("That address isn't a public website we can check.")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise VerifyFetchError("We couldn't find that website (its address doesn't resolve).")
    return ips


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, context=ssl.create_default_context(), **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        # Certificate + name are checked against the HOSTNAME, though we dial the IP we vetted.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_page(url, allowed_hosts, *, timeout=None):
    """GET `url` and return (final_url, html_text), or raise VerifyFetchError.
    Every hop — the first request and each redirect — must be http(s) on a
    standard port to a host in `allowed_hosts` that resolves only to public
    addresses."""
    timeout = timeout or FETCH_TIMEOUT
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https"):
            raise VerifyFetchError("That site sends us somewhere we can't follow.")
        host = (parts.hostname or "").lower()
        if host not in allowed_hosts:
            raise VerifyFetchError("That site redirects to a different website, which doesn't count.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise VerifyFetchError("We only check websites on the standard web ports.")
        ip = _resolve_public(host, port)[0]
        cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
        conn = cls(host, ip, port=port, timeout=timeout)
        try:
            path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            conn.request("GET", path, headers={
                "User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5",
                "Accept-Encoding": "identity", "Connection": "close"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise VerifyFetchError("That site redirected us without saying where.")
                url = urljoin(url, loc)
                continue
            if resp.status != 200:
                raise VerifyFetchError(f"That site answered with an error ({resp.status}) instead of a page.")
            body = resp.read(MAX_BODY_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                return url, body.decode(charset, errors="replace")
            except LookupError:
                return url, body.decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            raise VerifyFetchError("We couldn't reach that website (" + type(e).__name__ + ").")
        finally:
            conn.close()
    raise VerifyFetchError("That site redirects too many times.")


# ── reading the tag ──────────────────────────────────────────────────────────
class _MetaTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("name", "").strip().lower() == META_NAME:
                self.found.append(a.get("content", "").strip())
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


def find_verification_tags(html):
    """Every verification-tag value in the page's <head> (a tag in the body
    doesn't count: it is where user-generated content can put one)."""
    p = _MetaTags()
    try:
        p.feed(html or "")
    except Exception:
        pass
    return p.found


def check_meta_tag(domain, token, fetch=fetch_page):
    """(ok, reason). Tries https then http on the bare domain then its www twin."""
    hosts = {domain, "www." + domain}
    last = "We couldn't reach that website."
    for host in (domain, "www." + domain):
        for scheme in ("https", "http"):
            try:
                _final, html = fetch(f"{scheme}://{host}/", hosts)
            except VerifyFetchError as e:
                last = str(e)
                continue
            tags = find_verification_tags(html)
            if any(hmac.compare_digest(t.encode(), token.encode()) for t in tags):
                return True, None
            return False, ("We reached the site but didn't find the verification tag in its <head>."
                           if not tags else "We found a verification tag, but it isn't this company's code.")
    return False, last


# ── Search Console ───────────────────────────────────────────────────────────
def gsc_site_matches(site_url, domain):
    """Does a Search Console property cover `domain`? A `sc-domain:` property
    covers the domain and every subdomain; a URL-prefix property covers its host."""
    if not isinstance(site_url, str):
        return False
    if site_url.startswith("sc-domain:"):
        d = site_url[len("sc-domain:"):].strip().lower()
        return bool(d) and (domain == d or domain.endswith("." + d))
    return normalize_domain(site_url) == domain


def gsc_owner_of(sites, domain):
    """True if `sites` (list_gsc_sites output) contains a property for `domain`
    on which this Google account is a verified OWNER."""
    return any(s.get("permission_level") == "siteOwner" and gsc_site_matches(s.get("site_url"), domain)
               for s in sites or [])
