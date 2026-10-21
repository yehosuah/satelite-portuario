import json

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio import Affine
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from puertogt.ml.dataset_crops import crop_training_dataset
from puertogt.ml.training import validate_training_dataset
from puertogt.ml.workflows import labels_for_sample
from puertogt.preprocess import S2_BANDS, read_model_input
from puertogt.review import bundle_from_evidence
from puertogt.store import sha256_file


def dataset(tmp_path, *, split="train", width=600, height=256, grid=None):
    root = tmp_path / "source"
    root.mkdir()
    grid = grid or Affine(10, 0, 735000, 0, -10, 1540000)
    to_wgs = Transformer.from_crs(32615, 4326, always_xy=True)

    def geo(x1, y1, x2, y2):
        a, b = grid * (x1, y1), grid * (x2, y2)
        return transform(to_wgs.transform, box(a[0], b[1], b[0], a[1]))

    numbers = np.broadcast_to(np.arange(width, dtype="uint16"), (9, height, width)).copy()
    with rasterio.open(root / "bands.tif", "w", driver="GTiff", count=9,
                       width=width, height=height, dtype="uint16", crs="EPSG:32615",
                       transform=grid) as out:
        out.write(numbers)
    reference = {"type": "Feature", "geometry": mapping(geo(490, 80, 530, 100)),
                 "properties": {"id": "ship", "provenance": {"kind": "ai_visual_review"}}}
    (root / "labels.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [reference]}))
    bundle = {"bands": S2_BANDS, "masks": [], "units": {x: "DN_harmonized" for x in S2_BANDS},
              "transform": list(grid)[:6], "sensor": "sentinel-2", "product_level": "L1C",
              "preprocessing_version": "ai2-s2-v1", "metadata": {},
              "band_indices": {name: i + 1 for i, name in enumerate(S2_BANDS)}}
    sample = {"observation_id": "parent", "acquisition_key": "capture", "sensor": "sentinel-2",
              "captured_at": "2025-01-03T00:00:00Z", "port_id": "prq", "zone_id": "prq_fondeo",
              "split_groups": ["capture", "temporal-pair"], "reviewed_area": mapping(geo(0, 0, width, height)),
              "reference_provenance": {"kind": "ai_visual_review", "author": "reviewer"},
              "labels": "labels.geojson", "sha256": sha256_file(root / "labels.geojson"),
              "evidence_assets": [{"path": "bands.tif", "asset_id": "numeric",
                                   "sha256": sha256_file(root / "bands.tif"), "mime_type": "image/tiff",
                                   "metadata": {"crs": "EPSG:32615", "bundle": bundle}}]}
    (root / "manifest.json").write_text(json.dumps({"version": "source-v1", "split": split, "samples": [sample]}))
    return root


def test_crops_preserve_pixels_partial_objects_provenance_and_split_groups(tmp_path):
    source = dataset(tmp_path)
    target = tmp_path / "tiles"
    manifest = crop_training_dataset(source, target, version="tiles-v1")
    assert len(manifest["samples"]) == 2
    assert manifest["parent_manifest_sha256"] == sha256_file(source / "manifest.json")
    assert {s["crop_window"][0] for s in manifest["samples"]} == {0, 88}
    for sample in manifest["samples"]:
        assert sample["parent_observation_id"] == "parent"
        assert {"capture", "temporal-pair", "observation:parent"} <= set(sample["split_groups"])
        assert sample["reference_provenance"]["kind"] == "ai_visual_review"
        bundle = bundle_from_evidence(sample, target)
        image = read_model_input(bundle)
        assert image.shape == (9, 256, 512)
        offset = sample["crop_window"][0]
        np.testing.assert_allclose(image[0, 0], np.arange(offset, offset + 512) / 3000)
        labels = json.loads((target / sample["labels"]).read_text())["features"]
        assert len(labels) == 1  # The visible truncated hull is still a positive.
        assert shape(sample["reviewed_area"]).buffer(1e-9).covers(shape(labels[0]["geometry"]).centroid)
    assert validate_training_dataset(target)["samples"]
    reserved = tmp_path / "reserved.json"
    reserved.write_text(json.dumps({"split": "test", "samples": [{"observation_id": "different", "split_groups": ["temporal-pair"]}]}))
    with pytest.raises(ValueError, match="related"):
        validate_training_dataset(target, reserved_manifests=[reserved])
    with pytest.raises(FileExistsError):
        crop_training_dataset(source, target, version="overwrite")


def test_crop_export_rejects_tampered_numeric_evidence_atomically(tmp_path):
    source = dataset(tmp_path)
    with (source / "bands.tif").open("ab") as handle:
        handle.write(b"changed")
    target = tmp_path / "tiles"
    with pytest.raises(ValueError, match="checksum"):
        crop_training_dataset(source, target, version="v1")
    assert not target.exists()


def test_crop_export_does_not_repartition_test_as_train(tmp_path):
    source = dataset(tmp_path, split="test")
    with pytest.raises(ValueError, match="train"):
        crop_training_dataset(source, tmp_path / "tiles", version="v1")


def test_partial_scope_round_trip_preserves_area_without_boundary_lines(tmp_path):
    # Real partial-swath boundary from PBR Jan 1: the UTM/WGS84 round trip
    # intersects the original polygon in an area plus three zero-area lines.
    source = dataset(tmp_path, width=448, height=896,
                     grid=Affine(10, 0, 327040, 0, -10, 1747200))
    with rasterio.open(source / "bands.tif", "r+") as raster:
        raster.crs = "EPSG:32616"
    scope = {"type": "Polygon", "coordinates": [[
        [-88.60154202766304, 15.788200571236395], [-88.6015561676544, 15.79],
        [-88.601, 15.79], [-88.60412348750867, 15.76594914618323],
        [-88.60409999083265, 15.766130070588636], [-88.60435518917471, 15.766128190127555],
        [-88.60435589937441, 15.766218560556077], [-88.60482250291373, 15.766215121310058],
        [-88.60482321332437, 15.766305491717091], [-88.60528981695509, 15.766302051451243],
        [-88.60529052757666, 15.766392421836784], [-88.60575713129876, 15.766388980551088],
        [-88.60575784213125, 15.766479350915132], [-88.60622444594469, 15.766475908609582],
        [-88.60622515698813, 15.766566278952114], [-88.60654364562984, 15.76656392865023],
        [-88.60373579291478, 15.78818439455623], [-88.60154202766304, 15.788200571236395],
    ]]}
    (source / "labels.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    manifest = json.loads((source / "manifest.json").read_text())
    sample = manifest["samples"][0]
    sample.update(reviewed_area=scope, sha256=sha256_file(source / "labels.geojson"))
    sample["evidence_assets"][0]["sha256"] = sha256_file(source / "bands.tif")
    sample["evidence_assets"][0]["metadata"]["crs"] = "EPSG:32616"
    (source / "manifest.json").write_text(json.dumps(manifest))
    source_hash = sha256_file(source / "manifest.json")
    result = crop_training_dataset(source, tmp_path / "tiles", version="partial-v1")
    assert result["samples"]
    for derived in result["samples"]:
        area = shape(derived["reviewed_area"])
        assert area.geom_type in {"Polygon", "MultiPolygon"}
        assert area.area > 0 and derived["reviewed_pixel_count"] > 0
        assert area.difference(shape(scope)).area < 1e-15
    assert sha256_file(source / "manifest.json") == source_hash


def test_touching_positive_does_not_publish_a_roundoff_sliver_label(tmp_path):
    # Real PRQ Jan 18 label whose western edge is exactly crop column512.
    # Projection yields a 4.19e-8 m² sliver in the left crop which collapses
    # to a zero-area, invalid WGS84 triangle on the return transform.
    source = dataset(tmp_path, width=896, height=896,
                     grid=Affine(10, 0, 734720, 0, -10, 1541120))
    geometry = {"type": "Polygon", "coordinates": [[
        [-90.77812849860962, 13.87925677744177], [-90.77814399067857, 13.877630432680752],
        [-90.78073348178607, 13.877653961409393], [-90.7807180077364, 13.879280309034508],
        [-90.77812849860962, 13.87925677744177],
    ]]}
    (source / "labels.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": geometry, "properties": {"id": "edge-hull"}}]}))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["samples"][0]["sha256"] = sha256_file(source / "labels.geojson")
    (source / "manifest.json").write_text(json.dumps(manifest))
    before = sha256_file(source / "manifest.json")
    target = tmp_path / "tiles"
    result = crop_training_dataset(source, target, version="edge-v1")
    positive_crops = []
    for sample in result["samples"]:
        labels = labels_for_sample(target, sample)
        if sample["crop_window"][:2] == [0, 384]:
            assert labels == []
        if labels:
            positive_crops.append(sample["crop_window"][:2])
    assert positive_crops == [[384, 384]]
    assert sha256_file(source / "manifest.json") == before
