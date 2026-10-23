import hashlib
import json

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio import Affine
from shapely.geometry import box, mapping
from shapely.ops import transform

from puertogt.contracts import ModelManifest, RasterBundle
from puertogt.ml.workflows import (
    history_detection_report,
    history_evaluation_inputs,
    select_history_threshold,
    validate_history_evidence,
)
from puertogt.preprocess import S2_BANDS
from puertogt.store import json_text, sha256_file

KEY = "S2A:MSIL1C:20260403T161829:R040:T15PYR"
PASS = "S2A:20260403T161829:R040"


def model():
    return ModelManifest(
        model_id="fixture", version="1", sensor="sentinel-2", task="detection",
        recipe_version="ai2-s2-v1", bands=S2_BANDS,
        units={b: "DN_harmonized" for b in S2_BANDS},
        publication_policy={"score_threshold": 0.1},
    )


def frozen_history(root, *, split="test"):
    root.mkdir()
    entries = []
    for i in range(2):
        west = 500000 + i * 4480
        tile = {
            "tile_id": f"fixture-{i}", "crs": "EPSG:32615", "size": 512,
            "margin": 32, "pixel_size": 10,
            "core_bounds": [west, 1500000, west + 4480, 1504480],
            "transform": [10, 0, west - 320, 0, -10, 1504800],
            "zone_ids": ["prq_canal", "prq_fondeo"],
        }
        paths = {}
        for name, values in [("bands", np.full((9, 512, 512), 300 + 300 * i, "uint16")),
                             ("observable", np.ones((1, 512, 512), "uint8"))]:
            path = root / f"tile-{i}-{name}.tif"
            with rasterio.open(path, "w", driver="GTiff", height=512, width=512,
                               count=len(values), dtype=values.dtype, crs=tile["crs"],
                               transform=Affine(*tile["transform"]), compress="deflate") as dst:
                dst.write(values)
            paths[name] = path.name
        bundle = RasterBundle(
            band_paths={b: paths["bands"] for b in S2_BANDS},
            mask_paths={"observable": paths["observable"], "valid": paths["observable"]},
            crs=tile["crs"], transform=tile["transform"], units=model().units,
            preprocessing_version="ai2-s2-v1", sensor="sentinel-2", product_level="L1C",
            metadata={"acquisition_key": KEY, "band_indices": {b: n + 1 for n, b in enumerate(S2_BANDS)}},
        )
        entries.append({"tile": tile, "bundle": bundle.model_dump(mode="json"),
                        "file_sha256": {p: sha256_file(root / p) for p in paths.values()}})
    forward = Transformer.from_crs(32615, 4326, always_xy=True)
    area = mapping(transform(forward.transform, box(500000, 1500000, 508960, 1504480)))
    features = []
    for n, x in enumerate([504470, 505160]):
        features.append({"type": "Feature", "geometry": mapping(transform(
            forward.transform, box(x - 50, 1502000, x + 50, 1502100))),
            "properties": {"provenance": {"unique_physical_object_key": f"{KEY}|{n}"}}})
    labels = root / "labels.geojson"
    labels.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    samples = [{"observation_id": f"obs-{zone}", "acquisition_key": KEY, "pass_id": PASS,
                "captured_at": "2026-04-03T16:18:29Z", "port_id": "prq", "zone_id": zone,
                "sensor": "sentinel-2", "reviewed_area": area, "labels": labels.name,
                "sha256": sha256_file(labels), "split_groups": [KEY, PASS],
                "reference_provenance": {"kind": "ai_visual_review", "author": "fixture"}}
               for zone in ["prq_canal", "prq_fondeo"]]
    dataset = {"version": "test-fixture", "split": split, "samples": samples,
               "history_inference": {"version": "history-tiles-v1", "acquisitions": [{
                   "acquisition_key": KEY, "pass_id": PASS, "context_acquisition_key": None,
                   "plan_tiles_sha256": hashlib.sha256(json_text([e["tile"] for e in entries]).encode()).hexdigest(),
                   "provenance": {"kind": "retained_original_prepared_bytes", "original_halo_hash_available": True},
                   "expected_tile_ids": [e["tile"]["tile_id"] for e in entries], "tiles": entries}]}}
    (root / "manifest.json").write_text(json.dumps(dataset))
    return dataset


