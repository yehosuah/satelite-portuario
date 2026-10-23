import urllib.error
import urllib.request

import pytest

from puertogt.ml.public_attribute_transport import pooled_public_ranges


class Response:
    status_code = 206
    reason_phrase = "Partial Content"
    headers = {"Content-Range": "bytes 0-3/10", "Content-Length": "4"}

    def __init__(self):
        self.closed = False

    def iter_raw(self, chunk_size):
        yield b"ab"
        yield b"cd"

    def close(self):
        self.closed = True


class Client:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.closed = False

    def build_request(self, method, url, headers):
        return method, url, headers

    def send(self, request, stream):
        self.calls.append(request)
        response = Response()
        self.responses.append(response)
        return response
    def close(self):
        self.closed = True


def test_ranges_reuse_one_client_and_preserve_pins_without_prefetch():
    original = urllib.request._opener
    client = Client()
    url = "https://storage.googleapis.com/gcp-public-data-sentinel-2/source.jp2?generation=123"
    with pooled_public_ranges(client=client):
        for _ in range(2):
            request = urllib.request.Request(url, headers={"Range": "bytes=0-3", "If-Match": '"pinned"'})
            with urllib.request.urlopen(request) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 0-3/10"
                assert response.read(5) == b"abcd"
    assert len(client.calls) == 2
    assert client.calls[0][1] == url
    assert client.calls[0][2]["Range"] == "bytes=0-3"
    assert client.calls[0][2]["If-match"] == '"pinned"'
    assert all(response.closed for response in client.responses)
    assert client.closed
    assert urllib.request._opener is original


def test_provider_error_is_not_retried_by_connection_adapter(monkeypatch):
    original = urllib.request._opener
    client = Client()
    monkeypatch.setattr(Response, "status_code", 403)
    with pooled_public_ranges(client=client):
        request = urllib.request.Request("https://storage.googleapis.com/band.jp2?generation=1",
                                         headers={"Range": "bytes=0-3"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
        error.value.close()
    assert len(client.calls) == 1
    assert urllib.request._opener is original


def test_public_transport_never_forwards_caller_authentication_headers():
    client = Client()
    with pooled_public_ranges(client=client):
        request = urllib.request.Request("https://storage.googleapis.com/band.jp2?generation=1",
                                         headers={"Range": "bytes=0-3", "Authorization": "test-placeholder",
                                                  "Proxy-Authorization": "test-proxy-placeholder"})
        with urllib.request.urlopen(request) as response:
            response.read(5)
    assert set(name.lower() for name in client.calls[0][2]) <= {"range", "if-match", "accept-encoding"}
