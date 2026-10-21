"""Numeric preparation for the pinned Ai2 detector recipes."""

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import Window

from puertogt.assets import file_hash
from puertogt.contracts import ModelManifest, RasterBundle, Scene

S2_BANDS = ["B04", "B03", "B02", "B05", "B06", "B07", "B08", "B11", "B12"]
S1_BANDS = ["vv", "vh", "historical_vv", "historical_vh"]


def prepare(
    scene: Scene, zone_id: str, recipe_version: str, *, output_dir: Path | None = None
) -> RasterBundle:
    """Materialize checked local numeric assets on one georeferenced grid.

    ``scene.metadata`` must declare units for every band and (for raw L1C) the
    processing baseline. ``pixel_window`` optionally limits the first band grid.
    A caller must explicitly download remote assets with a measured byte budget;
    this function never starts a hidden full-scene download. Cloud and shadow
    masks are separate local assets with keys ``cloud_mask`` and ``shadow_mask``.
    Missing quality evidence remains unknown, rather than becoming clear sky.
    """
    bands = S2_BANDS if scene.sensor == "sentinel-2" else S1_BANDS
    expected_recipe = "ai2-s2-v1" if scene.sensor == "sentinel-2" else "ai2-s1-v1"
    expected_level = "L1C" if scene.sensor == "sentinel-2" else "GRDH"
    compatible_levels = {"L1C"} if scene.sensor == "sentinel-2" else {"GRDH", "IW_GRDH_1SDV"}
    if recipe_version != expected_recipe or scene.product_level not in compatible_levels:
        raise ValueError(f"requires recipe {expected_recipe} and product {expected_level}")
    missing = sorted(set(bands) - scene.source_assets.keys())
    if missing:
        raise ValueError(f"missing numerical bands: {', '.join(missing)}")
    units = scene.metadata.get("units", {})
    allowed_units = {"DN_raw", "DN_harmonized"} if scene.sensor == "sentinel-2" else {"amplitude_dn"}
    for band in bands:
        if units.get(band) not in allowed_units:
            raise ValueError(f"incompatible or unspecified units for {band}")
        if "://" in scene.source_assets[band]:
            raise ValueError("download remote assets explicitly with a byte budget before prepare")
    if any(units.get(band) == "DN_raw" for band in bands):
        try:
            baseline = float(scene.metadata["processing_baseline"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("raw L1C requires an explicit processing_baseline") from exc
    else:
        baseline = None

    digest = hashlib.sha256(
        f"{scene.acquisition_key}/{zone_id}/{recipe_version}".encode()
    ).hexdigest()[:24]
    destination = Path(output_dir or Path("data/rasters") / digest)
    destination.mkdir(parents=True, exist_ok=True)
    first = scene.source_assets[bands[0]]
    with rasterio.open(first) as reference:
        if reference.crs is None or reference.driver not in {"GTiff", "JP2OpenJPEG"}:
            raise ValueError("georeferenced numeric GeoTIFF/JP2 required")
        if "pixel_window" in scene.metadata:
            window = Window(*scene.metadata["pixel_window"])
            if window.col_off < 0 or window.row_off < 0 or (
                window.col_off + window.width > reference.width
                or window.row_off + window.height > reference.height
            ):
                raise ValueError("pixel_window lies outside the source raster")
        else:
            window = Window(0, 0, reference.width, reference.height)
        width, height = int(window.width), int(window.height)
        if width <= 0 or height <= 0 or max(width, height) > 4096:
            raise ValueError("prepare requires a crop of 1–4096 pixels per side")
        crs = reference.crs
        transform = reference.window_transform(window)
    profile = dict(driver="GTiff", width=width, height=height, count=1, crs=crs,
                   transform=transform, compress="deflate", nodata=0)

    def aligned(path: str, index: int = 1) -> np.ndarray:
        with rasterio.open(path) as src:
            if src.driver not in {"GTiff", "JP2OpenJPEG"} or src.crs is None:
                raise ValueError("georeferenced numeric GeoTIFF/JP2 required")
            result = np.zeros((height, width), dtype=np.float32)
            reproject(rasterio.band(src, index), result,
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=transform, dst_crs=crs, src_nodata=src.nodata,
                      dst_nodata=0, resampling=Resampling.nearest)
        if not np.isfinite(result).all() or (result < 0).any():
            raise ValueError("non-finite or negative DN values")
        return result

    def publish(name: str, data: np.ndarray) -> Path:
        path = destination / f"{name}.tif"
        with tempfile.NamedTemporaryFile(dir=destination, suffix=".part.tif", delete=False) as tmp:
            temporary = Path(tmp.name)
        try:
            with rasterio.open(temporary, "w", dtype=str(data.dtype), **profile) as dst:
                dst.write(data, 1)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    valid = np.ones((height, width), dtype=bool)
    band_paths = {}
    indices = scene.metadata.get("band_indices", {})
    for band in bands:
        data = aligned(scene.source_assets[band], int(indices.get(band, 1)))
        valid &= data > 0
        if units[band] == "DN_raw" and baseline >= 4:
            was_valid = data > 0
            data = np.maximum(data - 1000, 0)
            # Exact pinned rslearn harmonization: preserve valid zero as DN 1.
            data[(data == 0) & was_valid] = 1
        band_paths[band] = publish(band, data.astype(np.float32))
    masks = {"valid": publish("valid", valid.astype(np.uint8))}
    observable = valid.copy()
    quality_flags = []
    for name in ("cloud", "shadow"):
        key = f"{name}_mask"
        if key in scene.source_assets:
            excluded = aligned(scene.source_assets[key]) > 0
            masks[name] = publish(name, excluded.astype(np.uint8))
            observable &= ~excluded
        elif scene.sensor == "sentinel-2":
            quality_flags.append(f"{key}_missing")
    masks["potentially_observable"] = publish("potentially_observable", observable.astype(np.uint8))
    if not quality_flags:
        masks["observable"] = publish("observable", observable.astype(np.uint8))
    output_unit = "DN_harmonized" if scene.sensor == "sentinel-2" else "amplitude_dn"
    bundle = RasterBundle(
        band_paths=band_paths, mask_paths=masks, crs=str(crs),
        transform=tuple(transform)[:6], units={band: output_unit for band in bands},
        preprocessing_version=recipe_version, sensor=scene.sensor, product_level=scene.product_level,
        metadata={**scene.metadata, "scene_id": scene.scene_id,
                  "acquisition_key": scene.acquisition_key, "zone_id": zone_id,
                  "captured_at": scene.captured_at.isoformat(), "orbit_direction": scene.orbit_direction,
                  "band_indices": {}, "quality_flags": quality_flags,
                  "valid_fraction": float(valid.mean()),
                  "potentially_observable_fraction": float(observable.mean()),
                  "observable_fraction": None if quality_flags else float(observable.mean()),
                  "sha256": {band: file_hash(path) for band, path in band_paths.items()}},
    )
    read_model_input(bundle)
    manifest = destination / "bundle.json"
    manifest.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2) + "\n")
    return bundle


