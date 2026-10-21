"""Heavy work with lease heartbeats, numeric evidence and conservative observability."""

from __future__ import annotations

import io
import json
import threading
import uuid
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import array_bounds, rowcol
from shapely.geometry import Polygon, shape

from puertogt.api import config_path
from puertogt.contracts import ModelManifest, RasterBundle, Scene
from puertogt.jobs import JobQueue, ResourceLimit, RuntimeLimits
from puertogt.models import processing_recipe, register_portable_model, validated_scope
from puertogt.store import Store


def resolve_bundle_paths(
    bundle: RasterBundle, data_dir: Path, *, relative_to: Path | None = None
) -> RasterBundle:
    """Resolve numeric inputs against their file, current directory and data volume."""
    resolved = bundle.model_copy(deep=True)
    data_dir = Path(data_dir)
    for paths in (resolved.band_paths, resolved.mask_paths):
        for name, supplied in paths.items():
            supplied = Path(supplied)
            candidates = [supplied]
            if not supplied.is_absolute():
                if relative_to is not None:
                    candidates.insert(0, Path(relative_to) / supplied)
                candidates.append(data_dir / supplied)
                # Legacy repository bundles include the data directory itself.
                if supplied.parts and supplied.parts[0] == "data":
                    candidates.append(data_dir.joinpath(*supplied.parts[1:]))
            found = next(
                (candidate.resolve() for candidate in candidates if candidate.is_file()), None
            )
            if found is None:
                raise FileNotFoundError(f"Raster input not found: {supplied}")
            paths[name] = found
    return resolved


