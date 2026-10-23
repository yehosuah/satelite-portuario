"""Connection reuse for the dedicated public importer process only.

Byte accounting, immutable generations and Content-Range verification remain in
the existing bounded reader. This adapter performs no retry or prefetch of its
own, and changes neither the requested intervals nor the returned bytes.
"""

from __future__ import annotations

import io
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from contextlib import contextmanager

import httpx


def _url_error(error: Exception) -> urllib.error.URLError:
    cause = error
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return urllib.error.URLError(cause)
        cause = cause.__cause__
    return urllib.error.URLError(error)


class _Body(io.RawIOBase):
    def __init__(self, response):
        self.response = response
        self.chunks = iter(response.iter_raw(chunk_size=64 * 1024))
        self.buffer = bytearray()

    def read(self, size=-1):
        try:
            while size < 0 or len(self.buffer) < size:
                try:
                    self.buffer.extend(next(self.chunks))
                except StopIteration:
                    break
        except httpx.HTTPError as error:
            raise _url_error(error) from error
        count = len(self.buffer) if size < 0 else min(size, len(self.buffer))
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def close(self):
        self.response.close()
        super().close()


class _PublicRangeHandler(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, client):
        self.client = client

    def https_open(self, request):
        parsed = urllib.parse.urlsplit(request.full_url)
        if (parsed.hostname != "storage.googleapis.com" or request.get_method() != "GET"
                or not request.has_header("Range")):
            return None
        # These sources are anonymous. Proxy handling belongs to httpx, and
        # caller credentials are never part of a public object range request.
        headers = {name: value for name, value in request.header_items()
                   if name.lower() in {"range", "if-match", "accept-encoding"}}
        outgoing = self.client.build_request("GET", request.full_url, headers=headers)
        try:
            response = self.client.send(outgoing, stream=True)
        except httpx.HTTPError as error:
            raise _url_error(error) from error
        wrapped = urllib.response.addinfourl(_Body(response), response.headers,
                                             request.full_url, response.status_code)
        wrapped.msg = response.reason_phrase
        return wrapped


class RangeSession:
    """Explicit threaded range opener; metadata and global urllib stay untouched.

    A historical campaign passes this opener only to its bounded range readers.
    The existing reader retains retries, reservation receipts and response checks.
    """

    hosts = frozenset({"storage.googleapis.com", "sentinel-s1-l1c.s3.amazonaws.com",
                       "sentinel-s2-l1c.s3.eu-central-1.amazonaws.com"})

    def __init__(self, *, max_connections=4, client=None):
        if not 1 <= max_connections <= 4:
            raise ValueError("Historical connection concurrency must remain between one and four")
        self.client = client or httpx.Client(timeout=120, follow_redirects=True,
            limits=httpx.Limits(max_connections=max_connections,
                               max_keepalive_connections=max_connections, keepalive_expiry=120))

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.client.close()

    def open(self, request, timeout=120):
        parsed = urllib.parse.urlsplit(request.full_url)
        if (parsed.scheme != "https" or parsed.hostname not in self.hosts
                or parsed.username or parsed.password or request.get_method() != "GET"
                or not request.has_header("Range")):
            raise ValueError("Persistent transport accepts only anonymous pinned public range requests")
        headers = {name: value for name, value in request.header_items()
                   if name.lower() in {"range", "if-match", "accept-encoding"}}
        outgoing = self.client.build_request("GET", request.full_url, headers=headers, timeout=timeout)
        try:
            response = self.client.send(outgoing, stream=True)
        except httpx.HTTPError as error:
            raise _url_error(error) from error
        if response.status_code >= 400:
            response.close()
            raise urllib.error.HTTPError(request.full_url, response.status_code,
                                         response.reason_phrase, response.headers, None)
        wrapped = urllib.response.addinfourl(_Body(response), response.headers,
                                             request.full_url, response.status_code)
        wrapped.msg = response.reason_phrase
        return wrapped


@contextmanager
def pooled_public_ranges(*, client=None, max_connections=1):
    """Use only in the dedicated CLI process, never in a shared app server.

    The old urllib opener is restored even on failure. A single persistent HTTPS
    connection serves the sequential importer; provider errors still reach the
    existing bounded reader and consume its normal retry budget.
    """
    if not 1 <= max_connections <= 4:
        raise ValueError("Public connection concurrency must remain between one and four")
    previous = urllib.request._opener
    session = client or httpx.Client(timeout=120, follow_redirects=False,
                                    limits=httpx.Limits(max_connections=max_connections,
                                                       max_keepalive_connections=max_connections,
                                                       keepalive_expiry=120))
    try:
        urllib.request.install_opener(urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _PublicRangeHandler(session)))
        yield
    finally:
        urllib.request.install_opener(previous)
        session.close()