def test_original_halo_checksum_is_required_before_inference(tmp_path):
    root = tmp_path / "frozen"
    dataset = frozen_history(root)
    with rasterio.open(root / "tile-0-bands.tif", "r+") as dst:
        values = dst.read(1)
        values[0, 0] = 999  # Outside the core; losing the original halo is still a mismatch.
        dst.write(values, 1)
    with pytest.raises(ValueError, match="checksum"):
        validate_history_evidence(root, dataset, model())


def test_history_evaluation_preserves_halos_core_ownership_and_cross_tile_merge(tmp_path):
    root = tmp_path / "frozen"
    frozen_history(root)
    before = {p.name: sha256_file(p) for p in root.iterdir()}

    class NumericDetector:
        manifest = model()
        calls = 0

        def raw_predict(self, image):
            assert image.shape == (9, 512, 512)
            # The original halo distinguishes the tiles; a core-only mosaic loses it.
            marker = round(float(image[0, 0, 0]), 1)
            self.calls += 1
            boxes = ([[474, 270, 484, 280], [5, 270, 15, 280]] if marker == 0.1
                     else [[28, 270, 38, 280], [95, 270, 105, 280]])
            return {"boxes": np.asarray(boxes, dtype=float), "scores": np.asarray([.9, .7]),
                    "labels": np.asarray([1, 1])}

    detector = NumericDetector()
    _, records = history_evaluation_inputs(root, model(), required_split="test",
                                           cache_dir=tmp_path / "scratch", detector=detector)
    assert [len(r["predictions"]) for r in records] == [2, 2]
    assert detector.calls == 2  # Whole acquisition once, shared by both overlapping zones.
    assert [p["metadata"]["history_tile_id"] for p in records[0]["predictions"]] == [
        "fixture-0", "fixture-1"]
    assert before == {p.name: sha256_file(p) for p in root.iterdir()}
    assert len(list((tmp_path / "scratch").rglob("predictions.json"))) == 2


def test_incomplete_original_tile_set_is_rejected(tmp_path):
    root = tmp_path / "frozen"
    dataset = frozen_history(root)
    dataset["history_inference"]["acquisitions"][0]["tiles"].pop()
    with pytest.raises(ValueError, match="tile set"):
        validate_history_evidence(root, dataset, model())


def test_train_diagnostic_preserves_history_path_without_claiming_independence(tmp_path):
    from puertogt.ml.workflows import history_training_diagnostic_inputs

    root = tmp_path / "train"
    frozen_history(root, split="train")
    before = {p.name: sha256_file(p) for p in root.iterdir()}
    trained = model().model_copy(update={"metadata": {"training": {
        "split_groups": [KEY, PASS], "observation_ids": ["obs-prq_canal"],
    }}})

    class NumericDetector:
        manifest = trained
        calls = 0

        def raw_predict(self, image):
            assert image.shape == (9, 512, 512)
            self.calls += 1
            marker = round(float(image[0, 0, 0]), 1)
            boxes = ([[474, 270, 484, 280], [5, 270, 15, 280]] if marker == .1
                     else [[28, 270, 38, 280], [95, 270, 105, 280]])
            return {"boxes": np.asarray(boxes, dtype=float), "scores": np.asarray([.9, .7]),
                    "labels": np.asarray([1, 1])}

    detector = NumericDetector()
    _, records = history_training_diagnostic_inputs(root, trained,
                                                     cache_dir=tmp_path / "scratch", detector=detector)
    assert detector.calls == 2
    assert [len(record["predictions"]) for record in records] == [2, 2]
    assert all(record["inference_purpose"] == "training_fit_only" for record in records)
    assert before == {p.name: sha256_file(p) for p in root.iterdir()}
    with pytest.raises(ValueError, match="calibration or test"):
        history_evaluation_inputs(root, trained, required_split="train",
                                  cache_dir=tmp_path / "forbidden", detector=detector)


