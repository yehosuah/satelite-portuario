import copy
import json

import numpy as np
import pytest
import rasterio
from rasterio import Affine

from puertogt.contracts import Scene
from puertogt.ml.public_attributes import (
    _working_path,
    build_public_item,
    finalize_public_attributes,
    load_selection,
    materialize_public_attributes,
)
from puertogt.preprocess import S2_BANDS, prepare
from puertogt.review import bundle_from_evidence
from puertogt.store import sha256_file


def public_sample(window_id=1, split="train"):
    name = "S2B_MSIL1C_20211023T042829_N0301_R133_T45QYE_20211023T063920.SAFE"
    source_split = {"train": "train", "calibration": "val", "test": "test"}[split]
    source_values = {"family": 70, "length_m": 100, "width_m": 20}
    return {
        "window_id": window_id, "image_id": 10, "label_id": 20+window_id,
        "split": "sentinel2-attrs-"+source_split, "import_split": split,
        "column": 3140772, "row": 1830839, "width": 128, "height": 128,
        "projection": "epsg:3857", "zoom": 13, "name": name,
        "pass_key": "S2B:20211023T042829:R133",
        "split_groups": ["public-attribute-pass:S2B:20211023T042829:R133"],
        "mmsi": None,
        "attributes": {"family": "carga", "length_m": 100, "width_m": 20},
        "attribute_provenance": {field: {"kind": "public_dataset", "reference": "https://example.test/pinned",
                                            "metadata_sha256": "a"*64, "source_value": value}
                                 for field, value in source_values.items()},
        "raster_pin": {"complete_exact_original": True, "requested_representation": name,
                       "bands": {band: {"name": "tiles/45/Q/YE/"+name+"/GRANULE/source/IMG_DATA/image_"+band+".jp2",
                                         "generation": "123456", "size": "1000"} for band in S2_BANDS}},
    }


def write_selection(tmp_path, samples):
    path = tmp_path / "selection.json"
    path.write_text(json.dumps({"schema_version": "public-attributes-selection-v1",
                               "metadata_source": {"sha256": "a"*64}, "samples": samples}))
    return path


def test_selection_rejects_changed_hash_and_shared_pass_across_splits(tmp_path):
    path = write_selection(tmp_path, [public_sample()])
    assert len(load_selection(path, sha256_file(path))["samples"]) == 1
    with pytest.raises(ValueError, match="checksum"):
        load_selection(path, "b"*64)
    path = write_selection(tmp_path, [public_sample(), public_sample(2, "test")])
    with pytest.raises(ValueError, match="pass.*split"):
        load_selection(path, sha256_file(path))


def test_selection_rejects_missing_or_substituted_numeric_band(tmp_path):
    sample = public_sample()
    sample["raster_pin"]["bands"].pop("B12")
    path = write_selection(tmp_path, [sample])
    with pytest.raises(ValueError, match="nine"):
        load_selection(path, sha256_file(path))
    sample = public_sample()
    sample["raster_pin"]["bands"]["B12"]["name"] = "tiles/different.SAFE/image_B12.jp2"
    path = write_selection(tmp_path, [sample])
    with pytest.raises(ValueError, match="original"):
        load_selection(path, sha256_file(path))


def test_context_geometry_never_uses_target_dimensions():
    sample = public_sample()
    other = copy.deepcopy(sample)
    other["attributes"].update(length_m=400, width_m=80)
    item, discovery, reference = build_public_item(sample, "c"*64)
    changed_item, _, changed_reference = build_public_item(other, "c"*64)
    assert item["tiles"] == changed_item["tiles"]
    assert reference == changed_reference
    assert item["tiles"][0]["size"] == 128
    assert all("generation=123456" in value["url"] for value in discovery["sources"].values())
    assert discovery["processing_baseline"] == "03.01"


