from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from puertogt.contracts import RasterBundle, Scene
from puertogt.preprocess import prepare, read_model_input

S2_BANDS = ["B04", "B03", "B02", "B05", "B06", "B07", "B08", "B11", "B12"]


def make_bundle(tmp_path: Path, *, omit=None, unit="DN_harmonized"):
    transform = from_origin(500000, 1500000, 10, 10)
    paths = {}
    for band in S2_BANDS:
        if band == omit:
            continue
        path = tmp_path / f"{band}.tif"
        with rasterio.open(
            path, "w", driver="GTiff", height=2, width=2, count=1,
            dtype="uint16", crs="EPSG:32615", transform=transform,
        ) as dst:
            dst.write(np.array([[0, 1500], [3000, 9000]], dtype=np.uint16), 1)
        paths[band] = path
    return RasterBundle(
        band_paths=paths, crs="EPSG:32615", transform=tuple(transform)[:6],
        units={band: unit for band in paths}, preprocessing_version="ai2-s2-v1",
        sensor="sentinel-2", product_level="L1C",
    )


def test_missing_band_blocks_inference(tmp_path):
    bundle = make_bundle(tmp_path, omit="B08")
    with pytest.raises(ValueError, match="B08"):
        read_model_input(bundle)


def test_unknown_radiometry_is_not_silently_normalized(tmp_path):
    with pytest.raises(ValueError, match="units"):
        read_model_input(make_bundle(tmp_path, unit="reflectance"))


def test_numeric_input_matches_pinned_recipe_without_display_scaling(tmp_path):
    image = read_model_input(make_bundle(tmp_path))
    assert isinstance(image, np.ndarray)
    assert image.shape == (9, 2, 2)
    np.testing.assert_allclose(image[0], [[0, 0.5], [1, 1]])
    np.testing.assert_allclose(image[3], [[0, 0.1838235294], [0.3676470588, 1]])


def test_l2a_cannot_be_substituted_for_l1c(tmp_path):
    bundle = make_bundle(tmp_path)
    bundle.product_level = "L2A"
    with pytest.raises(ValueError, match="L1C"):
        read_model_input(bundle)


def test_radar_requires_verified_historical_orbit(tmp_path):
    source = make_bundle(tmp_path)
    keys = ["vv", "vh", "historical_vv", "historical_vh"]
    bundle = source.model_copy(update={
        "sensor": "sentinel-1", "product_level": "GRDH",
        "preprocessing_version": "ai2-s1-v1",
        "band_paths": {key: source.band_paths["B04"] for key in keys},
        "units": {key: "amplitude_dn" for key in keys},
        "metadata": {"orbit_direction": "ASCENDING", "historical_orbit_direction": "DESCENDING",
                     "captured_at": "2026-09-10T00:00:00Z",
                     "historical_captured_at": "2026-07-10T00:00:00Z"},
    })
    with pytest.raises(ValueError, match="orbit"):
        read_model_input(bundle)


def test_catalog_grdh_level_preserves_the_raw_radar_recipe(tmp_path):
    source = make_bundle(tmp_path)
    keys = ["vv", "vh", "historical_vv", "historical_vh"]
    bundle = source.model_copy(update={
        "sensor": "sentinel-1", "product_level": "IW_GRDH_1SDV",
        "preprocessing_version": "ai2-s1-v1",
        "band_paths": {key: source.band_paths["B04"] for key in keys},
        "units": {key: "amplitude_dn" for key in keys},
        "metadata": {"orbit_direction": "ASCENDING", "historical_orbit_direction": "ASCENDING",
                     "captured_at": "2026-09-10T00:00:00Z",
                     "historical_captured_at": "2026-07-10T00:00:00Z"},
    })
    np.testing.assert_array_equal(read_model_input(bundle)[0], [[0, 4], [4, 4]])


def test_prepare_harmonizes_new_baseline_and_keeps_nodata_distinct(tmp_path):
    source = make_bundle(tmp_path)
    scene = Scene(
        scene_id="sample-l1c", acquisition_key="sample-key", sensor="sentinel-2",
        product_level="L1C", captured_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        footprint={"type": "Polygon", "coordinates": []},
        source_assets={band: str(path) for band, path in source.band_paths.items()},
        metadata={"units": {band: "DN_raw" for band in S2_BANDS},
                  "processing_baseline": "05.12"},
    )
    result = prepare(scene, "prq_muelles", "ai2-s2-v1", output_dir=tmp_path / "prepared")
    assert isinstance(result, RasterBundle)
    with rasterio.open(result.band_paths["B04"]) as src:
        np.testing.assert_array_equal(src.read(1), [[0, 500], [2000, 8000]])
    assert result.metadata["observable_fraction"] is None
    assert "cloud_mask_missing" in result.metadata["quality_flags"]