def scoring_records():
    records = []
    for port in ["prq", "stc", "pbr"]:
        references = [{"geometry": mapping(box(i * .01, 0, i * .01 + .001, .001)),
                       "properties": {"provenance": {"unique_physical_object_key": str(i)}}}
                      for i in range(5)]
        predictions = [{"prediction_id": str(i), "geometry": r["geometry"],
                        "detection_score": .8 if i < 4 else .3}
                       for i, r in enumerate(references)]
        predictions.append({"prediction_id": "false", "geometry": mapping(box(.8, 0, .801, .001)),
                            "detection_score": .4})
        for zone in ["canal", "fondeo"]:
            records.append({"sample": {"port_id": port, "zone_id": f"{port}_{zone}",
                                        "sensor": "sentinel-2", "pass_id": "pass-1",
                                        "acquisition_key": "one"},
                            "predictions": predictions, "references": references})
    return records


def test_calibration_counts_overlap_once_and_obeys_all_port_recall_gates():
    records = scoring_records()
    # At .41 exactly four of five references remain; false score .40 is removed.
    selected = select_history_threshold(records, min_support=4)
    assert selected["selected"]["threshold"] == .41
    assert selected["selected"]["ports"]["prq"]["reference_count"] == 5
    assert selected["selected"]["ports"]["prq"]["prediction_count"] == 4
    assert selected["selected"]["ports"]["prq"]["recall"] == .8
    # A strong pooled result may not hide a weak port.
    for r in records:
        if r["sample"]["port_id"] == "pbr":
            r["predictions"] = r["predictions"][:3]
    assert select_history_threshold(records, min_support=3)["selected"] is None


def test_perfect_training_fit_never_qualifies_as_validation():
    from puertogt.ml.workflows import history_training_fit_report

    records = scoring_records()
    for record in records:
        record["inference_purpose"] = "training_fit_only"
        record["predictions"] = [{"prediction_id": str(i), "geometry": ref["geometry"],
                                  "detection_score": .9}
                                 for i, ref in enumerate(record["references"])]
    result = history_training_fit_report(records, threshold=.8)
    assert result["overall"]["true_positives"] == 15
    assert result["overall"]["false_positives"] == result["overall"]["false_negatives"] == 0
    assert result["eligible"] is False
    assert all(not port["eligible"] for port in result["ports"].values())
    assert result["status"] == "training_diagnostic_not_validation"


@pytest.mark.parametrize("split", ["calibration", "test"])
def test_train_diagnostic_rejects_reserved_splits_before_inference(tmp_path, split):
    from puertogt.ml.workflows import history_training_diagnostic_inputs

    root = tmp_path / split
    frozen_history(root, split=split)

    class ForbiddenDetector:
        def raw_predict(self, image):
            pytest.fail("Reserved split reached the TRAIN diagnostic detector")

    with pytest.raises(ValueError, match="train split"):
        history_training_diagnostic_inputs(root, model(), cache_dir=tmp_path / "cache",
                                            detector=ForbiddenDetector())
    assert not (tmp_path / "cache").exists()


def test_training_fit_report_rejects_unmarked_evaluation_records():
    from puertogt.ml.workflows import history_training_fit_report

    with pytest.raises(ValueError, match="training-diagnostic"):
        history_training_fit_report(scoring_records(), threshold=.8)


def test_repeated_zones_do_not_satisfy_independent_pass_support():
    result = history_detection_report(scoring_records(), threshold=.41, split="test",
                                      min_support=4, min_passes=10)
    assert result["ports"]["prq"]["pass_count"] == 1
    assert result["ports"]["prq"]["reference_count"] == 5
    assert "insufficient_passes" in result["ports"]["prq"]["reasons"]
    assert result["eligible"] is False
