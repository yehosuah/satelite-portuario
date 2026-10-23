import socket
import urllib.error

import httpx
import pytest

from puertogt.history import _RangeSource
from puertogt.ml.public_attribute_transport import RangeSession


class Budget:
    def __init__(self):
        self.reserved = []
        self.refunded = []

    def reserve_download(self, key, size):
        self.reserved.append((key, size))
        return len(self.reserved)

    def refund_dns_reservation(self, receipt):
        self.refunded.append(receipt)


def reader(tmp_path, session, budget, name="band"):
    return _RangeSource({"url": f"https://sentinel-s1-l1c.s3.amazonaws.com/{name}.tiff",
                         "size": 8, "etag": '"pinned"'}, budget, "acquisition", tmp_path,
                        opener=session.open)


def response(data=b"abcd", status=206, content_range="bytes 0-3/8"):
    return httpx.Response(status, headers={"Content-Range": content_range}, stream=httpx.ByteStream(data))


def test_explicit_pool_keeps_exact_ranges_pins_accounting_and_local_cache(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Range"] == "bytes=0-3"
        assert request.headers["If-Match"] == '"pinned"'
        assert request.headers["Accept-Encoding"] == "identity"
        return response()

    budget = Budget()
    with RangeSession(client=httpx.Client(transport=httpx.MockTransport(handle))) as session:
        one = reader(tmp_path, session, budget)
        two = reader(tmp_path, session, budget, "other")
        assert one.fetch(0, 4) == two.fetch(0, 4) == one.fetch(0, 4) == b"abcd"
    assert len(requests) == 2
    assert budget.reserved == [("acquisition", 4)] * 2
    assert one.cached_bytes == 4
    assert budget.refunded == []


@pytest.mark.parametrize("failure,expected_calls", [("wrong_range", 1), ("forbidden", 1), ("truncated", 3)])
def test_pool_leaves_validation_and_retry_budget_to_bounded_reader(tmp_path, monkeypatch, failure, expected_calls):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "wrong_range":
            return response(content_range="bytes 1-4/8")
        if failure == "forbidden":
            return response(status=403)
        return response(b"abc")

    monkeypatch.setattr("puertogt.history.time.sleep", lambda seconds: None)
    budget = Budget()
    with RangeSession(client=httpx.Client(transport=httpx.MockTransport(handle))) as session:
        with pytest.raises((ValueError, OSError)):
            reader(tmp_path, session, budget).fetch(0, 4)
    assert len(requests) == expected_calls
    assert sum(size for _, size in budget.reserved) == 4 * expected_calls
    assert budget.refunded == []


def test_pool_preserves_proven_pre_response_dns_failure_for_durable_refund(tmp_path, monkeypatch):
    def handle(request):
        try:
            raise socket.gaierror(socket.EAI_NONAME, "DNS unavailable")
        except socket.gaierror as cause:
            raise httpx.ConnectError("Cannot resolve source", request=request) from cause

    monkeypatch.setattr("puertogt.history.time.sleep", lambda seconds: None)
    budget = Budget()
    with RangeSession(client=httpx.Client(transport=httpx.MockTransport(handle))) as session:
        with pytest.raises(urllib.error.URLError):
            reader(tmp_path, session, budget).fetch(0, 4)
    assert budget.refunded == [1, 2, 3]
    assert sum(size for _, size in budget.reserved) == 12


def test_campaign_passes_explicit_transport_to_materializer(tmp_path, monkeypatch):
    from test_history_recovery import campaign

    from puertogt.history import run_history

    catalog, plan, ledger = campaign(tmp_path)
    sentinel = object()
    seen = []

    def materialize(item, actual_ledger, **kwargs):
        seen.append(kwargs.get("range_opener"))
        (actual_ledger.directory / "PAUSE").write_text("Stop after the fixture.\n")
        raise OSError("End fixture without inference")

    monkeypatch.setattr("puertogt.history.materialize", materialize)
    run_history(catalog, plan, ledger, {}, quality_models=tmp_path, range_opener=sentinel)
    assert seen == [sentinel]


def test_four_concurrent_ranges_cannot_overrun_durable_download_budget(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from puertogt.history import HistoryLedger, HistoryLimits
    from puertogt.jobs import ResourceLimit

    ledger = HistoryLedger(tmp_path / "ledger", HistoryLimits(reserve_bytes=0,
        total_download_bytes=12, acquisition_download_bytes=12, io_workers=4))
    ledger.initialize_plan({"acquisitions": [{"scene": {"acquisition_key": "acquisition", "sensor": "sentinel-2"}}]})
    barrier = threading.Barrier(3)
    requests = []

    def handle(request):
        requests.append(request)
        barrier.wait(timeout=5)
        return response()

    with RangeSession(client=httpx.Client(transport=httpx.MockTransport(handle))) as session:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(reader(tmp_path / "ranges", session, ledger, str(i)).fetch, 0, 4) for i in range(4)]
            values, errors = [], []
            for future in futures:
                try:
                    values.append(future.result())
                except ResourceLimit as error:
                    errors.append(error)
    assert values == [b"abcd"] * 3
    assert len(errors) == 1
    assert len(requests) == 3
    assert ledger.summary()["download_bytes_reserved"] == 12
    with ledger.store.connect() as tx:
        assert tx.execute("SELECT count(*) FROM history_download_reservations").fetchone()[0] == 3