def read_model_input(bundle: RasterBundle, model: ModelManifest | None = None) -> np.ndarray:
    """Return CHW float32 data in the exact pinned checkpoint order and scale.

    This accepts numeric GeoTIFF/JP2 only. Lower-resolution bands are resampled with
    nearest neighbour, matching the pinned rslearn training dataset loader. No
    percentile stretching, radiometric substitution or implicit temporal fill occurs.
    """
    bands = S2_BANDS if bundle.sensor == "sentinel-2" else S1_BANDS
    product_level = "L1C" if bundle.sensor == "sentinel-2" else "GRDH"
    compatible_levels = {"L1C"} if bundle.sensor == "sentinel-2" else {"GRDH", "IW_GRDH_1SDV"}
    if bundle.product_level not in compatible_levels:
        raise ValueError(f"{bundle.sensor} recipe requires {product_level}")
    expected_recipe = "ai2-s2-v1" if bundle.sensor == "sentinel-2" else "ai2-s1-v1"
    if bundle.preprocessing_version != expected_recipe:
        raise ValueError(f"unsupported preprocessing recipe: expected {expected_recipe}")
    if model is not None and (model.sensor != bundle.sensor or model.recipe_version != expected_recipe
                              or model.bands != bands):
        raise ValueError("model manifest does not match the numeric preprocessing recipe")
    missing = sorted(set(bands) - bundle.band_paths.keys())
    if missing:
        raise ValueError(f"missing numerical bands: {', '.join(missing)}")
    expected_unit = "DN_harmonized" if bundle.sensor == "sentinel-2" else "amplitude_dn"
    if model is not None and model.units != {band: expected_unit for band in bands}:
        raise ValueError("model manifest units do not match the numeric preprocessing recipe")
    for band in bands:
        if bundle.units.get(band) != expected_unit:
            raise ValueError(f"incompatible units for {band}: expected {expected_unit}")
    if bundle.sensor == "sentinel-1":
        orbit = bundle.metadata.get("orbit_direction")
        if orbit not in {"ASCENDING", "DESCENDING"} or orbit != bundle.metadata.get(
            "historical_orbit_direction"
        ):
            raise ValueError("target and historical orbit direction must be verified and equal")
        try:
            captured = datetime.fromisoformat(bundle.metadata["captured_at"].replace("Z", "+00:00"))
            historical = datetime.fromisoformat(
                bundle.metadata["historical_captured_at"].replace("Z", "+00:00")
            )
        except (KeyError, ValueError, AttributeError) as exc:
            raise ValueError("target and historical acquisition dates are required") from exc
        if captured.tzinfo is None or historical.tzinfo is None:
            raise ValueError("acquisition dates require timezones")
        days = (captured - historical).total_seconds() / 86400
        if not 30 <= days <= 90:
            raise ValueError("historical input must be 30–90 days before target for this recipe")
    arrays = []
    reference_shape = None
    transform = Affine(*bundle.transform)
    band_indices = bundle.metadata.get("band_indices", {})
    for band in bands:
        path = Path(bundle.band_paths[band])
        with rasterio.open(path) as src:
            if src.driver not in {"GTiff", "JP2OpenJPEG"}:
                raise ValueError(f"{band}: numeric GeoTIFF/JP2 required, not {src.driver}")
            if src.crs is None:
                raise ValueError(f"{band}: georeferencing is required")
            index = int(band_indices.get(band, 1))
            if reference_shape is None:
                reference_shape = src.shape
                if max(reference_shape) > 4096:
                    raise ValueError("model input requires a crop no larger than 4096 pixels per side")
                if str(src.crs) != bundle.crs or not src.transform.almost_equals(transform):
                    raise ValueError("bundle georeferencing does not match the reference band")
            if (src.shape != reference_shape or str(src.crs) != bundle.crs
                    or not src.transform.almost_equals(transform)):
                data = np.zeros(reference_shape, dtype=np.float32)
                reproject(
                    rasterio.band(src, index), data, src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=bundle.crs,
                    src_nodata=src.nodata, dst_nodata=0, resampling=Resampling.nearest,
                )
            else:
                data = src.read(index).astype(np.float32)
            if not np.isfinite(data).all() or (data < 0).any():
                raise ValueError(f"{band}: non-finite or negative DN values")
            arrays.append(data)
    image = np.stack(arrays)
    divisors = [3000] * 3 + [8160] * 6 if bundle.sensor == "sentinel-2" else [250] * 4
    maximum = 1 if bundle.sensor == "sentinel-2" else 4
    return np.clip(image / np.array(divisors, dtype=np.float32)[:, None, None], 0, maximum)


