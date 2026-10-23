import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

import pytest
from shapely.geometry import box, mapping

from puertogt.contracts import Scene
from puertogt.history import HistoryLedger, HistoryLimits, _atomic_json
from puertogt.store import Store, sha256_file


def source_failure(tmp_path):
    product = "S2B_MSIL1C_20250826T161829_N0511_R040_T16PCC_20250826T200224.SAFE"
    key = "S2B:MSIL1C:20250826T161829:R040:T16PCC"
    scene = Scene(scene_id="source-scene", acquisition_key=key, sensor="sentinel-2",
                  product_level="L1C", captured_at=datetime(2025, 8, 26, 16, 18, 29, tzinfo=timezone.utc),
                  footprint=mapping(box(-88.7, 15.6, -88.5, 15.8)),
                  metadata={"representations": [{"name": product}]})
    item = {"scene": scene.model_dump(mode="json"), "zones": [], "tiles": []}
    plan = {"plan_id": "frozen-plan", "protected_observation_ids": [], "acquisitions": [item]}
    ledger = HistoryLedger(tmp_path / "history", HistoryLimits(reserve_bytes=0))
    ledger.initialize_plan(plan)
    _atomic_json(ledger.directory / "plan.json", plan)
    ledger.claim(acquisition_key=key)
    ledger.reserve_download(key, 32)
    ledger.fail(key, FileNotFoundError("GCS has no complete L1C nine-band representation"), permanent=True)
    (ledger.directory / "PAUSE").write_text("Paused at the acquisition boundary.\n")
    candidate = {"schema_version": "historical-compatible-source-candidate-v1",
                 "acquisition_key": key, "exact_product": product, "processing_baseline": "05.11",
                 "provider": "Official public mirror", "source_documentation": "https://registry.opendata.aws/sentinel-2/",
                 "sources": {band: {"url": f"https://sentinel-s2-l1c.s3.eu-central-1.amazonaws.com/tiles/16/P/CC/2025/8/26/0/{band}.jp2",
                                    "size": 64, "etag": '"unchanged"', "format": "jp2"}
                             for band in ["B04", "B03", "B02", "B05", "B06", "B07", "B08", "B11", "B12"]}}
    path = tmp_path / "candidate.json"
    _atomic_json(path, candidate)
    catalog = Store(tmp_path / "catalog.sqlite", tmp_path / "data")
    return catalog, ledger, path, key


def recover(catalog, ledger, candidate):
    from puertogt.history_recovery import recover_compatible_source

    return recover_compatible_source(catalog, ledger, candidate,
                                     candidate_sha256=sha256_file(candidate),
                                     plan_sha256=sha256_file(ledger.directory / "plan.json"),
                                     reason="Exact SAFE and nine pinned numeric bands independently checked")


def test_source_recovery_preserves_plan_attempts_budget_and_records_original_failure(tmp_path):
    catalog, ledger, candidate, key = source_failure(tmp_path)
    plan_hash = sha256_file(ledger.directory / "plan.json")
    result = recover(catalog, ledger, candidate)
    assert result["status"] == "complete"
    assert result["ledger_before"]["status"] == "unavailable"
    assert result["ledger_before"]["error_code"] == "FileNotFoundError"
    with closing(ledger.store.connect()) as tx:
        row = tx.execute("SELECT status,attempts,bytes_reserved FROM history_acquisitions").fetchone()
    assert tuple(row) == ("retry", 1, 32)
    assert sha256_file(ledger.directory / "plan.json") == plan_hash
    sources = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24] / "sources.json"
    assert json.loads(sources.read_text())["sources"] == json.loads(candidate.read_text())["sources"]
    assert recover(catalog, ledger, candidate) == result


@pytest.mark.parametrize("phase", ["sources_pinned", "complete"])
def test_source_recovery_replays_after_interruption_without_duplicate_transition(tmp_path, monkeypatch, phase):
    import puertogt.history_recovery as module

    catalog, ledger, candidate, _ = source_failure(tmp_path)
    original = module._atomic_json

    def interrupt(path, value):
        if value.get("status") == phase:
            raise OSError("Simulated recovery interruption")
        original(path, value)

    monkeypatch.setattr(module, "_atomic_json", interrupt)
    with pytest.raises(OSError, match="Simulated"):
        recover(catalog, ledger, candidate)
    monkeypatch.setattr(module, "_atomic_json", original)
    result = recover(catalog, ledger, candidate)
    assert result["status"] == "complete"
    assert ledger.summary()["states"] == {"retry": 1}
    assert ledger.summary()["download_bytes_reserved"] == 32


@pytest.mark.parametrize("change", ["no_pause", "different_product", "missing_band", "different_sources", "bad_etag"])
def test_source_recovery_refuses_unreviewed_substitutions(tmp_path, change):
    catalog, ledger, candidate, key = source_failure(tmp_path)
    data = json.loads(candidate.read_text())
    if change == "no_pause":
        (ledger.directory / "PAUSE").unlink()
    elif change == "different_product":
        data["exact_product"] = data["exact_product"].replace("N0511", "N9999")
    elif change == "missing_band":
        del data["sources"]["B12"]
    elif change == "bad_etag":
        data["sources"]["B12"]["etag"] = ""
    else:
        source = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24] / "sources.json"
        _atomic_json(source, {"sources": "preexisting different pins"})
    _atomic_json(candidate, data)
    with pytest.raises(ValueError):
        recover(catalog, ledger, candidate)
    assert ledger.summary()["states"] == {"unavailable": 1}
    assert not list((ledger.directory / "recoveries").glob("*.json"))
