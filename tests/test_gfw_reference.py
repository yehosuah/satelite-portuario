import json
import runpy
import stat
from pathlib import Path

import httpx
import pytest

from puertogt.gfw_reference import (
    DATASET,
    DEFAULT_LIMITS,
    ENDPOINT,
    PORTS_VERSION,
    GFWError,
    build_plan,
    configure_token,
    fetch,
    freeze_plan,
    read_plan,
    status,
)

TOKEN = "offline-test-secret-123456789"


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    features = []
    for port, longitude in [("prq", -90.8), ("pbr", -88.6), ("stc", -88.7)]:
        features.append({
            "type": "Feature", "properties": {"port_id": port, "zone_id": port + "_muelles",
                                               "version": PORTS_VERSION},
            "geometry": {"type": "Polygon", "coordinates": [[[longitude, 14],
                         [longitude + .01, 14], [longitude + .01, 14.01],
                         [longitude, 14.01], [longitude, 14]]]},
        })
    (config / "ports.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "version": PORTS_VERSION, "features": features,
    }))
    return tmp_path


def prepared(workspace, **limits):
    ports = workspace / "config/ports.geojson"
    frozen = freeze_plan(workspace, ports, limits={**DEFAULT_LIMITS, **limits})
    return ports, Path(frozen["path"]), frozen["sha256"]


def execute(workspace, handle, **limits):
    ports, path, digest = prepared(workspace, **limits)
    configure_token(workspace, TOKEN)
    return fetch(workspace, path, digest, ports, transport=httpx.MockTransport(handle))


def event(identifier):
    return {"id": identifier, "type": "port_visit", "start": "2025-01-03T00:00:00Z",
            "end": "2025-01-04T00:00:00Z", "vessel": {"id": "example-vessel"}}


def page(entries, total=None, next_offset=None):
    return {"entries": entries, "total": len(entries) if total is None else total,
            "nextOffset": len(entries) if next_offset is None else next_offset}


def test_plan_is_metadata_only_train_v1_and_cannot_be_overwritten(workspace):
    ports, path, digest = prepared(workspace)
    plan = read_plan(workspace, path, digest, ports)
    assert plan["endpoint"] == ENDPOINT
    assert plan["split"] == "train"
    assert plan["window"] == {"start": "2025-01-01", "end_exclusive": "2025-07-01"}
    assert [q["port_id"] for q in plan["queries"]] == ["prq", "pbr", "stc"]
    assert all(q["body"]["datasets"] == [DATASET] for q in plan["queries"])
    assert all(set(q["body"]) == {"datasets", "startDate", "endDate", "geometry"}
               for q in plan["queries"])
    with pytest.raises(FileExistsError):
        freeze_plan(workspace, ports)
    with pytest.raises(GFWError, match="Only January-June"):
        build_plan(ports, start="2025-10-01", end="2026-01-01")


@pytest.mark.parametrize("change", ["source", "endpoint", "window", "digest", "geometry"])
def test_tampering_or_changed_source_is_rejected_before_network(workspace, change):
    ports, path, digest = prepared(workspace)
    configure_token(workspace, TOKEN)
    envelope = json.loads(path.read_text())
    if change == "source":
        ports.write_text(ports.read_text() + " ")
    elif change == "endpoint":
        envelope["plan"]["endpoint"] = "https://other.example/events"
    elif change == "window":
        envelope["plan"]["window"]["end_exclusive"] = "2026-09-01"
    elif change == "geometry":
        envelope["plan"]["queries"][0]["body"]["geometry"]["coordinates"][0][0][0] = 0
    else:
        digest = "0" * 64
    path.write_text(json.dumps(envelope))
    with pytest.raises(GFWError, match="Frozen plan"):
        fetch(workspace, path, digest, ports,
              transport=httpx.MockTransport(lambda request: pytest.fail("Network before validation")))


def test_token_permissions_no_implicit_replacement_and_status_never_uses_network(workspace):
    assert status(workspace)["status"] == "missing_token"
    assert configure_token(workspace, TOKEN)["network"] is False
    secret = workspace / "data/secrets/gfw-token"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert status(workspace) == {"status": "configured_not_verified", "configured": True,
                                 "network": False, "api_access_verified": False}
    with pytest.raises(GFWError, match="Credential exists"):
        configure_token(workspace, "different-secret")
    assert secret.read_text() == TOKEN
    configure_token(workspace, "different-secret", replace=True)
    assert secret.read_text() == "different-secret"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    secret.chmod(0o644)
    assert status(workspace)["status"] == "unsafe_token_file"


def test_missing_token_stops_before_client_creation_or_output(workspace, monkeypatch):
    ports, path, digest = prepared(workspace)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Client created without token"))
    with pytest.raises(GFWError) as result:
        fetch(workspace, path, digest, ports)
    assert result.value.code == "missing_token"
    assert not (workspace / "data/references/gfw/runs").exists()
    cli = runpy.run_path(str(Path(__file__).parents[1] / "scripts/gfw_reference.py"))["main"]
    assert cli(["--workspace", str(workspace), "status"]) == 2
    assert cli(["--workspace", str(workspace), "fetch", "--plan", str(path), "--sha256", digest]) == 2