def test_import_resume_and_portable_export_keep_context_and_field_provenance(tmp_path, monkeypatch):
    selection = write_selection(tmp_path, [public_sample()])
    expected = sha256_file(selection)
    calls = []

    def materialize(item, ledger, **_):
        calls.append(item["scene"]["acquisition_key"])
        work = _working_path(ledger.directory, item)
        tile = item["tiles"][0]
        raw = work / "raw"
        raw.mkdir()
        paths = {}
        for band in S2_BANDS:
            path = raw / (band+".tif")
            with rasterio.open(path, "w", driver="GTiff", width=128, height=128, count=1,
                               dtype="uint16", crs=tile["crs"], transform=Affine(*tile["transform"])) as raster:
                raster.write(np.ones((128, 128), dtype=np.uint16)*1000, 1)
            paths[band] = str(path)
        scene = Scene.model_validate(item["scene"])
        scene.source_assets = paths
        scene.metadata.update(units={band: "DN_raw" for band in S2_BANDS}, processing_baseline="03.01")
        prepared = work / tile["tile_id"] / "prepared"
        numeric = prepare(scene, tile["tile_id"], "ai2-s2-v1", output_dir=prepared)
        numeric.metadata["history_file_hashes"] = {name: sha256_file(path) for name, path in
                                                   {**numeric.band_paths, **numeric.mask_paths}.items()}
        (prepared / "bundle.json").write_text(numeric.model_dump_json())
        return [(tile, numeric)], {"downloaded_bytes": 0}

    monkeypatch.setattr("puertogt.ml.public_attributes.materialize", materialize)
    destination = tmp_path / "import"
    result = materialize_public_attributes(selection, expected, destination)
    assert result["states"] == {"complete": 1}
    materialize_public_attributes(selection, expected, destination)
    assert len(calls) == 1
    datasets = finalize_public_attributes(selection, expected, destination)
    manifest = json.loads((datasets / "train/manifest.json").read_text())
    sample = manifest["samples"][0]
    numeric = bundle_from_evidence(sample, datasets / "train")
    assert set(numeric.band_paths) == set(S2_BANDS)
    labels = json.loads((datasets / "train" / sample["labels"]).read_text())
    properties = labels["features"][0]["properties"]
    assert properties["length_m"] == 100
    assert properties["provenance"]["length_m"]["kind"] == "public_dataset"
    assert properties["geometry_role"] == "attribute_context_roi_not_vessel_extent"
    assert properties["mmsi"] is None
    with pytest.raises(FileExistsError, match="immutable"):
        finalize_public_attributes(selection, expected, destination)


def test_io_amendment_changes_only_concurrency_and_preserves_attempts_and_budget(tmp_path, monkeypatch):
    from dataclasses import asdict

    from puertogt.history import HistoryLedger
    from puertogt.ml.public_attributes import PUBLIC_LIMITS

    selection = write_selection(tmp_path, [public_sample()])
    digest = sha256_file(selection)
    root = tmp_path / "import"
    ledger = HistoryLedger(root, PUBLIC_LIMITS)
    item, _, _ = build_public_item(public_sample(), digest)
    ledger.initialize_plan({"acquisitions": [item]})
    ledger.claim()
    ledger.reserve_download(item["scene"]["acquisition_key"], 16)
    before = asdict(PUBLIC_LIMITS)
    amendment = tmp_path / "amendment.json"
    amendment.write_text(json.dumps({"schema_version": "public-attributes-performance-amendment-v1",
        "selection_sha256": digest, "limits_before": before, "limits_after": {**before, "io_workers": 4},
        "training_started": False, "test_evaluation_started": False}))
    observed = []

    def materialize(item, active, **kwargs):
        observed.append(asdict(active.limits))
        return [], {"downloaded_bytes": 0}

    monkeypatch.setattr("puertogt.ml.public_attributes.materialize", materialize)
    result = materialize_public_attributes(selection, digest, root, io_workers=4,
        performance_amendment=amendment, amendment_sha256=sha256_file(amendment))
    assert observed == [{**before, "io_workers": 4}]
    assert result["download_bytes_reserved"] == 16
    assert result["states"] == {"complete": 1}
    with ledger.store.connect() as tx:
        assert tx.execute("SELECT attempts FROM history_acquisitions").fetchone()[0] == 2
    assert json.loads((root / "limits.json").read_text()) == {**before, "io_workers": 4}
    assert len(list((root / "performance-amendments").glob("*.json"))) == 1


def test_io_concurrency_change_requires_explicit_pinned_amendment(tmp_path):
    selection = write_selection(tmp_path, [public_sample()])
    with pytest.raises(ValueError, match="amendment"):
        materialize_public_attributes(selection, sha256_file(selection), tmp_path / "import", io_workers=4)