OMNICLOUDMASK_WEIGHTS = {
    "PM_model_OCM_7.97_R_G_NIR_3_smp_edgenext_small.usi_in1k_PT_state.safetensors":
        "d5fe67ad00f6fdb73eb8382ad925849e97fb82cedb46225344afff1a734a9c1d",
    "PM_model_OCM_7.97_R_G_NIR_3_smp_regnety_004.pycls_in1k_PT_state.safetensors":
        "7f6e4202e17ee73efa4aba7abb5c34f4f90a9f7eb42480820714994dff2db660",
}


def add_cloud_shadow_masks(
    bundle: RasterBundle, model_dir: Path, *, device: str = "cpu"
) -> RasterBundle:
    """Add independently pretrained L1C-compatible cloud/shadow predictions.

    Uses the two pinned OmniCloudMask v4 models. The input is Red/Green/NIR DN,
    never the display PNG. Predictions estimate observability; they do not certify
    the detector's local accuracy. Model files must already exist and match SHA256.
    """
    if bundle.sensor != "sentinel-2" or bundle.product_level != "L1C":
        raise ValueError("this quality adapter requires Sentinel-2 L1C")
    read_model_input(bundle)
    for name, expected_hash in OMNICLOUDMASK_WEIGHTS.items():
        if file_hash(Path(model_dir) / name) != expected_hash:
            raise ValueError(f"OmniCloudMask checkpoint SHA256 mismatch: {name}")
    from omnicloudmask import predict_from_array

    # Model-specific Z-score normalization is done by OmniCloudMask itself.
    arrays = []
    reference_profile = None
    for band in ("B04", "B03", "B08"):
        with rasterio.open(bundle.band_paths[band]) as src:
            if str(src.crs) != bundle.crs or not src.transform.almost_equals(Affine(*bundle.transform)):
                raise ValueError("quality bands must be prepared on the bundle grid")
            arrays.append(src.read(int(bundle.metadata.get("band_indices", {}).get(band, 1))).astype(np.float32))
            reference_profile = src.profile
    rgbnir = np.stack(arrays)
    if min(rgbnir.shape[-2:]) < 32:
        raise ValueError("cloud/shadow inference needs at least 32 pixels per side")
    prediction = predict_from_array(
        rgbnir, patch_size=512, patch_overlap=64, batch_size=1,
        inference_device=device, mosaic_device="cpu", model_version=4.0,
        destination_model_dir=Path(model_dir), compile_models=False,
    )[0]
    with rasterio.open(bundle.mask_paths["valid"]) as src:
        valid = src.read(1) > 0
    cloud = np.isin(prediction, [1, 2])
    shadow = prediction == 3
    observable = valid & ~cloud & ~shadow
    if reference_profile is None:
        raise ValueError("numeric input profile missing")
    profile = {**reference_profile, "driver": "GTiff", "count": 1, "dtype": "uint8", "nodata": None}
    destination = Path(bundle.band_paths["B04"]).parent
    paths = dict(bundle.mask_paths)
    for name, mask in {"cloud": cloud, "shadow": shadow, "observable": observable,
                       "potentially_observable": observable, "cloud_classes": prediction}.items():
        path = destination / f"{name}.tif"
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(mask.astype(np.uint8), 1)
        paths[name] = path
    metadata = {**bundle.metadata,
                "quality_flags": [f for f in bundle.metadata.get("quality_flags", [])
                                  if f not in {"cloud_mask_missing", "shadow_mask_missing"}],
                "observable_fraction": float(observable.mean()),
                "potentially_observable_fraction": float(observable.mean()),
                "cloud_fraction": float((cloud & valid).mean()),
                "shadow_fraction": float((shadow & valid).mean()),
                "quality_model": {"name": "OmniCloudMask", "model_version": 4,
                                  "code_commit": "d7ca21c686ab2ea63fd0f7e6a662a3179169f7e6",
                                  "weights_sha256": OMNICLOUDMASK_WEIGHTS,
                                  "weights_revision": "c9a4fb88188709127aa25cfe51ae7fd41b0132f8",
                                  "local_mask_review": "pending"}}
    result = bundle.model_copy(update={"mask_paths": paths, "metadata": metadata})
    (destination / "bundle.json").write_text(result.model_dump_json(indent=2) + "\n")
    return result