def test_bounded_pagination_uses_fixed_host_body_and_records_no_secret(workspace):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url).split("?")[0] == ENDPOINT
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        body = json.loads(request.content)
        assert body["datasets"] == [DATASET]
        assert body["startDate"] == "2025-01-01" and body["endDate"] == "2025-07-01"
        assert body["geometry"]["type"] == "Polygon"
        offset = int(request.url.params["offset"])
        return httpx.Response(201, json=page([event(str(offset))], total=2, next_offset=offset + 1))

    result = execute(workspace, handle, page_size=1)
    assert result["status"] == "complete"
    assert result["completed_ports"] == ["prq", "pbr", "stc"]
    assert len(requests) == 6
    assert [r["offset"] for r in result["pages"]] == [0, 1, 0, 1, 0, 1]
    assert all(r["http_status"] == 201 for r in result["pages"])
    assert result["labels_imported"] is False
    for path in (workspace / "data/references/gfw").rglob("*.json"):
        assert TOKEN not in path.read_text()


@pytest.mark.parametrize("http_status,reason", [(401, "unauthorized"), (403, "forbidden"),
                                                (429, "rate_limited"), (302, "http_error")])
def test_auth_errors_and_redirects_never_echo_body_or_follow(workspace, http_status, reason):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(http_status, text=TOKEN,
                              headers={"Location": "https://other.example/" + TOKEN})

    result = execute(workspace, handle)
    assert len(calls) == 1
    assert result["status"] == "stopped" and result["reason"] == reason
    assert TOKEN not in json.dumps(result)
    assert not result["pages"]
    assert TOKEN not in Path(result["receipt_path"]).read_text()


def test_network_exception_is_redacted(workspace):
    def handle(request):
        raise httpx.ReadError("request contained " + TOKEN, request=request)

    result = execute(workspace, handle)
    assert result["reason"] == "network_error"
    assert TOKEN not in json.dumps(result)


def test_success_body_echoing_token_is_discarded_before_persistence(workspace):
    result = execute(workspace, lambda request: httpx.Response(
        200, json={**page([]), "metadata": {"authorization": TOKEN}}
    ))
    assert result["reason"] == "credential_echo"
    assert not result["pages"]
    assert all(TOKEN not in p.read_text() for p in (workspace / "data/references/gfw").rglob("*.json"))


@pytest.mark.parametrize("payload", [
    {"entries": [], "total": 1, "nextOffset": 0},
    {"entries": [event("a")], "total": 2, "nextOffset": 0},
    {"entries": [event("a")], "total": 2, "nextOffset": 2},
    {"entries": [event("a"), event("a")], "total": 2, "nextOffset": 2},
    {"entries": [{"id": "a", "type": "fishing"}], "total": 1, "nextOffset": 1},
    {"entries": [], "total": True, "nextOffset": 0},
])
def test_invalid_pagination_stops_after_one_request(workspace, payload):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    result = execute(workspace, handle)
    assert result["reason"] == "invalid_pagination"
    assert len(calls) == 1 and not result["pages"]


def test_page_budget_is_global_and_reports_partial_coverage(workspace):
    calls = []

    def handle(request):
        calls.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=page([event(str(offset))], total=10, next_offset=offset + 1))

    result = execute(workspace, handle, page_size=1, max_pages=2)
    assert result["status"] == "stopped" and result["reason"] == "page_budget"
    assert len(calls) == len(result["pages"]) == 2
    assert not result["completed_ports"]


def test_byte_budget_discards_oversized_response(workspace):
    result = execute(workspace, lambda request: httpx.Response(200, content=b"x" * 1025),
                     max_page_bytes=1024)
    assert result["reason"] == "byte_budget" and not result["pages"]


def test_event_budget_prevents_another_request(workspace):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=page([event("a")]))

    result = execute(workspace, handle, max_events=1)
    assert result["reason"] == "event_budget"
    assert len(calls) == 1 and result["completed_ports"] == ["prq"]
    assert calls[0].url.params["limit"] == "1"


def test_duplicate_ids_across_pages_stop_before_saving_second_page(workspace):
    result = execute(workspace, lambda request: httpx.Response(
        200, json=page([event("same")], total=2,
                       next_offset=int(request.url.params["offset"]) + 1)
    ), page_size=1)
    assert result["reason"] == "invalid_pagination"
    assert len(result["pages"]) == 1


def test_slow_small_chunks_respect_time_budget_without_waiting_for_full_buffer(workspace, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("puertogt.gfw_reference.time.monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(3):
                clock[0] += .6
                yield b" "

    result = execute(workspace, lambda request: httpx.Response(200, stream=SlowStream()),
                     max_seconds=1)
    assert result["reason"] == "time_budget"
    assert clock[0] == 1.2 and not result["pages"]