def process_once(
    store: Store,
    model: ModelManifest,
    *,
    device: str = "cpu",
    runtime: RuntimeLimits | None = None,
    predictor=None,
    prepare_fn=None,
    attribute_model: ModelManifest | None = None,
    acquisition_key: str | None = None,
):
    if model.task != "detection":
        raise ValueError("The scene worker requires a detection model")
    if predictor is None:
        from puertogt.ml.detection import detect

        def predictor(bundle, manifest):
            return detect(bundle, manifest, device=device)

    if prepare_fn is None:
        from puertogt.preprocess import prepare

        prepare_fn = prepare
    queue = JobQueue(store, runtime=runtime)
    job = queue.claim(
        f"local-{uuid.uuid4()}", recipe_version=processing_recipe(model, attribute_model),
        acquisition_key=acquisition_key,
    )
    if job is None:
        return None
    job_id, token = job["job_id"], job["lease_token"]
    done = threading.Event()
    heartbeat_errors = []

    def heartbeat():
        while not done.wait(max(1, queue.runtime.lease_seconds / 3)):
            try:
                queue.heartbeat(job_id, token)
            except Exception as exc:
                heartbeat_errors.append(exc)
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with closing(store.connect()) as tx:
            row = tx.execute(
                "SELECT o.*,a.data_json AS scene_json FROM observations o JOIN acquisitions a USING(acquisition_key) WHERE observation_id=?",
                (job["observation_id"],),
            ).fetchone()
        job_input = json.loads(job.get("data_json", "{}")).get("input_scene")
        scene = (
            Scene.model_validate(job_input)
            if job_input
            else Scene.model_validate_json(row["scene_json"])
        )
        if model.sensor != scene.sensor:
            raise ValueError("scene and model sensors do not match")
        work_root = store.data_dir / "temporary"
        work_root.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="job-", dir=work_root) as scratch:
            bundle = (
                RasterBundle.model_validate(scene.metadata["prepared_bundle"])
                if "prepared_bundle" in scene.metadata
                else prepare_fn(
                    scene, row["zone_id"], model.recipe_version, output_dir=Path(scratch)
                )
            )
            if (
                bundle.sensor != scene.sensor
                or bundle.preprocessing_version != model.recipe_version
            ):
                raise ValueError("prepared input does not match the scheduled recipe")
            bundle = resolve_bundle_paths(bundle, store.data_dir)
            bundle.observation_id = job["observation_id"]
            bundle.metadata["captured_at"] = scene.captured_at.isoformat()
            register_portable_model(store, model)
            predictions = predictor(bundle, model)
            if attribute_model:
                from puertogt.ml.attributes import predict_attributes

                register_portable_model(store, attribute_model)
                scoped_attributes = attribute_model
                if not validated_scope(attribute_model, row["port_id"], row["zone_id"]):
                    scoped_attributes = attribute_model.model_copy(
                        update={
                            "publication_policy": {
                                **attribute_model.publication_policy,
                                "fields": {},
                            },
                        }
                    )
                predictions = predict_attributes(bundle, predictions, scoped_attributes)
            # Evidence remains numeric and independent of this display rendering.
            root = f"assets/{job['observation_id']}"
            asset_ids = []
            with rasterio.open(next(iter(bundle.band_paths.values()))) as source:
                height, width = source.shape
                bounds = list(array_bounds(height, width, source.transform))
            transform = Transformer.from_crs(bundle.crs, "EPSG:4326", always_xy=True)
            west, south = transform.transform(bounds[0], bounds[1])
            east, north = transform.transform(bounds[2], bounds[3])
            attribution = f"Contains modified Copernicus Sentinel data {scene.captured_at.year}; model provenance in manifest"
            meta = {
                "crs": bundle.crs,
                "bounds": bounds,
                "attribution": attribution,
                "captured_at": scene.captured_at.isoformat(),
                "wgs84_bounds": [west, south, east, north],
            }
            unique_paths = {}
            for name, path in {**bundle.band_paths, **bundle.mask_paths}.items():
                source = Path(path)
                if source in unique_paths:
                    continue
                asset_id = queue.publish_asset(
                    job_id,
                    token,
                    source,
                    f"{root}/{name}{source.suffix}",
                    metadata={
                        **meta,
                        "mime_type": "image/tiff"
                        if source.suffix.lower() in {".tif", ".tiff"}
                        else "image/jp2",
                        "role": "numeric" if name in bundle.band_paths else "mask",
                        "bundle": {
                            "bands": [
                                band
                                for band, candidate in bundle.band_paths.items()
                                if Path(candidate) == source
                            ],
                            "masks": [
                                mask
                                for mask, candidate in bundle.mask_paths.items()
                                if Path(candidate) == source
                            ],
                            "band_indices": bundle.metadata.get("band_indices", {}),
                            "units": bundle.units,
                            "transform": bundle.transform,
                            "sensor": bundle.sensor,
                            "product_level": bundle.product_level,
                            "preprocessing_version": bundle.preprocessing_version,
                            "metadata": bundle.metadata,
                        },
                    },
                )
                asset_ids.append(asset_id)
                unique_paths[source] = asset_id
            preview = []
            for band in (
                ["B04", "B03", "B02"] if bundle.sensor == "sentinel-2" else ["vv", "vv", "vv"]
            ):
                with rasterio.open(bundle.band_paths[band]) as source:
                    raw = source.read(
                        int(bundle.metadata.get("band_indices", {}).get(band, 1)),
                        out_shape=(height, width),
                    ).astype(float)
                preview.append(
                    np.clip(raw / (3000 if bundle.sensor == "sentinel-2" else 1000), 0, 1)
                )
            stream = io.BytesIO()
            Image.fromarray((np.stack(preview, axis=-1) * 255).astype("uint8")).save(
                stream, format="PNG"
            )
            preview_id = queue.publish_asset(
                job_id,
                token,
                stream.getvalue(),
                f"{root}/preview.png",
                metadata={
                    **meta,
                    "mime_type": "image/png",
                    "role": "preview",
                    "display_only": True,
                },
            )
            asset_ids.append(preview_id)
            flags = list(bundle.metadata.get("quality_flags", []))
            observable_path = bundle.mask_paths.get("observable")
            published = []
            fraction = None
            if observable_path:
                with rasterio.open(observable_path) as source:
                    observable = source.read(1) > 0
                    mask_transform = source.transform
                    mask_crs = source.crs
                to_mask = Transformer.from_crs("EPSG:4326", mask_crs, always_xy=True)
                zones = json.loads((config_path() / "ports.geojson").read_text())["features"]
                zone = next(
                    (
                        shape(f["geometry"])
                        for f in zones
                        if f["properties"]["zone_id"] == row["zone_id"]
                    ),
                    None,
                )
                if zone is None:
                    raise ValueError("unknown observation zone")
                from rasterio.features import geometry_mask
                from shapely.ops import transform as transform_geometry

                projected_zone = transform_geometry(to_mask.transform, zone)
                in_zone = geometry_mask(
                    [projected_zone],
                    out_shape=observable.shape,
                    transform=mask_transform,
                    invert=True,
                )
                zone_pixels = int(in_zone.sum())
                if not zone_pixels:
                    raise ValueError("prepared crop does not overlap the scheduled zone")
                fraction = float(observable[in_zone].mean())
                # Compute crop coverage geometrically. Rasterized pixel counts
                # can undercount a fully covered zone at pixel-centre boundaries.
                mask_height, mask_width = observable.shape
                footprint = Polygon(
                    [
                        mask_transform * corner
                        for corner in (
                            (0, 0),
                            (mask_width, 0),
                            (mask_width, mask_height),
                            (0, mask_height),
                        )
                    ]
                )
                covered_fraction = min(
                    1.0, projected_zone.intersection(footprint).area / projected_zone.area
                )
                fraction *= covered_fraction
                if covered_fraction < 0.999:
                    flags.append("zone_partially_outside_crop")
                for prediction in predictions:
                    centre = shape(prediction.geometry).centroid
                    if zone is not None and not zone.covers(centre):
                        continue
                    x, y = to_mask.transform(centre.x, centre.y)
                    r, c = rowcol(mask_transform, x, y)
                    if (
                        0 <= r < observable.shape[0]
                        and 0 <= c < observable.shape[1]
                        and observable[r, c]
                    ):
                        prediction.evidence_asset_ids = asset_ids
                        published.append(prediction)
                status = (
                    "usable" if fraction >= 0.999 else ("partial" if fraction > 0 else "unusable")
                )
            else:
                status = "unusable"
                flags.append("observability_not_established")
                raw = json.dumps(
                    {
                        "status": "experimental_proposals_only",
                        "not_a_published_count": True,
                        "predictions": [p.model_dump(mode="json") for p in predictions],
                    }
                ).encode()
                queue.publish_asset(
                    job_id,
                    token,
                    raw,
                    f"{root}/experimental-proposals.json",
                    metadata={
                        **meta,
                        "mime_type": "application/json",
                        "role": "experimental_proposals",
                    },
                )
            locally_validated = validated_scope(model, row["port_id"], row["zone_id"])
            if not locally_validated:
                flags.append("model_not_locally_validated")
            if heartbeat_errors:
                raise heartbeat_errors[0]
            # Stop concurrent heartbeats before the final transaction releases the lease.
            done.set()
            thread.join(timeout=5)
            observation_id = queue.complete(
                job_id,
                token,
                status=status,
                observable_fraction=fraction,
                quality_flags=sorted(set(flags)),
                detections=published,
                model_id=model.model_id,
                metadata={
                    "preview_asset_id": preview_id,
                    "model_version": model.version,
                    "validation_status": "validated" if locally_validated else "experimental",
                    "attribute_model_id": attribute_model.model_id if attribute_model else None,
                    "provisional": True,
                },
            )
            return {
                "job_id": job_id,
                "observation_id": observation_id,
                "status": status,
                "published_detections": len(published) if status != "unusable" else None,
            }
    except Exception as exc:
        done.set()
        thread.join(timeout=5)
        try:
            if isinstance(exc, ResourceLimit):
                queue.pause(job_id, token, str(exc))
                return {"job_id": job_id, "status": "paused", "reason": str(exc)}
            transient = isinstance(
                exc, (TimeoutError, ConnectionError, OSError)
            ) and not isinstance(exc, FileNotFoundError)
            queue.fail(
                job_id,
                token,
                type(exc).__name__,
                transient=transient,
                error_code="transient_io" if transient else "invalid_input",
            )
        finally:
            queue.close()
        raise
    finally:
        done.set()
        thread.join(timeout=5)
        queue.close()
